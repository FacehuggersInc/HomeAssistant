from src.mixins import mixin
from src.plugin.template import Plugin
from src.plugin.carryover import PluginCarryover

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
        # override=True is required here. reload_plugin() already calls
        # client.goto(reload_page, override=True) using the page we were
        # on BEFORE unload — by the time this runs, self.client.PAGE.name
        # is very likely already "#cwb_home_page", which means a plain
        # goto("#cwb_home_page") (no override) hits goto()'s own early
        # return guard ("if self.PAGE and self.PAGE.name == page and not
        # override: return") and silently does nothing. That's not
        # actually a problem by itself — but it WAS masking the real bug
        # below, since this call looked like it should matter and didn't.
        if carryover and carryover.has("was_on_plugin_page"):
            self.client.goto("#cwb_home_page", override=True)

    def unload(self, carryover: PluginCarryover = None):
        current_page = self.client.PAGE

        # current_page can legitimately be None (e.g. reload triggered
        # while no page is showing) — accessing .name on it directly
        # without checking for None first raises AttributeError. Because
        # PluginManager.unload_plugin() wraps this whole call in a
        # try/except that only LOGS the error rather than re-raising it,
        # that crash was silent: carryover.set(...) below never ran,
        # carryover.has("was_on_plugin_page") was always False on the
        # next load/reload, and the "go back to where I was" behaviour
        # never fired — with no visible symptom other than a quiet log
        # line saying unload() errored.
        # carryover is only ever a real PluginCarryover during a RELOAD
        # cycle (reload_plugin() always builds one before calling
        # unload()) — a plain unload_plugin() call passes None here,
        # which happens both from the settings page's Unload button and
        # from the app's own shutdown path (unload_plugins()). Skipping
        # this when there's nothing to carry anything over TO is correct,
        # not just defensive — without this guard it crashed with
        # AttributeError on 'NoneType' has no attribute 'set' any time
        # the app closed (or this plugin was unloaded outright) while
        # sitting on its own home page.
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

        #pages registered under this plugin's key are cleaned up
        #automatically by PluginManager.unload_plugin() via
        #self.client.PAGES.unregister("corewidgetsbundle") — same
        #pattern as API_REGISTRY. No manual del needed here anymore.

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