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
from typing import TYPE_CHECKING

from src.assistant.judge_prompt import build_prompt, chat_prompt
from src.assistant.judge_protocol import ANSWER, IGNORE

if TYPE_CHECKING:
    from src.main import Client

#The build to fetch. int8 on a panel: around 350MB rather than 600, on
#hardware with a screen and a microphone on it. A machine reached over a
#socket has no such constraint and its package asks for fp16.
DEFAULT_REPO = "onnx-community/Qwen3-0.6B-ONNX"
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
        self._lock = threading.Lock()

        self.session = None
        self.tokenizer = None
        # The two token ids compared at the end of the prompt. Which pair
        # they are is decided at load time, because a tokenizer that gives
        # both keys the same first token would make every answer the same
        # answer without anything looking wrong.
        self.answer_id = None
        self.ignore_id = None

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
        The token each key starts with, and a check that they differ.

        A tokenizer that gave both keys the same first token would make every
        answer the same answer, and nothing downstream could tell: the facade
        would see a valid key, the log would look ordinary, and the panel
        would either answer the television every time or ignore its owner
        every time. Checked once, here, where it can be said out loud.
        """
        try:
            answer = self.tokenizer.encode(ANSWER, add_special_tokens=False).ids
            ignore = self.tokenizer.encode(IGNORE, add_special_tokens=False).ids
        except Exception as exc:
            self.error = f"could not encode the keys: {exc}"
            self._log("warning", self.error)
            return False

        if not answer or not ignore:
            self.error = "the tokenizer produced nothing for the keys"
            self._log("warning", self.error)
            return False
        if answer[0] == ignore[0]:
            self.error = (f"'{ANSWER}' and '{IGNORE}' start with the same "
                          f"token, so the two cannot be told apart")
            self._log("warning", self.error)
            return False

        self.answer_id, self.ignore_id = answer[0], ignore[0]
        return True

    ## -- asking

    def judge(self, payload: dict) -> str:
        """
        `ANSWER` or `IGNORE`. Anything else raises, and the facade uses the
        rules.
        """
        if not self.available:
            raise RuntimeError(self.error or "the model is not ready")

        prompt = chat_prompt(build_prompt(payload))
        # One at a time. An onnxruntime session is not safe to run
        # concurrently, and two utterances arriving together is ordinary on a
        # panel that has just been woken twice.
        with self._lock:
            logits = self._forward(prompt)

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
        self.session = None
        self.tokenizer = None
