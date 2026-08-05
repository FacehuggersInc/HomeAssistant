"""
System volume, via whatever the machine actually has.

Every backend here is optional. The panel asks `available()` first and hides
the slider when nothing answers, because a slider that silently does nothing
is worse than no slider - on a wall panel there is no console to check.

Nothing in here raises. A missing binary, a locked device or a reply in an
unexpected format all come back as "unavailable" rather than taking a control
down with them.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Optional

# Commands can hang when a sound server is wedged. Short, because this runs on
# the UI thread when the slider moves.
TIMEOUT = 2.0

_backend: str = None      # resolved once, then reused
_probed:  bool = False


def _run(args: list) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=TIMEOUT,
            # A backend prompting on stdin would block until the timeout.
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


## -- backend detection

def _detect() -> str:
    if os.name == "nt":
        try:
            from ctypes import cast, POINTER          # noqa: F401
            from comtypes import CLSCTX_ALL           # noqa: F401
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # noqa: F401
            return "pycaw"
        except Exception:
            return None

    # PipeWire first: on a system running both, pactl talks to the
    # compatibility shim and wpctl talks to the real thing.
    if shutil.which("wpctl") and _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]):
        return "wpctl"
    if shutil.which("pactl") and _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"]):
        return "pactl"
    if shutil.which("amixer") and _run(["amixer", "get", "Master"]):
        return "amixer"
    return None


def backend() -> str:
    global _backend, _probed
    if not _probed:
        _probed = True
        _backend = _detect()
    return _backend


def available() -> bool:
    return backend() is not None


def describe() -> str:
    return backend() or "none"


## -- read

def get_volume() -> int:
    """Current volume 0-100, or -1 when it cannot be read."""
    name = backend()

    if name == "wpctl":
        # "Volume: 0.65" — a float, and it can exceed 1.0 when boosted.
        out = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        match = re.search(r"([0-9]*\.?[0-9]+)", out)
        if match:
            return max(0, min(100, int(round(float(match.group(1)) * 100))))

    elif name == "pactl":
        out = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        match = re.search(r"(\d+)%", out)
        if match:
            return max(0, min(100, int(match.group(1))))

    elif name == "amixer":
        out = _run(["amixer", "get", "Master"])
        match = re.search(r"\[(\d+)%\]", out)
        if match:
            return max(0, min(100, int(match.group(1))))

    elif name == "pycaw":
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            speakers = AudioUtilities.GetSpeakers()
            interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(interface, POINTER(IAudioEndpointVolume))
            return int(round(volume.GetMasterVolumeLevelScalar() * 100))
        except Exception:
            return -1

    return -1


## -- write

def set_volume(percent: int) -> bool:
    percent = max(0, min(100, int(percent)))
    name = backend()

    if name == "wpctl":
        return _set_ok(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@",
                        f"{percent / 100:.2f}"])

    if name == "pactl":
        return _set_ok(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"])

    if name == "amixer":
        return _set_ok(["amixer", "set", "Master", f"{percent}%"])

    if name == "pycaw":
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            speakers = AudioUtilities.GetSpeakers()
            interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(interface, POINTER(IAudioEndpointVolume))
            volume.SetMasterVolumeLevelScalar(percent / 100.0, None)
            return True
        except Exception:
            return False

    return False


def _set_ok(args: list) -> bool:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False


##THE MICROPHONE

#The source the panel is listening on. `@DEFAULT_SOURCE@` follows whatever the
#system chose, which is what "Default" in the settings means; a named input
#device is resolved to its source below.
def _source_name(preferred: str = "") -> str:
    """
    The mixer's name for the input to mute, or the system default.

    A saved input device is an ALSA name from PortAudio's list, and the mixer
    speaks in sources. Matched on the card fragment the two share, because
    that is all they have in common - "HDA Intel PCH: ALC897 Analog (hw:1,0)"
    and "alsa_input.pci-0000_00_1f.3.analog-stereo" are the same input under
    two naming schemes and neither contains the other.
    """
    wanted = str(preferred or "").strip()
    if not wanted or wanted.lower() == "default":
        return ""

    if backend() not in ("wpctl", "pactl"):
        return ""

    out = _run(["pactl", "list", "short", "sources"])
    if not out:
        return ""

    # The distinctive part of the ALSA name: the card, without the plugin
    # wrapper or the channel numbers.
    fragment = wanted.split(":")[0].strip().lower().replace(" ", "_")
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[1].strip()
        if not name or name.endswith(".monitor"):
            continue
        if fragment and fragment in name.lower():
            return name
    return ""


def mic_muted(preferred: str = "") -> Optional[bool]:
    """
    Whether the input is muted. None means the question cannot be answered.

    None rather than False, so a caller does not treat "no mixer here" as
    "the microphone is live" and draw a control that does nothing.
    """
    name = backend()
    target = _source_name(preferred)

    if name == "wpctl":
        out = _run(["wpctl", "get-volume",
                    target or "@DEFAULT_AUDIO_SOURCE@"])
        if not out:
            return None
        return "[MUTED]" in out.upper()

    if name == "pactl":
        out = _run(["pactl", "get-source-mute", target or "@DEFAULT_SOURCE@"])
        if not out:
            return None
        return "yes" in out.lower()

    if name == "amixer":
        out = _run(["amixer", "get", "Capture"])
        if not out:
            return None
        return "[off]" in out.lower()

    return None


def set_mic_muted(muted: bool, preferred: str = "") -> bool:
    """Mute or unmute the input itself, not the panel's use of it."""
    name = backend()
    target = _source_name(preferred)
    flag = "1" if muted else "0"

    if name == "wpctl":
        return _set_ok(["wpctl", "set-mute",
                        target or "@DEFAULT_AUDIO_SOURCE@", flag])

    if name == "pactl":
        return _set_ok(["pactl", "set-source-mute",
                        target or "@DEFAULT_SOURCE@", flag])

    if name == "amixer":
        return _set_ok(["amixer", "set", "Capture",
                        "nocap" if muted else "cap"])

    return False


def toggle_mic_muted(preferred: str = "") -> Optional[bool]:
    """Flip it, and answer with where it ended up."""
    now = mic_muted(preferred)
    if now is None:
        return None
    return not now if set_mic_muted(not now, preferred) else now
