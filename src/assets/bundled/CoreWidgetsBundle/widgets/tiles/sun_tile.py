"""
Where the sun is, and what the moon is doing while it is gone.

Worked out rather than fetched - the Astronomy plugin is arithmetic on a
date and a position, so this keeps working with the router off and is never
the thing that wakes the network.

Two faces, one tile. In daylight it counts down to sunset over a warm arc; at
night it draws the moon at its actual phase and counts to sunrise. Which one
is showing is the answer to "what time is it, roughly" from across a room,
before any of the words are read.
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QLinearGradient, QPainterPath, QPen

from src.styling import make_font, SIZES, set_style, add_text_shadow
from src.ui.widgets.tile import Tile

if TYPE_CHECKING:
    from src.main import Client


#Sky behind each face. Day runs warm at the horizon into blue overhead;
#night is deep violet into near-black, the same pair the weather tile uses so
#two tiles side by side agree about what time it is.
DAY_TOP,   DAY_BOTTOM   = QColor("#3f7fbf"), QColor("#e8c06a")
NIGHT_TOP, NIGHT_BOTTOM = QColor("#191033"), QColor("#3a2159")

SUN_INK   = QColor("#ffd479")
MOON_INK  = QColor("#e8ecf4")
ARC_INK   = QColor(255, 255, 255, 60)


def _local(when: Optional[datetime]) -> Optional[datetime]:
    """A UTC-aware time as local wall clock, without a timezone on it."""
    if when is None:
        return None
    if when.tzinfo is None:
        return when
    return when.astimezone().replace(tzinfo=None)


def _clock(when: Optional[datetime]) -> str:
    """
    `7:12 AM`, on every platform.

    Not `%-I`: that is a glibc extension. Windows raises on it, and this panel
    runs there - a format string is a poor place to lose a whole platform.
    """
    if when is None:
        return "—"
    return when.strftime("%I:%M %p").lstrip("0")


class SunTile(Tile):
    """Sunrise, sunset, and the moon after dark."""

    KEY  = "sun_tile"
    NAME = "Sun & moon"
    ICON = "mdi.weather-sunset"

    MIN_GRID_W, MIN_GRID_H = 1, 1
    MAX_GRID_W, MAX_GRID_H = 4, 4

    #Recomputed this often. The arithmetic is cheap; the reason not to do it
    #every tick is that nothing it says changes inside a minute.
    REFRESH_SECONDS = 60

    def __init__(self, client: "Client", grid_w: int = 2, grid_h: int = 2):
        self._rise: Optional[datetime] = None
        self._set: Optional[datetime] = None
        self._next_name = ""
        self._next_at: Optional[datetime] = None
        self._wait = ""
        self._is_day = True
        self._phase = 0.0
        self._lit = 0.0
        self._waxing = True
        self._moon_name = ""
        self._computed_at = 0.0
        self._headline: Optional[QLabel] = None
        self._detail: Optional[QLabel] = None
        super().__init__(client, grid_w=grid_w, grid_h=grid_h,
                         bg_color="#16222e")
        self._recompute()

    ## -- what it knows

    def _coordinates(self) -> tuple:
        """
        Where the panel is, from whoever owns the weather.

        Asked of the registered API rather than read out of another plugin's
        settings file - `client.setting()` walks the CLIENT's tree and never
        reaches a plugin key, so that path answers with the default forever.
        """
        try:
            api = self.client.API.get("weather")
            if api is not None:
                return api.coordinates()
        except Exception:
            pass
        return 0.0, 0.0

    def _recompute(self) -> None:
        # Through the registry, not an import. The Astronomy plugin is a
        # declared dependency, so it has loaded and exposed by now - and a
        # panel with it uninstalled gets an empty tile rather than an
        # ImportError at build time.
        if not self.client.public.has("astronomy"):
            self._rise = self._set = self._next_at = None
            self._next_name = self._wait = ""
            return
        sky = self.client.public.astronomy
        sun_times = sky["sun_times"]
        next_sun_event = sky["next_sun_event"]
        describe_wait = sky["describe_wait"]
        moon_phase = sky["moon_phase"]
        moon_illumination = sky["moon_illumination"]
        moon_waxing = sky["moon_waxing"]
        moon_name = sky["moon_name"]

        self._computed_at = time.time()
        latitude, longitude = self._coordinates()

        self._phase = moon_phase()
        self._lit = moon_illumination()
        self._waxing = moon_waxing()
        self._moon_name = moon_name()

        if not latitude and not longitude:
            # No position set, so there is no sunrise to know. Said rather
            # than shown as midnight, which is what zero would draw.
            self._rise = self._set = None
            self._next_name, self._next_at, self._wait = "", None, ""
            self._is_day = True
            return

        try:
            # Converted to LOCAL, naive time on the way in.
            #
            # `sun_times` answers in UTC with a timezone attached, and
            # comparing one of those to `datetime.now()` raises rather than
            # returning False - so the day/night test threw, the handler
            # below caught it, and the tile showed the day face at midnight
            # while claiming it had no location.
            rise, sets = sun_times(latitude, longitude)
            self._rise = _local(rise)
            self._set = _local(sets)

            name, when, seconds = next_sun_event(latitude, longitude)
            self._next_name = name or ""
            self._next_at = _local(when)
            self._wait = describe_wait(seconds) if seconds > 0 else ""

            now = datetime.now()
            self._is_day = bool(self._rise and self._set
                                and self._rise <= now < self._set)
        except Exception as e:
            self.client.log("debug", f"[SunTile] Could not work it out: {e}")
            # Everything, not just the times. Half-reset state is a tile
            # confidently showing the wrong face with a stale countdown on it.
            self._rise = self._set = self._next_at = None
            self._next_name = self._wait = ""

    def tick(self) -> None:
        if time.time() - self._computed_at < self.REFRESH_SECONDS:
            return
        self._recompute()
        self._apply()
        self.update()

    ## -- variants

    def build_variants(self) -> None:
        # Thresholds rather than exact spans, like the weather tile: 1x1 is
        # the picture and the countdown, anything larger gets the times.
        self.add_variant(1, 1, self._build_face)
        self.add_variant(2, 2, self._build_detailed)

    def _build_face(self, detailed: bool = False) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")
        column = QVBoxLayout(host)
        column.setContentsMargins(10, 8, 10, 10)
        column.setSpacing(0)
        column.addStretch()

        self._headline = QLabel("")
        self._headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._headline.setFont(make_font(SIZES.S2, bold=True))
        self._headline.setStyleSheet("color:#f0f0f4;background:transparent;")
        add_text_shadow(self._headline, blur=8)
        column.addWidget(self._headline)

        self._detail = QLabel("")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail.setWordWrap(True)
        self._detail.setFont(make_font(SIZES.S1))
        self._detail.setStyleSheet(
            "color:rgba(232,236,244,190);background:transparent;")
        add_text_shadow(self._detail, blur=6)
        self._detail.setVisible(detailed)
        column.addWidget(self._detail)

        self._apply()
        return host

    def _build_detailed(self) -> QWidget:
        return self._build_face(detailed=True)

    def _apply(self) -> None:
        if self._headline is None:
            return

        if self._rise is None:
            self._headline.setText("No location")
            if self._detail is not None:
                self._detail.setText("Set a latitude and longitude in the "
                                     "weather settings.")
            return

        # At 1x1 there is no room for words - see Tile.label_for. The
        # countdown alone is what fits, and it is the useful half.
        headline = self._wait or "—"
        if self.grid_w > 1 or self.grid_h > 1:
            headline = f"{self._next_name.title()} in {self._wait}" \
                if self._wait else self._next_name.title()
        self._headline.setText(headline)

        if self._detail is None:
            return
        rise, sets = _clock(self._rise), _clock(self._set)
        if self._is_day:
            self._detail.setText(f"Up {rise}   ·   Down {sets}")
        else:
            self._detail.setText(
                f"{self._moon_name}, {self._lit * 100:.0f}% lit   ·   "
                f"Sunrise {rise}")

    ## -- painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())

        # The gradient replaces the flat card fill, so this does not call up
        # to Tile.paintEvent - it draws the same rounded rect with a sky in
        # it, exactly as WeatherTile does, so the two agree side by side.
        sky = QLinearGradient(0, 0, 0, self.height())
        if self._is_day:
            sky.setColorAt(0.0, DAY_TOP)
            sky.setColorAt(1.0, DAY_BOTTOM)
        else:
            sky.setColorAt(0.0, NIGHT_TOP)
            sky.setColorAt(1.0, NIGHT_BOTTOM)

        body = QRectF(self.rect().adjusted(1, 1, -1, -1))
        path = QPainterPath()
        path.addRoundedRect(body, self.radius, self.radius)
        painter.fillPath(path, sky)

        painter.save()
        painter.setClipPath(path)
        if self._is_day:
            self._paint_day(painter, body)
        else:
            self._paint_night(painter, body)
        painter.restore()

        if self.dragging:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 60))

        if self.selected and not self.dragging:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#cfe4ff"), 2, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2),
                                    self.radius, self.radius)
            self._paint_handles(painter)
        painter.end()

    def _paint_day(self, painter: QPainter, rect: QRectF) -> None:
        """
        The sun on its arc, at how far through the day it actually is.

        The position IS the information: a dot near the left has just come up
        and one near the right is nearly down, which is read before any word
        on the tile.
        """
        centre_y = rect.height() * 0.72
        radius = min(rect.width() * 0.42, rect.height() * 0.5)
        arc = QRectF(rect.center().x() - radius, centre_y - radius,
                     radius * 2, radius * 2)

        painter.setPen(QPen(ARC_INK, 2, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(arc, 0, 180 * 16)

        through = self._through_day()
        if through is None:
            return
        angle = math.pi * (1.0 - through)
        x = rect.center().x() + math.cos(angle) * radius
        y = centre_y - math.sin(angle) * radius

        size = max(10.0, min(rect.width(), rect.height()) * 0.13)
        painter.setPen(Qt.PenStyle.NoPen)
        # A halo, so it reads as light rather than as a dot.
        painter.setBrush(QColor(255, 212, 121, 60))
        painter.drawEllipse(QPointF(x, y), size * 1.7, size * 1.7)
        painter.setBrush(SUN_INK)
        painter.drawEllipse(QPointF(x, y), size, size)

    def _paint_night(self, painter: QPainter, rect: QRectF) -> None:
        """
        The moon at its real phase, drawn rather than picked from a set.

        Two circles: the lit disc, then the shadow offset across it by how
        far through the cycle the moon is. That gives every phase including
        the ones between the eight names, and it is the same maths the night
        clock draws with.
        """
        size = min(rect.width(), rect.height()) * 0.30
        centre = QPointF(rect.center().x(), rect.height() * 0.42)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(232, 236, 244, 40))
        painter.drawEllipse(centre, size * 1.5, size * 1.5)

        painter.setBrush(MOON_INK)
        painter.drawEllipse(centre, size, size)

        # New moon is all shadow, full is none; between, the terminator moves
        # across. `phase` is 0..1 from new through full and back.
        shadow = QColor(NIGHT_TOP)
        shadow.setAlpha(240)
        offset = (0.5 - self._phase) * 4.0 * size
        if abs(offset) < size * 2:
            painter.setBrush(shadow)
            painter.drawEllipse(
                QPointF(centre.x() + (offset if self._waxing else -offset),
                        centre.y()), size, size)

        # A few stars, placed from the date so they do not crawl on every
        # repaint. Cheap, and an empty night sky reads as a broken tile.
        painter.setBrush(QColor(255, 255, 255, 120))
        seed = int(self._phase * 997)
        for index in range(7):
            seed = (seed * 1103515245 + 12345) % 2147483648
            x = rect.left() + (seed % 1000) / 1000.0 * rect.width()
            seed = (seed * 1103515245 + 12345) % 2147483648
            y = rect.top() + (seed % 1000) / 1000.0 * rect.height() * 0.55
            painter.drawEllipse(QPointF(x, y), 1.6, 1.6)

    def _through_day(self) -> Optional[float]:
        """How far between sunrise and sunset it is now, 0..1."""
        if not (self._rise and self._set):
            return None
        now = datetime.now().timestamp()
        start, end = self._rise.timestamp(), self._set.timestamp()
        if end <= start:
            return None
        return max(0.0, min(1.0, (now - start) / (end - start)))

