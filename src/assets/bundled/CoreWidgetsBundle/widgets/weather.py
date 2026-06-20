from __future__ import annotations
import time
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt

from src.ui.widget import Widget
from src.styling import make_font, SIZES, add_text_shadow, set_style

if TYPE_CHECKING:
    from src.main import Client


class WeatherWidget(Widget):
    """
    Displays current temperature from Open-Meteo.
    Fetches once on startup then refreshes every hour.
    """

    def __init__(self, client: "Client"):
        super().__init__(
            client = client,
            key    = "weatherwidget",
            anchor = "top-left",
            width  = None,
            height = None,
        )

        self._update_interval = 60 * 60  # seconds
        self._next_update     = time.time() + self._update_interval
        self._weather_data    = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.setContentsMargins(0, 0, 0, 0)

        # Font metrics gives us exact height so icon matches text
        from PyQt6.QtGui import QFontMetrics
        _font = make_font(SIZES.L2, bold=False)
        _icon_size = QFontMetrics(_font).height()

        self._icon_lbl = QLabel(self)
        self._icon_lbl.setFixedSize(_icon_size, _icon_size)
        set_style(self._icon_lbl, "common", "transparent")
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        add_text_shadow(self._icon_lbl, blur=8)

        self._temp_lbl = QLabel("--°", self)
        self._temp_lbl.setFont(_font)
        set_style(self._temp_lbl, "widgets", "weather-temp")
        add_text_shadow(self._temp_lbl, blur=8)

        row.addWidget(self._icon_lbl)
        row.addWidget(self._temp_lbl)
        row.addStretch()
        layout.addLayout(row)

        # Initial fetch in tick so it doesn't block __init__
        self.start_tick(interval_ms=5000)   # check every 5s; first tick fetches

    def _fetch(self) -> None:
        try:
            data = self.client.API["weather"].get_current_weather()
            if data:
                self._weather_data = data
                self._update_display()
                self._next_update = time.time() + self._update_interval
        except Exception as e:
            self.client.log("warning", f"[WeatherWidget] fetch failed: {e}")

    def _update_display(self) -> None:
        if not self._weather_data:
            return
        temp = int(self._weather_data.get("temperature_2m", 0))
        self._temp_lbl.setText(f"{temp}°")

        try:
            import qtawesome as qta
            mdi_name = self.client.API["weather"].get_icon(self._weather_data)
            q_icon = qta.icon(mdi_name, color="white")
            sz = self._icon_lbl.width()
            self._icon_lbl.setPixmap(q_icon.pixmap(sz, sz))
        except Exception:
            pass

    def tick(self) -> None:
        if self._weather_data is None or time.time() >= self._next_update:
            self._fetch()