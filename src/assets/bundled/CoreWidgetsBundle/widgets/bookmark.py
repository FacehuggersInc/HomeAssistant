"""
A saved page, on the home screen.

The icon and the label are the whole point: a row of identical buttons saying
"Bookmark" would be a list somebody has to read, and this is meant to be
recognised from across a room and pressed without thinking.

Every bookmark the client holds is available to this. The store is the client's
- see `src/bookmarks.py` - so a widget placed here survives this plugin being
unloaded, and the same address bookmarked from the toolbar shows up without
this needing to know it happened.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen, QFont

from src.styling import make_font, SIZES
from src.ui.widget import Widget

if TYPE_CHECKING:
    from src.main import Client


class BookmarkWidget(Widget):
    """One saved address, as a picture and a name."""

    KEY         = "bookmark"
    NAME        = "Bookmark"
    ICON        = "mdi.bookmark"
    DESCRIPTION = "A saved web page, opened locked to that site."

    RESIZABLE = True
    KEEP_ASPECT = False
    ROTATABLE = False
    FLOATABLE = True
    REMOVABLE = True
    MULTIPLE  = True        # each Add is another bookmark

    MIN_W, MIN_H = 96, 96
    MAX_W, MAX_H = 400, 400
    DEFAULT_ANCHOR = "bottom-right"

    #Most of the widget is the picture.
    #
    #A favicon at 46% of a 140px square is 64px with a name under it, which is
    #a label with a decoration. The icon is the thing that says where this
    #goes; the name is there for the one somebody has not met before.
    ICON_SHARE = 0.70

    def __init__(self, client: "Client", url: str = "", **_ignored):
        super().__init__(client)
        self.url = str(url or "").strip()
        self._pixmap: Optional[QPixmap] = None
        self._label = ""
        self._initial = "?"
        self.set_content_size(160, 160)
        self.refresh()

    ## -- content

    def refresh(self) -> None:
        """Read what the store currently says about this address."""
        self._pixmap = None
        self._label = ""
        self._initial = "?"

        if not self.url:
            # Placed with nothing chosen yet. Drawn as an invitation rather
            # than as an error - it is one tap from being useful.
            self._label = "Choose a bookmark"
            self._initial = "+"
            self.update()
            return

        try:
            bookmark = self.client.BOOKMARKS.get(self.url)
        except Exception:
            bookmark = None

        if bookmark is None:
            # The address was removed from the store while this stayed on the
            # page. Says so rather than drawing an empty square.
            self._label = "Missing"
            self._initial = "?"
            self.update()
            return

        self._label = bookmark.label
        self._initial = bookmark.initial
        try:
            path = self.client.BOOKMARKS.icon_path(bookmark)
            if path is not None:
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    self._pixmap = pixmap
        except Exception:
            self._pixmap = None
        self.update()

    def set_url(self, url: str) -> None:
        self.url = str(url or "").strip()
        self.refresh()
        # Written now, not at the next thing that happens to save. Choosing a
        # bookmark is the whole interaction; losing it to a restart in between
        # would be the interaction failing.
        try:
            framework = self.parent()
            if framework is not None and hasattr(framework, "save_layout"):
                framework.save_layout()
        except Exception:
            pass

    ## -- state

    def layout_state(self) -> dict:
        state = super().layout_state()
        # Which address this is, so a bookmark comes back as itself rather
        # than as an empty frame asking to be chosen again.
        state["url"] = self.url
        return state

    def apply_layout_state(self, state: dict) -> None:
        super().apply_layout_state(state)
        if isinstance(state, dict) and state.get("url"):
            self.url = str(state.get("url"))
            self.refresh()

    @classmethod
    def choose_before_add(cls, client, then) -> None:
        """
        Asked before the panel adds one: which bookmark?

        Deferred the same way the sticker picker is - the widget is not built
        until `then(**kwargs)` runs, so cancelling leaves nothing behind
        rather than placing an empty frame somebody then has to remove.
        """
        client.choose_bookmark(lambda url: then(url=url))

    ## -- painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width, height = self.width(), self.height()
        radius = max(12, int(min(width, height) * 0.16))

        painter.setPen(QPen(QColor("#33333b"), 1))
        painter.setBrush(QColor(26, 26, 30, 230))
        painter.drawRoundedRect(0, 0, width - 1, height - 1, radius, radius)

        side = int(min(width, height) * self.ICON_SHARE)
        x = (width - side) // 2
        y = int(height * 0.10)

        if self._pixmap is not None:
            scaled = self._pixmap.scaled(
                QSize(side, side), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap(x + (side - scaled.width()) // 2,
                               y + (side - scaled.height()) // 2, scaled)
        else:
            # A letter, when there is no picture. Better than a generic globe:
            # it differs between bookmarks, which is the job.
            painter.setBrush(QColor("#26262b"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(x, y, side, side, side // 5, side // 5)
            letter = QFont(make_font(SIZES.M1, bold=True))
            letter.setPixelSize(max(14, int(side * 0.52)))
            painter.setFont(letter)
            painter.setPen(QColor("#2ff08e"))
            painter.drawText(x, y, side, side,
                             Qt.AlignmentFlag.AlignCenter, self._initial)

        painter.setPen(QColor("#e8ecf4"))
        name = make_font(SIZES.S1, bold=True)
        name.setPixelSize(max(9, int(min(width, height) * 0.095)))
        painter.setFont(name)
        painter.drawText(6, y + side + 6, width - 12,
                         height - (y + side) - 10,
                         Qt.AlignmentFlag.AlignHCenter
                         | Qt.AlignmentFlag.AlignTop
                         | Qt.TextFlag.TextWordWrap,
                         self._label)
        painter.end()

    ## -- pressing

    def on_activate(self) -> None:
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
        self.client.goto("#webpage", data={
            "url": self.url,
            "lock_base": self._lock_base(),
            "lock_address": True,
        }, override=True)

    def _lock_base(self) -> str:
        """The site this bookmark belongs to, as a prefix to stay within."""
        try:
            from urllib.parse import urlparse
            parts = urlparse(self.url)
            if parts.scheme and parts.netloc:
                return f"{parts.scheme}://{parts.netloc}"
        except Exception:
            pass
        return ""
