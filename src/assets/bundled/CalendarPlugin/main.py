from __future__ import annotations

from src.constants import get_data_dir, APP_NAME
from src.mixins import mixin
from src.plugin.template import Plugin

from .store import CalendarStore, Event


from pathlib import Path

from src.webui import WebAssets

# The two pages this plugin serves are files in web/ - see docs/web-ui.md.
# Nothing here formats or substitutes into them: what the panel has to say
# goes into one JSON object and the scripts read it. The token in particular
# used to be spliced into the script with a __TOKEN__ replace.
ASSETS = WebAssets(Path(__file__).with_name("web"),
                   required=("form.html", "form.css", "form.js",
                             "subs.html", "subs.css", "subs.js"))

FORM_PATH = "/public/calendar_form"
ADD_PATH = "/public/calendar_add"
SUBS_PATH = "/public/calendar_subscriptions"


def render_form_page(token: str, people: list, upcoming: list) -> str:
    """
    The add-an-event page.

    `people` and `upcoming` are sent as data and drawn by the script, so an
    event called `<b>dentist` is text on the page rather than markup in it.
    """
    return ASSETS.page(
        title="Add an event",
        heading="Add an event",
        blurb="It appears on the panel straight away.",
        token=token, endpoint=FORM_PATH,
        body_file="form.html", css_file="form.css", script_file="form.js",
        data={
            "people": list(people or []),
            "upcoming": list(upcoming or []),
            # The form posts to a different endpoint than the one that served
            # it, which is the one thing here the script cannot work out.
            "addEndpoint": ADD_PATH,
        },
    )


def render_subs_page(token: str, people: list, subscriptions: list,
                     message: str = "") -> str:
    """
    The subscribed-calendars page.

    A calendar's name and the error text from a feed both come from outside
    the panel - one of them is a string a stranger's server chose - so they
    are sent as data and written as text.
    """
    return ASSETS.page(
        title="Subscribed calendars",
        heading="Subscribed calendars",
        blurb="Mirrored onto the panel, one way. Nothing is sent back.",
        token=token, endpoint=SUBS_PATH, message=message,
        body_file="subs.html", css_file="subs.css", script_file="subs.js",
        data={
            "people": list(people or []),
            "subscriptions": list(subscriptions or []),
        },
    )


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
        self.subscriptions = None

    ## LIFECYCLE

    def load(self, carryover=None):
        # Declared before anything can subscribe. subscribe_to_event() indexes
        # straight into the event table, so an undeclared name is a KeyError
        # rather than a quietly ignored subscription.
        if "on_calendar_changed" not in self.client.EVENTS["on_call"]:
            self.client.create_on_call_event("on_calendar_changed")

        path = get_data_dir(APP_NAME) / "calendar" / "events.json"
        self.store = CalendarStore(path, log=self.client.log)

        # Owned here rather than by the page: the widgets and the reminder
        # panel draw stickers too, and a store belonging to a page is a store
        # that does not exist until somebody opens that page.
        from .stickers import StickerStore
        self.stickers = StickerStore(
            get_data_dir(APP_NAME) / "calendar" / "stickers.json",
            resolve_event=self.store.get,
            resolve_holiday=self._holiday_on,
            log=self.client.log)

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
            "find_holiday":      self.store.find_holiday,
            "next_user_event":   self.store.next_user_event,
            "previous_event":    self.store.previous_event,
            "current_event":     self.store.current_event,
            "time_until":        self.store.time_until,
            "days_until":        self.store.days_until,
            "describe_gap":      self.store.describe_gap,
            "describe_duration": self.store.describe_duration,
            "holidays":          self.store.holidays,
            "expand":            self.store.expand,
            "skip_occurrence":   self.store.skip_occurrence,
            "unskip_occurrence": self.store.unskip_occurrence,
            "looks_like":        self.store.looks_like,
            "remove_matching":   self.store.remove_matching,
            "skip_next":         self.store.skip_next,
            "set_hidden":        self.store.set_hidden,
            "hidden_keys":       self.store.hidden_keys,
            "prune":             self.store.prune,
            "deduplicate":       self.store.deduplicate,
            "subscriptions":     lambda: self.subscriptions.all() if self.subscriptions else [],
            "add_subscription":  lambda url, name="", colour="", owner="": self.subscriptions.add(url, name, colour, owner),
            "remove_subscription": lambda key: self.subscriptions.remove(key),
            "sync_subscriptions": self.sync_subscriptions,
            "reset_subscriptions": self.reset_subscriptions,
            "reload":            self.store.load,
            # Published rather than looked up. A page reaching back for its own
            # plugin needs an accessor PluginManager does not have, and the
            # registry is already the one surface everything else reads.
            "option":            self.option,
            # Stickers stuck to days and to events - see stickers.py.
            "stickers":          self.stickers,
            "stickers_for":      self.stickers.for_event,
        }, overwrite=True)

        # Remote events. Authed, because anything that can write here can put
        # arbitrary text on a screen in someone's kitchen.
        self.client.API.register(
            "calendar", "calendar_add", self.api_add, requires_auth=True)
        self.client.API.register(
            "calendar", "calendar_upcoming", self.api_upcoming, requires_auth=True)
        self.client.API.register(
            "calendar", "calendar_sync", self.api_sync, requires_auth=True,
            action="Sync calendars", icon="sync")
        self.client.API.register(
            "calendar", "calendar_subscriptions", self.api_subscriptions,
            requires_auth=True, gui="Subscribed calendars", icon="calendar",
            description="Mirror a Google, Apple or Outlook calendar onto the panel.")
        self.client.API.register(
            "calendar", "calendar_dump", self.api_dump, requires_auth=True)
        self.client.API.register(
            "calendar", "calendar_form", self.api_form, requires_auth=True,
            gui="Add an event", icon="calendar",
            description="A page sized for a phone. Adds to the panel straight away.")

        # Serves the two pages' larger files, and remembers its own URL so no
        # page has to name it. Both pages share this one endpoint.
        ASSETS.register(self.client, "calendar")
        for name in ASSETS.missing():
            self.client.log("error",
                            f"[Calendar] A page cannot be drawn - {name} is "
                            f"not in {ASSETS.folder}")

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
            page.features().insert_plugin_block("calendar", 0, host)
        except Exception as e:
            self.client.log("warning",
                            f"[Calendar] Default location block failed: {e}",
                            include_traceback=True)

    @mixin("settings.__init__", "calendar", "after")
    def _add_subscriptions_block(self, page, *args):
        """
        The synced calendars, each with its own controls.

        A toggle called "resync everything" was the wrong shape: the thing a
        person wants to act on is one calendar that has gone wrong, and it is
        already on screen in a list.
        """
        from PyQt6.QtWidgets import (
            QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
        )
        from PyQt6.QtCore import Qt
        from src.styling import make_font, SIZES, set_style

        host = QWidget()
        set_style(host, "common", "transparent")
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)

        heading = QLabel("Synced calendars")
        heading.setFont(make_font(SIZES.S2, bold=True))
        set_style(heading, "common", "text-strong")
        column.addWidget(heading)

        from PyQt6.QtWidgets import QPushButton as _Button
        add = _Button("Add a calendar")
        add.setFont(make_font(SIZES.S1, bold=True))
        add.setFixedHeight(42)
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(add, "overlays", "dialog-button-primary")
        add.clicked.connect(lambda: self._add_feed_dialog())

        header = QHBoxLayout()
        header.addWidget(heading, stretch=1)
        header.addWidget(add)
        column.removeWidget(heading)
        column.addLayout(header)

        feeds = self.subscriptions.all() if self.subscriptions else []
        if not feeds:
            empty = QLabel("None yet. Add a calendar with the button above.")
            empty.setFont(make_font(SIZES.S1))
            empty.setWordWrap(True)
            set_style(empty, "common", "text-muted")
            column.addWidget(empty)
        else:
            from .subscription_editor import subscription_row
            for feed in feeds:
                column.addWidget(subscription_row(
                    self.client, feed,
                    on_changed=lambda: self.client.goto("#settings", override=True)))

        try:
            page.features().insert_plugin_block("calendar", 0, host)
        except Exception as e:
            self.client.log("warning",
                            f"[Calendar] Subscriptions block failed: {e}",
                            include_traceback=True)

    def _add_feed_dialog(self) -> None:
        """
        The same dialog the calendar page opens.

        There were briefly two: this one and SubscriptionEditorDialog. Two
        dialogs for one job drift - one grew the provider hint and the address
        validation, the other did not - and which you got depended on where you
        started from.
        """
        from .subscription_editor import SubscriptionEditorDialog

        def saved():
            # Re-entered so the new calendar is in the list behind the dialog.
            self.client.goto("#settings", override=True)

        self.client.dialog(SubscriptionEditorDialog(self.client, on_saved=saved))

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
        from .subscriptions import SubscriptionManager
        self.subscriptions = SubscriptionManager(
            self, get_data_dir(APP_NAME) / "calendar" / "subscriptions.json")

        # Before anything syncs: an event the manager cannot recognise as its
        # own is an event the next sync duplicates rather than replaces.
        #
        # One batch for all three. Each step passes through a state nobody
        # should be shown - orphans still listed, duplicates still doubled -
        # and the tiles and widgets are already reading by now. Held together,
        # the calendar goes from how it was to how it should be with nothing
        # in between, and the file is written once instead of three times.
        try:
            with self.store.batch():
                self.subscriptions.migrate_events()
                self.subscriptions.orphans()
                removed = self.store.deduplicate()
            if removed:
                self.client.log("info",
                                f"[Calendar] Removed {removed} duplicate event(s).")
        except Exception as e:
            self.client.log("warning", f"[Calendar] Could not tidy subscriptions: {e}")

        # On a thread: a feed is a network fetch and this runs on the UI one.
        # Staggered from startup so it is not competing with everything else
        # the panel does in its first seconds.
        def first_sync():
            try:
                self.sync_subscriptions()
            except Exception as e:
                self.client.log("warning", f"[Calendar] First sync failed: {e}")

        self.client.TIMEOUTS.add(12, first_sync, "calendar_first_sync")
        self.client.TIMEOUTS.start("calendar_first_sync")
        self._schedule_sync()

        # Once, at startup. events.json is otherwise unbounded, and a daily
        # timer for something this slow-moving is a timer for nothing.
        try:
            days = int(self.option("general.keep_events_for_days", 365))
            if days > 0:
                dropped = self.store.prune(days)
                if dropped:
                    self.client.log("info", f"[Calendar] Pruned {dropped} old event(s).")
        except Exception as e:
            self.client.log("warning", f"[Calendar] Prune failed: {e}")

        from .skills import build as build_skills
        try:
            self.client.SKILLS.register("calendar", build_skills(self))
        except Exception as e:
            self.client.log("warning", f"[Calendar] Could not register skills: {e}")

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
        try:
            self.client.SKILLS.un_register("calendar")
        except Exception:
            pass

        for timer in ("calendar_sync", "calendar_first_sync"):
            try:
                self.client.TIMEOUTS.cancel(timer)
            except Exception:
                pass

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
        self.client.API.unregister("calendar")

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

    ## SUBSCRIPTIONS

    def sync_subscriptions(self) -> None:
        """Refresh every feed, off the UI thread."""
        from threading import Thread

        def work():
            try:
                results = self.subscriptions.sync_all()
            except Exception as e:
                self.client.log("warning", f"[Calendar] Sync failed: {e}")
                return
            if any(results.values()):
                self.client.call_on_ui(lambda: self.client.trigger_on_call_event_iteration(
                    "on_calendar_changed", None))

        Thread(target=work, name="__calendar_sync", daemon=True).start()

    def reset_subscriptions(self, key: str = "") -> None:
        """Wipe and re-fetch, off the UI thread like any other sync."""
        from threading import Thread

        def work():
            try:
                dropped = self.subscriptions.reset(key)
                self.client.call_on_ui(lambda: (
                    self.client.simple_notify(
                        "mdi.calendar-sync", "Calendar",
                        f"Re-synced from scratch, {dropped} old event(s) cleared."),
                    self.client.trigger_on_call_event_iteration(
                        "on_calendar_changed", None)))
            except Exception as e:
                self.client.log("warning", f"[Calendar] Reset failed: {e}")

        Thread(target=work, name="__calendar_reset", daemon=True).start()

    def _schedule_sync(self) -> None:
        minutes = 60
        try:
            minutes = max(15, int(self.option("subscriptions.refresh_minutes", 60)))
        except (TypeError, ValueError):
            pass

        def again():
            self.sync_subscriptions()
            # Re-armed each time rather than left repeating, so a change to
            # the interval takes effect at the next tick instead of at the
            # next restart.
            self._schedule_sync()

        self.client.TIMEOUTS.add(minutes * 60, again, "calendar_sync")
        self.client.TIMEOUTS.start("calendar_sync")

    ## EVENTS

    def _holiday_on(self, slug: str, year: int):
        """
        Where a holiday falls in a given year, by the stable part of its key.

        Computed rather than stored, and many of them move - so a sticker
        following one has to ask each year rather than remember a date.
        """
        if not slug:
            return None
        try:
            for entry in self.store.holidays(int(year)):
                if str(entry.key or "").split(":")[-1] == slug:
                    return entry.date
        except Exception as e:
            self.client.log("warning",
                            f"[Calendar] Could not place holiday '{slug}': {e}")
        return None

    def remove_event(self, key: str) -> bool:
        """
        Removal goes through here so it is announced.

        The store's own remove() is silent, and anything showing the calendar
        would have kept showing the deleted event until its next tick.
        """
        removed = self.store.remove(key)
        if removed:
            # Anything stuck to it stays, on the day it was last shown on.
            # A sticker is something somebody put there, and deleting an event
            # is not a statement about it.
            try:
                self.stickers.remove_for_event(key)
            except Exception as e:
                self.client.log("warning",
                                f"[Calendar] Could not unstick from {key}: {e}")
            self.client.trigger_on_call_event_iteration("on_calendar_changed", key)
        else:
            # Every caller discarded the return value, so a removal that
            # matched nothing looked exactly like one that worked - the dialog
            # closed, the day view refreshed, and the event was still there.
            self.client.log(
                "warning",
                f"[Calendar] remove_event('{key}') matched no stored event - "
                f"nothing was removed.")
        return removed

    def add_event(self, **fields) -> Event:
        event = Event(**fields)
        self.store.add(event)
        self.client.trigger_on_call_event_iteration("on_calendar_changed", event)
        return event

    ## API

    def api_add(self, title: str = "", day: str = "", time: str = "",
                end_time: str = "", location: str = "", notes: str = "",
                icon: str = "mdi.calendar", user: str = ""):
        """
        POST or GET /public/calendar_add?user=&title=&day=YYYY-MM-DD&time=HH:MM

        `user` is required. The device is known - it was approved by name -
        but a device is not a person: a shared tablet in a kitchen is used by
        everybody in the house. Saying who is asking is what makes two
        identical events two events rather than one.

        Pass a name to override the device's; leave it out and it is refused
        rather than guessed.
        """
        owner = (user or "").strip()
        if not owner:
            return {"request": "Failed",
                    "reason": "user is required - say who this event is for"}, 400

        candidate = Event.from_dict({
            "title": title, "day": day, "time": time, "end_time": end_time,
            "location": location, "notes": notes, "icon": icon,
            "source": "imported", "owner": owner,
        })
        if candidate is None:
            return {"request": "Failed",
                    "reason": "title and a day of YYYY-MM-DD are required"}, 400

        # Who added it, when the caller is a known device. The API's own auth
        # records the user on the request, so an endpoint does not have to ask
        # again - and an event gains an author without anybody typing one.
        try:
            from flask import request as _request
            device = _request.environ.get("ha.user")
            if device is not None and not candidate.notes:
                candidate.notes = f"Sent from {device.name}"
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
        # Whoever the panel knows. A free text field here meant "Chris",
        # "chris" and "Chris " were three people who each owned some of the
        # same events.
        return render_form_page(
            token,
            people=list(self.client.USERS.names()),
            upcoming=[{
                "title": event.title,
                "when": (event.day if event.all_day
                         else f"{event.day} \u00b7 {event.time}"),
            } for event in self.store.upcoming(5)],
        ), 200

    def api_sync(self, **_ignored):
        """
        Refresh every subscribed calendar now.

        Synchronous, unlike the timer's version: this one was asked for, and a
        button that says "done" before anything has happened is a button that
        lies. The feeds are a few seconds at worst.
        """
        if self.subscriptions is None:
            return {"request": "Failed", "reason": "Subscriptions are not ready"}, 503

        feeds = self.subscriptions.all()
        if not feeds:
            return {"request": "Success", "synced": 0,
                    "detail": "No subscribed calendars yet."}, 200

        try:
            results = self.subscriptions.sync_all()
        except Exception as e:
            self.client.log("warning", f"[Calendar] Manual sync failed: {e}")
            return {"request": "Failed", "reason": str(e)[:140]}, 500

        total = sum(results.values())
        self.client.call_on_ui(lambda: self.client.trigger_on_call_event_iteration(
            "on_calendar_changed", None))

        broken = [f.name for f in feeds if f.last_error]
        detail = f"{total} event(s) from {len(results)} calendar(s)."
        if broken:
            # Named, not counted. "One failed" is not actionable; "Work failed"
            # is somewhere to look.
            detail += " Failed: " + ", ".join(broken)
        return {"request": "Success", "synced": total, "detail": detail}, 200

    def api_subscriptions(self, add: str = "", name: str = "", user: str = "",
                          remove: str = "", **_ignored):
        """
        Manage subscribed calendars from a phone.

        One endpoint for the page and its actions: a form that posts back to
        the address it was served from needs no second URL, and a phone that
        has this bookmarked can do the whole job from it.
        """
        message = ""
        if remove:
            message = ("Removed." if self.subscriptions.remove(remove.strip())
                       else "That subscription is already gone.")
        elif add:
            owner = (user or "").strip()
            if not owner:
                message = "Say who the calendar is for."
            else:
                sub = self.subscriptions.add(add.strip(), (name or "").strip(), owner=owner)
                self.sync_subscriptions()
                message = f"Added {sub.name}. Fetching it now."

        import datetime as _dt

        rows = []
        for sub in self.subscriptions.all():
            when = "never"
            if sub.last_sync:
                when = _dt.datetime.fromtimestamp(
                    sub.last_sync).strftime("%d %b %H:%M")
            rows.append({
                "key": sub.key,
                "name": sub.name,
                "owner": sub.owner or "unassigned",
                "synced": when,
                # Whatever a stranger's server said went wrong. Sent as data
                # and written as text, never built into markup.
                "error": sub.last_error or "",
            })

        token = ""
        try:
            from flask import request as _request
            token = (_request.args.get("token")
                     or _request.headers.get("X-Client-Token") or "")
        except Exception:
            pass

        return render_subs_page(
            token,
            people=list(self.client.USERS.names()),
            subscriptions=rows, message=message), 200

    def api_dump(self, title: str = "", day: str = "", show: str = ""):
        """
        What is actually on disk, and what the calendar draws from it.

        With no arguments it reports what appears more than once rather than
        making somebody guess a search term - which is the question anybody
        calling this actually has.

          /public/calendar_dump?token=...                  duplicates only
          /public/calendar_dump?token=...&show=all         every stored row
          /public/calendar_dump?token=...&title=conference one title
        """
        from collections import defaultdict

        stored = self.store.snapshot()
        needle = (title or "").strip().lower()

        if needle or day:
            rows = [e.to_dict() for e in stored
                    if (not needle or needle in e.title.lower())
                    and (not day or e.day == day)]
            return {"request": "Success", "stored": len(stored),
                    "matched": len(rows), "events": rows}, 200

        if (show or "").lower() == "all":
            return {"request": "Success", "stored": len(stored),
                    "events": [e.to_dict() for e in stored]}, 200

        # Grouped on what a person would call the same event, not on the key.
        # Two rows with different keys and the same content is the thing being
        # looked for, so a key-based grouping would report nothing.
        groups = defaultdict(list)
        for event in stored:
            groups[(event.title.strip().lower(), event.day,
                    event.end_day, event.time, event.owner)].append(event)

        repeated = {f"{k[0]} @ {k[1]}": [e.to_dict() for e in v]
                    for k, v in groups.items() if len(v) > 1}

        # And the other kind: a stored one-off landing on a day its own series
        # already covers. Nothing in the file looks wrong - the two only meet
        # once the series is expanded, which is why a store-level check misses
        # it entirely.
        overlaps = []
        for event in stored:
            if event.recurring or not event.date:
                continue
            for series in stored:
                if not series.recurring or series.key == event.key:
                    continue
                if series.title.strip().lower() != event.title.strip().lower():
                    continue
                if any(o.date == event.date
                       for o in self.store.expand(series, event.date, event.date)):
                    overlaps.append({"loose": event.to_dict(),
                                     "series": series.key,
                                     "series_title": series.title})
                    break

        return {"request": "Success",
                "stored": len(stored),
                "duplicate_rows": len(repeated),
                "series_overlaps": len(overlaps),
                "duplicates": repeated,
                "overlaps": overlaps,
                "hint": ("Nothing repeated on disk." if not repeated and not overlaps
                         else "duplicates are rows stored twice; overlaps are a "
                              "one-off sitting on a day its series already covers.")}, 200

    def api_upcoming(self, count: str = "5"):
        try:
            wanted = max(1, min(50, int(count)))
        except (TypeError, ValueError):
            wanted = 5
        return {"request": "Success",
                "events": [e.to_dict() for e in self.store.upcoming(wanted)]}, 200
