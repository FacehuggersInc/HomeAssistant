from __future__ import annotations

import os
import sys
import threading
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
#ALSA plugins that PortAudio lists as devices. They are routing and format
#conversion, not hardware - "samplerate" is a resampler, "dmix" is a mixer,
#"surround51" is a channel map for a card that may have two speakers.
#
#Every one of them accepts an open() and then behaves in ways nobody chose.
#A settings dropdown full of these is worse than useless: it looks like a
#list of microphones, and picking the wrong entry is how a panel ends up
#hearing nothing with no error to show for it.
_HELPER_DEVICES = {
    # Rate and format conversion
    "lavrate", "samplerate", "speexrate", "speex", "upmix", "vdownmix",
    # Mixing and routing
    "dmix", "dsnoop", "dsp", "asym", "shm", "tee", "plug", "hw", "plughw",
    # Sinks that go nowhere
    "null", "file",
    # Sound servers reached some other way
    "jack", "oss", "pulse_monitor",
    # Channel maps rather than devices
    "front", "rear", "center_lfe", "side",
    "surround21", "surround40", "surround41", "surround50",
    "surround51", "surround71",
    # Aliases for whatever is already the default
    "sysdefault", "spdif", "iec958",
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

#How long to wait for a device to open before giving up on it.
#
#Opening a stream is not cheap and it is not guaranteed to return. PortAudio
#calls into ALSA, which on a machine whose `default` points at a wedged or
#exclusively-held device blocks with no error and no end. There is no way to
#cancel that from outside, so the wait is bounded and the attempt abandoned.
PROBE_TIMEOUT = 6.0


#Tried in order when the chosen device will not open. `default` first because it
#is what the machine says it wants; the servers next, since they are usually what
#`default` was pointing at anyway; real hardware last, which always works when
#nothing else will but bypasses whatever mixing the desktop expects.
FALLBACK_NAMES = ("pipewire", "pulse", "sysdefault")


def working_input(preferred: str = "",
                  timeout: float = None,
                  on_attempt=None) -> tuple:
    """
    The first input device that actually opens.

    Returns (index_or_None, name, note). A listed device is not an openable one,
    and the one the system calls `default` is no more trustworthy than the rest -
    if it points at something wedged, opening it hangs, and the panel used to
    hang with it.

    So each candidate is tried in turn, bounded, and the first that opens wins.
    A panel that comes up listening through `pipewire` because `default` would
    not answer is worth much more than one that does not come up.

    `on_attempt(name, ok, reason)` is called for each candidate as it is tried.
    It exists so the caller can say something while this is happening: the
    search takes seconds per device that will not answer, and a panel sitting
    silently through that looks broken rather than busy. Nothing is said on a
    machine where the first attempt works, which is most of them.
    """
    if timeout is None:
        timeout = PROBE_TIMEOUT

    candidates = []
    if preferred:
        index, note = resolve(preferred)
        candidates.append((index, preferred, note))
    candidates.append((None, "system default", ""))

    listed = {d["name"]: d["index"]
              for d in input_devices(include_helpers=True)}
    for name in FALLBACK_NAMES:
        if name in listed:
            candidates.append((listed[name], name, ""))
    # Real hardware last.
    for d in input_devices(include_helpers=False):
        candidates.append((d["index"], d["name"], ""))

    tried, seen = [], set()
    for index, name, note in candidates:
        key = (index, name)
        if key in seen:
            continue
        seen.add(key)
        ok, reason = probe(index, timeout=timeout)
        if on_attempt is not None:
            try:
                on_attempt(name, ok, reason)
            except Exception:
                # A caller's notification must not decide whether the
                # microphone works.
                pass
        if ok:
            extra = ""
            if tried:
                # Said, because a panel listening through something other than
                # what was asked for should not be a silent substitution.
                extra = (f"Using '{name}' - "
                         f"{'; '.join(tried)} would not open.")
            return index, name, (note or extra)
        tried.append(f"'{name}' ({reason.rstrip('.')})")

    return None, "", ("No audio input could be opened. Tried: "
                      + "; ".join(tried) if tried else "No audio input found.")


def probe(device: Optional[int] = None,
          samplerate: int = DEFAULT_SAMPLE_RATE,
          channels: int = 1,
          timeout: float = PROBE_TIMEOUT) -> tuple[bool, str]:
    """
    Actually open the stream briefly, and give up if it will not.

    query_devices() listing a microphone does not mean it can be opened - it may
    be claimed exclusively by another process, or exist only as a stale entry
    after the hardware was removed. This is the check that matters.

    **Bounded, because it can hang rather than fail.** The open runs on its own
    thread and is abandoned if it does not come back. The thread may stay stuck
    inside PortAudio for the life of the process; it is a daemon, so that costs
    a thread rather than the application. Blocking here froze the panel during
    startup with no error and nothing in the log after the attempt began.
    """
    ok, reason = available()
    if not ok:
        return False, reason

    answer: dict = {}

    def attempt():
        sd = _sd()
        try:
            with sd.InputStream(samplerate=samplerate, channels=channels,
                               dtype="int16", device=device):
                pass
            answer["ok"] = True
        except Exception as e:  # noqa: BLE001 - reported, not handled
            answer["error"] = e

    worker = threading.Thread(target=attempt, daemon=True,
                              name="__audio_probe")
    worker.start()
    worker.join(timeout)

    if worker.is_alive():
        return False, (f"Opening the microphone did not finish within "
                       f"{timeout:.0f}s. The device may be held by another "
                       f"application, or the audio server may not be "
                       f"responding.")
    if answer.get("ok"):
        return True, ""

    e = answer.get("error")
    if e is None:
        return False, "Could not open the microphone."
    try:
        text = str(e)
    except Exception:
        text = ""
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


def model_is_cached(model: str, precision: str = "") -> bool:
    """
    Whether this speech model is already on disk, whichever family it is.

    Used to decide whether starting the assistant would trigger a download,
    so the user gets asked first rather than the app silently pulling several
    hundred MB on a metered connection.

    Whisper-only, it answered False for every Parakeet name forever - there
    is no `models--Systran--faster-whisper-parakeet-v3` and there never will
    be. The caller read that as "not downloaded", so the download prompt came
    back on every start and every settings save, and its Download button led
    to a path that downloads nothing.
    """
    if os.path.isdir(model):
        return True

    try:
        from src.assistant import parakeet
        if parakeet.is_parakeet(model):
            # `precision` decides which files count as "the model" - the
            # int8 export and the full-size one are different downloads.
            return parakeet.cached(model, precision)
    except Exception:
        # No loader, so nothing to look for. False is the honest answer and
        # the assistant falls back to whisper - see WakeWhisper.
        if str(model or "").strip().lower().startswith("parakeet"):
            return False

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
        "parakeet-v3": "~600 MB", "parakeet-v2": "~600 MB",
    }.get(model, "a few hundred MB")
