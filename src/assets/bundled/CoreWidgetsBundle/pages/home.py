from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QPoint, QTimer

from src.mixins import mixin_target
from src.styling import set_style
from src.ui.page import PageFramework, SubPageFramework
from .sub.sub_home import SubHomePage
from .sub.sub_tiles import SubTilesPage

if TYPE_CHECKING:
    from src.main import Client


class HomePage(PageFramework):

    @mixin_target("home.__init__")
    def __init__(self, client: "Client", data=None):
        super().__init__(key="#cwb_home_page", client=client, data=data)

        w = int(client.SETTINGS.application.window.size.value[0])
        h = int(client.SETTINGS.application.window.size.value[1])
        self.setFixedSize(w, h)
        set_style(self, "common", "page-background")

        # Sub-page registry
        self.sub_page_dict: dict[str, SubPageFramework] = {}
        self._current_coord = [0, 0]

        # Hold on empty space to open the page map. Only reaches here when no
        # widget above wanted the press, which is what makes "empty space"
        # mean what it says.
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.setInterval(550)
        self._hold.timeout.connect(self.open_minimap)
        self._minimap = None

        # Create default sub-pages
        self._add_sub_page_internal(
            "home",  client.MIXINS.apply_mixins_to(SubHomePage)(client, self)
        )
        self._add_sub_page_internal(
            "tiles", client.MIXINS.apply_mixins_to(SubTilesPage)(client, self)
        )

        # Position sub-pages in virtual grid
        for page in self.sub_page_dict.values():
            page.move(page.coord[0] * w, page.coord[1] * h)
            page.show()

        # Active sub-page
        self.sub_page_dict["home"].is_active = True

        # Swipe tracking
        self._drag_start: QPoint | None = None
        self._min_swipe = 40   # px

        # Expose features
        self.add_features({
            "add_sub_page":    self.add_sub_page,
            "remove_sub_page": self.remove_sub_page,
        })
        # Also expose each sub-page's own features
        for page in self.sub_page_dict.values():
            self.add_features({page.name: page.features()})

    # ── Sub-page management ───────────────────────────────────────────────────

    def _add_sub_page_internal(self, key: str, page: SubPageFramework) -> None:
        page.setParent(self)
        self.sub_page_dict[key] = page

    def add_sub_page(self, key: str, page_class) -> None:
        if key in self.sub_page_dict:
            return
        w = self.width()
        h = self.height()
        page = self.client.MIXINS.apply_mixins_to(page_class)(self.client, self)
        page.setParent(self)
        page.setFixedSize(w, h)
        page.move(page.coord[0] * w, page.coord[1] * h)
        page.show()
        self.sub_page_dict[key] = page
        self.add_features({page.name: page.features()})

    def remove_sub_page(self, key: str) -> None:
        page = self.sub_page_dict.pop(key, None)
        if page:
            self.remove_features([page.name])
            page.setParent(None)

    def _get_page_at_coord(self, cx: int, cy: int) -> SubPageFramework | None:
        for page in self.sub_page_dict.values():
            if page.coord[0] == cx and page.coord[1] == cy:
                return page
        return None

    def _current_page(self) -> SubPageFramework | None:
        return self._get_page_at_coord(*self._current_coord)

    # ── Swipe navigation ──────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
            self._hold.start()

    def mouseReleaseEvent(self, event) -> None:
        self._hold.stop()
        if self._drag_start is None:
            return
        delta = event.globalPosition().toPoint() - self._drag_start
        self._drag_start = None
        dx, dy = delta.x(), delta.y()
        if max(abs(dx), abs(dy)) < self._min_swipe:
            return
        # A swipe is not a hold, and the map opening mid-swipe would be worse
        # than it not opening at all.
        self._hold.stop()
        if abs(dx) >= abs(dy):
            self._try_swipe(1 if dx < 0 else -1, 0)
        else:
            self._try_swipe(0, 1 if dy < 0 else -1)

    def _try_swipe(self, dcx: int, dcy: int) -> None:
        target_coord = [
            self._current_coord[0] + dcx,
            self._current_coord[1] + dcy,
        ]
        target = self._get_page_at_coord(*target_coord)
        if not target:
            return

        current = self._current_page()
        if current:
            current.is_active = False

        self._current_coord = target_coord
        target.is_active    = True

        # Slide all sub-pages
        w = self.width()
        h = self.height()
        for page in self.sub_page_dict.values():
            dest_x = (page.coord[0] - self._current_coord[0]) * w
            dest_y = (page.coord[1] - self._current_coord[1]) * h
            page.animate_to(dest_x, dest_y)

    # ── Page map ──────────────────────────────────────────────────────────────

    def open_minimap(self) -> None:
        self._drag_start = None

        # Checked by visibility, not by the reference existing. The dialog
        # manager hides and unparents a closed dialog rather than deleting it,
        # so `destroyed` never fires and a reference-only guard would refuse to
        # open the map ever again after the first time.
        existing = self._minimap
        if existing is not None:
            try:
                if existing.isVisible():
                    return
                existing.deleteLater()
            except RuntimeError:
                pass
        self._minimap = None

        from .minimap import MinimapDialog
        self._minimap = MinimapDialog(self.client, self)
        self.client.dialog(self._minimap)

    def jump_to_coord(self, coord) -> None:
        """Go straight to a coordinate rather than one swipe at a time."""
        target = self._get_page_at_coord(*coord)
        if target is None:
            return
        current = self._current_page()
        if current:
            current.is_active = False
        self._current_coord = list(coord)
        target.is_active = True
        self.apply_layout()

    def apply_layout(self, animate: bool = True) -> None:
        """Re-place every sub-page from its coord. Used after a rearrange."""
        w, h = self.width(), self.height()
        for page in self.sub_page_dict.values():
            dest_x = (page.coord[0] - self._current_coord[0]) * w
            dest_y = (page.coord[1] - self._current_coord[1]) * h
            if animate:
                page.animate_to(dest_x, dest_y)
            else:
                page.move(dest_x, dest_y)

    ## -- persistence

    def _layout_path(self):
        from src.constants import get_data_dir, APP_NAME
        return get_data_dir(APP_NAME) / "sub_page_layout.json"

    def save_page_layout(self) -> None:
        data = {key: list(page.coord) for key, page in self.sub_page_dict.items()}
        try:
            path = self._layout_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            self.client.dump(data, path)
            self.client.log("debug", f"[HomePage] saved {len(data)} sub-page positions")
        except Exception as e:
            self.client.log("warning", f"[HomePage] Could not save page layout: {e}")

    def load_page_layout(self) -> None:
        """
        Applied after every sub-page has registered, not as they arrive - a
        saved coord for a page that has not been added yet would be dropped.
        """
        try:
            path = self._layout_path()
            if not path.is_file():
                return
            import json
            saved = json.loads(path.read_text())
        except Exception as e:
            self.client.log("warning", f"[HomePage] Could not read page layout: {e}")
            return

        for key, coord in (saved or {}).items():
            page = self.sub_page_dict.get(key)
            if page is not None and isinstance(coord, list) and len(coord) == 2:
                page.coord = (int(coord[0]), int(coord[1]))

        if not any(tuple(p.coord) == (0, 0) for p in self.sub_page_dict.values()):
            # A layout with nothing at the origin cannot be navigated to from
            # startup, so it is discarded rather than half-applied.
            self.client.log("warning",
                            "[HomePage] Saved layout has no page at (0,0) - ignoring it.")
            return

        self._current_coord = [0, 0]
        self.apply_layout(animate=False)

    # ── Resize ────────────────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        for page in self.sub_page_dict.values():
            page.setFixedSize(w, h)
            dest_x = (page.coord[0] - self._current_coord[0]) * w
            dest_y = (page.coord[1] - self._current_coord[1]) * h
            page.move_to(dest_x, dest_y)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        # Here, not in __init__: sub-pages are added by plugins after the page
        # is constructed, so this is the first point at which they all exist.
        self.load_page_layout()
        super().start()

    def stop(self) -> None:
        super().stop()