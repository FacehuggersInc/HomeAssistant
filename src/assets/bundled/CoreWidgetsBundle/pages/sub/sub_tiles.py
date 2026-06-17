from __future__ import annotations
from typing import TYPE_CHECKING

from src.mixins import mixin_target
from src.ui.page import SubPageFramework
from src.ui.widget import WidgetFramework
from src.ui.controls.drawer import Drawer
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons
from src.styling import COLORS

if TYPE_CHECKING:
    from src.main import Client


class SubTilesPage(SubPageFramework):

    @mixin_target("sub.tiles.__init__")
    def __init__(self, client: "Client", page):
        super().__init__(client=client, key="tiles", coord=(1, 0))
        self.page_ = page

        w = int(client.SETTINGS.application.window.size.value[0])
        h = int(client.SETTINGS.application.window.size.value[1])
        self.setFixedSize(w, h)
        self.setStyleSheet(f"background-color: {COLORS.DARK.BGDARK};")

        self.widget_manager = WidgetFramework(
            client   = client,
            page_key = "#",
            padding  = client.SETTINGS.home.widget_margin.value,
        )
        self.widget_manager.setParent(self)
        self.widget_manager.setGeometry(0, 0, w, h)
        self.widget_manager.show()

        self.drawer = Drawer(client, position="bottom")
        self.drawer.setParent(self)
        self.drawer.place_on_page()

        self._btn_close    = IconButton(Icons.CLOSE,    client.stop)
        self._btn_fullscr  = IconButton(Icons.FULLSCREEN, client.toggle_fullscreen)
        self._btn_settings = IconButton(Icons.SETTINGS,        lambda: client.goto("#settings"))

        self.drawer.add_controls([
            self._btn_settings,
            self._btn_fullscr,
            self._btn_close,
        ])

        self.widget_manager.raise_()
        self.drawer.raise_()

        self.add_features({
            "add_widgets":            self.widget_manager.add,
            "remove_widget":          self.widget_manager.remove,
            "add_drawer_controls":    self.drawer.insert_controls,
            "remove_drawer_controls": self.drawer.remove_controls,
        })

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self.widget_manager.setGeometry(0, 0, w, h)
        self.widget_manager.update_geometry()
        self.drawer.apply_parent_width()

    def tick(self) -> None:
        self.widget_manager.tick_widgets()
        self.drawer.tick()