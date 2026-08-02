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

        # The weather, owned here rather than read off the page.
        #
        # The page used to reach into the weather widget for it - but the
        # widget lives on sub.home, and goto() DESTROYS that page on the way
        # to this one. So by the time the night page looked, the widget was
        # either gone or a deleted QWidget, and the temperature simply never
        # appeared. A plugin outlives every page, so it is the right place to
        # keep this.
        self.weather: dict = {}
        self._weather_at = 0.0
        self._weather_busy = False
        self._forced_weather: dict = {}
        self._forced_key = ""
        # Where to go back to. Updated on every page visit worth returning to -
        # see _remember_page.
        self._last_page = ""
        # Where to go back to. Updated on every page change that is worth
        # returning to - see _remember_page.
        self._last_page = ""

    def setting_value(self, key: str, default=None):
        """One of this plugin's own settings, for anything it exposes to."""
        try:
            return getattr(self.settings, key).value
        except Exception:
            return default

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
        self.client.subscribe_to_event("on_visit", self._remember_page)
        # Whatever is up right now counts as the last page.
        self._remember_page()

        self.client.public.expose(self.KEY, "nighttime", {
            # The page reads these rather than reaching for the plugin
            # instance. `client.setting()` walks the client's own tree, which
            # a plugin key never reaches, and going through the manager to
            # find yourself is a route around the registry that exists for
            # exactly this.
            "setting":    self.setting_value,
            "weather":    lambda: dict(self._forced_weather or self.weather),
            "condition":  self.condition,
            "refresh":    self.refresh_weather,
            "schedule":   lambda: self.schedule,
            "is_night":   lambda: self.schedule.is_night(now_minutes()),
            "phase":      lambda: self.schedule.phase(now_minutes()),
            "describe":   lambda: self.schedule.describe(now_minutes()),
            "go_night":   self.enter_night,
            "go_day":     self.enter_day,
        })

        self.client.API.register(
            self.KEY, "nighttime", self.api_state, requires_auth=True,
            description="Whether it is night, and what the brightness is.")

        # A way to reach the clock deliberately, rather than only by waiting
        # for nine o'clock. Doubles as the way back off it.
        # Whether this plugin does anything at all.
        #
        # Separate from the button that shows the clock: one is "take me to
        # the night page now" and the other is "leave me alone tonight". A
        # single button cannot mean both, and conflating them meant the only
        # way to stop the panel dimming at 10pm was the settings page.
        # Asked for out loud, which is when somebody is already in bed.
        try:
            self.client.SKILLS.register(self.KEY, self._skills())
        except Exception as e:
            self.client.log("warning", f"[Nighttime] No skills: {e}")

        self.client.QUICK.register(
            self.KEY, "night_enabled", "Night mode", "mdi.sleep",
            on_press=self.toggle_enabled,
            on_state=self._enabled,
            order=44)

        self.client.QUICK.register(
            self.KEY, "night_clock", "Night clock", "mdi.weather-night",
            on_press=self.toggle_page,
            on_state=self._on_night_page,
            order=45,
            enabled=self._enabled(),
            # It navigates, so the panel goes with it rather than sitting over
            # the page it just switched to.
            closes_panel=True)

        self._register_debug_entries()

    def unload(self, carryover=None):
        for name, handler in (("on_update", self._tick),
                              ("on_interaction", self._on_interaction),
                              ("on_interaction_timeout", self._on_idle),
                              ("on_settings_saved", self._on_settings),
                              ("on_visit", self._remember_page)):
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

    ## DEBUG

    #(key, label, icon, the weather to pretend it is)
    #Temperatures here are Fahrenheit, converted like any other reading -
    #see environment.to_fahrenheit.
    FAKE_WEATHER = [
        ("dbg_clear",   "Clear",   "mdi.weather-night",
         {"cloud_cover": 5}),
        ("dbg_cloudy",  "Cloudy",  "mdi.weather-cloudy",
         {"cloud_cover": 85}),
        ("dbg_rain",    "Rain",    "mdi.weather-pouring",
         {"cloud_cover": 95, "rain": 0.25, "wind_speed_10m": 14,
          "wind_direction_10m": 250}),
        ("dbg_snow",    "Snow",    "mdi.weather-snowy",
         {"cloud_cover": 90, "snowfall": 0.25, "temperature_2m": 24,
          "wind_speed_10m": 8, "wind_direction_10m": 300}),
        ("dbg_fog",     "Fog",     "mdi.weather-fog",
         {"cloud_cover": 100, "weather_code": 45}),
        ("dbg_storm",   "Storm",   "mdi.weather-lightning-rainy",
         {"cloud_cover": 100, "rain": 0.6, "weather_code": 95,
          "wind_speed_10m": 30, "wind_direction_10m": 200}),
        ("dbg_hail",    "Hail",    "mdi.weather-hail",
         {"cloud_cover": 100, "rain": 0.4, "weather_code": 96,
          "temperature_2m": 36, "wind_speed_10m": 18,
          "wind_direction_10m": 260}),
        ("dbg_drizzle", "Drizzle", "mdi.weather-rainy",
         {"cloud_cover": 80, "rain": 0.01, "weather_code": 53}),
        ("dbg_windy",   "Windy",   "mdi.weather-windy",
         {"cloud_cover": 30, "wind_speed_10m": 34,
          "wind_gusts_10m": 58, "wind_direction_10m": 270}),
        ("dbg_freeze",  "Freezing", "mdi.snowflake",
         {"cloud_cover": 10, "temperature_2m": 12}),
        # Moon phases shift the DATE the moon is worked out from, since
        # nothing in the weather can move it.
        ("dbg_full",    "Full moon", "mdi.moon-full",
         {"cloud_cover": 5, "_moon_days": 14.77}),
        ("dbg_crescent", "Crescent", "mdi.moon-waxing-crescent",
         {"cloud_cover": 5, "_moon_days": 3.5}),
        ("dbg_real",    "Real",    "mdi.restart",
         None),
    ]

    def _register_debug_entries(self) -> None:
        """
        Environment switches, for working on this.

        Only while `debug.enabled` is on. Waiting for it to snow to find
        out whether the snow looks right is not a workflow, and these are not
        controls a household wants in their quick settings.
        """
        if not self._debug():
            return
        for key, label, glyph, _fake in self.FAKE_WEATHER:
            self.client.QUICK.register(
                self.KEY, key, label, glyph,
                on_press=lambda k=key: self._force_weather(k),
                on_state=lambda k=key: self._forced_key == k,
                order=90, closes_panel=True)

    def _debug(self) -> bool:
        try:
            return bool(self.client.debug_mode())
        except Exception:
            return False

    def _force_weather(self, key: str) -> None:
        """Pretend it is doing something else, and show the clock."""
        entry = next((e for e in self.FAKE_WEATHER if e[0] == key), None)
        if entry is None:
            return
        fake = entry[3]
        if fake is None:
            self._forced_weather = {}
            self._forced_key = ""
            self.client.log("info", "[Nighttime] Environment back to the real weather.")
        else:
            # Marked, so the page knows to rebuild even when the temperature
            # happens to match what it already had.
            # _unit pins these to Fahrenheit. They are written as Fahrenheit
            # literals, and converting them by the panel's own unit setting
            # would turn a 12F freeze into a 54F evening on a celsius panel.
            self._forced_weather = {"_forced": key, "_unit": "fahrenheit",
                                    "temperature_2m": 60,
                                    "is_day": 0, "precipitation": 0,
                                    "rain": 0, "showers": 0, "snowfall": 0,
                                    "cloud_cover": 0, "wind_speed_10m": 0,
                                    "wind_direction_10m": 0,
                                    "wind_gusts_10m": 0, "weather_code": 0}
            self._forced_weather.update(fake)
            self._forced_key = key
            self.client.log("info", f"[Nighttime] Forced environment: {entry[1]}.")

        self._goto(NightPage.KEY)
        page = getattr(self.client, "PAGE", None)
        if page is not None and getattr(page, "name", "") == NightPage.KEY:
            self.client.call_on_ui(lambda: self._rebuild_page())

    def _rebuild_page(self) -> None:
        page = getattr(self.client, "PAGE", None)
        if page is not None and hasattr(page, "_recheck_weather"):
            try:
                page._recheck_weather()
            except Exception as e:
                self.client.log("debug", f"[Nighttime] Rebuild failed: {e}")

    ## SETTINGS

    def _setting(self, path: str, default):
        """
        Read one of this plugin's own settings, by dotted path.

        From `self.settings` — the object the loader builds from this plugin's
        settings.json. `client.setting()` walks the CLIENT's tree, which a
        plugin key never reaches, so a lookup there answers with the default
        whatever is on disk and whatever the settings page saved.

        The path includes the sub-heading the setting sits under, the same way
        the client's own paths do: `scene.fireflies`, not `fireflies`.
        """
        try:
            node = self.settings
            for part in path.split("."):
                node = getattr(node, part)
            return node.value
        except Exception:
            return default

    def _enabled(self) -> bool:
        return bool(self._setting("schedule.enabled", True))

    def _set_enabled(self, on: bool) -> bool:
        """
        Write this plugin's own setting.

        Through `self.settings`, NOT client.apply_settings(). The client's
        version takes a dotted path and calls SETTINGS.update(), which puts a
        `nighttimeclock` key in the CLIENT's settings - and the settings page
        builds a nav section per top-level key, so an empty "Nighttimeclock"
        appeared beside Application and Home while the real settings stayed in
        the plugin's own file.
        """
        try:
            self.settings.schedule.enabled.value = bool(on)
            return True
        except Exception as e:
            self.client.log("warning", f"[Nighttime] Could not set: {e}")
            return False

    def _reload_schedule(self) -> None:
        self.schedule = Schedule(
            night=self._setting("schedule.night_time", "21:00"),
            day=self._setting("schedule.day_time", "07:00"),
            lead_minutes=self._setting("brightness.dim_lead_minutes", 60),
            night_brightness=self._setting("brightness.night_brightness", 12),
            dim_enabled=self._setting("brightness.dim_enabled", True),
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

        # Debug may have been switched on or off since the last save.
        has_debug = any(self.client.QUICK.get(self.KEY, k) is not None
                        for k, *_ in self.FAKE_WEATHER)
        if self._debug() and not has_debug:
            self._register_debug_entries()
        elif not self._debug() and has_debug:
            for key, *_ in self.FAKE_WEATHER:
                self.client.QUICK.unregister(self.KEY, key)
            self._forced_weather, self._forced_key = {}, ""

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

    #how often the weather is refreshed while the plugin is loaded
    WEATHER_SECONDS = 900

    def refresh_weather(self, force: bool = False) -> None:
        """
        Keep a current reading, from whichever source has one.

        The widget first, because it is already fetching on its own timer and
        a second caller waking the network at three in the morning is exactly
        what a night page should not do. Only when that has nothing - which is
        the whole time the night page is up, since its page was destroyed -
        does this ask the API itself.
        """
        now = time.time()
        if not force and self.weather and \
                now - self._weather_at < self.WEATHER_SECONDS:
            return
        if self._weather_busy:
            return

        borrowed = self._weather_from_widget()
        if borrowed:
            self.weather = borrowed
            self._weather_at = now
            return

        api = None
        try:
            api = self.client.API.get("weather")
        except Exception:
            api = None
        if api is None:
            return

        self._weather_busy = True

        def work():
            data = None
            try:
                data = api.get_current_weather()
            except Exception as e:
                self.client.log("warning", f"[Nighttime] Weather fetch failed: {e}")
            finally:
                self._weather_busy = False
            if data:
                self.weather = dict(data)
                self._weather_at = time.time()
                self.client.log("debug", "[Nighttime] Weather refreshed.")

        from threading import Thread
        Thread(target=work, name="__nighttime_weather", daemon=True).start()

    def condition(self) -> str:
        """The single strongest thing the sky is doing, as one word."""
        from .environment import condition
        return condition(self._forced_weather or self.weather)

    def _weather_from_widget(self) -> dict:
        """Whatever the weather widget last fetched, if it is still alive."""
        try:
            widgets = self.client.public.cwb_widgets.get("sub.home", [])
        except Exception:
            return {}
        for widget in widgets:
            try:
                data = getattr(widget, "_weather_data", None)
            except RuntimeError:
                # The widget was deleted with its page. Expected, not an error.
                continue
            if data:
                return dict(data)
        return {}

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

        # Kept fresh whatever page is up, so the clock has it the moment it
        # appears rather than a quarter of an hour later.
        self.refresh_weather()

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
        """Dim, show the clock, and stop making noise."""
        self._awake = False
        minute = now_minutes()
        self.client.log("info", "[Nighttime] Entering night.")
        self._goto(NightPage.KEY)
        self._set_brightness(self.schedule.brightness(minute), self.FADE_MS)

        # Quiet as well as dark.
        #
        # A panel that dims itself and then chimes at 3am has done half a job.
        # Remembered, so day only turns it back off if night is what turned it
        # on - somebody who set it themselves before bed should still have it
        # in the morning.
        try:
            self._dnd_was_on = self.client.do_not_disturb()
            if not self._dnd_was_on:
                self.client.set_do_not_disturb(True)
        except Exception as e:
            self.client.log("debug", f"[Nighttime] Could not quieten: {e}")

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

        # Only if night is what turned it on.
        try:
            if not getattr(self, "_dnd_was_on", False):
                self.client.set_do_not_disturb(False)
            self._dnd_was_on = False
        except Exception:
            pass

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
            self._set_brightness(self._setting("brightness.woken_brightness", 55),
                                 self.WAKE_MS)
            if self._on_night_page():
                self._goto(self._home_target())

        # Re-armed on every interaction, so the countdown measures quiet
        # rather than time since the first touch.
        self._arm_settle()

    def _skills(self) -> list:
        """
        Good night, and good morning.

        Here rather than in the core bundle because the schedule is this
        plugin's: a skill that turns on something the panel might not have is
        a skill that answers "I cannot do that" for anybody who removed it.
        """
        from src.assistant.skill import Skill
        wake = self.client.wake_word()
        return [
            Skill(
                wake_word=wake, skill_key="night-good-night",
                plugin_key=self.KEY,
                examples=[
                    "good night", "goodnight", "night night",
                    "im going to bed", "i am going to bed", "bedtime",
                    "turn on night mode", "night mode",
                    "im going to sleep", "time for bed",
                ],
                func=self.skill_good_night,
            ),
            Skill(
                wake_word=wake, skill_key="night-good-morning",
                plugin_key=self.KEY,
                examples=[
                    "good morning", "im awake", "i am awake", "wake up",
                    "turn off night mode", "im up", "morning",
                ],
                func=self.skill_good_morning,
            ),
        ]

    def skill_good_night(self) -> None:
        """
        Straight to night, whatever the clock says.

        On the UI thread: a skill runs on the assistant's worker, and
        enter_night() switches the page and fades the backlight - both Qt work
        that Qt refuses from anywhere else.
        """
        def run():
            if not self._enabled():
                self._set_enabled(True)
            self.enter_night()

        self.client.call_on_ui(run)

    def skill_good_morning(self) -> None:
        """
        Out of night, without turning the schedule off.

        Waking is not the same as saying "do not do this tonight" - the
        schedule should still put the panel back to sleep at the usual time.
        """
        self.client.call_on_ui(self._wake_now)

    def _wake_now(self) -> None:
        self._awake = True
        self._set_brightness(
            self._setting("brightness.woken_brightness", 55)
            if self.schedule.is_night(now_minutes()) else 100,
            self.WAKE_MS)
        if self._on_night_page():
            self._goto(self._home_target())
        try:
            if not getattr(self, "_dnd_was_on", False):
                self.client.set_do_not_disturb(False)
            self._dnd_was_on = False
        except Exception:
            pass

    def toggle_enabled(self) -> None:
        """
        Turn the whole schedule on or off.

        Turning it OFF while the panel is dimmed and on the clock has to undo
        both, or the setting says off while the screen still says night.
        Brought back to the WOKEN brightness rather than to 100: switching this
        off is usually somebody sitting in a dark room, and full brightness is
        not a kindness there.
        """
        wanted = not self._enabled()
        if not self._set_enabled(wanted):
            return

        if wanted:
            # Back on: put the panel wherever the schedule says it should be.
            self._settle_to(now_minutes(), immediate=False)
            return

        try:
            self.client.TIMEOUTS.discard(self._settle_key)
        except Exception:
            pass
        self._awake = True
        self._set_brightness(self._setting("brightness.woken_brightness", 55),
                             self.WAKE_MS)
        if self._on_night_page():
            self._goto(self._home_target())
        # And whatever night quietened.
        try:
            if not getattr(self, "_dnd_was_on", False):
                self.client.set_do_not_disturb(False)
            self._dnd_was_on = False
        except Exception:
            pass

    def toggle_page(self) -> None:
        """The quick access button: onto the clock, or back off it."""
        if self._on_night_page():
            self._awake = True
            self._set_brightness(
                self._setting("brightness.woken_brightness", 55)
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
        seconds = max(2, int(self._setting("brightness.settle_seconds", 20)))
        try:
            # idle: this measures nothing happening, so it is held while a
            # dialog is open. Somebody answering "who is this" is doing
            # something, and going back to the night clock underneath them is
            # measuring the wrong thing.
            self.client.TIMEOUTS.add(seconds, self._back_to_night,
                                     self._settle_key, transient=True,
                                     idle=True)
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

    #Whether do not disturb was already on when night began, so day knows
    #whether it is this plugin's to turn off.
    _dnd_was_on = False

    def _on_night_page(self) -> bool:
        page = getattr(self.client, "PAGE", None)
        return bool(page is not None and getattr(page, "name", "") == NightPage.KEY)

    #never worth returning to: transient, or somewhere nobody meant to leave
    #the panel sitting
    NEVER_RETURN = ("#settings", NightPage.KEY)

    def _remember_page(self, event=None) -> None:
        """
        Track where the panel actually was.

        Settings is excluded deliberately. Somebody who changed a setting at
        nine and walked away did not choose to leave the panel on Settings,
        and waking at 2am onto a settings form is nobody's idea of useful.
        """
        page = getattr(self.client, "PAGE", None)
        name = getattr(page, "name", "") if page is not None else ""
        if name and name not in self.NEVER_RETURN:
            self._last_page = name

    def _home_target(self) -> str:
        """
        Where "off the clock" means.

        `return_to` picks: `last` goes back to whatever the panel was on
        before the night started, `home` always goes to the home page. Last is
        the default because a panel that lives on a particular page should
        come back to it - but a household that wanders through pages will want
        home, hence the setting.
        """
        mode = str(self._setting("schedule.return_to", "last")).strip().lower()
        if mode.startswith("h"):
            return self._default_home()
        return self._last_page or self._default_home()

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
                "condition": self.condition(),
                "night_at": Schedule.clock(self.schedule.night),
                "day_at": Schedule.clock(self.schedule.day)}, 200
