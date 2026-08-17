"""
A drawn coin, and the flip that lands on an answer already decided.

The result is chosen BEFORE the animation starts and the animation is
choreographed to land on it. A simulation would have to be rigged to finish on
a predetermined face, which is harder than an animation that was always
heading there - and it makes three things true that matter more than realism:

  * the settle moment is known in advance, so whatever wanted the answer can
    schedule what happens next rather than watching for it
  * a dropped frame, a slow panel or an early dismissal cannot change the
    outcome, because the outcome was never a product of the drawing
  * the caller can have the result immediately while the show plays out

Nothing here touches the client, the overlay or a setting. It is a QWidget
that draws a coin - see stage.py for the thing that puts one on screen.
"""

from __future__ import annotations

import math
import random
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import QPainter, QPainterPath, QColor, QLinearGradient, QPen
from PyQt6.QtWidgets import QWidget

HEADS = "heads"
TAILS = "tails"
FACES = (HEADS, TAILS)

# Chosen from the OS entropy pool rather than the Mersenne Twister. It costs
# nothing here and this is asked to settle arguments between people.
_rng = random.SystemRandom()

# ── The coin's own palette ───────────────────────────────────────────────────
# Not from COLORS, which is the panel's greyscale and brand green. A coin that
# is not gold does not read as a coin at a distance, and reading at a distance
# is the whole job.
FACE_LIGHT = QColor("#f2cd7e")
FACE_MID = QColor("#d9a441")
FACE_DARK = QColor("#a8761f")
RIM = QColor("#f7e0aa")
ENGRAVE = QColor("#6b4a12")
EDGE = QColor("#8f6318")


def other_face(face: str) -> str:
    return TAILS if face == HEADS else HEADS


def decide() -> str:
    """One flip. The only place the outcome is ever chosen."""
    return _rng.choice(FACES)


def _heads_path(radius: float) -> QPainterPath:
    """
    A crown - the monarch's side, which is why it is called heads.

    A head in profile was tried first and is the obvious choice, but a face
    drawn small enough to sit on a coin needs the brow, the eye socket, the
    lip and the chin all set back from each other by a few pixels, and any
    less than that reads as a hooded figure. Face-on is worse: a symmetrical
    head and shoulders is the account-avatar glyph on every phone.

    A crown carries the same meaning, is unambiguous at any size, and is
    straight lines and circles.

    Filled rather than stroked, because the face is squashed vertically
    during the flip: a stroked path scaled on one axis gets a pen thick one
    way and thin the other, and the motif turns to mush halfway through every
    turn. A filled shape just gets shorter.
    """
    unit = radius / 100.0
    path = QPainterPath()

    def at(x, y):
        return QPointF(x * unit, y * unit)

    # The points, as one zigzag closed along its base. The middle peak is the
    # tallest, so the shape is read as a crown rather than as three spikes.
    body = QPainterPath()
    body.moveTo(at(-52, 26))
    body.lineTo(at(-52, -18))
    body.lineTo(at(-26, 8))
    body.lineTo(at(0, -36))
    body.lineTo(at(26, 8))
    body.lineTo(at(52, -18))
    body.lineTo(at(52, 26))
    body.closeSubpath()
    path = path.united(body)

    # The band it sits on, wider than the points so it reads as a base.
    band = QPainterPath()
    band.addRoundedRect(QRectF(-58 * unit, 22 * unit, 116 * unit, 22 * unit),
                        6 * unit, 6 * unit)
    path = path.united(band)

    # A jewel on each point.
    for x, y in ((-52, -24), (0, -42), (52, -24)):
        tip = QPainterPath()
        tip.addEllipse(at(x, y), 8 * unit, 8 * unit)
        path = path.united(tip)

    return path


def _tails_path(radius: float) -> QPainterPath:
    """
    Nothing. Tails is the blank face.

    Two motifs both have to be recognised and told apart; one motif and a
    plain face only has to be recognised, and "there is a crown on it or
    there is not" is the easiest thing to see from across a room. It also
    means the moment of landing is unmistakable at a glance, without reading
    the banner.
    """
    return QPainterPath()


class CoinWidget(QWidget):
    """
    A coin, mid-flip or at rest.

    Sized to the whole arc rather than to the coin, so the widget never moves
    and only its own rect is ever repainted. The overlay layer is translucent
    and a repaint on it forces everything composited above to redraw as well,
    so how much area changes per frame is the number that matters.
    """

    # Below this the face is edge-on and the motif is not worth drawing;
    # the coin's side is drawn instead.
    EDGE_ON = 0.10
    # How far the squash goes when it lands, and for how long.
    SETTLE_SQUASH = 0.84
    SETTLE_MS = 170

    def __init__(self, diameter: int, arc_height: int, parent=None):
        super().__init__(parent)

        self.diameter = int(diameter)
        self.arc_height = int(arc_height)

        # The border is stroked ON the disc's outline, so half of it sits
        # outside the ellipse. Without padding for that the widget's own edge
        # cuts the left and right of the coin off - which reads as the coin
        # being wider than the space it was given rather than as a clipped
        # pen.
        self.border = max(2.0, self.diameter * 0.035)
        self.pad = int(self.border) + 2

        self.setFixedSize(self.diameter + self.pad * 2,
                          self.diameter + self.arc_height + self.pad * 2)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.result = HEADS
        self._start_face = HEADS
        self._turns = 10

        self._elapsed = 0
        self._duration = 0
        self._frame_ms = 33
        self._settling = 0
        self._running = False
        self._on_settled: Optional[Callable] = None

        # What was last actually drawn. A frame that would draw the same
        # picture is skipped - see tick().
        self._drawn = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # ── Running it ───────────────────────────────────────────────────────────

    def start(self, result: str, duration_ms: int = 2200, frame_ms: int = 33,
              animate: bool = True, on_settled: Callable = None) -> None:
        """
        Flip to `result`, which has already been decided.

        `animate=False` shows the answer immediately and still calls
        `on_settled`, so a panel with animation turned off follows exactly the
        same sequence as one without.
        """
        self.result = result if result in FACES else HEADS
        self._on_settled = on_settled
        self._frame_ms = max(16, int(frame_ms))
        self._elapsed = 0
        self._settling = 0
        self._drawn = None

        if not animate or duration_ms <= 0:
            self._running = False
            self._start_face = self.result
            self._turns = 0
            self.update()
            QTimer.singleShot(0, self._settled)
            return

        # An even number of half-turns finishes on the face it started on, an
        # odd number on the other. The result is fixed, so the START is what
        # gets chosen to suit it.
        self._turns = _rng.randint(9, 14)
        self._start_face = (self.result if self._turns % 2 == 0
                            else other_face(self.result))

        self._duration = max(200, int(duration_ms))
        self._running = True
        self._timer.start(self._frame_ms)

    def stop(self) -> None:
        """Give up drawing. The result stands - it was never the drawing's."""
        self._running = False
        self._timer.stop()
        self._on_settled = None

    def _tick(self) -> None:
        self._elapsed += self._frame_ms

        if self._running and self._elapsed >= self._duration:
            self._running = False
            self._settling = 1
            self._elapsed = 0

        if self._settling and self._elapsed >= self.SETTLE_MS:
            self._settling = 0
            self._timer.stop()
            self.update()
            self._settled()
            return

        # Only if the picture actually changed. Twice a second of nothing is
        # cheap; thirty repaints a second of nothing is not, on a layer that
        # drags the whole composite with it.
        face, scale_y, lift = self._pose()
        drawn = (face, round(scale_y * 50), round(lift))
        if drawn != self._drawn:
            self._drawn = drawn
            self.update()

    def _settled(self) -> None:
        hook, self._on_settled = self._on_settled, None
        if callable(hook):
            hook()

    # ── Where the coin is, this frame ────────────────────────────────────────

    def _pose(self) -> tuple:
        """The face showing, how squashed it is, and how high it is."""
        if self._settling:
            # A short squash on landing, and back. Purely the arrival.
            progress = min(1.0, self._elapsed / float(self.SETTLE_MS))
            squash = math.sin(progress * math.pi)
            return (self.result,
                    1.0 - (1.0 - self.SETTLE_SQUASH) * squash,
                    0.0)

        if not self._running:
            return self.result, 1.0, 0.0

        progress = min(1.0, self._elapsed / float(self._duration))

        # The spin decelerates so the last half-turn reads as it settling;
        # the arc does not, because a coin goes up fast, hangs, and comes
        # down fast, which is what a plain sine already does.
        eased = 1.0 - (1.0 - progress) ** 2.2
        turned = eased * self._turns

        # Which half-turn is showing, rounded rather than truncated.
        #
        # A face is edge-on at turned = k + 0.5 and full-on at k, so the face
        # has to change as it passes THROUGH edge-on. Truncating changes it at
        # the whole turn instead - which is the moment the face is widest - so
        # the last approach was drawn showing the wrong side at nearly full
        # size and then swapped at the end. That reads exactly as the result
        # being painted over the animation rather than the animation arriving
        # at it, because that is what it was.
        half_turns = int(turned + 0.5)
        face = (self._start_face if half_turns % 2 == 0
                else other_face(self._start_face))
        scale_y = abs(math.cos(turned * math.pi))
        lift = math.sin(progress * math.pi) * self.arc_height
        return face, scale_y, lift

    # ── Drawing it ───────────────────────────────────────────────────────────

    def content_centre_y(self) -> int:
        """
        Where the coin sits when it is at rest, within this widget.

        The stage centres content on this rather than on the widget's middle,
        so the arc is headroom above a coin that lands in the middle of the
        screen - not something that pushes the resting coin below it.
        """
        return int(self.pad + self.arc_height + self.diameter / 2)

    def paintEvent(self, event) -> None:
        face, scale_y, lift = self._pose()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        radius = self.diameter / 2.0
        center_x = self.width() / 2.0
        center_y = self.content_centre_y() - lift

        painter.translate(center_x, center_y)

        if scale_y <= self.EDGE_ON:
            self._paint_edge(painter, radius, scale_y)
        else:
            self._paint_face(painter, radius, scale_y, face)

        painter.end()

    def _paint_edge(self, painter: QPainter, radius: float,
                    scale_y: float) -> None:
        """The coin seen side on: its thickness, not either face."""
        thickness = max(3.0, radius * 0.10)
        height = max(thickness, radius * 2 * scale_y + thickness)
        rect = QRectF(-radius, -height / 2.0, radius * 2, height)

        gradient = QLinearGradient(-radius, 0, radius, 0)
        gradient.setColorAt(0.0, EDGE)
        gradient.setColorAt(0.5, FACE_MID)
        gradient.setColorAt(1.0, EDGE)

        painter.setPen(QPen(EDGE, max(1.0, self.border * 0.5)))
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, thickness / 2.0, thickness / 2.0)

    def _paint_face(self, painter: QPainter, radius: float, scale_y: float,
                    face: str) -> None:
        squashed = radius * scale_y
        rect = QRectF(-radius, -squashed, radius * 2, squashed * 2)

        gradient = QLinearGradient(0, -squashed, 0, squashed)
        gradient.setColorAt(0.0, FACE_LIGHT)
        gradient.setColorAt(0.55, FACE_MID)
        gradient.setColorAt(1.0, FACE_DARK)

        # The border is the coin's edge seen head on, so it is the same colour
        # the edge is drawn in when the coin turns side on. A rim that changed
        # colour between the two would read as two different objects.
        painter.setPen(QPen(EDGE, self.border))
        painter.setBrush(gradient)
        painter.drawEllipse(rect)

        # A milled highlight just inside it. Clamped vertically, or at the end
        # of a turn the inset is deeper than the face is tall and the ellipse
        # inverts.
        inset = self.border * 1.6
        inset_y = min(inset, squashed * 0.45)
        if squashed - inset_y > 1.0:
            painter.setPen(QPen(RIM, max(1.0, self.border * 0.45)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(-radius + inset, -squashed + inset_y,
                                       (radius - inset) * 2,
                                       (squashed - inset_y) * 2))

        if face != HEADS:
            return

        # The motif, squashed with the face. Scaling the painter rather than
        # the path keeps the shape defined once at full size.
        painter.save()
        painter.scale(1.0, scale_y)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ENGRAVE)
        painter.drawPath(_heads_path(radius))
        painter.restore()
