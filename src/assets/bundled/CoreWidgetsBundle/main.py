from src.mixins import mixin
from src.plugin.template import Plugin
from src.ui.controls.buttons import IconButton

from .widgets.cycling_background import CyclingBackground
from .widgets.datetime import DateTimeWidget
from .widgets.weather import WeatherWidget
from .widgets.notification import NotificationCenterWidget
from .widgets.tiles.clock_tile import ClockTile
from .pages.home import HomePage
from .api.openmeteo import OpenMeteoAPI


class CoreWidgetsBundle(Plugin):
    def __init__(self):
        self.pages = {
            "home":      None,
            "settings":  None,
            "sub.home":  None,
            "sub.tiles": None,
        }
        self.widgets = {
            "settings":  [],
            "sub.tiles": [],
            "sub.home":  [],
        }
        self.drawer_btns = {
            "sub.home": [],
        }
        self.sub_pages = {
            "home": [],
        }
        self._background = None

    ## CORE

    def load(self):
        self.client.public.expose("corewidgetsbundle", "cwb_widgets",   self.widgets)
        self.client.public.expose("corewidgetsbundle", "cwb_drawer",    self.drawer_btns)
        self.client.public.expose("corewidgetsbundle", "cwb_sub_pages", self.sub_pages)
        self.client.API["weather"] = OpenMeteoAPI(self, self.client)

        # Register pages owned by this plugin
        self.client.add_page("#cwb_home_page", "Home Page", HomePage)
        self.client.DEFAULT_PAGE = "#cwb_home_page"

        #Register API
        api_endpoint, registered_flag = self.client.API_REGISTRY.register(
            "corewidgetsbundle",
            "test",
            self.api_endpoint_test,
            False,
            False
        )

        self.client.log("info", "[CoreWidgetsBundle] Loaded.")

    def reload(self):
        pass

    def unload(self):
        current_page = self.client.PAGE

        if current_page and current_page.name == "#settings":
            for widget in self.widgets.get("settings", []):
                widget.stop_tick()
                current_page.features().remove_widget(widget.KEY)

        elif current_page and current_page.name == "#cwb_home_page":
            sub_home  = self.pages.get("sub.home")
            sub_tiles = self.pages.get("sub.tiles")

            if sub_home:
                for widget in self.widgets.get("sub.home", []):
                    widget.stop_tick()
                    if sub_home.has_feature("remove_widget"):
                        sub_home.features().remove_widget(widget.KEY)
                if self._background:
                    self._background.stop()
                    self._background.setParent(None)
                    self._background = None
                if sub_home.has_feature("remove_drawer_controls"):
                    sub_home.features().remove_drawer_controls(
                        self.drawer_btns.get("sub.home", [])
                    )

            if sub_tiles:
                for widget in self.widgets.get("sub.tiles", []):
                    widget.stop_tick()
                    if sub_tiles.has_feature("remove_widget"):
                        sub_tiles.features().remove_widget(widget.KEY)
        
        del self.client.PAGES["#cwb_home_page"]

    ## CALLBACKS
    def api_endpoint_test(self, *args, **kwargs):
        return {"request":"Success",  "package":self.client.public.exposed["corewidgetsbundle"]}, 200

    ## MIXINS
    @mixin("home.__init__", "corewidgetsbundle", "after")
    def _inject_home_sub_pages(self, home_page, *args):
        self.pages["home"] = home_page

    @mixin("settings.__init__", "corewidgetsbundle", "after")
    def _inject_settings_widgets(self, settings_page, *args):
        self.pages["settings"] = settings_page

    @mixin("sub.tiles.__init__", "corewidgetsbundle", "after")
    def _inject_tiles_widgets(self, sub_tiles, *args):
        self.pages["sub.tiles"] = sub_tiles

        #register example clock tile — pass the CLASS, not an instance;
        #SubTilesPage constructs it and handles persistence/placement
        #entirely on its own, this plugin never touches that machinery
        sub_tiles.features().register_tile(ClockTile, in_grid=False)

        #NOTE: this page has no WidgetFramework / anchored widget layer
        #(see the comment block at the top of sub_tiles.py) — a
        #DateTimeWidget used to be anchored here via add_widgets(), but
        #that feature no longer exists on this page. If a clock display
        #is wanted here again, register a Tile-based clock instead
        #(see ClockTile above, or widgets/tiles/clock_tile.py).

    @mixin("sub.home.__init__", "corewidgetsbundle", "after")
    def _inject_home_widgets(self, sub_home, *args):
        self.pages["sub.home"] = sub_home

        # Background — parented directly to page, lowered behind everything
        self._background = CyclingBackground(self.client, sub_home)
        self._background.setParent(sub_home)
        self._background.lower()

        # Drawer buttons from the background widget
        self.drawer_btns["sub.home"] = [
            (self._background._pin_btn,   0),
            (self._background._cycle_btn, 1),
        ]
        if sub_home.has_feature("add_drawer_controls"):
            sub_home.features().add_drawer_controls(self.drawer_btns["sub.home"])

        # Anchored widgets into WidgetFramework
        widgets = [
            DateTimeWidget(self.client, show_date=True, show_time=True),
            WeatherWidget(self.client),
            NotificationCenterWidget(self.client),
        ]
        self.client.public.cwb_widgets["sub.home"] = widgets
        sub_home.features().add_widgets(widgets)

    