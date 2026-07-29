from __future__ import annotations

import time

from src.plugin.template import Plugin

from .schedule import Schedule, now_minutes, NIGHT, DIMMING, DAY
from .night_page import NightPage


class NighttimeClockPlugin(Plugin):
    """
    A clock for a dark room, and the brightness to go with it.

    Three things, which are easier to reason about kept apart:

    * the **schedule** says what time it is in the day/night cycle. Pure
      arithmetic, in schedule.py, tested on its own.
    * the **brightness** follows the schedule, unless somebody has just
      touched the panel - then it stays half-up until they stop.
    * the **page** switches at the boundaries, and back again once the room
      settles.
    """

    KEY = "nighttimeclock"

    #how often the schedule is consulted. Once a second is far more than a
    #clock needs; this runs off on_update, which fires ~20 times a second.
    CHECK_SECONDS = 1.0

    #how long the fade takes when the panel does it by itself
    FADE_MS = 1400
    #and when somebody has just touched it, so it feels like a response
    WAKE_MS = 350

    def __init__(self):
        self.schedule = None
        self._last_minute = None
        self._last_check = 0.0
        self._woken_at = 0.0
        self._awake = False          # touched during the night
        self._settle_key = "nighttimeclock:settle"
        self._was_enabled = True

    ## CORE

    def load(self, carryover=None):
        self.client.add_page(NightPage.KEY, "Night Clock", NightPage,
                             owner=self.KEY)
        self._reload_schedule()
        self._was_enabled = self._enabled()

        self.client.subscribe_to_event("on_update", self._tick)
        self.client.subscribe_to_event("on_interaction", self._on_interaction)
        self.client.subscribe_to_event("on_interaction_timeout", self._on_idle)
        self.client.subscribe_to_event("on_settings_saved", self._on_settings)

        self.client.public.expose(self.KEY, "nighttime", {
            "schedule":   lambda: self.schedule,
            "is_night":   lambda: self.schedule.is_night(now_minutes()),
            "phase":      lambda: self.schedule.phase(now_minutes()),
            "describe":   lambda: self.schedule.describe(now_minutes()),
            "go_night":   self.enter_night,
            "go_day":     self.enter_day,
        })

        self.client.API_REGISTRY.register(
            self.KEY, "nighttime", self.api_state, requires_auth=True,
            description="Whether it is night, and what the brightness is.")

        # A way to reach the clock deliberately, rather than only by waiting
        # for nine o'clock. Doubles as the way back off it.
        self.client.QUICK.register(
            self.KEY, "night_clock", "Night clock", "mdi.weather-night",
            on_press=self.toggle_page,
            on_state=self._on_night_page,
            order=45,
            enabled=self._enabled(),
            # It navigates, so the panel goes with it rather than sitting over
            # the page it just switched to.
            closes_panel=True)

    def unload(self, carryover=None):
        for name, handler in (("on_update", self._tick),
                              ("on_interaction", self._on_interaction),
                              ("on_interaction_timeout", self._on_idle),
                              ("on_settings_saved", self._on_settings)):
            try:
                self.client.unsubscribe_from_event(name, handler)
            except Exception:
                pass
        try:
            self.client.TIMEOUTS.discard(self._settle_key)
        except Exception:
            pass
        try:
            self.client.QUICK.unregister(self.KEY)
        except Exception:
            pass
        # Left bright, and off the clock. A plugin being unloaded should not
        # leave the panel dark on a page nothing will now navigate away from.
        self._set_brightness(100, self.FADE_MS)
        if self._on_night_page():
            self._goto(self._default_home())

    ## SETTINGS

    def _setting(self, key: str, default):
        try:
            return self.client.setting(f"{self.KEY}.{key}.value", default)
        except Exception:
            return default

    def _enabled(self) -> bool:
        return bool(self._setting("enabled", True))

    def _reload_schedule(self) -> None:
        self.schedule = Schedule(
            night=self._setting("night_time", "21:00"),
            day=self._setting("day_time", "07:00"),
            lead_minutes=self._setting("dim_lead_minutes", 60),
            night_brightness=self._setting("night_brightness", 12),
            dim_enabled=self._setting("dim_enabled", True),
        )

    def _on_settings(self, event=None) -> None:
        was_enabled = self._was_enabled
        self._reload_schedule()
        enabled = self._enabled()
        self._was_enabled = enabled

        try:
            self.client.QUICK.set_enabled(self.KEY, "night_clock", enabled)
        except Exception:
            pass

        if was_enabled and not enabled:
            # Turned off. Undo everything it was doing rather than freezing
            # the panel dim on a page nothing will now navigate away from.
            self._awake = False
            try:
                self.client.TIMEOUTS.discard(self._settle_key)
            except Exception:
                pass
            self._set_brightness(100, self.FADE_MS)
            if self._on_night_page():
                self._goto(self._home_target())
            return

        # Applied at once rather than on the next boundary: somebody who just
        # changed the night time wants to see whether they got it right.
        self._last_minute = None
        self._last_check = 0.0

    ## TICK

    def _tick(self, event=None) -> None:
        if not self._enabled():
            return
        now = time.time()
        if now - self._last_check < self.CHECK_SECONDS:
            return
        self._last_check = now

        minute = now_minutes()
        previous = self._last_minute
        self._last_minute = minute

        if previous is None:
            # First look. Put the panel where it should already be, without
            # treating it as a crossing - a restart at midnight should not
            # animate up from nothing.
            self._settle_to(minute, immediate=True)
            return

        if self.schedule.crossed_into_night(previous, minute):
            self.enter_night()
            return
        if self.schedule.crossed_into_day(previous, minute):
            self.enter_day()
            return

        # Between boundaries: follow the fade, unless somebody is about.
        if not self._awake:
            target = self.schedule.brightness(minute)
            if abs(target - self._brightness()) >= 2:
                self._set_brightness(target, self.FADE_MS)

    ## TRANSITIONS

    def enter_night(self) -> None:
        """Dim, and show the clock."""
        self._awake = False
        minute = now_minutes()
        self.client.log("info", "[Nighttime] Entering night.")
        self._goto(NightPage.KEY)
        self._set_brightness(self.schedule.brightness(minute), self.FADE_MS)

    def enter_day(self) -> None:
        """Back to full brightness and whatever page was up before."""
        self._awake = False
        self.client.log("info", "[Nighttime] Entering day.")
        try:
            self.client.TIMEOUTS.discard(self._settle_key)
        except Exception:
            pass
        self._set_brightness(100, self.FADE_MS)
        if self._on_night_page():
            self._goto(self._home_target())

    def _settle_to(self, minute: int, immediate: bool = False) -> None:
        """Put the panel where the schedule says it should be, now."""
        if self.schedule.is_night(minute):
            self._goto(NightPage.KEY)
        self._set_brightness(self.schedule.brightness(minute),
                             1 if immediate else self.FADE_MS)

    ## INTERACTION

    def _on_interaction(self, event=None) -> None:
        """
        Somebody is about. Half brightness, and the home page.

        Only during the night: in the day the panel is already at full, and
        during the fade the whole point is that it is on its way down.

        It leaves the clock rather than brightening it. Somebody touching a
        wall panel at 2am wants to *use* it, and a clock is the one thing they
        can already see from across the room.
        """
        if not self._enabled() or not self.schedule.is_night(now_minutes()):
            return

        self._woken_at = time.time()
        if not self._awake:
            self._awake = True
            self._set_brightness(self._setting("woken_brightness", 55),
                                 self.WAKE_MS)
            if self._on_night_page():
                self._goto(self._home_target())

        # Re-armed on every interaction, so the countdown measures quiet
        # rather than time since the first touch.
        self._arm_settle()

    def toggle_page(self) -> None:
        """The quick access button: onto the clock, or back off it."""
        if self._on_night_page():
            self._awake = True
            self._set_brightness(
                self._setting("woken_brightness", 55)
                if self.schedule.is_night(now_minutes()) else 100,
                self.WAKE_MS)
            self._goto(self._home_target())
            if self.schedule.is_night(now_minutes()):
                self._arm_settle()
            return
        self._awake = False
        self._goto(NightPage.KEY)
        self._set_brightness(self.schedule.brightness(now_minutes()),
                             self.FADE_MS)

    def _on_idle(self, event=None) -> None:
        """
        The panel went idle. If it is still night, go back quickly.

        The idle timeout is the panel's own, and it is usually longer than
        somebody glancing at a clock warrants - so this arms a shorter one of
        its own as well, and whichever comes first wins.
        """
        if not self._enabled() or not self.schedule.is_night(now_minutes()):
            return
        self._back_to_night()

    def _arm_settle(self) -> None:
        seconds = max(2, int(self._setting("settle_seconds", 20)))
        try:
            self.client.TIMEOUTS.add(seconds, self._back_to_night,
                                     self._settle_key, transient=True)
            self.client.TIMEOUTS.start(self._settle_key)
        except Exception as e:
            self.client.log("warning", f"[Nighttime] Could not arm settle: {e}")

    def _back_to_night(self) -> None:
        if not self.schedule.is_night(now_minutes()):
            self._awake = False
            return
        self._awake = False
        self.client.log("debug", "[Nighttime] Settling back to night.")
        self._set_brightness(self.schedule.brightness(now_minutes()),
                             self.FADE_MS)
        if not self._on_night_page():
            self._goto(NightPage.KEY)

    ## HELPERS

    def _brightness(self) -> int:
        try:
            return self.client.DIMMER.brightness()
        except Exception:
            return 100

    def _set_brightness(self, percent: int, duration_ms: int) -> None:
        def apply():
            try:
                self.client.DIMMER.animate_brightness(percent, duration_ms)
            except Exception as e:
                self.client.log("warning", f"[Nighttime] Brightness: {e}")
        # on_update runs on the update thread; the dimmer is a widget.
        self.client.call_on_ui(apply)

    def _on_night_page(self) -> bool:
        page = getattr(self.client, "PAGE", None)
        return bool(page is not None and getattr(page, "name", "") == NightPage.KEY)

    def _home_target(self) -> str:
        """
        Where "off the clock" means.

        The home page, not wherever the panel happened to be when night fell.
        Waking at 2am onto the Settings page because that is where it was left
        at nine is not what anybody means by going back.
        """
        return self._default_home()

    def _default_home(self) -> str:
        for candidate in ("#cwb_home_page", "#root"):
            try:
                if self.client.has_page(candidate):
                    return candidate
            except Exception:
                continue
        return "#root"

    def _goto(self, key: str) -> None:
        if not key:
            return
        try:
            if self.client.PAGE is not None and self.client.PAGE.name == key:
                return
        except Exception:
            pass
        self.client.call_on_ui(lambda: self.client.goto(key))

    ## API

    def api_state(self, **_ignored):
        minute = now_minutes()
        return {"request": "Success",
                "phase": self.schedule.phase(minute),
                "is_night": self.schedule.is_night(minute),
                "detail": self.schedule.describe(minute),
                "brightness": self._brightness(),
                "target_brightness": self.schedule.brightness(minute),
                "woken": self._awake,
                "night_at": Schedule.clock(self.schedule.night),
                "day_at": Schedule.clock(self.schedule.day)}, 200
