"""
What a capability needs, in words a person can act on.

A control that quietly does nothing is the worst of the three options. Hiding it
is better, but it leaves somebody wondering where a feature went - and on a wall
panel there is no console to check. Showing it and **saying what is missing when
it is pressed** is better still: the panel is often not the machine you would
install anything from, so the answer has to be readable from across a room and
specific enough to act on later.

Each entry names the tool, why it is needed rather than merely wanted, and how
to get it. The install line is a best guess at the package name; it is offered
as a starting point rather than a promise, since the panel may not be on the
distribution it was written for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Requirement:
    """What one capability needs."""
    capability: str      #what the person was trying to do
    tool: str            #what is missing
    why: str             #what it is for, in one sentence
    install: str = ""    #a starting point, not a promise
    note: str = ""       #anything else worth knowing

    def message(self) -> str:
        parts = [self.why]
        if self.install:
            parts.append(f"Install it with:\n{self.install}")
        if self.note:
            parts.append(self.note)
        return "\n\n".join(parts)

    def title(self) -> str:
        return f"{self.capability} needs {self.tool}"


REQUIREMENTS = {
    "media_keys": Requirement(
        capability="Media controls",
        tool="playerctl",
        why=("These buttons send the media keys a keyboard would, so they "
             "reach whatever the desktop is playing. Nothing on this machine "
             "can send them."),
        install="sudo apt install playerctl",
        note=("xdotool or ydotool also work, though on Wayland they are less "
              "reliable than playerctl."),
    ),
    "volume": Requirement(
        capability="The volume slider",
        tool="a mixer",
        why=("Nothing on this machine can read or set the system volume."),
        install="sudo apt install pipewire-utils   # or pulseaudio-utils",
        note="wpctl, pactl and amixer are all understood, in that order.",
    ),
    "wifi_join": Requirement(
        capability="Joining a network",
        tool="NetworkManager",
        why=("NetworkManager stores the password and brings the connection "
             "back after a reboot. Joining a network any other way would hold "
             "until the next restart and then stop, which is worse than not "
             "offering it."),
        install="sudo apt install network-manager",
        note="Reading the current network works without it.",
    ),
    "wifi": Requirement(
        capability="Wi-Fi",
        tool="wireless tooling",
        why="Nothing on this machine can see a wireless connection.",
        install="sudo apt install network-manager",
    ),
    "bluetooth": Requirement(
        capability="Bluetooth",
        tool="BlueZ",
        why=("BlueZ is the Linux Bluetooth service. Without it running there "
             "is no adapter to turn on and nothing to pair with."),
        install="sudo apt install bluez\nsudo systemctl enable --now bluetooth",
        note=("The panel talks to it over D-Bus using jeepney, which is a "
              "pure-Python package: pip install jeepney"),
    ),
    "bluetooth_dbus": Requirement(
        capability="Bluetooth",
        tool="the jeepney package",
        why=("The panel talks to BlueZ over D-Bus, and jeepney is what speaks "
             "it. It is pure Python, so there is nothing to compile."),
        install="pip install jeepney",
    ),
}


#Import failures whose message names something nobody installs.
#
#The module in an ImportError is usually also the package to install, and
#when it is there is nothing to add. These are the cases where it is not, and
#each one has cost somebody an afternoon: the name in the message is the
#thing they searched for, and it does not exist under that name.
#
#Matched against the rendered message rather than `error.name`, because only
#ImportError carries that attribute and the audio stack fails with OSError as
#often as not.
IMPORT_HINTS = (
    ("pkg_resources",
     "pkg_resources was REMOVED in setuptools v82, so installing setuptools "
     "does not bring it back and pinning it below 82 holds the whole "
     "environment on a superseded build tool. Whatever asked for it needs "
     "standing in for: src/system/pkg_resources_shim.py does that for "
     "webrtcvad, so seeing this from the speech process means the shim did "
     "not run."),
    ("PortAudio",
     "PortAudio is the C library sounddevice records through. It is a system "
     "package rather than a Python one, so pip will not have brought it. "
     "Install it with: sudo apt install libportaudio2"),
)


def explain_import(error: BaseException) -> str:
    """
    An import failure, with the thing to install in it where the two differ.

    `str(error)` alone is enough whenever the missing module is the package
    somebody would install. It is not enough when it is not: "No module named
    'pkg_resources'" reads as a missing dependency that was somehow left out
    of requirements.txt, and the speech process repeats it on every restart
    while the answer is a package with a different name entirely.
    """
    said = f"{type(error).__name__}: {error}"
    for needle, hint in IMPORT_HINTS:
        if needle in said:
            return f"{said}. {hint}"
    return said


def get(key: str) -> Optional[Requirement]:
    return REQUIREMENTS.get(key)

def explain(client, key: str) -> bool:
    """
    Put the requirement on screen. Returns whether there was one to show.

    Used as the press handler for a control that cannot work yet, so pressing
    it answers the question it raises rather than doing nothing.
    """
    requirement = REQUIREMENTS.get(key)
    if requirement is None:
        return False
    try:
        client.alert(requirement.title(), requirement.message())
    except Exception:
        return False
    return True
