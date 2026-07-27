from __future__ import annotations
from typing import TYPE_CHECKING, Callable

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt

from src.styling import set_style

if TYPE_CHECKING:
    from src.main import Client


class TopEdgeSwipe(QWidget):
    """
    Invisible strip along the top of the screen that opens quick settings.

    It has to consume presses inside the strip. An ignored press goes to this
    widget's parent - the overlay layer - not to the page underneath, so there
    is no way to watch the gesture and still let it through. That makes the
    top few pixels a dead band, which is the same trade the old drawer handle
    made along the bottom edge, minus the visible handle.

    Kept deliberately thin, and a plain tap does nothing: only a downward drag
    past THRESHOLD opens the panel, so brushing the edge costs nothing.
    """

    HEIGHT    = 20   # dead band along the top
    THRESHOLD = 45   # downward travel before the gesture counts

    def __init__(self, client: "Client", on_open: Callable):
        super().__init__()
        self.client   = client
        self.on_open  = on_open
        self._start   = None
        self._fired   = False

        set_style(self, "common", "transparent")
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    ## -- geometry

    def sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(0, 0, parent.width(), self.HEIGHT)

    ## -- gesture

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self._start = event.globalPosition().toPoint()
        self._fired = False
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._start is None or self._fired:
            return
        delta = event.globalPosition().toPoint() - self._start
        # Fires mid-drag rather than on release: waiting for the finger to lift
        # makes a pull-down feel like it did not register.
        if delta.y() >= self.THRESHOLD and abs(delta.y()) > abs(delta.x()):
            self._fired = True
            try:
                self.on_open()
            except Exception as e:
                self.client.log("warning", f"[TopEdgeSwipe] Open failed: {e}")
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._start = None
        self._fired = False
        event.accept()
