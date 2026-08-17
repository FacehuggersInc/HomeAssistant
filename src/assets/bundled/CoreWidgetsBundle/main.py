import time
from src.mixins import mixin
from src.constants import get_data_dir, APP_NAME
from src.plugin.template import Plugin
from src.ui.icons import Icons
from src.plugin.carryover import PluginCarryover

from .widgets.cycling_background import CyclingBackground
from .widgets.datetime import DateTimeWidget
from .widgets.bookmark import BookmarkWidget
from .widgets.checklist import ChecklistWidget
from .widgets.sticker import StickerWidget
from .widgets.weather import WeatherWidget
from .widgets.configuration_bar import ConfigurationBar
from .widgets.sticky_note import StickyNote
from .widgets.tiles.bookmark_tile import BookmarkTile
from src.assets.bundled.CoreWidgetsBundle.widgets.tiles.action_tile import ActionTile
from .widgets.tiles.clock_tile import ClockTile
from .widgets.tiles.weather_tile import WeatherTile
from .widgets.tiles.sun_tile import SunTile
from .widgets.weather_event import (
    WeatherEventTile, WeatherEventWidget)
from .widgets.tiles.switch_tiles import DEFAULT_TILES
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
            # Putting one on the home screen, for anything that makes a
            # sticker rather than only reading them - see the whiteboard. The
            # alternative is reaching for this plugin's instance, which is the
            # route around the registry the registry exists to prevent.
            "place":  self._place_sticker,
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
        sticker_asset.mark_deletable()
        self.client.register_asset("stickers", sticker_asset, "FOLDER")

        # Timers. The service owns the countdowns; the widget only draws one.
        from .timers import TimerService
        self.timers = TimerService(self)
        self.timers.start_watching()

        # Declared before anything can subscribe - subscribe_to_event indexes
        # straight into the event table, so a name that has not been created
        # is a KeyError rather than a quiet no-op.
        for name in ("on_timer_finished",
                     # Nobody acknowledged it and it cleared itself. Apart
                     # from `on_timer_finished`, which says a timer RANG -
                     # this says the room was empty when it did, which is a
                     # different thing to know and the only one a listener
                     # can act on afterwards.
                     "on_timer_timed_out",
                     # Stopped before it ever finished.
                     "on_timer_cancelled"):
            if name not in self.client.EVENTS["on_call"]:
                self.client.create_on_call_event(name)

        # Alarms. A wall clock time rather than a countdown - see alarms.py
        # for the three ways that makes it a different thing.
        from .alarms import AlarmService
        self.alarms = AlarmService(self)
        self.alarms.start_watching()

        for name in ("on_alarm_fired",
                     # Rang its full length with nobody answering.
                     "on_alarm_timed_out",
                     # Somebody answered it - tapped it, or said stop.
                     "on_alarm_dismissed"):
            if name not in self.client.EVENTS["on_call"]:
                self.client.create_on_call_event(name)

        from src.assets.bundled.CoreWidgetsBundle.timers import describe

        self.client.public.expose("corewidgetsbundle", "timers", {
            # A duration as a noun phrase - "half an hour", "ninety seconds".
            # Exposed so a plugin reading a timer can also say it, without
            # importing this module to get one function.
            "describe":    describe,
            "start":       self.timers.start,
            "cancel":      self.timers.cancel,
            "cancel_all":  self.timers.cancel_all,
            "cancel_matching": self.timers.cancel_matching,
            "find":        self.timers.find,
            "get":         self.timers.get,
            "running":     self.timers.running,
            "all":         self.timers.all_timers,
        })

        from src.assets.bundled.CoreWidgetsBundle.alarms import (
            clock_text, describe_alarm)

        self.client.public.expose("corewidgetsbundle", "alarms", {
            # Said the way somebody would, so a plugin reading an alarm can
            # also announce it without importing this module.
            "clock_text":      clock_text,
            "describe":        describe_alarm,
            "schedule":        self.alarms.schedule,
            "cancel":          self.alarms.cancel,
            "cancel_all":      self.alarms.cancel_all,
            "cancel_matching": self.alarms.cancel_matching,
            "find":            self.alarms.find,
            "get":             self.alarms.get,
            "scheduled":       self.alarms.scheduled,
            "ringing":         self.alarms.ringing,
            "silence":         self.alarms.silence,
        })

        # "Stop" silences a ringing alarm, and only while one is ringing.
        #
        # A higher priority than the music: an alarm is the panel demanding
        # attention, so it is what "stop" means while it is going off.
        # `stops_listening` is False because somebody silencing an alarm at
        # seven in the morning is quite likely to ask for something next.
        self.client.CANCEL.register(
            "corewidgetsbundle", "stop_alarm",
            keywords=["stop", "stop it", "stop the alarm", "silence",
                      "silence the alarm", "turn it off", "turn off the alarm",
                      "shut up", "be quiet", "quiet", "enough", "ok ok",
                      "alright", "i'm up", "im up", "wake up", "snooze off",
                      "dismiss", "dismiss it", "cancel it"],
            handler=lambda: self.alarms.silence(),
            is_active=lambda: bool(self.alarms.ringing()),
            priority=40,
            description="silence the alarm",
            stops_listening=False,
        )

        api_endpoint, registered_flag = self.client.API.register(
            "corewidgetsbundle",
            "test",
            self.api_endpoint_test,
            False,
            False
        )

        self.client.API.register(
            "corewidgetsbundle", "note_add", self.api_note_add,
            requires_auth=True,
            gui="Sticky note", icon="message-text",
            description="Put a note on the panel from anywhere.")
        self.client.API.register(
            "corewidgetsbundle", "list_add", self.api_list_add,
            requires_auth=True,
            gui="Checklist", icon="check-network",
            description="Start a list, or add to one that is already up.")
        self.client.API.register(
            "corewidgetsbundle", "timer_start", self.api_timer_start,
            requires_auth=True,
            description="Start a timer. seconds= or minutes=, optional name=.")
        # A page, not an index action. The action fired the endpoint with no
        # arguments at all, so the duration never arrived and the button's own
        # label was a promise nothing kept.
        self.client.API.register(
            "corewidgetsbundle", "timer_form", self.api_timer_form,
            requires_auth=True, gui="Start a timer", icon="timer-sand",
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
            gui="Stickers", icon="sticker-emoji",
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

    def _open_timers(self) -> None:
        if not self.client.public.has("timers"):
            self.client.simple_notify("mdi.timer-off-outline", "Timers",
                                      "The timer service is not available.")
            return
        from .widgets.schedule_lists import TimersDialog
        self.client.dialog(TimersDialog(self.client))

    def _open_alarms(self) -> None:
        if not self.client.public.has("alarms"):
            self.client.simple_notify("mdi.alarm-off", "Alarms",
                                      "The alarm service is not available.")
            return
        from .widgets.schedule_lists import AlarmsDialog
        self.client.dialog(AlarmsDialog(self.client))

    def unload(self, carryover: PluginCarryover = None):
        current_page = self.client.PAGE

        # First: it is subscribed to on_update and owns transient widgets on a
        # page this is about to stop owning. A handler left on the bus fires
        # into a module that is gone.
        if getattr(self, "timers", None) is not None:
            self.timers.stop_watching()
        if getattr(self, "alarms", None) is not None:
            self.alarms.stop_watching()
        # The cancel entry goes with it. Left registered, "stop" would call a
        # handler bound to a service that has stopped watching the clock.
        try:
            self.client.CANCEL.unregister("corewidgetsbundle", "stop_alarm")
        except Exception:
            pass

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
        # Dismissable by pressing beside it as well as by the timer below -
        # fifteen seconds is a long time to look at a test panel you have
        # finished with.
        panel = self.client.create_panel(on_created=self.panel_callback,
                                         dismiss_on_outside_click=True)
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
        render_page = self.sibling("api.timer_page").render_page

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
                        scale="normal", size=0, delete_after=None, remove: str = "",
                        files=None, **_ignored):
        """
        The page a phone does this from, and the actions it posts back.

        One endpoint for both: a form that posts to the address it was served
        from needs no second URL, and a bookmarked page can do the whole job.

        `files` only arrives because this endpoint registered with
        accepts_files - see APIRegistry.
        """
        render_page = self.sibling("api.sticker_page").render_page

        token = self._request_token()
        message, bad = "", False

        # An upload. Validated by the store, which is where the rules about
        # size, type and overwriting already live.
        if files is not None:
            # Every file under the field, not just the first.
            #
            # A file input with `multiple` posts them all under the same name,
            # and getlist is how they come back. Reading only `get("file")`
            # took the first and silently dropped the rest - which looks like
            # the upload half-working.
            uploads = []
            try:
                uploads = list(files.getlist("file"))
            except AttributeError:
                one = files.get("file")
                uploads = [one] if one is not None else []
            except Exception:
                uploads = []
            uploads = [u for u in uploads if getattr(u, "filename", "")]

            if not uploads:
                message, bad = "No file arrived.", True
            else:
                added, failed = [], []
                for upload in uploads:
                    try:
                        data = upload.read()
                    except Exception as e:
                        failed.append(f"{upload.filename}: {e}")
                        continue
                    made, reason = self.stickers.add_bytes(upload.filename, data)
                    if made is None:
                        failed.append(f"{upload.filename}: {reason}")
                    else:
                        added.append(made.label)

                # Both halves reported. One bad file in a batch of ten should
                # not read as the whole upload having failed, and should not
                # pass silently either.
                parts = []
                if added:
                    parts.append(f"Added {len(added)}: " + ", ".join(added[:6])
                                 + ("..." if len(added) > 6 else ""))
                if failed:
                    parts.append(f"{len(failed)} refused - " + "; ".join(failed[:3])
                                 + ("..." if len(failed) > 3 else ""))
                message = " ".join(parts) or "Nothing was added."
                bad = bool(failed and not added)

        # Take them off the screen, from the page's Clear button.
        #
        # Off the SCREEN, not out of the library. Deleting the files is what
        # Remove does, one at a time and deliberately; this is for a home page
        # that has accumulated a dozen cats.
        if str(_ignored.get("clear_placed") or "").strip().lower() in (
                "1", "true", "yes", "on"):
            gone = self._clear_placed_stickers()
            message = (f"Cleared {gone} sticker{'s' if gone != 1 else ''} "
                       f"from the home page. The library is untouched."
                       if gone else "There were none on the home page.")
            bad = False

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

    ## -- notes and lists, from anywhere

    def _framework(self):
        """The home page's widget framework, or None when it is not up."""
        from .homepage import widget_framework
        return widget_framework(self.client)

    def _place(self, template_key: str, state: dict, at: str = "",
               key: str = None):
        """
        Put a widget on the home page and save it there.

        Through the framework's public create/place pair, which is the same
        path the widgets panel takes. `at` is one of the nine positions and is
        honoured whether the widget floats or anchors - which is the whole
        reason the position controls on these pages do anything.
        """
        framework = self._framework()
        if framework is None:
            return None

        widget = framework.create(template_key, key=key, state=state)
        if widget is None:
            return None
        framework.place(widget, at=at)
        framework.save_layout()
        return widget

    def _lists(self) -> list:
        """
        Every checklist ON the home page, newest last.

        Placed ones only. The delete handle files a widget into the widgets
        panel rather than destroying it - it keeps its text, so dragging it
        back out restores it exactly - which leaves the instance in the
        registry with `placed` false. Offering one of those here means editing
        a list nobody can see, and a list taken off the wall reads as gone.

        A restart already agrees: an entry saved as unplaced is not rebuilt.
        """
        framework = self._framework()
        if framework is None:
            return []

        lists = []
        for key, widget in list(framework.registry.items()):
            if not (getattr(widget, "template_key", "") == "checklist"
                    or key == "checklist" or key.startswith("checklist-")):
                continue
            try:
                if not widget.placed:
                    continue
            except RuntimeError:
                # The C++ object went without the entry going with it.
                continue
            lists.append((key, widget))
        return lists

    def api_note_add(self, text: str = None, colour: str = "",
                     quadrant: str = "top-right", **_ignored):
        """
        A sticky note, from a phone.

        GET  -> the form
        POST -> writes the note and puts it on the panel
        """
        render_page = self.sibling("api.note_page").render_page
        token = self._request_token()
        message, bad = "", False

        typed = text is not None
        text = str(text or "").strip()
        colour = str(colour or "").strip()

        from src.ui.widget import normalise_position, POSITION_LABELS
        position = normalise_position(quadrant)

        if text:
            # `placed` too, or the widget is registered and never shown.
            state = {"text": text, "placed": True}
            if colour:
                state["colour"] = colour

            # The position is passed to place(), not written into the state.
            # A sticky note floats, so an `anchor` in its state is recorded and
            # never read - which is why choosing a corner used to do nothing.
            self.client.call_on_ui(
                lambda: self._place("sticky-note", state, at=position))
            message = (f"\u201c{text[:40]}\u201d is on the panel, "
                       f"{POSITION_LABELS[position].lower()}.")
        elif typed:
            message, bad = "Type something first.", True

        from .widgets.sticky_note import COLOURS
        return render_page(token, colours=COLOURS, message=message, bad=bad,
                           kind="note", quadrant=position)

    def _list_options(self) -> list:
        """Every checklist on the panel as (key, title, text) for the page."""
        return [(key, getattr(widget, "title", key) or key,
                 ChecklistWidget.serialise(getattr(widget, "items", [])))
                for key, widget in self._lists()]

    def api_list_add(self, title: str = None, text: str = None,
                     colour: str = "", target: str = "",
                     quadrant: str = "top-right", **_ignored):
        """
        A checklist, edited from a phone.

        GET  -> the editor, opened on nothing
        POST -> writes one list and reopens the editor on it

        The chooser decides which list; the page decides what is on it. A new
        list is not built until it is put up, and from then on the same page
        is editing the widget it just made - so adding one item and then
        another is one page, not two visits.
        """
        render_page = self.sibling("api.list_page").render_page
        token = self._request_token()

        from src.ui.widget import normalise_position, POSITION_LABELS
        position = normalise_position(quadrant)

        # A submission always posts both fields, so their presence - not their
        # emptiness - is what separates it from somebody opening the page.
        submitted = title is not None or text is not None
        wanted_title = str(title or "").strip()
        items = ChecklistWidget._parse(text or "")
        colour = str(colour or "").strip()
        target = str(target or "").strip()
        message, bad = "", False
        # Whether this request handed a write to the UI thread. The response
        # renders that list from what arrived rather than from the widget.
        wrote = False

        if submitted and not wanted_title and not items:
            message, bad = "Give it a name, or something to put on it.", True

        elif submitted and target:
            widget = dict(self._lists()).get(target)
            if widget is None:
                # It was removed on the panel while this page was open. Said
                # plainly and the chooser falls back, rather than writing the
                # edit into nothing and reporting success.
                message, bad = ("That list is not on the panel any more.", True)
                target = ""
            else:
                # REPLACED, not appended. The form arrives holding what the
                # list already had, so appending would double every line
                # nobody deleted. What comes back is the list they meant.
                def rewrite(w=widget, new=items, name=wanted_title,
                            tint=colour):
                    w.items = [dict(entry) for entry in new]
                    if name:
                        w.title = name
                    if tint:
                        w.colour = tint
                    w.update()
                    w._save()
                self.client.call_on_ui(rewrite)
                wrote = True
                message = (f"\u201c{wanted_title or widget.title}\u201d saved "
                           f"with {len(items)} item(s).")

        elif submitted:
            framework = self._framework()
            if framework is None:
                message, bad = "The home page is not on screen.", True
            else:
                # Named before it is built. Creating a widget is the UI
                # thread's job and call_on_ui does not report back, so without
                # this the page could not say which list it had just made -
                # and could not go on to edit it.
                target = framework.reserve_key("checklist")
                state = {"title": wanted_title or "Checklist", "items": items,
                         "placed": True}
                if colour:
                    state["colour"] = colour
                self.client.call_on_ui(
                    lambda k=target, s=state: self._place(
                        "checklist", s, at=position, key=k))
                wrote = True
                message = (f"\u201c{state['title']}\u201d is on the panel, "
                           f"{POSITION_LABELS[position].lower()}. "
                           f"You are editing it now.")

        options = self._list_options()

        # The list that was just written is rendered from what arrived, never
        # from the widget.
        #
        # Writing it is the UI thread's job and call_on_ui does not wait, so
        # reading the widget here reads the state from BEFORE this request.
        # The page would then show the previous contents, the next save would
        # post those back, and the two would trade places on every press.
        if wrote and target:
            written = (target, wanted_title or "Checklist",
                       ChecklistWidget.serialise(items))
            merged, replaced = [], False
            for row in options:
                if row[0] == target:
                    merged.append(written)
                    replaced = True
                else:
                    merged.append(row)
            if not replaced:
                # A list this request created. The widget does not exist yet,
                # so there is nothing in `options` to replace.
                merged.append(written)
            options = merged

        from .widgets.checklist import COLOURS
        return render_page(token, colours=COLOURS, message=message, bad=bad,
                           lists=options, target=target, quadrant=position)

    def _clear_placed_stickers(self) -> int:
        """
        Remove every sticker from the home page, and say how many.

        The library is not touched. A sticker on screen is a **copy** of a
        library entry, keyed `sticker-1`, `sticker-2` and so on, so removing
        the copies leaves the files exactly where they were.

        Transient stickers are included: one placed with a timeout that has not
        expired is still on the page, and "clear" that leaves something on the
        page is not what the button says.
        """
        framework = self._framework()
        if framework is None:
            return 0

        # Placed ones, which is what the button says. A sticker filed into the
        # widgets panel is not on the home page, and taking it out of the
        # registry loses a copy somebody deliberately put away.
        keys = []
        for key, widget in list(framework.registry.items()):
            if not isinstance(widget, StickerWidget):
                continue
            try:
                if not widget.placed:
                    continue
            except RuntimeError:
                continue
            keys.append(key)
        gone = 0
        for key in keys:
            try:
                framework.remove(key)
                gone += 1
            except Exception as e:
                self.client.log("warning",
                                f"[Stickers] Could not clear '{key}': {e}")
        if gone:
            framework.save_layout()
        return gone

    def api_sticker_place(self, sticker: str = "", quadrant: str = "center",
                          mode: str = "permanent", timeout=0, x=None, y=None,
                          scale="normal", size=0, delete_after=None, **_ignored):
        """The same placement, as JSON, for a script rather than a person."""
        if not sticker:
            return {"request": "Failed", "reason": "Pass sticker=<filename>."}, 400
        placed, reason = self._place_sticker(
            sticker, quadrant=quadrant, mode=mode, timeout=timeout,
            x=x, y=y, scale=scale, size=size, delete_after=delete_after)
        if not placed:
            return {"request": "Failed", "reason": reason}, 400
        return {"request": "Success", "sticker": sticker, "detail": reason}, 200

    #What each name means, as a share of the panel's width. A share rather
    #than a pixel count so the words mean the same thing on any panel.
    SIZE_SHARES = {
        "small":    0.08,
        "normal":   0.16,
        "large":    0.30,
        "huge":     0.50,
        "enormous": 0.75,
    }

    def _longest_side(self, scale="normal", size=0) -> int:
        """
        The starting size in pixels, from either control.

        `scale` is a **share of the panel's width**, not a multiplier on a
        fixed number. A multiplier means whatever the panel happens to be:
        "huge" as 2x180px is 360px, which on a 2560px screen is a seventh of
        the width - indistinguishable from "large", and nothing like the word.

        `size` wins when given, because "exact size" is a more specific answer
        than a share. Both are clamped: the page is HTML anyone can post to,
        and a sticker 9000px across is not a sticker.
        """
        from .widgets.sticker import StickerWidget

        name = str(scale or "normal").strip().lower()

        # An exact size belongs to the "custom" choice and is only read for it.
        #
        # Not "whenever a number arrives": the page's pixel field is hidden with
        # display:none when another size is chosen, and a field hidden that way
        # is still submitted. Reading it whenever it had a value meant its
        # default of 180 won every time and the size choice did nothing at all.
        if name == "custom":
            try:
                exact = int(float(size or 0))
            except (TypeError, ValueError):
                exact = 0
            if exact > 0:
                return max(StickerWidget.MIN_W,
                           min(StickerWidget.MAX_W, exact))
            # Asked for an exact size without giving one.
            name = "normal"
        share = self.SIZE_SHARES.get(name)

        if share is None:
            # A number. Still the old multiplier on DEFAULT_SIDE, because that
            # is what any existing link or script means by it - the two ranges
            # overlap, so guessing would silently change what those do.
            try:
                factor = float(name)
            except (TypeError, ValueError):
                factor = 1.0
            factor = max(0.1, min(6.0, factor))
            return max(StickerWidget.MIN_W,
                       min(StickerWidget.MAX_W,
                           int(StickerWidget.DEFAULT_SIDE * factor)))

        return max(StickerWidget.MIN_W,
                   min(StickerWidget.MAX_W,
                       int(self._screen_width() * share)))

    def _screen_width(self) -> int:
        try:
            host = self.client.OVERLAYS
            if host is not None and host.width() > 0:
                return host.width()
        except Exception:
            pass
        return 1280

    def _place_sticker(self, name: str, quadrant: str = "center",
                       mode: str = "permanent", timeout=0, x=None, y=None,
                       scale="normal", size=0, delete_after=None):
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

        framework = self._framework()
        if framework is None:
            return False, "The home page is not on screen."

        # Asked here, where the answer can still be returned to whoever
        # asked. The build itself belongs to the UI thread and this function
        # returns before it runs, so anything checkable on this side must be
        # checked on this side or the caller is told a placement happened
        # that has not been attempted yet.
        if "sticker" not in framework.templates:
            return False, ("The sticker widget is not registered on the home "
                           "page, so there is nothing to place it with.")

        # `quadrant` stays the name on the wire - it is in bookmarks and
        # scripts - but it is one of the nine positions from here on.
        from src.ui.widget import normalise_position, POSITION_LABELS
        position = normalise_position(quadrant, "center")
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
                # One path for both. Temporary and permanent differ in whether
                # the widget is written to the layout and whether it expires -
                # not in how it is built or where it goes.
                widget = framework.create(
                    "sticker", transient=temporary, sticker=name,
                    longest_side=longest,
                    **({"delete_after": wipe} if temporary else {}))
                if widget is None:
                    # create() has already said why - a missing template or a
                    # constructor that raised. This says which sticker went
                    # nowhere because of it, since the caller was told the
                    # placement succeeded before this ever ran.
                    self.client.log(
                        "error",
                        f"[Stickers] '{name}' was not placed: the home page's "
                        f"widget framework would not build a sticker widget.")
                    return

                if temporary:
                    framework.show_transient(
                        widget, center=center, at=position, timeout=seconds,
                        on_expired=widget.delete_source if wipe else None)
                else:
                    framework.place(widget, at=position, exact=center)
                    framework.schedule_save()
            except Exception as e:
                self.client.log("warning",
                                f"[Stickers] Could not place '{name}': {e}",
                                include_traceback=True)

        # A Flask worker; building and placing a widget is the UI thread's.
        self.client.call_on_ui(place)

        where = f"in the {POSITION_LABELS[position].lower()}"
        also = " The file goes with it." if wipe else ""
        if temporary and seconds:
            return True, f"Placed {entry.label} {where} for {int(seconds)}s.{also}"
        if temporary:
            return True, f"Placed {entry.label} {where} until you remove it.{also}"
        return True, f"Placed {entry.label} {where}."


    #How long a bookmark's transient widget stays once it is finally shown.
    BOOKMARK_SHOW_SECONDS = 8.0
    #And how long a queued one is worth showing at all. Coming back to the home
    #page an hour later, a card announcing something long forgotten is clutter
    #rather than an acknowledgement.
    BOOKMARK_QUEUE_LIFE = 600.0

    #A bookmark waiting for the home page to come back, as (url, when).
    #
    #Class level so it exists before __init__ has run: on_web_event can arrive
    #during startup if a page restores itself, and getattr with a default
    #hides the mistake rather than preventing it.
    _pending_bookmark = None

    def _on_web_event(self, payload=None) -> None:
        """
        A page was bookmarked, so show it - when there is somewhere to show it.

        The transient widget is the acknowledgement: pressing a star in a
        toolbar gives no sign anything happened once the page is closed, and a
        notification for something this small is more interruption than it is
        worth.

        Queued rather than placed, because bookmarking happens ON THE WEB PAGE.
        The home page is not on screen at that moment, so a transient placed
        there with an eight second life expires unseen - which is exactly what
        it did.
        """
        if not isinstance(payload, dict):
            return
        if payload.get("kind") != "bookmarked":
            return
        url = str(payload.get("url") or "")
        if not url:
            return

        self._pending_bookmark = (url, time.time())
        # Already home? Then there is nothing to wait for.
        try:
            on_home = (self.client.PAGE is not None
                       and self.client.PAGE.name == "#cwb_home_page")
        except Exception:
            on_home = False
        if on_home:
            self._show_pending_bookmark()

    def _on_visit(self, event=None) -> None:
        """Show anything that was waiting for the home page to come back."""
        try:
            name = (event or {}).get("page", {}).get("name")
        except Exception:
            name = None
        if name == "#cwb_home_page":
            self._show_pending_bookmark()

    def _show_pending_bookmark(self) -> None:
        pending = getattr(self, "_pending_bookmark", None)
        if not pending:
            return
        url, saved_at = pending
        self._pending_bookmark = None

        if time.time() - saved_at > self.BOOKMARK_QUEUE_LIFE:
            return

        sub_home = self._sub_home()
        if sub_home is None or not sub_home.has_feature("widget_framework"):
            return
        framework = sub_home.features().widget_framework

        def place():
            try:
                widget = framework.make_transient("bookmark", url=url)
                if widget is None:
                    return
                sub_home.features().show_transient(
                    widget, quadrant="top-right",
                    timeout=self.BOOKMARK_SHOW_SECONDS)
            except Exception as e:
                self.client.log("debug",
                                f"[Bookmarks] Could not show it: {e}")

        self.client.call_on_ui(place)

    def _toggle_mute(self) -> None:
        """
        Flip the mute, and say when do not disturb is what is silencing it.

        Turning "Silence" off while do not disturb is on would otherwise do
        nothing audible and look broken - the panel stays quiet, because the
        other mode is still holding it.
        """
        if self.client.do_not_disturb():
            self.client.simple_notify(
                Icons.DO_NOT_DISTURB, "Silence",
                "Do not disturb is on, so the panel is quiet either way.",
                history=False, urgent=True)
            return
        self.client.set_sounds_muted(not self.client.sounds_muted())

    def _sub_home(self):
        from .homepage import sub_home
        return sub_home(self.client)

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
        sub_tiles.features().register_tile(SunTile, in_grid=False)
        sub_tiles.features().register_tile(WeatherEventTile,
                                          in_grid=False)
        # The panel's own switches. Only Core Widgets' - a plugin that wants
        # a tile for its own registers one itself, which is what keeps this
        # from becoming the place every plugin's UI lives.
        for tile_class in DEFAULT_TILES:
            sub_tiles.features().register_tile(tile_class, in_grid=False)
        sub_tiles.features().register_tile(BookmarkTile, in_grid=False)
        sub_tiles.features().register_tile(ActionTile, in_grid=False)


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
        # Two quiet modes, side by side.
        #
        # The state lives on the client and these only toggle it - a button
        # holding its own copy is a button that disagrees with the setting page
        # the moment either is used.
        # Straight to the browser's home, which is the bookmark grid.
        self.client.QUICK.register(
            "corewidgetsbundle", "web_home", "Web", Icons.EARTH,
            on_press = lambda: self.client.goto(
                "#webpage", data={"url": self.client.BOOKMARKS_HOME},
                override=True),
            order    = 15,
            # It navigates, so the panel goes with it rather than sitting over
            # the page it just opened.
            closes_panel = True)

        # Two entries, not one with tabs. A timer and an alarm answer
        # different questions - "how long left" against "what time" - and
        # somebody opening this already knows which they meant.
        self.client.QUICK.register(
            "corewidgetsbundle", "timers", "Timers", "mdi.timer-outline",
            on_press = self._open_timers,
            order    = 16,
            closes_panel = True)

        self.client.QUICK.register(
            "corewidgetsbundle", "alarms", "Alarms", "mdi.alarm",
            on_press = self._open_alarms,
            order    = 17,
            closes_panel = True)

        self.client.QUICK.register(
            "corewidgetsbundle", "do_not_disturb", "Do not disturb",
            Icons.DO_NOT_DISTURB,
            on_press = lambda: self.client.set_do_not_disturb(
                not self.client.do_not_disturb()),
            on_state = lambda: self.client.do_not_disturb(),
            order    = 20)

        self.client.QUICK.register(
            "corewidgetsbundle", "mute_sounds", "Silence",
            Icons.VOLUME_OFF,
            on_press = self._toggle_mute,
            # Shown as on while do not disturb is holding it, since it IS
            # silent - a button reading off next to a panel making no noise is
            # the button being wrong.
            on_state = lambda: self.client.sounds_muted(),
            order    = 21)

        # Only where there is a mixer to do it with. A button that cannot
        # mute anything is worse than no button: it looks like the microphone
        # is off.
        if self.client.mic_mute_available():
            self.client.QUICK.register(
                "corewidgetsbundle", "mic_mute", "Microphone",
                Icons.MICROPHONE_OFF,
                on_press = self.client.toggle_mic_muted,
                on_state = lambda: self.client.mic_muted(),
                order    = 22)

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
        # And the same again for a saved page: a template, because a panel
        # with one bookmark on it is not what anybody wants a bookmark widget
        # for.
        register(BookmarkWidget)
        # A list to tick off - the most-used thing on a kitchen wall.
        register(ChecklistWidget)
        register(WeatherEventWidget)

        # A star pressed in the browser toolbar puts one on the home
        # page briefly - see _on_web_event.
        self.client.subscribe_to_event("on_web_event", self._on_web_event)
        # And the moment the home page comes back, in case one is waiting.
        self.client.subscribe_to_event("on_visit", self._on_visit)

        self.client.public.cwb_widgets["sub.home"] = [
            w for w in (
                # Always present; carries notifications and the panel button.
                register(ConfigurationBar),
                register(DateTimeWidget, show_date=True, show_time=True),
                register(WeatherWidget),
            ) if w is not None
        ]