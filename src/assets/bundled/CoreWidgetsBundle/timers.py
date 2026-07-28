from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.main import Client


# One colour per timer, handed out in turn rather than hashed from the key.
# A hash can collide, and two timers side by side in the same colour is the
# one case this is meant to prevent.
PALETTE = [
    "#3f7fbf",   # blue
    "#c0603f",   # terracotta
    "#4f9d6a",   # green
    "#8a5fc0",   # violet
    "#c0a03f",   # amber
    "#3fa8b0",   # teal
    "#bf4f7f",   # rose
    "#7f8f3f",   # olive
]


class Timer:
    """One countdown. Plain data - no Qt, so the service is testable."""

    def __init__(self, seconds: float, name: str = "", key: str = "",
                 colour: str = ""):
        self.key      = key or f"timer-{uuid.uuid4().hex[:8]}"
        self.name     = (name or "").strip()
        self.duration = max(1.0, float(seconds))
        self.started  = time.time()
        self.colour   = colour or PALETTE[0]
        self.finished_at: Optional[float] = None
        self.dismissed = False

    @property
    def ends_at(self) -> float:
        return self.started + self.duration

    def remaining(self, now: float = None) -> float:
        if self.finished_at is not None:
            return 0.0
        return max(0.0, self.ends_at - (now or time.time()))

    def fraction(self, now: float = None) -> float:
        """How much is left, 1.0 down to 0.0 - what the widget drains by."""
        if self.finished_at is not None:
            return 0.0
        if self.duration <= 0:
            return 0.0
        return max(0.0, min(1.0, self.remaining(now) / self.duration))

    @property
    def done(self) -> bool:
        return self.finished_at is not None

    def label(self) -> str:
        return self.name or "Timer"

    def as_dict(self) -> dict:
        return {
            "key":       self.key,
            "name":      self.name,
            "duration":  self.duration,
            "remaining": round(self.remaining(), 1),
            "colour":    self.colour,
            "done":      self.done,
        }


def describe(seconds: float) -> str:
    """'2 minutes', '1 hour 30 minutes' - what a person would say back."""
    seconds = int(round(max(0, seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if minutes:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    if secs and not hours:
        parts.append(f"{secs} second" + ("s" if secs != 1 else ""))
    return " ".join(parts) or "0 seconds"


def clock(seconds: float) -> str:
    """The countdown face: 5:00, 1:04:09."""
    seconds = int(max(0, round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class TimerService:
    """
    Owns the countdowns; widgets only render them.

    Deliberately not state on the widget. A widget lives on `sub.home` and is
    destroyed whenever the page is rebuilt - navigating to Settings and back
    would silently cancel every running timer if the countdown lived there.

    Timers do not survive a restart. One is a thing happening in the room over
    the next few minutes, and a panel that reboots has already failed to be
    the thing counting it.
    """

    #how long a finished timer stays on screen before it takes itself away
    DONE_TIMEOUT = 60

    def __init__(self, plugin):
        self.plugin  = plugin
        self.client: "Client" = plugin.client
        self.timers: dict = {}          # key -> Timer
        self._colour_index = 0
        self._subscribed = False

    ## -- lifecycle

    def start_watching(self) -> None:
        if not self._subscribed:
            self.client.subscribe_to_event("on_update", self._tick)
            self._subscribed = True

    def stop_watching(self) -> None:
        if self._subscribed:
            try:
                self.client.unsubscribe_from_event("on_update", self._tick)
            except Exception:
                pass
            self._subscribed = False
        for key in list(self.timers):
            self._remove_widget(key)
        self.timers.clear()

    ## -- the api

    def start(self, seconds: float, name: str = "", quadrant: str = "",
              center=None) -> Optional[Timer]:
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return None
        if seconds <= 0:
            return None

        timer = Timer(seconds, name=name, colour=self._next_colour())
        self.timers[timer.key] = timer
        self.client.call_on_ui(lambda: self._place_widget(timer, quadrant, center))
        return timer

    def _next_colour(self) -> str:
        """
        The next palette entry not already on screen.

        Cycling alone repeats once more timers are running than there are
        colours; this walks past the ones in use first, so two live timers
        only share a colour when every colour is taken.
        """
        in_use = {t.colour for t in self.timers.values()}
        for _ in range(len(PALETTE)):
            colour = PALETTE[self._colour_index % len(PALETTE)]
            self._colour_index += 1
            if colour not in in_use:
                return colour
        colour = PALETTE[self._colour_index % len(PALETTE)]
        self._colour_index += 1
        return colour

    def cancel(self, key: str) -> bool:
        timer = self.timers.pop(key, None)
        if timer is None:
            return False
        self.client.call_on_ui(lambda: self._remove_widget(key))
        return True

    def find(self, name: str = "", seconds: float = 0) -> list:
        """
        Timers matching a name, a duration, or both.

        Name matching is deliberately loose. It comes from a transcript, so
        "Eggs" arrives as "eggs", "egg" or occasionally "ex" - an exact compare
        would fail on exactly the input this has to survive. Tried in order of
        confidence so a real match is never beaten by a fuzzy one:

            exact -> starts-with -> contains -> close enough

        Duration matching is against what was *asked for*, not what is left: a
        five minute timer stays "the five minute timer" four minutes in.
        """
        import difflib

        candidates = [t for t in self.timers.values() if not t.done]
        if not candidates:
            return []

        if seconds:
            tolerance = max(1.0, float(seconds) * 0.02)
            candidates = [t for t in candidates
                          if abs(t.duration - float(seconds)) <= tolerance]
            if not name:
                return candidates

        wanted = (name or "").strip().lower()
        if not wanted:
            return candidates

        named = [t for t in candidates if t.name]

        exact = [t for t in named if t.name.lower() == wanted]
        if exact:
            return exact

        prefix = [t for t in named
                  if t.name.lower().startswith(wanted) or wanted.startswith(t.name.lower())]
        if prefix:
            return prefix

        contains = [t for t in named
                    if wanted in t.name.lower() or t.name.lower() in wanted]
        if contains:
            return contains

        # Only above four characters. Below that nearly everything is close to
        # everything, which is the same reason the intent matcher draws the
        # line there.
        if len(wanted) > 4:
            close = [t for t in named
                     if difflib.SequenceMatcher(
                         None, wanted, t.name.lower()).ratio() >= 0.72]
            if close:
                return close

        return []

    def cancel_matching(self, name: str = "", seconds: float = 0) -> list:
        """Cancel every timer matching, and return the ones that went."""
        matched = self.find(name=name, seconds=seconds)
        for timer in matched:
            self.cancel(timer.key)
        return matched

    def cancel_all(self) -> int:
        keys = list(self.timers)
        for key in keys:
            self.cancel(key)
        return len(keys)

    def get(self, key: str) -> Optional[Timer]:
        return self.timers.get(key)

    def running(self) -> list:
        return [t for t in self.timers.values() if not t.done]

    def all_timers(self) -> list:
        return list(self.timers.values())

    ## -- the tick

    def _tick(self, event=None) -> None:
        """
        Runs on the client tick, which is a background thread.

        Only the finishing edge does anything - the widget redraws itself off
        its own timer, so this does not touch the UI once a second for every
        timer on screen.
        """
        now = time.time()
        for timer in list(self.timers.values()):
            if timer.done or timer.remaining(now) > 0:
                continue
            timer.finished_at = now
            self._announce(timer)

    def _announce(self, timer: Timer) -> None:
        """
        Say that it finished, and start the clock on taking the widget away.

        This is the service's job rather than the widget placement API's: the
        thing that asked for a widget is what knows why, and a placement API
        that also notified would notify for stickers and notes too.
        """
        label = timer.label()
        spoken = (f"{label} is up." if timer.name
                  else f"Your {describe(timer.duration)} timer is up.")

        # Before the panel, not after: a timer going off is the panel asking
        # for attention, so the idle clock is measuring the wrong thing. This
        # also wakes it from idle, so a screensaver is dismissed rather than
        # left sitting over the answer.
        try:
            self.client.reset_interaction_timeout()
        except Exception as e:
            self.client.log("warning", f"[Timers] Could not reset idle: {e}")

        try:
            self.client.answer("mdi.timer-outline", f"{label} finished",
                               [f"{describe(timer.duration)} is up."],
                               tint="#c0603f", speak=spoken)
        except Exception as e:
            self.client.log("warning", f"[Timers] Could not announce: {e}")
            try:
                self.client.simple_notify("mdi.timer-outline", label,
                                          f"{describe(timer.duration)} is up.")
            except Exception:
                pass

        self.client.trigger_on_call_event_iteration("on_timer_finished", timer)

        # The widget stays, showing that it finished, and then goes.
        key = f"timer_done:{timer.key}"
        try:
            self.client.TIMEOUTS.add(self.DONE_TIMEOUT,
                                     lambda k=timer.key: self.cancel(k),
                                     key, transient=True)
            self.client.TIMEOUTS.start(key)
        except Exception:
            pass

    ## -- widgets

    def _sub_home(self):
        entry = self.client.PAGES.get_entry("#cwb_home_page")
        if entry is None or getattr(entry, "instance", None) is None:
            return None
        return entry.instance.sub_page_dict.get("home")

    def _place_widget(self, timer: Timer, quadrant: str, center) -> None:
        sub_home = self._sub_home()
        if sub_home is None or not sub_home.has_feature("show_transient"):
            # No home page, or not built yet. The timer still runs and still
            # announces itself - the widget is how you watch it, not what it is.
            return
        try:
            from .widgets.timer import TimerWidget
            widget = TimerWidget(self.client, timer, service=self)
            sub_home.features().show_transient(
                widget, center=center, quadrant=quadrant or "bottom-right")
        except Exception as e:
            self.client.log("warning",
                            f"[Timers] Could not place widget for {timer.key}: {e}",
                            include_traceback=True)

    def _remove_widget(self, key: str) -> None:
        sub_home = self._sub_home()
        if sub_home is None or not sub_home.has_feature("dismiss_transient"):
            return
        try:
            sub_home.features().dismiss_transient(key)
        except Exception:
            pass
