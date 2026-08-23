"""
Spotting the wake word with a model built for spotting wake words.

The Whisper path asks a transcription model to write down 150ms of audio and
then checks whether the wake word is in the text. That is a sledgehammer used
as a tuning fork: it costs a full transcription per check, it answers with a
spelling that has to be matched loosely to be useful at all, and how much
audio it gets is a number somebody has to tune.

openWakeWord answers the actual question - is the wake word present in this
audio - as a probability per frame. Nothing to spell, nothing to match, no
window size to get right.

This sits beside the Whisper path rather than replacing it. The Whisper one
works on any word without training; this one needs a model for the word, and
ships four. Neither is right for everybody, which is why the setting exists.
"""

from __future__ import annotations

import os
from threading import Lock
from typing import Optional

import numpy as np


#What openWakeWord wants: 16kHz mono, and audio in multiples of 1280 samples
#(80ms). The panel reads 30ms windows (480 samples), so they do not line up
#one to one and a buffer bridges them - see feed().
FRAME_SAMPLES = 1280

#How many embedding frames the classifier reads, and so how many the vector
#taken from a fire has to hold. openWakeWord's own default: the head is
#trained on a (16, 96) matrix, and asking for a different number would produce
#a vector that cannot be compared with one taken any other way.
FEATURE_FRAMES = 16

#How sure it has to be. 0.5 is the library's own suggestion; lower catches
#more and fires on more, higher is the reverse.
DEFAULT_THRESHOLD = 0.5

#How long after firing before it can fire again. Without this one utterance
#produces a burst of detections as the word passes through the window.
DEFAULT_REFRACTORY = 1.5

#The words that ship with the library. Anything else needs a model trained
#for it, which is a real piece of work and not something this can do.
BUILT_IN = {
    "alexa": "alexa",
    "hey jarvis": "hey_jarvis",
    "jarvis": "hey_jarvis",
    "hey mycroft": "hey_mycroft",
    "mycroft": "hey_mycroft",
    "hey rhasspy": "hey_rhasspy",
    "rhasspy": "hey_rhasspy",
}


def model_for(wake_word: str) -> str:
    """
    The openWakeWord model for a spoken word, or "" if there is not one.

    Answered honestly rather than guessed at. A panel set to a word with no
    model would otherwise load the library, hear nothing forever, and give no
    reason - so the caller checks this first and stays on Whisper when it is
    empty.
    """
    key = " ".join(str(wake_word or "").lower().split())
    return BUILT_IN.get(key, "")


def available() -> tuple:
    """
    Whether openWakeWord can be used at all. Returns (ok, reason).

    Import is tried rather than assumed: it is an optional dependency, and a
    panel without it should say so once rather than fail at every frame.
    """
    try:
        import openwakeword  # noqa: F401
        from openwakeword.model import Model  # noqa: F401
    except ImportError as e:
        return False, f"openwakeword is not installed ({e})"
    except Exception as e:
        return False, f"openwakeword could not be loaded ({e})"
    return True, ""


class WakeSpotter:
    """
    One wake word, scored frame by frame.

    Fed whatever sized chunks the audio loop produces; it buffers internally
    to openWakeWord's 80ms frames, so the caller does not have to care.
    """

    def __init__(self, wake_word: str, threshold: float = DEFAULT_THRESHOLD,
                 refractory: float = DEFAULT_REFRACTORY, log=None,
                 speex: bool = False, vad_threshold: float = 0.0):
        self.wake_word = str(wake_word or "")
        self.model_name = model_for(self.wake_word)
        self.threshold = float(threshold or DEFAULT_THRESHOLD)
        self.refractory = float(refractory or 0)
        # Two things openWakeWord can do about a noisy room, both off by
        # default in the library and both aimed at exactly the case a fan or
        # an air conditioner creates. See _build().
        self.speex = bool(speex)
        self.vad_threshold = float(vad_threshold or 0.0)
        self.log = log or (lambda level, message: None)

        self.model = None
        # The embedding of whatever last fired, for recognising the same sound
        # again. Set by feed() at the firing frame; see _features().
        self.last_features = None
        self.ready = False
        self.reason = ""
        # Options asked for and dropped because this install cannot use them.
        # A spotter that started is not the same as one that started with
        # everything it was configured with, and only this side knows.
        self.degraded = ()
        # feed() runs on the audio thread and reset() has been called from
        # the command thread. The model carries state from frame to frame and
        # both halves write it, so a reset landing inside a predict() left the
        # frames still being scored running against a model that was half
        # cleared - and cleared the refractory under them, which is a wake
        # fired on nothing. One lock, held for a frame at a time.
        self._lock = Lock()
        #Samples waiting to make up a whole frame.
        self._pending = np.zeros(0, dtype=np.int16)
        #Frames since it last fired, as samples, so no clock is needed.
        self._quiet_for = 0
        self.last_score = 0.0

        self._load()

    def _load(self) -> None:
        if not self.model_name:
            self.reason = (f"openWakeWord has no model for "
                           f"'{self.wake_word}'")
            return
        ok, why = available()
        if not ok:
            self.reason = why
            return

        try:
            import openwakeword
            from openwakeword.model import Model

            # Newer releases take model NAMES and download them on demand;
            # older ones take PATHS to files shipped beside the library. Both
            # are in the wild, and a panel should not care which one pip
            # happened to install - so the name is tried first and the path
            # found on disk second.
            try:
                openwakeword.utils.download_models(
                    model_names=[self.model_name])
            except Exception:
                pass

            self.model = self._build(Model, openwakeword)
            if self.model is None:
                self.reason = (f"no openWakeWord model file for "
                               f"'{self.model_name}'")
                self.log("warning", f"[Wake] {self.reason}")
                return

            self.ready = True
            self.log("info", f"[Wake] Spotting '{self.wake_word}' with "
                             f"openWakeWord ({self.model_name}).")
        except Exception as e:
            self.reason = f"openWakeWord could not start: {e}"
            self.log("warning", f"[Wake] {self.reason}")

    def _build(self, Model, openwakeword):
        """
        One model, however this version of the library wants to be asked.

        Two shapes are in the wild: newer releases take model NAMES and
        download on demand, older ones take PATHS to files shipped beside the
        library. Which one pip installed is not something a panel should care
        about.

        **Every construction goes through `_attempt`, and every option is
        droppable.** A build can NAME an option it cannot use - Speex is a
        separate package and the voice detector is a download - so accepting
        the argument says nothing about whether it will work. An option that
        cannot be used must never be the reason the panel is deaf: it is an
        improvement to the wake word, and the wake word is the feature.
        """
        import inspect
        import os

        takes = inspect.signature(Model.__init__).parameters

        # Only what this build accepts at all. Passing one to a release that
        # predates it is a TypeError at startup, which is the wake word not
        # working in exchange for making it work better.
        options = {}
        if self.speex and "enable_speex_noise_suppression" in takes:
            options["enable_speex_noise_suppression"] = True
        if self.vad_threshold > 0 and "vad_threshold" in takes:
            options["vad_threshold"] = float(self.vad_threshold)

        if "wakeword_models" in takes:
            return self._attempt(Model, {"wakeword_models": [self.model_name]},
                                 options)

        # The older shape. Files live beside the library, named
        # "<word>_v0.1.onnx" or ".tflite" depending on the build.
        folder = os.path.join(os.path.dirname(openwakeword.__file__),
                              "resources", "models")
        found = ""
        try:
            for name in sorted(os.listdir(folder)):
                stem = name.rsplit(".", 1)[0]
                if stem.startswith(self.model_name) and name.endswith(
                        (".onnx", ".tflite")):
                    found = os.path.join(folder, name)
                    break
        except OSError:
            found = ""
        if not found:
            return None
        return self._attempt(Model, {"wakeword_model_paths": [found]}, options)

    def _attempt(self, Model, base: dict, options: dict):
        """
        Build it with the options, and again without them if that fails.

        The retry is the whole point, so it lives here rather than beside one
        of the two shapes above - written out per branch it was present on
        one and missing from the other, which is a spotter that starts on a
        panel whose library is new and refuses on a panel whose library is
        old, for a reason neither one names.
        """
        if not options:
            return Model(**base)

        self.log("info", f"[Wake] Spotting with {options}.")
        try:
            return Model(**base, **options)
        except Exception as exc:
            # Named individually, because which one is missing is what
            # somebody has to act on: Speex is `pip install speexdsp-ns`, the
            # detector is a download that failed.
            self.log("warning",
                     f"[Wake] Starting without {', '.join(sorted(options))} - "
                     f"{exc}. The wake word still works; that option does "
                     f"not, and this panel is missing what it needs.")
            self.degraded = tuple(sorted(options))
            return Model(**base)

    ## -- listening

    def feed(self, audio: bytes) -> Optional[float]:
        """
        Score some audio. Returns the score when the wake word fires, else
        None.

        Buffered to whole frames. openWakeWord is a streaming model - each
        frame builds on the last - so audio must be handed over in order and
        without gaps, which is why this takes whatever the loop has rather
        than asking for a particular size.
        """
        if not self.ready or not audio:
            return None

        try:
            samples = np.frombuffer(audio, dtype=np.int16)
        except Exception:
            return None
        if not len(samples):
            return None

        with self._lock:
            self._pending = np.concatenate([self._pending, samples])
            fired = None

            while len(self._pending) >= FRAME_SAMPLES:
                frame = self._pending[:FRAME_SAMPLES]
                self._pending = self._pending[FRAME_SAMPLES:]
                self._quiet_for += FRAME_SAMPLES

                try:
                    scores = self.model.predict(frame)
                except Exception as e:
                    self.log("warning", f"[Wake] Scoring failed: {e}")
                    self.ready = False
                    return None

                score = float(max(scores.values()) if scores else 0.0)
                self.last_score = score
                if score < self.threshold:
                    continue

                # Above the line. Ignored if it fired a moment ago: one
                # utterance produces a run of high frames as the word passes
                # through, and each is the same detection.
                if self._quiet_for < self.refractory * 16000:
                    continue
                self._quiet_for = 0
                fired = score
                # Taken HERE, at the frame that fired, and not afterwards.
                #
                # The feature buffer is a rolling window that moves on with
                # every frame handed over. Reading it once feed() has returned
                # would describe whatever arrived since, which on a busy loop
                # is a different moment entirely - and the whole point of the
                # vector is that it is the sixteen frames the classifier just
                # scored.
                self.last_features = self._features()
            return fired

    def _features(self):
        """
        The sixteen frames the classifier scored, as a flat list.

        openWakeWord's own: mel spectrogram, then Google's speech embedding,
        then a (16, 96) matrix that the small classifier reads. Already in
        memory, so this costs a copy rather than a model run.

        It is also the right thing to compare on. The mel stage normalises
        level, so it does not care how loud the sound was, and the embedding
        was trained on speech content rather than on who was speaking, so it
        does not care about pitch.

        None when it cannot be had - an older openWakeWord, or a model built
        some other way. Everything downstream treats that as "no vector" and
        carries on rather than failing.
        """
        try:
            features = self.model.preprocessor.get_features(FEATURE_FRAMES)
        except Exception:
            return None
        try:
            return [float(x) for x in np.asarray(features).reshape(-1)]
        except Exception:
            return None

    def reset(self, deafen: bool = False) -> None:
        """
        Forget the buffered audio, the model's own state, and the vector.

        The vector describes a fire that is now being abandoned, and leaving
        it would let the next fire be matched against audio it has nothing to
        do with.

        Called when the panel stops listening for a while - the model carries
        context between frames, and stale context from before a gap describes
        audio that is no longer adjacent to what comes next.

        **Still best called on the thread that calls `feed()`.** The lock
        makes it safe from any thread, but not sensible from any thread: a
        reset in the middle of an utterance throws away frames that were
        about to be scored together, and the caller that wanted it usually
        wanted it at a frame boundary. See `switch_mode()` in the speech
        process, which defers it to the audio loop for exactly that reason.

        `deafen` is for a CANCELLATION. Ordinarily the refractory is left
        satisfied, so a mode change does not cost a second and a half of a
        panel that cannot hear its own name. A cancel is the one case that
        wants the opposite: the word that caused it is still in the air, and
        scoring its tail against a fresh model with no refractory is how a
        panel wakes itself up on the way down.
        """
        with self._lock:
            self._pending = np.zeros(0, dtype=np.int16)
            self._quiet_for = 0 if deafen else int(self.refractory * 16000)
            self.last_features = None
            if self.model is not None:
                try:
                    self.model.reset()
                except Exception:
                    pass
