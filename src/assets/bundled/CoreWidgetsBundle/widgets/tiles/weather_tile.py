from __future__ import annotations
import time
from datetime import datetime
from threading import Thread
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import (
    QPainter, QColor, QLinearGradient, QBrush, QPainterPath)

from src.ui.widgets.tile import Tile
from src.styling import make_font, set_style, add_text_shadow, get_style_sheet, style_scrollbar

if TYPE_CHECKING:
    from src.main import Client


# Sky gradients. Day is warm at the horizon into blue overhead; night is deep
# violet into near-black. The tile reads at a glance from across a room, and
# the background does all of that work - there is no icon to confirm it.
DAY_TOP,   DAY_BOTTOM   = QColor("#3f7fbf"), QColor("#e8c06a")
NIGHT_TOP, NIGHT_BOTTOM = QColor("#191033"), QColor("#3a2159")


class _Meter(QLabel):
    """
    A value with a bar under it saying how much of its scale that is.

    A label rather than a widget with children: the grid this sits in aligns
    on baselines, and a nested layout there stops lining up with the plain
    labels beside it.
    """

    HEIGHT = 3

    def __init__(self, text: str, fill: float, tint: str):
        super().__init__(text)
        self._fill = max(0.0, min(1.0, float(fill)))
        self._tint = QColor(tint)
        self.setContentsMargins(0, 0, 0, self.HEIGHT + 3)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = self.width()
        y = self.height() - self.HEIGHT - 1

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 45))
        painter.drawRoundedRect(QRectF(0, y, width, self.HEIGHT),
                                self.HEIGHT / 2, self.HEIGHT / 2)
        if self._fill > 0:
            painter.setBrush(self._tint)
            painter.drawRoundedRect(
                QRectF(0, y, max(self.HEIGHT, width * self._fill), self.HEIGHT),
                self.HEIGHT / 2, self.HEIGHT / 2)
        painter.end()


class WeatherTile(Tile):
    """
    Current conditions, in three layouts depending on how big it is.

    A worked example of size variants: the same tile is a temperature over a
    drawn sky at one cell, gains an hourly strip at 2x3, and becomes a full
    readout at 3x3 and above.
    """

    KEY  = "weather_tile"
    NAME = "Weather"
    ICON = "mdi.weather-partly-cloudy"

    MIN_GRID_W, MIN_GRID_H = 1, 1
    MAX_GRID_W, MAX_GRID_H = 6, 6

    def __init__(self, client: "Client", grid_w: int = 2, grid_h: int = 2):
        self._is_day    = True
        self._current    = None
        self._hourly     = []
        self._fetching   = False
        self._fetched_at = 0.0
        super().__init__(client, grid_w=grid_w, grid_h=grid_h, bg_color="#16222e")

    ## -- variants

    def build_variants(self) -> None:
        # Thresholds, not exact spans. 1x1, 2x1 and 3x1 all land on the first
        # entry, which is the point - a tile should not need an entry per size.
        self.add_variant(1, 1, self._build_glance)
        self.add_variant(2, 3, self._build_hourly)
        self.add_variant(3, 3, self._build_full)

    def _build_glance(self, scale: float = 1.0) -> QWidget:
        self._glance_scale = scale
        host = QWidget()
        set_style(host, "common", "transparent")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # No glyph. The background draws the weather now - see
        # paint_condition - and an icon on top of a picture of the same thing
        # is the same fact twice, in the space the temperature wants.
        self.temp = QLabel("--")
        self.temp.setFont(make_font(max(18, int(30 * scale)), bold=True))
        self.temp.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.temp, "common", "text-strong")
        add_text_shadow(self.temp, blur=10)
        layout.addWidget(self.temp)

        self.hours_row = None
        self.detail_grid = None
        return host

    def _build_hourly(self) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self._build_glance(scale=0.8))

        self.hours_row = QHBoxLayout()
        self.hours_row.setSpacing(6)
        self.hours_row.setContentsMargins(0, 0, 0, 0)
        self.hours_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        holder = QWidget()
        set_style(holder, "common", "transparent")
        holder.setLayout(self.hours_row)
        layout.addWidget(holder)

        self.detail_grid = None
        return host

    def _build_full(self) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        layout.addWidget(self._build_hourly())

        self.detail_grid = QGridLayout()
        self.detail_grid.setHorizontalSpacing(16)
        self.detail_grid.setVerticalSpacing(3)
        self.detail_grid.setContentsMargins(0, 0, 0, 0)
        holder = QWidget()
        set_style(holder, "common", "transparent")
        holder.setLayout(self.detail_grid)

        # Scrolled rather than shrunk. The readout has a fixed number of rows
        # and the tile does not, so at 3x3 there is genuinely less room than
        # there is content - and shrinking the text to fit is what makes a
        # wall panel unreadable from across a room.
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(holder)
        style_scrollbar(area)
        layout.addWidget(area, stretch=1)

        return host

    ## -- painting

    def paintEvent(self, event) -> None:
        # The gradient replaces the flat card fill, so this does not call up
        # to Tile.paintEvent - it draws the same rounded rect with a sky in it.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # The same painter the weather-word tile uses, so two of these side by
        # side agree about what the sky is doing rather than one showing a
        # gradient and the other a storm.
        from ..weather_event import paint_condition, condition_of

        condition = condition_of(self._current or {})
        paint_condition(painter, QRectF(self.rect().adjusted(1, 1, -1, -1)),
                        condition, self._is_day, radius=self.radius,
                        seed=len(condition))

        # A scrim under the readout. The drawing is behind numbers now, and
        # white-on-cloud is the one combination that stops being readable.
        if self.grid_w > 1 or self.grid_h > 1:
            scrim = QLinearGradient(0, self.height() * 0.35, 0, self.height())
            scrim.setColorAt(0.0, QColor(0, 0, 0, 0))
            scrim.setColorAt(1.0, QColor(0, 0, 0, 110))
            veil = QPainterPath()
            veil.addRoundedRect(QRectF(self.rect().adjusted(1, 1, -1, -1)),
                                self.radius, self.radius)
            painter.fillPath(veil, QBrush(scrim))

        if self.dragging:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 60))

        if self.selected and not self.dragging:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(self._selection_pen())
            painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2),
                                    self.radius, self.radius)
            self._paint_handles(painter)

    def _selection_pen(self):
        from PyQt6.QtGui import QPen
        return QPen(QColor("#cfe4ff"), 2, Qt.PenStyle.DashLine)

    ## -- data

    REFRESH_SECONDS = 600

    def tick(self) -> None:
        """
        Paints from cache and refreshes in the background.

        Nothing here may block. tick() also runs from apply_span() on every
        variant swap, which happens mid-drag while resizing - a synchronous
        weather request there freezes the drag until it returns, which is why
        resizing appeared to need a fresh grab for each step.
        """
        self._maybe_refresh()
        self._repaint_from_cache()

    def _maybe_refresh(self) -> None:
        if getattr(self, "_fetching", False):
            return
        last = getattr(self, "_fetched_at", 0)
        if self._current is not None and (time.time() - last) < self.REFRESH_SECONDS:
            return

        api = self.client.API.get("weather")
        if api is None:
            return

        self._fetching = True

        def work():
            current  = None
            hourly   = None
            try:
                current = api.get_current_weather()
                hourly  = api.get_hourly_forecast(hours=5)
            except Exception as e:
                self.client.log("warning", f"[WeatherTile] Refresh failed: {e}")

            def apply():
                try:
                    self._fetching  = False
                    self._fetched_at = time.time()
                    if current:
                        self._current   = current
                        self._is_day    = bool(current.get("is_day", 1) > 0)
                    if hourly:
                        self._hourly = hourly
                    self._repaint_from_cache()
                except RuntimeError:
                    pass      # tile removed while the request was in flight

            self.client.call_on_ui(apply)

        Thread(target=work, name="__weather_tile", daemon=True).start()

    def _repaint_from_cache(self) -> None:
        self._apply_glance()
        if self.hours_row is not None:
            self._apply_hours()
        if self.detail_grid is not None:
            self._apply_details()
        self.update()

    def _apply_glance(self) -> None:
        if self._current is None:
            return
        try:
            self.temp.setText(f"{int(self._current['temperature_2m'])}\u00b0")
        except (KeyError, TypeError, ValueError):
            self.temp.setText("--")


    def _hour_columns(self) -> int:
        """
        How many hours actually fit.

        A column is about 44px with its spacing, and a 2-cell tile is not wide
        enough for five of them - which is the clipping. Scale with the span
        rather than picking a number and hoping.
        """
        return max(2, min(5, self.grid_w + 1))

    def _apply_hours(self) -> None:
        while self.hours_row.count():
            item = self.hours_row.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        wanted = self._hour_columns()
        # "1p" not "1pm" below three columns - the suffix is the widest part
        # of the label and the meridiem letter alone still reads.
        short = self.grid_w < 3
        rows = []
        for moment, temp in (self._hourly or [])[:wanted]:
            label = moment.strftime("%I%p").lstrip("0").lower()
            if short:
                label = label[:-1]
            rows.append((label, f"{int(temp)}\u00b0"))

        for label, value in rows:
            column = QVBoxLayout()
            column.setSpacing(0)
            column.setAlignment(Qt.AlignmentFlag.AlignCenter)

            # White with a shadow rather than the muted grey. The day
            # gradient goes to a pale gold at the bottom, which is exactly
            # where this strip sits - grey-on-gold is unreadable, and a
            # shadow is what carries it over both ends of the sky.
            hour = QLabel(label)
            hour.setFont(make_font(12, bold=True))
            hour.setMinimumWidth(0)
            hour.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hour.setStyleSheet("color: rgba(255,255,255,225); background: transparent;")
            add_text_shadow(hour, blur=9)

            temp = QLabel(value)
            temp.setFont(make_font(17, bold=True))
            temp.setAlignment(Qt.AlignmentFlag.AlignCenter)
            temp.setStyleSheet("color: #ffffff; background: transparent;")
            add_text_shadow(temp, blur=10)

            column.addWidget(hour)
            column.addWidget(temp)

            holder = QWidget()
            set_style(holder, "common", "transparent")
            holder.setLayout(column)
            self.hours_row.addWidget(holder)

    def _apply_details(self) -> None:
        while self.detail_grid.count():
            item = self.detail_grid.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        if self._current is None:
            return

        api = self.client.API.get("weather")
        # `fill` is 0..1 and draws a bar under the value, so the tile answers
        # "a lot or a little" before any number is read. None means there is
        # no scale worth drawing - inches of rain have no ceiling.
        rows = [
            ("Feels like",   self._degrees("apparent_temperature"), None,
             "#ffd479"),
            ("Humidity",     self._pct("relative_humidity_2m"),
             self._share("relative_humidity_2m", 100), "#6fd0e0"),
            ("Cloud cover",  self._pct("cloud_cover"),
             self._share("cloud_cover", 100), "#cfd6e4"),
            ("Wind",         self._wind(api),
             self._share("wind_speed_10m", 40), "#8fd6a0"),
            ("Gusts",        self._speed("wind_gusts_10m"),
             self._share("wind_gusts_10m", 60), "#8fd6a0"),
            ("Precipitation", self._inches("precipitation"), None, "#7fb8f0"),
            ("Rain",         self._inches("rain"), None, "#7fb8f0"),
            ("Showers",      self._inches("showers"), None, "#7fb8f0"),
            ("Snowfall",     self._inches("snowfall"), None, "#ffffff"),
        ]

        rows = [(name, value, fill, tint)
                for name, value, fill, tint in rows if value is not None]
        for index, (name, value, fill, tint) in enumerate(rows):
            key = QLabel(name)
            key.setFont(make_font(12))
            key.setStyleSheet("color: rgba(255,255,255,205); background: transparent;")
            add_text_shadow(key, blur=8)

            # Built first, styled after. Replacing it once the font and
            # shadow were on the QLabel left the meter with neither.
            val = _Meter(value, fill, tint) if fill is not None else QLabel(value)
            val.setFont(make_font(13, bold=True))
            val.setStyleSheet("color: #ffffff; background: transparent;")
            add_text_shadow(val, blur=8)

            if self.grid_w >= 4:
                column = index % 2
                self.detail_grid.addWidget(key, index // 2, column * 2)
                self.detail_grid.addWidget(val, index // 2, column * 2 + 1)
            else:
                # Two columns of label+value need about four cells of width.
                # Below that they collide, which is the clipping.
                val.setAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
                self.detail_grid.addWidget(key, index, 0)
                self.detail_grid.addWidget(val, index, 1)
                self.detail_grid.setColumnStretch(0, 1)

    ## -- formatting, all None-safe so one missing field is not a blank tile

    def _degrees(self, key: str):
        value = (self._current or {}).get(key)
        return None if value is None else f"{int(float(value))}\u00b0"

    def _share(self, key: str, ceiling: float):
        """
        Where this reading sits on its own scale, 0..1.

        A ceiling rather than the day's maximum: a bar that rescales itself
        looks like the weather changed when only the range did.
        """
        value = (self._current or {}).get(key)
        if value is None:
            return None
        try:
            return max(0.0, min(1.0, float(value) / float(ceiling)))
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    def _pct(self, key: str):
        value = (self._current or {}).get(key)
        return None if value is None else f"{int(value)}%"

    def _inches(self, key: str):
        value = (self._current or {}).get(key)
        if value is None or float(value) <= 0:
            # "Snowfall 0.00 in" in July is a row spending space to say nothing.
            return None
        return f"{float(value):.2f} in"

    def _speed(self, key: str):
        value = (self._current or {}).get(key)
        return None if value is None else f"{int(value)} mph"

    def _wind(self, api):
        value = (self._current or {}).get("wind_speed_10m")
        if value is None:
            return None
        text = f"{int(value)} mph"
        try:
            if api is not None:
                text += f"  (F{api.get_beaufort_scale(float(value))})"
        except Exception:
            pass
        return text
