"""
A panel for a spoken answer.

A notification is a line that has often gone by the time anyone looks up, and
it is the wrong shape for an answer with several parts to it. This is what a
skill uses when it has something to show rather than something to report.

Client-level, because two plugins already need it and a third will.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QBrush, QLinearGradient, QPainterPath

from src.ui.overlays import Panel
from src.ui.icons import icon
from src.styling import make_font, SIZES, set_style, add_text_shadow

if TYPE_CHECKING:
    from src.main import Client


class AnswerPanel(Panel):
    """
    Icon, headline, and however many detail lines the answer has.

    Closes itself, because an answer nobody dismissed should not still be
    there an hour later - and closes on a tap, because sometimes it should go
    sooner than that.

    Only one is ever up. A second answer displaces the first rather than
    landing on it - see `_displace_others`.
    """

    # A card, not a full-height drawer. edge="right" with no height fills the
    # cross axis, which gave a screen-tall slab holding two lines of text.
    WIDTH_RATIO = 0.30
    MIN_WIDTH   = 380
    MAX_WIDTH   = 620
    MIN_HEIGHT  = 140
    MAX_RATIO   = 0.55        # of the screen height, for a long answer
    MARGIN      = 22          # non-zero, so it floats as a card
    TIMEOUT     = 30

    def __init__(self, client: "Client", icon_name: str, title: str,
                 lines: list = None, tint: str = "#4f9de0",
                 timeout: int = None):
        width = self.MIN_WIDTH
        try:
            host = client.OVERLAYS
            if host is not None and host.width() > 0:
                width = max(self.MIN_WIDTH, int(host.width() * self.WIDTH_RATIO))
        except Exception:
            pass

        width = min(width, self.MAX_WIDTH)

        # uuid, not hash(): hash() is randomised per process and two answers
        # with different titles can collide inside one run.
        super().__init__(client, width=width, edge="right",
                         key=f"__answer_{client.uuid()}",
                         margin=self.MARGIN, destroy_on_close=True)
        self.tint = QColor(tint)

        body = QWidget()
        set_style(body, "common", "transparent")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(14)

        glyph = QLabel()
        try:
            glyph.setPixmap(icon(icon_name, color="#ffffff").pixmap(48, 48))
        except Exception:
            pass
        glyph.setFixedWidth(54)
        glyph.setAlignment(Qt.AlignmentFlag.AlignTop)
        head.addWidget(glyph)

        headline = QLabel(title)
        headline.setFont(make_font(SIZES.M2, bold=True))
        headline.setWordWrap(True)
        headline.setStyleSheet("color: #ffffff; background: transparent;")
        add_text_shadow(headline, blur=14)
        head.addWidget(headline, stretch=1)
        layout.addLayout(head)

        for line in (lines or []):
            if not line:
                continue
            label = QLabel(str(line))
            label.setFont(make_font(SIZES.S3))
            label.setWordWrap(True)
            label.setStyleSheet("color: rgba(255,255,255,225); background: transparent;")
            add_text_shadow(label, blur=8)
            layout.addWidget(label)

        layout.addStretch()
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.add_content(body)

        # Every tap inside lands on the panel rather than on a label, so
        # tap-anywhere-to-dismiss is reliable. A QLabel ignores mouse events
        # and they propagate, but a drop shadow effect and the stretch area
        # made that less dependable than simply not accepting them at all.
        for child in body.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        body.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._fit_to_content(body)

        seconds = self.TIMEOUT if timeout is None else timeout
        if seconds > 0:
            # A uuid, not id(self). CPython reuses addresses, so a new panel
            # landing where a freed one was inherited its registration - and
            # with it a callback pointing at the deleted panel.
            self._timeout_key = f"answer:{client.uuid()}"
            # idle: an answer left on screen while somebody reads a dialog
            # over it has not been ignored.
            client.TIMEOUTS.add(seconds, self.close_panel, self._timeout_key,
                                idle=True,
                                transient=True)
            client.TIMEOUTS.start(self._timeout_key)

    def open_panel(self) -> None:
        """
        Go up, and take down whatever answer was up already.

        After the open rather than before it. Displacing runs the other
        panel's `on_closed`, which belongs to whoever put it up and is
        allowed to answer back - and a hook that does needs to see THIS panel
        as the one on screen, or the answer it raises will not displace it
        and both end up open. Being open first is what makes that true.
        """
        super().open_panel()
        if self.open:
            self._displace_others()

    def _displace_others(self) -> None:
        """
        Take down any answer already up, now that this one is.

        Every answer is the same card in the same corner, so a second one
        does not sit beside the first - it lands on top of it, and what shows
        is whichever edges of the older card the newer one fails to cover.
        Asking two things in a row is ordinary rather than a race: each
        answer stands for thirty seconds and nothing about the panel suggests
        waiting.

        Here rather than in `client.answer()` because the rule is about
        answers and not about the one method that happens to make them.
        Anything opening an AnswerPanel gets it.

        **Answers only.** A conversation panel, a notification centre or
        anything else on the overlay is a different thing in a different
        place, and an answer arriving is no reason to take it away.
        """
        host = getattr(self.client, "OVERLAYS", None)
        if host is None:
            return

        try:
            # Listed before any of them is closed. Each close runs a hook
            # that can open and close panels itself, and walking the overlay's
            # children while that happens is walking a list being edited.
            #
            # `open` is the test rather than existence: one already sliding
            # out is leaving, and closing it again takes the destroy path
            # from underneath its own animation.
            others = [panel for panel in host.findChildren(AnswerPanel)
                      if panel is not self and panel.open]
        except RuntimeError:
            # The overlay went while this was being built.
            return

        for panel in others:
            try:
                panel.close_panel()
            except RuntimeError:
                # Its C++ half has already gone; there is nothing to close.
                continue
            except Exception as e:
                self.client.log("warning",
                                f"[AnswerPanel] Could not displace "
                                f"{panel.key}: {e}")

    def _fit_to_content(self, body: QWidget) -> None:
        """
        Height from what is actually in it, capped to the screen.

        Without this the panel is as tall as the display whatever it holds,
        and an answer of two lines reads as a wall.
        """
        try:
            wanted = body.sizeHint().height() + 8
            ceiling = self.MIN_HEIGHT
            host = self.client.OVERLAYS
            if host is not None and host.height() > 0:
                ceiling = max(self.MIN_HEIGHT, int(host.height() * self.MAX_RATIO))
            self.panel_height = max(self.MIN_HEIGHT, min(wanted, ceiling))
            self._sync_geometry()
        except Exception as e:
            self.client.log("debug", f"[AnswerPanel] Could not fit to content: {e}")

    def close_panel(self, destroy: bool = None) -> None:
        key = getattr(self, "_timeout_key", "")
        if key:
            self.client.TIMEOUTS.discard(key)

        # Whatever put the answer up gets told it has gone, however it went -
        # a tap beside it, the timeout, or a tap on the card. A caller that
        # only hears about one of those has to guess about the others.
        hook, self.on_closed = getattr(self, "on_closed", None), None
        if callable(hook):
            try:
                hook()
            except Exception as e:
                self.client.log("warning",
                                f"[AnswerPanel] on_closed failed: {e}")
        super().close_panel(destroy)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor(self.tint.red(), self.tint.green(),
                                        self.tint.blue(), 104))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 45))

        # Into the rounded shape, not the rectangle.
        #
        # fillRect() paints the corners the stylesheet had rounded off, so the
        # panel had square edges with a rounded outline underneath doing
        # nothing. The radius matches the one in the sheet.
        # The panel's own radius, not a second copy of the number: Panel takes
        # it as an argument, so reading it back is the only way the two cannot
        # drift apart.
        radius = float(getattr(self, "_border_radius", 8) or 0)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), radius, radius)
        painter.fillPath(path, QBrush(gradient))
        painter.end()

    def mousePressEvent(self, event) -> None:
        # Accepted here so the release below is delivered to this widget. A
        # press that propagates away takes its release with it, which is what
        # made tapping an answer to dismiss it unreliable.
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        event.accept()
        self.close_panel()
