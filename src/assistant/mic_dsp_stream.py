"""
The filter, wrapped for the two places the speech process uses it.

`mic_dsp.py` beside this is a copy of the plugin's arithmetic and is kept
byte-identical so it can be re-copied. Everything the panel needs and that
file does not have lives here.

Two callers, and they are not the same problem:

  * the PHRASE path filters a finished buffer on its way to the model. Each
    buffer is a separate piece of audio with silence either side of it.
  * the STREAM path filters a continuous feed on its way to the VAD and the
    wake spotter.

A biquad carries state between samples, which is what makes it a filter, and
that difference decides what to do with the state between calls. The phrase
path must clear it and the stream path must not.
"""

from __future__ import annotations

import threading

import numpy as np

from src.assistant.mic_dsp import Chain, normalise_profile

#Full scale for int16. 32768 negative and 32767 positive, and the positive one
#is the ceiling because a sample written as 32768 wraps to -32768 - a
#full-scale sign flip, which is a click rather than a loud sample.
INT16_PEAK = 32767


class PhraseFilter:
    """
    The chain for a captured phrase, cleared before each one.

    **Cleared, and that is the whole point of this class.** A chain holds the
    last two samples per section, so a phrase filtered with the state left
    over from the previous one begins with a transient built out of audio from
    some other moment - minutes ago, in a different part of the room, at a
    different level. On a high pass that is a thump on the front of every
    capture, landing exactly on the first word.

    Locked because `as_audio()` has several callers - the transcription path,
    the wake diagnostics, the report - and two of them running at once through
    one set of state would interleave their samples into each other's filter.
    """

    def __init__(self, profile: dict, samplerate: float):
        self.samplerate = float(samplerate)
        self.profile = normalise_profile(profile,
                                         nyquist=self.samplerate / 2.0)
        self.chain = Chain(self.profile, self.samplerate)
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return not self.chain.bypassed and self.chain.active

    def describe(self) -> str:
        return self.chain.describe()

    def process(self, audio: np.ndarray) -> np.ndarray:
        """
        One phrase, as a flat float array in and out.

        Clipped on the way out. `makeup_db` is a gain and a gain can push
        samples past full scale; the model is handed floats and would take
        them, but every measurement made of the buffer afterwards - the peak,
        the voiced share, the report - is read against a full scale that they
        are outside of.
        """
        if not self.active or audio is None or audio.size == 0:
            return audio
        with self._lock:
            self.chain.reset(1)
            out = self.chain.process(np.asarray(audio,
                                                dtype=np.float64).reshape(-1, 1))
        return np.clip(out.ravel(), -1.0, 1.0).astype(np.float32)


class StreamFilter:
    """
    The chain for the live feed, whose state is meant to be kept.

    The audio is continuous, so the state carried from one window to the next
    is the filter working. Resetting between windows would put a discontinuity
    at every seam - 33 a second - which on a spectrum reads as broadband noise
    that is not in the room, and to the wake spotter reads as a worse
    microphone.

    So this is reset only when the stream is reopened, and NOT on a mode
    switch: switching between waiting for a wake word and listening for a
    phrase does not interrupt the audio.
    """

    def __init__(self, profile: dict, samplerate: float):
        self.samplerate = float(samplerate)
        self.profile = normalise_profile(profile,
                                         nyquist=self.samplerate / 2.0)
        self.chain = Chain(self.profile, self.samplerate)

    @property
    def active(self) -> bool:
        return not self.chain.bypassed and self.chain.active

    def describe(self) -> str:
        return self.chain.describe()

    def reset(self) -> None:
        self.chain.reset(1)

    def process_int16(self, window: np.ndarray) -> np.ndarray:
        """
        Mono int16 in, mono int16 out.

        The conversion is not optional and cannot be skipped by handing floats
        on. webrtcvad takes int16 bytes and openWakeWord's own input is int16,
        so audio filtered in float has to come back before either of them sees
        it - and coming back is where a gain past full scale becomes a wrap
        rather than a loud sample, which is why the clip is here and not left
        to numpy's cast.
        """
        if not self.active or window is None or window.size == 0:
            return window
        audio = np.asarray(window, dtype=np.float64).reshape(-1, 1)
        out = self.chain.process(audio).ravel()
        return np.clip(out, -INT16_PEAK, INT16_PEAK).astype(np.int16)


def build(profile: dict, samplerate: float, stream: bool = False):
    """
    A filter, or None when there is nothing for one to do.

    None rather than a bypassed chain, so every caller is one `is not None`
    away from the old behaviour exactly - no conversion, no copy, no clip. A
    profile that is off, or on and flat, costs nothing at all.
    """
    if not isinstance(profile, dict) or not profile:
        return None
    made = StreamFilter(profile, samplerate) if stream \
        else PhraseFilter(profile, samplerate)
    return made if made.active else None
