from src.mixins import mixin
from src.plugin.template import Plugin
from src.ui.icons import Icons
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
        self.sub_pages = {
            "home": [],
        }
        self._background = None

    ## CORE

    def load(self, carryover: PluginCarryover = None):
        self.client.public.expose("corewidgetsbundle", "cwb_widgets",   self.widgets)
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
                self.client.public.unexpose("corewidgetsbundle", "cwb_wallpaper")
                self.client.QUICK.unregister("corewidgetsbundle")

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

        # Wallpaper controls, published for the quick settings header. The
        # panel hides them when this is absent, which is how they stay
        # sub.home-only now that the panel itself is global.
        self.client.public.expose("corewidgetsbundle", "cwb_wallpaper", {
            "cycle":      self._background.cycle,
            "toggle_pin": self._background.toggle_pin,
            "is_pinned":  self._background.is_pinned,
            "can_cycle":  self._background.can_cycle,
        }, overwrite=True)

        # The widgets panel was only reachable from this page. As a quick
        # access entry it is reachable from anywhere.
        self.client.QUICK.register(
            "corewidgetsbundle", "widget_panel", "Widgets", Icons.EXTENSION,
            on_press = lambda: sub_home.features().toggle_widget_panel(),
            order    = 10,
        )

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