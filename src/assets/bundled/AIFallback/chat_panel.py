from __future__ import annotations

import urllib.error
import urllib.request
from threading import Thread
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QTextBrowser, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, QRect
from PyQt6.QtGui import (
    QDesktopServices, QImage, QTextDocument, QPainter, QColor, QPen, QBrush,
    QTextCursor, QTextBlockFormat)

from src.styling import make_font, SIZES, set_style, get_style_sheet, style_scrollbar

from .markdown import to_rich_text

if TYPE_CHECKING:
    from src.main import Client

# Remote images referenced by a reply. Qt will not fetch these itself - a
# QTextBrowser only resolves local files and Qt resources - so an https image
# renders as a broken box until it is loaded and handed to the document.
IMAGE_TIMEOUT = 10
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_WIDTH = 560          # panel width less its padding
IMAGE_CACHE_LIMIT = 40

_IMAGE_CACHE: dict = {}
_IMAGE_PENDING: set = set()
_IMAGE_FAILED: set = set()


def format_tokens(prompt: int, completion: int) -> str:
    """One consistent rendering, so the per-message and session lines match."""
    return f"{prompt:,} in  ·  {completion:,} out  ·  {prompt + completion:,} total"


class _RichText(QTextBrowser):
    """
    A markdown body that grows to its content instead of scrolling.

    The panel scrolls; a bubble with a scrollbar inside it hides part of the
    reply and is close to unusable on a touch screen. Height therefore comes
    from the document - but the document's height depends on the width it
    wraps at, so it can only be measured once the widget has actually been
    laid out. The previous version measured from a QTimer.singleShot(0) that
    fired before the panel had its real width, sized the bubble for a wrap
    that never happened, and left long replies cut off.
    """

    def __init__(self, client: "Client", markdown: str,
                 ink: str = "#f0f0f4", align_right: bool = False):
        super().__init__()
        self.client = client
        self._html = to_rich_text(markdown)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.anchorClicked.connect(QDesktopServices.openUrl)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Code blocks and wide tables can exceed the panel. With both bars off
        # that content was clipped and unreachable; a bar appears only when
        # something genuinely cannot wrap.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFont(make_font(SIZES.M1))
        self.document().setDocumentMargin(0)
        # Colour as well as background. `transparent` sets the second and
        # leaves the first to the palette, which renders a reply in black on
        # a dark bubble - readable only by selecting it.
        self.setStyleSheet(
            f"background: transparent; border: none; color: {ink};")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setHtml(self._html)
        if align_right:
            # Set on the document rather than on the widget: a bubble filling
            # the row leaves a short question stranded at the far left of it,
            # which reads as the wrong side of the conversation.
            self.setAlignment(Qt.AlignmentFlag.AlignRight)
            cursor = self.textCursor()
            cursor.select(QTextCursor.SelectionType.Document)
            block = QTextBlockFormat()
            block.setAlignment(Qt.AlignmentFlag.AlignRight)
            cursor.mergeBlockFormat(block)
            cursor.clearSelection()
            self.setTextCursor(cursor)

    ## -- remote images

    def loadResource(self, resource_type, url):
        """
        Hand the document an image it cannot fetch for itself.

        QTextBrowser resolves local files and Qt resources only, so an https
        image in a reply draws as a broken box no matter how correct the
        Markdown was. Fetching happens off the UI thread; until it lands there
        is nothing to return.
        """
        try:
            if int(resource_type) == int(QTextDocument.ResourceType.ImageResource):
                key = url.toString()
                cached = _IMAGE_CACHE.get(key)
                if cached is not None:
                    return cached
                self._request_image(key)
                return None
        except Exception:
            pass
        return super().loadResource(resource_type, url)

    def _request_image(self, url: str) -> None:
        # http(s) only. A file: or data: URL from a model reply has no business
        # being read off this machine.
        if not url.lower().startswith(("http://", "https://")):
            return
        if url in _IMAGE_PENDING or url in _IMAGE_FAILED:
            # loadResource() is called again on every relayout, so without this
            # a single image would start a fetch per layout pass.
            return
        _IMAGE_PENDING.add(url)
        Thread(target=self._fetch_image, args=[url],
               name="__chat_image", daemon=True).start()

    def _fetch_image(self, url: str) -> None:
        data = b""
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "DesktopHomeAssistant"})
            with urllib.request.urlopen(request, timeout=IMAGE_TIMEOUT) as response:
                # One byte past the cap, so something oversized is rejected
                # rather than silently truncated into a corrupt image.
                data = response.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                data = b""
        except Exception:
            data = b""
        self.client.call_on_ui(lambda: self._image_arrived(url, data))

    def _image_arrived(self, url: str, data: bytes) -> None:
        _IMAGE_PENDING.discard(url)

        image = QImage()
        if not data or not image.loadFromData(data):
            # Remembered, so a dead link is not re-fetched on every relayout.
            _IMAGE_FAILED.add(url)
            return

        if image.width() > MAX_IMAGE_WIDTH:
            image = image.scaledToWidth(MAX_IMAGE_WIDTH,
                                        Qt.TransformationMode.SmoothTransformation)

        if len(_IMAGE_CACHE) >= IMAGE_CACHE_LIMIT:
            _IMAGE_CACHE.pop(next(iter(_IMAGE_CACHE)), None)
        _IMAGE_CACHE[url] = image

        try:
            # Rebuilt rather than nudged: the document already measured this
            # image as missing, and only a fresh parse asks for the resource
            # again.
            self.setHtml(self._html)
            self._fit()
        except RuntimeError:
            # Panel closed between the fetch finishing and this running.
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        try:
            width = self.viewport().width()
            if width <= 0:
                return
            document = self.document()
            document.setTextWidth(width)
            height = int(document.size().height()) + self.frameWidth() * 2
            # Anything that refuses to wrap adds a horizontal bar, and the bar
            # eats viewport height that the document does not know about.
            if document.idealWidth() > width + 1:
                height += self.horizontalScrollBar().sizeHint().height()
            if height != self.height():
                # Re-entrant: this triggers another resizeEvent, which measures
                # the same width and computes the same height, so it settles on
                # the second pass rather than looping.
                self.setFixedHeight(height)
        except RuntimeError:
            # The panel can close between a queued call and this running, and
            # touching a deleted Qt object raises rather than returning None.
            pass


#Colour tells the two apart, and the side says it again. Both are needed: a
#reply that fills most of the panel has little edge left to show which side it
#is on, so the fill has to carry it.
#
#Light ink on both. Dark text on a bright fill is legible on paper and harder
#work on a lit panel across a room, and a conversation where one side is read
#differently from the other is one side being shouted.
USER_INK   = "#f2fbf6"
#Deep green rather than bright: the words sit on it.
USER_FILL  = "rgba(16,41,30,242)"
USER_EDGE  = "rgba(47,240,142,120)"

#Warm neutral rather than blue-grey. Two cool greys either side of a cool
#backdrop is a screen with one colour on it.
AI_INK     = "#f4f1ee"
AI_FILL    = "rgba(38,35,33,242)"
AI_EDGE    = "rgba(255,236,214,44)"

#How much of the panel one message may take.
#
#Nearly all of it. The card fills the screen so that a reply has room, and a
#bubble holding it to three quarters gives that room back for nothing - the
#colour and the unrounded corner already say who is speaking.
BUBBLE_SHARE = 0.94


class Bubble(QFrame):
    """
    One message, on its own side.

    User messages sit right and the assistant's sit left, which is how every
    messaging application has said who is speaking for twenty years. There is
    no name on each line: the side and the colour already say it, and a label
    above every message is a third answer to a question nobody asked twice.
    """

    RADIUS = 18
    #The one corner that is not rounded, on the side the message came from.
    #A bubble rounded equally on all four reads as a card rather than as
    #something somebody said.
    TAIL = 5

    def __init__(self, client: "Client", text: str, from_user: bool, usage=None):
        super().__init__()
        self.client = client
        self.from_user = from_user
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        fill, edge, ink = ((USER_FILL, USER_EDGE, USER_INK) if from_user
                           else (AI_FILL, AI_EDGE, AI_INK))
        corners = (f"border-top-left-radius:{self.RADIUS}px;"
                   f"border-top-right-radius:{self.RADIUS}px;"
                   f"border-bottom-left-radius:"
                   f"{self.TAIL if from_user else self.RADIUS}px;"
                   f"border-bottom-right-radius:"
                   f"{self.RADIUS if from_user else self.TAIL}px;")
        self.setStyleSheet(
            f"QFrame {{ background:{fill}; border:1px solid {edge}; {corners} }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(6)

        # Both sides go through the same renderer, so a transcript is typeset
        # one way and anything Markdown in a question does not show as literal
        # asterisks.
        layout.addWidget(_RichText(client, text, ink=ink,
                                   align_right=from_user))

        # Only on the reply. The prompt count covers the system message and
        # the whole history as well as the question, so attaching it to the
        # user's line would read as the cost of that one sentence.
        if usage is not None and not from_user and usage.total:
            cost = QLabel(format_tokens(usage.prompt, usage.completion))
            cost.setFont(make_font(SIZES.S1))
            cost.setAlignment(Qt.AlignmentFlag.AlignRight)
            cost.setStyleSheet(
                "background: transparent; border: none;"
                "color: rgba(244,241,238,120);")
            layout.addWidget(cost)


class _Row(QWidget):
    """
    One message, filling the row it is given.

    Laid out by weight rather than by content. A bubble added beside a stretch
    is only as wide as the words in it, so a two-word answer is a stub and a
    long one still stops short of the edge - the panel fills the screen and
    the message inside it does not.

    The weights are the share and its remainder, so the message takes almost
    all of the row and the small piece left over is what puts it on its side.
    """

    def __init__(self, bubble: Bubble):
        super().__init__()
        set_style(self, "common", "transparent")
        bubble.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Preferred)

        line = QHBoxLayout(self)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(0)

        share = max(1, int(round(BUBBLE_SHARE * 100)))
        gap = max(1, 100 - share)
        if bubble.from_user:
            line.addStretch(gap)
            line.addWidget(bubble, stretch=share)
        else:
            line.addWidget(bubble, stretch=share)
            line.addStretch(gap)
        self.bubble = bubble


class StatusPill(QWidget):
    """
    What the assistant is doing, at the bottom of the conversation.

    The voice bar lives at the bottom of the SCREEN, which a full-screen card
    covers - so while a conversation was open the one thing saying whether it
    was listening was hidden behind it, and the panel said "Thinking…" in grey
    text in a corner instead.

    Here, in the same place and the same shape, because that is where somebody
    already looks for it.
    """

    #Colour, and what it says. Keyed by what the assistant reports.
    #
    #The colours are the voice bar's, deliberately: the same state should not
    #be two different colours depending on which pill you are looking at.
    STATES = {
        "SPEAKING":  ("#2ff08e", "Speaking  ·  say the wake word to interrupt"),
        "LISTENING": ("#da091f", "Listening…"),
        "THINKING":  ("#2d6cc0", "Thinking…"),
        "ACTING":    ("#1f7a4d", "Working…"),
        # Not the same as READY, and saying so matters more than it used to.
        # The assistant can now legitimately be off - a declined model
        # download, a missing package - and an unknown status fell through to
        # READY, so the panel invited somebody to say a wake word that
        # nothing was listening for.
        "DORMANT":   ("#8a8a8a", "The voice assistant is off"),
        "READY":     ("#8a8a8a", "Say the wake word to ask another"),
    }

    #Which states the dot breathes in. Anything else is a resting state and
    #the pill stops repainting entirely.
    ALIVE = ("LISTENING", "THINKING", "ACTING", "SPEAKING")

    HEIGHT = 46
    RADIUS = 23
    BORDER = 3
    DOT = 10
    #The animation frame rate, matching the voice bar's. It was 200ms, which
    #is both the poll AND the pulse - so the dot breathed at five frames a
    #second, and a state that came and went between two polls was never shown
    #at all. Transcribing is now under 300ms on this transcriber.
    TICK_MS = 33
    #The TTS probe is a call into the backend rather than an attribute read,
    #so it is asked at something nearer the old rate. Nothing depends on
    #noticing the exact moment speech ends.
    SPEAKING_EVERY = 5

    def __init__(self, client: "Client"):
        super().__init__()
        self.client = client
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self._state = "READY"
        self._override = ""
        self._pulse = 0.0
        self._speaking = False
        self._ticks = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self.TICK_MS)
        self._tick()

    ## -- state

    def set_message(self, text: str) -> None:
        """A message from the plugin, which wins while it is set."""
        text = str(text or "").strip()
        if text == self._override:
            return
        self._override = text
        self.update()

    def _is_speaking(self) -> bool:
        """
        Whether the panel is talking right now, asked occasionally.

        Its own method, and its own try. Both this and the status read used to
        sit inside ONE try that fell back to READY - so a backend that raised
        on `is_speaking()` did not merely lose the speaking state, it pinned
        the pill to "say the wake word" while the assistant was listening and
        thinking behind it. One failure, and the pill stops reporting
        anything at all, silently.
        """
        try:
            tts = getattr(self.client, "TTS", None)
            if tts is None or not getattr(tts, "available", False):
                return False
            return bool(tts.is_speaking())
        except Exception:
            return False

    def _reported(self) -> str:
        """What the assistant says it is doing."""
        try:
            reported = str(getattr(self.client, "ASSIST_STATUS", "") or "")
        except Exception:
            return "READY"
        if reported in self.STATES:
            return reported
        # LIVE is the ordinary idle state and has no entry of its own.
        return "READY"

    def _tick(self) -> None:
        self._ticks += 1
        if self._ticks % self.SPEAKING_EVERY == 0:
            self._speaking = self._is_speaking()

        state = "SPEAKING" if self._speaking else self._reported()

        changed = state != self._state
        self._state = state

        # Repainting is what costs, so it happens when there is something to
        # see: a state change, or a dot that is moving. A panel sitting at
        # READY used to repaint five times a second to draw the same pixels.
        if state in self.ALIVE:
            self._pulse = (self._pulse + 0.026) % 1.0
            self.update()
        elif changed:
            self._pulse = 0.0
            self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start(self.TICK_MS)
        self._tick()

    def hideEvent(self, event) -> None:
        # A pill nobody is looking at does not need a frame rate. This ticks
        # thirty times a second now, and the panel is closed far more of the
        # time than it is open.
        self._timer.stop()
        super().hideEvent(event)

    ## -- painting

    def paintEvent(self, event) -> None:
        colour, words = self.STATES.get(self._state, self.STATES["READY"])
        text = self._override or words

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        accent = QColor(colour)
        body = QRect(0, 0, self.width(), self.height())
        # A thicker edge in the state's own colour: the pill is read from
        # across a room, and a one-pixel line at 120 alpha is a shape rather
        # than a colour at that distance.
        painter.setPen(QPen(accent, self.BORDER))
        painter.setBrush(QBrush(QColor(18, 18, 22, 232)))
        inset = self.BORDER // 2 + 1
        painter.drawRoundedRect(body.adjusted(inset, inset, -inset, -inset),
                                self.RADIUS, self.RADIUS)

        # A dot that breathes while something is happening, and sits still
        # when nothing is. Motion is what says "working" from across a room.
        import math
        alive = self._state in self.ALIVE
        swell = (0.5 + 0.5 * math.sin(self._pulse * 2 * math.pi)) if alive else 0.0
        size = self.DOT + int(round(swell * 5))
        middle = self.height() // 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(accent))
        painter.drawEllipse(20 - size // 2, middle - size // 2, size, size)

        painter.setPen(QPen(QColor("#e9e6e3")))
        painter.setFont(make_font(SIZES.S3, bold=True))
        painter.drawText(QRect(38, 0, self.width() - 52, self.height()),
                         Qt.AlignmentFlag.AlignVCenter
                         | Qt.AlignmentFlag.AlignLeft, text)
        painter.end()


class ChatPanel(QWidget):
    """Scrolling conversation. Reused across turns rather than rebuilt."""

    def __init__(self, client: "Client"):
        super().__init__()
        self.client = client
        # Set before anything can connect a signal that reads them.
        self._follow = True
        self._adjusting = False
        set_style(self, "common", "transparent")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 20, 26, 20)
        outer.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)
        title = QLabel("Assistant")
        title.setFont(make_font(SIZES.L1, bold=True))
        set_style(title, "common", "text-strong")
        header.addWidget(title)
        header.addStretch()

        outer.addLayout(header)

        # Its own line rather than sharing the header row: the status beside it
        # flickers between "Thinking…" and empty on every turn, and a total
        # that shifted sideways each time would be hard to read.
        self.totals = QLabel("")
        self.totals.setFont(make_font(SIZES.S2))
        self.totals.setAlignment(Qt.AlignmentFlag.AlignRight)
        set_style(self.totals, "common", "text-muted")
        self.totals.hide()
        outer.addWidget(self.totals)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        style_scrollbar(self.scroll)

        self.inner = QWidget()
        set_style(self.inner, "common", "transparent")
        self.messages = QVBoxLayout(self.inner)
        self.messages.setContentsMargins(0, 0, 0, 0)
        # Room to breathe: a wall of bubbles a few pixels apart reads as one
        # block of text rather than as a conversation.
        self.messages.setSpacing(16)
        self.messages.addStretch()

        self.scroll.setWidget(self.inner)
        outer.addWidget(self.scroll, stretch=1)

        # Follow the bottom as the content GROWS, not once when a message is
        # added.
        #
        # `add_message` already scrolled to the maximum on the next event
        # loop turn, and that is too early: a bubble is a markdown body that
        # sizes itself to its text, so at that moment the new one is a few
        # pixels tall and the maximum it scrolls to is the old one. A long
        # reply then landed below the fold and stayed there, which on a
        # session of any length means the answer is never the thing on
        # screen.
        #
        # `rangeChanged` fires every time the content's height changes, which
        # is exactly when the bottom moves.
        bar = self.scroll.verticalScrollBar()
        bar.rangeChanged.connect(self._range_changed)
        bar.valueChanged.connect(self._value_changed)

        # At the bottom, where the voice bar would be if this card were not
        # covering it.
        self.status = StatusPill(client)
        outer.addWidget(self.status)

    ## -- content

    def add_message(self, text: str, from_user: bool, usage=None) -> None:
        row = _Row(Bubble(self.client, text, from_user, usage=usage))
        self.messages.insertWidget(self.messages.count() - 1, row)
        # A new turn is the reason this panel is on screen, so it is always
        # followed - even if somebody had scrolled up to reread something.
        # Growth of an existing bubble is what respects where they are.
        self._follow = True
        QTimer.singleShot(0, self._scroll_to_end)

    def set_totals(self, prompt: int, completion: int) -> None:
        """Running total for the conversation. Hidden until there is one."""
        if not (prompt or completion):
            self.totals.hide()
            return
        self.totals.setText(f"Session  ·  {format_tokens(prompt, completion)}")
        self.totals.show()

    def set_status(self, text: str) -> None:
        """A message from the plugin. Empty hands the pill back to the state."""
        self.status.set_message(text)

    #How far off the bottom still counts as being at the bottom. A scroll
    #area rarely lands exactly on its maximum, and a few pixels short is
    #somebody at the end of the conversation rather than somebody who has
    #scrolled away from it.
    STICK_SLACK = 24

    def _scroll_to_end(self) -> None:
        bar = self.scroll.verticalScrollBar()
        self._adjusting = True
        bar.setValue(bar.maximum())
        self._adjusting = False

    def _range_changed(self, _minimum: int, maximum: int) -> None:
        """The content grew or shrank. Stay at the bottom if that is where we were."""
        if not getattr(self, "_follow", True):
            return
        self._adjusting = True
        self.scroll.verticalScrollBar().setValue(maximum)
        self._adjusting = False

    def _value_changed(self, value: int) -> None:
        """
        Somebody scrolled. Remember whether they are still at the end.

        Ignored while this class is doing the scrolling, or every automatic
        jump to the bottom would be read as a deliberate one and the flag
        would only ever describe itself.
        """
        if getattr(self, "_adjusting", False):
            return
        bar = self.scroll.verticalScrollBar()
        self._follow = value >= bar.maximum() - self.STICK_SLACK

    def clear(self) -> None:
        while self.messages.count() > 1:
            item = self.messages.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
