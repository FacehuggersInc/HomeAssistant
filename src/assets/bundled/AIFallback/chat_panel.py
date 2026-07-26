from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QTextBrowser, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QDesktopServices

from src.styling import make_font, SIZES, set_style

from .markdown import to_rich_text

if TYPE_CHECKING:
    from src.main import Client


class Bubble(QFrame):
    # One message. User messages are plain text; AI messages are markdown
    # rendered through QTextBrowser, which is the only Qt widget that handles
    # tables, <pre> blocks and clickable links together.

    def __init__(self, client: "Client", text: str, from_user: bool):
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

        if from_user:
            body = QLabel(text)
            body.setFont(make_font(SIZES.S2))
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            set_style(body, "common", "text-strong")
            layout.addWidget(body)
        else:
            layout.addWidget(self._rich(text))

    def _rich(self, markdown: str) -> QTextBrowser:
        view = QTextBrowser()
        view.setOpenExternalLinks(False)
        view.setOpenLinks(False)
        view.anchorClicked.connect(QDesktopServices.openUrl)
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setFont(make_font(SIZES.S2))
        set_style(view, "common", "transparent")
        view.setHtml(to_rich_text(markdown))

        # Grow to fit instead of scrolling: the panel scrolls, the bubble
        # should not have its own scrollbar inside it.
        def fit():
            # The Qt object can be gone by the time a queued timer fires - the
            # panel closing between scheduling and running is enough. Touching
            # a deleted widget raises RuntimeError and takes the thread down.
            try:
                document = view.document()
                document.setTextWidth(max(200, view.viewport().width()))
                view.setFixedHeight(int(document.size().height()) + 8)
            except RuntimeError:
                pass

        view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        QTimer.singleShot(0, fit)
        view._fit = fit
        return view

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for child in self.findChildren(QTextBrowser):
            if hasattr(child, "_fit"):
                try:
                    child._fit()
                except RuntimeError:
                    pass


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

    def add_message(self, text: str, from_user: bool) -> None:
        bubble = Bubble(self.client, text, from_user)
        self.messages.insertWidget(self.messages.count() - 1, bubble)
        QTimer.singleShot(0, self._scroll_to_end)

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
