from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt

from src.mixins import mixin_target
from src.styling import set_style
from src.ui.page import SubPageFramework
from src.ui.widget import WidgetFramework

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
        set_style(self, "common", "page-background")

        self.widget_manager = WidgetFramework(
            client   = client,
            page_key = "sub.home",
            padding  = client.SETTINGS.home.widget_margin.value,
        )
        self.widget_manager.setParent(self)
        self.widget_manager.setGeometry(0, 0, w, h)
        self.widget_manager.show()

        # No drawer. These controls are global now and live in the quick
        # settings panel, reachable from every page by a swipe from the top.
        self.widget_manager.raise_()

        self.add_features({
            "add_widgets":            self.widget_manager.add,
            "register_widget":        self.widget_manager.register,
            "remove_widget":          self.widget_manager.remove,
            "toggle_widget_panel":    self.widget_manager.toggle_panel,
            "widget_framework":       self.widget_manager,
            "remove_widget":          self.widget_manager.remove,
        })

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self.widget_manager.setGeometry(0, 0, w, h)
        self.widget_manager.update_geometry()

    def on_activated(self) -> None:
        self.widget_manager.set_ticking(True)

    def on_deactivated(self) -> None:
        self.widget_manager.set_ticking(False)

    def teardown(self) -> None:
        self.widget_manager.set_ticking(False)

    def tick(self) -> None:
        self.widget_manager.tick_widgets()