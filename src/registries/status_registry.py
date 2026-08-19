"""
`client.STATUS` - what the panel is busy with, as a row of icons.

A long-running job has nowhere to say so. A log line is read afterwards by
somebody already suspicious, and a dedicated panel is a screen nobody has
open at the moment it matters. What is missing is the middle: something
running right now, visible without being asked for, and gone when it stops.

So: an icon. One per job, in the Quick Settings header, and nothing else -
no label, no count, no progress. The question it answers is "is something
happening", and anything wordier is a thing to read rather than a thing to
notice.

    status = client.STATUS.start("mdi.microphone", "stt.listening", "client")
    status.set_colour(COLORS.DARK.TEXT.ACCENT)
    status.hide()          # still registered, not drawn
    status.show()
    status.stop()          # gone from the row and from the registry

`start()` is the only entry point. A caller holds the object it returns and
never reaches back into the registry by key - which is what stops two
plugins fighting over one entry, and what makes `stop()` mean something.
"""

from __future__ import annotations

import time
from typing import Callable, Optional


class Status:
    """
    One thing the panel is doing.

    Held by whatever started it. Every method is safe to call after `stop()`,
    because the thing that started a job is rarely the thing that notices it
    ended - a process that dies takes its own cleanup with it, and a handle
    that raises afterwards turns one failure into two.
    """

    __slots__ = ("key", "owner", "_icon", "_colour", "_visible", "_started",
                 "_registry", "_live")

    def __init__(self, registry, key: str, owner: str, icon: str,
                 colour: str = ""):
        self._registry = registry
        self.key = str(key)
        self.owner = str(owner)
        self._icon = str(icon or "")
        self._colour = str(colour or "")
        self._visible = True
        self._started = time.time()
        self._live = True

    ## -- what it looks like

    @property
    def icon(self) -> str:
        return self._icon

    @property
    def colour(self) -> str:
        return self._colour

    @property
    def visible(self) -> bool:
        return self._visible and self._live

    @property
    def running_for(self) -> float:
        """Seconds since it started. For anything that wants to say how long."""
        return max(0.0, time.time() - self._started)

    def set_icon(self, icon: str) -> None:
        """
        A different glyph, for a job whose state changed rather than ended.

        Speech recognition waking, then transcribing, is one job doing two
        things - two entries appearing and disappearing beside each other
        would read as two.
        """
        icon = str(icon or "")
        if icon == self._icon:
            return
        self._icon = icon
        self._changed()

    def set_colour(self, colour: str = "") -> None:
        """Empty for the ordinary muted one."""
        colour = str(colour or "")
        if colour == self._colour:
            return
        self._colour = colour
        self._changed()

    def set(self, icon: str = None, colour: str = None) -> None:
        """Both at once, without drawing the row twice."""
        changed = False
        if icon is not None and str(icon) != self._icon:
            self._icon = str(icon)
            changed = True
        if colour is not None and str(colour) != self._colour:
            self._colour = str(colour)
            changed = True
        if changed:
            self._changed()

    ## -- whether it is drawn

    def hide(self) -> None:
        """
        Stop drawing it, keep it registered.

        For a job that comes and goes on its own - the microphone between
        phrases - where starting and stopping an entry every few seconds
        would cost more than it says.
        """
        if not self._visible:
            return
        self._visible = False
        self._changed()

    def show(self) -> None:
        if self._visible:
            return
        self._visible = True
        self._changed()

    def set_visible(self, visible: bool) -> None:
        self.show() if visible else self.hide()

    ## -- ending

    def stop(self) -> None:
        """Gone from the row and from the registry. Safe to call twice."""
        if not self._live:
            return
        self._live = False
        registry, self._registry = self._registry, None
        if registry is not None:
            registry._remove(self)

    @property
    def live(self) -> bool:
        return self._live

    def _changed(self) -> None:
        if self._registry is not None:
            self._registry.changed()

    def __repr__(self) -> str:
        return (f"<Status {self.key!r} of {self.owner!r} "
                f"icon={self._icon!r} visible={self.visible}>")


class StatusRegistry:
    """
    `client.STATUS`.

    Anything starts one; the Quick Settings header draws them. The registry
    keeps the order they were started in, so a row does not reshuffle itself
    while somebody is looking at it.
    """

    def __init__(self, client):
        self.client = client
        self._entries: list = []
        self._listeners: list = []

    ## -- the one entry point

    def start(self, icon: str, key: str, owner: str,
              colour: str = "") -> Status:
        """
        Say that something is happening. Returns the handle to end it with.

        `key` names the job and `owner` names who is doing it - a plugin key,
        or `"client"` for the panel itself. Starting the same key twice
        replaces the first: a job that was already running and started again
        is one job, and two icons for it would say the work had doubled.
        """
        key, owner = str(key or ""), str(owner or "")
        if not key:
            self.client.log("warning",
                            "[Status] Refusing an entry with no key - "
                            "nothing could stop it again.")
            return Status(None, "", owner, icon, colour)

        existing = self.get(key)
        if existing is not None:
            existing.set(icon=icon, colour=colour or existing.colour)
            existing.show()
            return existing

        entry = Status(self, key, owner, icon, colour)
        self._entries.append(entry)
        self.client.log("debug",
                        f"[Status] '{key}' started by '{owner}'.")
        self.changed()
        return entry

    ## -- reading

    def get(self, key: str) -> Optional[Status]:
        return next((e for e in self._entries if e.key == str(key)), None)

    def all(self) -> list:
        """Every entry, started-first. Includes the hidden ones."""
        return list(self._entries)

    def visible(self) -> list:
        """What a row should draw."""
        return [entry for entry in self._entries if entry.visible]

    def owned_by(self, owner: str) -> list:
        return [entry for entry in self._entries if entry.owner == str(owner)]

    ## -- ending

    def stop(self, key: str) -> bool:
        """By key, for a caller that did not keep the handle."""
        entry = self.get(key)
        if entry is None:
            return False
        entry.stop()
        return True

    def stop_all(self, owner: str) -> int:
        """
        Everything one owner started.

        Called when a plugin unloads: a plugin that goes away while one of
        its jobs is showing leaves an icon nothing can ever remove.
        """
        going = self.owned_by(owner)
        for entry in going:
            entry.stop()
        return len(going)

    def _remove(self, entry: Status) -> None:
        if entry in self._entries:
            self._entries.remove(entry)
            self.client.log("debug", f"[Status] '{entry.key}' stopped.")
            self.changed()

    ## -- who is watching

    def subscribe(self, callback: Callable) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def unsubscribe(self, callback: Callable) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def changed(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception as e:
                # Dropped rather than left to throw on every future change.
                self._listeners.remove(callback)
                self.client.log("warning",
                                f"[Status] Listener failed and was removed: "
                                f"{e}")
