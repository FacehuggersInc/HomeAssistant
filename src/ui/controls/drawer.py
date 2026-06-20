from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QSizePolicy
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen

from src.ui.controls.handle import Handle
from src.styling import COLORS, set_style

if TYPE_CHECKING:
    from src.main import Client


class Drawer(QWidget):
    """
    A slide-in toolbar at the top or bottom edge of its parent page.
    The handle pill peeks above the bottom edge; dragging it up slides
    the full bar into view.
    """

    BUTTON_BAR_HEIGHT = 85
    HANDLE_SPACING    = 8   # gap between handle and bar

    def __init__(
        self,
        client:             "Client",
        position:           str = "bottom",
        auto_close_seconds: int = 15,
    ):
        super().__init__()
        self.client      = client
        self.position    = position
        self.is_open     = False

        self._timeout_id = self.client.TIMEOUTS.add(
            auto_close_seconds,
            self._close,
            f"__timeout_drawer_{position}:{self.client.uuid()}",
        )

        # Handle
        self.handle = Handle(
            self.client,
            on_open  = self._open,
            on_close = self._close,
            position = position,
        )
        self.handle.use_advanced_drag_logic = True

        # Button bar
        self._bar = QWidget(self)
        self._bar.setFixedHeight(self.BUTTON_BAR_HEIGHT)

        set_style(self._bar, "common", "transparent")
        self._btn_layout = QHBoxLayout(self._bar)
        self._btn_layout.setContentsMargins(16, 0, 16, 0)
        self._btn_layout.setSpacing(8)
        self._btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Do NOT use a QLayout on self — position children manually
        # so we have full control over geometry without layout interference.
        self.handle.setParent(self)

        # Animation on self.pos()
        self._anim = QPropertyAnimation(self, b"pos")
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Will be set properly in place_on_page()
        self._total_height = (
            self.handle.height() + self.HANDLE_SPACING + self.BUTTON_BAR_HEIGHT
        )

    # ── Geometry helpers ──────────────────────────────────────────────────────

    def _layout_children(self) -> None:
        """Position handle and bar inside self without a QLayout."""
        w = self.width()
        h_h = self.handle.height()
        h_w = self.handle.width()

        if self.position == "bottom":
            # handle at top of drawer widget, bar below
            self.handle.move((w - h_w) // 2, 0)
            self._bar.setGeometry(0, h_h + self.HANDLE_SPACING, w, self.BUTTON_BAR_HEIGHT)
        else:
            # handle at bottom, bar above
            self._bar.setGeometry(0, 0, w, self.BUTTON_BAR_HEIGHT)
            self.handle.move((w - h_w) // 2, self.BUTTON_BAR_HEIGHT + self.HANDLE_SPACING)

    def _hidden_y(self) -> int:
        parent_h = self.parent().height() if self.parent() else 480
        if self.position == "bottom":
            # Only the handle peeks above the bottom edge
            return parent_h - self.handle.height() - self.HANDLE_SPACING
        else:
            return -(self._total_height - self.handle.height() - self.HANDLE_SPACING)

    def _shown_y(self) -> int:
        parent_h = self.parent().height() if self.parent() else 480
        if self.position == "bottom":
            return parent_h - self._total_height
        else:
            return 0

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        h_h = self.handle.height()
        sp  = self.HANDLE_SPACING

        if self.position == "bottom":
            bar_y = h_h + sp
        else:
            bar_y = 0

        color = QColor(COLORS.DARK.BGLIGHT)
        color.setAlphaF(0.90)
        painter.setBrush(QBrush(color))
        painter.setPen(QPen(Qt.GlobalColor.transparent))
        painter.drawRect(0, bar_y, self.width(), self.BUTTON_BAR_HEIGHT)

    # ── Controls ──────────────────────────────────────────────────────────────

    def add_controls(self, controls: list) -> None:
        for c in controls:
            self._btn_layout.addWidget(c)

    def insert_controls(self, controls: list) -> None:
        for control, index in controls:
            self._btn_layout.insertWidget(index, control)

    def remove_controls(self, controls: list) -> None:
        for item in controls:
            widget = item[0] if isinstance(item, tuple) else item
            self._btn_layout.removeWidget(widget)
            widget.setParent(None)

    # ── Open / close ──────────────────────────────────────────────────────────

    def _open(self, event=None) -> None:
        self.client.TIMEOUTS.start(self._timeout_id)
        self.is_open = True
        self._animate_to(self._shown_y())

    def _close(self, event=None) -> None:
        self.client.TIMEOUTS.cancel(self._timeout_id)
        self.is_open = False
        self._animate_to(self._hidden_y())

    def _animate_to(self, y: int) -> None:
        self._anim.stop()
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(QPoint(self.x(), y))
        self._anim.start()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def place_on_page(self) -> None:
        """
        Call once after the parent page exists and has a valid size.
        Sets width, lays out children, moves to hidden position, then shows.
        """
        if not self.parent():
            return

        pw = self.parent().width()
        self.setFixedWidth(pw)
        self.setFixedHeight(self._total_height)
        self._layout_children()
        self.move(0, self._hidden_y())
        self.show()
        self.raise_()

    def apply_parent_width(self) -> None:
        if not self.parent():
            return
        pw = self.parent().width()
        if self.width() != pw:
            self.setFixedWidth(pw)
            self._layout_children()
            # Re-snap to correct y in case height changed
            if not self._anim.state() == QPropertyAnimation.State.Running:
                y = self._shown_y() if self.is_open else self._hidden_y()
                self.move(0, y)

    # ── Tick ──────────────────────────────────────────────────────────────────

    def tick(self) -> None:
        try:
            target_w = int(self.client.SETTINGS.application.window.size.value[0])
            if self.width() != target_w:
                self.apply_parent_width()
        except Exception:
            pass