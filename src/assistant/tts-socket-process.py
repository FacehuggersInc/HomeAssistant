#!/usr/bin/env python3
"""
Speech synthesis, over a socket.

Run this on whatever machine should do the work. The panel sends text and
takes back audio; nothing about the model runs on the panel.

    python3 tts-socket-process.py --host 0.0.0.0 --port 8770

**Why this is not a thread.** A neural voice holds a CPU for a second or two
per sentence, and in the panel's own process that starves the window, the web
server and the microphone reader for the whole of it - one interpreter, one
lock. A separate process fixes that whether it is on this machine or another
one, and once it is separate the address is the only difference.

**Why a session key.** `say` answers immediately, before the model has run.
`stream` takes the key and stays open while the audio comes back. `cancel`
arrives on a connection of its own - which is the point: the streaming
connection is busy carrying audio, so nothing can be said on it, and a reply
that cannot be stopped until it finishes is what this exists to fix.

The backend is deliberately swappable. `Voice` below is the only part that
knows about a model, and it is about thirty lines.
"""

from __future__ import annotations

import argparse
import queue
import socket
import socketserver
import sys
import threading
import time
import uuid

try:
    from tts_protocol import (COMMANDS, DEFAULT_PORT, FORMAT, ProtocolError,
                              end_frames, failure, read_line, send_frame,
                              send_line, success)
except ImportError:                                   # in the panel's tree
    from src.assistant.tts_protocol import (
        COMMANDS, DEFAULT_PORT, FORMAT, ProtocolError, end_frames, failure,
        read_line, send_frame, send_line, success)

#How long a session waits to be streamed before it is thrown away. A panel
#that asked for a sentence and then lost its network would otherwise leave the
#audio in memory for ever, and a room that is talking produces one of these
#every few seconds.
SESSION_TTL = 120.0

#Roughly a fifth of a second of audio per chunk at 24kHz. Small enough that
#playback starts promptly, large enough that the framing is not most of the
#traffic.
CHUNK_SAMPLES = 4800


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


## -- the model


class Voice:
    """
    Whatever actually makes the sound.

    The only part of this file that knows about a model. Replacing it is how a
    different engine gets used, and nothing above here changes.

    The calls mirror the panel's own local backend exactly, because a second
    guess at somebody else's API is a second thing to be wrong about:
    `TTSModel.load_model(language=...)`, `get_state_for_audio_prompt(voice)`
    for a voice, `generate_audio(state, text)` for the audio.
    """

    #What the panel's setting is when it has not been chosen, and what an
    #older install may still hold. None of these is a language: `default` in
    #particular made the model look for a `default.yaml` nobody ships, so it
    #would not load at all. English is what it falls back to anyway.
    LEGACY_LANGUAGE = {"": "english", "default": "english",
                       "auto": "english", "none": "english"}

    def __init__(self, voice: str = "anna", language: str = "english"):
        self.voice = voice
        self.language = self._real_language(language)
        self.rate = 24000
        self.error = ""
        self._model = None
        self._states: dict = {}
        self._lock = threading.Lock()
        self._load()

    @classmethod
    def _real_language(cls, value: str) -> str:
        """
        A real language, whatever was asked for.

        Checked here as well as at the panel. This process is started by hand
        as often as it is spawned, and `--language default` from somebody
        copying an older startup script is the same mistake with none of the
        panel's code in the way.
        """
        value = str(value or "").strip().lower()
        return cls.LEGACY_LANGUAGE.get(value, value)

    def _load(self) -> None:
        try:
            from pocket_tts import TTSModel
        except ImportError as exc:
            self.error = (f"pocket-tts is not installed here ({exc}). "
                          f"pip install -r requirements.txt")
            return
        try:
            # `language` picks the pretrained weights. Passing nothing takes
            # the default rather than guessing, which is a different question.
            self._model = TTSModel.load_model(language=self.language)
            try:
                # The model pads short text to get its token count up - its
                # own note says it does not do well with very few tokens,
                # which is exactly a panel's one-line replies.
                self._model.pad_with_spaces_for_short_inputs = True
            except Exception:
                pass
            self.rate = int(getattr(self._model, "sample_rate", 24000))
            # The default voice now, so the first sentence is not also the
            # first time a voice prompt is read from disk.
            self._state(self.voice)
        except Exception as exc:
            self._model = None
            asked = self.language or "the model's default"
            self.error = f"The model would not load ({asked}): {exc}"
            return
        log(f"Voice '{self.voice}' ready at {self.rate} Hz.")

    @property
    def ready(self) -> bool:
        return self._model is not None

    def _state(self, voice: str):
        """
        The voice state, kept in memory.

        Building one is slow and it does not change, so it is built once per
        voice. No disk cache here: the panel keeps one because it reloads
        often, and this process is started once and left running.
        """
        name = voice or self.voice
        with self._lock:
            if name in self._states:
                return self._states[name]
        state = self._model.get_state_for_audio_prompt(name)
        with self._lock:
            self._states[name] = state
        return state

    def voices(self) -> list:
        """
        Which voices are loaded.

        There is no catalogue to enumerate - any audio prompt can be a voice -
        so what is answered is what has been asked for so far.
        """
        with self._lock:
            return sorted(set(self._states) | {self.voice})

    def generate(self, text: str, voice: str = ""):
        """
        The whole sentence, as float32 samples.

        Serialised: `generate_audio` is documented as not thread-safe, and a
        panel answering while a skill also speaks sends two within a second.
        """
        state = self._state(voice or self.voice)
        with self._lock:
            return self._model.generate_audio(state, text)


## -- sessions


class Session:
    """One thing to say, from `say` until it has been streamed or dropped."""

    def __init__(self, key: str, text: str, voice: str):
        self.key = key
        self.text = text
        self.voice = voice
        self.made_at = time.time()
        self.cancelled = False
        self.error = ""
        self.audio = None
        self.done = threading.Event()

    def stale(self, now: float) -> bool:
        return (now - self.made_at) > SESSION_TTL


class Sessions:
    """
    Every outstanding session, and the thread that runs the model.

    One worker rather than a pool. The model is serialised anyway, and a queue
    that accepts more than it can start hides how far behind it is - which on
    a panel shows up as a reply arriving after somebody has given up and asked
    again.
    """

    def __init__(self, voice: Voice):
        self.voice = voice
        self.sessions: dict = {}
        self.lock = threading.Lock()
        self.work = queue.Queue()
        threading.Thread(target=self._run, name="tts-worker",
                         daemon=True).start()

    def add(self, text: str, voice: str) -> Session:
        session = Session(f"s-{uuid.uuid4().hex[:8]}", text, voice)
        with self.lock:
            self._reap()
            self.sessions[session.key] = session
        self.work.put(session)
        return session

    def get(self, key: str):
        with self.lock:
            return self.sessions.get(key)

    def drop(self, key: str) -> bool:
        with self.lock:
            session = self.sessions.pop(key, None)
        if session is None:
            return False
        session.cancelled = True
        # Woken, so a stream waiting on it stops waiting rather than sitting
        # there until the generation it no longer wants has finished.
        session.done.set()
        return True

    def _reap(self) -> None:
        now = time.time()
        for key in [k for k, s in self.sessions.items() if s.stale(now)]:
            log(f"Session {key} was never collected - dropping it.")
            self.sessions.pop(key, None)

    def outstanding(self) -> int:
        with self.lock:
            return len(self.sessions)

    def _run(self) -> None:
        while True:
            session = self.work.get()
            if session.cancelled:
                # Cancelled while it was still queued, which is the cheapest
                # outcome and the common one when a room is noisy.
                #
                # A cancel arriving once this is INSIDE the model cannot stop
                # it - nothing interrupts an inference in progress, which is
                # the constraint that made cancelling in the panel's own
                # process impossible. What changes here is that the work is
                # wasted on a machine with cycles to spare rather than on the
                # one that has to stay responsive, and the audio is dropped
                # instead of spoken.
                continue
            began = time.time()
            try:
                session.audio = self.voice.generate(session.text,
                                                    session.voice)
            except Exception as exc:
                session.error = str(exc)
                log(f"Session {session.key} failed: {exc}")
            else:
                took = time.time() - began
                log(f"Session {session.key}: {len(session.text)} chars in "
                    f"{took:.1f}s")
            session.done.set()


## -- the server


class Handler(socketserver.StreamRequestHandler):

    def handle(self) -> None:
        buffer = bytearray()
        sock = self.request
        try:
            message = read_line(sock, buffer, timeout=30.0)
        except ProtocolError as exc:
            log(f"Bad connection from {self.client_address[0]}: {exc}")
            return

        command = str(message.get("cmd") or "").strip().lower()
        if command not in COMMANDS:
            self.say(failure(f"No command called '{command}'.",
                             commands=list(COMMANDS)))
            return
        getattr(self, f"do_{command}")(message, buffer)

    def say(self, payload: dict) -> None:
        try:
            send_line(self.request, payload)
        except Exception:
            pass

    ## -- commands

    def do_ping(self, message, buffer) -> None:
        self.say(success(pong=True, at=time.time()))

    def do_status(self, message, buffer) -> None:
        voice = self.server.voice
        self.say(success(ready=voice.ready, error=voice.error,
                         voice=voice.voice, rate=voice.rate, format=FORMAT,
                         outstanding=self.server.sessions.outstanding()))

    def do_voices(self, message, buffer) -> None:
        voice = self.server.voice
        if not voice.ready:
            self.say(failure(voice.error or "No voice is loaded."))
            return
        self.say(success(voices=voice.voices(), current=voice.voice))

    def do_say(self, message, buffer) -> None:
        """
        Take the text and answer with a key. Does NOT wait for the model.

        The whole point of two phases: the panel is free the moment this
        returns, and can decide not to collect the audio at all - which is
        what happens when somebody speaks again before the answer is ready.
        """
        voice = self.server.voice
        if not voice.ready:
            self.say(failure(voice.error or "No voice is loaded."))
            return
        text = str(message.get("text") or "").strip()
        if not text:
            self.say(failure("Nothing to say."))
            return
        session = self.server.sessions.add(text, str(message.get("voice") or ""))
        self.say(success(key=session.key, rate=voice.rate, format=FORMAT,
                         channels=1))

    def do_cancel(self, message, buffer) -> None:
        key = str(message.get("key") or "")
        dropped = self.server.sessions.drop(key)
        if dropped:
            log(f"Session {key} cancelled.")
        self.say(success(dropped=dropped))

    def do_stream(self, message, buffer) -> None:
        """
        Wait for the audio and send it in chunks.

        This connection is busy for the whole sentence, which is why `cancel`
        has to arrive on another one.
        """
        key = str(message.get("key") or "")
        session = self.server.sessions.get(key)
        if session is None:
            self.say(failure(f"No session called '{key}'. It may have been "
                             f"cancelled, or collected already."))
            return

        wait = float(message.get("timeout") or 60.0)
        if not session.done.wait(timeout=wait):
            self.say(failure("The model did not finish in time."))
            return
        if session.cancelled:
            self.say(failure("Cancelled.", cancelled=True))
            return
        if session.error:
            self.say(failure(session.error))
            return

        data = self._as_bytes(session.audio)
        if data is None:
            self.say(failure("The audio came back in a shape this cannot "
                             "send."))
            return

        rate = self.server.voice.rate
        self.say(success(rate=rate, channels=1, format=FORMAT,
                         bytes=len(data)))

        # Four bytes a sample, so the chunk size is in samples rather than
        # bytes - the panel plays samples and the arithmetic belongs on the
        # side that knows the format.
        step = CHUNK_SAMPLES * 4
        sent = 0
        try:
            for start in range(0, len(data), step):
                if session.cancelled:
                    # Mid-playback. The panel hears the audio stop, which is
                    # what interrupting is, and the connection closes cleanly
                    # rather than the panel waiting out a sentence nobody
                    # wants.
                    log(f"Session {key} cancelled mid-stream.")
                    break
                send_frame(self.request, data[start:start + step])
                sent += 1
            end_frames(self.request)
            self.say(success(end=True, chunks=sent,
                             cancelled=session.cancelled))
        except (OSError, ProtocolError) as exc:
            log(f"Session {key} stopped sending: {exc}")
        finally:
            # Collected, one way or another.
            self.server.sessions.drop(key)

    @staticmethod
    def _as_bytes(audio):
        """Float32 mono bytes from whatever the model returned."""
        if audio is None:
            return None
        try:
            import numpy as np
        except ImportError:
            return None
        try:
            array = np.asarray(audio, dtype=np.float32)
            if array.ndim > 1:
                array = array.reshape(array.shape[0], -1)[:, 0]
            return array.tobytes()
        except Exception:
            return None


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Speech synthesis over a socket.")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Address to listen on. 0.0.0.0 for any.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--voice", default="anna")
    parser.add_argument("--language", default="english",
                        help="Which pretrained weights to load.")
    args = parser.parse_args(argv)

    voice = Voice(args.voice, args.language)
    if not voice.ready:
        # Started anyway. A panel asking why it is silent gets an answer from
        # `status` instead of a refused connection, and the reason is the
        # useful part.
        log(f"WARNING: {voice.error}")

    server = Server((args.host, args.port), Handler)
    server.voice = voice
    server.sessions = Sessions(voice)

    log(f"Listening on {args.host}:{args.port}")
    log(f"Point the panel at this machine: Settings -> Audio -> Speech -> "
        f"where it speaks = socket, host = this machine's IP, port = "
        f"{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Stopping.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
