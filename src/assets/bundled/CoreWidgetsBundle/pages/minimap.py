from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QLabel
from PyQt6.QtCore import Qt, QPoint, QMimeData, QTimer
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QDrag, QPixmap

from src.ui.icons import Icons, icon
from src.ui.overlays import BaseDialog
from src.styling import make_font, SIZES, set_style

if TYPE_CHECKING:
    from src.main import Client


# One colour per slot, picked by coordinate rather than by page, so a page
# keeps the colour of the position it is in. That is what makes the map
# readable while pages are being moved around it.
PALETTE = [
    "#2f8f6a", "#2f6a8f", "#8f5f2f", "#6a2f8f",
    "#8f2f4f", "#2f8f8f", "#8f8f2f", "#4f4f8f",
]


# The origin, empty. Not one of the slot colours - it is a problem, not a place.
ORIGIN_EMPTY = QColor("#e08a8a")


def colour_for(coord: tuple) -> QColor:
    index = (coord[0] * 3 + coord[1] * 5) % len(PALETTE)
    return QColor(PALETTE[index])


class MiniPage(QWidget):
    """One cell in the map: a page, or an empty slot a page can be moved into."""

    SIZE = 76

    def __init__(self, dialog: "MinimapDialog", coord: tuple,
                 page=None, is_current: bool = False,
                 cell: tuple = None):
        super().__init__()
        self.dialog     = dialog
        self.coord      = coord
        self.page       = page
        self.is_current = is_current

        width, height = cell or (self.SIZE, int(self.SIZE * 0.62))
        self.setFixedSize(int(width), int(height))
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._press: Optional[QPoint] = None
        self._hovered_by_drag = False

    ## -- painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)

        is_origin = self.coord == (0, 0)

        if self.page is None:
            # A dead slot. Outline only - it has to read as "somewhere a page
            # could go" rather than as a page that is somehow blank. The origin
            # gets its own colour, because an empty one is not a free slot but
            # a state the dialog will not let you leave.
            if self._hovered_by_drag:
                edge, fill = QColor("#7ed6a6"), QColor(255, 255, 255, 28)
            elif is_origin:
                edge, fill = ORIGIN_EMPTY, QColor(224, 138, 138, 30)
            else:
                edge, fill = QColor(255, 255, 255, 60), None

            painter.setBrush(QBrush(fill) if fill else Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(edge, 2, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(rect, 8, 8)
            self._draw_glyph(painter, rect, edge, origin=is_origin)
            return

        fill = colour_for(self.coord)
        painter.setBrush(QBrush(fill))
        if self._hovered_by_drag:
            painter.setPen(QPen(QColor("#7ed6a6"), 3))
        else:
            painter.setPen(QPen(QColor("#f2f2f2") if self.is_current
                                else QColor(255, 255, 255, 70),
                                3 if self.is_current else 1))
        painter.drawRoundedRect(rect, 8, 8)

        # One short word. A mini page is an position on a map, not a label -
        # anything longer wraps into two illegible lines at this size.
        if is_origin:
            # The origin is marked rather than named - it is the one slot whose
            # identity matters more than whichever page happens to be in it.
            self._draw_glyph(painter, rect, QColor(255, 255, 255, 235), origin=True)
            return

        painter.setPen(QPen(QColor(255, 255, 255, 235)))
        painter.setFont(make_font(SIZES.S1, bold=self.is_current))
        painter.drawText(rect.adjusted(3, 0, -3, 0),
                         int(Qt.AlignmentFlag.AlignCenter), self.label())

    def _draw_glyph(self, painter, rect, colour, origin: bool) -> None:
        if not origin:
            painter.setPen(QPen(colour))
            painter.setFont(make_font(SIZES.S1))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), "+")
            return
        size = max(14, min(rect.width(), rect.height()) // 2)
        try:
            pixmap = icon(Icons.HOME, color=colour.name()).pixmap(size, size)
            painter.drawPixmap(rect.center().x() - size // 2,
                               rect.center().y() - size // 2, pixmap)
        except Exception:
            painter.setPen(QPen(colour))
            painter.setFont(make_font(SIZES.S1, bold=True))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), "\u2302")

    def label(self) -> str:
        name = (getattr(self.page, "name", "") or "").replace("sub.", "")
        return name.replace("_", " ").split(".")[-1].title()[:9]

    ## -- interaction

    def mousePressEvent(self, event) -> None:
        self._press = event.position().toPoint()

    def mouseMoveEvent(self, event) -> None:
        if self._press is None or self.page is None:
            return
        if (event.position().toPoint() - self._press).manhattanLength() < 12:
            return

        drag = QDrag(self)
        data = QMimeData()
        data.setText(f"{self.coord[0]},{self.coord[1]}")
        drag.setMimeData(data)

        shot = QPixmap(self.size())
        shot.fill(Qt.GlobalColor.transparent)
        self.render(shot)
        drag.setPixmap(shot)
        drag.setHotSpot(self._press)
        self._press = None
        drag.exec(Qt.DropAction.MoveAction)

    def mouseReleaseEvent(self, event) -> None:
        if self._press is None or self.page is None:
            return
        self._press = None
        self.dialog.go_to(self.coord)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasText():
            event.acceptProposedAction()
            self._hovered_by_drag = True
            self.update()

    def dragMoveEvent(self, event) -> None:
        # Accepting the enter is not enough on its own - without this, every
        # move inside the cell falls through to the default handler and the
        # drop never arrives, which reads as the drag silently doing nothing.
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._hovered_by_drag = False
        self.update()

    def dropEvent(self, event) -> None:
        try:
            x, y = event.mimeData().text().split(",")
            source = (int(x), int(y))
        except (ValueError, AttributeError):
            return
        event.acceptProposedAction()
        self._hovered_by_drag = False
        self.update()
        if source == self.coord:
            return
        # Queued, not called here. rearrange() rebuilds the board, which
        # deletes this very widget - doing that while its own drop event is
        # still on the stack takes the process down with SIGABRT.
        target = self.coord
        QTimer.singleShot(0, lambda: self.dialog.rearrange(source, target))


class MinimapDialog(BaseDialog):
    """
    A map of the home page's sub-pages.

    Tap one to go there. Drag one onto another to swap them, or onto an empty
    slot to move it. Empty slots are only offered next to a page that exists,
    so the map cannot grow into places nothing can reach by swiping.
    """

    WIDTH = 560
    GAP   = 8
    CONTENT_MARGIN = 56   #the dialog's own padding, both sides

    def __init__(self, client: "Client", home):
        super().__init__(client, "Minimap")
        self.home = home

        # Built by rebuild(), which replaces them outright each time.
        self.holder = None
        self.board  = None

        self.warning = QLabel("")
        self.warning.setFont(make_font(SIZES.S1))
        self.warning.setWordWrap(True)
        set_style(self.warning, "common", "text-muted")
        self.content.addWidget(self.warning)

        self.done_button = self.add_button("Done", self._try_close, "primary")
        self.rebuild()

    ## -- board

    def _pages(self) -> dict:
        return {tuple(page.coord): page
                for page in self.home.sub_page_dict.values()}

    def rebuild(self) -> None:
        # The board is replaced wholesale rather than emptied and refilled.
        # QGridLayout keeps setRowMinimumHeight/setColumnMinimumWidth for
        # indices that no longer exist, so a rearrange that makes the board
        # narrower leaves the wider board's minimums behind - the holder is
        # then fixed smaller than the layout's own minimum, the layout squeezes
        # it, and the cells overlap. There is no API to clear them, and this
        # only runs on a drag, so a fresh layout is the cheap way to be sure.
        if self.holder is not None:
            self.content.removeWidget(self.holder)
            self.holder.setParent(None)
            self.holder.deleteLater()

        self.holder = QWidget()
        set_style(self.holder, "common", "transparent")
        self.board = QGridLayout(self.holder)
        self.board.setSpacing(self.GAP)
        self.board.setContentsMargins(0, 0, 0, 0)
        self.content.insertWidget(0, self.holder,
                                  alignment=Qt.AlignmentFlag.AlignCenter)

        pages = self._pages()
        if not pages:
            return

        # Dead slots, but only the ones adjacent to a real page - anywhere else
        # is unreachable by swiping and would just be noise on the map.
        slots = set(pages)
        for x, y in list(pages):
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                slots.add((x + dx, y + dy))

        xs = [c[0] for c in slots]
        ys = [c[1] for c in slots]
        current = tuple(self.home._current_coord)

        columns = max(xs) - min(xs) + 1
        rows    = max(ys) - min(ys) + 1

        # Sized to fit, not to a constant. The dialog has a fixed width, so a
        # board wider than it gets squeezed by the layout and the cells overlap
        # - which is the same failure as the collapsed rows, one axis over.
        available = self.WIDTH - self.CONTENT_MARGIN
        cell_w = min(MiniPage.SIZE,
                     (available - (columns - 1) * self.GAP) // max(1, columns))
        cell_w = max(28, int(cell_w))
        cell_h = max(20, int(cell_w * 0.62))

        for y in range(min(ys), max(ys) + 1):
            for x in range(min(xs), max(xs) + 1):
                coord = (x, y)
                if coord not in slots:
                    continue
                cell = MiniPage(self, coord, pages.get(coord),
                                is_current=(coord == current),
                                cell=(cell_w, cell_h))
                self.board.addWidget(cell, y - min(ys), x - min(xs))

        # Every row and column is pinned to a cell, occupied or not. A grid
        # row containing only gaps collapses to nothing, which is what let the
        # row above the origin ride up over it after a rearrange.
        for column in range(columns):
            self.board.setColumnMinimumWidth(column, cell_w)
            self.board.setColumnStretch(column, 0)
        for row in range(rows):
            self.board.setRowMinimumHeight(row, cell_h)
            self.board.setRowStretch(row, 0)

        self.holder.setFixedSize(
            columns * cell_w + (columns - 1) * self.GAP,
            rows * cell_h + (rows - 1) * self.GAP,
        )

        # Queued, and center() rather than adjustSize(). The new holder has
        # only just been inserted and the old one is still pending deletion,
        # so the layout's hint at this instant describes neither board - taking
        # it here shrank the dialog to the height of its title and buttons,
        # clipping the map and putting Done on top of it. center() also shrinks
        # where adjustSize() alone only grows, and re-centres afterwards.
        QTimer.singleShot(0, self._settle)

        self._check_origin()

    def _settle(self) -> None:
        try:
            self.layout().activate()
            self.center()
            self.refresh_backdrop()
        except RuntimeError:
            pass      # dialog closed before the queued call ran

    def _check_origin(self) -> bool:
        """(0,0) must always hold a page - it is where the app starts."""
        occupied = (0, 0) in self._pages()
        self.warning.setText("" if occupied else "A page must sit at the origin.")
        self.warning.setVisible(not occupied)
        self.done_button.setEnabled(occupied)
        return occupied

    ## -- actions

    def go_to(self, coord: tuple) -> None:
        page = self._pages().get(coord)
        if page is None:
            return
        self.home.jump_to_coord(coord)
        if self._check_origin():
            self.close()

    def rearrange(self, source: tuple, target: tuple) -> None:
        try:
            pages  = self._pages()
            moving = pages.get(source)
            if moving is None:
                # The board may have been rebuilt between the drop and this
                # running, so the source is re-read rather than trusted.
                return

            was_current = tuple(self.home._current_coord)
            other = pages.get(target)
            moving.coord = tuple(target)
            if other is not None:
                other.coord = tuple(source)   # a swap, not an overwrite

            # Follow whichever page the user was standing on. Without this,
            # moving the current page leaves the view parked on a coordinate
            # that no longer has anything at it.
            if was_current == source:
                self.home._current_coord = list(target)
            elif was_current == target and other is not None:
                self.home._current_coord = list(source)

            self.home.apply_layout()
            self.home.save_page_layout()
        except Exception as e:
            self.client.log("warning", f"[Minimap] Rearrange failed: {e}")
        finally:
            self.rebuild()

    def _try_close(self) -> None:
        if self._check_origin():
            self.close()

    def can_close(self) -> bool:
        """
        Asked by the dialog manager before it closes anything.

        The origin is where the app starts, so leaving it empty makes the
        layout unreachable on the next launch. This covers the click blocker
        as well as the Done button - a tap outside the dialog goes through the
        manager too, and would otherwise close it regardless.
        """
        return self._check_origin()
