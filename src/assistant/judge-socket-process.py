#!/usr/bin/env python3
"""
A judge, listening on a socket.

Run beside the panel or on another machine. It answers one question - was
somebody talking to the assistant - with one key, and holds no state between
requests.

    python3 judge-socket-process.py --port 8771

Nothing here imports the panel. It is handed to another machine as a package
and has to stand on its own, so it carries its own copy of the protocol and
its own copy of the prompt. Both are copied verbatim rather than rewritten:
a protocol described in two places is a protocol that disagrees with itself,
and two prompts is two behaviours with only one of them being watched.
"""

from __future__ import annotations

import argparse
import json
import socket
import socketserver
import sys
import threading
import time

try:
    from judge_protocol import (ANSWER, IGNORE, MAX_LINE, ProtocolError,
                                answer, failure, read_line, send_line)
except ImportError:  # running from inside the project
    from src.assistant.judge_protocol import (ANSWER, IGNORE, MAX_LINE,
                                              ProtocolError, answer, failure,
                                              read_line, send_line)

try:
    from judge_prompt import build_prompt, chat_prompt
except ImportError:
    from src.assistant.judge_prompt import build_prompt, chat_prompt


STARTED = time.time()


class Model:
    """
    The model, loaded once and asked many times.

    Loaded up front rather than lazily: this process exists only to answer,
    so a first request that takes thirty seconds while weights load is worse
    than a process that is not listening yet.
    """

    def __init__(self, repo: str, filename: str, threads: int):
        import onnxruntime
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        self.repo = repo
        print(f"Fetching {repo} ({filename})...", flush=True)
        tokenizer_path = hf_hub_download(repo, "tokenizer.json")
        model_path = hf_hub_download(repo, filename)

        self.tokenizer = Tokenizer.from_file(tokenizer_path)
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        print("Opening the model...", flush=True)
        self.session = onnxruntime.InferenceSession(
            model_path, options, providers=["CPUExecutionProvider"])

        first = self.tokenizer.encode(ANSWER, add_special_tokens=False).ids
        second = self.tokenizer.encode(IGNORE, add_special_tokens=False).ids
        if not first or not second:
            raise RuntimeError("the tokenizer produced nothing for the keys")
        if first[0] == second[0]:
            # Both keys starting with the same token would make every answer
            # the same answer, with nothing downstream able to tell.
            raise RuntimeError(f"'{ANSWER}' and '{IGNORE}' start with the "
                               f"same token")
        self.answer_id, self.ignore_id = first[0], second[0]
        self._lock = threading.Lock()
        print(f"Ready in {time.time() - STARTED:.1f}s.", flush=True)

    def judge(self, payload: dict) -> str:
        import numpy as np

        prompt = chat_prompt(build_prompt(payload))
        ids = self.tokenizer.encode(prompt).ids
        length = len(ids)

        feeds = {}
        for entry in self.session.get_inputs():
            name = entry.name
            if name == "input_ids":
                feeds[name] = np.array([ids], dtype=np.int64)
            elif name == "attention_mask":
                feeds[name] = np.ones((1, length), dtype=np.int64)
            elif name == "position_ids":
                feeds[name] = np.arange(length, dtype=np.int64)[None, :]
            else:
                shape = []
                for dimension in entry.shape:
                    if isinstance(dimension, int):
                        shape.append(dimension)
                    elif "batch" in str(dimension):
                        shape.append(1)
                    else:
                        shape.append(0)
                feeds[name] = np.zeros(shape, dtype=np.float32)

        # One at a time. An onnxruntime session is not safe to run
        # concurrently, and a threaded server will happily try.
        with self._lock:
            logits = self.session.run(["logits"], feeds)[0][0, -1, :]

        return ANSWER if float(logits[self.answer_id]) >= \
            float(logits[self.ignore_id]) else IGNORE


class Handler(socketserver.BaseRequestHandler):
    """One connection, one request, one answer."""

    def handle(self):
        self.request.settimeout(self.server.read_timeout)
        try:
            payload, _rest = read_line(self.request)
        except (ProtocolError, OSError) as exc:
            self._reply(failure(f"could not read the request: {exc}"))
            return

        command = str(payload.get("cmd") or "").strip().lower()
        model = self.server.model

        if command == "ping":
            if model is None:
                self._reply(failure(self.server.error or "no model"))
            else:
                self._reply({"ok": True, "model": model.repo})
            return

        if command == "status":
            self._reply({
                "ok": model is not None,
                "model": getattr(model, "repo", ""),
                "uptime": round(time.time() - STARTED, 1),
                "judged": self.server.judged,
                "reason": "" if model is not None else self.server.error,
            })
            return

        if command != "judge":
            self._reply(failure(f"'{command}' is not a command here"))
            return

        if model is None:
            self._reply(failure(self.server.error or "no model"))
            return

        began = time.time()
        try:
            key = model.judge(payload)
        except Exception as exc:
            self._reply(failure(f"the model failed: {exc}"))
            return

        self.server.judged += 1
        spent = (time.time() - began) * 1000
        print(f"{key}  {spent:6.0f}ms  {str(payload.get('text'))[:60]!r}",
              flush=True)
        self._reply(answer(key))

    def _reply(self, payload: dict):
        try:
            send_line(self.request, payload)
        except OSError:
            # The panel gave up and closed. That is its business - it has a
            # timeout and the rules to fall back on.
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, model, error, read_timeout):
        self.model = model
        self.error = error
        self.judged = 0
        self.read_timeout = read_timeout
        super().__init__(address, Handler)


def main() -> int:
    parser = argparse.ArgumentParser(description="A judge on a socket.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--model", default="onnx-community/Qwen3-0.6B-ONNX")
    parser.add_argument("--file", default="onnx/model_fp16.onnx",
                        help="fp16 by default: a machine reached over a "
                             "socket is not a panel and has room for it")
    parser.add_argument("--threads", type=int, default=0,
                        help="0 lets onnxruntime decide")
    parser.add_argument("--read-timeout", type=float, default=10.0)
    args = parser.parse_args()

    model, error = None, ""
    try:
        model = Model(args.model, args.file, args.threads)
    except Exception as exc:
        # Listening anyway, and saying why. A server that exits leaves the
        # panel with a refused connection and no explanation; one that
        # answers "no model, because X" puts the reason in the panel's log.
        error = f"{type(exc).__name__}: {exc}"
        print(f"Could not load the model: {error}", file=sys.stderr, flush=True)

    server = Server((args.host, args.port), model, error, args.read_timeout)
    print(f"Listening on {args.host}:{args.port}.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
