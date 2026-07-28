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
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QBrush, QLinearGradient

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
    """

    WIDTH_RATIO = 0.42
    MIN_WIDTH   = 460
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

        super().__init__(client, width=width, edge="right",
                         key=f"__answer_{abs(hash(title)) % 10 ** 8}",
                         destroy_on_close=True)
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
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.add_content(body)

        seconds = self.TIMEOUT if timeout is None else timeout
        if seconds > 0:
            self._timeout_key = f"answer_{id(self)}"
            client.TIMEOUTS.add(seconds, self.close_panel, self._timeout_key)
            client.TIMEOUTS.start(self._timeout_key)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor(self.tint.red(), self.tint.green(),
                                        self.tint.blue(), 104))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 45))
        painter.fillRect(self.rect(), QBrush(gradient))
        painter.end()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self.close_panel()
