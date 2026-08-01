"""
A saved page, as a tile.

One cell and no bigger. A bookmark is an icon and a word; stretched over four
cells it is the same icon with more space around it, which is not a better tile
- it is a worse dashboard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout

from src.styling import make_font, set_style, add_text_shadow, SIZES
from src.ui.widgets.tile import Tile

if TYPE_CHECKING:
    from src.main import Client


class BookmarkTile(Tile):
    """One bookmark, one square."""

    KEY  = "bookmark_tile"
    NAME = "Bookmark"
    ICON = "mdi.bookmark"

    MIN_GRID_W, MIN_GRID_H = 1, 1
    MAX_GRID_W, MAX_GRID_H = 1, 1

    def __init__(self, client: "Client", grid_w: int = 1, grid_h: int = 1,
                 url: str = "", **_ignored):
        # Set BEFORE super(), which calls build_variants() -> _build() while
        # this constructor's body has not run. A label reading an attribute
        # assigned below would find nothing there.
        self.url = str(url or "").strip()
        self._icon_label: Optional[QLabel] = None
        self._name_label: Optional[QLabel] = None

        # One cell whatever it is asked for: the grid can hand back a
        # remembered span from before the limit existed.
        super().__init__(client, grid_w=1, grid_h=1, bg_color="#1b1b22")
        self.on_click = self._open

    ## -- content

    def build_variants(self) -> None:
        self.add_variant(1, 1, self._build)

    def _build(self) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(6, 10, 6, 8)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon_label = QLabel()
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setFixedHeight(44)
        set_style(self._icon_label, "common", "transparent")
        layout.addWidget(self._icon_label)

        self._name_label = QLabel("")
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_label.setWordWrap(True)
        self._name_label.setFont(make_font(SIZES.S1, bold=True))
        self._name_label.setStyleSheet("color:#e8ecf4;background:transparent;")
        add_text_shadow(self._name_label, blur=8)
        layout.addWidget(self._name_label)

        self.refresh()
        return host

    ## -- state

    def refresh(self) -> None:
        """Read what the store currently says, and draw it."""
        if self._name_label is None or self._icon_label is None:
            return

        mark = None
        if self.url:
            try:
                mark = self.client.BOOKMARKS.get(self.url)
            except Exception:
                mark = None

        if not self.url:
            label, initial = "Choose", "+"
        elif mark is None:
            # The address was removed from the store while this stayed on the
            # grid. Says so rather than showing an empty square.
            label, initial = "Missing", "?"
        else:
            label, initial = mark.label, mark.initial

        pixmap = None
        if mark is not None:
            try:
                path = self.client.BOOKMARKS.icon_path(mark)
                if path is not None:
                    candidate = QPixmap(str(path))
                    if not candidate.isNull():
                        pixmap = candidate.scaled(
                            QSize(40, 40), Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
            except Exception:
                pixmap = None

        try:
            self._name_label.setText(label)
            if pixmap is not None:
                self._icon_label.setPixmap(pixmap)
                self._icon_label.setText("")
            else:
                # A letter, when there is no picture. Better than a generic
                # globe: it differs between bookmarks, which is the job.
                self._icon_label.setPixmap(QPixmap())
                letter = make_font(SIZES.M1, bold=True)
                letter.setPixelSize(26)
                self._icon_label.setFont(letter)
                self._icon_label.setStyleSheet(
                    "color:#2ff08e;background:transparent;")
                self._icon_label.setText(initial)
        except RuntimeError:
            pass

    def set_url(self, url: str) -> None:
        self.url = str(url or "").strip()
        self.refresh()
        # Written now, not on the next drag. Choosing the address IS the
        # change worth keeping, and nothing else on this tile triggers a save.
        self.request_save()

    def tile_state(self) -> dict:
        """Read by TileGrid.save_positions and merged into this tile's entry."""
        return {"url": self.url}

    def apply_tile_state(self, state: dict) -> None:
        if isinstance(state, dict) and state.get("url"):
            self.url = str(state.get("url"))
            self.refresh()

    ## -- pressing

    def _open(self) -> None:
        """
        Open it, locked to that site.

        Locked because a bookmark is a destination rather than a way into the
        internet: a mis-tapped link on a wall panel should not end somewhere
        nobody chose, and the address bar is the way out for anybody who meant
        to go further.
        """
        if not self.url:
            self.client.choose_bookmark(self.set_url)
            return

        from urllib.parse import urlparse
        base = ""
        try:
            parts = urlparse(self.url)
            if parts.scheme and parts.netloc:
                base = f"{parts.scheme}://{parts.netloc}"
        except Exception:
            base = ""

        self.client.goto("#webpage", data={
            "url": self.url, "lock_base": base, "lock_address": True,
        }, override=True)
