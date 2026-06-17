from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional

import time

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QPainter, QBrush, QColor, QPen, QMouseEvent

if TYPE_CHECKING:
    from src.main import Client


class Handle(QWidget):
    """
    A pill-shaped drag handle that triggers open/close callbacks.

    NOTE: WA_TranslucentBackground is intentionally NOT set here.
    On Linux compositors it causes the widget to lose its input region,
    making it invisible to mouse events. Instead we paint a transparent
    background manually in paintEvent.
    """

    def __init__(
        self,
        client:   "Client",
        on_open:  Optional[Callable] = None,
        on_close: Optional[Callable] = None,
        position: str = "bottom",
    ):
        super().__init__()
        self.client   = client
        self.on_open  = on_open
        self.on_close = on_close
        self.position = position

        self.open                    = False
        self.min_drag                = 8
        self.min_time_to_event       = 0.3
        self.last_event_time         = 0.0
        self.use_advanced_drag_logic = False

        self._drag_start: Optional[QPoint] = None
        self._opacity    = 0.7

        if position in ("top", "bottom"):
            self.setFixedSize(175, 75)
        else:
            self.setFixedSize(75, 175)

        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        from src.styling import add_text_shadow
        add_text_shadow(self, blur=12, offset_x=0, offset_y=0, color="#000000")

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw the pill
        if self.position in ("top", "bottom"):
            pill_w, pill_h = 110, 12
        else:
            pill_w, pill_h = 12, 110

        x = (self.width()  - pill_w) // 2
        y = (self.height() - pill_h) // 2

        color = QColor(255, 255, 255, int(self._opacity * 255))
        painter.setBrush(QBrush(color))
        painter.setPen(QPen(Qt.GlobalColor.transparent))
        painter.drawRoundedRect(x, y, pill_w, pill_h, pill_h // 2, pill_h // 2)

    # ── Mouse events ──────────────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

        try:
            if self.client.SETTINGS.accessibility.handles_open_on_touch:
                self._simple_toggle()
                self._drag_start = None
        except Exception:
            pass

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._drag_start is None:
            return

        delta = event.globalPosition().toPoint() - self._drag_start
        dx, dy = delta.x(), delta.y()

        if max(abs(dx), abs(dy)) < self.min_drag:
            return

        if self.use_advanced_drag_logic:
            self._advanced_logic(dx, dy)
        else:
            self._simple_toggle()

        self._drag_start = None

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._drag_start = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    # ── Toggle logic ──────────────────────────────────────────────────────────

    def _can_call_event(self) -> bool:
        return time.time() - self.last_event_time >= self.min_time_to_event

    def _simple_toggle(self) -> None:
        if self.open:
            self.close_event()
        else:
            self.open_event()

    def _advanced_logic(self, dx: int, dy: int) -> None:
        match self.position:
            case "bottom":
                if dy < -self.min_drag:   self.open_event()
                elif dy > self.min_drag:  self.close_event()
            case "top":
                if dy > self.min_drag:    self.open_event()
                elif dy < -self.min_drag: self.close_event()
            case "right":
                if dx < -self.min_drag:   self.open_event()
                elif dx > self.min_drag:  self.close_event()
            case "left":
                if dx > self.min_drag:    self.open_event()
                elif dx < -self.min_drag: self.close_event()

    def open_event(self, event=None) -> None:
        if callable(self.on_open) and self._can_call_event() and not self.open:
            self.last_event_time = time.time()
            self.open = True
            self.on_open(event)

    def close_event(self, event=None) -> None:
        if callable(self.on_close) and self._can_call_event() and self.open:
            self.last_event_time = time.time()
            self.open = False
            self.on_close(event)

    def should_toggle(self) -> bool:
        return callable(self.on_open) and callable(self.on_close)