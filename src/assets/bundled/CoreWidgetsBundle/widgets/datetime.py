from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QVBoxLayout, QLabel
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics

from src.ui.widget import Widget
from src.styling import make_font, add_text_shadow, set_style

if TYPE_CHECKING:
    from src.main import Client


class DateTimeWidget(Widget):
    """Displays current time and/or date. Labels are tight to their font height."""

    def __init__(
        self,
        client:    "Client",
        show_date: bool,
        show_time: bool,
        date_size: int = 28,
        date_font: str = "poppins-light",
        time_size: int = 95,
        time_font: str = "poppins-medium",
        anchor:    str = "bottom-left:0",
        width:     int | None = None,
        height:    int | None = None,
        **kwargs,
    ):
        super().__init__(
            client = client,
            key    = "datetimewidget",
            anchor = anchor,
            width  = None,
            height = None,
            **kwargs,
        )

        self._show_time = show_time
        self._show_date = show_date
        self._time_fmt  = client.SETTINGS.home.time_format.value
        self._date_fmt  = client.SETTINGS.home.date_format.value

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if show_date:
            f = make_font(date_size, bold=False, family=date_font)
            self._date_lbl = QLabel(self)
            self._date_lbl.setFont(f)
            set_style(self._date_lbl, "widgets", "widget-label-light")
            self._date_lbl.setContentsMargins(0, 0, 0, 0)
            self._date_lbl.setFixedHeight(QFontMetrics(f).height())
            add_text_shadow(self._date_lbl, blur=8, offset_x=1, offset_y=1)
            layout.addWidget(self._date_lbl)

        if show_time:
            f = make_font(time_size, bold=False, family=time_font)
            self._time_lbl = QLabel(self)
            self._time_lbl.setFont(f)
            set_style(self._time_lbl, "widgets", "widget-label-light")
            self._time_lbl.setContentsMargins(0, 0, 0, 0)
            self._time_lbl.setFixedHeight(QFontMetrics(f).height())
            add_text_shadow(self._time_lbl, blur=8, offset_x=1, offset_y=1)
            layout.addWidget(self._time_lbl)

        self._refresh()
        self.start_tick(interval_ms=1000)

    def _refresh(self) -> None:
        now = datetime.now()
        if self._show_date:
            self._date_lbl.setText(now.strftime(self._date_fmt))
        if self._show_time:
            self._time_lbl.setText(now.strftime(self._time_fmt))

    def tick(self) -> None:
        self._refresh()