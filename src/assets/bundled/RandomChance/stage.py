"""
Where a chance is shown: a banner, the thing itself, and the answer.

The stage is an overlay rather than a home-screen widget, because a flip
asked for by voice happens on whatever page is up. A transient widget belongs
to `sub.home`'s framework and only exists while that page is on screen, so
asking from Settings or the calendar would have had nowhere to draw.

Everything it puts up is `WA_TransparentForMouseEvents`, which `OverlayManager`
hosts on its passthrough surface - so the panel underneath stays usable while
a coin is in the air and no tap is ever swallowed by a decoration.

Every method here runs on the UI thread. A skill runs on the assistant's
worker, so the caller marshals - see main.py.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtGui import QPainter, QColor, QFontMetrics
from PyQt6.QtWidgets import QWidget

from src.styling import COLORS, SIZES, make_font

# The banner sits above the middle, leaving the centre for whatever is being
# shown. A fraction of the height rather than a pixel count, so it lands in
# the same place on a 1024x600 panel and on a 1080p one.
BANNER_Y = 0.17
# The coin is sized from the shorter edge for the same reason.
COIN_FRACTION = 0.30
COIN_MIN = 120
COIN_MAX = 340


class Banner(QWidget):
    """
    A line of text on a slab, drawn rather than composed from labels.

    Self-painted because it lives on the overlay: a QLabel here would want a
    stylesheet, a background attribute and its own opaque region, and the one
    thing this has to do is be legible over whatever page happens to be
    behind it.

    An optional second line sits under the first, smaller and muted. Dice
    have two answers - the total, and what each one showed - and they are not
    equally important, so they are not the same size.
    """

    PAD_X = 34
    PAD_Y = 16
    GAP = 6
    RADIUS = 18

    def __init__(self, text: str, size: int, detail: str = "",
                 detail_size: int = SIZES.S2, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.text = str(text or "")
        self.detail = str(detail or "")
        self._font = make_font(size, bold=True)
        self._detail_font = make_font(detail_size, bold=False)

        metrics = QFontMetrics(self._font)
        width = metrics.horizontalAdvance(self.text)
        height = metrics.height()

        self._detail_height = 0
        if self.detail:
            detail_metrics = QFontMetrics(self._detail_font)
            width = max(width, detail_metrics.horizontalAdvance(self.detail))
            self._detail_height = detail_metrics.height() + self.GAP
            height += self._detail_height

        self._line_height = metrics.height()
        self.setFixedSize(width + self.PAD_X * 2, height + self.PAD_Y * 2)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        background = QColor(COLORS.DARK.BG)
        background.setAlpha(235)
        border = QColor(COLORS.DARK.BORDER.NORMAL)

        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(border)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, self.RADIUS, self.RADIUS)

        painter.setFont(self._font)
        painter.setPen(QColor(COLORS.DARK.TEXT.IMPORTANT))
        painter.drawText(
            QRectF(0, self.PAD_Y, self.width(), self._line_height),
            Qt.AlignmentFlag.AlignCenter, self.text)

        if not self.detail:
            return

        painter.setFont(self._detail_font)
        painter.setPen(QColor(COLORS.DARK.TEXT.MUTED))
        painter.drawText(
            QRectF(0, self.PAD_Y + self._line_height + self.GAP,
                   self.width(), self._detail_height),
            Qt.AlignmentFlag.AlignCenter, self.detail)


class Stage:
    """
    One thing on screen at a time, with a beginning and an end.

    A second request replaces the first rather than queueing behind it. Two
    flips asked for in quick succession are somebody asking again, and an
    answer that arrives four seconds after the question is not an answer.
    """

    LAYER = "DIALOG"

    # How long the answer stays up, for one thing on the stage.
    HOLD_BASE_MS = 2400
    # And for each one after that. A coin is one face and a word; forty dice
    # are a total and a breakdown line, and the same three seconds that is
    # generous for the first is not enough to find your own die in the
    # second. What is on the stage is what says how long it takes to read.
    HOLD_PER_ITEM_MS = 120
    # A ceiling, because past a point nobody is reading it die by die.
    HOLD_MAX_MS = 6500

    def __init__(self, client):
        self.client = client
        self.hold_base = self.HOLD_BASE_MS
        self.hold_per_item = self.HOLD_PER_ITEM_MS
        self.hold_max = self.HOLD_MAX_MS
        self._banner: Optional[Banner] = None
        self._content: Optional[QWidget] = None
        self._timer: Optional[QTimer] = None
        self._running = False

    # ── How long to hold it ──────────────────────────────────────────────────

    def configure_hold(self, base: int, per_item: int, maximum: int) -> None:
        """Read from settings by the caller, so a change takes effect at once."""
        self.hold_base = max(0, int(base))
        self.hold_per_item = max(0, int(per_item))
        self.hold_max = max(self.hold_base, int(maximum))

    def hold_for(self, items: int, bonus: int = 0) -> int:
        """
        How long the answer stays up for this many things.

        One rule for every kind of chance rather than a number per caller.
        `bonus` is added on top and is still subject to the ceiling, so a
        caller can ask for longer without being able to ask for forever.
        """
        extra = max(0, int(items) - 1) * self.hold_per_item
        return int(min(self.hold_max,
                       self.hold_base + extra + max(0, int(bonus))))

    # ── Geometry ─────────────────────────────────────────────────────────────

    def surface(self) -> tuple:
        """How big the screen is, as far as the overlay is concerned."""
        host = getattr(self.client, "OVERLAYS", None)
        if host is not None and host.width() > 1 and host.height() > 1:
            return host.width(), host.height()
        window = getattr(self.client, "window", None)
        if window is not None and window.width() > 1:
            return window.width(), window.height()
        return 1024, 600

    def content_size(self) -> int:
        """A sensible diameter for whatever is being drawn in the middle."""
        width, height = self.surface()
        return int(max(COIN_MIN, min(COIN_MAX, min(width, height) * COIN_FRACTION)))

    def _centre(self, widget: QWidget, y_fraction: float = 0.5) -> None:
        width, height = self.surface()
        # A widget can say where its own middle is. The coin's arc is headroom
        # above where it lands, so centring the widget would put the resting
        # coin half an arc below the middle of the screen.
        anchor = getattr(widget, "content_centre_y", None)
        offset = anchor() if callable(anchor) else widget.height() / 2
        widget.move(int((width - widget.width()) / 2),
                    int(height * y_fraction - offset))

    # ── Putting things up ────────────────────────────────────────────────────

    def _add(self, widget: QWidget) -> None:
        host = getattr(self.client, "OVERLAYS", None)
        if host is None:
            return
        host.add(self.LAYER, widget)

    def _drop(self, widget: Optional[QWidget]) -> None:
        if widget is None:
            return
        host = getattr(self.client, "OVERLAYS", None)
        try:
            if host is not None:
                host.remove(self.LAYER, widget)
            widget.hide()
            widget.deleteLater()
        except RuntimeError:
            # Already gone underneath us - a page rebuild takes its children
            # with it and the Python half outlives the C++ one.
            pass

    def _show_banner(self, text: str, size: int, detail: str = "") -> None:
        self._drop(self._banner)
        self._banner = None
        if not text:
            return
        self._banner = Banner(text, size, detail=detail)
        self._add(self._banner)
        self._centre(self._banner, BANNER_Y)

    def _after(self, milliseconds: int, then: Callable) -> None:
        """A phase change, cancellable by whatever comes next."""
        if self._timer is not None:
            self._timer.stop()
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(then)
        self._timer.start(max(0, int(milliseconds)))

    # ── The sequence ─────────────────────────────────────────────────────────

    def present(self, content: QWidget, start: Callable, result: str,
                detail: str = "", title: str = "", title_ms: int = 1400,
                items: int = 1, hold_bonus: int = 0, epilogue: str = "",
                epilogue_ms: int = 2600, on_result: Callable = None) -> None:
        """
        Title, then the thing, then the answer.

        `start` is handed a callable to invoke once whatever it is doing has
        settled - so the stage does not need to know how long a flip takes or
        what it is watching.

        `on_result` fires at the same moment the answer appears. That is what
        anything announcing the outcome should hang off: the result is known
        before the drawing starts, so saying it when it is DECIDED means the
        panel calls the flip while the coin is still in the air.

        `items` is how many things are being read - one coin, six dice - and
        decides how long the answer stays up. See `hold_for`.

        `hold_bonus` is for something that earns a longer look without having
        more to read: a wheel is one name whatever its slice count, and the
        moment worth having is everybody looking at the wheel it stopped on.
        """
        result_ms = self.hold_for(items, hold_bonus)
        self.dismiss()
        self._running = True

        self._content = content
        self._add(content)
        self._centre(content)
        content.hide()

        def run_it():
            if not self._running:
                return
            self._hold_idle()
            self._show_banner("", SIZES.M3)
            content.show()
            start(self._settled_with(result, detail, result_ms,
                                     epilogue, epilogue_ms, on_result))

        if title:
            self._hold_idle()
            self._show_banner(title, SIZES.M3)
            self._after(title_ms, run_it)
        else:
            run_it()

    def _settled_with(self, result: str, detail: str, result_ms: int,
                      epilogue: str = "", epilogue_ms: int = 2600,
                      on_result: Callable = None) -> Callable:
        """
        The answer, and then what it meant.

        An epilogue is shown after the result rather than instead of it. A
        rule like "over 15 and you may enter" is a reading OF the total, so
        replacing the total with it would hide the thing being read - and
        anybody watching a roll wants to see the number first.
        """
        def done():
            if not self._running:
                return
            self._hold_idle()
            self._show_banner(result, SIZES.L2, detail=detail)
            if callable(on_result):
                try:
                    on_result()
                except Exception as e:
                    self.client.log("warning",
                                    f"[RandomChance] Announcing the result "
                                    f"failed: {e}")
            if epilogue:
                self._after(result_ms, lambda: self._epilogue(epilogue,
                                                              epilogue_ms))
            else:
                self._after(result_ms, self.dismiss)
        return done

    def _epilogue(self, text: str, hold_ms: int) -> None:
        if not self._running:
            return
        self._hold_idle()
        self._show_banner(text, SIZES.L1)
        self._after(hold_ms, self.dismiss)

    def _hold_idle(self) -> None:
        """
        Keep the screensaver off the answer.

        Idle blocking is honoured for the dialog stack, for a page, and for a
        `Panel` - a bare widget in an overlay layer is invisible to all three.
        A voice-triggered flip produces no interaction at all, and the default
        interaction timeout is five seconds, so without this the panel can go
        idle over a coin that is still in the air. Resetting the clock at each
        phase is what `TimerService` already does when a timer fires.
        """
        try:
            self.client.reset_interaction_timeout()
        except Exception:
            pass

    def dismiss(self) -> None:
        """Take everything down. Safe to call when nothing is up."""
        self._running = False
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

        content, self._content = self._content, None
        if content is not None:
            stop = getattr(content, "stop", None)
            if callable(stop):
                stop()
        self._drop(content)

        self._drop(self._banner)
        self._banner = None
