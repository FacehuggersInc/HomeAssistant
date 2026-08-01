"""
Everything on this month, in one list.

The grid answers "what is on the 14th" well and "what is on at all this month"
badly - a day box holds two or three bars before it runs out of room, and a
month with anything busy in it hides the rest behind a count.
"""

from __future__ import annotations

import calendar as calendar_module
from datetime import date
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QScrollArea, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt

from src.styling import set_style, make_font, SIZES, get_style_sheet, style_scrollbar
from .dialogs import _WideDialog, EventRow

if TYPE_CHECKING:
    from src.main import Client


class MonthEventsDialog(_WideDialog):
    """Every event in a month, grouped by day."""

    WIDTH = 780

    def __init__(self, client: "Client", page, year: int, month: int):
        super().__init__(client,
                         calendar_module.month_name[month],
                         str(year))
        self.page = page
        self.year = year
        self.month = month

        self.list_host = QWidget()
        set_style(self.list_host, "common", "transparent")
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(300)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Expanding)
        scroll.setWidget(self.list_host)
        style_scrollbar(scroll)
        self.content.addWidget(scroll, stretch=1)

        self.add_button("Close", self.close, "secondary")
        self.refresh()

    def _calendar(self):
        try:
            return self.client.public.calendar
        except Exception:
            return None

    def _show_holidays(self) -> bool:
        try:
            return bool(self.page._show_holidays())
        except Exception:
            return True

    def refresh(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        api = self._calendar()
        by_day = {}
        if api is not None:
            try:
                by_day = api["in_month"](self.year, self.month,
                                         self._show_holidays())
            except Exception as e:
                self.client.log("warning",
                                f"[Calendar] Could not read the month: {e}")

        days = sorted(day for day in by_day
                      if day.year == self.year and day.month == self.month
                      and by_day[day])

        if not days:
            empty = QLabel("Nothing on this month.")
            empty.setFont(make_font(SIZES.S2))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            set_style(empty, "common", "text-muted")
            self.list_layout.addWidget(empty)
            self.list_layout.addStretch()
            return

        today = date.today()
        for day in days:
            header = QLabel(day.strftime("%A %-d"))
            header.setFont(make_font(SIZES.S2, bold=True))
            # Today is named rather than only styled: a heading that differs
            # by colour alone is a heading somebody has to already know about.
            if day == today:
                header.setText(header.text() + "  ·  today")
            set_style(header, "common",
                      "text-strong" if day == today else "text-muted")
            self.list_layout.addWidget(header)

            for entry in by_day[day]:
                self.list_layout.addWidget(
                    EventRow(self.client, entry, self._open,
                             on_remove=self.refresh, on_edit=self.refresh))

        self.list_layout.addStretch()

    def _open(self, event_key: str) -> None:
        self.close()
        try:
            self.page.open_event(event_key)
        except Exception as e:
            self.client.log("warning", f"[Calendar] Could not open: {e}")
