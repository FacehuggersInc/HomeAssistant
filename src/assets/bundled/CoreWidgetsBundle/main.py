from src.mixins import mixin
from src.plugin.template import Plugin
from src.plugin.carryover import PluginCarryover

from .widgets.cycling_background import CyclingBackground
from .widgets.datetime import DateTimeWidget
from .widgets.weather import WeatherWidget
from .widgets.configuration_bar import ConfigurationBar
from .widgets.sticky_note import StickyNote
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

    def load(self, carryover: PluginCarryover = None):
        self.client.public.expose("corewidgetsbundle", "cwb_widgets",   self.widgets)
        self.client.public.expose("corewidgetsbundle", "cwb_drawer",    self.drawer_btns)
        self.client.public.expose("corewidgetsbundle", "cwb_sub_pages", self.sub_pages)
        self.client.API["weather"] = OpenMeteoAPI(self, self.client)

        # Register pages owned by this plugin
        self.client.add_page("#cwb_home_page", "Home Page", HomePage, owner="corewidgetsbundle")
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

    def reload(self, carryover: PluginCarryover = None):
        if carryover and carryover.has("was_on_plugin_page"):
            self.client.goto("#cwb_home_page", override=True)

    def unload(self, carryover: PluginCarryover = None):
        current_page = self.client.PAGE

        if carryover and current_page and current_page.name == "#cwb_home_page":
            carryover.set("was_on_plugin_page", (True, "#cwb_home_page"))
            carryover.set("handled_navigation", True)

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


    ## CALLBACKS
    def api_endpoint_test(self, *args, **kwargs):
        panel = self.client.create_panel(on_created=self.panel_callback)
        return {"request": "Success"}, 200

    def panel_callback(self, panel):
        self.client.TIMEOUTS.add(15, panel.close_panel, "api_request_open_panel", True)

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

        sub_tiles.features().register_tile(ClockTile, in_grid=False)


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

        # Registered, not constructed-and-added: the saved layout decides what
        # sits on the page and what waits in the widgets panel, the same way
        # sub.tiles registers tiles.
        register = sub_home.features().register_widget

        # StickyNote is a template: register() returns None for those, since
        # the panel offers it and each Add makes a copy with its own key.
        register(StickyNote)

        self.client.public.cwb_widgets["sub.home"] = [
            w for w in (
                # Always present; carries notifications and the panel button.
                register(ConfigurationBar),
                register(DateTimeWidget, show_date=True, show_time=True),
                register(WeatherWidget),
            ) if w is not None
        ]