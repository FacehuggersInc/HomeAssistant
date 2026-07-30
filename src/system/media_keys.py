"""
Play, pause, next, previous - for whatever the machine is playing.

Not the panel's own player. These are the media keys a keyboard sends, so they
reach whatever the desktop considers to be playing: a browser tab, a music
player, anything that registered for them. The panel's own controls are on the
now-playing card; this is for everything else.

Three ways, tried in order. `playerctl` speaks MPRIS directly and is the most
likely to be installed on a desktop that plays anything. Failing that the keys
are synthesised, which needs a tool that can do it - and on Wayland that is
harder than on X11, so it may simply not be available.
"""

from __future__ import annotations

import shutil
import subprocess

CALL_TIMEOUT = 3.0

#(playerctl command, xdotool key, ydotool key code)
ACTIONS = {
    "play":     ("play",       "XF86AudioPlay", "164"),
    "pause":    ("pause",      "XF86AudioPlay", "164"),
    "toggle":   ("play-pause", "XF86AudioPlay", "164"),
    "next":     ("next",       "XF86AudioNext", "163"),
    "previous": ("previous",   "XF86AudioPrev", "165"),
    "stop":     ("stop",       "XF86AudioStop", "166"),
}


def _run(args: list) -> bool:
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=CALL_TIMEOUT, check=False)
        return done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def backend() -> str:
    """Which tool will be used, or "" when none can be."""
    if shutil.which("playerctl"):
        return "playerctl"
    if shutil.which("xdotool"):
        return "xdotool"
    if shutil.which("ydotool"):
        return "ydotool"
    return ""


def available() -> bool:
    return bool(backend())


def send(action: str) -> bool:
    """
    Send one media action. Returns whether anything took it.

    False is a real answer rather than a failure: a machine with nothing
    playing and no media-key tool has nothing to send to, and the caller
    should hide the buttons rather than offer ones that do nothing.
    """
    entry = ACTIONS.get(str(action or "").lower())
    if entry is None:
        return False
    player_cmd, x_key, y_code = entry

    name = backend()
    if name == "playerctl":
        return _run(["playerctl", player_cmd])
    if name == "xdotool":
        return _run(["xdotool", "key", x_key])
    if name == "ydotool":
        # ydotool needs its daemon running, and says so on stderr if not.
        return _run(["ydotool", "key", f"{y_code}:1", f"{y_code}:0"])
    return False


def describe() -> str:
    name = backend()
    return name or "unavailable"
