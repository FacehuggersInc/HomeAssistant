from __future__ import annotations
import json
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRect, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush

from src.ui.widgets.tile import Tile

if TYPE_CHECKING:
    from src.main import Client


##TILE GRID

class TileGrid(QWidget):
    """
    Manages placement, drag/snap, ticking and persistence of Tile widgets.

    Cells are kept perfectly square. Whichever axis (width or height) is
    more cramped decides the cell size; the other axis then stretches its
    GAP between cells so the grid still reaches edge to edge instead of
    leaving a dead margin. That's why gap_x and gap_y can differ even
    though every cell is the same square size.

    See Tile.screen_to_grid() in tile.py — it reads cell_size, gap_x,
    gap_y, origin_x, origin_y directly off this class, so keep those
    names in sync if you ever rename them here.
    """

    def __init__(self, client: "Client", cols: int = 16, rows: int = 10):
        super().__init__()
        self.client  = client
        self.cols    = cols
        self.rows    = rows

        #the plugin instance that owns the settings.json this grid
        #persists tile positions into. Looked up directly by key rather
        #than being handed in by SubTilesPage or by whichever plugin is
        #registering a tile — neither of those should need to know or
        #care how/where persistence happens.
        self.owning_plugin_key = "corewidgetsbundle"

        self.tiles: list[Tile] = []

        #all of these are computed by recalculate(), called once on
        #show and again on every resizeEvent
        self.cell_size = 0
        self.gap_x     = 0   #horizontal spacing between cells
        self.gap_y     = 0   #vertical spacing between cells
        self.margin    = 0
        self.origin_x  = 0
        self.origin_y  = 0
        self.drawer_h  = 0   #reserved space at the bottom for the drawer handle

        #live drag state — set by on_tile_move_requested() while a tile
        #(from the grid OR the panel) is being dragged over this widget
        self.dragging_tile: Optional[Tile] = None
        self.hover_col:     int = -1
        self.hover_row:     int = -1

        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    ##PERSISTENCE
    #
    # Positions are stored as one JSON blob inside CoreWidgetsBundle's
    # own settings.json — not anywhere on this widget, not passed in by
    # SubTilesPage, and not something a registering plugin needs to
    # know about at all. The plugin instance is looked up by key only
    # when actually needed (read or write), rather than being stored
    # for the lifetime of the grid — this keeps TileGrid from depending
    # on anyone handing it a reference up front.

    def get_owning_plugin(self):
        """Look up the plugin instance that owns tile persistence, or None if unavailable."""
        try:
            return self.client.PLUGIN.plugins.get(self.owning_plugin_key)
        except Exception:
            return None

    def load_positions(self) -> dict:
        """Read {tile_key: {col, row}} back from plugin settings, or {} if unavailable."""
        plugin = self.get_owning_plugin()
        if not plugin:
            return {}
        try:
            raw = plugin.settings.tiles.positions.value
            return json.loads(raw) if raw and raw != "{}" else {}
        except Exception:
            return {}

    def save_positions(self) -> None:
        """
        Write every currently-placed tile's position to disk RIGHT NOW.

        Two separate bugs had to be fixed for this to actually work:

        1. Plugin settings (plugin.settings) are a custom Settings
           object (src/settings.py), NOT Dynaconf — it only writes to
           disk when the whole app shuts down cleanly through
           PluginManager.unload_plugin(). Too late for tile placement,
           since any crash or force-kill before that point loses
           whatever was just dragged. Fixed by writing the settings
           file ourselves immediately below, the same way
           unload_plugin() eventually would.

        2. Settings.__setattr__ on a nested value (e.g. doing
           `plugin.settings.tiles.positions.value = X`) does NOT reach
           the internal _store dict that to_dict() actually reads from
           — it silently sets a plain Python attribute that to_dict()
           never sees. The fix is dict-style assignment instead:
           `positions["value"] = X`, which routes through
           Settings.__setitem__ and does update _store correctly.
        """
        plugin = self.get_owning_plugin()
        if not plugin:
            return

        positions = {t.KEY: {"col": t.grid_col, "row": t.grid_row} for t in self.tiles}

        try:
            #dict-style assignment — see bug #2 above. Plain attribute
            #assignment here would silently fail to persist.
            plugin.settings.tiles.positions["value"] = json.dumps(positions)

            #flush the WHOLE plugin settings file to disk right now —
            #see bug #1 above. This is what actually makes it durable.
            settings_path = plugin.config["settings"]["path"]
            with open(settings_path, "w") as f:
                json.dump(plugin.settings.to_dict(), f, indent=4)
        except Exception as e:
            self.client.log("error", f"[TileGrid] Failed to save tile positions: {e}", include_traceback=True)

    ##LAYOUT

    def set_drawer_height(self, h: int) -> None:
        """Called once by SubTilesPage so the bottom reserve matches the drawer handle."""
        self.drawer_h = h

    def recalculate(self) -> None:
        """Recompute cell_size / gap_x / gap_y / origin from the current widget size."""
        margin = int(self.client.SETTINGS.home.widget_margin.value)
        self.margin = margin

        #minimum gap before any stretching — keeps cells from touching
        base_gap = max(6, margin // 4)

        bottom_reserve = max(self.drawer_h, margin)
        available_w    = self.width()  - margin * 2
        available_h    = self.height() - margin - bottom_reserve

        #square cells: whichever axis is tighter wins the cell size
        cell_from_w = (available_w - base_gap * (self.cols - 1)) / self.cols
        cell_from_h = (available_h - base_gap * (self.rows - 1)) / self.rows
        self.cell_size = int(min(cell_from_w, cell_from_h))

        grid_w = self.cell_size * self.cols + base_gap * (self.cols - 1)
        grid_h = self.cell_size * self.rows + base_gap * (self.rows - 1)

        #the axis that DIDN'T decide cell_size now has leftover space.
        #rather than centring with dead margin on the sides, spread that
        #leftover space into the gaps so the grid reaches edge to edge
        leftover_w = available_w - grid_w
        leftover_h = available_h - grid_h

        if leftover_w > 0 and self.cols > 1:
            self.gap_x = base_gap + leftover_w / (self.cols - 1)
        else:
            self.gap_x = base_gap

        if leftover_h > 0 and self.rows > 1:
            self.gap_y = base_gap + leftover_h / (self.rows - 1)
        else:
            self.gap_y = base_gap

        #origin is now always just the margin — no extra centring offset
        #needed since gap stretching already fills the available space
        self.origin_x = margin
        self.origin_y = margin

    def cell_rect(self, col: int, row: int, span_w: int = 1, span_h: int = 1) -> QRect:
        """Pixel rect for a tile occupying span_w x span_h cells starting at col,row."""
        x = self.origin_x + col * (self.cell_size + self.gap_x)
        y = self.origin_y + row * (self.cell_size + self.gap_y)
        w = span_w * self.cell_size + (span_w - 1) * self.gap_x
        h = span_h * self.cell_size + (span_h - 1) * self.gap_y
        return QRect(int(x), int(y), int(w), int(h))

    def place_tile(self, tile: Tile, col: int, row: int, animate: bool = False) -> None:
        """
        Move/resize a tile to its grid cell. animate=True slides it there
        smoothly (used when snapping after a drag).

        NOTE: this used to only call save_positions() on the animated
        branch, which meant the very FIRST time a tile was added to the
        grid (animate=False, e.g. straight out of the panel, or on
        startup restoration) its position was never written to disk.
        Both branches now save — restoring from disk passes the same
        saved values back in, so this is a harmless no-op write in that
        case, but it's what actually makes first-placement persist.
        """
        rect = self.cell_rect(col, row, tile.grid_w, tile.grid_h)
        tile.grid_col = col
        tile.grid_row = row
        tile.resize(rect.width(), rect.height())
        if animate:
            anim = QPropertyAnimation(tile, b"pos")
            anim.setDuration(180)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(tile.pos())
            anim.setEndValue(rect.topLeft())
            #save once the slide finishes — not before, so we don't
            #write a half-finished animation's position
            anim.finished.connect(self.save_positions)
            anim.start()
            tile.snap_anim = anim   #keep a reference so it isn't garbage collected mid-flight
        else:
            tile.move(rect.topLeft())
            self.save_positions()

    ##PUBLIC API
    #
    # add_tile() is the single entry point used for THREE different
    # situations, all funnelled through the same code so behaviour is
    # consistent:
    #   1. SubTilesPage.register_tile() placing a tile on first launch
    #   2. TilePanel.place_tile_on_grid() when a tile is dragged out of
    #      the side panel for the first time
    #   3. internally, when restoring a saved position on startup

    def add_tile(self, tile: Tile, col: int = 0, row: int = 0) -> None:
        if not tile.KEY:
            raise ValueError("Tile must have a non-empty KEY")
        if any(t.KEY == tile.KEY for t in self.tiles):
            raise ValueError(f"Tile key '{tile.KEY}' already registered in this grid")

        #if this tile was placed somewhere before and we remember it,
        #that saved spot always wins over whatever col/row was passed in
        positions = self.load_positions()
        if tile.KEY in positions:
            saved = positions[tile.KEY]
            col   = int(saved.get("col", col))
            row   = int(saved.get("row", row))

        col = max(0, min(col, self.cols - tile.grid_w))
        row = max(0, min(row, self.rows - tile.grid_h))

        tile.setParent(self)
        tile.move_requested.connect(self.on_tile_move_requested)
        self.tiles.append(tile)

        if self.cell_size > 0:
            #grid has already been laid out at least once — place immediately
            self.place_tile(tile, col, row)
        else:
            #grid hasn't been shown/resized yet — resizeEvent will place
            #every tile once cell_size becomes known
            tile.grid_col = col
            tile.grid_row = row

        tile.show()

    def remove_tile(self, key: str) -> None:
        """Remove a tile from the grid by key and persist the change immediately."""
        found = [t for t in self.tiles if t.KEY == key]
        for tile in found:
            tile.setParent(None)
            self.tiles.remove(tile)
        if found:
            #write the removal to disk right away — otherwise a deleted
            #tile would reappear on next launch since its old saved
            #position is still sitting in the JSON
            self.save_positions()

    def get_tile(self, key: str) -> Optional[Tile]:
        found = [t for t in self.tiles if t.KEY == key]
        return found[0] if found else None

    def tick(self) -> None:
        """Called once per update cycle by SubTilesPage.tick() — forwards to every placed tile."""
        for tile in self.tiles:
            tile.tick()

    ##DRAG / SNAP
    #
    # on_tile_move_requested() is connected to Tile.move_requested in
    # add_tile() above. It just records where the guide box should be
    # drawn (hover_col/hover_row) — see paintEvent() below. It does NOT
    # move the actual tile; Tile moves itself directly under the cursor
    # in its own mouseMoveEvent. This split exists so the guide box can
    # snap to whole cells while the tile itself can follow the cursor
    # smoothly in between.

    def on_tile_move_requested(self, tile: Tile, col: int, row: int) -> None:
        col = max(0, min(col, self.cols - tile.grid_w))
        row = max(0, min(row, self.rows - tile.grid_h))
        self.hover_col     = col
        self.hover_row     = row
        self.dragging_tile = tile
        self.update()   #trigger a repaint so the guide box moves

    def snap_tile(self, tile: Tile) -> None:
        """
        Called by Tile.mouseReleaseEvent once a drag ends.
        Uses hover_col/hover_row — the exact same numbers the guide box
        was drawn at — so the tile always lands exactly where it looked
        like it was going to land.
        """
        if self.hover_col >= 0 and self.hover_row >= 0:
            col = max(0, min(self.hover_col, self.cols - tile.grid_w))
            row = max(0, min(self.hover_row, self.rows - tile.grid_h))
        else:
            #fallback: recompute directly from the tile's current pixel
            #position, in case hover_col/row were never set for some reason
            if self.cell_size > 0:
                col = round((tile.x() - self.origin_x) / (self.cell_size + self.gap_x))
                row = round((tile.y() - self.origin_y) / (self.cell_size + self.gap_y))
                col = max(0, min(col, self.cols - tile.grid_w))
                row = max(0, min(row, self.rows - tile.grid_h))
            else:
                col, row = tile.grid_col, tile.grid_row

        self.place_tile(tile, col, row, animate=True)
        self.dragging_tile = None
        self.hover_col     = -1
        self.hover_row     = -1
        self.update()

    def mousePressEvent(self, event) -> None:
        #the grid itself never handles a tile drag directly — Tile
        #widgets handle their own mouse events. Ignoring here lets a
        #press that misses every tile fall through to the page behind
        #(e.g. for swipe navigation between sub pages)
        event.ignore()

    def mouseMoveEvent(self, event) -> None:
        if self.dragging_tile is None:
            event.ignore()
            return
        #only reached while a tile drag is active — recompute hover cell
        #from the live cursor position over the grid itself
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

    def paintEvent(self, event) -> None:
        if self.cell_size <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        #faint dots marking every cell corner across the whole grid
        p.setBrush(QBrush(QColor(255, 255, 255, 22)))
        p.setPen(Qt.GlobalColor.transparent)
        r = 2
        for col in range(self.cols + 1):
            for row in range(self.rows + 1):
                x = self.origin_x + col * (self.cell_size + self.gap_x) - self.gap_x / 2
                y = self.origin_y + row * (self.cell_size + self.gap_y) - self.gap_y / 2
                p.drawEllipse(int(x - r), int(y - r), r * 2, r * 2)

        #green-ish highlight box showing exactly where a dragged tile
        #would land if released right now
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
        #every placed tile needs to be re-laid-out against the new
        #cell_size/gap numbers, but each keeps its existing grid_col/row
        for tile in self.tiles:
            self.place_tile(tile, tile.grid_col, tile.grid_row)
        self.update()