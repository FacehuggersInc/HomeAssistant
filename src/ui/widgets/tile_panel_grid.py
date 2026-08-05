"""
The tile panel's own grid, and the packing behind it.

A column would be several screens tall at the bundled set's twenty-three
entries - one per row, every row as tall as the tallest thing in it - and
finding a one-cell switch would mean scrolling past everything bigger than it.

So it is a grid, and **it is the same grid**. Cells are the real
`TileGrid`'s cells, the space between them is its gap, and the dots behind
them are drawn the same way - so the panel reads as a corner of the dashboard
holding the tiles that are not on it yet. An entry is the pixel size it will
be once it is out, which is what makes dragging one out change nothing about
it.

**Nothing is named.** A label over every entry costs a line of text per tile
and pushes the grid back apart into rows of cards. The tile draws its own
face, and its face is what somebody is choosing between.

`pack()` and `pack_groups()` have no Qt in them. Packing is arithmetic, and
arithmetic gets things wrong in ways that are invisible on a screen nobody is
looking at.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QPainter, QColor, QBrush, QPixmap

from src.styling import set_style

if TYPE_CHECKING:
    from src.ui.widgets.tile_panel import TilePanel


##PACKING


def _smallest_first(entry: tuple) -> tuple:
    """
    Sort key for `(anything, width, height)`, small to large.

    Area first, because that is what "bigger" means when comparing a 3x1 to a
    2x2; then the taller of the two, since the panel is read downwards and a
    tall entry pushes everything after it further down than a wide one does.
    """
    _, width, height = entry
    return (width * height, height, width)


def pack(items: list, cols: int) -> tuple[dict, int]:
    """
    Place `items` into `cols` columns. Everything here is in whole cells.

    `items` is `[(ident, width, height)]` in the order they should be
    considered. The answer is `({ident: (col, row)}, rows)`.

    A skyline: each item goes at the lowest row it fits. Ties go to the
    placement that buries the least empty column, then to the leftmost, so the
    packing reads left to right rather than wandering.

    An item wider than the grid is placed at the full width rather than
    refused - the caller has already decided it is worth showing, and refusing
    it here would make a tile vanish rather than look wrong.
    """
    cols = max(1, int(cols))
    skyline = [0] * cols
    placed: dict = {}

    for ident, width, height in items:
        width = max(1, min(int(width), cols))
        height = max(1, int(height))

        best_col, best_row, best_waste = 0, None, 0
        for col in range(cols - width + 1):
            row = max(skyline[col:col + width])
            # How much empty column this placement buries. Two slots at the
            # same height are not equally good: one that sits across a deep
            # valley fills it in and nothing can use it again.
            waste = sum(row - skyline[c] for c in range(col, col + width))
            if best_row is None or (row, waste) < (best_row, best_waste):
                best_col, best_row, best_waste = col, row, waste
        best_row = best_row or 0

        placed[ident] = (best_col, best_row)
        for col in range(best_col, best_col + width):
            skyline[col] = best_row + height

    return placed, max(skyline) if placed else 0


def pack_groups(groups: list, cols: int) -> tuple[dict, int]:
    """
    The same, with each group kept together and **smallest first**.

    `groups` is `[(group_id, [(ident, width, height)])]`. A group is packed on
    its own first, and the rectangle it comes out as is then packed with the
    other groups - so a tile's sizes are always adjacent, whatever the packing
    does with them afterwards.

    Order is by size: the one-cell switches across the top, then the
    two-by-twos, down to whatever is biggest. Within a group the same, so a
    tile's small size sits above its large one. That is a reading order rather
    than a packing one - it costs about three rows on the bundled set against
    letting the packer choose, because once the small groups have all been
    placed there is nothing small left to drop into the gaps the large ones
    leave. Being able to find a tile is worth three rows.

    Ties go to the shorter group, then to the narrower, then to the order they
    were registered in, so the same panel packs the same way every time.
    """
    blocks = []
    inner: dict = {}

    for group_id, entries in groups:
        spots, height = pack(sorted(entries, key=_smallest_first), cols)
        inner[group_id] = spots
        width = max((spots[i][0] + min(w, cols) for i, w, _ in entries),
                    default=1)
        blocks.append((group_id, width, height))

    skyline = [0] * max(1, int(cols))
    outer: dict = {}

    for index in sorted(range(len(blocks)),
                        key=lambda i: _smallest_first(blocks[i]) + (i,)):
        group_id, width, height = blocks[index]
        width = max(1, min(width, len(skyline)))

        best_col, best_row, best_waste = 0, None, 0
        for col in range(len(skyline) - width + 1):
            row = max(skyline[col:col + width])
            waste = sum(row - skyline[c] for c in range(col, col + width))
            if best_row is None or (row, waste) < (best_row, best_waste):
                best_col, best_row, best_waste = col, row, waste
        best_row = best_row or 0

        outer[group_id] = (best_col, best_row)
        for c in range(best_col, best_col + width):
            skyline[c] = best_row + height

    placed: dict = {}
    for group_id, spots in inner.items():
        base_col, base_row = outer[group_id]
        for ident, (col, row) in spots.items():
            placed[ident] = (base_col + col, base_row + row)
    return placed, max(skyline) if placed else 0


##THE WIDGET


class TilePanelGrid(QWidget):
    """
    The packed grid inside the panel's scroll view.

    It owns nothing: the panel owns the entries, and this decides which cell
    each one sits in and how tall the whole thing is. It re-packs when the
    panel is opened, when the scroll view changes width, and when the real
    grid's cell size changes underneath it - which it does once at startup,
    since the panel is built during plugin load and the grid has not been laid
    out by then.
    """

    #Kept clear on every side, so no entry touches the panel edge or the
    #scrollbar. The right side carries the scrollbar's own width as well; see
    #`columns_for()`.
    PAD = 14
    #Room for the scrollbar, from `scrollbar.css`. Reserved whether or not one
    #is showing: a scrollbar appearing over the last column is worse than a
    #strip of space that is always there.
    SCROLLBAR = 10

    def __init__(self, panel: "TilePanel"):
        super().__init__()
        self.panel = panel
        self.cols = 1
        self.rows = 0
        self.cell_size = 0
        self.gap = 0
        self.origin_x = self.PAD
        #What the last layout was computed against, so a resize that changes
        #nothing does not rebuild every entry's geometry.
        self._fingerprint: Optional[tuple] = None
        #The dots, rendered once per layout and blitted after that - the same
        #reasoning as TileGrid, where drawing them per paint was what made
        #dragging feel heavy.
        self._dot_cache: Optional[QPixmap] = None
        #Where a scroll of the background is being measured from, or None.
        self._scroll_from: Optional[QPoint] = None
        set_style(self, "common", "transparent")

    ## -- metrics

    def metrics(self) -> tuple[int, int]:
        """The real grid's cell and gap, or the fallback before it has one."""
        grid = getattr(self.panel, "grid", None)
        cell = int(getattr(grid, "cell_size", 0) or 0)
        if cell > 0:
            return cell, int(getattr(grid, "gap_x", 0) or 0)
        return self.panel.FALLBACK_CELL, 0

    def columns_for(self, cell: int, gap: int) -> int:
        """
        How many of the real grid's cells fit across the panel.

        Never more than the grid itself has: an entry offered at nine columns
        on a grid eight wide is an offer that cannot be taken.
        """
        usable = self.width() - self.PAD * 2 - self.SCROLLBAR
        step = cell + gap
        if step <= 0:
            return 1
        cols = int((usable + gap) // step)
        grid = getattr(self.panel, "grid", None)
        limit = int(getattr(grid, "cols", 0) or 0)
        if limit > 0:
            cols = min(cols, limit)
        return max(1, cols)

    def span_size(self, span: tuple, cell: int, gap: int) -> tuple[int, int]:
        """The pixel size a tile of this span occupies on the real grid."""
        width = span[0] * cell + (span[0] - 1) * gap
        height = span[1] * cell + (span[1] - 1) * gap
        return max(1, int(width)), max(1, int(height))

    def cell_origin(self, col: int, row: int) -> tuple[int, int]:
        step = self.cell_size + self.gap
        return self.origin_x + col * step, self.PAD + row * step

    ## -- layout

    def relayout(self, force: bool = False) -> None:
        # Before the panel has been laid out even once this is a few pixels
        # wide, which packs everything into one column and marks every wide
        # entry as impossible. Nothing is lost by waiting: the resize that
        # gives it a width calls this again.
        if self.width() < self.PAD * 2 + self.SCROLLBAR + 20:
            return

        items = list(self.panel.all_items())
        cell, gap = self.metrics()
        cols = self.columns_for(cell, gap)

        fingerprint = (cols, cell, gap, self.width(),
                       tuple((id(item), item.span) for item in items))
        if fingerprint == self._fingerprint and not force:
            return
        self._fingerprint = fingerprint
        self.cols, self.cell_size, self.gap = cols, cell, gap

        step = cell + gap
        full = cols * step - gap
        self.origin_x = self.PAD + max(
            0, (self.width() - self.PAD * 2 - self.SCROLLBAR - full) // 2)

        # An entry wider than the panel can hold cannot be shown at the size
        # it advertises, and shrinking it would be a lie about what dragging
        # it out will place. It is dropped instead - unless it is the only
        # entry its tile has, in which case a scaled one is better than a tile
        # that cannot be found at all.
        #
        # Decided fresh every time. A narrower moment - the first layout, a
        # smaller cell - must not leave an entry marked impossible for good.
        showing, hidden = [], []
        for item in items:
            item.scaled = False
            (showing if item.span[0] <= cols else hidden).append(item)
        rescued = self._rescue(hidden, showing)

        groups: dict = {}
        order: list = []
        for item in showing:
            key = item.tile.KEY
            if key not in groups:
                groups[key] = []
                order.append(key)
            width, height = self.span_size(item.span, cell, gap)
            span_w, span_h = item.span
            if item.scaled:
                # Only ever a rescued entry. Both axes by the same factor, so
                # it is a smaller picture of the tile rather than a different
                # shape of one, and it reserves however many rows that needs.
                height = max(1, int(height * (full / width)))
                width, span_w = full, cols
                span_h = max(1, -(-(height + gap) // step))
            item.apply_metrics(width, height)
            groups[key].append((id(item), span_w, span_h))

        placed, self.rows = pack_groups([(key, groups[key]) for key in order],
                                        cols)

        for item in items:
            if item in showing:
                item.move(*self.cell_origin(*placed[id(item)]))
                item.show()
            else:
                item.hide()

        self.setMinimumHeight(self.PAD * 2 + max(0, self.rows * step - gap))
        self._dot_cache = None
        self.update()

        for item in rescued:
            self.panel.client.log(
                "debug", f"[TilePanel] {item.tile.KEY} at {item.span[0]}x"
                         f"{item.span[1]} is wider than the panel; shown scaled.")

    def _rescue(self, hidden: list, showing: list) -> list:
        """
        Put back any tile whose every size was too wide to show.

        The narrowest of them, scaled to fit. This cannot happen on a panel
        wide enough for the grid's own widest tile, which is the case the
        sizes are chosen for - it is here so a smaller screen loses a
        preview's fidelity rather than losing the tile.
        """
        represented = {item.tile.KEY for item in showing}
        rescued = []
        for key in {item.tile.KEY for item in hidden} - represented:
            candidates = [item for item in hidden if item.tile.KEY == key]
            keep = min(candidates, key=lambda item: item.span[0])
            keep.scaled = True
            showing.append(keep)
            rescued.append(keep)
        return rescued

    ## -- input

    def mousePressEvent(self, event) -> None:
        """
        A press on the background, which is a scroll and nothing else.

        There is no tile here to pull out, so unlike an entry this needs no
        threshold and no direction: the only thing a drag on empty grid can
        mean is moving the panel. Without it the panel could only be scrolled
        by starting on a tile, which is most of the panel but not the part
        somebody reaches for when they are trying not to disturb one.
        """
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._scroll_from = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event) -> None:
        if self._scroll_from is None:
            event.ignore()
            return
        point = event.globalPosition().toPoint()
        self.panel.scroll_by(self._scroll_from.y() - point.y())
        self._scroll_from = point

    def mouseReleaseEvent(self, event) -> None:
        self._scroll_from = None

    ## -- painting

    def _build_dot_cache(self) -> None:
        """
        A dot at every cell corner, the same as the grid's own.

        Rendered once and blitted after that. There are more of them here than
        on the grid, since the panel is taller than a screen, and a few
        hundred antialiased ellipses per paint is what made dragging feel
        heavy on the grid before it cached them.
        """
        self._dot_cache = None
        if self.cell_size <= 0 or self.width() <= 0 or self.height() <= 0:
            return

        ratio = self.devicePixelRatioF() or 1.0
        cache = QPixmap(int(self.width() * ratio), int(self.height() * ratio))
        cache.setDevicePixelRatio(ratio)
        cache.fill(Qt.GlobalColor.transparent)

        painter = QPainter(cache)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(255, 255, 255, 22)))
        painter.setPen(Qt.GlobalColor.transparent)
        step = self.cell_size + self.gap
        radius = 2
        # Past the last packed row, down to the bottom of whatever height the
        # scroll view gave this. The dots are what say there is somewhere to
        # put a tile, and a grid that stops dead under the last one says the
        # opposite.
        down = max(self.rows, int((self.height() - self.PAD * 2 + self.gap) // step))
        for col in range(self.cols + 1):
            for row in range(down + 1):
                x = self.origin_x + col * step - self.gap / 2
                y = self.PAD + row * step - self.gap / 2
                painter.drawEllipse(int(x - radius), int(y - radius),
                                    radius * 2, radius * 2)
        painter.end()
        self._dot_cache = cache

    def paintEvent(self, event) -> None:
        if self.cell_size <= 0:
            return
        if self._dot_cache is None:
            self._build_dot_cache()
        if self._dot_cache is not None:
            painter = QPainter(self)
            painter.drawPixmap(0, 0, self._dot_cache)
            painter.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._dot_cache = None
        self.relayout()
