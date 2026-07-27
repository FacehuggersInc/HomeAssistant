from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor

if TYPE_CHECKING:
    from src.main import Client


class Dimmer(QWidget):
    """
    A black wash over the whole window, standing in for monitor brightness.

    Real backlight control needs a DDC/CI channel the display may not expose,
    root on most Linux setups, and a different API on every platform. Painting
    over the window gets the same result for a wall panel in a dark room, with
    nothing to fail at runtime.

    It lives on OVERLAYS.passthrough because that layer is
    WA_TransparentForMouseEvents - Qt skips it entirely during hit testing, so
    a full-screen widget can sit over everything without swallowing a single
    touch. On the masked overlay layer this would have blocked the whole UI.
    """

    # Never fully black. At 100% dim the screen would be unreadable and the
    # only way back would be to find a control that cannot be seen.
    MAX_DIM_ALPHA = 200

    def __init__(self, client: "Client"):
        super().__init__()
        self.client = client
        self._level = 0.0          # 0.0 = untouched, 1.0 = as dark as it goes

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    ## -- level

    def level(self) -> float:
        return self._level

    def brightness(self) -> int:
        """The level as a brightness percentage, which is how it is presented."""
        return int(round((1.0 - self._level) * 100))

    def set_brightness(self, percent: int) -> None:
        self.set_level(1.0 - (max(0, min(100, int(percent))) / 100.0))

    def set_level(self, level: float) -> None:
        level = max(0.0, min(1.0, float(level)))
        if abs(level - self._level) < 0.001:
            return
        self._level = level

        if level <= 0.0:
            # Hidden rather than transparent: the passthrough layer masks
            # itself to its visible children, so hiding this shrinks the mask
            # back off the screen instead of leaving a full-window region
            # being composited for nothing.
            self.hide()
            return

        self.sync_geometry()
        self.show()
        self.raise_()
        self.update()

    ## -- geometry

    def sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(0, 0, parent.width(), parent.height())

    ## -- painting

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if self._level <= 0.0:
            return
        painter = QPainter(self)
        painter.fillRect(
            self.rect(),
            QColor(0, 0, 0, int(self._level * self.MAX_DIM_ALPHA)),
        )
