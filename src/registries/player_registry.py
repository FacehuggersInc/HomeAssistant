"""
What is playing, and how to control it — whatever is playing it.

One contract for every source. A plugin that can play something registers as a
**backend** and publishes state; anything that wants to show or control what is
playing talks to this and never to the backend directly.

The point is that a now-playing widget, a quick settings control and a voice
skill should not each need to know whether the sound is coming from YouTube, a
local library or a network player. Backends come and go; the contract does not.
"""

from __future__ import annotations

from typing import Callable, Optional


#playback states, deliberately few
STOPPED = "stopped"
PLAYING = "playing"
PAUSED = "paused"
LOADING = "loading"

STATES = (STOPPED, PLAYING, PAUSED, LOADING)


class NowPlaying:
    """
    One snapshot of what is playing.

    A plain value object rather than a dict, so a backend that forgets a field
    gets a sensible default instead of a KeyError somewhere in a paint method.
    """

    __slots__ = ("title", "artist", "album", "art_url", "state",
                 "position", "duration", "source", "track_id", "can_seek")

    def __init__(self, title: str = "", artist: str = "", album: str = "",
                 art_url: str = "", state: str = STOPPED,
                 position: float = 0.0, duration: float = 0.0,
                 source: str = "", track_id: str = "",
                 can_seek: bool = True):
        self.title = str(title or "")
        self.artist = str(artist or "")
        self.album = str(album or "")
        self.art_url = str(art_url or "")
        self.state = state if state in STATES else STOPPED
        self.position = max(0.0, float(position or 0))
        self.duration = max(0.0, float(duration or 0))
        self.source = str(source or "")
        self.track_id = str(track_id or "")
        self.can_seek = bool(can_seek)

    @property
    def playing(self) -> bool:
        return self.state == PLAYING

    @property
    def active(self) -> bool:
        """Whether there is anything worth showing."""
        return self.state != STOPPED and bool(self.title or self.artist)

    @property
    def progress(self) -> float:
        """0.0 to 1.0, or 0.0 when the length is unknown."""
        if self.duration <= 0:
            return 0.0
        return max(0.0, min(1.0, self.position / self.duration))

    def describe(self) -> str:
        """'Everlong by Foo Fighters', or as much of it as there is."""
        if self.title and self.artist:
            return f"{self.title} by {self.artist}"
        return self.title or self.artist or ""

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__slots__} | {
            "playing": self.playing,
            "progress": self.progress,
            "describe": self.describe(),
        }

    def same_track(self, other: "NowPlaying") -> bool:
        """
        Whether two snapshots are the same track.

        Position is excluded on purpose: it changes every tick, and a widget
        that rebuilt its artwork on every tick would be unusable.
        """
        if other is None:
            return False
        return (self.track_id == other.track_id
                and self.title == other.title
                and self.source == other.source)


class Backend:
    """Something that can play. Registered by a plugin."""

    __slots__ = ("owner", "key", "label", "commands")

    def __init__(self, owner: str, key: str, label: str, commands: dict):
        self.owner = owner
        self.key = key
        self.label = label
        # {name: callable}. Missing names are simply unsupported, which is
        # normal - a radio stream has no "seek" and should not have to
        # provide one that raises.
        self.commands = dict(commands or {})

    def supports(self, command: str) -> bool:
        return callable(self.commands.get(command))

    def call(self, command: str, *args, **kwargs):
        handler = self.commands.get(command)
        if not callable(handler):
            return None
        return handler(*args, **kwargs)


class PlayerRegistry:
    """
    `client.PLAYER`.

    Backends register; one is active at a time. Commands go to the active
    backend, and anything showing the state subscribes here.
    """

    #every command a backend may offer. Anything not listed is ignored, so a
    #typo in a backend fails loudly at registration rather than silently at
    #the first press.
    COMMANDS = ("play", "pause", "toggle", "stop", "next", "previous",
                "seek", "volume", "duck", "unduck", "search")

    def __init__(self, client):
        self.client = client
        self._backends: dict = {}
        self._active: str = ""
        self._state: NowPlaying = NowPlaying()
        self._listeners: list = []
        #volume before a duck, so it can be put back exactly
        self._ducked_from: Optional[int] = None

    ## -- backends

    def register(self, owner: str, key: str, label: str,
                 commands: dict) -> Optional[Backend]:
        unknown = sorted(set(commands or {}) - set(self.COMMANDS))
        if unknown:
            self.client.log("warning", f"[Player] '{key}' offers unknown "
                                       f"commands: {', '.join(unknown)}")
        usable = {name: fn for name, fn in (commands or {}).items()
                  if name in self.COMMANDS and callable(fn)}
        if not usable:
            self.client.log("warning", f"[Player] '{key}' registered nothing "
                                       f"callable - ignored.")
            return None

        backend = Backend(owner, key, label, usable)
        self._backends[key] = backend
        if not self._active:
            self._active = key
        self.client.log("info", f"[Player] Backend '{key}' registered by "
                                f"'{owner}' ({len(usable)} commands).")
        self.changed()
        return backend

    def unregister(self, owner: str, key: str = "") -> None:
        keys = ([key] if key else
                [k for k, b in self._backends.items() if b.owner == owner])
        for name in keys:
            backend = self._backends.get(name)
            if backend is None or backend.owner != owner:
                continue
            del self._backends[name]
            if self._active == name:
                # Whatever it was playing has gone with it.
                self._active = next(iter(self._backends), "")
                self._state = NowPlaying()
            self.client.log("info", f"[Player] Backend '{name}' un-registered.")
        self.changed()

    def backends(self) -> list:
        return sorted(self._backends.values(), key=lambda b: b.label.lower())

    def active(self) -> Optional[Backend]:
        return self._backends.get(self._active)

    def set_active(self, key: str) -> bool:
        if key not in self._backends:
            return False
        if key != self._active:
            self._active = key
            self._state = NowPlaying()
            self.changed()
        return True

    ## -- state

    def state(self) -> NowPlaying:
        return self._state

    def publish(self, key: str, playing: NowPlaying) -> None:
        """
        A backend saying what it is doing.

        Identified by **backend key**, not by owner. One plugin can register
        several - a web player and a reader of whatever else the machine is
        doing - and checking the owner lets both through: each publishes on
        its own timer, the state alternates between them every second, and
        anything watching rebuilds continuously.

        Only the active backend's state is kept. The panel plays one thing at
        a time, and a publish from any other is dropped.
        """
        backend = self._backends.get(key)
        if backend is None or key != self._active:
            return
        if not isinstance(playing, NowPlaying):
            return

        previous = self._state
        playing.source = playing.source or backend.key
        self._state = playing

        # A position tick is not a change worth rebuilding artwork for.
        #
        # The duration is compared to the nearest second. A source that
        # reports it as a float recomputed from microseconds can differ in the
        # last decimal place between two reads of the same track, and an exact
        # comparison would call that a new track on every poll.
        if (previous.same_track(playing) and previous.state == playing.state
                and round(previous.duration) == round(playing.duration)):
            self.ticked()
            return
        self.changed()

    ## -- commands

    def can(self, command: str) -> bool:
        backend = self.active()
        return bool(backend and backend.supports(command))

    def command(self, name: str, *args, **kwargs):
        """Send a command to the active backend. Returns None if it cannot."""
        backend = self.active()
        if backend is None:
            return None
        if not backend.supports(name):
            self.client.log("debug", f"[Player] '{backend.key}' does not "
                                     f"support '{name}'.")
            return None
        try:
            return backend.call(name, *args, **kwargs)
        except Exception as e:
            self.client.log("warning", f"[Player] '{backend.key}' failed on "
                                       f"'{name}': {e}")
            return None

    def play(self, *args, **kwargs):     return self.command("play", *args, **kwargs)
    def pause(self):                     return self.command("pause")
    def stop(self):                      return self.command("stop")
    def next(self):                      return self.command("next")
    def previous(self):                  return self.command("previous")
    def seek(self, seconds: float):      return self.command("seek", seconds)
    def search(self, query: str):        return self.command("search", query)

    def toggle(self):
        """Pause if playing, resume if not. Falls back when unsupported."""
        if self.can("toggle"):
            return self.command("toggle")
        return self.pause() if self._state.playing else self.play()

    def volume(self, percent: int = None):
        if percent is None:
            return self.command("volume")
        return self.command("volume", max(0, min(100, int(percent))))

    ## -- ducking

    def duck(self, to_percent: int = 25) -> None:
        """
        Drop the volume for something more important.

        The level is remembered so unduck() can put back exactly what was
        there. Ducking twice does not stack - the second call would otherwise
        record the ducked level as the one to restore, and the volume would
        never come back up.
        """
        if self._ducked_from is not None:
            return
        if self.can("duck"):
            self._ducked_from = -1
            self.command("duck", to_percent)
            return
        current = self.volume()
        if current is None:
            return
        self._ducked_from = int(current)
        self.volume(to_percent)

    def unduck(self) -> None:
        if self._ducked_from is None:
            return
        level, self._ducked_from = self._ducked_from, None
        if level < 0:
            self.command("unduck")
            return
        self.volume(level)

    @property
    def ducked(self) -> bool:
        return self._ducked_from is not None

    ## -- change notification

    def subscribe(self, callback: Callable) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def unsubscribe(self, callback: Callable) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def changed(self) -> None:
        """The track, the state or the backends changed. Rebuild."""
        self._fire("changed")

    def ticked(self) -> None:
        """Only the position moved. Repaint, do not rebuild."""
        self._fire("ticked")

    def _fire(self, kind: str) -> None:
        for callback in list(self._listeners):
            try:
                callback(kind)
            except Exception as e:
                # A listener that throws is dropped rather than left to throw
                # on every future update.
                self._listeners.remove(callback)
                self.client.log("warning", f"[Player] Listener failed and was "
                                           f"removed: {e}")
