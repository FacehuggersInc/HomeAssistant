"""
Listening with openWakeWord and Parakeet, and nothing else.

The whisper process spots the wake word by transcribing every 150ms of speech
and reading the result. That is slow, it needs the spelling to be close
enough to match, and - because a transcription takes long enough that the
person is already mid-sentence when it answers - it left the wake word and
the phrase inside ONE utterance. Everything downstream was built on that:
the phrase that gets transcribed contains the wake word, and the routing
finds it there.

openWakeWord answers acoustically, at the moment the word is said. That
breaks the assumption rather than improving on it. The pause after somebody
says "alexa" is longer than the silence that ends an utterance, so the wake
word finalises as a phrase all by itself, is transcribed as "alexa", has the
wake word stripped off it, comes out empty, and stands the panel down before
the question is even started. The panel says listening, then thinking, then
nothing - and the question that follows arrives with nothing armed to hear it.

So this process waits. A wake fires, the audio that produced it is thrown
away, and a window opens for the phrase - which has not been spoken yet.
That is the whole difference, and it is not a constant that could be tuned.

Deliberately not a mode of the whisper process. Half of what that class does
is whisper's failure modes: hallucinated sign-offs, repetition loops, beam
widths, a second small model for the wake check, a noise-reduction pass to
keep those under control. Parakeet has none of them, and the branches to skip
them all were what made the two paths so hard to reason about that this bug
survived in the seams between them.
"""

from __future__ import annotations

import collections
import json
import os
import queue
import signal
import sys
import time
from pathlib import Path
from socket import socket, AF_INET, SOCK_STREAM, SOL_SOCKET, SO_REUSEADDR
from threading import Thread, Event as ThreadEvent


# Spawned as a script, so sys.path[0] is src/assistant/ and the project root
# is nowhere. Everything below that imports `src.*` fails without this, and
# fails quietly - see whisper-process.py, where exactly that disabled both of
# the features this file is made of.
def _add_project_root() -> None:
    here = Path(__file__).resolve()
    for folder in here.parents:
        if (folder / "src" / "assistant").is_dir():
            if str(folder) not in sys.path:
                sys.path.insert(0, str(folder))
            return
    fallback = str(here.parents[2]) if len(here.parents) > 2 else ""
    if fallback and fallback not in sys.path:
        sys.path.insert(0, fallback)


_add_project_root()

try:
    import psutil
except Exception:
    psutil = None

# Guarded so a missing audio stack is a message rather than a traceback into
# a log file nobody reads. sounddevice raises OSError when PortAudio is
# absent, so this catches Exception rather than ImportError.
_IMPORT_ERROR = None
try:
    import numpy as np
    import sounddevice as sd
    import webrtcvad
except Exception as _e:
    np = sd = webrtcvad = None
    _IMPORT_ERROR = f"{type(_e).__name__}: {_e}"


def _pid_alive(pid: int) -> bool:
    """Whether a process id is still running, as portably as possible."""
    if not pid:
        return True
    if psutil is not None:
        try:
            return psutil.pid_exists(pid)
        except Exception:
            pass
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True


class ParakeetListener:
    """
    One microphone, one wake spotter, one transcriber.

    Two threads. The audio thread reads the stream, decides where an
    utterance starts and ends, and puts finished ones on a queue. The
    processing thread takes them off it and transcribes. Nothing slow is
    allowed on the audio thread - the stream keeps filling while it runs, and
    what overflows is the start of whatever was said next.
    """

    # 16kHz mono, in 30ms windows. 16kHz because both the spotter and the
    # transcriber want it; 30ms because webrtcvad accepts 10, 20 or 30 and
    # 30 is the cheapest of the three per second of audio.
    SAMPLE_RATE = 16000
    WINDOW_MS = 30

    # How much audio before the first speech window is kept. Speech is
    # recognised as speech a little after it starts, so without a lead-in the
    # first consonant is missing from every phrase.
    PRE_CONTEXT_MS = 420

    # How long a silence ends a phrase. Wake mode is answering "have they
    # finished the sentence", not "have they stopped talking to me", so this
    # is a real pause rather than a breath.
    SILENCE_MS = 700

    # Below this, an utterance is a cough, a door, or a chair. Measured in
    # DETECTED SPEECH, not buffered audio - the buffer has the lead-in
    # prepended, so its length says nothing about how much was said.
    MIN_SPEECH_MS = 200

    # onnx-asr's own guidance is 20-30 seconds for these models. A phrase
    # that runs past this is a television, and cutting it is better than
    # feeding the model something it will handle badly.
    MAX_PHRASE_MS = 18000

    # How long after a wake the audio is still the wake word.
    #
    # openWakeWord answers as the word completes, so its last syllable is
    # still arriving when it fires. The VAD calls that speech, it finalises
    # as a phrase of its own, and it transcribes to nothing.
    #
    # The audio is not thrown away - it goes into the lead-in buffer, so
    # somebody who runs straight on ("alexa what is the weather") loses
    # nothing off the front of their question. It just cannot START a phrase.
    WAKE_TAIL_MS = 250

    # A mute that is never lifted is a deaf panel, and the lift depends on a
    # message arriving from another process. Two minutes is longer than any
    # reply and shorter than somebody wondering what is wrong.
    MUTE_DEADLINE = 120.0

    # One level report per this many speech windows - about ten a second,
    # which is faster than the eye and a third of the traffic.
    LEVEL_EVERY = 3

    # How long after a wake to keep waiting for a phrase that never comes.
    # The client has its own, longer timeout as a backstop; this one exists
    # so the panel stands down at the right moment rather than that one.
    LISTEN_TIMEOUT = 8.0

    def __init__(self, log=None, model_name: str = "parakeet-v3",
                 precision: str = "int8", wake_words: list[str] = None,
                 input_device=None, session_silence_ms: int = 800,
                 wake_listen_timeout: float = 0.0,
                 vad_aggressiveness: int = 3,
                 mic_processing: str = "software",
                 wake_sensitivity: float = 0.5,
                 wake_sensitivity_speaking: float = 0.0,
                 initial_mode: str = "wake"):
        # First, so everything that reports during setup reports through the
        # socket rather than into a terminal nobody is reading.
        self._log_sink = log

        self.model_name = str(model_name or "parakeet-v3")
        self.precision = precision
        self.wake_words = [w for w in (wake_words or []) if w]
        self.wake_word = self.wake_words[0] if self.wake_words else ""
        self.input_device = input_device
        # How sure openWakeWord has to be. Lower hears the word through more
        # noise and fires on more things that were not it; higher is the
        # reverse. A fan in the room is the case this is for.
        self.wake_sensitivity = max(0.05, min(0.95, float(wake_sensitivity or 0.5)))
        # And the bar while the panel is talking. Zero means "the same as
        # above" - the setting is off until somebody sets it.
        #
        # A lower one is defensible HERE and nowhere else: the microphone is
        # carrying the panel's own voice at the same time, so the word arrives
        # buried in a way it never is when the room is quiet. The cost of
        # going too low is also smaller in this window, because a false fire
        # only interrupts a sentence the panel was already reading out.
        speaking = float(wake_sensitivity_speaking or 0.0)
        self.wake_sensitivity_speaking = (
            max(0.05, min(0.95, speaking)) if speaking > 0 else self.wake_sensitivity)
        self.mic_processing = str(mic_processing or "software").lower()

        self.window_samples = int(self.SAMPLE_RATE * self.WINDOW_MS / 1000)
        self.pre_context_windows = self._windows(self.PRE_CONTEXT_MS)
        self.silence_windows = self._windows(self.SILENCE_MS)
        self.session_silence_windows = self._windows(session_silence_ms)
        self.min_speech_windows = max(1, self._windows(self.MIN_SPEECH_MS))
        self.max_phrase_windows = self._windows(self.MAX_PHRASE_MS)
        self.listen_timeout = float(wake_listen_timeout or self.LISTEN_TIMEOUT)

        self.running = False
        self.stop_event = ThreadEvent()
        self._listen_thread = None
        self._process_thread = None
        self.audio_queue = queue.Queue(maxsize=8)
        self._overflows = 0

        # "wake" waits for the word. "passthrough" transcribes everything,
        # which is what a session and the microphone test page want.
        self.mode = initial_mode if initial_mode in ("wake", "passthrough") else "wake"
        self.switching = False

        # Bumped on every mode switch. A queued phrase carries the generation
        # it was captured under, and one from before a switch is dropped.
        self.generation = 0

        # Set while the panel is speaking. Nothing is captured, so a reply
        # read into a live microphone cannot come back as a question.
        self._muted = False
        self._muted_at = 0.0

        # Armed means a wake fired and the phrase is expected. Only ever true
        # in wake mode; passthrough is permanently listening.
        self.armed = False
        # When the wake fired, which is what the tail guard is measured
        # from. Separate from `waiting_since` below: that one is reset every
        # time a phrase is finalised, and resetting the guard there would
        # start swallowing the front of a follow-up.
        self.armed_at = 0.0
        # When the panel last had nothing to do but wait. The listening
        # timeout is measured from here.
        self.waiting_since = 0.0

        self.on_wake = None
        self.on_final = None
        self.on_timeout = None
        self.on_transcribing = None
        self.on_transcribed = None
        self.on_voice_activity = None
        self.on_audio_error = None
        self._announced = False

        self.vad = webrtcvad.Vad(vad_aggressiveness) if webrtcvad else None
        self.spotter = None
        self.parakeet = None
        self.normalize = None
        # Set by start(); a reason here means the process has nothing to
        # offer and should say so rather than listen to no purpose.
        self.reason = ""

    def _windows(self, milliseconds) -> int:
        return max(1, int(round(max(0, int(milliseconds)) / self.WINDOW_MS)))

    def send_log(self, level: str, message: str, *extra) -> None:
        """
        Say something through whatever owns this.

        Handed in rather than reached for - this class has no socket. `*extra`
        is joined on, because this is called from exception handlers and a
        logger that raises replaces the failure being reported with one about
        the report.
        """
        if extra:
            message = " ".join([str(message)] + [str(part) for part in extra])
        sink = self._log_sink
        if sink is not None:
            try:
                sink(level, message)
                return
            except Exception:
                pass
        print(f"[{level.upper()[:4]}] {message}")

    def set_callbacks(self, on_wake=None, on_final=None, on_timeout=None,
                      on_voice_activity=None, on_audio_error=None,
                      on_transcribing=None, on_transcribed=None) -> None:
        self.on_wake = on_wake
        self.on_final = on_final
        self.on_timeout = on_timeout
        self.on_voice_activity = on_voice_activity
        self.on_audio_error = on_audio_error
        self.on_transcribing = on_transcribing
        self.on_transcribed = on_transcribed

    ## -- SETUP ---------------------------------------------------------

    def prepare(self) -> bool:
        """
        Load the spotter and the transcriber. False means do not start.

        Both are required. There is no whisper here to fall back to, which is
        the point of this process - so a panel that cannot have them is told
        so plainly rather than left listening with something missing and no
        way to tell from the outside.
        """
        if _IMPORT_ERROR:
            self.reason = f"the audio stack is unavailable ({_IMPORT_ERROR})"
            return False

        self._load_normalizer()
        if not self._load_spotter():
            return False
        return self._load_parakeet()

    def _load_spotter(self) -> bool:
        try:
            from src.assistant.wake_spotter import WakeSpotter, model_for
        except Exception as exc:
            self.reason = f"the wake spotter could not be imported ({exc})"
            return False

        if not self.wake_word:
            self.reason = "no wake word is configured"
            return False
        if not model_for(self.wake_word):
            # The setting is an enum of the four words openWakeWord ships, so
            # this is a panel whose settings predate that or were edited by
            # hand. Named, because "openWakeWord has no model" is not obvious
            # from the outside and the fix is one dropdown.
            self.reason = (f"openWakeWord has no model for '{self.wake_word}' "
                           f"- pick one it ships in Settings")
            return False

        spotter = WakeSpotter(self.wake_word, threshold=self.wake_sensitivity,
                              log=self.send_log)
        if not spotter.ready:
            self.reason = spotter.reason or "the wake spotter would not load"
            return False
        self.spotter = spotter
        return True

    def _load_normalizer(self) -> None:
        """
        The transcript cleaner, if it can be reached.

        Optional on purpose: without it the panel still hears, it just sends
        the odd invented phrase for the client to drop. `normalize` is stdlib
        only, so this is a path problem or nothing.
        """
        try:
            from src.assistant import normalize
            self.normalize = normalize
        except Exception as exc:
            self.normalize = None
            self.send_log("warning",
                          f"[Parakeet]: No transcript cleaning ({exc}). The "
                          f"client still checks what arrives.")

    def _load_parakeet(self) -> bool:
        try:
            from src.assistant.parakeet import load as load_parakeet
        except Exception as exc:
            self.reason = f"the Parakeet loader could not be imported ({exc})"
            return False

        parakeet = load_parakeet(self.model_name, log=self.send_log,
                                 precision=self.precision)
        if parakeet is None:
            self.reason = f"'{self.model_name}' is not a Parakeet model"
            return False
        if not parakeet.ready:
            self.reason = parakeet.reason or "Parakeet would not load"
            return False
        self.parakeet = parakeet
        return True

    ## -- CORE ----------------------------------------------------------

    def start(self) -> None:
        if self.stop_event.is_set():
            self.stop_event.clear()
        self.running = True
        self._listen_thread = Thread(target=self.__listen_loop, daemon=True)
        self._process_thread = Thread(target=self.__processing_loop, daemon=True)
        self._listen_thread.start()
        self._process_thread.start()

    def stop(self) -> None:
        self.running = False
        self.stop_event.set()
        try:
            # The sentinel, so the processing thread leaves its get() rather
            # than sitting on a queue nothing will ever fill again.
            self.audio_queue.put_nowait(None)
        except queue.Full:
            pass

    @property
    def muted(self) -> bool:
        """
        Whether capture is held off because the panel is talking.

        Lifts itself after `MUTE_DEADLINE`. The unmute arrives as a message
        from the client, and a client that crashed mid-reply would otherwise
        leave this process listening to a room it never records.
        """
        if self._muted and (time.time() - self._muted_at) > self.MUTE_DEADLINE:
            self.send_log("warning",
                          "[Parakeet]: Mute expired without an UNMUTE - "
                          "capturing again.")
            self._muted = False
        return self._muted

    def set_muted(self, value: bool) -> None:
        value = bool(value)
        if value == self._muted:
            return
        self._muted = value
        self._muted_at = time.time()
        self.send_log("debug",
                      f"[Parakeet]: Capture {'held' if value else 'resumed'}.")

    def switch_mode(self, mode: str) -> None:
        """
        Change what the child is listening for, and abandon what it had.

        The generation is bumped and the queue drained, so audio captured
        under the old mode is not transcribed and announced under the new
        one. Without it, closing a conversation leaves whatever was already
        finalised to arrive afterwards: each one announces `transcribing`,
        the panel flashes THINKING, nothing is woken so nothing comes of it,
        and it stands back down. Three queued phrases is three flashes on a
        panel nobody is talking to.
        """
        self.mode = mode if mode in ("wake", "passthrough") else "wake"
        self.switching = True
        self.generation += 1
        while True:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    ## -- LISTENING -----------------------------------------------------

    def __listen_loop(self) -> None:
        """
        Hold the microphone open, and reopen it when it goes.

        A microphone that disappears - unplugged, or an audio server restart
        - is an ordinary event on a panel that runs for months. Reported once
        per outage rather than every five seconds.
        """
        connected = True
        while not self.stop_event.is_set():
            try:
                with sd.InputStream(samplerate=self.SAMPLE_RATE, channels=1,
                                    dtype="int16",
                                    device=self.input_device) as stream:
                    self.send_log("debug", "[Parakeet]: Microphone opened.")
                    if not connected and callable(self.on_audio_error):
                        self.on_audio_error("")   # recovered
                    connected = True
                    self.__stream_loop(stream)
            except Exception as exc:
                if connected:
                    connected = False
                    self.send_log("warning",
                                  f"[Parakeet]: Microphone error: {exc}")
                    if callable(self.on_audio_error):
                        self.on_audio_error(str(exc))
                # wait(), not sleep(): a stop arriving during the backoff
                # would otherwise be ignored for the whole five seconds,
                # which is long enough for the client to give up and kill it.
                self.stop_event.wait(5)

    def __stream_loop(self, stream) -> None:
        pre_context = collections.deque(maxlen=self.pre_context_windows)
        phrase: list = []
        speech_windows = 0
        silence_windows = 0
        in_speech = False

        def reset_phrase():
            nonlocal phrase, speech_windows, silence_windows, in_speech
            phrase = []
            speech_windows = 0
            silence_windows = 0
            in_speech = False
            pre_context.clear()

        while not self.stop_event.is_set():
            window, overflowed = stream.read(self.window_samples)
            if overflowed:
                # The driver dropped input because this loop fell behind
                # realtime. Discarding the report is what made the truncated
                # phrases that follow look like model errors.
                self._overflows += 1
                if self._overflows in (1, 10, 100) or self._overflows % 500 == 0:
                    self.send_log("warning",
                                  f"[Parakeet]: Audio overflow x{self._overflows} "
                                  f"- input dropped, processing is behind realtime.")
            window = window[:, 0].tobytes()

            if self.switching:
                self.switching = False
                self.armed = False
                reset_phrase()
                if self.spotter is not None:
                    self.spotter.reset()
                self.send_log("debug",
                              f"[Parakeet]: Mode -> {self.mode}, state reset.")
                continue

            # THE SPOTTER, IN BOTH MODES.
            #
            # It is a streaming model - each frame builds on the one before -
            # so it is fed in order and without gaps. Skipped only while a
            # phrase is being captured in wake mode, where a re-fire would
            # restart the capture of the sentence it is already taking.
            #
            # Fed during PASSTHROUGH as well, which is the whole reason the
            # wake word works during a conversation. Left out, a panel saying
            # "say the wake word to ask something else" has no detector
            # running: the word would have to survive being transcribed,
            # matched as text, and passed by every self-hearing guard first.
            if self.mode == "passthrough" or not self.armed:
                try:
                    # Muted means the panel is speaking. Same detector, a
                    # different bar for the moment its own voice is in the
                    # microphone with yours.
                    wanted = (self.wake_sensitivity_speaking if self.muted
                              else self.wake_sensitivity)
                    if self.spotter.threshold != wanted:
                        self.spotter.threshold = wanted
                    if self.spotter.feed(window) is not None:
                        self.__woke()
                        if self.mode == "wake":
                            reset_phrase()
                            continue
                        # In passthrough the word is an interruption. The
                        # client stops whatever is speaking; capture carries
                        # on, because what follows the word is the question.
                        reset_phrase()
                except Exception as exc:
                    self.send_log("warning", f"[Parakeet]: Spotting failed: {exc}")

            # CAPTURE.
            #
            # Muted while the panel is speaking, so a reply read into a live
            # microphone is never captured, never transcribed and never
            # matched against anything. The spotter above still runs, so the
            # wake word still interrupts.
            capturing = (self.mode == "passthrough" or self.armed) and not self.muted
            if not capturing:
                if in_speech or phrase:
                    reset_phrase()
                if self.mode == "wake" and self.armed and not self.muted:
                    self.__check_listen_timeout(reset_phrase)
                continue

            is_speech = False
            try:
                is_speech = self.vad.is_speech(window, sample_rate=self.SAMPLE_RATE)
            except Exception:
                is_speech = False

            if is_speech:
                if (not in_speech and self.armed
                        and (time.time() - self.armed_at) * 1000 < self.WAKE_TAIL_MS):
                    # Still the wake word. Into the lead-in buffer, so it is
                    # there if the question runs straight on from it.
                    pre_context.append(window)
                    continue

                if not in_speech:
                    in_speech = True
                    # The lead-in, so the phrase does not start halfway
                    # through its first word.
                    phrase.extend(pre_context)
                    pre_context.clear()
                phrase.append(window)
                speech_windows += 1
                silence_windows = 0
                # Not every window. The meter is a bar on a screen and
                # nobody can see it move at 33 updates a second - but every
                # one of those is a socket write, and a stream of them is
                # what the messages that MATTER have to arrive through.
                if speech_windows % self.LEVEL_EVERY == 0:
                    self.__report_level(window)

                if len(phrase) >= self.max_phrase_windows:
                    self.send_log("debug",
                                  "[Parakeet]: Phrase hit the length limit - "
                                  "finalising what there is.")
                    self.__finalise(phrase, speech_windows)
                    reset_phrase()
                continue

            # SILENCE.
            if in_speech:
                # Kept, not dropped. The tail of a word runs past the point
                # the VAD stops calling it speech, and a phrase cut at that
                # point loses its last consonant.
                phrase.append(window)
                silence_windows += 1
                ending = (self.session_silence_windows
                          if self.mode == "passthrough" else self.silence_windows)
                if silence_windows >= ending:
                    self.__finalise(phrase, speech_windows)
                    reset_phrase()
                continue

            pre_context.append(window)

            # ARMED, AND NOBODY SAID ANYTHING.
            if self.mode == "wake" and self.armed:
                self.__check_listen_timeout(reset_phrase)

    def __check_listen_timeout(self, reset_phrase) -> None:
        """Stand down a wake nobody followed up on."""
        if time.time() - self.waiting_since < self.listen_timeout:
            return
        self.armed = False
        reset_phrase()
        if self.spotter is not None:
            self.spotter.reset()
        self.send_log("debug", "[Parakeet]: Woke, but nothing was said.")
        if callable(self.on_timeout):
            self.on_timeout("wake_timeout")

    def __woke(self) -> None:
        """
        The word was heard. Now wait for the sentence.

        The audio that fired the spotter is deliberately not kept. It is the
        wake word, the phrase has not started, and keeping it is what made
        "alexa" finalise as a phrase of its own.
        """
        self.armed = True
        self.armed_at = self.waiting_since = time.time()
        self.send_log("debug", f"[Parakeet]: Woke on '{self.wake_word}'.")
        if callable(self.on_wake):
            self.on_wake(self.wake_word)

    def __report_level(self, window: bytes) -> None:
        if not callable(self.on_voice_activity):
            return
        try:
            samples = np.frombuffer(window, dtype=np.int16).astype(np.float32) / 32768.0
            self.on_voice_activity(float(min(max(
                np.sqrt(np.mean(samples ** 2)), 0.0), 1.0)))
        except Exception:
            pass

    def __finalise(self, phrase: list, speech_windows: int) -> None:
        """Hand a finished utterance to the processing thread, or drop it."""
        if speech_windows < self.min_speech_windows:
            # A cough, a chair, a door. Not worth a model run, and worth even
            # less as a transcript acted on by a skill.
            self.send_log("debug",
                          f"[Parakeet]: Ignored {speech_windows * self.WINDOW_MS}ms "
                          f"- too short to be a phrase.")
            return

        spoke_ms = speech_windows * self.WINDOW_MS
        self.send_log("debug",
                      f"[Parakeet]: Finalising - {spoke_ms}ms spoken, "
                      f"{len(phrase) * self.WINDOW_MS}ms captured.")
        # The clock for the listening timeout restarts here, so a phrase
        # being transcribed cannot be stood down halfway through.
        self.waiting_since = time.time()
        try:
            self.audio_queue.put_nowait(
                (b"".join(phrase), time.time(), self.generation))
        except queue.Full:
            # Dropped loudly. Silently is how a panel ends up ignoring
            # somebody with no indication that it ever heard them.
            self.send_log("warning",
                          "[Parakeet]: Transcription queue is full - phrase dropped.")

    ## -- TRANSCRIBING --------------------------------------------------

    def __clean(self, text: str) -> str:
        """
        Drop an invented phrase, and trim invented edges off a real one.

        Here as well as on the client, because a transcript that reaches the
        panel has already been shown: the voice bar draws it and every
        listener sees it, and only then does routing decide it was nothing.
        A phrase dropped here was never said.

        Two different questions. `is_hallucination` is about the WHOLE
        utterance - boilerplate a transcriber produces from room tone, or the
        same words repeated. `strip_hallucination` takes known filler off the
        ends of a real phrase, which is what a question asked with a pause
        after it collects.
        """
        text = str(text or "").strip()
        if not text or self.normalize is None:
            return text

        if self.normalize.is_hallucination(text):
            self.send_log("debug",
                          f"[Parakeet]: Discarded {text!r} - nothing was said.")
            return ""

        trimmed = self.normalize.strip_hallucination(text)
        if trimmed != text:
            self.send_log("debug", f"[Parakeet]: Trimmed {text!r} -> {trimmed!r}")
        return trimmed

    def __processing_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                if not self.__one_phrase():
                    break
            except Exception as exc:
                self.send_log("warning", f"[Parakeet]: Processing failed: {exc}")
            finally:
                # Every pass that announced itself says when it stopped,
                # however it stopped. Several paths below end without a
                # transcript, and each one that did not clear this left the
                # panel reading "thinking" until something else moved it.
                if self._announced:
                    self._announced = False
                    if callable(self.on_transcribed):
                        self.on_transcribed()

    def __one_phrase(self) -> bool:
        """
        One trip round the processing loop.

        Returns False only for the shutdown sentinel. Everything else - a
        model error, an empty transcript - returns True, because ending this
        thread means every later phrase queues behind a worker that has gone
        and nothing ever restarts it.
        """
        item = self.audio_queue.get()
        if item is None:
            return False

        raw, finalised_at, generation = item
        if generation != self.generation:
            # Captured under a mode that has since been left. Dropped before
            # the announcement, so there is no "transcribing" to pair with.
            self.send_log("debug",
                          "[Parakeet]: Dropped a phrase from before the mode "
                          "switch.")
            return True

        queued_ms = int((time.time() - finalised_at) * 1000)

        # Announced before the model runs, not after. Otherwise the panel
        # stands down after the wake and the answer arrives out of nowhere
        # some seconds later.
        if callable(self.on_transcribing):
            self._announced = True
            self.on_transcribing()

        began = time.time()
        # No noise reduction. Whisper needed it to keep its hallucinations
        # down; Parakeet was trained on noisy speech and does not invent
        # words out of room tone, so a de-noising pass here costs a large
        # fraction of a core to make clean speech sound underwater.
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        text = self.parakeet.transcribe(audio)
        model_ms = int((time.time() - began) * 1000)
        text = self.__clean(text)

        if not text:
            # STILL ARMED. This is the important half of a wake that produced
            # nothing: the audio thread cannot tell a phrase from a cough or
            # the tail of the wake word, so it does not get to decide that
            # the panel has been spoken to.
            #
            # Disarming there meant one unusable capture made the process
            # deaf while the panel went on showing "listening" - because the
            # client keeps `woke_with` until ITS timeout, seconds later. The
            # question asked in between was never heard by anything.
            self.send_log("debug",
                          f"[Parakeet]: Nothing transcribed ({model_ms}ms in "
                          f"the model) - still listening.")
            return True

        self.send_log("debug",
                      f"[Parakeet]: Final transcription ({queued_ms}ms queued, "
                      f"{model_ms}ms in the model): {text}")
        if self.mode == "wake":
            # Spoken to, and answered. One phrase per wake; a follow-up comes
            # through a session, which the client opens by switching this
            # process to passthrough.
            self.armed = False

        if callable(self.on_final):
            self.on_final(text, [])
        return True


class ParakeetServer:
    """
    The socket half. Speaks exactly what STTProcessing already listens for.

    Same two ports, same `host:<event>:<payload>` messages, same commands -
    so the client side needs to know nothing about which process it started
    beyond which file to run.
    """

    def __init__(self, host="127.0.0.1", command_port=65432, data_port=65433):
        self.host = host
        self.ports = {"command": command_port, "data": data_port}
        self.running = True
        self.connections: dict = {"command": None, "data": None}

        config = {}
        if len(sys.argv) > 1:
            try:
                config = json.loads(sys.argv[1])
            except (ValueError, TypeError):
                config = {}

        wake_words = config.get("wake_words") or ["alexa"]
        self.model_name = str(config.get("model") or "parakeet-v3")
        self.parent_pid = int(config.get("parent_pid") or 0)

        self.listener = ParakeetListener(
            log=self.send_log,
            model_name=self.model_name,
            precision=str(config.get("parakeet_precision") or "int8"),
            wake_words=wake_words,
            input_device=config.get("input_device"),
            session_silence_ms=int(config.get("session_silence_ms") or 800),
            wake_listen_timeout=float(config.get("wake_listen_timeout") or 0),
            # Aggressive whether or not the microphone has its own VAD. A
            # softer gate calls fewer things silence, so the end-of-phrase
            # counter fills more slowly and every phrase ENDS later - which
            # reads as the panel being slow to hear you.
            vad_aggressiveness=3,
            mic_processing=str(config.get("mic_processing") or "software"),
            wake_sensitivity=float(config.get("wake_sensitivity") or 0.5),
            wake_sensitivity_speaking=float(
                config.get("wake_sensitivity_speaking") or 0.0),
        )
        self.listener.set_callbacks(
            on_wake=self.trigger_wake,
            on_final=self.process_transcribed,
            on_timeout=self.trigger_wait,
            on_transcribing=self.send_transcribing,
            on_transcribed=self.send_transcribed,
            on_voice_activity=self.send_voice_activity,
            on_audio_error=self.send_audio_error,
        )

    ## -- EVENTS --------------------------------------------------------

    def send_log(self, level: str, message: str, *extra) -> None:
        """
        Say something in the parent's log rather than this process's stdout.

        A print here goes to whatever stdout was inherited: no timestamp, no
        level, not in the log file, never on the Logs page. Kept as the
        fallback for the startup window before the socket exists, which is
        where something going wrong matters most.
        """
        if extra:
            message = " ".join([str(message)] + [str(part) for part in extra])
        text = str(message).replace("\n", " ")[:600]
        if self.connections.get("data"):
            try:
                self.connections["data"].sendall(
                    f"host:log:{level}:{text}\n".encode("utf-8"))
                return
            except Exception:
                self.__close_connection("data")
        # A print, and it must stay one: this IS the logger, so anything else
        # recurses until the stack gives out.
        print(f"[{level.upper()[:4]}] {text}")

    def __send(self, message: bytes, complain: bool = True) -> None:
        """
        One message, newline-terminated.

        The terminator is the protocol. `recv` on the other side returns
        whatever bytes have arrived rather than one message, so two of these
        sent a moment apart come back as one string - and without something
        to split on, the second is read as part of the first's payload and
        lost. See __listen_for_stt_data.
        """
        if not self.connections.get("data"):
            return
        try:
            self.connections["data"].sendall(message + b"\n")
        except Exception:
            if complain:
                self.send_log("warning", "[Parakeet]: Lost transcript connection.")
            self.__close_connection("data")

    def send_voice_activity(self, level: float) -> None:
        self.__send(f"host:voice_activity:{level:.3f}".encode("utf-8"), False)

    def send_audio_error(self, message: str) -> None:
        self.__send(f"host:audio_error:{message}".encode("utf-8"), False)

    def trigger_wake(self, wake_word: str) -> None:
        self.__send(f"host:woke:{wake_word}".encode("utf-8"))

    def send_transcribing(self) -> None:
        self.__send(b"host:transcribing:1")

    def send_transcribed(self) -> None:
        self.__send(b"host:transcribed:1")

    def trigger_wait(self, kind: str) -> None:
        self.__send(f"host:wait:{kind}".encode("utf-8"))

    def process_transcribed(self, transcribed: str, timestamps=None) -> None:
        if str(transcribed or "").strip():
            self.__send(
                f"host:transcribe:{transcribed.lower()}".encode("utf-8"))

    ## -- SOCKETS -------------------------------------------------------

    def __close_connection(self, which: str) -> None:
        conn = self.connections.get(which)
        if conn:
            try:
                conn.shutdown(1)
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
        self.connections[which] = None

    def __listen_for_commands(self) -> None:
        with socket(AF_INET, SOCK_STREAM) as s:
            s.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
            s.bind((self.host, self.ports["command"]))
            s.listen(1)
            # Timed accept, so `running` going False is noticed rather than
            # this thread sitting in accept() forever.
            s.settimeout(0.5)
            self.send_log("debug", "[Parakeet]: Listening for commands...")
            while self.running:
                try:
                    conn, addr = s.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                with conn:
                    try:
                        data = conn.recv(1024)
                        if not data:
                            # continue, NOT break. An empty connection - a
                            # port probe, a client that reconnected and
                            # dropped - killing this listener means STOP can
                            # never be delivered and the process survives
                            # every shutdown.
                            continue
                        to, command = data.decode("utf-8").strip().split(":")
                        if to != "server":
                            continue
                        if command == "STOP":
                            self.send_log("debug", "[Parakeet]: Received STOP.")
                            self.stop()
                            break
                        elif command == "START_WAKE":
                            self.listener.switch_mode("wake")
                            self.send_log("debug", "[Parakeet]: Mode -> WAKE.")
                        elif command == "START_PASSTHROUGH":
                            self.listener.switch_mode("passthrough")
                            self.send_log("debug", "[Parakeet]: Mode -> PASSTHROUGH.")
                        elif command == "MUTE":
                            self.listener.set_muted(True)
                        elif command == "UNMUTE":
                            self.listener.set_muted(False)
                    except Exception as e:
                        self.send_log("warning", f"[Parakeet]: Command error: {e}")

    def __accept_data(self) -> None:
        with socket(AF_INET, SOCK_STREAM) as s:
            s.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
            s.bind((self.host, self.ports["data"]))
            s.listen(1)
            self.send_log("debug", "[Parakeet]: Waiting for transcript connection...")
            conn, addr = s.accept()
            if conn:
                self.connections["data"] = conn
                self.send_log("debug", f"[Parakeet]: Data connection from {addr}")
                try:
                    conn.sendall(b"host:notify:Ready!\n")
                except Exception:
                    self.__close_connection("data")

    def __watch_parent(self) -> None:
        """
        Leave if the client goes away without sending STOP.

        A crash or a launcher restart otherwise leaves this holding the
        microphone and both ports, so the next client cannot bind them and
        comes up with no audio and no explanation.
        """
        if not self.parent_pid:
            return
        while self.running:
            if not _pid_alive(self.parent_pid):
                self.send_log("debug", "[Parakeet]: Parent is gone - shutting down.")
                self.shutdown()
            time.sleep(2)

    def __install_signals(self) -> None:
        def handler(signum, _frame):
            self.send_log("debug", f"[Parakeet]: Signal {signum} - shutting down.")
            self.shutdown()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError, AttributeError):
                pass

    ## -- LIFECYCLE -----------------------------------------------------

    def run(self) -> None:
        self.__install_signals()
        Thread(target=self.__listen_for_commands, daemon=True).start()
        Thread(target=self.__accept_data, daemon=True).start()
        Thread(target=self.__watch_parent, daemon=True).start()

        # Loaded after the sockets exist, so the reason for a failure has
        # somewhere to go. Everything here takes seconds and reaches the
        # network on a cold cache.
        if not self.listener.prepare():
            # Given a moment to connect first. A process that vanishes before
            # the client is listening takes its explanation with it, and the
            # panel is left with a microphone that does nothing.
            time.sleep(2)
            self.send_log("warning", f"[Parakeet]: Not starting - {self.listener.reason}.")
            self.send_audio_error(f"The voice assistant could not start: "
                                  f"{self.listener.reason}.")
            time.sleep(2)
            self.running = False
            return

        try:
            self.listener.start()
        except Exception as e:
            time.sleep(2)
            self.send_audio_error(f"The voice assistant could not start: {e}")
            self.running = False
            return

        self.send_log("info", f"[Parakeet]: Listening for "
                              f"'{self.listener.wake_word}'.")
        while self.running:
            time.sleep(1)
        self.send_log("debug", "[Parakeet]: Shutdown complete.")

    def stop(self) -> None:
        if not self.running:
            return
        self.send_log("debug", "[Parakeet]: Stopping...")
        self.running = False
        try:
            self.listener.stop()
        except Exception as e:
            self.send_log("warning", f"[Parakeet]: Error stopping listener: {e}")
        self.__close_connection("command")
        self.__close_connection("data")

    def shutdown(self, code: int = 0) -> None:
        """Release everything and leave, without waiting on native threads."""
        try:
            self.stop()
        except Exception as e:
            self.send_log("warning", f"[Parakeet]: Error during stop: {e}")
        try:
            sys.stdout.flush()
        except Exception:
            pass
        # os._exit, not sys.exit: a normal interpreter shutdown joins at the C
        # level on threads parked inside PortAudio or onnxruntime, which is
        # exactly where they are when a transcription is in flight.
        os._exit(code)


if __name__ == "__main__":
    server = ParakeetServer()
    try:
        server.run()
    finally:
        server.shutdown()
