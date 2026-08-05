"""
The outputs the system itself lists, and how to play through one.

PortAudio answers "what can play sound" with ALSA's view: `hw:CARD=…`, plugin
devices, `pulse`, `default`. A desktop answers it with **sinks** -
"Built-in Audio Analog Stereo", an HDMI output, a USB speaker - which is the
list in the system's own sound settings and the list somebody recognises.
They are different layers, and a dropdown built from the first one shares no
names with the second.

So the sinks are read from the server and the ALSA layer is left to route:
everything plays through PortAudio's `pulse` device, and which sink that
lands on is chosen per stream with `PULSE_SINK`. Nothing here changes the
system's default output - a panel is one program on the machine, and a
dropdown inside it should not move every other program's audio.

`pactl` talks to PipeWire through `pipewire-pulse`, which is how both a
PipeWire and a PulseAudio system end up answering the same question the same
way. No server, no sinks, and the caller falls back to the ALSA list.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional

#Long enough for a busy server, short enough that a hung one does not hold up
#a settings page.
CALL_TIMEOUT = 2.0

#What PortAudio calls the route into the sound server. The first that exists.
SERVER_DEVICES = ("pulse", "pipewire", "default")


def _run(args: list) -> str:
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=CALL_TIMEOUT)
    except Exception:
        return ""
    return done.stdout if done.returncode == 0 else ""


def available() -> bool:
    """Whether there is a sound server that can be asked about sinks."""
    return bool(shutil.which("pactl")) and bool(sinks())


def sinks() -> list:
    """
    Every output the server knows, as `{"name", "description"}`.

    The description is what the system's own settings show and what goes in
    the dropdown; the name is the identifier `PULSE_SINK` wants. Both are
    kept, because the description is not unique in principle and the name is
    not readable in practice.
    """
    out = _run(["pactl", "list", "sinks"])
    if not out:
        return []

    found, current = [], {}
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Sink #"):
            if current.get("name"):
                found.append(current)
            current = {}
        elif stripped.startswith("Name:"):
            current["name"] = stripped.partition(":")[2].strip()
        elif stripped.startswith("Description:"):
            current["description"] = stripped.partition(":")[2].strip()
    if current.get("name"):
        found.append(current)

    for sink in found:
        # A sink with no description is unusual and still has to be pickable.
        sink.setdefault("description", sink["name"])
    return found


def default_sink() -> str:
    """The server's own default, or empty."""
    out = _run(["pactl", "get-default-sink"])
    return out.strip() if out else ""


def name_for(description: str) -> str:
    """
    The sink name behind a description, or empty if nothing matches.

    Matched on the description first, then on the name, so a setting saved
    before this existed - or edited by hand - still resolves.
    """
    wanted = str(description or "").strip()
    if not wanted:
        return ""
    catalogue = sinks()
    for sink in catalogue:
        if sink.get("description") == wanted:
            return sink["name"]
    for sink in catalogue:
        if sink.get("name") == wanted:
            return sink["name"]
    return ""


def server_device_index(sounddevice) -> Optional[int]:
    """
    PortAudio's index for the route into the sound server.

    None if there is not one, which means the sinks cannot be reached from
    here and the caller should use the ALSA list instead.
    """
    try:
        devices = list(sounddevice.query_devices())
    except Exception:
        return None
    for wanted in SERVER_DEVICES:
        for index, device in enumerate(devices):
            if not device.get("max_output_channels", 0):
                continue
            if str(device.get("name") or "").split(":")[0].strip().lower() == wanted:
                return index
    return None


class routed:
    """
    A `with` block whose audio plays on one sink.

    `PULSE_SINK` is read when a stream is created, so it has to be set before
    the stream opens and put back afterwards - a process-wide variable left
    pointing at somebody's HDMI output is the next sound going somewhere
    nobody asked for.

    An empty sink name does nothing at all, which is what "Default" means.
    """

    VARIABLE = "PULSE_SINK"

    def __init__(self, sink_name: str = ""):
        self.sink_name = str(sink_name or "").strip()
        self._before = None
        self._had = False

    def __enter__(self):
        if not self.sink_name:
            return self
        self._had = self.VARIABLE in os.environ
        self._before = os.environ.get(self.VARIABLE)
        os.environ[self.VARIABLE] = self.sink_name
        return self

    def __exit__(self, *exc):
        if not self.sink_name:
            return False
        if self._had:
            os.environ[self.VARIABLE] = self._before or ""
        else:
            os.environ.pop(self.VARIABLE, None)
        return False
