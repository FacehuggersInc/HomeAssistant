from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtCore import QTimer, QPointF
from PyQt6.QtGui import QPainter, QColor, QFontMetrics, QLinearGradient

from src.ui.page import PageFramework
from src.styling import make_font, set_style
from .environment import (
    layers_for, sky_colours, describe, condition, gusts_for,
)

if TYPE_CHECKING:
    from src.main import Client


class NightPage(PageFramework):
    """
    The clock, alone in the dark.

    Deliberately almost empty: it is on for eight hours in a dark room, and
    every extra thing on it is something glowing at somebody trying to sleep.
    The time, the date, and the temperature if it is known.
    """

    KEY = "#nighttime_clock"

    # Idle triggers are a screensaver, and this page already is one. Checked
    # by IdleRandomTriggers, so neither plugin has to know about the other.
    #
    # NOT `blocks_idle`: the idle clock must keep running here, because going
    # idle is exactly what brings the panel back to this page after somebody
    # has looked at it.
    blocks_idle_triggers = True

    #ms between firefly steps. 20fps is plenty for something this slow, and
    #this repaints the whole page.
    TICK_MS = 50

    def __init__(self, client: "Client", data=None):
        super().__init__(key=self.KEY, client=client, data=data)

        try:
            width = int(client.SETTINGS.application.window.size.value[0])
            height = int(client.SETTINGS.application.window.size.value[1])
        except Exception:
            width, height = 1280, 720
        self.setFixedSize(width, height)
        set_style(self, "common", "page-background")

        self._layers: list = []
        self._last_step = time.time()
        self._weather = ""
        self._weather_data: dict = {}
        self._sun_line = ""
        self._sky = ([6, 7, 11], [14, 17, 26])
        self._gusts = None

        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._step)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self.update)

        # A fetch started as this page opened lands a second or two later.
        # Without this the page would show nothing until the next night.
        self._weather_timer = QTimer(self)
        self._weather_timer.setInterval(20000)
        self._weather_timer.timeout.connect(self._recheck_weather)

        # Weather first: the layers are chosen from it.
        self._read_weather()
        self._build_layers()

    ## -- settings

    def _setting(self, key: str, default):
        """
        One of the plugin's settings, through what the plugin exposes.

        The page has no settings of its own, and `client.setting()` walks the
        client's tree, which a plugin key never reaches. So the plugin puts a
        reader on the public registry and the page uses that - rather than
        asking the plugin manager for an instance, which is a route around
        the registry the plugin already publishes.
        """
        try:
            return self.client.public.nighttime["setting"](key, default)
        except Exception:
            return default

    def _unit_for(self, weather: dict) -> str:
        """
        Which scale this reading is in.

        A forced environment carries its own, because those are written as
        Fahrenheit literals and must not be reinterpreted by whatever the
        panel is set to display.
        """
        pinned = (weather or {}).get("_unit")
        if pinned:
            return str(pinned)
        try:
            return self.client.API["weather"].unit()
        except Exception:
            return "fahrenheit"

    def _build_layers(self) -> None:
        """
        Rebuild the stack from whatever the weather last said.

        Cheap enough to do on every activation and every resize: it allocates
        the particles once and nothing after that allocates at all.
        """
        try:
            # With effects off it still gets fireflies, because those are
            # the setting above and were here first.
            weather = (self._weather_data
                       if bool(self._setting("scene.weather_effects", True)) else {})
            # The reading is in whatever unit the weather setting asked for,
            # and every threshold in there is Fahrenheit.
            unit = self._unit_for(weather)

            self._layers = layers_for(
                weather,
                unit=unit,
                fireflies=bool(self._setting("scene.fireflies", True)),
                firefly_count=int(self._setting("scene.firefly_count", 16)),
                events=bool(self._setting("scene.sky_events", True)),
                moon=bool(self._setting("scene.show_moon", True)),
                when=self._moon_when(weather),
            )
            # One gust source for all of them, so they lean together rather
            # than each drifting to its own rhythm.
            self._gusts = gusts_for(weather)
            for layer in self._layers:
                layer.gusts = self._gusts
        except Exception as e:
            self.client.log("warning", f"[Nighttime] Could not build the "
                                       f"environment: {e}")
            self._layers = []
        for layer in self._layers:
            layer.resize(self.width(), self.height())
        self._sky = sky_colours(self._weather_data,
                                unit=self._unit_for(self._weather_data))

    ## -- weather

    @staticmethod
    def _moon_when(weather: dict):
        """
        The date to work the moon out from.

        Normally now. A debug switch can pass `_moon_days`, which shifts it -
        nothing in the weather can move the moon, and waiting a fortnight to
        see whether the gibbous draws correctly is not a workflow.
        """
        try:
            days = float((weather or {}).get("_moon_days") or 0)
        except (TypeError, ValueError):
            return None
        if not days:
            return None
        from datetime import datetime, timedelta
        # Counted from a known new moon, so the offset means what it says.
        sky = self.client.public.astronomy["module"]
        KNOWN_NEW_MOON, SYNODIC = sky.KNOWN_NEW_MOON, sky.SYNODIC
        base = datetime(2000, 1, 6, 18, 14)
        cycles = int((datetime.now() - base).days / SYNODIC)
        return base + timedelta(days=cycles * SYNODIC + days)

    def _read_weather(self) -> None:
        """
        The reading the plugin holds.

        Asked of the plugin rather than of the weather widget: the widget
        lives on sub.home, and goto() destroys that page on the way here - so
        by the time this page existed there was nothing to read, which is why
        the temperature kept not appearing.
        """
        self._weather_data = {}
        self._weather = ""
        try:
            if self.client.public.has("nighttime"):
                self._weather_data = dict(
                    self.client.public.nighttime["weather"]() or {})
        except Exception:
            self._weather_data = {}
        if self._weather_data:
            try:
                temp = int(float(self._weather_data.get("temperature_2m", 0)))
            except (TypeError, ValueError):
                self._weather = ""
                return
            symbol = ""
            try:
                symbol = self.client.API["weather"].unit_symbol()
            except Exception:
                pass
            # The condition alongside the number: "38F" alone does not say
            # whether to expect ice on the way out.
            state = condition(self._weather_data)
            self._weather = f"{temp}\u00b0{symbol}"
            if state and state != "unknown":
                self._weather += f"  \u00b7  {state}"
        self._sun_line = self._read_sun()

    def _read_sun(self) -> str:
        """
        "Sunrise in 2h 14m". The most useful thing on a clock at 5am.

        Computed, not fetched: it is arithmetic on a date and a position, and
        this keeps working with the router off.
        """
        if not bool(self._setting("scene.show_sun", True)):
            return ""
        try:
            sky = self.client.public.astronomy
            next_sun_event = sky["next_sun_event"]
            describe_wait = sky["describe_wait"]
            # Asked of the weather API, not read out of another plugin's
            # settings file. `client.setting()` walks the client's own tree
            # and never reaches a plugin key, so those paths always answered
            # with the default - which here meant no sun time, ever.
            weather = self.client.API.get("weather")
            if weather is None:
                return ""
            latitude, longitude = weather.coordinates()
            if not latitude and not longitude:
                return ""
            name, moment, seconds = next_sun_event(latitude, longitude)
            if not name or seconds <= 0:
                return ""
            return f"{name.capitalize()} in {describe_wait(seconds)}"
        except Exception:
            return ""

    def conditions(self) -> str:
        """Everything the sky is doing, for logs."""
        return describe(self._weather_data)

    def condition(self) -> str:
        """The single strongest thing it is doing."""
        return condition(self._weather_data)

    ## -- lifecycle

    def start(self) -> None:
        super().start()
        self._last_step = time.time()
        self._read_weather()
        self._build_layers()
        if self._layers:
            self._timer.start()
        self._clock_timer.start()
        self._weather_timer.start()
        if self._weather_data:
            self.client.log("debug", f"[Nighttime] Environment: "
                                     f"{self.conditions() or 'unknown'}")

    def stop(self) -> None:
        # Stopped, not left running. This page is destroyed on navigation, but
        # a timer still ticking between goto() and deletion repaints a page
        # that is on its way out.
        self._timer.stop()
        self._clock_timer.stop()
        self._weather_timer.stop()
        try:
            super().stop()
        except AttributeError:
            pass

    def _recheck_weather(self) -> None:
        """Pick up a reading that arrived after this page was built."""
        before = self._weather_data.get("temperature_2m")
        forced = self._weather_data.get("_forced")
        self._read_weather()
        if self._weather_data.get("temperature_2m") == before and not forced:
            return
        # Only rebuilt when it actually changed: this reallocates every
        # particle, and doing that every twenty seconds would be visible.
        self._build_layers()

    def _step(self) -> None:
        now = time.time()
        dt = min(0.25, now - self._last_step)
        self._last_step = now
        if self._gusts is not None:
            self._gusts.step(dt)
        width, height = self.width(), self.height()
        for layer in self._layers:
            layer.step(dt, width, height)
        self.update()

    ## -- painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        self._paint_background(painter)
        for layer in self._layers:
            layer.paint(painter, self.width(), self.height())
        self._paint_clock(painter)

        painter.end()

    def _paint_background(self, painter: QPainter) -> None:
        """
        A near-black gradient, lighter at the bottom.

        Tinted by temperature and lifted a little by cloud - a cloudy sky is
        never as black as a clear one, and saying so costs one subtraction.
        """
        top, bottom = self._sky
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor(*top))
        gradient.setColorAt(0.65, QColor((top[0] + bottom[0]) // 2,
                                         (top[1] + bottom[1]) // 2,
                                         (top[2] + bottom[2]) // 2))
        gradient.setColorAt(1.0, QColor(*bottom))
        painter.fillRect(self.rect(), gradient)

    def _paint_clock(self, painter: QPainter) -> None:
        now = datetime.now()
        try:
            fmt = str(self.client.setting("home.clock.time_format.value", "%I:%M %p"))
        except Exception:
            fmt = "%I:%M %p"
        face = now.strftime(fmt)
        if len(face) > 1 and face[0] == "0" and face[1].isdigit():
            face = face[1:]
        date_text = f"{now.strftime('%A')}, {now.strftime('%B')} {now.day}"

        # Sized to the window rather than fixed: this is the only thing on the
        # page, and it should be readable from across a dark room.
        time_font = make_font(max(48, int(self.height() * 0.20)), bold=False,
                              family="poppins-light")
        date_font = make_font(max(16, int(self.height() * 0.035)), bold=False,
                              family="poppins-light")

        time_m = QFontMetrics(time_font)
        date_m = QFontMetrics(date_font)
        time_ink = time_m.tightBoundingRect(face)
        date_ink = date_m.tightBoundingRect(date_text)

        gap = max(10, int(self.height() * 0.02))
        block = (-time_ink.top()) + gap + (-date_ink.top())
        if self._weather:
            block += gap + (-date_ink.top())
        if self._sun_line:
            block += gap * 0.72 + (-date_ink.top())

        top = (self.height() - block) / 2.0
        centre = self.width() / 2.0

        baseline = top - time_ink.top()
        painter.setFont(time_font)
        painter.setPen(QColor(226, 232, 245, 232))
        painter.drawText(
            QPointF(centre - time_m.horizontalAdvance(face) / 2.0, baseline),
            face)

        baseline += gap + (-date_ink.top())
        painter.setFont(date_font)
        painter.setPen(QColor(150, 162, 190, 190))
        painter.drawText(
            QPointF(centre - date_m.horizontalAdvance(date_text) / 2.0,
                    baseline),
            date_text)

        if self._weather:
            baseline += gap + (-date_ink.top())
            painter.setPen(QColor(122, 148, 190, 175))
            painter.drawText(
                QPointF(centre - date_m.horizontalAdvance(self._weather) / 2.0,
                        baseline),
                self._weather)

        if self._sun_line:
            baseline += gap * 0.72 + (-date_ink.top())
            painter.setPen(QColor(104, 126, 164, 150))
            painter.drawText(
                QPointF(centre - date_m.horizontalAdvance(self._sun_line) / 2.0,
                        baseline),
                self._sun_line)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        for layer in self._layers:
            layer.resize(self.width(), self.height())
