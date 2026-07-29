from src.mixins import mixin
from src.constants import get_data_dir, APP_NAME
from src.plugin.template import Plugin
from src.ui.icons import Icons
from src.plugin.carryover import PluginCarryover

from .widgets.cycling_background import CyclingBackground
from .widgets.datetime import DateTimeWidget
from .widgets.sticker import StickerWidget
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
        # Registered with an owner rather than assigned into a dict, so it
        # goes when this plugin does.
        self.client.API.register_api("corewidgetsbundle", "weather",
                                     OpenMeteoAPI(self, self.client))

        # Register pages owned by this plugin
        self.client.add_page("#cwb_home_page", "Home Page", HomePage, owner="corewidgetsbundle")
        self.client.DEFAULT_PAGE = "#cwb_home_page"

        # Stickers. The store is plain filesystem logic with no Qt in it, so
        # it is usable from the API thread as well as the UI.
        from .stickers import StickerStore
        sticker_dir = get_data_dir(APP_NAME) / "stickers"
        self.stickers = StickerStore(sticker_dir, log=self.client.log)
        self.client.public.expose("corewidgetsbundle", "stickers", {
            "store":  self.stickers,
            "list":   self.stickers.all_stickers,
            "search": self.stickers.search,
            "get":    self.stickers.get,
            "add":    self.stickers.add_bytes,
            "remove": self.stickers.remove,
            "dir":    sticker_dir,
        })
        # Reachable over the API, which is how a phone gets a sticker onto the
        # panel without a file share.
        # An Asset, not a str. register_asset stores whatever it is given
        # when forced_type is set, so a plain string registered fine and then
        # failed far away in the download route on `path / filename`.
        from src.enums import Asset
        sticker_asset = Asset(sticker_dir)
        sticker_asset.mark_uploadable()
        self.client.register_asset("stickers", sticker_asset, "FOLDER")

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
        api_endpoint, registered_flag = self.client.API.register(
            "corewidgetsbundle",
            "test",
            self.api_endpoint_test,
            False,
            False
        )

        self.client.API.register(
            "corewidgetsbundle", "timer_start", self.api_timer_start,
            requires_auth=True,
            description="Start a timer. seconds= or minutes=, optional name=.")
        # A page, not an index action. The action fired the endpoint with no
        # arguments at all, so the duration never arrived and the button's own
        # label was a promise nothing kept.
        self.client.API.register(
            "corewidgetsbundle", "timer_form", self.api_timer_form,
            requires_auth=True, gui="Start a timer",
            description="Choose a duration and start a timer.")
        self.client.API.register(
            "corewidgetsbundle", "timer_list", self.api_timer_list,
            requires_auth=True,
            description="Every timer the panel is counting.")
        self.client.API.register(
            "corewidgetsbundle", "timer_cancel", self.api_timer_cancel,
            requires_auth=True,
            description="Cancel one timer by key, or all of them.")
        self.client.API.register(
            "corewidgetsbundle", "sticker_add", self.api_sticker_add,
            requires_auth=True, accepts_files=True,
            gui="Stickers",
            description="Upload a sticker, or place one from the library.")
        self.client.API.register(
            "corewidgetsbundle", "sticker_list", self.api_sticker_list,
            requires_auth=True,
            description="Every sticker in the library.")
        self.client.API.register(
            "corewidgetsbundle", "sticker_place", self.api_sticker_place,
            requires_auth=True,
            description="Place a sticker. sticker=, quadrant=, mode=, timeout=.")
        self.client.API.register(
            "corewidgetsbundle", "sticker_remove", self.api_sticker_remove,
            requires_auth=True,
            description="Delete a sticker from the library.")

        self.client.API.register(
            "corewidgetsbundle", "widget_show", self.api_widget_show,
            requires_auth=True,
            description="Place a transient widget on the home screen.")
        self.client.API.register(
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

    def api_timer_form(self, hours=0, minutes=0, seconds=0, name: str = "",
                       quadrant: str = "", **_ignored):
        """The page a phone starts a timer from, and what it posts back to."""
        from .api.timer_page import render_page

        token = self._request_token()
        message, bad = "", False
        submitted = any(str(v or "").strip() not in ("", "0")
                        for v in (hours, minutes, seconds))

        if submitted:
            result, status = self.api_timer_start(
                seconds=seconds, minutes=minutes, hours=hours,
                name=name, quadrant=quadrant)
            if isinstance(result, dict) and result.get("request") == "Success":
                started = result.get("timer") or {}
                from .timers import describe
                label = started.get("name") or "Timer"
                length = describe(started.get("duration") or 0)
                message = f"Started {label} for {length}."
                # Cleared, so a reload does not start a second one.
                hours = minutes = seconds = 0
                name = ""
            else:
                bad = True
                message = (result.get("reason") if isinstance(result, dict)
                           else "That did not work.")

        running = []
        try:
            running = self.timers.running()
        except Exception:
            pass

        page = render_page(token, running, message=message, bad=bad, form={
            "hours": hours, "minutes": minutes, "seconds": seconds,
            "name": name, "quadrant": quadrant,
        })
        return page, 200, {"Content-Type": "text/html; charset=utf-8"}

    ## STICKER API

    def _request_token(self) -> str:
        try:
            from flask import request as _request
            return (_request.args.get("token")
                    or _request.headers.get("X-Client-Token") or "")
        except Exception:
            return ""

    def api_sticker_list(self, **_ignored):
        """GET /public/sticker_list?token=... - the library, as JSON."""
        return {"request": "Success",
                "directory": str(self.stickers.directory),
                "stickers": [s.as_dict()
                             for s in self.stickers.all_stickers(refresh=True)]}, 200

    def api_sticker_remove(self, key: str = "", **_ignored):
        if not key:
            return {"request": "Failed", "reason": "Pass key=<filename>."}, 400
        if not self.stickers.remove(key):
            return {"request": "Failed", "reason": f"No sticker '{key}'."}, 404
        return {"request": "Success", "removed": key}, 200

    def api_sticker_add(self, sticker: str = "", quadrant: str = "center",
                        mode: str = "permanent", timeout=0, x=None, y=None,
                        scale="1", size=0, delete_after=None, remove: str = "",
                        files=None, **_ignored):
        """
        The page a phone does this from, and the actions it posts back.

        One endpoint for both: a form that posts to the address it was served
        from needs no second URL, and a bookmarked page can do the whole job.

        `files` only arrives because this endpoint registered with
        accepts_files - see APIRegistry.
        """
        from .api.sticker_page import render_page

        token = self._request_token()
        message, bad = "", False

        # An upload. Validated by the store, which is where the rules about
        # size, type and overwriting already live.
        if files is not None:
            upload = None
            try:
                upload = files.get("file")
            except Exception:
                upload = None
            if upload is None or not getattr(upload, "filename", ""):
                message, bad = "No file arrived.", True
            else:
                data = upload.read()
                made, reason = self.stickers.add_bytes(upload.filename, data)
                if made is None:
                    message, bad = reason, True
                else:
                    message = f"Added {made.label}. Pick it below to place it."

        # A deletion, from the page's Remove button.
        elif remove:
            entry = self.stickers.get(remove)
            if entry is None:
                message, bad = f"There is no sticker called '{remove}'.", True
            elif self.stickers.remove(remove):
                message = f"Deleted {entry.label}."
                sticker = ""
            else:
                message, bad = f"Could not delete {entry.label}.", True

        # A placement.
        elif sticker:
            placed, reason = self._place_sticker(
                sticker, quadrant=quadrant, mode=mode, timeout=timeout,
                x=x, y=y, scale=scale, size=size, delete_after=delete_after)
            message, bad = reason, not placed

        stickers = self.stickers.all_stickers(refresh=True)
        # Handed back what was submitted, so the quadrant, size and duration
        # survive a placement rather than resetting on every one.
        page = render_page(token, stickers, message=message, bad=bad, form={
            "sticker":  sticker,
            "quadrant": quadrant,
            "mode":     mode,
            "timeout":  timeout,
            "scale":    scale,
            "size":     size,
            "delete_after": delete_after,
        })
        return page, 200, {"Content-Type": "text/html; charset=utf-8"}

    def api_sticker_place(self, sticker: str = "", quadrant: str = "center",
                          mode: str = "permanent", timeout=0, x=None, y=None,
                          scale="1", size=0, delete_after=None, **_ignored):
        """The same placement, as JSON, for a script rather than a person."""
        if not sticker:
            return {"request": "Failed", "reason": "Pass sticker=<filename>."}, 400
        placed, reason = self._place_sticker(
            sticker, quadrant=quadrant, mode=mode, timeout=timeout,
            x=x, y=y, scale=scale, size=size, delete_after=delete_after)
        if not placed:
            return {"request": "Failed", "reason": reason}, 400
        return {"request": "Success", "sticker": sticker, "detail": reason}, 200

    @staticmethod
    def _longest_side(scale="1", size=0) -> int:
        """
        The starting size in pixels, from either control.

        `size` wins when given, because "exact size" is a more specific answer
        than a multiplier. Both are clamped: the page is HTML anyone can post
        to, and a sticker 9000px across is not a sticker.
        """
        from .widgets.sticker import StickerWidget
        try:
            exact = int(float(size or 0))
        except (TypeError, ValueError):
            exact = 0
        if exact > 0:
            return max(StickerWidget.MIN_W, min(StickerWidget.MAX_W, exact))
        try:
            factor = float(scale or 1)
        except (TypeError, ValueError):
            factor = 1.0
        factor = max(0.1, min(6.0, factor))
        return max(StickerWidget.MIN_W,
                   min(StickerWidget.MAX_W,
                       int(StickerWidget.DEFAULT_SIDE * factor)))

    def _place_sticker(self, name: str, quadrant: str = "center",
                       mode: str = "permanent", timeout=0, x=None, y=None,
                       scale="1", size=0, delete_after=None):
        """
        Put a sticker on the home screen. Returns (ok, message).

        Permanent stickers go through the same path the widgets panel uses, so
        they are saved in the layout and come back after a restart. Temporary
        ones go through the transient API, which deliberately never persists.
        """
        entry = self.stickers.get(name)
        if entry is None:
            return False, f"There is no sticker called '{name}'."
        if entry.kind == "video":
            return False, ("Video stickers cannot be shown on the panel yet - "
                           "only images and GIFs.")

        sub_home = self._sub_home()
        if sub_home is None or not sub_home.has_feature("widget_framework"):
            return False, "The home page is not on screen."

        framework = sub_home.features().widget_framework
        temporary = str(mode or "").strip().lower() in ("temporary", "temp", "1", "true")

        seconds = 0.0
        if temporary:
            try:
                seconds = max(0.0, float(timeout or 0))
            except (TypeError, ValueError):
                seconds = 0.0

        center = None
        if x not in (None, "") and y not in (None, ""):
            try:
                center = (int(x), int(y))
            except (TypeError, ValueError):
                return False, "x and y must be whole numbers."

        longest = self._longest_side(scale, size)
        # Only ever for a temporary sticker. A permanent one deleting its own
        # source would break every other copy of it on the screen.
        wipe = temporary and str(delete_after or "").strip().lower() in (
            "1", "true", "on", "yes")

        def place():
            try:
                if temporary:
                    widget = framework.make_transient(
                        "sticker", sticker=name, longest_side=longest,
                        delete_after=wipe)
                    if widget is None:
                        return
                    sub_home.features().show_transient(
                        widget, center=center, quadrant=quadrant,
                        timeout=seconds,
                        on_expired=widget.delete_source if wipe else None)
                else:
                    widget = framework._make_copy("sticker", sticker=name,
                                                  longest_side=longest)
                    if widget is None:
                        return
                    # Placed where it was asked for rather than at the default
                    # anchor, so the quadrant means something for a permanent
                    # sticker too.
                    point = framework._transient_position(
                        widget, center, quadrant, bundle=False)
                    widget.float_x, widget.float_y = point
                    widget.move(*point)
                    framework.schedule_save()
            except Exception as e:
                self.client.log("warning",
                                f"[Stickers] Could not place '{name}': {e}",
                                include_traceback=True)

        # A Flask worker; building and placing a widget is the UI thread's.
        self.client.call_on_ui(place)

        where = f"in the {quadrant.replace('-', ' ')}" if quadrant else ""
        also = " The file goes with it." if wipe else ""
        if temporary and seconds:
            return True, f"Placed {entry.label} {where} for {int(seconds)}s.{also}"
        if temporary:
            return True, f"Placed {entry.label} {where} until you remove it.{also}"
        return True, f"Placed {entry.label} {where}."


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
            # Opens a panel of its own, which this one would otherwise be
            # sitting on top of.
            closes_panel = True,
        )

        # Registered, not constructed-and-added: the saved layout decides what
        # sits on the page and what waits in the widgets panel, the same way
        # sub.tiles registers tiles.
        register = sub_home.features().register_widget

        # StickyNote is a template: register() returns None for those, since
        # the panel offers it and each Add makes a copy with its own key.
        register(StickyNote)
        # The same, except Add asks which image first - see
        # StickerWidget.choose_before_add.
        register(StickerWidget)

        self.client.public.cwb_widgets["sub.home"] = [
            w for w in (
                # Always present; carries notifications and the panel button.
                register(ConfigurationBar),
                register(DateTimeWidget, show_date=True, show_time=True),
                register(WeatherWidget),
            ) if w is not None
        ]