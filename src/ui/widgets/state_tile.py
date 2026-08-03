"""
Tiles that hold a state, and tiles that hold a state and a value.

`StateTile` is a tile that is one of two or three things and looks different
in each. `SliderTile` is the same tile with a value under it, dragged along
whichever axis it is longer on.

Base classes rather than a widget each. Do not disturb, silence and night
mode are the same tile with different words, and writing that three times is
how three of them end up behaving slightly differently.

**Reading is the subclass's job.** These never cache what the state is - they
ask on every tick, because the thing they toggle can be changed from a skill,
a Quick Setting or another panel, and a tile showing what it last set is a
tile that lies whenever anything else touches it.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QColor, QPainterPath, QPen, QLinearGradient, QMouseEvent,
)

from src.styling import make_font, SIZES, set_style, add_text_shadow
from src.ui.icons import icon
from src.ui.widgets.tile import Tile

if TYPE_CHECKING:
    from src.main import Client


class TileState:
    """
    One of the things a tile can be, and how it looks while it is.

    Every colour is optional. A state that names none inherits the tile's
    ordinary card look, which is what "off" should usually be - a tile that
    shouts in both states tells you nothing by shouting.
    """

    __slots__ = ("key", "label", "icon", "background", "border", "ink")

    def __init__(self, key: str, label: str = "", icon: str = "",
                 background: str = "", border: str = "", ink: str = ""):
        self.key = key
        self.label = label or key.replace("_", " ").title()
        self.icon = icon
        self.background = background
        self.border = border
        self.ink = ink or "#f0f0f4"


class StateTile(Tile):
    """
    A tile that is one of two or three things.

    A subclass provides `STATES` and answers `read_state()`; pressing it calls
    `apply_state()` with whichever comes next. That split is the whole point:
    the tile owns how it looks, and the subclass owns what it means.
    """

    #Filled in by a subclass. Two or three - past that a tap is a guessing
    #game, and what somebody wants is a menu rather than a tile.
    STATES: list = []

    MIN_GRID_W, MIN_GRID_H = 1, 1
    MAX_GRID_W, MAX_GRID_H = 4, 4

    #How often the state is re-read. Cheap, and the answer can change from
    #anywhere.
    REFRESH_SECONDS = 1.0

    def __init__(self, client: "Client", grid_w: int = 1, grid_h: int = 1,
                 **kwargs):
        self._state_key = ""
        self._checked_at = 0.0
        self._label: Optional[QLabel] = None
        self._icon: Optional[QLabel] = None
        super().__init__(client, grid_w=grid_w, grid_h=grid_h, **kwargs)
        self.on_click = self._pressed
        self._state_key = self._safe_read()
        self._apply_face()

    ## -- for a subclass

    def read_state(self) -> str:
        """Which state key this is right now."""
        return self.STATES[0].key if self.STATES else ""

    def apply_state(self, key: str) -> None:
        """Make it so. Called with the key the press landed on."""

    def next_state(self, key: str) -> str:
        """
        Which state a press moves to. Round the list by default.

        Overridable because three states are not always a cycle - see the
        brightness tile, where the third is "back to what it was".
        """
        keys = [state.key for state in self.STATES]
        if key not in keys:
            return keys[0] if keys else ""
        return keys[(keys.index(key) + 1) % len(keys)]

    ## -- state

    def _safe_read(self) -> str:
        try:
            return self.read_state() or ""
        except Exception as e:
            self.client.log("debug", f"[{type(self).__name__}] read failed: {e}")
            return ""

    def state(self) -> TileState:
        for entry in self.STATES:
            if entry.key == self._state_key:
                return entry
        return self.STATES[0] if self.STATES else TileState("", "")

    def _pressed(self) -> None:
        if not self.STATES:
            return
        wanted = self.next_state(self._state_key or self.STATES[0].key)
        try:
            self.apply_state(wanted)
        except Exception as e:
            self.client.log("warning",
                            f"[{type(self).__name__}] could not set "
                            f"'{wanted}': {e}")
        # Re-read rather than assume. What was asked for and what happened
        # are different questions, and the second is the one to draw.
        self._state_key = self._safe_read()
        self._checked_at = time.time()
        self._apply_face()
        self.update()

    def tick(self) -> None:
        if time.time() - self._checked_at < self.REFRESH_SECONDS:
            return
        self._checked_at = time.time()
        current = self._safe_read()
        if current != self._state_key:
            self._state_key = current
            self._apply_face()
            self.update()

    ## -- face

    def build_variants(self) -> None:
        self.add_variant(1, 1, self._build_face)

    def _build_face(self) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")
        column = QVBoxLayout(host)
        column.setContentsMargins(8, 8, 8, 8)
        column.setSpacing(4)
        column.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self._icon, "common", "transparent")
        column.addWidget(self._icon)

        self._label = QLabel("")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setFont(make_font(SIZES.S1, bold=True))
        add_text_shadow(self._label, blur=8)
        column.addWidget(self._label)

        self._apply_face()
        return host

    def _apply_face(self) -> None:
        state = self.state()
        if self._label is not None:
            # `label_for` hides it entirely at 1x1 - a square that size holds
            # an icon and three letters, and three letters of a word is not a
            # word.
            text = self.label_for(self._label, state.label)
            self._label.setText(text)
            self._label.setVisible(bool(text))
            self._label.setStyleSheet(
                f"color:{state.ink};background:transparent;")
        if self._icon is not None and state.icon:
            side = max(20, min(56, min(self.width(), self.height()) // 3))
            try:
                self._icon.setPixmap(
                    icon(state.icon, color=state.ink).pixmap(side, side))
            except Exception:
                self._icon.clear()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        try:
            self._apply_face()
        except Exception:
            pass

    ## -- painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        body = QRectF(self.rect().adjusted(1, 1, -1, -1))
        path = QPainterPath()
        path.addRoundedRect(body, self.radius, self.radius)

        state = self.state()
        painter.fillPath(path, QColor(state.background or self.bg_color))
        self.paint_value(painter, body, path)

        if state.border:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(state.border), 2))
            painter.drawPath(path)

        if self.dragging:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 60))
        if self.selected and not self.dragging:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#cfe4ff"), 2, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2),
                                    self.radius, self.radius)
            self._paint_handles(painter)
        painter.end()

    def paint_value(self, painter: QPainter, body: QRectF,
                    path: QPainterPath) -> None:
        """Between the background and the border. See SliderTile."""


class SliderTile(StateTile):
    """
    A state tile with a value you can drag.

    Which way it slides comes from its shape: wider than tall goes left to
    right, otherwise bottom to top. Square counts as vertical - a square
    handle read as horizontal is a coin toss, and up-for-more is the one
    everybody agrees on.

    A tap still toggles the state, which is what makes this one control
    rather than two: volume slides and mutes, brightness slides and has a
    full / low / last cycle.
    """

    #How far a finger has to move before it is a slide rather than a tap.
    #Below this every tap nudges the value it was meant to leave alone.
    SLIDE_THRESHOLD = 6

    def __init__(self, client: "Client", grid_w: int = 2, grid_h: int = 1,
                 **kwargs):
        self._value = 0.0
        self._sliding = False
        self._slide_from: Optional[QPointF] = None
        super().__init__(client, grid_w=grid_w, grid_h=grid_h, **kwargs)
        self._value = self._safe_value()

    ## -- for a subclass

    def read_value(self) -> float:
        """Where the value is now, 0..1."""
        return 0.0

    def apply_value(self, value: float) -> None:
        """Put it there. Called while dragging, so keep it cheap."""

    ## -- value

    def _safe_value(self) -> float:
        try:
            return max(0.0, min(1.0, float(self.read_value())))
        except Exception:
            return 0.0

    @property
    def vertical(self) -> bool:
        return self.grid_w <= self.grid_h

    def tick(self) -> None:
        super().tick()
        if self._sliding:
            # Not while a finger is on it. Re-reading mid-drag fights the
            # drag, and the value jumps back a frame at a time.
            return
        current = self._safe_value()
        if abs(current - self._value) > 0.005:
            self._value = current
            self.update()

    ## -- input

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._slide_from = event.position()
        self._sliding = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        # Only once it has moved far enough, and never while the tile itself
        # is being dragged around the grid or resized - the grid's gesture
        # wins, because moving a tile is rarer and harder to recover from.
        if self._slide_from is not None and not self.dragging and not self.resizing:
            delta = event.position() - self._slide_from
            travelled = abs(delta.y()) if self.vertical else abs(delta.x())
            if self._sliding or travelled >= self.SLIDE_THRESHOLD:
                self._sliding = True
                self._set_from_point(event.position())
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._sliding:
            # It was a slide. Not a tap, so the state must not toggle - the
            # one thing nobody wants after setting the volume is for it to
            # mute as they let go.
            self._sliding = False
            self._slide_from = None
            self.dragging = False
            self.drag_start = None
            self.update()
            return
        self._slide_from = None
        super().mouseReleaseEvent(event)

    def _set_from_point(self, point: QPointF) -> None:
        if self.vertical:
            share = 1.0 - (point.y() / max(1.0, self.height()))
        else:
            share = point.x() / max(1.0, self.width())
        self._value = max(0.0, min(1.0, share))
        try:
            self.apply_value(self._value)
        except Exception as e:
            self.client.log("warning",
                            f"[{type(self).__name__}] could not set: {e}")
        self.update()

    ## -- painting

    def paint_value(self, painter: QPainter, body: QRectF,
                    path: QPainterPath) -> None:
        """
        The filled part, clipped to the tile's own rounded corners.

        Drawn as a share of the tile rather than as a bar inside it: the
        whole tile IS the control, which is what makes it usable with a thumb
        on a wall panel.
        """
        state = self.state()
        if self._value <= 0:
            return

        painter.save()
        painter.setClipPath(path)
        tint = QColor(state.border or state.ink)
        tint.setAlpha(70)

        if self.vertical:
            height = body.height() * self._value
            filled = QRectF(body.left(), body.bottom() - height,
                            body.width(), height)
        else:
            filled = QRectF(body.left(), body.top(),
                            body.width() * self._value, body.height())
        painter.fillRect(filled, tint)

        # A line at the level, so the edge is readable when the fill is not.
        edge = QColor(state.border or state.ink)
        painter.setPen(QPen(edge, 2))
        if self.vertical:
            painter.drawLine(QPointF(filled.left(), filled.top()),
                             QPointF(filled.right(), filled.top()))
        else:
            painter.drawLine(QPointF(filled.right(), filled.top()),
                             QPointF(filled.right(), filled.bottom()))
        painter.restore()
