from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath, QFontMetrics

from src.ui.widget import Widget
from src.assets.bundled.CoreWidgetsBundle.timers import (
    clock, describe,
)
from src.styling import make_font, SIZES

if TYPE_CHECKING:
    from src.main import Client


class TimerWidget(Widget):
    """
    A running countdown, drawn as a draining square.

    Self-painted rather than composed from labels: the fill level is the whole
    point, and a QLabel cannot be half a colour. Painting it also means the
    square can rotate with the rest of the home screen, and there are no child
    hit targets to fall out of alignment.

    It renders a `Timer` the service owns and holds no countdown of its own -
    `sub.home` is destroyed and rebuilt on every navigation, so a widget owning
    the time left would cancel every timer in the house on the way to Settings.
    """

    KEY         = "timer"
    NAME        = "Timer"
    ICON        = "mdi.timer-outline"
    DESCRIPTION = "A running countdown."

    RESIZABLE = False
    ROTATABLE = True       # nothing but painting, so it can
    FLOATABLE = True
    REMOVABLE = True       # the delete handle stops the real timer
    MULTIPLE  = False      # placed by the service, never out of the panel

    SIZE = 148             # square
    MIN_W, MIN_H = SIZE, SIZE
    MAX_W, MAX_H = SIZE, SIZE

    RADIUS = 18

    def __init__(self, client: "Client", timer, service=None, **kwargs):
        # Its own key per timer, so several can be up at once and each is
        # addressable for dismissal.
        super().__init__(client=client, key=timer.key,
                         width=self.SIZE, height=self.SIZE,
                         floating=True, **kwargs)
        self.timer   = timer
        self.service = service
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        # Worked out once. The colour never changes, and deriving the empty
        # ground meant an HSV conversion on every single paint.
        self._base, self._empty = self._derive_colours()
        self._surface = QColor(self._base).lighter(135)
        self._surface.setAlpha(200)

        # What was last drawn, so tick() can tell whether anything actually
        # moved. See tick().
        self._last_render = None
        self._font_cache: dict = {}

        self.start_tick(500)

    ## -- painting

    def _derive_colours(self):
        base = QColor(self.timer.colour)
        if not base.isValid():
            base = QColor("#3f7fbf")
        # The empty part is the same hue, dark enough to read as empty rather
        # than as a second colour.
        empty = QColor()
        empty.setHsv(base.hue() if base.hue() >= 0 else 0,
                     max(0, base.saturation() - 90),
                     max(28, base.value() // 4))
        return base, empty

    def _fitted_font(self, face: str, width: float):
        """
        The largest font this face fits in, cached by the face.

        Measuring costs a QFont and a QFontMetrics per step, and the face only
        changes once a second at most - so doing it on every paint was work
        thrown away twice a second per timer.
        """
        cached = self._font_cache.get(face)
        if cached is not None:
            return cached
        size = SIZES.L2
        font = make_font(size, bold=True)
        while size > SIZES.S1:
            font = make_font(size, bold=True)
            if QFontMetrics(font).horizontalAdvance(face) <= width - 20:
                break
            size -= 3
        # A countdown only ever produces a handful of distinct widths, so this
        # is bounded without needing eviction.
        if len(self._font_cache) < 32:
            self._font_cache[face] = font
        return font

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.apply_rotation(painter)

        content_w, content_h = self.content_size()
        box = QRectF(0, 0, content_w, content_h)

        base, empty = self._base, self._empty
        done = self.timer.done

        clip = QPainterPath()
        clip.addRoundedRect(box, self.RADIUS, self.RADIUS)
        painter.setClipPath(clip)

        # The empty ground, then the time remaining drawn over it from the
        # bottom up - so the level falls as the timer runs out.
        painter.fillRect(box, QBrush(empty))

        if done:
            painter.fillRect(box, QBrush(base))
        else:
            filled_h = content_h * self.timer.fraction()
            if filled_h > 0:
                painter.fillRect(
                    QRectF(0, content_h - filled_h, content_w, filled_h),
                    QBrush(base))
                # A brighter line on the surface, so a nearly-full square still
                # reads as having a level rather than being plain colour.
                painter.fillRect(
                    QRectF(0, content_h - filled_h, content_w, 2.0),
                    QBrush(self._surface))

        painter.setClipping(False)

        # Border last, over the fill, so the corners stay clean.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5),
                                self.RADIUS, self.RADIUS)

        self._paint_text(painter, content_w, content_h, done)
        painter.end()

    def _paint_text(self, painter: QPainter, w: float, h: float,
                    done: bool) -> None:

        name = self.timer.name          # no title unless one was given
        face = "Done" if done else clock(self.timer.remaining())

        # Sized to fit rather than fixed: "1:04:09" is nearly twice the width
        # of "5:00", and a square that clips its own countdown is worse than
        # one with smaller digits. Cached, because the face changes at most
        # once a second.
        font = self._fitted_font(face, w)

        shift = 0.0
        if name:
            small = make_font(SIZES.S1)
            metrics = QFontMetrics(small)
            painter.setFont(small)
            painter.setPen(QPen(QColor(255, 255, 255, 195)))
            label = metrics.elidedText(name, Qt.TextElideMode.ElideRight,
                                       int(w) - 18)
            painter.drawText(
                QRectF(0, h * 0.15, w, metrics.height()),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                label)
            shift = metrics.height() * 0.5

        painter.setFont(font)
        # A shadow rather than an outline: the fill moves underneath the text,
        # so the contrast behind any given digit changes as it drains.
        painter.setPen(QPen(QColor(0, 0, 0, 140)))
        painter.drawText(QRectF(1.5, 1.5 + shift, w, h),
                         int(Qt.AlignmentFlag.AlignCenter), face)
        painter.setPen(QPen(QColor(255, 255, 255, 245)))
        painter.drawText(QRectF(0, shift, w, h),
                         int(Qt.AlignmentFlag.AlignCenter), face)

    ## -- lifecycle

    def _render_state(self):
        """What is actually visible: the face, and the fill level in pixels."""
        done = self.timer.done
        face = "Done" if done else clock(self.timer.remaining())
        _, content_h = self.content_size()
        level = 0 if done else int(self.timer.fraction() * content_h)
        return (face, level, done)

    def tick(self) -> None:
        """
        Repaint only when something moved.

        The overlay layer is translucent, so a repaint here forces everything
        composited above it to redraw too - including the frosted quick
        settings panel and its full-width backdrop pixmap. Repainting twice a
        second regardless of whether anything changed made opening quick
        settings visibly slower with timers on screen.

        The face changes once a second at most, and the fill only moves when
        it crosses a whole pixel - once every four seconds on a ten minute
        timer. Ticking stays at 500ms so nothing ever looks a beat late; what
        drops is the painting.
        """
        state = self._render_state()
        if state == self._last_render:
            return
        self._last_render = state
        self.update()

    def on_activate(self) -> None:
        """A tap that was not a drag: say how long is left."""
        if self.timer.done:
            self.client.simple_notify("mdi.timer-outline", self.timer.label(),
                                      "Finished.")
            return
        self.client.simple_notify(
            "mdi.timer-outline", self.timer.label(),
            f"{describe(self.timer.remaining())} left.")

    def on_dismissed(self) -> bool:
        """
        The delete handle was pressed.

        Cancels the countdown, not just the square. Removing the widget alone
        would leave the timer running with nothing on screen, and it would
        announce itself minutes later from nowhere.

        Returns True because the service takes the widget away itself, so the
        framework must not also try.
        """
        if self.service is None:
            return False
        self.service.cancel(self.timer.key)
        return True

    def teardown(self) -> None:
        self.stop_tick()
