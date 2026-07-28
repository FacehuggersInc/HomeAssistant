from __future__ import annotations

from src.constants import get_data_dir, APP_NAME
from src.mixins import mixin
from src.plugin.template import Plugin

from .store import CalendarStore, Event


def _escape(text) -> str:
    import html
    return html.escape(str(text or ""), quote=True)


# Sized for a phone held one-handed: one column, large fields, no zooming.
FORM_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Add an event</title>
<style>
 :root{--bg:#151517;--card:#1c1c1f;--line:#2c2c31;--text:#e6e6e8;
       --muted:#9a9aa2;--accent:#2ff08e}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--text);
      font:16px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;padding:18px}
 h1{font-size:22px;margin:0 0 4px}
 p.sub{color:var(--muted);margin:0 0 18px;font-size:14px}
 form{background:var(--card);border:1px solid var(--line);
      border-radius:14px;padding:16px}
 label{display:block;font-size:13px;color:var(--muted);margin:12px 0 4px}
 input,textarea{width:100%;padding:13px;border-radius:9px;font-size:16px;
      background:#111114;color:var(--text);border:1px solid var(--line)}
 input:focus,textarea:focus{outline:none;border-color:var(--accent)}
 textarea{min-height:90px;resize:vertical}
 .row{display:flex;gap:10px}.row>div{flex:1}
 button{width:100%;margin-top:18px;padding:15px;border:0;border-radius:10px;
      background:var(--accent);color:#10281c;font-size:17px;font-weight:600}
 ul{list-style:none;padding:0;margin:18px 0 0}
 li{display:flex;justify-content:space-between;gap:12px;padding:10px 0;
    border-bottom:1px solid var(--line);font-size:14px}
 li span{color:var(--muted)}
 .ok{background:rgba(47,240,142,.14);border:1px solid rgba(47,240,142,.5);
     border-radius:10px;padding:12px;margin-bottom:14px;display:none}
</style></head><body>
<h1>Add an event</h1>
<p class="sub">It appears on the panel straight away.</p>
<div class="ok" id="ok">Added.</div>
<form id="f">
  <label for="title">Title</label>
  <input id="title" name="title" required placeholder="What is it?">
  <div class="row">
    <div><label for="day">Date</label><input id="day" name="day" type="date" required></div>
    <div><label for="time">Start</label><input id="time" name="time" type="time"></div>
  </div>
  <div class="row">
    <div><label for="end_time">End</label><input id="end_time" name="end_time" type="time"></div>
    <div><label for="icon">Icon</label><input id="icon" name="icon" value="mdi.calendar"></div>
  </div>
  <label for="location">Location</label>
  <input id="location" name="location" placeholder="Optional">
  <label for="notes">Notes</label>
  <textarea id="notes" name="notes" placeholder="Optional"></textarea>
  <button type="submit">Add to the calendar</button>
</form>
<ul>__UPCOMING__</ul>
<script>
 document.getElementById('day').valueAsDate = new Date();
 document.getElementById('f').addEventListener('submit', function (event) {
   event.preventDefault();
   var params = new URLSearchParams({token: '__TOKEN__'});
   new FormData(event.target).forEach(function (value, key) {
     if (value) { params.append(key, value); }
   });
   fetch('/public/calendar_add?' + params.toString(), {method: 'POST'})
     .then(function (r) { return r.json(); })
     .then(function (body) {
       if (body.request !== 'Success') { alert(body.reason || 'Could not add that.'); return; }
       document.getElementById('ok').style.display = 'block';
       // Reloaded rather than patched in: the list below is rendered by the
       // panel, and rebuilding it here would be a second copy of that logic.
       setTimeout(function () { location.reload(); }, 700);
     })
     .catch(function () { alert('Could not reach the panel.'); });
 });
</script></body></html>"""


class Calendar(Plugin):
    """
    Events, holidays and the questions everything else asks about them.

    The store is published on the public registry as `calendar`, so widgets,
    tiles, skills and other plugins all read one source rather than each
    keeping their own idea of what is coming up.
    """

    def __init__(self):
        self.store: CalendarStore = None
        self.reminders = None

    ## LIFECYCLE

    def load(self, carryover=None):
        # Declared before anything can subscribe. subscribe_to_event() indexes
        # straight into the event table, so an undeclared name is a KeyError
        # rather than a quietly ignored subscription.
        if "on_calendar_changed" not in self.client.EVENTS["on_call"]:
            self.client.create_on_call_event("on_calendar_changed")

        path = get_data_dir(APP_NAME) / "calendar" / "events.json"
        self.store = CalendarStore(path, log=self.client.log)

        # One published surface. Methods rather than the store itself, so the
        # storage can change shape without every caller changing with it.
        self.client.public.expose("calendar", "calendar", {
            "store":             self.store,
            "add_event":         self.add_event,
            "remove_event":      self.remove_event,
            "update_event":      self.store.update,
            "get_event":         self.store.get,
            "on_day":            self.store.on_day,
            "in_month":          self.store.in_month,
            "between":           self.store.between,
            "upcoming":          self.store.upcoming,
            "next_event":        self.store.next_event,
            "next_holiday":      self.store.next_holiday,
            "next_user_event":   self.store.next_user_event,
            "previous_event":    self.store.previous_event,
            "current_event":     self.store.current_event,
            "time_until":        self.store.time_until,
            "days_until":        self.store.days_until,
            "describe_gap":      self.store.describe_gap,
            "describe_duration": self.store.describe_duration,
            "holidays":          self.store.holidays,
            "reload":            self.store.load,
            # Published rather than looked up. A page reaching back for its own
            # plugin needs an accessor PluginManager does not have, and the
            # registry is already the one surface everything else reads.
            "option":            self.option,
        }, overwrite=True)

        # Remote events. Authed, because anything that can write here can put
        # arbitrary text on a screen in someone's kitchen.
        self.client.API_REGISTRY.register(
            "calendar", "calendar_add", self.api_add, requires_auth=True)
        self.client.API_REGISTRY.register(
            "calendar", "calendar_upcoming", self.api_upcoming, requires_auth=True)
        self.client.API_REGISTRY.register(
            "calendar", "calendar_form", self.api_form, requires_auth=True)

    @mixin("settings.__init__", "calendar", "after")
    def _add_settings_block(self, page, *args):
        """
        A button beside the default location field.

        The settings page renders a `string` as a text field, which is correct
        for most of them and useless for an address - so the plugin adds the
        map button itself rather than the page growing a location type it
        would only ever use for this.
        """
        from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
        from PyQt6.QtCore import Qt
        from src.styling import make_font, SIZES, set_style

        host = QWidget()
        set_style(host, "common", "transparent")
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        label = QLabel("Default location")
        label.setFont(make_font(SIZES.S2, bold=True))
        set_style(label, "common", "text-strong")
        row.addWidget(label)

        value = QLabel(self.option("general.default_location", "") or "Not set")
        value.setFont(make_font(SIZES.S1))
        value.setWordWrap(True)
        set_style(value, "common", "text-muted")
        row.addWidget(value, stretch=1)

        button = QPushButton("Choose on a map")
        button.setFont(make_font(SIZES.S1, bold=True))
        button.setFixedHeight(42)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(button, "overlays", "dialog-button-primary")
        button.clicked.connect(lambda: self._choose_default_location(value))
        row.addWidget(button)

        try:
            # "general", not "calendar". Categories are the top-level keys of
            # the plugin's settings.json, so a block addressed to the plugin
            # name is silently dropped - which is exactly what happened.
            page.features().insert_block("general", 0, host)
        except Exception as e:
            self.client.log("debug", f"[Calendar] No settings block: {e}")

    def _choose_default_location(self, label=None) -> None:
        from .pickers import LocationPickerDialog

        def chosen(place: str):
            try:
                self.settings.general.default_location.value = place
                if label is not None:
                    label.setText(place or "Not set")
            except Exception as e:
                self.client.log("warning",
                                f"[Calendar] Could not save default location: {e}")

        self.client.dialog(LocationPickerDialog(
            self.client, self.option("general.default_location", ""),
            on_chosen=chosen))

    def built(self):
        # Started here, not in load(): the panel it shows needs the overlay
        # layer, and that does not exist until the client has built.
        from .reminder import ReminderWatcher
        self.reminders = ReminderWatcher(self)
        self.reminders.start()

    @mixin("sub.home.__init__", "calendar", "after")
    def _add_widgets(self, sub_home, *args):
        """Registered, not placed - the saved layout decides what is on screen."""
        from .widgets import UpcomingEventWidget, NextEventsWidget
        register = sub_home.features().register_widget
        register(UpcomingEventWidget)
        register(NextEventsWidget)

    @mixin("sub.tiles.__init__", "calendar", "after")
    def _add_tiles(self, sub_tiles, *args):
        from .widgets import MiniCalendarTile
        sub_tiles.features().register_tile(MiniCalendarTile, in_grid=False)

    @mixin("home.__init__", "calendar", "after")
    def _add_sub_page(self, home, *args):
        """
        Registered on the home page's own construction rather than in built().

        A mixin fires whenever the page is built - at startup and again after a
        reload - where a one-shot call in built() only covers the first.
        """
        from .calendar_page import CalendarPage
        home.add_sub_page("calendar", CalendarPage)

    def unload(self, carryover=None):
        if self.reminders is not None:
            self.reminders.stop()
            self.reminders = None

        # The event stays declared - another plugin may still be listening,
        # and re-creating it on the next load would wipe their subscriptions.
        page = self.client.PAGES.get_entry("#cwb_home_page")
        if page is not None and getattr(page, "instance", None) is not None:
            home = page.instance
            # Widgets and tiles first: they live on sub-pages that outlive this
            # plugin, so one left behind keeps painting from a dead module.
            subs = getattr(home, "sub_page_dict", {})
            sub_home = subs.get("home")
            if sub_home is not None and sub_home.has_feature("remove_widget"):
                for key in ("calendar_upcoming", "calendar_list"):
                    try:
                        sub_home.features().remove_widget(key)
                    except Exception:
                        pass
            sub_tiles = subs.get("tiles")
            if sub_tiles is not None and sub_tiles.has_feature("remove_tile"):
                try:
                    sub_tiles.features().remove_tile("calendar_mini")
                except Exception:
                    pass
            try:
                home.remove_sub_page("calendar")
            except Exception:
                pass

        self.client.public.unexpose("calendar", "calendar")
        self.client.API_REGISTRY.unregister("calendar")

    ## SETTINGS

    def option(self, path: str, default):
        """
        Read one of this plugin's own settings.

        Plugin does not provide this - each plugin defines it, the same way
        AIFallback does. Everything guards with a default, so a setting that
        has not migrated in yet degrades rather than raising.
        """
        # getattr, not self.settings. Plugin only declares the attribute as a
        # type annotation - the loader assigns it, and anything reading it
        # before or without that raises AttributeError. This runs on every
        # client tick, so it was raising a warning per frame.
        node = getattr(self, "settings", None)
        if node is None:
            return default
        try:
            for part in path.split("."):
                node = getattr(node, part)
            return node.value
        except Exception:
            return default

    ## EVENTS

    def remove_event(self, key: str) -> bool:
        """
        Removal goes through here so it is announced.

        The store's own remove() is silent, and anything showing the calendar
        would have kept showing the deleted event until its next tick.
        """
        removed = self.store.remove(key)
        if removed:
            self.client.trigger_on_call_event_iteration("on_calendar_changed", key)
        return removed

    def add_event(self, **fields) -> Event:
        event = Event(**fields)
        self.store.add(event)
        self.client.trigger_on_call_event_iteration("on_calendar_changed", event)
        return event

    ## API

    def api_add(self, title: str = "", day: str = "", time: str = "",
                end_time: str = "", location: str = "", notes: str = "",
                icon: str = "mdi.calendar"):
        """POST or GET /public/calendar_add?title=&day=YYYY-MM-DD&time=HH:MM"""
        candidate = Event.from_dict({
            "title": title, "day": day, "time": time, "end_time": end_time,
            "location": location, "notes": notes, "icon": icon,
            "source": "imported",
        })
        if candidate is None:
            return {"request": "Failed",
                    "reason": "title and a day of YYYY-MM-DD are required"}, 400

        # Who added it, when the caller is a known device. The API's own auth
        # records the user on the request, so an endpoint does not have to ask
        # again - and an event gains an author without anybody typing one.
        try:
            from flask import request as _request
            user = _request.environ.get("ha.user")
            if user is not None and not candidate.notes:
                candidate.notes = f"Added by {user.name}"
        except Exception:
            pass

        self.store.add(candidate)
        self.client.trigger_on_call_event_iteration("on_calendar_changed", candidate)
        self.client.simple_notify(
            icon or "mdi.calendar", "Calendar",
            f"'{candidate.title}' added for {candidate.day}")
        return {"request": "Success", "event": candidate.to_dict()}, 201

    def api_form(self, **_ignored):
        """
        A page you can add an event from, on a phone.

        Served rather than shipped as a file: the client id has to be in the
        form for the POST to authenticate, and it is not known until runtime.

        Deliberately plain HTML with no build step and no framework - it is a
        form with six fields, and anything more is a dependency to maintain
        for the sake of a page most people open twice.
        """
        # The token that fetched this page. The form posts as the same device
        # rather than embedding a shared secret, so a page left open on a
        # phone is only ever that phone's access.
        token = ""
        try:
            from flask import request as _request
            token = (_request.args.get("token")
                     or _request.headers.get("X-Client-Token") or "")
        except Exception:
            pass
        upcoming = self.store.upcoming(5)
        listed = "".join(
            f"<li><b>{_escape(e.title)}</b>"
            f"<span>{_escape(e.day)}"
            f"{'' if e.all_day else ' &middot; ' + _escape(e.time)}</span></li>"
            for e in upcoming
        ) or "<li><span>Nothing coming up.</span></li>"

        return (FORM_PAGE
                .replace("__TOKEN__", _escape(token))
                .replace("__UPCOMING__", listed)), 200

    def api_upcoming(self, count: str = "5"):
        try:
            wanted = max(1, min(50, int(count)))
        except (TypeError, ValueError):
            wanted = 5
        return {"request": "Success",
                "events": [e.to_dict() for e in self.store.upcoming(wanted)]}, 200
