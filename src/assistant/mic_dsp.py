# The filter, copied from plugins/MicDSP/dsp.py at 0.2.0.
#
# Byte-identical below this comment, deliberately: re-copying it is replacing
# everything after these lines, and anything edited in place is something the
# next copy silently reverts. Core's own additions go in mic_dsp_stream.py
# beside it rather than in here.
#
# It is CORE's now rather than a plugin's. The audio path runs in a separate
# process that has no client and must start with no plugins installed, so it
# cannot call into one - the panel owns the arithmetic and a plugin owns the
# numbers. `normalise_profile()` is the contract between the two.
"""
The filter chain, as arithmetic and nothing else.

numpy is the only import. No Qt, no client, no settings object - so this file
can be copied into the speech process unchanged the day there is a hook to
call it from. That is the whole reason it is a file of its own rather than
part of the page that draws it.

Everything works on blocks shaped `(samples, channels)`. Mono is
`(samples, 1)`, so there is one path rather than two and nothing has to
remember which it was handed.

The profile dict below is the contract: what gets saved, what goes on the
public registry, and what a hook would be given. Nothing else here is part
of it.
"""

from __future__ import annotations

import math

import numpy as np

#SciPy's cascaded-biquad filter, when it is there.
#
#The fallback below it is a Python loop over samples, which is correct and
#roughly two hundred times slower - 25 ms to filter a 30 ms window on a
#desktop, which on the audio thread of a panel is not a filter, it is a
#dropout. The loop cannot be vectorised (each output feeds the next state),
#so the only way to be quick is to hand the recursion to something compiled.
#
#Not a new dependency: `noisereduce` requires scipy and is already in the
#panel's requirements, so anything that can de-noise a phrase can do this.
#Guarded anyway, because a filter that is slow is better than one that is
#absent, and this file is meant to run anywhere.
try:
    from scipy.signal import sosfilt as _sosfilt
except Exception:
    _sosfilt = None


## -- LIMITS ------------------------------------------------------------------

#Slopes offered, in dB per octave. A slope is an ORDER in disguise - 6 dB per
#octave is one pole - so these are the multiples of six rather than a range.
SLOPES = (6, 12, 18, 24, 36, 48)

BAND_COUNT = 5

HZ_MIN, HZ_MAX = 20.0, 20000.0
GAIN_MIN, GAIN_MAX = -18.0, 18.0
Q_MIN, Q_MAX = 0.3, 8.0
MAKEUP_MIN, MAKEUP_MAX = -12.0, 12.0


#Where the chain starts on a panel nobody has tuned yet.
#
#The high pass is the only thing on by default, and it is on because it is
#the one setting that is right on essentially every microphone in a house: a
#wall panel hears the building through its own bracket, and nothing below
#about 80 Hz is speech. Everything else is off, because a plugin that arrives
#already colouring the audio is a plugin that gets blamed for whatever the
#room was doing anyway.
DEFAULT_PROFILE = {
    "enabled": True,
    "highpass": {"enabled": True,  "hz": 90.0,   "slope": 12},
    "lowpass":  {"enabled": False, "hz": 7000.0, "slope": 12},
    "bands": [
        {"enabled": False, "hz": 160.0,  "gain_db": 0.0, "q": 1.0},
        {"enabled": False, "hz": 400.0,  "gain_db": 0.0, "q": 1.0},
        {"enabled": False, "hz": 1000.0, "gain_db": 0.0, "q": 1.0},
        {"enabled": False, "hz": 2500.0, "gain_db": 0.0, "q": 1.0},
        {"enabled": False, "hz": 5000.0, "gain_db": 0.0, "q": 1.0},
    ],
    "makeup_db": 0.0,
}


def _clamp(value, low, high, fallback):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if not math.isfinite(number):
        return float(fallback)
    return float(min(high, max(low, number)))


def normalise_profile(raw: dict = None, nyquist: float = None) -> dict:
    """
    Any dict, folded into one that will build.

    Read from disk, from a settings field, or from whatever a future caller
    hands over - none of which is trusted to be the right shape. A profile
    that is short of a band gets the default band rather than an index error
    three files away.

    `nyquist` clamps every frequency below half the sample rate. A 7 kHz low
    pass on a 16 kHz capture is fine; the same number on an 8 kHz one is a
    filter design that produces coefficients nothing can be done with, and
    the failure otherwise arrives as silence.
    """
    raw = raw if isinstance(raw, dict) else {}
    ceiling = HZ_MAX
    if nyquist:
        # Just under, never at. A pole exactly on the Nyquist frequency is a
        # divide by zero in the bilinear transform.
        ceiling = min(HZ_MAX, max(HZ_MIN + 1.0, float(nyquist) * 0.95))

    def cut(key, default):
        section = raw.get(key) if isinstance(raw.get(key), dict) else {}
        slope = section.get("slope", default["slope"])
        try:
            slope = int(slope)
        except (TypeError, ValueError):
            slope = default["slope"]
        return {
            "enabled": bool(section.get("enabled", default["enabled"])),
            "hz": _clamp(section.get("hz", default["hz"]),
                         HZ_MIN, ceiling, min(default["hz"], ceiling)),
            "slope": slope if slope in SLOPES else default["slope"],
        }

    bands = []
    given = raw.get("bands")
    given = given if isinstance(given, list) else []
    for index in range(BAND_COUNT):
        default = DEFAULT_PROFILE["bands"][index]
        supplied = given[index] if index < len(given) else None
        band = supplied if isinstance(supplied, dict) else {}
        bands.append({
            "enabled": bool(band.get("enabled", default["enabled"])),
            "hz": _clamp(band.get("hz", default["hz"]),
                         HZ_MIN, ceiling, min(default["hz"], ceiling)),
            "gain_db": _clamp(band.get("gain_db", default["gain_db"]),
                              GAIN_MIN, GAIN_MAX, default["gain_db"]),
            "q": _clamp(band.get("q", default["q"]), Q_MIN, Q_MAX, default["q"]),
        })

    return {
        "enabled": bool(raw.get("enabled", DEFAULT_PROFILE["enabled"])),
        "highpass": cut("highpass", DEFAULT_PROFILE["highpass"]),
        "lowpass": cut("lowpass", DEFAULT_PROFILE["lowpass"]),
        "bands": bands,
        "makeup_db": _clamp(raw.get("makeup_db", 0.0),
                            MAKEUP_MIN, MAKEUP_MAX, 0.0),
    }


## -- COEFFICIENTS ------------------------------------------------------------
#
# The RBJ audio EQ cookbook forms, normalised by a0 so the difference equation
# below does not have to divide. Each returns (b0, b1, b2, a1, a2).


def _butterworth_qs(order: int) -> tuple:
    """
    The Q of each second-order section in a Butterworth of this order, and
    whether a first-order section is needed alongside them.

    An odd order has one real pole, which no second-order section can carry -
    a third-order high pass is a one-pole and a two-pole together, not one
    and a half of something.
    """
    qs = [1.0 / (2.0 * math.sin(math.pi * (2 * k + 1) / (2 * order)))
          for k in range(order // 2)]
    return qs, bool(order % 2)


def highpass(fs: float, hz: float, q: float) -> tuple:
    w0 = 2.0 * math.pi * hz / fs
    cos_w0, sin_w0 = math.cos(w0), math.sin(w0)
    alpha = sin_w0 / (2.0 * q)
    a0 = 1.0 + alpha
    return ((1.0 + cos_w0) / 2.0 / a0,
            -(1.0 + cos_w0) / a0,
            (1.0 + cos_w0) / 2.0 / a0,
            (-2.0 * cos_w0) / a0,
            (1.0 - alpha) / a0)


def lowpass(fs: float, hz: float, q: float) -> tuple:
    w0 = 2.0 * math.pi * hz / fs
    cos_w0, sin_w0 = math.cos(w0), math.sin(w0)
    alpha = sin_w0 / (2.0 * q)
    a0 = 1.0 + alpha
    return ((1.0 - cos_w0) / 2.0 / a0,
            (1.0 - cos_w0) / a0,
            (1.0 - cos_w0) / 2.0 / a0,
            (-2.0 * cos_w0) / a0,
            (1.0 - alpha) / a0)


def highpass_one_pole(fs: float, hz: float) -> tuple:
    k = math.tan(math.pi * hz / fs)
    norm = 1.0 / (1.0 + k)
    return (norm, -norm, 0.0, (k - 1.0) * norm, 0.0)


def lowpass_one_pole(fs: float, hz: float) -> tuple:
    k = math.tan(math.pi * hz / fs)
    norm = 1.0 / (1.0 + k)
    return (k * norm, k * norm, 0.0, (k - 1.0) * norm, 0.0)


def peaking(fs: float, hz: float, gain_db: float, q: float) -> tuple:
    amp = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * hz / fs
    cos_w0, sin_w0 = math.cos(w0), math.sin(w0)
    alpha = sin_w0 / (2.0 * q)
    a0 = 1.0 + alpha / amp
    return ((1.0 + alpha * amp) / a0,
            (-2.0 * cos_w0) / a0,
            (1.0 - alpha * amp) / a0,
            (-2.0 * cos_w0) / a0,
            (1.0 - alpha / amp) / a0)


## -- SECTIONS ----------------------------------------------------------------

class Biquad:
    """
    One second-order section, transposed direct form II.

    State is per channel and the sample loop runs over every channel at once,
    so eight microphones cost what one does plus the width of a numpy add.
    The loop itself cannot be vectorised - each output feeds the next state -
    which is why it is a loop of numpy rather than one call.
    """

    __slots__ = ("b0", "b1", "b2", "a1", "a2", "z1", "z2")

    def __init__(self, coefficients: tuple):
        self.b0, self.b1, self.b2, self.a1, self.a2 = coefficients
        self.z1 = None
        self.z2 = None

    def reset(self, channels: int) -> None:
        self.z1 = np.zeros(channels, dtype=np.float64)
        self.z2 = np.zeros(channels, dtype=np.float64)

    def process(self, block: np.ndarray) -> np.ndarray:
        channels = block.shape[1]
        if self.z1 is None or self.z1.shape[0] != channels:
            self.reset(channels)

        out = np.empty_like(block)
        z1, z2 = self.z1, self.z2
        b0, b1, b2, a1, a2 = self.b0, self.b1, self.b2, self.a1, self.a2

        for index in range(block.shape[0]):
            x = block[index]
            y = b0 * x + z1
            z1 = b1 * x - a1 * y + z2
            z2 = b2 * x - a2 * y
            out[index] = y

        self.z1, self.z2 = z1, z2
        return out

    def response(self, omega: np.ndarray) -> np.ndarray:
        """H at each normalised frequency, as a complex array."""
        z1 = np.exp(-1j * omega)
        z2 = z1 * z1
        numerator = self.b0 + self.b1 * z1 + self.b2 * z2
        denominator = 1.0 + self.a1 * z1 + self.a2 * z2
        return numerator / denominator


class Chain:
    """
    A profile, built into sections, ready to be handed blocks.

    Built once and reused. Rebuilding per block would reset the filter state
    on every window, and a filter whose state resets is a filter that clicks
    at the seams - which on a spectrum reads as broadband noise that is not
    in the room.
    """

    def __init__(self, profile: dict, samplerate: float):
        self.samplerate = float(samplerate)
        self.profile = normalise_profile(profile, nyquist=self.samplerate / 2.0)
        self.sections: list = []
        self.makeup = 10.0 ** (float(self.profile["makeup_db"]) / 20.0)
        self.bypassed = not self.profile["enabled"]
        if not self.bypassed:
            self._build()

        # The same sections as a second-order-section array, for scipy. Built
        # once here rather than per block: it is the sections in a different
        # shape, and the shape is all sosfilt wants.
        self._sos = None
        self._state = None
        if self.sections and _sosfilt is not None:
            self._sos = np.array(
                [[s.b0, s.b1, s.b2, 1.0, s.a1, s.a2] for s in self.sections],
                dtype=np.float64)

    def _build(self) -> None:
        fs = self.samplerate
        nyquist = fs / 2.0

        for key, two_pole, one_pole in (
                ("highpass", highpass, highpass_one_pole),
                ("lowpass", lowpass, lowpass_one_pole)):
            cut = self.profile[key]
            if not cut["enabled"]:
                continue
            hz = min(float(cut["hz"]), nyquist * 0.95)
            order = max(1, int(cut["slope"]) // 6)
            qs, needs_first = _butterworth_qs(order)
            for q in qs:
                self.sections.append(Biquad(two_pole(fs, hz, q)))
            if needs_first:
                self.sections.append(Biquad(one_pole(fs, hz)))

        for band in self.profile["bands"]:
            # A band at zero gain is an identity filter with rounding error
            # in it, and five of them in series is five chances to drift.
            if not band["enabled"] or abs(float(band["gain_db"])) < 0.05:
                continue
            hz = min(float(band["hz"]), nyquist * 0.95)
            self.sections.append(
                Biquad(peaking(fs, hz, float(band["gain_db"]),
                               float(band["q"]))))

    @property
    def active(self) -> bool:
        return bool(self.sections) or abs(self.makeup - 1.0) > 1e-6

    def reset(self, channels: int) -> None:
        """
        Forget the last two samples, on every path.

        Called between PHRASES and never mid-stream. A biquad carries its
        state, so a phrase filtered with the previous one's leaves a
        transient built out of audio that is not in it - on a high pass, a
        thump landing on the first word.
        """
        for section in self.sections:
            section.reset(channels)
        self._state = None

    def process(self, block: np.ndarray) -> np.ndarray:
        """
        A `(samples, channels)` block, filtered.

        Returns the input untouched when the chain is bypassed or empty, so a
        caller does not have to ask first. State carries across calls either
        way, so consecutive blocks of one stream filter as one signal.
        """
        if self.bypassed or not self.active:
            return block
        out = np.asarray(block, dtype=np.float64)

        if self._sos is not None and out.shape[0]:
            channels = out.shape[1]
            if self._state is None or self._state.shape[2] != channels:
                self._state = np.zeros((self._sos.shape[0], 2, channels),
                                       dtype=np.float64)
            out, self._state = _sosfilt(self._sos, out, axis=0, zi=self._state)
        else:
            for section in self.sections:
                out = section.process(out)

        if abs(self.makeup - 1.0) > 1e-6:
            out = out * self.makeup
        return out

    def response_db(self, freqs: np.ndarray) -> np.ndarray:
        """
        The curve, in dB, at each frequency in Hz.

        What the sliders draw. Worked out from the coefficients rather than
        measured off a sweep, so the line is the filter rather than an
        estimate of it.
        """
        freqs = np.asarray(freqs, dtype=np.float64)
        if self.bypassed:
            return np.zeros_like(freqs)
        omega = 2.0 * np.pi * freqs / self.samplerate
        total = np.ones_like(omega, dtype=np.complex128)
        for section in self.sections:
            total = total * section.response(omega)
        magnitude = np.abs(total) * self.makeup
        # A stop band goes to zero and log of zero is not a number anything
        # can plot. Floored well below anything the display shows.
        return 20.0 * np.log10(np.maximum(magnitude, 1e-7))

    def describe(self) -> str:
        if self.bypassed:
            return "bypassed"
        parts = []
        cut = self.profile["highpass"]
        if cut["enabled"]:
            parts.append("HP {:.0f}Hz/{}dB".format(cut["hz"], cut["slope"]))
        cut = self.profile["lowpass"]
        if cut["enabled"]:
            parts.append("LP {:.0f}Hz/{}dB".format(cut["hz"], cut["slope"]))
        for band in self.profile["bands"]:
            if band["enabled"] and abs(band["gain_db"]) >= 0.05:
                parts.append("{:.0f}Hz {:+.1f}dB Q{:.1f}".format(
                    band["hz"], band["gain_db"], band["q"]))
        if abs(self.profile["makeup_db"]) >= 0.05:
            parts.append("makeup {:+.1f}dB".format(self.profile["makeup_db"]))
        return ", ".join(parts) if parts else "flat"
