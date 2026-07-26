from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# sounddevice raises OSError("PortAudio library not found") at IMPORT time on a
# machine with no audio stack - a fresh Windows box without drivers, a minimal
# container, a headless server. `except ImportError` does not catch that, so
# every access goes through _sd() and catches Exception.
_SD = None
_SD_ERROR: Optional[str] = None
_PROBED = False

DEFAULT_SAMPLE_RATE = 16000

MODELS = ("tiny.en", "tiny", "base.en", "base", "small.en", "small")

# ALSA advertises its rate-conversion and channel-mixing plugins as capture
# devices. They are not microphones, and listing them makes the input_device
# setting much harder to use. Real backends (pulse, pipewire, default,
# sysdefault, hw:*) are deliberately NOT in here.
_HELPER_DEVICES = {
    "lavrate", "samplerate", "speexrate", "speex",
    "upmix", "vdownmix", "dmix", "null", "jack", "oss",
}


class AudioUnavailable(Exception):
    pass


def _sd():
    global _SD, _SD_ERROR, _PROBED
    if not _PROBED:
        _PROBED = True
        try:
            import sounddevice
            _SD = sounddevice
        except Exception as e:
            _SD_ERROR = f"{type(e).__name__}: {e}"
    return _SD


## -- AVAILABILITY -----------------------------------------------------------

def available() -> tuple[bool, str]:
    """(usable, reason). Reason is user-facing when usable is False."""
    sd = _sd()
    if sd is None:
        if _SD_ERROR and "PortAudio" in _SD_ERROR:
            return False, ("PortAudio is not installed. On Linux install "
                           "'portaudio'; on Windows check that an audio driver "
                           "is present.")
        if _SD_ERROR and "No module named" in _SD_ERROR:
            return False, "The 'sounddevice' package is not installed."
        return False, f"Audio system unavailable ({_SD_ERROR})."

    try:
        devices = sd.query_devices()
    except Exception as e:
        return False, f"Could not query audio devices ({e})."

    if not any(d.get("max_input_channels", 0) > 0 for d in devices):
        return False, "No microphone or other audio input device was found."

    return True, ""


def error_detail() -> str:
    return _SD_ERROR or ""


## -- DEVICES ----------------------------------------------------------------

def input_devices(include_helpers: bool = False) -> list[dict]:
    """
    Every device with at least one input channel.

    Helper plugins are hidden by default but still resolvable by name, so a
    user who genuinely wants one can still name it in settings.
    """
    sd = _sd()
    if sd is None:
        return []
    try:
        devices = sd.query_devices()
        default_index = _default_index(sd)
    except Exception:
        return []

    out = []
    for index, d in enumerate(devices):
        if d.get("max_input_channels", 0) <= 0:
            continue
        name = d.get("name", f"Device {index}")
        if not include_helpers and name.split(":")[0].strip().lower() in _HELPER_DEVICES:
            continue
        out.append({
            "index": index,
            "name": name,
            "channels": d.get("max_input_channels", 1),
            "samplerate": int(d.get("default_samplerate", DEFAULT_SAMPLE_RATE) or DEFAULT_SAMPLE_RATE),
            "is_default": index == default_index,
        })
    return out


def _default_index(sd) -> Optional[int]:
    try:
        device = sd.default.device
        index = device[0] if isinstance(device, (list, tuple)) else device
        return int(index) if index is not None and int(index) >= 0 else None
    except Exception:
        return None


def default_input() -> Optional[dict]:
    devices = input_devices()
    for d in devices:
        if d["is_default"]:
            return d
    return devices[0] if devices else None


def device_names() -> list[str]:
    return [d["name"] for d in input_devices()]


def resolve(name: str = "") -> tuple[Optional[int], str]:
    """
    Map a configured device name to a PortAudio index.

    Returns (index_or_None, note). An empty name means "system default", which
    resolves to None so PortAudio picks - that is the correct behaviour rather
    than pinning an index, since indices shift when devices come and go.

    A configured name that is no longer present falls back to the default and
    says so, rather than failing outright: unplugging a USB mic should not stop
    the assistant from working with the built-in one.
    """
    name = (name or "").strip()
    if not name or name.lower() in ("default", "system default"):
        return None, ""

    devices = input_devices(include_helpers=True)
    for d in devices:
        if d["name"] == name:
            return d["index"], ""
    for d in devices:
        if name.lower() in d["name"].lower():
            return d["index"], f"Matched '{name}' to '{d['name']}'."

    fallback = default_input()
    if fallback:
        return None, (f"Audio input '{name}' was not found. "
                      f"Falling back to the system default ({fallback['name']}).")
    return None, f"Audio input '{name}' was not found, and there is no default."


## -- PROBE ------------------------------------------------------------------

def probe(device: Optional[int] = None,
          samplerate: int = DEFAULT_SAMPLE_RATE,
          channels: int = 1) -> tuple[bool, str]:
    """
    Actually open the stream briefly.

    query_devices() listing a microphone does not mean it can be opened - it may
    be claimed exclusively by another process, or exist only as a stale entry
    after the hardware was removed. This is the check that matters, and it is
    cheap.
    """
    ok, reason = available()
    if not ok:
        return False, reason

    sd = _sd()
    try:
        with sd.InputStream(samplerate=samplerate, channels=channels,
                            dtype="int16", device=device):
            pass
        return True, ""
    except Exception as e:
        text = str(e)
        if "Invalid number of channels" in text:
            return False, "The selected audio input does not support mono recording."
        if "Device unavailable" in text or "busy" in text.lower():
            return False, "The microphone is in use by another application."
        if "Invalid sample rate" in text:
            return False, (f"The selected audio input does not support "
                           f"{samplerate} Hz recording.")
        return False, f"Could not open the microphone ({e})."


def describe(device: Optional[int]) -> str:
    if device is None:
        d = default_input()
        return f"{d['name']} (system default)" if d else "system default"
    for d in input_devices(include_helpers=True):
        if d["index"] == device:
            return d["name"]
    return f"device {device}"


## -- MODEL CACHE ------------------------------------------------------------

def _hf_cache_dir() -> Path:
    for var in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        value = os.getenv(var)
        if value:
            return Path(value)
    home = os.getenv("HF_HOME")
    if home:
        return Path(home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def model_is_cached(model: str) -> bool:
    """
    Whether faster-whisper already has this model on disk.

    Used to decide whether starting the assistant would trigger a download,
    so the user gets asked first rather than the app silently pulling several
    hundred MB on a metered connection.
    """
    if os.path.isdir(model):
        return True
    cache = _hf_cache_dir()
    if not cache.is_dir():
        return False
    wanted = f"models--Systran--faster-whisper-{model}"
    try:
        for entry in cache.iterdir():
            if entry.name == wanted and any(entry.rglob("*.bin")):
                return True
    except OSError:
        pass
    return False


def model_size_hint(model: str) -> str:
    return {
        "tiny.en": "~75 MB", "tiny": "~75 MB",
        "base.en": "~145 MB", "base": "~145 MB",
        "small.en": "~490 MB", "small": "~490 MB",
    }.get(model, "a few hundred MB")
