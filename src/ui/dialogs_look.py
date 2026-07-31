"""
Colour and text size, as things to touch.

A list of "Text 17pt / Text 22pt" rows is a menu somebody reads; a swatch and a
stepper are controls somebody uses. The difference matters on a wall panel,
where the whole interaction is a finger and a glance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel

from src.styling import make_font, set_style, SIZES
from src.ui.overlays import BaseDialog

if TYPE_CHECKING:
    from src.main import Client


class Swatch(QWidget):
    """One colour, big enough to hit, ringed when it is the current one."""

    SIZE = 52

    def __init__(self, colour: str, chosen: bool, on_pick: Callable):
        super().__init__()
        self.colour = colour
        self.chosen = chosen
        self._on_pick = on_pick
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        event.accept()
        self._on_pick(self.colour)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        inset = 4
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.colour))
        painter.drawEllipse(inset, inset,
                            self.SIZE - inset * 2, self.SIZE - inset * 2)
        if self.chosen:
            # A ring outside the swatch rather than a tick inside it: a tick
            # in ink dark enough to read changes how the colour itself looks,
            # which is the one thing this control is for judging.
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#f0f0f4"), 2))
            painter.drawEllipse(1, 1, self.SIZE - 2, self.SIZE - 2)
        painter.end()


class LookDialog(BaseDialog):
    """
    How something looks: its colour, and how big its text is.

    Applied as it changes rather than on a Done button. The widget is behind
    the dialog and visibly redraws, so somebody picking a colour is looking at
    the answer while they choose it.
    """

    WIDTH = 420

    def __init__(self, client: "Client", title: str, colours: list,
                 colour: str, size: int, sizes: tuple,
                 on_colour: Callable, on_size: Callable,
                 on_rename: Callable = None):
        super().__init__(client, title, "")
        self._on_colour = on_colour
        self._on_size = on_size
        self._colours = list(colours)
        self._colour = colour
        self._swatches: list = []

        body = QVBoxLayout()
        body.setSpacing(18)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch()
        for entry in self._colours:
            swatch = Swatch(entry, entry == colour, self._pick)
            self._swatches.append(swatch)
            row.addWidget(swatch)
        row.addStretch()
        body.addLayout(row)

        from src.ui.controls.stepper import Stepper
        self._stepper = Stepper(
            "Text size", int(size), min(sizes), max(sizes),
            wrap=False, on_change=self._resize, pad=False, step=1)
        holder = QHBoxLayout()
        holder.addStretch()
        holder.addWidget(self._stepper)
        holder.addStretch()
        body.addLayout(holder)

        host = QWidget()
        set_style(host, "common", "transparent")
        host.setLayout(body)
        self.content.addWidget(host)

        # Renaming belongs here for anything that HAS a name.
        #
        # It went missing when this dialog replaced the sheet: the sheet had a
        # Rename row and this had nowhere for one, so the only way to retitle
        # a list was to remove it and make another.
        if on_rename is not None:
            def rename() -> None:
                self.close()
                on_rename()
            self.add_button("Rename", rename, "secondary")

        self.add_button("Done", self.close, "primary")

    def _pick(self, colour: str) -> None:
        self._colour = colour
        for swatch in self._swatches:
            was = swatch.chosen
            swatch.chosen = swatch.colour == colour
            if swatch.chosen != was:
                swatch.update()
        try:
            self._on_colour(colour)
        except Exception as e:
            self.client.log("warning", f"[Look] Colour failed: {e}")

    def _resize(self, value: int) -> None:
        try:
            self._on_size(int(value))
        except Exception as e:
            self.client.log("warning", f"[Look] Size failed: {e}")
