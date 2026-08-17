"""
A drawn wheel, and the spin that lands on an answer already decided.

Same contract as the coin and the dice. `wheels.pick()` chooses the winner
before anything is painted, and the rotation is worked out so that slice
finishes under the pointer. A spin that decided its own answer by where it
happened to stop would put the outcome in the hands of the frame rate.

Nothing here reads a setting or touches the client. It is a QWidget that
draws a wheel - see stage.py for the thing that puts one on screen, and
wheels.py for what a wheel is.
"""

from __future__ import annotations

import math
import random
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import (QPainter, QPainterPath, QColor, QPen, QFont,
                         QFontMetrics)
from PyQt6.QtWidgets import QWidget

from src.styling import COLORS

_rng = random.SystemRandom()

# Below this a slice is too narrow to carry its own name, and the label is
# left off rather than drawn as one elided letter.
LABEL_MIN_DEGREES = 13.0
# And below this it cannot carry its percentage either.
PERCENT_MIN_DEGREES = 26.0


def slice_colour(item: dict) -> QColor:
    """
    A slice's fill, from the hue its label was given.

    Lightness is varied as well as hue. Hues taken from a hash land where
    they land, and two of six coming out as neighbouring blues is common
    enough to see - varying the tone as well means two slices that are close
    on the wheel are still told apart at a glance.
    """
    hue = int(item.get("hue", 0)) % 360
    lightness = 0.44 + (int(item.get("tone", 0)) % 5) * 0.05
    return QColor.fromHslF(hue / 360.0, 0.62, lightness)


class WheelWidget(QWidget):
    """
    The wheel, its pointer, and one spin.

    The pointer does not move. A wheel with a travelling pointer has two
    things to watch and no fixed place to look, and the moment worth watching
    is the wheel slowing under a mark that has been there the whole time.
    """

    #how many whole turns before it starts hunting for the answer
    MIN_TURNS = 4
    MAX_TURNS = 7
    #how much of the widget the pointer hangs over the rim by
    POINTER = 0.085
    #the hub in the middle, as a fraction of the radius
    HUB = 0.17

    def __init__(self, items: list, size: int, parent=None):
        super().__init__(parent)

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # Already normalised by wheels.normalise() - shares are whole
        # percentages that come to 100, and disabled items are gone.
        self.items = list(items or [])
        self.size = int(size)
        self.pointer = max(10, int(self.size * self.POINTER))
        # A little more than the pointer needs, or its outline is drawn
        # flush against the widget's own edge and shaved off.
        self.padding = self.pointer + 6
        self.setFixedSize(self.size + self.padding * 2,
                          self.size + self.padding * 2)

        self.winner = 0
        self._turn = 0.0
        self._target = 0.0
        self._elapsed = 0
        self._duration = 0
        self._frame_ms = 33
        self._running = False
        self._on_settled: Optional[Callable] = None
        self._drawn = None

        self._spans = self._measure()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # ── Where each slice sits ────────────────────────────────────────────────

    def _measure(self) -> list:
        """
        Each slice as `(start, span)` in degrees clockwise from the top.

        Built once. The wheel turns by rotating the painter, so the slices
        themselves never move and there is nothing to recompute per frame.
        """
        spans = []
        at = 0.0
        for item in self.items:
            span = float(item.get("share", 0)) * 3.6
            spans.append((at, span))
            at += span
        return spans

    def _centre_of(self, index: int) -> float:
        """The middle of a slice, clockwise from the top."""
        if not (0 <= index < len(self._spans)):
            return 0.0
        start, span = self._spans[index]
        return start + span / 2.0

    # ── Running it ───────────────────────────────────────────────────────────

    def start(self, winner: int, duration_ms: int = 3600, frame_ms: int = 33,
              animate: bool = True, on_settled: Callable = None) -> None:
        """
        Spin to `winner`, which has already been chosen.

        The landing angle is what the winner requires; the number of whole
        turns before it is the only part left to chance, and it changes
        nothing except how long it feels.
        """
        self.winner = max(0, min(int(winner), max(0, len(self.items) - 1)))
        self._on_settled = on_settled
        self._frame_ms = max(16, int(frame_ms))
        self._elapsed = 0
        self._drawn = None

        # The wheel turns clockwise, so bringing a slice at `centre` up to the
        # pointer at the top means turning by what is left of the circle.
        landing = (360.0 - self._centre_of(self.winner)) % 360.0
        turns = _rng.randint(self.MIN_TURNS, self.MAX_TURNS)
        self._target = turns * 360.0 + landing

        if not animate or duration_ms <= 0 or not self.items:
            self._running = False
            self._turn = landing
            self.update()
            QTimer.singleShot(0, self._settled)
            return

        self._turn = 0.0
        self._duration = max(300, int(duration_ms))
        self._running = True
        self._timer.start(self._frame_ms)

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        self._on_settled = None

    def _tick(self) -> None:
        self._elapsed += self._frame_ms
        if self._elapsed >= self._duration:
            self._running = False
            self._turn = self._target
            self._timer.stop()
            self.update()
            self._settled()
            return

        progress = self._elapsed / float(self._duration)
        # Cubic ease out. A wheel is all deceleration - it is thrown once and
        # everything after that is it running down, and the last few degrees
        # are the part anybody is watching.
        self._turn = self._target * (1.0 - (1.0 - progress) ** 3)

        drawn = round(self._turn, 1)
        if drawn != self._drawn:
            self._drawn = drawn
            self.update()

    def _settled(self) -> None:
        hook, self._on_settled = self._on_settled, None
        if callable(hook):
            hook()

    # ── Drawing ──────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        radius = self.size / 2.0
        centre = QPointF(self.width() / 2.0, self.height() / 2.0)

        painter.save()
        painter.translate(centre)
        painter.rotate(self._turn)
        self._paint_slices(painter, radius)
        painter.restore()

        painter.save()
        painter.translate(centre)
        self._paint_rim(painter, radius)
        self._paint_hub(painter, radius)
        painter.restore()

        # Last and unrotated: the pointer is the one fixed thing on screen.
        self._paint_pointer(painter, centre, radius)
        painter.end()

    def _paint_slices(self, painter: QPainter, radius: float) -> None:
        box = QRectF(-radius, -radius, radius * 2, radius * 2)
        edge = QColor(COLORS.DARK.BGDARK)

        for index, item in enumerate(self.items):
            start, span = self._spans[index]
            if span <= 0:
                continue

            wedge = QPainterPath()
            wedge.moveTo(0, 0)
            # Qt measures anticlockwise from three o'clock; these are
            # clockwise from twelve. Hence the 90 and the minus.
            wedge.arcTo(box, 90.0 - start, -span)
            wedge.closeSubpath()

            painter.setPen(QPen(edge, max(1.5, radius * 0.012)))
            painter.setBrush(slice_colour(item))
            painter.drawPath(wedge)

        for index, item in enumerate(self.items):
            self._paint_label(painter, radius, index, item)

    def _paint_label(self, painter: QPainter, radius: float, index: int,
                     item: dict) -> None:
        start, span = self._spans[index]
        if span < LABEL_MIN_DEGREES:
            return

        middle = start + span / 2.0
        painter.save()
        # Minus ninety, for the same reason the wedges carry a plus ninety.
        # Qt's zero is three o'clock and these angles are clockwise from
        # twelve, so rotating by the slice angle alone leaves +x - the
        # direction the text runs along - a quarter turn away from the slice
        # it belongs to. Every label was in the wrong wedge.
        painter.rotate(middle - 90.0)

        # Right way up on the left half of the wheel. Text hanging upside
        # down is unreadable however correctly it is placed - but turning the
        # painter another 180 degrees also turns "outward" from +x to -x, and
        # drawing at +x anyway puts a slice's name in the slice opposite. So
        # the box moves to the other side of the centre with the text.
        flipped = ((middle + self._turn) % 360.0) > 180.0
        if flipped:
            painter.rotate(180.0)

        size = max(9, int(radius * 0.095))
        font = QFont("poppins-medium", size)
        font.setBold(True)
        metrics = QFontMetrics(font)

        inner = radius * (self.HUB + 0.08)
        room = int(radius - inner - radius * 0.10)
        if room < 24:
            painter.restore()
            return

        label = metrics.elidedText(str(item.get("label", "")),
                                   Qt.TextElideMode.ElideRight, room)
        percent = (f"{int(item.get('share', 0))}%"
                   if span >= PERCENT_MIN_DEGREES else "")

        left = -(inner + room) if flipped else inner
        align = (Qt.AlignmentFlag.AlignRight if flipped
                 else Qt.AlignmentFlag.AlignLeft)

        # Stacked, not overlapping: the name sits above the radius line and
        # the share below it, rather than both being centred on it.
        line = metrics.height()
        top = -line if percent else -line / 2.0

        painter.setFont(font)
        painter.setPen(QColor(COLORS.DARK.BGDARK))
        painter.drawText(QRectF(left, top, room, line),
                         int(align | Qt.AlignmentFlag.AlignVCenter), label)

        if percent:
            small = QFont("poppins-light", max(8, int(size * 0.78)))
            painter.setFont(small)
            painter.drawText(QRectF(left, 0.0, room,
                                    QFontMetrics(small).height()),
                             int(align | Qt.AlignmentFlag.AlignVCenter),
                             percent)
        painter.restore()

    def _paint_rim(self, painter: QPainter, radius: float) -> None:
        painter.setPen(QPen(QColor(COLORS.DARK.BORDER.HIGHLIGHT),
                            max(2.0, radius * 0.035)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(-radius, -radius, radius * 2, radius * 2))

    def _paint_hub(self, painter: QPainter, radius: float) -> None:
        hub = radius * self.HUB
        painter.setPen(QPen(QColor(COLORS.DARK.BORDER.HIGHLIGHT),
                            max(1.5, radius * 0.02)))
        painter.setBrush(QColor(COLORS.DARK.BG))
        painter.drawEllipse(QRectF(-hub, -hub, hub * 2, hub * 2))

    def _paint_pointer(self, painter: QPainter, centre: QPointF,
                       radius: float) -> None:
        """A wedge at the top, biting into the rim."""
        width = self.pointer * 1.25
        top = centre.y() - radius - self.pointer
        into = centre.y() - radius + self.pointer * 0.55

        point = QPainterPath()
        point.moveTo(centre.x() - width, top)
        point.lineTo(centre.x() + width, top)
        point.lineTo(centre.x(), into)
        point.closeSubpath()

        painter.setPen(QPen(QColor(COLORS.DARK.BGDARK),
                            max(1.5, radius * 0.014)))
        painter.setBrush(QColor(COLORS.DARK.TEXT.IMPORTANT))
        painter.drawPath(point)
