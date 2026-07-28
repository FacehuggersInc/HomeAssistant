from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt

from src.ui.controls.buttons import IconButton
from src.styling import make_font, SIZES, set_style


class Stepper(QWidget):
    """
    A big number with up and down. Sized for a finger, not a spinbox.

    Client-level rather than living with the calendar's pickers: a stepper is
    how every bounded number on this panel is entered, and a second copy would
    drift from the first. A value chosen on one cannot be out of range, which
    is the whole reason to prefer it to a keyboard on a screen with no keys.
    """

    def __init__(self, label: str, value: int, low: int, high: int,
                 wrap: bool = True, on_change: Callable = None,
                 pad: bool = True, step: int = 1):
        super().__init__()
        self.low, self.high, self.wrap = low, high, wrap
        self.value = value
        self.pad = pad
        self.step_by = max(1, int(step))
        self.on_change = on_change

        column = QVBoxLayout(self)
        column.setSpacing(4)
        column.setAlignment(Qt.AlignmentFlag.AlignCenter)

        caption = QLabel(label)
        caption.setFont(make_font(SIZES.S1))
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(caption, "common", "text-muted")

        self.up = IconButton("mdi.chevron-up", lambda: self.step(1), size=22)
        self.down = IconButton("mdi.chevron-down", lambda: self.step(-1), size=22)

        self.display = QLabel("")
        self.display.setFont(make_font(SIZES.L1, bold=True))
        self.display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display.setFixedWidth(110)
        set_style(self.display, "common", "text-strong")

        column.addWidget(caption)
        column.addWidget(self.up, alignment=Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(self.display)
        column.addWidget(self.down, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._show()

    def step(self, by: int) -> None:
        value = self.value + (by * self.step_by)
        if value > self.high:
            value = self.low if self.wrap else self.high
        elif value < self.low:
            value = self.high if self.wrap else self.low
        self.value = value
        self._show()
        if callable(self.on_change):
            self.on_change(value)

    def set_value(self, value: int) -> None:
        self.value = max(self.low, min(self.high, int(value)))
        self._show()

    def _show(self) -> None:
        self.display.setText(f"{self.value:02d}" if self.pad else str(self.value))
