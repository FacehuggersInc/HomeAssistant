"""
Switches for starting the panel with one subsystem out of the way.

A startup that freezes gives you a log that stops and nothing else. Reading it
tells you roughly where; it does not tell you whether that stage is the cause or
merely the last thing that got a chance to run. The only way to be sure is to
start without a subsystem and see - and on a wall panel, every attempt is a walk
across a room, so the switches have to be worth the trip.

Environment variables rather than settings: a setting lives in a file the app has
to start to edit.

    HA_SAFE_MODE=1        everything below at once
    HA_NO_BLUETOOTH=1     no D-Bus, no adapter lookup
    HA_NO_ASSISTANT=1     no microphone, no speech model
    HA_NO_WEBENGINE=1     no hidden player page, no embedded browser
"""

from __future__ import annotations

import os

FLAGS = ("HA_NO_BLUETOOTH", "HA_NO_ASSISTANT", "HA_NO_WEBENGINE")


def _on(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in (
        "1", "true", "yes", "on")


def safe_mode() -> bool:
    return _on("HA_SAFE_MODE")


def off(name: str) -> bool:
    """Whether a subsystem has been switched off."""
    return safe_mode() or _on(name)


def no_bluetooth() -> bool:
    return off("HA_NO_BLUETOOTH")


def no_assistant() -> bool:
    return off("HA_NO_ASSISTANT")


def no_webengine() -> bool:
    return off("HA_NO_WEBENGINE")


def describe() -> str:
    """What is switched off, for the startup log."""
    if safe_mode():
        return "safe mode: bluetooth, assistant and webengine are all off"
    off_now = [name for name in FLAGS if _on(name)]
    return ("switched off: " + ", ".join(off_now)) if off_now else ""
