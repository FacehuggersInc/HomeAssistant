"""
Getting to a month quickly.

Paging a month at a time is fine for next week and useless for next March.
A year, then a month, is two taps to anywhere.

Shaped like `DatePickerDialog` in `pickers.py`, because it answers the same
kind of question and should not need learning twice: chevrons over a title,
an even grid under it, the same cell styling.
"""

from __future__ import annotations

import calendar as calendar_module
from datetime import date
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QGridLayout, QLabel, QPushButton, QSizePolicy,
)
from PyQt6.QtCore import Qt

from src.styling import set_style, make_font, SIZES
from src.ui.controls.buttons import IconButton
from .dialogs import _WideDialog

if TYPE_CHECKING:
    from src.main import Client


class JumpToMonthDialog(_WideDialog):
    """A year, and the twelve months in it."""

    # A shortcut, not a page. The base class is sized to the screen, which is
    # right for a month of events and wrong for twelve buttons - inheriting it
    # gave a grid of three enormous months across most of the display.
    WIDTH_RATIO  = 0.42
    HEIGHT_RATIO = 0.44
    MIN_WIDTH    = 420
    CHEVRON      = 26

    #Four across, three down. Twelve divides evenly and every cell is the same
    #size, which three columns of four rows is not.
    COLUMNS = 4
    CELL_H = 52

    #How far the double chevrons step.
    LEAP = 10

    def __init__(self, client: "Client", page):
        super().__init__(client, "Go to", "")
        self.page = page

        today = date.today()
        try:
            self.year, self.month = page.features("current_month")
        except Exception:
            self.year, self.month = today.year, today.month

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(IconButton("mdi.chevron-double-left",
                                    lambda: self._step(-self.LEAP),
                                    size=self.CHEVRON))
        header.addWidget(IconButton("mdi.chevron-left",
                                    lambda: self._step(-1),
                                    size=self.CHEVRON))
        self.title_label = QLabel("")
        self.title_label.setFont(make_font(SIZES.M1, bold=True))
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.title_label, "common", "text-strong")
        header.addWidget(self.title_label, stretch=1)
        header.addWidget(IconButton("mdi.chevron-right",
                                    lambda: self._step(1),
                                    size=self.CHEVRON))
        header.addWidget(IconButton("mdi.chevron-double-right",
                                    lambda: self._step(self.LEAP),
                                    size=self.CHEVRON))
        self.content.addLayout(header)

        self.grid = QGridLayout()
        self.grid.setSpacing(6)
        holder = QWidget()
        set_style(holder, "common", "transparent")
        holder.setLayout(self.grid)
        holder.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Expanding)
        # stretch, so the grid takes the dialog rather than sitting in a band
        # at the top of it with empty space underneath.
        self.content.addWidget(holder, stretch=1)

        # Marks named as well as coloured. A mark that is a shade of a button
        # is a mark somebody has to already know about.
        self.note = QLabel("")
        self.note.setFont(make_font(SIZES.S1))
        self.note.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        set_style(self.note, "common", "text-muted")
        self.content.addWidget(self.note)

        # NOT `self.buttons`: the base dialog keeps its own button row under
        # that name, and taking it replaces a layout with a list.
        self.month_buttons = []
        self.rebuild()

        self.add_button("Today", self._today, "secondary")
        self.add_button("Close", self.close, "secondary")

    ## -- state

    def _step(self, by: int) -> None:
        self.year += int(by)
        self.rebuild()

    def _showing(self) -> tuple:
        """The month the page has on screen, or (None, None)."""
        try:
            return self.page.features("current_month")
        except Exception:
            return None, None

    def rebuild(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.month_buttons = []

        self.title_label.setText(str(self.year))

        for column in range(self.COLUMNS):
            self.grid.setColumnStretch(column, 1)
        for row in range(3):
            self.grid.setRowStretch(row, 1)

        today = date.today()
        showing_year, showing_month = self._showing()

        for index in range(12):
            month = index + 1
            on_screen = (showing_year == self.year and showing_month == month)
            this_month = (self.year == today.year and month == today.month)

            button = QPushButton(calendar_module.month_abbr[month])
            button.setFont(make_font(SIZES.S2, bold=(on_screen or this_month)))
            button.setMinimumHeight(self.CELL_H)
            button.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Expanding)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            set_style(button, "overlays",
                      "dialog-button-primary" if on_screen
                      else "dialog-button-secondary")
            button.clicked.connect(lambda _=False, m=month: self._chose(m))
            self.grid.addWidget(button, index // self.COLUMNS,
                                index % self.COLUMNS)
            self.month_buttons.append(button)

        parts = []
        if self.year == today.year:
            parts.append(
                f"{calendar_module.month_abbr[today.month]} is this month")
        if showing_year == self.year and showing_month:
            parts.append(
                f"{calendar_module.month_abbr[showing_month]} is on screen")
        self.note.setText("   ·   ".join(parts))

    ## -- acting

    def _chose(self, month: int) -> None:
        self.close()
        try:
            self.page.show_month(self.year, month)
        except Exception as e:
            self.client.log("warning", f"[Calendar] Could not go there: {e}")

    def _today(self) -> None:
        self.close()
        try:
            self.page.go_today()
        except Exception as e:
            self.client.log("warning", f"[Calendar] Could not go home: {e}")
