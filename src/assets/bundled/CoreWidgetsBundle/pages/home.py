from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QPoint

from src.mixins import mixin_target
from src.ui.page import PageFramework, SubPageFramework
from .sub.sub_home import SubHomePage
from .sub.sub_tiles import SubTilesPage

if TYPE_CHECKING:
    from src.main import Client


class HomePage(PageFramework):

    @mixin_target("home.__init__")
    def __init__(self, client: "Client", data=None):
        super().__init__(key="#", client=client, data=data)

        w = int(client.SETTINGS.application.window.size.value[0])
        h = int(client.SETTINGS.application.window.size.value[1])
        self.setFixedSize(w, h)
        self.setStyleSheet("background-color: #0d0d0d;")

        # Sub-page registry
        self.sub_page_dict: dict[str, SubPageFramework] = {}
        self._current_coord = [0, 0]

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

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_start is None:
            return
        delta = event.globalPosition().toPoint() - self._drag_start
        self._drag_start = None
        dx, dy = delta.x(), delta.y()
        if max(abs(dx), abs(dy)) < self._min_swipe:
            return
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
        super().start()

    def stop(self) -> None:
        super().stop()