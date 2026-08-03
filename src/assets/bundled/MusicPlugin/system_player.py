"""
Whatever the machine is playing.

Every Linux media player worth having speaks **MPRIS** over D-Bus - Spotify,
VLC, Firefox, mpv, Rhythmbox - so one reader covers all of them without any of
them knowing this panel exists.

This is the default source. It shows what is already playing, and a plugin that
plays something itself takes over while it does.

**Nothing this process owns counts.** The panel plays music through a hidden
browser page, and a browser page can put itself on the bus like any other
player - so without a check the panel reads its own playback back as
somebody else's, hands the card over to itself and reopens what it just
closed. Ownership is decided by process, not by name: the name belongs to
whichever engine is embedded and would have to be guessed at.

`playerctl` is used when it is installed, because it already solves picking
between several players. Failing that, `busctl` is queried directly - it ships
with systemd, so on the machines this panel runs on it is always there.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from src.registries.player_registry import (
    NowPlaying, PLAYING, PAUSED, STOPPED,
)


CALL_TIMEOUT = 3.0

#How long the list of this process's own children is trusted for. Renderer
#processes come and go, and re-reading /proc on every poll is a few hundred
#file reads for an answer that rarely changes.
OWN_PIDS_TTL = 10.0

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"

#MPRIS says Playing / Paused / Stopped
STATE_MAP = {
    "playing": PLAYING,
    "paused":  PAUSED,
    "stopped": STOPPED,
}


def _run(args: list, timeout: float = CALL_TIMEOUT) -> tuple:
    """(ok, stdout). Never raises, never hangs."""
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout, check=False)
        return done.returncode == 0, (done.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return False, ""


class SystemPlayer:
    """Reads MPRIS. Read-mostly, with the controls every player supports."""

    def __init__(self, client, owner: str):
        self.client = client
        self.owner = owner
        self._bus = ""
        self._have_playerctl = bool(shutil.which("playerctl"))
        self._have_busctl = bool(shutil.which("busctl"))
        self._own_pids: set = set()
        self._own_pids_at = 0.0

    @property
    def available(self) -> bool:
        return self._have_playerctl or self._have_busctl

    def describe(self) -> str:
        if self._have_playerctl:
            return "playerctl"
        if self._have_busctl:
            return "busctl"
        return "unavailable"

    ## -- what belongs to this process

    def own_pids(self) -> set:
        """
        This process and everything it has spawned.

        Read from `/proc` rather than asked of Qt: the pid on the bus belongs
        to whichever process actually registered, which for an embedded
        browser is sometimes this one and sometimes a child of it, and both
        answers have to count as ours.
        """
        import time
        if self._own_pids and time.time() - self._own_pids_at < OWN_PIDS_TTL:
            return self._own_pids

        mine = os.getpid()
        children: dict = {}
        try:
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    status = (entry / "status").read_text(encoding="utf-8",
                                                          errors="replace")
                except OSError:
                    continue    # it exited between the listing and the read
                for line in status.splitlines():
                    if line.startswith("PPid:"):
                        parent = line.split()[-1]
                        if parent.isdigit():
                            children.setdefault(int(parent), []).append(
                                int(entry.name))
                        break
        except OSError:
            # No /proc worth reading. Own pid alone is better than nothing,
            # and covers the common case where the engine registers in-process.
            self._own_pids = {mine}
            self._own_pids_at = time.time()
            return self._own_pids

        found, queue = {mine}, [mine]
        while queue:
            for child in children.get(queue.pop(), []):
                if child not in found:
                    found.add(child)
                    queue.append(child)

        self._own_pids = found
        self._own_pids_at = time.time()
        return found

    #`org.mpris.MediaPlayer2.chromium.instance1234`
    _INSTANCE = re.compile(r"\.instance(\d+)$")

    def is_ours(self, name: str, pid: str = "") -> bool:
        """
        Whether this player is the panel's own hidden page.

        Two ways of telling, because only one of them is always available.
        The pid beside the name in `busctl list` is definitive; the
        `.instance<pid>` a browser puts on the end of its bus name is what
        there is to go on when only playerctl is installed.
        """
        own = self.own_pids()
        if str(pid).isdigit() and int(pid) in own:
            return True
        found = self._INSTANCE.search(str(name or ""))
        return bool(found) and int(found.group(1)) in own

    ## -- finding a player

    def _find_bus(self) -> str:
        """
        The bus name of something that is playing, preferred over something
        that is merely open.

        A paused Firefox tab and a playing Spotify are both on the bus, and
        showing the paused one would be wrong.
        """
        if not self._have_busctl:
            return ""
        ok, out = _run(["busctl", "--user", "list", "--no-legend"])
        if not ok:
            return ""

        names = []
        for line in out.splitlines():
            fields = line.split()
            if not fields or not fields[0].startswith(MPRIS_PREFIX):
                continue
            # `busctl list` prints NAME then PID, which is the one answer
            # about ownership that needs no guessing.
            if self.is_ours(fields[0], fields[1] if len(fields) > 1 else ""):
                continue
            names.append(fields[0])
        if not names:
            return ""

        for name in names:
            if str(self._property(name, "PlaybackStatus")).lower() == "playing":
                return name
        return names[0]

    def _property(self, bus: str, name: str) -> str:
        ok, out = _run(["busctl", "--user", "get-property", bus,
                        "/org/mpris/MediaPlayer2", PLAYER_IFACE, name])
        return out if ok else ""

    ## -- reading

    def read(self) -> Optional[NowPlaying]:
        """A snapshot, or None when nothing is on the bus."""
        if self._have_playerctl:
            playing = self._read_playerctl()
            if playing is not None:
                return playing
        if self._have_busctl:
            return self._read_busctl()
        return None

    #What one line of `playerctl -a metadata` carries. `-a` prints a line per
    #player, which is one subprocess for every player rather than one each.
    PLAYERCTL_FORMAT = ("{{playerName}}\x1f{{status}}\x1f{{title}}\x1f{{artist}}"
                        "\x1f{{album}}\x1f{{mpris:artUrl}}\x1f{{mpris:length}}"
                        "\x1f{{position}}")

    def _read_playerctl(self) -> Optional[NowPlaying]:
        # Every player at once, so this process's own can be dropped before
        # one is chosen. Asking playerctl for "the" player picks it first and
        # gives no way to say the pick was wrong.
        ok, out = _run(["playerctl", "-a", "metadata",
                        "--format", self.PLAYERCTL_FORMAT])
        if not ok or not out.strip():
            return None

        theirs = []
        for line in out.splitlines():
            parts = (line.split("\x1f") + [""] * 8)[:8]
            if not parts[0].strip() or self.is_ours(parts[0].strip()):
                continue
            theirs.append(parts)
        if not theirs:
            return None

        # Something playing, over something merely open: a paused browser tab
        # and a playing Spotify are both on the bus.
        chosen = next((p for p in theirs
                       if p[1].strip().lower() == "playing"), theirs[0])
        player, status, title, artist, album, art, length, position = chosen

        return NowPlaying(
            title    = title,
            artist   = artist,
            album    = album,
            art_url  = self._local_art(art),
            state    = STATE_MAP.get(status.strip().lower(), STOPPED),
            # MPRIS is microseconds.
            duration = self._number(length) / 1_000_000.0,
            position = self._number(position) / 1_000_000.0,
            source   = f"system:{player.strip() or 'mpris'}",
            track_id = f"{title}|{artist}",
        )

    def _read_busctl(self) -> Optional[NowPlaying]:
        bus = self._bus or self._find_bus()
        if not bus:
            return None
        self._bus = bus

        status = self._parse_variant(self._property(bus, "PlaybackStatus"))
        if not status:
            # It left the bus. Looked up again next time rather than kept.
            self._bus = ""
            return None

        metadata = self._property(bus, "Metadata")
        fields = self._parse_metadata(metadata)
        position = self._number(self._parse_variant(
            self._property(bus, "Position")))

        return NowPlaying(
            title    = fields.get("xesam:title", ""),
            artist   = fields.get("xesam:artist", ""),
            album    = fields.get("xesam:album", ""),
            art_url  = self._local_art(fields.get("mpris:artUrl", "")),
            state    = STATE_MAP.get(status.lower(), STOPPED),
            duration = self._number(fields.get("mpris:length", 0)) / 1_000_000.0,
            position = position / 1_000_000.0,
            source   = f"system:{bus[len(MPRIS_PREFIX):]}",
            track_id = fields.get("mpris:trackid", "")
                       or f"{fields.get('xesam:title', '')}",
        )

    ## -- parsing

    @staticmethod
    def _parse_variant(raw: str) -> str:
        """busctl prints `s "Playing"` or `x 12345`."""
        raw = str(raw or "").strip()
        if not raw:
            return ""
        parts = raw.split(None, 1)
        value = parts[1] if len(parts) > 1 else parts[0]
        return value.strip().strip('"')

    #a quoted string, or a bare token
    _TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|(\S+)')

    @classmethod
    def _tokens(cls, raw: str) -> list:
        """(is_quoted, text) for each token, so a value cannot run into a key."""
        out = []
        for quoted, bare in cls._TOKEN.findall(raw or ""):
            if bare:
                out.append((False, bare))
            else:
                out.append((True, quoted))
        return out

    @classmethod
    def _parse_metadata(cls, raw: str) -> dict:
        """
        The `a{sv}` dictionary busctl prints for Metadata.

        Walked as tokens rather than matched with one expression. The format
        is `"key" type value`, where the type decides how much of what follows
        belongs to it - `s` takes one string, `as` takes a count and then that
        many. A single regex cannot know that, and a greedy one reads the next
        key as part of the previous value.
        """
        fields = {}
        tokens = cls._tokens(raw)

        index = 0
        # Skip the leading `a{sv} 5`.
        while index < len(tokens) and not tokens[index][0]:
            index += 1

        while index < len(tokens):
            quoted, key = tokens[index]
            if not quoted:
                index += 1
                continue
            if index + 1 >= len(tokens):
                break

            kind = tokens[index + 1][1]
            index += 2

            if kind.startswith("a") and len(kind) > 1:
                # An array: a count, then that many values.
                if index >= len(tokens):
                    break
                try:
                    count = int(tokens[index][1])
                except (TypeError, ValueError):
                    count = 0
                index += 1
                values = []
                for _ in range(count):
                    if index >= len(tokens):
                        break
                    values.append(tokens[index][1])
                    index += 1
                # Joined, since a widget shows one line.
                fields[key] = ", ".join(v for v in values if v)
            else:
                if index < len(tokens):
                    fields[key] = tokens[index][1]
                    index += 1

        return fields

    @staticmethod
    def _number(value) -> float:
        try:
            return float(str(value).strip().strip('"') or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _local_art(url: str) -> str:
        """
        MPRIS art is often a `file://` path, which nothing can fetch over HTTP.

        Returned as-is so the caller can decide; a widget loading over the
        network will simply find nothing, which is the same as no art.
        """
        return str(url or "").strip()

    ## -- controls

    def _control(self, command: str) -> None:
        if self._have_playerctl:
            _run(["playerctl", command])
            return
        bus = self._bus or self._find_bus()
        if not bus:
            return
        method = {"play-pause": "PlayPause", "play": "Play", "pause": "Pause",
                  "next": "Next", "previous": "Previous",
                  "stop": "Stop"}.get(command)
        if not method:
            return
        _run(["busctl", "--user", "call", bus, "/org/mpris/MediaPlayer2",
              PLAYER_IFACE, method])

    def play(self, *_a, **_k):   self._control("play")
    def pause(self):             self._control("pause")
    def toggle(self):            self._control("play-pause")
    def stop_playing(self):      self._control("stop")
    def next(self):              self._control("next")
    def previous(self):          self._control("previous")

    def volume(self, percent: int = None):
        """
        MPRIS volume is 0.0 to 1.0.

        Read and written through playerctl only: busctl needs the variant
        spelled out and getting it wrong sets somebody's music to silence.
        """
        if not self._have_playerctl:
            return None
        if percent is None:
            ok, out = _run(["playerctl", "volume"])
            try:
                return int(round(float(out) * 100)) if ok else None
            except (TypeError, ValueError):
                return None
        _run(["playerctl", "volume", f"{max(0, min(100, int(percent))) / 100:.2f}"])
        return None
