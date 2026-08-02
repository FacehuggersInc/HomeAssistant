from __future__ import annotations
import json
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRect, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPixmap

from src.ui.widgets.tile import Tile

if TYPE_CHECKING:
    from src.main import Client


##TILE GRID

class TileGrid(QWidget):
    #The gap between tiles, as a share of one tile. Proportional rather than
    #fixed so the spacing looks the same on a grid of four and a grid of forty.
    GAP_RATIO = 0.15


    def __init__(self, client: "Client", cols: int = 16, rows: int = 10):
        super().__init__()
        self.client  = client
        self.cols    = cols
        self.rows    = rows

        self.owning_plugin_key = "corewidgetsbundle"

        self.tiles: list[Tile] = []

        self.cell_size = 0
        self.gap_x     = 0   #horizontal spacing between cells
        self.gap_y     = 0   #vertical spacing between cells, always the same
        self.margin    = 0
        self.origin_x  = 0
        self.origin_y  = 0

        self.dragging_tile: Optional[Tile] = None
        self.hover_col:     int = -1
        self.hover_row:     int = -1

        # Rendered once per layout, blitted per paint - see _build_dot_cache().
        self._dot_cache: Optional[QPixmap] = None

        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


    def get_owning_plugin(self):
        try:
            return self.client.PLUGIN.plugins.get(self.owning_plugin_key)
        except Exception:
            return None

    ## -- LAYOUT STORAGE
    #
    # In the user data directory, alongside widget_layout.json - not in the
    # owning plugin's settings.json. Layout is user state and settings.json is
    # shipped with the plugin: an update unpacks over it, and a reload rewrites
    # it. The widget framework learnt this the hard way and tiles were still
    # doing it the old way.

    def _layout_path(self):
        from src.constants import get_data_dir, APP_NAME
        return get_data_dir(APP_NAME) / "tile_layout.json"

    def load_positions(self) -> dict:
        path = self._layout_path()
        try:
            if not path.is_file():
                # Nothing yet. There used to be a migration here that lifted a
                # layout out of the owning plugin's settings.json; that setting
                # is gone, so the only thing left to say is that there is no
                # layout.
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get(self.owning_plugin_key, {}) or {}
        except Exception as e:
            self.client.log("warning", f"[TileGrid] Could not read layout: {e}")
            return {}

    def _write(self, positions: dict) -> None:
        path = self._layout_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if path.is_file():
                # Merged, not replaced - another grid's tiles live in the same
                # file under their own plugin key.
                try:
                    data = json.loads(path.read_text(encoding="utf-8")) or {}
                except Exception:
                    data = {}
            data[self.owning_plugin_key] = positions
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            self.client.log("warning", f"[TileGrid] Could not save layout: {e}")

    def _on_snap_finished(self) -> None:
        """
        Guarded as well as parented.

        A `finished` already queued when the grid went is still delivered, and
        there is nothing left to save it to.
        """
        try:
            self.save_positions()
        except RuntimeError:
            pass

    def forget(self, key: str) -> bool:
        """
        Drop one tile's saved entry from disk.

        For a key that should no longer exist at all - a bookmark that held
        the template's own key before this tile could be placed twice, and has
        been re-saved under one of its own. Left behind it would be restored
        onto the template again on the next launch.
        """
        path = self._layout_path()
        try:
            if not path.is_file():
                return False
            data = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError) as e:
            self.client.log("warning", f"[TileGrid] Could not forget {key}: {e}")
            return False

        section = data.get(self.owning_plugin_key) or {}
        if key not in section:
            return False
        section.pop(key, None)
        # _write() merges under the plugin key, so it is handed the section
        # rather than the whole file.
        self._write(section)
        return True

    def save_positions(self) -> None:
        # Span as well as position, so a resized tile comes back resized -
        # plus whatever the tile itself says it needs.
        #
        # A bookmark tile is a position and an ADDRESS; without somewhere to
        # put the second it comes back on the right cell asking to be chosen
        # again. Merged under the same key rather than in a second file: it is
        # the same tile's state.
        saved = {}
        for tile in self.tiles:
            entry = {"col": tile.grid_col, "row": tile.grid_row,
                     "w": tile.grid_w, "h": tile.grid_h}
            extra = getattr(tile, "tile_state", None)
            if callable(extra):
                try:
                    more = extra()
                    if isinstance(more, dict):
                        # Position wins. A tile cannot overwrite where it is by
                        # returning a key with the same name.
                        entry = {**more, **entry}
                except Exception as e:
                    self.client.log("debug",
                                    f"[Tiles] {tile.KEY} state failed: {e}")
            saved[tile.KEY] = entry
        self._write(saved)

    ##LAYOUT

    def recalculate(self) -> None:
        # The dots are derived from cell_size, the gaps and the origin, all of
        # which this recomputes - so the cache is dropped here rather than at
        # each call site.
        self._dot_cache = None
        margin = int(self.client.SETTINGS.home.layout.widget_margin.value)
        self.margin = margin

        #minimum gap before anything else — keeps cells from touching
        base_gap = max(6, margin // 4)

        bottom_reserve = margin   #was max(drawer_handle, margin); there is no drawer now
        available_w    = self.width()  - margin * 2
        available_h    = self.height() - margin - bottom_reserve

        # One gap, both ways, proportional to the cell.
        #
        # Pouring each axis's LEFTOVER into that axis's gaps makes the two
        # disagree by however much the screen disagrees with the grid: on a
        # wide panel almost all the spare room is horizontal, so the columns
        # end up far apart and the rows nearly touching. The tiles are square
        # and the spacing between them should be too.
        #
        # So the cell is sized to leave room for the same gap on both axes,
        # and whatever is still spare becomes margin rather than more gap.
        ratio = self.GAP_RATIO
        cell_from_w = available_w / (self.cols + (self.cols - 1) * ratio)
        cell_from_h = available_h / (self.rows + (self.rows - 1) * ratio)
        self.cell_size = max(1, int(min(cell_from_w, cell_from_h)))

        gap = max(base_gap, int(round(self.cell_size * ratio)))
        # A gap that no longer fits after rounding comes back down rather than
        # pushing the last column off the edge.
        for axis, count in ((available_w, self.cols), (available_h, self.rows)):
            if count > 1:
                room = (axis - self.cell_size * count) / (count - 1)
                gap = min(gap, int(room))
        gap = max(0, gap)
        self.gap_x = self.gap_y = gap

        grid_w = self.cell_size * self.cols + gap * (self.cols - 1)
        grid_h = self.cell_size * self.rows + gap * (self.rows - 1)

        # Centred in what is left, so the spare room reads as a margin.
        self.origin_x = margin + max(0, (available_w - grid_w) // 2)
        self.origin_y = margin + max(0, (available_h - grid_h) // 2)

    def cell_rect(self, col: int, row: int, span_w: int = 1, span_h: int = 1) -> QRect:
        x = self.origin_x + col * (self.cell_size + self.gap_x)
        y = self.origin_y + row * (self.cell_size + self.gap_y)
        w = span_w * self.cell_size + (span_w - 1) * self.gap_x
        h = span_h * self.cell_size + (span_h - 1) * self.gap_y
        return QRect(int(x), int(y), int(w), int(h))

    def place_tile(self, tile: Tile, col: int, row: int, animate: bool = False) -> None:
        # Spans change during a resize drag, so a position that fitted a moment
        # ago may not now.
        col = max(0, min(col, max(0, self.cols - tile.grid_w)))
        row = max(0, min(row, max(0, self.rows - tile.grid_h)))
        rect = self.cell_rect(col, row, tile.grid_w, tile.grid_h)
        tile.grid_col = col
        tile.grid_row = row
        tile.resize(rect.width(), rect.height())
        if animate:
            # Parented to the tile. The first argument to QPropertyAnimation
            # is the TARGET, not a parent - so without the third it belongs to
            # nothing, outlives whatever it was animating, and fires finished
            # into an object that has gone. Inside a Qt signal that aborts the
            # process rather than raising.
            anim = QPropertyAnimation(tile, b"pos", tile)
            anim.setDuration(180)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(tile.pos())
            anim.setEndValue(rect.topLeft())
            anim.finished.connect(self._on_snap_finished)
            anim.start()
            tile.snap_anim = anim   #keep a reference so it isn't garbage collected mid-flight
        else:
            tile.move(rect.topLeft())
            self.save_positions()


    def add_tile(self, tile: Tile, col: int = 0, row: int = 0) -> None:
        if not tile.KEY:
            raise ValueError("Tile must have a non-empty KEY")
        if any(t.KEY == tile.KEY for t in self.tiles):
            raise ValueError(f"Tile key '{tile.KEY}' already registered in this grid")

        positions = self.load_positions()
        if tile.KEY in positions:
            saved = positions[tile.KEY]
            col   = int(saved.get("col", col))
            row   = int(saved.get("row", row))
            apply_state = getattr(tile, "apply_tile_state", None)
            if callable(apply_state):
                try:
                    apply_state(saved)
                except Exception as e:
                    self.client.log("debug",
                                    f"[Tiles] {tile.KEY} restore failed: {e}")

        col = max(0, min(col, self.cols - tile.grid_w))
        row = max(0, min(row, self.rows - tile.grid_h))

        tile.setParent(self)
        tile.move_requested.connect(self.on_tile_move_requested)
        tile.resize_requested.connect(self.on_tile_resize_requested)
        tile.remove_requested.connect(self.on_tile_remove_requested)
        self.tiles.append(tile)

        if self.cell_size > 0:
            #grid has already been laid out at least once — place immediately
            self.place_tile(tile, col, row)
        else:
            tile.grid_col = col
            tile.grid_row = row

        tile.show()

    def remove_tile(self, key: str) -> None:
        tile = next((t for t in self.tiles if t.KEY == key), None)
        teardown = getattr(tile, "teardown", None) if tile is not None else None
        if callable(teardown):
            try:
                teardown()
            except Exception:
                pass

        found = [t for t in self.tiles if t.KEY == key]
        for tile in found:
            tile.setParent(None)
            self.tiles.remove(tile)
        if found:
            self.save_positions()

    def get_tile(self, key: str) -> Optional[Tile]:
        found = [t for t in self.tiles if t.KEY == key]
        return found[0] if found else None

    def tick(self) -> None:
        for tile in self.tiles:
            tile.tick()


    def on_tile_resize_requested(self, tile: Tile, span_w: int, span_h: int) -> None:
        """Live during the drag: re-place at the new span so the preview is real."""
        col = min(tile.grid_col, max(0, self.cols - span_w))
        row = min(tile.grid_row, max(0, self.rows - span_h))
        self.place_tile(tile, col, row)
        self.update()

    def can_resize_to(self, tile: Tile, span_w: int, span_h: int) -> bool:
        return self.cells_free(tile, tile.grid_col, tile.grid_row, span_w, span_h)

    def on_tile_remove_requested(self, tile: Tile) -> None:
        """
        Off the grid and back into the panel.

        Not a delete - the instance is kept, so its saved size and whatever
        state it built up survive being put back.
        """
        page = self.parent()
        self.remove_tile(tile.KEY)
        tile.deselect()
        if page is not None and hasattr(page, "return_tile_to_panel"):
            page.return_tile_to_panel(tile)

    def deselect_all(self, except_tile: Tile = None) -> None:
        for tile in self.tiles:
            if tile is not except_tile:
                tile.deselect()

    def on_tile_move_requested(self, tile: Tile, col: int, row: int) -> None:
        col = max(0, min(col, self.cols - tile.grid_w))
        row = max(0, min(row, self.rows - tile.grid_h))

        # Only when the drop target actually changes cell. This is called on
        # every mouse-move event of a drag - many per cell of travel - and
        # each one used to force a full-grid repaint to move a guide box that
        # had not moved.
        if (col == self.hover_col and row == self.hover_row
                and tile is self.dragging_tile):
            return

        self.hover_col     = col
        self.hover_row     = row
        self.dragging_tile = tile
        self.update()   #trigger a repaint so the guide box moves

    def cells_free(self, tile: Tile, col: int, row: int,
                   span_w: int = None, span_h: int = None) -> bool:
        """Whether tile could occupy this block without landing on another."""
        span_w = span_w or tile.grid_w
        span_h = span_h or tile.grid_h
        if col < 0 or row < 0 or col + span_w > self.cols or row + span_h > self.rows:
            return False

        for other in self.tiles:
            if other is tile:
                continue
            if (col < other.grid_col + other.grid_w
                    and col + span_w > other.grid_col
                    and row < other.grid_row + other.grid_h
                    and row + span_h > other.grid_row):
                return False
        return True

    def _first_free(self, tile: Tile):
        for row in range(self.rows - tile.grid_h + 1):
            for col in range(self.cols - tile.grid_w + 1):
                if self.cells_free(tile, col, row):
                    return col, row
        return None

    def snap_tile(self, tile: Tile) -> None:
        if self.hover_col >= 0 and self.hover_row >= 0:
            col = max(0, min(self.hover_col, self.cols - tile.grid_w))
            row = max(0, min(self.hover_row, self.rows - tile.grid_h))
        else:
            if self.cell_size > 0:
                col = round((tile.x() - self.origin_x) / (self.cell_size + self.gap_x))
                row = round((tile.y() - self.origin_y) / (self.cell_size + self.gap_y))
                col = max(0, min(col, self.cols - tile.grid_w))
                row = max(0, min(row, self.rows - tile.grid_h))
            else:
                col, row = tile.grid_col, tile.grid_row

        # Occupied cells send it home. Sliding it to the nearest free block
        # instead would move a tile somewhere nobody pointed at, and on a grid
        # this size that is usually across the screen.
        if not self.cells_free(tile, col, row):
            col, row = getattr(tile, "drag_origin", (tile.grid_col, tile.grid_row))
            if not self.cells_free(tile, col, row):
                # A tile arriving from the panel has nowhere to go back to, so
                # the first free block is better than dropping it on a neighbour.
                col, row = self._first_free(tile) or (tile.grid_col, tile.grid_row)

        self.place_tile(tile, col, row, animate=True)
        self.dragging_tile = None
        self.hover_col     = -1
        self.hover_row     = -1
        self.update()

    def mousePressEvent(self, event) -> None:
        # A press that reaches the grid landed on empty space, not on a tile -
        # which is the natural "I am done with that one" gesture.
        self.deselect_all()
        event.ignore()

    def mouseMoveEvent(self, event) -> None:
        if self.dragging_tile is None:
            event.ignore()
            return
        col = int((event.position().x() - self.origin_x) // (self.cell_size + self.gap_x))
        row = int((event.position().y() - self.origin_y) // (self.cell_size + self.gap_y))
        col = max(0, min(col, self.cols - self.dragging_tile.grid_w))
        row = max(0, min(row, self.rows - self.dragging_tile.grid_h))
        self.hover_col = col
        self.hover_row = row
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self.dragging_tile:
            tile = self.dragging_tile
            col  = max(0, min(self.hover_col, self.cols - tile.grid_w))
            row  = max(0, min(self.hover_row, self.rows - tile.grid_h))
            self.place_tile(tile, col, row, animate=True)
            self.dragging_tile = None
            self.hover_col     = -1
            self.hover_row     = -1
            self.update()
        else:
            event.ignore()

    ##PAINTING

    def _build_dot_cache(self) -> None:
        """
        The cell dots, drawn once into a pixmap.

        There are (cols+1)x(rows+1) of them - 187 on the default grid - and
        paintEvent runs on every mouse-move of a drag. Drawing that many
        antialiased ellipses per move is what made dragging a tile feel heavy;
        the dots only change when the grid is laid out, so they are rendered
        once and blitted thereafter.
        """
        self._dot_cache = None
        if self.cell_size <= 0 or self.width() <= 0 or self.height() <= 0:
            return

        ratio = self.devicePixelRatioF() or 1.0
        cache = QPixmap(int(self.width() * ratio), int(self.height() * ratio))
        cache.setDevicePixelRatio(ratio)
        cache.fill(Qt.GlobalColor.transparent)

        p = QPainter(cache)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(255, 255, 255, 22)))
        p.setPen(Qt.GlobalColor.transparent)
        r = 2
        for col in range(self.cols + 1):
            for row in range(self.rows + 1):
                x = self.origin_x + col * (self.cell_size + self.gap_x) - self.gap_x / 2
                y = self.origin_y + row * (self.cell_size + self.gap_y) - self.gap_y / 2
                p.drawEllipse(int(x - r), int(y - r), r * 2, r * 2)
        p.end()

        self._dot_cache = cache

    def paintEvent(self, event) -> None:
        if self.cell_size <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        #faint dots marking every cell corner across the whole grid
        if self._dot_cache is None:
            self._build_dot_cache()
        if self._dot_cache is not None:
            p.drawPixmap(0, 0, self._dot_cache)

        if self.dragging_tile and self.hover_col >= 0:
            drop_rect = self.cell_rect(
                self.hover_col, self.hover_row,
                self.dragging_tile.grid_w,
                self.dragging_tile.grid_h,
            )
            p.setBrush(QBrush(QColor(255, 255, 255, 20)))
            p.setPen(QPen(QColor(255, 255, 255, 60), 1.5))
            p.drawRoundedRect(drop_rect, 10, 10)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.recalculate()
        self._dot_cache = None      # geometry changed, so the dots have too
        for tile in self.tiles:
            self.place_tile(tile, tile.grid_col, tile.grid_row)
        self.update()