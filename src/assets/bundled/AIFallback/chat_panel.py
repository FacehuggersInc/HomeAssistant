from __future__ import annotations

import urllib.error
import urllib.request
from threading import Thread
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QTextBrowser, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QDesktopServices, QImage, QTextDocument

from src.styling import make_font, SIZES, set_style

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

    def __init__(self, client: "Client", markdown: str):
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
        self.setFont(make_font(SIZES.S2))
        self.document().setDocumentMargin(0)
        set_style(self, "common", "transparent")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setHtml(self._html)

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


class Bubble(QFrame):
    # One message. User messages are plain text; AI messages are markdown
    # rendered through QTextBrowser, which is the only Qt widget that handles
    # tables, <pre> blocks and clickable links together.

    def __init__(self, client: "Client", text: str, from_user: bool, usage=None):
        super().__init__()
        self.client = client
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(self, "settings", "chat-bubble-user" if from_user else "chat-bubble-ai")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        who = QLabel("You" if from_user else "Assistant")
        who.setFont(make_font(SIZES.S1, bold=True))
        set_style(who, "common", "text-muted")
        layout.addWidget(who)

        # Both sides go through the same renderer. The question was a plain
        # QLabel before, which meant the two halves of the conversation were
        # typeset differently and anything Markdown in a transcript showed as
        # literal asterisks.
        layout.addWidget(_RichText(client, text))

        # Only on the reply. The prompt count covers the system message and
        # the whole history as well as the question, so attaching it to the
        # user's line would read as the cost of that one sentence.
        if usage is not None and not from_user and usage.total:
            cost = QLabel(format_tokens(usage.prompt, usage.completion))
            cost.setFont(make_font(SIZES.S1))
            cost.setAlignment(Qt.AlignmentFlag.AlignRight)
            set_style(cost, "common", "text-muted")
            layout.addWidget(cost)


class ChatPanel(QWidget):
    """Scrolling conversation. Reused across turns rather than rebuilt."""

    def __init__(self, client: "Client"):
        super().__init__()
        self.client = client
        set_style(self, "common", "transparent")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Assistant")
        title.setFont(make_font(SIZES.M1, bold=True))
        set_style(title, "common", "text-strong")
        header.addWidget(title)
        header.addStretch()

        self.status = QLabel("")
        self.status.setFont(make_font(SIZES.S1))
        set_style(self.status, "common", "text-muted")
        header.addWidget(self.status)
        outer.addLayout(header)

        # Its own line rather than sharing the header row: the status beside it
        # flickers between "Thinking…" and empty on every turn, and a total
        # that shifted sideways each time would be hard to read.
        self.totals = QLabel("")
        self.totals.setFont(make_font(SIZES.S1))
        self.totals.setAlignment(Qt.AlignmentFlag.AlignRight)
        set_style(self.totals, "common", "text-muted")
        self.totals.hide()
        outer.addWidget(self.totals)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        set_style(self.scroll, "common", "transparent")

        self.inner = QWidget()
        set_style(self.inner, "common", "transparent")
        self.messages = QVBoxLayout(self.inner)
        self.messages.setContentsMargins(0, 0, 0, 0)
        self.messages.setSpacing(10)
        self.messages.addStretch()

        self.scroll.setWidget(self.inner)
        outer.addWidget(self.scroll, stretch=1)

    ## -- content

    def add_message(self, text: str, from_user: bool, usage=None) -> None:
        bubble = Bubble(self.client, text, from_user, usage=usage)
        self.messages.insertWidget(self.messages.count() - 1, bubble)
        QTimer.singleShot(0, self._scroll_to_end)

    def set_totals(self, prompt: int, completion: int) -> None:
        """Running total for the conversation. Hidden until there is one."""
        if not (prompt or completion):
            self.totals.hide()
            return
        self.totals.setText(f"Session  ·  {format_tokens(prompt, completion)}")
        self.totals.show()

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def _scroll_to_end(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear(self) -> None:
        while self.messages.count() > 1:
            item = self.messages.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
