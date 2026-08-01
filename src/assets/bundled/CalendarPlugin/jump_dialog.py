"""
Getting to a month quickly.

Paging a month at a time is fine for next week and useless for next March.
A year, then a month, is two taps to anywhere.
"""

from __future__ import annotations

import calendar as calendar_module
from datetime import date
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QLabel
from PyQt6.QtCore import Qt

from src.styling import set_style, make_font, SIZES
from src.ui.controls.stepper import Stepper
from src.ui.controls.buttons import ActionButton
from .dialogs import _WideDialog

if TYPE_CHECKING:
    from src.main import Client


class JumpToMonthDialog(_WideDialog):
    """A year, and the twelve months in it."""

    #Wide enough for three readable months and no wider. This dialog is a
    #shortcut, and a shortcut that fills the screen is a page.
    WIDTH = 380

    #As far as the stepper will go either way. Far enough for a mortgage and a
    #birthday, short enough that holding the arrow does not run away.
    SPAN = 25

    #Comfortably past a finger's width, without the grid becoming the point.
    CELL_H = 50
    #Three across rather than four: twelve cells in three rows of four is a
    #wide block, and the year above it is the thing being chosen first.
    COLUMNS = 3

    def __init__(self, client: "Client", page):
        today = date.today()
        super().__init__(client, "Go to", "Pick a year, then a month.")
        self.page = page

        try:
            self.year, self.month = page.features("current_month")
        except Exception:
            self.year, self.month = today.year, today.month

        self.stepper = Stepper(
            "Year", self.year, today.year - self.SPAN, today.year + self.SPAN,
            wrap=False, on_change=self._year_changed, pad=False)
        self.content.addWidget(self.stepper,
                               alignment=Qt.AlignmentFlag.AlignHCenter)

        holder = QWidget()
        set_style(holder, "common", "transparent")
        self.grid = QGridLayout(holder)
        self.grid.setContentsMargins(0, 6, 0, 0)
        self.grid.setSpacing(6)
        self.content.addWidget(holder)

        # NOT `self.buttons`: the base dialog keeps its own button row under
        # that name, and taking it replaces a layout with a list.
        self.month_buttons = []
        for index in range(12):
            month = index + 1
            button = ActionButton("", calendar_module.month_abbr[month], None,
                                  kind="secondary", size=self.CELL_H)
            button.setFont(make_font(SIZES.S2, bold=True))
            button.clicked.connect(
                lambda _=False, m=month: self._chose(m))
            self.grid.addWidget(button, index // self.COLUMNS,
                                index % self.COLUMNS)
            self.month_buttons.append(button)

        self.note = QLabel("")
        self.note.setFont(make_font(SIZES.S1))
        self.note.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        set_style(self.note, "common", "text-muted")
        self.content.addWidget(self.note)

        self.add_button("Today", self._today, "secondary")
        self.add_button("Close", self.close, "secondary")
        self._mark()

    ## -- state

    def _year_changed(self, value: int) -> None:
        self.year = int(value)
        self._mark()

    def _mark(self) -> None:
        """
        Say which month is the one on screen, and which is this one.

        Named in a line under the grid rather than only coloured: a mark that
        is a shade of a button is a mark somebody has to already know about.
        """
        today = date.today()
        parts = []
        if self.year == today.year:
            parts.append(f"{calendar_module.month_abbr[today.month]} is this month")
        try:
            showing_year, showing_month = self.page.features("current_month")
        except Exception:
            showing_year = showing_month = None
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
