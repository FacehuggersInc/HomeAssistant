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
lands on is chosen per stream with `PULSE_SINK`. The output dropdown moves
the panel's own audio only - a panel is one program on the machine, and a
setting inside it should not move every other program's sound.

`set_default_sink` is the one thing here that does. It exists for the
Bluetooth follow, where the opposite is true: a speaker somebody just
connected is meant to get everything, including the music, which plays
through a browser page that follows the system default and cannot be routed
per stream from in here.

`pactl` talks to PipeWire through `pipewire-pulse`, which is how both a
PipeWire and a PulseAudio system end up answering the same question the same
way. No server, no sinks, and the caller falls back to the ALSA list.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
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


def description_for(name: str) -> str:
    """The readable description behind a sink name, or the name itself."""
    wanted = str(name or "").strip()
    if not wanted:
        return ""
    for sink in sinks():
        if sink.get("name") == wanted:
            return sink.get("description") or wanted
    return wanted


def set_default_sink(name: str) -> bool:
    """
    Point the whole machine at one output.

    This is the exception to the rule above, and it is deliberate. Per-stream
    routing moves the panel's OWN audio and nothing else, which is right for
    a program sharing a desktop and wrong for the one thing on a wall panel:
    music plays through a hidden browser page, and a browser follows the
    system default. Moving only what this process plays would put the
    assistant's voice on a Bluetooth speaker and leave the music behind.

    Used only by the Bluetooth follow, and only for a speaker somebody
    deliberately connected.
    """
    wanted = str(name or "").strip()
    if not wanted:
        return False
    try:
        done = subprocess.run(["pactl", "set-default-sink", wanted],
                              capture_output=True, text=True,
                              timeout=CALL_TIMEOUT)
    except Exception:
        return False
    return done.returncode == 0


def _sink_names_by_index() -> dict:
    """
    `{index: name}` for every sink.

    Needed because the two short listings do not speak the same language:
    `sink-inputs` names the sink it is on by INDEX, while everything else
    here works in names. Comparing the two directly compares "1" against
    "bluez_output.…", which never matches - so every stream looks misplaced
    and gets moved on every check.
    """
    out = _run(["pactl", "list", "short", "sinks"])
    if not out:
        return {}

    table = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip().isdigit():
            table[parts[0].strip()] = parts[1].strip()
    return table


def sink_inputs() -> list:
    """
    Streams currently playing, as `[(index, sink_name)]`.

    Needed because a new default only applies to streams that START after it.
    Everything already playing stays where it is, so switching while music is
    on does nothing audible until the track changes - which reads as the
    switch not having worked.
    """
    out = _run(["pactl", "list", "short", "sink-inputs"])
    if not out:
        return []

    names = _sink_names_by_index()
    found = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        index, sink = parts[0].strip(), parts[1].strip()
        if not index.isdigit():
            continue
        # Left as-is when the index is unknown - a sink that appeared between
        # the two listings. An unrecognised sink reads as "not where it
        # should be", which is the safe way round: it gets moved.
        found.append((index, names.get(sink, "")))
    return found


def move_streams_to(name: str) -> int:
    """
    Move everything already playing onto one sink. Returns how many moved.

    Per stream rather than all at once: a stream can end between listing and
    moving, and one that has gone should not stop the rest from arriving.
    """
    wanted = str(name or "").strip()
    if not wanted:
        return 0

    moved = 0
    for index, current in sink_inputs():
        if current == wanted:
            continue
        try:
            done = subprocess.run(["pactl", "move-sink-input", index, wanted],
                                  capture_output=True, text=True,
                                  timeout=CALL_TIMEOUT)
        except Exception:
            continue
        if done.returncode == 0:
            moved += 1
    return moved


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

    #Both, because which one is read depends on which plugin PortAudio's
    #server device turns out to be.
    #
    #`PULSE_SINK` is read by the PulseAudio ALSA plugin. PipeWire's own ALSA
    #plugin does not read it at all - it reads `PIPEWIRE_NODE`, and a sink's
    #`Name` is its node name, so the same string answers both. `default` is
    #whichever of the two the machine has wired up.
    #
    #Only the first was set, and SERVER_DEVICES prefers `pulse` but falls back
    #to `pipewire`. On a PipeWire desktop with no alsa-plugins - which is the
    #ordinary shape, and is what this panel has: PortAudio lists `pipewire`
    #and `default` and no `pulse` - the dropdown filled with real sink names,
    #the setting saved, the variable was set, and nothing moved. Silently.
    #
    #Setting the one the other plugin ignores costs nothing.
    VARIABLES = ("PULSE_SINK", "PIPEWIRE_NODE")

    def __init__(self, sink_name: str = ""):
        self.sink_name = str(sink_name or "").strip()
        #name -> what was there before, or None when it was not set at all.
        #The difference matters: putting an empty string back is not the same
        #as leaving the variable unset, and one of them routes nowhere.
        self._before: dict = {}

    def __enter__(self):
        if not self.sink_name:
            return self
        for name in self.VARIABLES:
            self._before[name] = os.environ.get(name)
            os.environ[name] = self.sink_name
        return self

    def __exit__(self, *exc):
        if not self.sink_name:
            return False
        for name, was in self._before.items():
            if was is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = was
        self._before = {}
        return False


#How many times the repair below looks for our own stream, and how long it
#waits between looks.
#
#A stream is registered with the sound server a moment AFTER PortAudio opens
#it, so the first look can legitimately find nothing - and treating that as
#"nothing to move" is how the repair misses the very sound it was started for.
ROUTE_ATTEMPTS = 4
ROUTE_GAP = 0.15


def _sink_input_blocks() -> list:
    """
    `pactl list sink-inputs`, as one dict per stream.

    Split out from the reading so it can be driven against captured output
    without a sound server.
    """
    out = _run(["pactl", "list", "sink-inputs"])
    if not out:
        return []

    blocks, current = [], None
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Sink Input #"):
            if current is not None:
                blocks.append(current)
            current = {"index": stripped.partition("#")[2].strip()}
        elif current is None:
            continue
        elif stripped.startswith("Sink:"):
            current["sink"] = stripped.partition(":")[2].strip()
        elif stripped.startswith("application.process.id"):
            current["pid"] = stripped.partition("=")[2].strip().strip('"')
    if current is not None:
        blocks.append(current)
    return blocks


def own_sink_inputs() -> list:
    """
    The streams THIS process is playing, as `[(index, sink_name)]`.

    Matched on `application.process.id`, so the panel moves its own audio and
    leaves everything else on the machine where it is. That is the whole
    difference between this and `sink_inputs()`, which is what the Bluetooth
    follow uses and which deliberately moves the lot.
    """
    blocks = _sink_input_blocks()
    if not blocks:
        return []
    names = _sink_names_by_index()
    pid = str(os.getpid())
    return [(block["index"], names.get(block.get("sink", ""), ""))
            for block in blocks if block.get("pid") == pid]


def move_own_streams_to(name: str) -> int:
    """Move this process's streams onto one sink. Returns how many moved."""
    wanted = str(name or "").strip()
    if not wanted:
        return 0

    moved = 0
    for index, current in own_sink_inputs():
        if current == wanted:
            continue
        try:
            done = subprocess.run(["pactl", "move-sink-input", index, wanted],
                                  capture_output=True, text=True,
                                  timeout=CALL_TIMEOUT)
        except Exception:
            continue
        if done.returncode == 0:
            moved += 1
    return moved


def ensure_routed(sink_name: str, log=None) -> None:
    """
    Confirm a stream that has just started really is on the chosen sink, and
    move it if it is not.

    `routed()` asks by environment variable, which is free and is right
    whenever the plugin underneath reads the variable. When it does not, there
    is no error and no fallback - the sound simply comes out of the old place,
    which is exactly the bug this pair was written for.

    So the answer is checked rather than assumed. **On a worker**: `pactl` is
    a subprocess and this is called from the phrase path, where the handoff's
    rule about measuring the cost applies. Nothing here blocks the audio.

    Costs, since they are real: two `pactl` reads per sound that names a sink,
    on a thread of their own, plus one `move-sink-input` per stream that
    turned out to be in the wrong place. Nothing at all when the output is
    `Default`, which is the shipped setting.
    """
    wanted = str(sink_name or "").strip()
    if not wanted:
        return

    def work():
        for attempt in range(ROUTE_ATTEMPTS):
            try:
                mine = own_sink_inputs()
            except Exception as e:
                if log:
                    log("debug", f"[Audio] Could not check the output sink: {e}")
                return
            if mine:
                misplaced = [index for index, current in mine
                             if current != wanted]
                if not misplaced:
                    return
                moved = move_own_streams_to(wanted)
                if log:
                    # Worth a line: it means the environment variable was not
                    # read, which is the difference between a switch that
                    # works and one that only appears to.
                    log("debug", f"[Audio] The output setting was not honoured "
                                 f"by the driver - moved {moved} stream(s) onto "
                                 f"'{wanted}'.")
                return
            time.sleep(ROUTE_GAP)

    threading.Thread(target=work, name="__sink_route", daemon=True).start()
