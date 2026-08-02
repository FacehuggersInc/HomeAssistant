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
from typing import Optional

import numpy as np


#What openWakeWord wants: 16kHz mono, and audio in multiples of 1280 samples
#(80ms). The panel reads 30ms windows (480 samples), so they do not line up
#one to one and a buffer bridges them - see feed().
FRAME_SAMPLES = 1280

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
                 refractory: float = DEFAULT_REFRACTORY, log=None):
        self.wake_word = str(wake_word or "")
        self.model_name = model_for(self.wake_word)
        self.threshold = float(threshold or DEFAULT_THRESHOLD)
        self.refractory = float(refractory or 0)
        self.log = log or (lambda level, message: None)

        self.model = None
        self.ready = False
        self.reason = ""
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
        """One model, however this version of the library wants to be asked."""
        import inspect
        import os

        takes = inspect.signature(Model.__init__).parameters
        if "wakeword_models" in takes:
            return Model(wakeword_models=[self.model_name])

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
        return Model(wakeword_model_paths=[found])

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

            # Above the line. Ignored if it fired a moment ago: one utterance
            # produces a run of high frames as the word passes through, and
            # each is the same detection.
            if self._quiet_for < self.refractory * 16000:
                continue
            self._quiet_for = 0
            fired = score
        return fired

    def reset(self) -> None:
        """
        Forget the buffered audio and the model's own state.

        Called when the panel stops listening for a while - the model carries
        context between frames, and stale context from before a gap describes
        audio that is no longer adjacent to what comes next.
        """
        self._pending = np.zeros(0, dtype=np.int16)
        self._quiet_for = int(self.refractory * 16000)
        if self.model is not None:
            try:
                self.model.reset()
            except Exception:
                pass
