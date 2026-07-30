"""
One row of the history, as something to press.

Asking for a song by name works until the name is the hard part - a title in
another script, an artist a speech engine mangles every time. Once something
has played once, pressing it is easier than saying it again.

The list itself lives in `history.py`, which has no Qt import so it can be read
and tested without a display.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QPixmap, QPainterPath
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel

from src.styling import set_style, make_font, SIZES

if TYPE_CHECKING:
    from src.main import Client


class HistoryCard(QWidget):
    """One row: a cover, a title, an artist. The whole row is the target."""

    ART = 52
    HEIGHT = 68
    DRAG_SLOP = 14

    def __init__(self, client: "Client", entry: dict, on_pressed):
        super().__init__()
        self.client = client
        self.entry = entry
        self.on_pressed = on_pressed
        self._press = None
        self._down = False
        self._art: QPixmap = None

        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 12, 8)
        row.setSpacing(12)

        self._cover = QLabel()
        self._cover.setFixedSize(self.ART, self.ART)
        self._cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self._cover, "player", "cover")
        row.addWidget(self._cover)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)

        title = QLabel(self._elide(entry.get("title") or "Unknown", 42))
        title.setFont(make_font(SIZES.S2, bold=True))
        set_style(title, "common", "text-strong")
        column.addWidget(title)

        artist = QLabel(self._elide(entry.get("artist") or "", 44))
        artist.setFont(make_font(SIZES.S1))
        set_style(artist, "common", "text-muted")
        artist.setVisible(bool(entry.get("artist")))
        column.addWidget(artist)

        column.addStretch()
        row.addLayout(column, 1)

        # Mouse-transparent, so a press anywhere on the row reaches the row.
        for child in (self._cover, title, artist):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                               True)

        self._load_art(entry.get("art_url") or "")

    @staticmethod
    def _elide(text: str, limit: int) -> str:
        text = " ".join(str(text or "").split())
        return text if len(text) <= limit else text[:limit - 1] + "\u2026"

    ## -- input

    def mousePressEvent(self, event) -> None:
        self._press = event.globalPosition().toPoint()
        self._down = True
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        start, self._press = self._press, None
        was_down, self._down = self._down, False
        self.update()
        if not was_down:
            return
        if start is not None:
            moved = event.globalPosition().toPoint() - start
            if max(abs(moved.x()), abs(moved.y())) > self.DRAG_SLOP:
                return      # a scroll of the list, not a tap on a row
        self.on_pressed(self.entry)

    ## -- painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 10, 10)
        painter.fillPath(path, QColor(255, 255, 255,
                                      34 if self._down else 14))
        painter.end()

    def _load_art(self, url: str) -> None:
        if not url:
            return
        from threading import Thread

        def work():
            data = None
            try:
                import urllib.request
                if url.startswith("file://"):
                    from urllib.parse import unquote, urlsplit
                    with open(unquote(urlsplit(url).path), "rb") as handle:
                        data = handle.read(4 * 1024 * 1024)
                else:
                    request = urllib.request.Request(
                        url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(request, timeout=8) as response:
                        data = response.read(4 * 1024 * 1024)
            except Exception:
                return
            if data:
                self.client.call_on_ui(lambda: self._apply(data))

        Thread(target=work, name="__history_art", daemon=True).start()

    def _apply(self, data: bytes) -> None:
        try:
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                return
            size = self.ART
            scaled = pixmap.scaled(size, size,
                                   Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)
            if scaled.width() > size or scaled.height() > size:
                scaled = scaled.copy((scaled.width() - size) // 2,
                                     (scaled.height() - size) // 2, size, size)

            rounded = QPixmap(size, size)
            rounded.fill(Qt.GlobalColor.transparent)
            painter = QPainter(rounded)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(0, 0, size, size), 7, 7)
            painter.setClipPath(clip)
            painter.drawPixmap(0, 0, scaled)
            painter.end()

            self._art = rounded
            self._cover.setPixmap(rounded)
        except RuntimeError:
            pass      # the panel went while this was in flight
