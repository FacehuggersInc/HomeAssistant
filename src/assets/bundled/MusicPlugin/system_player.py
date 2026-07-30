"""
Whatever the machine is playing.

Every Linux media player worth having speaks **MPRIS** over D-Bus - Spotify,
VLC, Firefox, mpv, Rhythmbox - so one reader covers all of them without any of
them knowing this panel exists.

This is the default source. It shows what is already playing, and a plugin that
plays something itself takes over while it does.

`playerctl` is used when it is installed, because it already solves picking
between several players. Failing that, `busctl` is queried directly - it ships
with systemd, so on the machines this panel runs on it is always there.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Optional

from src.registries.player_registry import (
    NowPlaying, PLAYING, PAUSED, STOPPED,
)


CALL_TIMEOUT = 3.0

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

    @property
    def available(self) -> bool:
        return self._have_playerctl or self._have_busctl

    def describe(self) -> str:
        if self._have_playerctl:
            return "playerctl"
        if self._have_busctl:
            return "busctl"
        return "unavailable"

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
            name = line.split()[0] if line.split() else ""
            if name.startswith(MPRIS_PREFIX):
                names.append(name)
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

    def _read_playerctl(self) -> Optional[NowPlaying]:
        ok, status = _run(["playerctl", "status"])
        if not ok or not status:
            return None

        # One call rather than one per field: playerctl starts a process each
        # time, and this is polled.
        ok, out = _run([
            "playerctl", "metadata", "--format",
            "{{title}}\x1f{{artist}}\x1f{{album}}\x1f{{mpris:artUrl}}"
            "\x1f{{mpris:length}}\x1f{{position}}\x1f{{playerName}}"
        ])
        if not ok:
            return None

        parts = (out.split("\x1f") + [""] * 7)[:7]
        title, artist, album, art, length, position, player = parts
        return NowPlaying(
            title    = title,
            artist   = artist,
            album    = album,
            art_url  = self._local_art(art),
            state    = STATE_MAP.get(status.strip().lower(), STOPPED),
            # MPRIS is microseconds.
            duration = self._number(length) / 1_000_000.0,
            position = self._number(position) / 1_000_000.0,
            source   = f"system:{player or 'mpris'}",
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
