from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt

from src.mixins import mixin_target
from src.ui.page import SubPageFramework
from src.ui.widget import WidgetFramework
from src.ui.controls.drawer import Drawer
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons

if TYPE_CHECKING:
    from src.main import Client


class SubHomePage(SubPageFramework):

    @mixin_target("sub.home.__init__")
    def __init__(self, client: "Client", page):
        super().__init__(client=client, key="home", coord=(0, 0))
        self.page_ = page

        w = int(client.SETTINGS.application.window.size.value[0])
        h = int(client.SETTINGS.application.window.size.value[1])
        self.setFixedSize(w, h)
        self.setStyleSheet("background-color: #0d0d0d;")

        # Widget layer — WA_TransparentForMouseEvents so clicks pass through
        # to any widget below it (e.g. the drawer handle)
        self.widget_manager = WidgetFramework(
            client   = client,
            page_key = "#",
            padding  = client.SETTINGS.home.widget_margin.value,
        )
        self.widget_manager.setParent(self)
        self.widget_manager.setGeometry(0, 0, w, h)
        self.widget_manager.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.widget_manager.show()

        # Drawer — placed and shown by place_on_page()
        self.drawer = Drawer(client, position="bottom")
        self.drawer.setParent(self)
        self.drawer.place_on_page()

        # Built-in drawer buttons
        self._btn_close    = IconButton(Icons.CLOSE,    client.stop)
        self._btn_fullscr  = IconButton(Icons.FULLSCREEN, client.toggle_fullscreen)
        self._btn_settings = IconButton(Icons.SETTINGS,        lambda: client.goto("#settings"))

        self.drawer.add_controls([
            self._btn_settings,
            self._btn_fullscr,
            self._btn_close,
        ])

        # Z-order: background < widget_manager < drawer
        # widget_manager is transparent to mouse so drawer handle receives clicks
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