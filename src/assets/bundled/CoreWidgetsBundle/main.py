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
from .widgets.tiles.weather_tile import WeatherTile
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
        self.timers = None      #TimerService, built in load()

    ## CORE

    def load(self, carryover: PluginCarryover = None):
        self.client.public.expose("corewidgetsbundle", "cwb_widgets",   self.widgets)
        self.client.public.expose("corewidgetsbundle", "cwb_sub_pages", self.sub_pages)
        self.client.API["weather"] = OpenMeteoAPI(self, self.client)

        # Register pages owned by this plugin
        self.client.add_page("#cwb_home_page", "Home Page", HomePage, owner="corewidgetsbundle")
        self.client.DEFAULT_PAGE = "#cwb_home_page"

        # Timers. The service owns the countdowns; the widget only draws one.
        from .timers import TimerService
        self.timers = TimerService(self)
        self.timers.start_watching()

        # Declared before anything can subscribe - subscribe_to_event indexes
        # straight into the event table, so a name that has not been created
        # is a KeyError rather than a quiet no-op.
        if "on_timer_finished" not in self.client.EVENTS["on_call"]:
            self.client.create_on_call_event("on_timer_finished")

        self.client.public.expose("corewidgetsbundle", "timers", {
            "start":       self.timers.start,
            "cancel":      self.timers.cancel,
            "cancel_all":  self.timers.cancel_all,
            "cancel_matching": self.timers.cancel_matching,
            "find":        self.timers.find,
            "get":         self.timers.get,
            "running":     self.timers.running,
            "all":         self.timers.all_timers,
        })

        #Register API
        api_endpoint, registered_flag = self.client.API_REGISTRY.register(
            "corewidgetsbundle",
            "test",
            self.api_endpoint_test,
            False,
            False
        )

        self.client.API_REGISTRY.register(
            "corewidgetsbundle", "timer_start", self.api_timer_start,
            requires_auth=True, action="Start a 5 minute timer",
            description="Start a timer. seconds= or minutes=, optional name=.")
        self.client.API_REGISTRY.register(
            "corewidgetsbundle", "timer_list", self.api_timer_list,
            requires_auth=True,
            description="Every timer the panel is counting.")
        self.client.API_REGISTRY.register(
            "corewidgetsbundle", "timer_cancel", self.api_timer_cancel,
            requires_auth=True,
            description="Cancel one timer by key, or all of them.")
        self.client.API_REGISTRY.register(
            "corewidgetsbundle", "widget_show", self.api_widget_show,
            requires_auth=True,
            description="Place a transient widget on the home screen.")
        self.client.API_REGISTRY.register(
            "corewidgetsbundle", "widget_dismiss", self.api_widget_dismiss,
            requires_auth=True,
            description="Take a transient widget away again.")

        self.client.log("info", "[CoreWidgetsBundle] Loaded.")

    def reload(self, carryover: PluginCarryover = None):
        if carryover and carryover.has("was_on_plugin_page"):
            self.client.goto("#cwb_home_page", override=True)

    def unload(self, carryover: PluginCarryover = None):
        current_page = self.client.PAGE

        # First: it is subscribed to on_update and owns transient widgets on a
        # page this is about to stop owning. A handler left on the bus fires
        # into a module that is gone.
        if getattr(self, "timers", None) is not None:
            self.timers.stop_watching()

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

    ## TIMER API

    def api_timer_start(self, seconds=None, minutes=None, hours=None,
                        name: str = "", quadrant: str = "", x=None, y=None):
        """
        GET /public/timer_start?token=...&minutes=5&name=Eggs

        seconds, minutes and hours add up, so `minutes=90` and `hours=1&minutes=30`
        are the same request.
        """
        total = 0.0
        for value, scale in ((seconds, 1), (minutes, 60), (hours, 3600)):
            if value in (None, ""):
                continue
            try:
                total += float(value) * scale
            except (TypeError, ValueError):
                return {"request": "Failed",
                        "reason": f"'{value}' is not a number."}, 400
        if total <= 0:
            return {"request": "Failed",
                    "reason": "Pass seconds=, minutes= or hours=."}, 400

        center = None
        if x not in (None, "") and y not in (None, ""):
            try:
                center = (int(x), int(y))
            except (TypeError, ValueError):
                return {"request": "Failed", "reason": "x and y must be whole numbers."}, 400

        timer = self.timers.start(total, name=name, quadrant=quadrant, center=center)
        if timer is None:
            return {"request": "Failed", "reason": "Could not start that timer."}, 400
        return {"request": "Success", "timer": timer.as_dict()}, 200

    def api_timer_list(self):
        return {"request": "Success",
                "timers": [t.as_dict() for t in self.timers.all_timers()]}, 200

    def api_timer_cancel(self, key: str = "", all=None):
        if all not in (None, "") or key.strip().lower() == "all":
            return {"request": "Success", "cancelled": self.timers.cancel_all()}, 200
        if not key:
            return {"request": "Failed", "reason": "Pass key= or all=1."}, 400
        if not self.timers.cancel(key):
            return {"request": "Failed", "reason": f"No timer '{key}'."}, 404
        return {"request": "Success", "cancelled": 1}, 200

    ## TRANSIENT WIDGET API

    def _sub_home(self):
        entry = self.client.PAGES.get_entry("#cwb_home_page")
        if entry is None or getattr(entry, "instance", None) is None:
            return None
        return entry.instance.sub_page_dict.get("home")

    def api_widget_show(self, widget: str = "", quadrant: str = "",
                        x=None, y=None, timeout=0, **extra):
        """
        GET /public/widget_show?token=...&widget=sticky-note&quadrant=top-left&timeout=120

        `widget` is a KEY already registered on sub.home. Anything else in the
        query string is handed to the widget, so a note can arrive with its
        text on it.
        """
        sub_home = self._sub_home()
        if sub_home is None or not sub_home.has_feature("show_transient"):
            return {"request": "Failed",
                    "reason": "The home page is not on screen."}, 409

        key = (widget or "").strip()
        if not key:
            return {"request": "Failed", "reason": "Pass widget=<key>."}, 400

        framework = sub_home.features().widget_framework
        template = framework.registry.get(key) or framework.templates.get(key)
        if template is None:
            return {"request": "Failed",
                    "reason": f"No widget registered as '{key}'.",
                    "widgets": sorted(set(framework.registry) | set(framework.templates))}, 404

        center = None
        if x not in (None, "") and y not in (None, ""):
            try:
                center = (int(x), int(y))
            except (TypeError, ValueError):
                return {"request": "Failed", "reason": "x and y must be whole numbers."}, 400

        try:
            seconds = float(timeout or 0)
        except (TypeError, ValueError):
            seconds = 0.0

        done = {}

        def place():
            try:
                made = framework.make_transient(key, **extra)
                if made is None:
                    done["error"] = f"'{key}' could not be built."
                    return
                sub_home.features().show_transient(
                    made, center=center, quadrant=quadrant, timeout=seconds)
                done["key"] = made.KEY
            except Exception as e:
                done["error"] = str(e)

        self.client.call_on_ui(place)
        # Answered without waiting: this is a Flask worker and the UI thread
        # may be mid-frame. The caller gets the key it will have.
        return {"request": "Success", "widget": key,
                "quadrant": quadrant or "bottom-right",
                "timeout": seconds}, 200

    def api_widget_dismiss(self, key: str = "", all=None):
        sub_home = self._sub_home()
        if sub_home is None or not sub_home.has_feature("dismiss_transient"):
            return {"request": "Failed",
                    "reason": "The home page is not on screen."}, 409

        if all not in (None, ""):
            widgets = sub_home.features().transient_widgets()
            keys = [w.KEY for w in widgets]
            for k in keys:
                self.client.call_on_ui(lambda kk=k: sub_home.features().dismiss_transient(kk))
            return {"request": "Success", "dismissed": len(keys)}, 200

        if not key:
            return {"request": "Failed", "reason": "Pass key= or all=1."}, 400
        self.client.call_on_ui(lambda: sub_home.features().dismiss_transient(key))
        return {"request": "Success", "dismissed": 1}, 200

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
        sub_tiles.features().register_tile(WeatherTile, in_grid=False)


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