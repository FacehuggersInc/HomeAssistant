"""
The judge, running inside the panel on onnxruntime.

**One forward pass, and the answer is a key.** Nothing is generated. The
prompt is built so the next token is the decision, the logits for that
position are read once, and the larger of two candidates is the verdict.
That is not a shortcut around asking the model - it IS asking the model, and
it has three properties that generating text does not:

  - It cannot answer anything except a key. There is no sampling loop to run
    long, no explanation to strip, no `<think>` block to handle. The set of
    possible answers is the set of tokens compared.
  - It costs one pass rather than one pass per token, on hardware that is
    also holding a screen, a microphone and a web server.
  - It is deterministic. The same utterance in the same room gets the same
    answer, which is what makes a log worth reading.

`onnxruntime` and `huggingface_hub` are already here for the transcriber, and
`tokenizers` is the only thing this adds - a few megabytes, not the whole of
transformers.

**Loaded on a thread, and the panel does not wait for it.** The first run
downloads a few hundred megabytes. Until that finishes `available` is False,
`RECOVERS` is True, and every utterance is decided by the rules - which is
exactly what the panel does with no judge at all.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as Expired
from typing import TYPE_CHECKING

from src.assistant.judge_prompt import (LABELS, build_prompt, chat_prompt,
                                        system_for)
from src.assistant.judge_protocol import ANSWER, IGNORE

if TYPE_CHECKING:
    from src.main import Client

#The build to fetch. int8 on a panel: around 350MB rather than 600, on
#hardware with a screen and a microphone on it. A machine reached over a
#socket has no such constraint and its package asks for fp16.
DEFAULT_REPO = "onnx-community/Qwen3-1.7B-ONNX"
DEFAULT_FILE = "onnx/model_q4.onnx"
FALLBACK_FILES = ("onnx/model_quantized.onnx", "onnx/model_int8.onnx",
                  "onnx/model.onnx")

class LocalJudge:
    """
    Qwen, in this process. `available` is False until the model is loaded.

    The same surface the socket backend has, so `JudgeFacade` cannot tell
    them apart: `available`, `error`, `judge(payload)`, `stop()`.
    """

    #Answers "not yet" rather than "never" while the model is downloading or
    #loading, so the facade keeps it and asks again instead of throwing away
    #the only object that knows how to finish.
    RECOVERS = True

    def __init__(self, client: "Client"):
        self.client = client
        self.error = "loading the model"
        self._ready = False
        # One worker, so two utterances arriving together are serialised - an
        # onnxruntime session is not safe to run concurrently, and a panel
        # woken twice in a second is ordinary. `_running` is what stops them
        # QUEUEING: a caller that cannot be served now is told so rather than
        # waiting behind a pass that has already outlived its own deadline.
        self._pool = ThreadPoolExecutor(max_workers=1,
                                        thread_name_prefix="__judge")
        self._running = False

        self.session = None
        self.tokenizer = None
        # The two token ids compared at the end of the prompt. Which pair
        # they are is decided at load time, because a tokenizer that gives
        # both keys the same first token would make every answer the same
        # answer without anything looking wrong.
        self.answer_id = None
        self.ignore_id = None
        # Filled by _pick_keys, which also builds the instruction naming them.
        self.system = None
        self.labels = ()

        self.repo = str(self._setting("assistant.wake.judge_model.value",
                                      DEFAULT_REPO) or DEFAULT_REPO).strip()
        self.timeout = float(self._setting(
            "assistant.wake.judge_timeout.value", 1.0) or 1.0)

        self._loader = threading.Thread(target=self._load, name="__judge_load",
                                        daemon=True)
        self._loader.start()

    def _setting(self, path, default=None):
        try:
            return self.client.setting(path, default)
        except Exception:
            return default

    def _log(self, level, message):
        try:
            self.client.log(level, f"[Judge] {message}")
        except Exception:
            pass

    ## -- readiness

    @property
    def available(self) -> bool:
        return bool(self._ready and self.session is not None)

    ## -- loading

    def _load(self) -> None:
        """
        Fetch and open the model, off the thread everything else is on.

        Every failure is a reason rather than an exception: this runs where
        nobody is waiting, and a panel that will not start because a judge
        would not download is a panel broken by an optional feature.
        """
        started = time.time()
        try:
            import onnxruntime
            from huggingface_hub import hf_hub_download
            from tokenizers import Tokenizer
        except ImportError as exc:
            self.error = f"a package is missing: {exc}"
            self._log("warning", f"Cannot judge locally - {self.error}")
            return

        try:
            self._log("info", f"Fetching {self.repo}. The first run downloads "
                              f"a few hundred megabytes; the rules decide "
                              f"until it is ready.")
            tokenizer_path = hf_hub_download(self.repo, "tokenizer.json")
            model_path = ""
            wanted = [DEFAULT_FILE, *FALLBACK_FILES]
            reasons = []
            for name in wanted:
                try:
                    model_path = hf_hub_download(self.repo, name)
                    break
                except Exception as exc:
                    reasons.append(f"{name}: {type(exc).__name__}")
            if not model_path:
                self.error = (f"no usable build in {self.repo} "
                              f"({'; '.join(reasons)})")
                self._log("warning", self.error)
                return
        except Exception as exc:
            # Kept as a reason and retried later rather than given up on: a
            # panel that booted before the network was up should not need
            # restarting to get a judge.
            self.error = f"could not fetch the model: {exc}"
            self._log("warning", self.error)
            return

        try:
            self.tokenizer = Tokenizer.from_file(tokenizer_path)
            options = onnxruntime.SessionOptions()
            # One thread. The panel has a screen, a microphone and a web
            # server on the same processor, and a model that takes every core
            # for 200ms is a model that makes the screen stutter.
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            self.session = onnxruntime.InferenceSession(
                model_path, options, providers=["CPUExecutionProvider"])
        except Exception as exc:
            self.error = f"could not open the model: {exc}"
            self._log("warning", self.error)
            return

        if not self._pick_keys():
            return

        self._ready = True
        self.error = ""
        self._log("info", f"Judging with {self.repo}, ready in "
                          f"{time.time() - started:.1f}s.")

    def _pick_keys(self) -> bool:
        """
        The pair of words the model will be asked to say, and their tokens.

        **Each has to be exactly one token.** The comparison happens at one
        position, so a label spelling as several tokens is represented by its
        first - and a leading fragment like `ANS` carries the weight of every
        word the vocabulary can finish it with. Against a whole word such as
        `IGNORE` it wins regardless of the utterance, which reads as a model
        that agrees with everything.

        So the pairs are tried in order and the first where BOTH sides are a
        single token is taken. The instruction is then built from that pair,
        so what the model is asked to say and what is compared cannot drift.
        """
        tried = []
        for yes, no in LABELS:
            try:
                first = self.tokenizer.encode(yes, add_special_tokens=False).ids
                second = self.tokenizer.encode(no, add_special_tokens=False).ids
            except Exception as exc:
                self.error = f"could not encode the keys: {exc}"
                self._log("warning", self.error)
                return False

            if len(first) != 1 or len(second) != 1:
                tried.append(f"{yes}/{no}: {len(first)} and {len(second)} tokens")
                continue
            if first[0] == second[0]:
                # Both keys the same token would make every answer the same
                # answer, with nothing downstream able to tell: a valid key,
                # an ordinary log, and a panel that either answers the
                # television every time or ignores its owner every time.
                tried.append(f"{yes}/{no}: the same token")
                continue

            self.answer_id, self.ignore_id = first[0], second[0]
            self.system = system_for(yes, no)
            self.labels = (yes, no)
            self._log("info", f"Answering with {yes} or {no}.")
            return True

        self.error = (f"no usable pair of labels for this tokenizer "
                      f"({'; '.join(tried)})")
        self._log("warning", self.error)
        return False

    ## -- asking

    def judge(self, payload: dict) -> str:
        """
        `ANSWER` or `IGNORE`. Anything else raises, and the facade uses the
        rules.
        """
        if not self.available:
            raise RuntimeError(self.error or "the model is not ready")

        prompt = chat_prompt(build_prompt(payload), self.system)

        # **Bounded, because somebody is standing in front of the panel.**
        #
        # A forward pass cannot be interrupted - onnxruntime runs to
        # completion whatever this thread does - so the deadline is on the
        # WAIT rather than on the work. The pass finishes into nothing and
        # the caller gets the rules, which is what a judge that has gone slow
        # is supposed to cost. Without this, `judge_timeout` is a number in
        # the settings file that decides nothing, and a model too big for the
        # hardware holds up every reply with no way to say so.
        if self._running:
            raise RuntimeError("still working on the last one")

        self._running = True
        try:
            pending = self._pool.submit(self._forward, prompt)
        except Exception:
            self._running = False
            raise

        def done(_):
            self._running = False

        pending.add_done_callback(done)

        try:
            logits = pending.result(timeout=self.timeout)
        except Expired as exc:
            raise RuntimeError(
                f"took longer than {self.timeout:.1f}s") from exc

        answer = float(logits[self.answer_id])
        ignore = float(logits[self.ignore_id])
        return ANSWER if answer >= ignore else IGNORE

    def _forward(self, prompt: str):
        """
        The logits for the next token, and nothing else.

        **The input names are read off the model rather than assumed.** ONNX
        exports of the same architecture differ about what they ask for -
        `position_ids` is sometimes there and sometimes not, and the empty
        cache tensors are named differently by different exporters. Asking
        the session what it wants is a few lines; guessing is a backend that
        works against one export and fails against the next with an error
        that names a tensor nobody has heard of.
        """
        import numpy as np

        ids = self.tokenizer.encode(prompt).ids
        length = len(ids)
        input_ids = np.array([ids], dtype=np.int64)
        attention = np.ones((1, length), dtype=np.int64)

        feeds = {}
        for entry in self.session.get_inputs():
            name = entry.name
            if name == "input_ids":
                feeds[name] = input_ids
            elif name == "attention_mask":
                feeds[name] = attention
            elif name == "position_ids":
                feeds[name] = np.arange(length, dtype=np.int64)[None, :]
            else:
                # An empty cache entry. The shape is whatever the export
                # declares with the sequence length set to zero, which is
                # what "no history" means to every one of them.
                shape = []
                for dimension in entry.shape:
                    if isinstance(dimension, int):
                        shape.append(dimension)
                    elif "batch" in str(dimension):
                        shape.append(1)
                    else:
                        shape.append(0)
                feeds[name] = np.zeros(shape, dtype=np.float32)

        outputs = self.session.run(["logits"], feeds)
        # (batch, position, vocabulary) - the last position is the token the
        # model would say next, which is the decision.
        return outputs[0][0, -1, :]

    def stop(self) -> None:
        self._ready = False
        # Not waited for. A pass already inside onnxruntime cannot be
        # stopped, and the assistant is being taken down - holding shutdown
        # for a judgement nobody will read is the wrong trade.
        try:
            self._pool.shutdown(wait=False)
        except Exception:
            pass
        self.session = None
        self.tokenizer = None
