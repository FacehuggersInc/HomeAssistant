"""
What the sky is doing, as one event.

No numbers. A temperature is a thing you read; "raining" is a thing you see
from the far side of a room and act on without reading anything - and the
weather tile beside it already has the numbers.

The picture and the word are one thing: `paint_condition` draws the sky and
the weather in it, and everything here is a frame around that. Sharing the
painter rather than the widget is what lets a 1x1 tile and a home-page widget
look like the same thing at very different sizes.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QColor, QLinearGradient, QPainterPath, QPen, QBrush,
)

from src.styling import make_font, SIZES, set_style, add_text_shadow
from src.ui.widgets.tile import Tile
from src.ui.widget import Widget

if TYPE_CHECKING:
    from src.main import Client


DAY_TOP,   DAY_BOTTOM   = QColor("#3f7fbf"), QColor("#e8c06a")
NIGHT_TOP, NIGHT_BOTTOM = QColor("#191033"), QColor("#3a2159")

CLOUD_INK = QColor(236, 240, 248, 210)
RAIN_INK  = QColor(150, 200, 245, 210)
SNOW_INK  = QColor(255, 255, 255, 225)
HAIL_INK  = QColor(205, 230, 255, 235)
BOLT_INK  = QColor("#ffd479")
FOG_INK   = QColor(220, 226, 236, 90)
SUN_INK   = QColor("#ffd479")
MOON_INK  = QColor("#e8ecf4")


def condition_of(data: dict) -> str:
    """
    One word for what the sky is doing, from the current reading.

    Ordered by what somebody would say first. Snow beats rain beats cloud,
    and a thunderstorm beats all of it - if it is doing two things, the one
    worth mentioning is the worse one.
    """
    def number(key) -> float:
        try:
            return float(data.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    if number("thunderstorm") or number("lightning_potential"):
        return "storming"
    if number("snowfall") or number("snow_depth"):
        return "snowing"
    if number("showers"):
        return "showers"
    if number("rain") or number("precipitation"):
        return "raining"

    visibility = data.get("visibility")
    try:
        if visibility is not None and float(visibility) < 3000:
            return "misty"
    except (TypeError, ValueError):
        pass

    cloud = number("cloud_cover")
    if cloud > 85:
        return "overcast"
    if cloud > 45:
        return "cloudy"
    if cloud > 15:
        return "partly cloudy"
    return "clear"


def paint_condition(painter: QPainter, rect: QRectF, condition: str,
                    is_day: bool = True, radius: float = 18.0,
                    seed: int = 0) -> None:
    """
    The sky, and the weather happening in it.

    A painter rather than a widget, so a tile and a widget can be the same
    picture at different sizes without one of them owning the other.

    `seed` keeps the raindrops still. Placed at random on every repaint they
    crawl, which reads as a rendering fault rather than as rain.
    """
    sky = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    if is_day:
        sky.setColorAt(0.0, DAY_TOP)
        sky.setColorAt(1.0, DAY_BOTTOM)
    else:
        sky.setColorAt(0.0, NIGHT_TOP)
        sky.setColorAt(1.0, NIGHT_BOTTOM)

    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.fillPath(path, sky)

    painter.save()
    painter.setClipPath(path)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    unit = min(rect.width(), rect.height())
    condition = condition or "clear"
    clear = condition in ("clear", "partly cloudy")

    if clear:
        _paint_light(painter, rect, unit, is_day)

    if condition != "clear":
        _paint_cloud(painter, rect, unit,
                     heavy=condition in ("overcast", "storming", "raining",
                                         "snowing", "showers", "hailing"))

    if condition in ("raining", "showers"):
        _paint_fall(painter, rect, unit, RAIN_INK, seed, streak=True)
    elif condition == "snowing":
        _paint_fall(painter, rect, unit, SNOW_INK, seed, streak=False)
    elif condition == "hailing":
        _paint_fall(painter, rect, unit, HAIL_INK, seed, streak=False, fast=True)
    elif condition == "storming":
        _paint_fall(painter, rect, unit, RAIN_INK, seed, streak=True)
        _paint_bolt(painter, rect, unit)
    elif condition == "misty":
        _paint_fog(painter, rect, unit)

    painter.restore()


def _paint_light(painter: QPainter, rect: QRectF, unit: float,
                 is_day: bool) -> None:
    centre = QPointF(rect.left() + rect.width() * 0.68,
                     rect.top() + rect.height() * 0.30)
    size = unit * 0.14
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 212, 121, 55) if is_day
                     else QColor(232, 236, 244, 40))
    painter.drawEllipse(centre, size * 1.8, size * 1.8)
    painter.setBrush(SUN_INK if is_day else MOON_INK)
    painter.drawEllipse(centre, size, size)


def _paint_cloud(painter: QPainter, rect: QRectF, unit: float,
                 heavy: bool) -> None:
    """
    Three overlapping circles and a base. Enough to read as a cloud at 60
    pixels, which is the size that has to work.
    """
    painter.setPen(Qt.PenStyle.NoPen)
    ink = QColor(CLOUD_INK)
    if heavy:
        ink = QColor(198, 206, 220, 225)
    painter.setBrush(ink)

    base_y = rect.top() + rect.height() * 0.46
    centre_x = rect.center().x()
    size = unit * 0.17

    painter.drawEllipse(QPointF(centre_x - size * 1.1, base_y), size, size)
    painter.drawEllipse(QPointF(centre_x + size * 0.9, base_y),
                        size * 0.85, size * 0.85)
    painter.drawEllipse(QPointF(centre_x, base_y - size * 0.55),
                        size * 1.15, size * 1.15)
    painter.drawRoundedRect(
        QRectF(centre_x - size * 2.0, base_y - size * 0.1,
               size * 3.9, size * 1.1), size * 0.55, size * 0.55)


def _paint_fall(painter: QPainter, rect: QRectF, unit: float, ink: QColor,
                seed: int, streak: bool, fast: bool = False) -> None:
    """Rain, snow or hail under the cloud. Placed from the seed, so still."""
    count = max(6, int(rect.width() / (unit * 0.16)))
    top = rect.top() + rect.height() * 0.58
    value = (seed or 1) * 2654435761 % 2147483647

    painter.setPen(QPen(ink, max(1.4, unit * 0.022),
                        Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.setBrush(ink)
    for _ in range(count):
        value = (value * 1103515245 + 12345) % 2147483648
        x = rect.left() + (value % 1000) / 1000.0 * rect.width()
        value = (value * 1103515245 + 12345) % 2147483648
        y = top + (value % 1000) / 1000.0 * (rect.bottom() - top) * 0.85
        if streak:
            length = unit * (0.16 if not fast else 0.10)
            painter.drawLine(QPointF(x, y),
                             QPointF(x - length * 0.35, y + length))
        else:
            painter.drawEllipse(QPointF(x, y), unit * 0.022, unit * 0.022)


def _paint_bolt(painter: QPainter, rect: QRectF, unit: float) -> None:
    x = rect.center().x()
    y = rect.top() + rect.height() * 0.58
    size = unit * 0.20
    bolt = QPainterPath()
    bolt.moveTo(x + size * 0.15, y)
    bolt.lineTo(x - size * 0.30, y + size * 0.62)
    bolt.lineTo(x - size * 0.02, y + size * 0.62)
    bolt.lineTo(x - size * 0.22, y + size * 1.25)
    bolt.lineTo(x + size * 0.36, y + size * 0.48)
    bolt.lineTo(x + size * 0.05, y + size * 0.48)
    bolt.closeSubpath()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(BOLT_INK)
    painter.drawPath(bolt)


def _paint_fog(painter: QPainter, rect: QRectF, unit: float) -> None:
    painter.setPen(QPen(FOG_INK, max(2.0, unit * 0.05),
                        Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for index in range(4):
        y = rect.top() + rect.height() * (0.52 + index * 0.11)
        inset = rect.width() * (0.14 if index % 2 else 0.24)
        painter.drawLine(QPointF(rect.left() + inset, y),
                         QPointF(rect.right() - inset, y))


def _paint_word(painter: QPainter, rect: QRectF, condition: str,
                big: bool = False) -> None:
    """
    The word, across the bottom of the picture, with a shadow under it.

    A shadow rather than a panel behind it: the sky runs from a pale gold at
    the bottom in daylight to near-black at night, and white text needs
    carrying over both.
    """
    text = condition.title() if condition else "\u2026"
    unit = min(rect.width(), rect.height())

    font = painter.font()
    font.setBold(True)
    font.setPointSizeF(max(10.0, unit * (0.155 if big else 0.185)))
    painter.setFont(font)

    where = QRectF(rect.left(), rect.bottom() - unit * 0.34,
                   rect.width(), unit * 0.28)
    painter.setPen(QColor(0, 0, 0, 150))
    painter.drawText(where.adjusted(0, 2, 0, 2),
                     int(Qt.AlignmentFlag.AlignCenter), text)
    painter.setPen(QColor("#f0f0f4"))
    painter.drawText(where, int(Qt.AlignmentFlag.AlignCenter), text)


class _ConditionReader:
    """
    Shared between the tile and the widget: fetch, cache, and the word.

    A mixin rather than a base class, because one of them is a `Tile` and the
    other is a `Widget` and neither can be the other.
    """

    REFRESH_SECONDS = 600

    def _reader_init(self) -> None:
        # Not "clear" - that is a real answer, and showing it before anything
        # has been fetched is the tile lying about the weather while it waits.
        self._condition = ""
        self._is_day = True
        self._fetched_at = 0.0
        self._fetching = False

    def _maybe_refresh(self) -> None:
        if self._fetching or time.time() - self._fetched_at < self.REFRESH_SECONDS:
            return
        api = self.client.API.get("weather")
        if api is None:
            return
        self._fetching = True

        def work():
            data = None
            try:
                data = api.get_current_weather()
            except Exception as e:
                # Warning, not debug. A tile stuck saying "Clear" forever is
                # indistinguishable from clear weather, so the one place that
                # knows better has to say so.
                self.client.log("warning",
                                f"[WeatherEvent] Could not read it: {e}")

            def apply():
                self._fetching = False
                self._fetched_at = time.time()
                if not data:
                    return
                self._condition = condition_of(data)
                try:
                    self._is_day = bool(int(data.get("is_day", 1)))
                except (TypeError, ValueError):
                    self._is_day = True
                self._show_condition()
                self.update()

            self.client.call_on_ui(apply)

        # Named per instance. A shared name means the tile and the widget -
        # and two of either - fight over one thread slot, and whichever
        # started second never fetches anything at all.
        name = f"__weather_event_{id(self)}"
        self.client.THREADS.create(name, lambda stop: work())
        self.client.THREADS.start(name)

    def _show_condition(self) -> None:
        """
        Put the word where this one keeps it.

        The tile has a label from its variant builder; the widget paints its
        own and only needs a repaint. Neither knows about the other.
        """
        label = getattr(self, "_word", None)
        if label is not None:
            label.setText(self._condition.title() if self._condition else "…")


class WeatherEventTile(Tile):
    """The sky, and one word for what it is doing."""

    KEY  = "weather_event_tile"
    NAME = "Weather event"
    ICON = "mdi.weather-partly-rainy"

    MIN_GRID_W, MIN_GRID_H = 1, 1
    MAX_GRID_W, MAX_GRID_H = 4, 4

    def __init__(self, client: "Client", grid_w: int = 2, grid_h: int = 1):
        _ConditionReader._reader_init(self)
        self._word: Optional[QLabel] = None
        super().__init__(client, grid_w=grid_w, grid_h=grid_h,
                         bg_color="#16222e")

    _maybe_refresh = _ConditionReader._maybe_refresh
    _show_condition = _ConditionReader._show_condition
    REFRESH_SECONDS = _ConditionReader.REFRESH_SECONDS

    def build_variants(self) -> None:
        self.add_variant(1, 1, self._build)

    def _build(self) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")
        column = QVBoxLayout(host)
        column.setContentsMargins(8, 6, 8, 8)
        column.addStretch()

        self._word = QLabel(self._condition.title() if self._condition else "…")
        self._word.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._word.setFont(make_font(SIZES.S2, bold=True))
        self._word.setStyleSheet("color:#f0f0f4;background:transparent;")
        add_text_shadow(self._word, blur=8)
        column.addWidget(self._word)
        return host

    def tick(self) -> None:
        self._maybe_refresh()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        body = QRectF(self.rect().adjusted(1, 1, -1, -1))
        paint_condition(painter, body, self._condition, self._is_day,
                        radius=self.radius, seed=len(self._condition))

        if self.dragging:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 60))
        if self.selected and not self.dragging:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#cfe4ff"), 2, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2),
                                    self.radius, self.radius)
            self._paint_handles(painter)
        painter.end()


class WeatherEventWidget(Widget):
    """The same picture, on the home page."""

    KEY         = "weather_event"
    NAME        = "Weather event"
    ICON        = "mdi.weather-partly-rainy"
    DESCRIPTION = "What the sky is doing, in one word."

    RESIZABLE = True
    #Without this a widget is pinned to an anchor zone, and `DEFAULT_ANCHOR`
    #is bottom-left - so it could be picked up and never put down anywhere
    #else. Everything on the home page that is meant to be arranged says so.
    FLOATABLE = True
    ROTATABLE = False
    DEFAULT_W, DEFAULT_H = 240, 150
    MIN_W, MIN_H = 120, 90
    MAX_W, MAX_H = 720, 480

    def __init__(self, client: "Client", **kwargs):
        _ConditionReader._reader_init(self)
        self._word: Optional[QLabel] = None
        super().__init__(client, **kwargs)
        # A widget is NOT ticked unless it asks. `tick()` exists on the base
        # class and nothing calls it on its own, so the fetch never ran and
        # the word stayed on its placeholder forever.
        self.start_tick(interval_ms=5000)

    _maybe_refresh = _ConditionReader._maybe_refresh
    _show_condition = _ConditionReader._show_condition
    REFRESH_SECONDS = _ConditionReader.REFRESH_SECONDS

    def tick(self) -> None:
        self._maybe_refresh()

    def paintEvent(self, event) -> None:
        """
        Sky, then the word on top of it.

        Painted rather than laid out. `Widget` has no `build()` hook - every
        widget here draws itself - so a `build()` returning a QLabel was
        never called and the word never appeared. The tile is different: a
        `Tile` DOES take builders, through `add_variant`.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        body = QRectF(self.rect().adjusted(1, 1, -1, -1))
        paint_condition(painter, body, self._condition, self._is_day,
                        radius=16.0, seed=len(self._condition or "clear"))
        _paint_word(painter, body, self._condition, big=True)
        painter.end()
