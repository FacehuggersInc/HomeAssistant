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
