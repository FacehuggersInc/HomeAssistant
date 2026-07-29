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

        self._temp_lbl = QLabel("--\u00b0", self)
        self._temp_lbl.setFont(_font)
        set_style(self._temp_lbl, "widgets", "weather-temp")
        add_text_shadow(self._temp_lbl, blur=8)

        row.addWidget(self._icon_lbl)
        row.addWidget(self._temp_lbl)
        row.addStretch()
        layout.addLayout(row)

        # Initial fetch in tick so it doesn't block __init__
        self._fetching = False
        self.start_tick(interval_ms=5000)   # check every 5s; first tick fetches

    def _fetch(self) -> None:
        """
        Fetches on a worker thread and paints from cache.

        tick() runs on the UI thread, so calling the weather API straight from
        it froze the whole panel for the length of an HTTP request every time
        the interval elapsed - and for the full timeout when the panel was
        offline, which for a wall panel is not a rare case. WeatherTile
        already did it this way; this widget did not.
        """
        if self._fetching:
            return
        api = self.client.API.get("weather")
        if api is None:
            return
        self._fetching = True

        def work(stop_event=None):
            data = None
            try:
                data = api.get_current_weather()
            except Exception as e:
                self.client.log("warning", f"[WeatherWidget] fetch failed: {e}")

            def apply():
                self._fetching = False
                if not data:
                    # Retried on the next tick rather than pinned an hour out.
                    return
                try:
                    self._weather_data = data
                    self._update_display()
                    self._next_update = time.time() + self._update_interval
                except RuntimeError:
                    pass    # widget removed while the request was in flight

            self.client.call_on_ui(apply)

        from threading import Thread
        Thread(target=work, name="__cwb_weather_widget_fetch", daemon=True).start()

    def _update_display(self) -> None:
        if not self._weather_data:
            return
        temp = int(self._weather_data.get("temperature_2m", 0))
        # With the unit shown. "72°" is ambiguous on a panel somebody else
        # set up, and it is one character.
        try:
            symbol = self.client.API["weather"].unit_symbol()
        except Exception:
            symbol = ""
        self._temp_lbl.setText(f"{temp}\u00b0{symbol}")

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