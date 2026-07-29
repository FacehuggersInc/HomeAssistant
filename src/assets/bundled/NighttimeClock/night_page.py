from __future__ import annotations

import math
import random
import time
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF
from PyQt6.QtGui import (
    QPainter, QColor, QFontMetrics, QRadialGradient, QLinearGradient,
)

from src.ui.page import PageFramework
from src.styling import make_font, set_style

if TYPE_CHECKING:
    from src.main import Client


class Firefly:
    """
    One drifting point of light.

    Plain data with a step(), so the page can move a dozen of them without a
    QObject each - a QPropertyAnimation per dot would be a dozen timers
    running all night for something nobody is watching closely.
    """

    __slots__ = ("x", "y", "vx", "vy", "phase", "speed", "size", "hue")

    def __init__(self, width: float, height: float):
        self.x = random.uniform(0.05, 0.95) * width
        self.y = random.uniform(0.05, 0.95) * height
        angle = random.uniform(0, math.tau)
        # Slow. These are meant to be noticed only if you look for them.
        drift = random.uniform(3.0, 11.0)
        self.vx = math.cos(angle) * drift
        self.vy = math.sin(angle) * drift
        self.phase = random.uniform(0, math.tau)
        self.speed = random.uniform(0.25, 0.7)
        self.size = random.uniform(2.0, 4.2)
        self.hue = random.choice((48, 52, 60, 140))

    def step(self, dt: float, width: float, height: float) -> None:
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.phase += self.speed * dt

        # Turned back at the edges rather than wrapped: a dot vanishing on one
        # side and reappearing on the other reads as a glitch.
        margin = 20.0
        if self.x < margin and self.vx < 0:
            self.vx = -self.vx
        if self.x > width - margin and self.vx > 0:
            self.vx = -self.vx
        if self.y < margin and self.vy < 0:
            self.vy = -self.vy
        if self.y > height - margin and self.vy > 0:
            self.vy = -self.vy

    def glow(self) -> float:
        """0..1, never fully out - a dot that vanishes looks like a dead pixel."""
        return 0.25 + 0.75 * (0.5 + 0.5 * math.sin(self.phase))


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

        self._fireflies: list = []
        self._last_step = time.time()
        self._weather = ""

        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._step)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self.update)

        self._build_fireflies()
        self._read_weather()

    ## -- settings

    def _setting(self, key: str, default):
        try:
            return self.client.setting(f"nighttimeclock.{key}.value", default)
        except Exception:
            return default

    def _build_fireflies(self) -> None:
        self._fireflies = []
        if not bool(self._setting("fireflies", True)):
            return
        count = max(0, min(60, int(self._setting("firefly_count", 16))))
        for _ in range(count):
            self._fireflies.append(Firefly(self.width(), self.height()))

    ## -- weather

    def _read_weather(self) -> None:
        """
        Whatever the weather widget already fetched, if anything.

        Deliberately not its own request: the panel has a widget doing this on
        a timer already, and a second caller waking the network at three in the
        morning is exactly the sort of thing a night page should not do.
        """
        try:
            widgets = self.client.public.cwb_widgets.get("sub.home", [])
            for widget in widgets:
                data = getattr(widget, "_weather_data", None)
                if data:
                    temp = int(data.get("temperature_2m", 0))
                    self._weather = f"{temp}\u00b0"
                    return
        except Exception:
            pass
        self._weather = ""

    ## -- lifecycle

    def start(self) -> None:
        super().start()
        self._last_step = time.time()
        self._build_fireflies()
        self._read_weather()
        if self._fireflies:
            self._timer.start()
        self._clock_timer.start()

    def stop(self) -> None:
        # Stopped, not left running. This page is destroyed on navigation, but
        # a timer still ticking between goto() and deletion repaints a page
        # that is on its way out.
        self._timer.stop()
        self._clock_timer.stop()
        try:
            super().stop()
        except AttributeError:
            pass

    def _step(self) -> None:
        now = time.time()
        dt = min(0.25, now - self._last_step)
        self._last_step = now
        for fly in self._fireflies:
            fly.step(dt, self.width(), self.height())
        self.update()

    ## -- painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        self._paint_background(painter)
        for fly in self._fireflies:
            self._paint_firefly(painter, fly)
        self._paint_clock(painter)

        painter.end()

    def _paint_background(self, painter: QPainter) -> None:
        """A near-black gradient, lighter at the bottom. Barely there."""
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor(6, 7, 11))
        gradient.setColorAt(0.65, QColor(9, 11, 18))
        gradient.setColorAt(1.0, QColor(14, 17, 26))
        painter.fillRect(self.rect(), gradient)

    def _paint_firefly(self, painter: QPainter, fly: Firefly) -> None:
        glow = fly.glow()
        colour = QColor()
        colour.setHsv(fly.hue, 140, 255)

        radius = fly.size * 5.5
        gradient = QRadialGradient(QPointF(fly.x, fly.y), radius)
        halo = QColor(colour)
        halo.setAlpha(int(52 * glow))
        gradient.setColorAt(0.0, halo)
        edge = QColor(colour)
        edge.setAlpha(0)
        gradient.setColorAt(1.0, edge)
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(fly.x, fly.y), radius, radius)

        core = QColor(colour)
        core.setAlpha(int(200 * glow))
        painter.setBrush(core)
        painter.drawEllipse(QPointF(fly.x, fly.y), fly.size, fly.size)

    def _paint_clock(self, painter: QPainter) -> None:
        now = datetime.now()
        try:
            fmt = str(self.client.setting("home.time_format.value", "%I:%M %p"))
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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._build_fireflies()
