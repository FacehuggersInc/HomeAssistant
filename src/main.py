from __future__ import annotations

import asyncio
import gc
import json
import multiprocessing
import os
import platform as _platform
import shutil
import subprocess
import sys
import time
import traceback
import uuid as _uuid
from datetime import datetime
from pathlib import Path
from threading import Thread, enumerate as thread_enum
from typing import Callable, Literal, Optional, TextIO

from dynaconf import Dynaconf

from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject, QEvent
from PyQt6.QtGui import QFontDatabase, QColor

import psutil

from src.threading import ThreadManager
from src.enums import clear_events, get_global_events, TriggerAppEvent, Asset
from src.timing import TimeoutScheduler
from src.mixins import MixinManager, mixin_target
from src.plugin.loader import PluginManager
from src.registries.api_registry import APIRegistry
from src.registries.public_registry import PublicRegistry
from src.registries.page_registry import PageRegistry
from src.backend import FlaskApp, FlaskService
from src.assistant.skill import Skill, SkillIntentEngine
from src.assistant.stt import STTProcessing
from src.assistant.tts import TTSProcessing
from src.ui.overlays import OverlayManager, NotificationManager, DialogManager, Panel
from src.styling import COLORS, load_styles, set_style

EVENT_LEVELS = Literal["debug", "info", "warning", "error", "critical"]
EVENTS = Literal[
    "initialized", "on_focus", "on_un_focus", "on_visit", "on_leave",
    "on_update", "on_minimize", "on_maximize", "on_fullscreen",
    "on_state_change", "on_close", "on_settings_saved",
    "on_woke_assistant", "on_assistant_transcribed", "on_plugin_reloading", "on_plugin_unload",
    "on_interaction", "on_fresh_interaction", "on_interaction_timeout",
    "on_collection",
]
APP_NAME = "Desktop Home Assistant"


##DATA DIR

def get_data_dir(app_name: str) -> Path:
    system = _platform.system()
    if system == "Windows":
        base = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        #XDG compliant - works on Arch, Mint, Ubuntu etc.
        base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / app_name.replace(" ", "")


##UI BRIDGE

class UIBridge(QObject):
    """Marshals callables from background threads onto the Qt main thread."""

    ui_call = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        #QueuedConnection required for safe cross-thread signal delivery
        self.ui_call.connect(self.execute, Qt.ConnectionType.QueuedConnection)

    def execute(self, fn) -> None:
        try:
            fn()
        except Exception as e:
            print(f"[UIBridge] error executing {fn}: {e}")
            traceback.print_exc()

    def dispatch(self, fn: Callable) -> None:
        self.ui_call.emit(fn)


##APP WINDOW

class AppWindow(QMainWindow):
    """The QMainWindow. Owned by Client, forwards window events back to it."""

    def __init__(self, client: "Client"):
        super().__init__()
        self.client = client

    def closeEvent(self, event) -> None:
        event.ignore()
        self.client.stop()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if self.client.BUILT:
            if self.isMinimized():
                self.client.iterate_event_callables("on_minimize", event, True)
            elif self.isMaximized():
                self.client.iterate_event_callables("on_maximize", event, True)
            elif self.isFullScreen():
                self.client.iterate_event_callables("on_fullscreen", event, True)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        if self.client.BUILT:
            self.client.iterate_event_callables("on_focus", event, True)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if self.client.BUILT:
            self.client.iterate_event_callables("on_un_focus", event, True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.client.BUILT:
            self.client.on_window_resized(event.size().width(), event.size().height())


##INTERACTION WATCHER

class InteractionWatcher(QObject):
    """
    Installed app-wide on Client.app — watches for any mouse/touch
    interaction anywhere in the app and forwards it back to
    Client._on_global_interaction(). This used to live inside
    CarouselPlugin as its own InteractionEventWatcher, installed by
    that one plugin for its own use; it's a Client-level concern now,
    so every plugin gets on_interaction/on_fresh_interaction/
    on_interaction_timeout for free via subscribe_to_event() instead
    of installing its own event filter.
    """

    INTERACTION_EVENT_TYPES = (
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseMove,
        QEvent.Type.TouchBegin,
        QEvent.Type.TouchUpdate,
        QEvent.Type.TouchEnd,
    )

    def __init__(self, client: "Client"):
        super().__init__()
        self.client = client

    def eventFilter(self, obj, event) -> bool:
        if event.type() in self.INTERACTION_EVENT_TYPES:
            self.client._on_global_interaction(event)
        return False   #never consume — this only observes


##CLIENT

class Client:
    """
    Central application object. Owns the QApplication, QMainWindow,
    all managers, and the plugin system.

    Usage
    -----
    client = Client()
    client.run()
    """

    @mixin_target("client.__init__")
    def __init__(self):
        self.START_TIME  = time.time()
        self.WINDOW_NAME = APP_NAME

        ## -- QT

        self.app    = QApplication.instance() or QApplication(sys.argv)
        self.window = AppWindow(self)
        self.bridge = UIBridge()

        # See InteractionWatcher above and _on_global_interaction()/
        # _check_interaction_timeout() below — drives the
        # on_interaction/on_fresh_interaction/on_interaction_timeout
        # events any plugin can subscribe to instead of installing its
        # own event filter the way CarouselPlugin used to.
        self._last_interaction_time = time.time()
        self._interaction_idle      = False
        self._interaction_watcher   = InteractionWatcher(self)
        self.app.installEventFilter(self._interaction_watcher)

        self.BUILT   = False
        self.RESTART = False
        self.UPDATE  = False

        self.LAST_COLLECTION = time.time()
        self._process        = psutil.Process()   #for the memory diagnostics in update_thread()'s hourly collection

        self.STATES = {
            "home_page_setup": False
        }

        ## -- EVENTS

        self.EVENTS: dict = {
            "states": {"home_page_setup": False},
            "keys":   [],
            "on_call": {
                "initialized":              [],
                "on_key":                   [],
                "on_focus":                 [],
                "on_un_focus":              [],
                "on_visit":                 [],
                "on_leave":                 [],
                "on_update":                [],
                "on_minimize":              [],
                "on_maximize":              [],
                "on_fullscreen":            [],
                "on_state_change":          [],
                "on_close":                 [],
                "on_settings_saved":        [],
                "on_woke_assistant":        [],
                "on_assistant_transcribed": [],
                "on_plugin_reloading":      [],
                "on_plugin_unload" :        [],
                "on_interaction":           [],
                "on_fresh_interaction":     [],
                "on_interaction_timeout":   [],
                "on_collection":            [],
            },
        }

        self.LOGGING               = True
        self.LOGGING_FILE_CREATED  = False
        self.LOG: Optional[TextIO] = None

        self.THREADS  = ThreadManager()
        self.TIMEOUTS = TimeoutScheduler(self)

        self.window_locked      = False
        self.window_should_lock = True

        ## -- ASSETS

        self.ASSETS: dict = {}

        cwd = Path(os.getcwd())
        local_asset = Asset(cwd)
        local_asset.mark_uploadable()
        self.register_asset("local",   local_asset,                             "FOLDER")
        self.register_asset("logs",    Asset(cwd / "logs"),                     "FOLDER")
        self.register_asset("plugins", Asset(cwd / "plugins"),                  "FOLDER")
        self.register_asset("fonts",   Asset(cwd / "src" / "assets" / "fonts"), "FOLDER")
        self.register_asset("icons",   Asset(cwd / "src" / "assets" / "icons"), "FOLDER")
        self.register_asset("styles",  Asset(cwd / "src" / "assets" / "styles"), "FOLDER")

        #parses every *.css class file into STYLES — must run before any
        #widget that calls set_style()/get_style() gets constructed
        self.log("info", "[Styling] Loading Styles")
        load_styles()
        

        self.DATAPATH = Asset(get_data_dir(APP_NAME))
        self.DATA     = Asset(self.DATAPATH / f"{APP_NAME.replace(' ', '')}.json")
        self.register_asset("data", self.DATA, "json")
        self.create_user_data_files()

        ## -- CLIENT ID

        self.CLIENT_ID = self.load_or_create_client_id()

        ## -- SETTINGS

        self.SETTINGS = Dynaconf(settings_files=[str(self.DATA)])
        bg_asset = Asset(self.SETTINGS.home.images.value)
        bg_asset.mark_uploadable()
        self.register_asset("background_images", bg_asset, "FOLDER")

        ## -- ASSISTANT

        self.ASSIST_VOICE_ACTIVITY_LEVEL = 0.0
        self.ASSIST_STATUS               = "DORMANT"
        self.SKILLS = SkillIntentEngine(self)
        self.STT    = None
        self.TTS    = None

        ## -- APIS
        self.API_REGISTRY = APIRegistry(self)
        self.API: dict = {} #This is for custom API Classes (NOT the API_REGISTRY which handles backend.py Flask REST API endpoints)

        ## -- OVERLAYS
        self.OVERLAYS             = OverlayManager(self)
        self.DIALOG               = DialogManager(self)
        self.NOTIFICATION_MANAGER = NotificationManager(
            self,
            self.SETTINGS.notifications.notification_duration.value,
            self.SETTINGS.notifications.notification_queue_delay.value,
        )

        ## -- PLUGINS

        self.MIXINS = MixinManager(self)
        self.public = PublicRegistry()
        self.plugin_dirs = [
            Asset(Path("src") / "assets" / "bundled"),
            Asset("plugins"),
        ]

        ## -- PAGES

        self.SWITCHING_PAGE = False
        self.PAGE           = None
        self.PAGES          = PageRegistry(self)
        self.DEFAULT_PAGE   = ""

        from src.pages.settings import SettingsPage
        from src.pages.root import RootPage
        self.add_page("#settings", "Settings Page", SettingsPage)
        self.add_page("#root",     "Root Page",     RootPage)

        self.PLUGIN = PluginManager(self, self.plugin_dirs)
        self.PLUGIN.load_plugins()
        self.MIXINS.apply_mixins_to(self)

        self.page_host = QWidget(self.window)
        self.page_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.log("debug", "Application Pre-Initialized")

    ##UI BRIDGE

    def call_on_ui(self, fn: Callable) -> None:
        """Schedule fn() to run on the Qt main thread. Safe from any thread."""
        self.bridge.dispatch(fn)

    ##EVENTS

    def set_state(self, state_name: str, state) -> None:
        self.STATES[state_name] = state

    def get_state(self, state_name: str):
        return self.STATES.get(state_name)

    def subscribe_to_event(self, on_call_type: EVENTS, callable_: Callable,
                           call_index: int = -1) -> None:
        if callable_ in self.EVENTS["on_call"][on_call_type]:
            self.EVENTS["on_call"][on_call_type].remove(callable_)
        self.EVENTS["on_call"][on_call_type].insert(call_index, callable_)

    def unsubscribe_from_event(self, on_call_type: EVENTS, callable_: Callable) -> None:
        try:
            self.EVENTS["on_call"][on_call_type].remove(callable_)
        except Exception as e:
            self.log("error", f"Could not unsubscribe to {on_call_type} w/ {callable_}: {e}")

    def create_on_call_event(self, call_type: str) -> None:
        if call_type in EVENTS:
            raise Exception(f"on_call type '{call_type}' is a App ONLY event")
        self.EVENTS["on_call"][call_type] = []

    def trigger_on_call_event_iteration(self, on_call_type: str, event) -> None:
        if on_call_type in EVENTS:
            raise Exception(f"on_call type '{on_call_type}' is a Client ONLY event")
        self.iterate_event_callables(on_call_type, event)

    def iterate_event_callables(self, on_call_type: EVENTS, event,
                                hide_logging: bool = False) -> None:
        if not hide_logging:
            self.log("info", f"Event '{on_call_type}' was called")
        to_be_removed = []
        for callable_ in self.EVENTS["on_call"].get(on_call_type, []):
            try:
                callable_(event)
            except Exception as e:
                self.log("error", f"'{str(callable_)}' had an error: {e}")
                to_be_removed.append((on_call_type, callable_))
        for type_, callable_ in to_be_removed:
            self.unsubscribe_from_event(type_, callable_)

    ##INTERACTION

    def _on_global_interaction(self, event) -> None:
        """
        Called by InteractionWatcher for every qualifying mouse/touch
        event anywhere in the app. Fires on_interaction every time, and
        on_fresh_interaction additionally on the specific edge where
        this is the first interaction after a period of idleness (i.e.
        right as on_interaction_timeout's effect should end). Runs on
        the Qt UI thread, same as the event itself.
        """
        was_idle = self._interaction_idle
        self._interaction_idle      = False
        self._last_interaction_time = time.time()

        self.iterate_event_callables("on_interaction", event, True)
        if was_idle:
            self.iterate_event_callables("on_fresh_interaction", event, True)

    def _check_interaction_timeout(self) -> None:
        """
        Polled from update_thread() at the same cadence as on_update.
        Fires on_interaction_timeout exactly once per idle period, the
        moment application.interaction_timeout is first crossed with no
        interaction — not on every tick afterwards, and not at all
        while sitting on the Settings page, since that page already
        manages its own separate idle/return-home timeout and plugins
        like the Carousel shouldn't be popping anything up over it.
        """
        if self._interaction_idle:
            return
        if self.PAGE and self.PAGE.name == "#settings":
            return

        # .get() with a default rather than direct attribute access:
        # this setting is new (added under "application" alongside the
        # window settings) — an existing data.json from before it
        # existed won't have the key yet, and this shouldn't crash
        # just because someone hasn't re-saved their settings since
        # upgrading.
        timeout_ms = self.SETTINGS.get("application.interaction_timeout.value", 5000)
        if time.time() - self._last_interaction_time >= (timeout_ms / 1000):
            self._interaction_idle = True
            self.iterate_event_callables("on_interaction_timeout", None, True)

    ##LOGGING

    def log(self, level: EVENT_LEVELS, message: str,
            pointer=None, include_traceback: bool = False) -> None:
        now    = datetime.now()
        timeof = f"{now.year}/{now.month}/{now.day} {now.hour:02}:{now.minute:02}:{now.second:02}"

        if self.LOGGING and not self.LOGGING_FILE_CREATED:
            logdir  = Path("logs")
            logpath = logdir / "latest.log"
            ts      = f"{now.year}-{now.month}-{now.day}-{now.hour:02}-{now.minute:02}"
            logdir.mkdir(exist_ok=True)

            self.LOGGING_FILE_CREATED = True
            if logpath.exists():
                with open(logpath, "r") as lf:
                    lines = lf.readlines()
                lasttimeof = lines[0].strip() if lines else ts
                renamed = logdir / f"{lasttimeof}.log"
                if renamed.exists():
                    renamed.unlink()
                logpath.rename(renamed)

            self.LOG = open(logpath, "a")
            self.LOG.write(f"{ts}\n")

        alt_msg = f" {message}"
        message = message if message.startswith(" ") else alt_msg
        message = message if not message.strip().startswith("[") else message.strip()

        if not pointer:
            log_line = f"[{timeof}][{level.upper()[:4]}]{message}"
        else:
            log_line = f"[{timeof}][{level.upper()[:4]}]{message} FRM {str(pointer)}"

        print(log_line)
        if include_traceback:
            trace = traceback.format_exc()
            print(trace)

        if self.LOGGING and self.LOG:
            self.LOG.write(f"{log_line}\n")
            if include_traceback:
                self.LOG.write(f"{trace.strip()}\n")
            self.LOG.flush()

    ##NOTIFICATIONS

    def overlay(self, args: dict) -> None:
        self.NOTIFICATION_MANAGER.add_to_queue(args)

    def simple_notify(self, icon, title: str, body: str,
                      history: bool = True) -> None:
        if history and self.public.has("notification_history"):
            self.public.notification_history.add(icon, title, body, datetime.now())
        self.NOTIFICATION_MANAGER.add_to_queue({
            "icon":    icon,
            "title":   title,
            "body":    body,
            "bgcolor": COLORS.DARK.BG,
            "height":  90,
            "padding": 10,
            "anchor":  self.SETTINGS.notifications.notification_position.value,
        })

    ##PANELS

    def create_panel(self, content: QWidget = None, width: int = None,
                      edge: str = "right", bgcolor: str = "#1e1e1e",
                      key: str = None, destroy_on_close: bool = True,
                      on_created: Optional[Callable[[Panel], None]] = None
                      ) -> Optional[Panel]:
        """
        Build a basic Panel on the overlay system, optionally drop a
        content widget straight into it, and slide it into view
        immediately. Returns the Panel instance so the caller can hang
        onto it for a later .toggle()/.close_panel(), or just
        fire-and-forget it for a one-off panel.

        destroy_on_close defaults to True here (unlike Panel's own
        default of False): once this panel's close_panel() animation
        finishes, the widget is fully released — detached from
        OVERLAYS and deleted — rather than just hidden. A hidden-but-
        never-destroyed Panel still gets counted in every
        OverlayManager mask recompute and still receives events
        forever, since Qt's C++ parent-child ownership keeps it alive
        regardless of whether anything in Python still references it.
        That's exactly the leak this method used to have: build a new
        one-off Panel here every time (e.g. each Carousel rotation, or
        each API request), and they'd pile up indefinitely, getting
        slower the longer the app ran. Pass destroy_on_close=False if
        you genuinely want to build a *reusable* panel this way and
        toggle the same instance open/closed repeatedly — most callers
        don't, but if you do, that's the same pattern TilePanel/
        NotificationPanel use building Panel directly.

        Thread-safe: Panel is a real QWidget, and QWidgets must be
        built on the Qt main/UI thread. Calling this from a Qt slot
        (a button click, etc.) already puts you there, so this
        returns the Panel directly, same as always. Calling it from
        anywhere else — a Flask backend route, a voice command
        handler, a subscribed event callback, a background thread —
        building the widget right there would leave it only
        half-parented; Qt/your window manager would then place it
        wherever they like (usually dead centre) instead of anchored
        to the edge you asked for, since none of the geometry this
        class sets actually "took". This method detects that case,
        hops onto the UI thread for you via call_on_ui(), and returns
        None immediately since the Panel can't exist synchronously
        yet at that point — pass on_created if you need a reference to
        the finished panel once it exists (e.g. to toggle/close it
        later from that same background context).

        See src/ui/overlays.py:Panel for the underlying class — this is
        just the convenience entry point most plugins/pages should use
        instead of constructing Panel directly.
        """
        def _build() -> Panel:
            panel = Panel(self, width=width, edge=edge, bgcolor=bgcolor, key=key,
                           destroy_on_close=destroy_on_close)
            if content is not None:
                panel.add_content(content)
            panel.open_panel()
            return panel

        if QThread.currentThread() is self.app.thread():
            panel = _build()
            if on_created:
                on_created(panel)
            return panel

        def _dispatched() -> None:
            panel = _build()
            if on_created:
                on_created(panel)

        self.call_on_ui(_dispatched)
        return None

    ##PAGES

    def action(self, feature_path: str, *args, **kwargs):
        if self.PAGE and self.BUILT:
            features = self.PAGE.features()
            if len(features) > 0:
                return features.get_path(feature_path)["call"](*args, **kwargs)

    def has_page(self, query: str) -> bool:
        return self.PAGES.has_page(query)

    def get_page_data(self, name: str):
        """Returns the PageEntry for a registered page key, or None."""
        return self.PAGES.get_entry(name)

    def get_page(self):
        return self.PAGE

    def get_pages(self):
        return self.PAGES.keys()

    def add_page(self, key: str, display: str, page_class, owner: str = "client") -> None:
        """
        Register a page. owner defaults to "client" for the Client's
        own built-in pages (#root, #settings). Plugins registering
        their own pages should pass their own plugin key as owner —
        this is what lets PluginManager.unload_plugin() automatically
        clean up every page a plugin registered, the same way it
        already does for API endpoints (see API_REGISTRY.unregister).

        Without a real owner, an unloaded/reloaded plugin's old pages
        would stay registered under PageRegistry forever, which is
        exactly what caused the leftover blank window during hot
        reload: the stale page entry/instance never got torn down
        because nothing tracked who was responsible for cleaning it up.
        """
        self.PAGES.register(owner, key, display, page_class)

    def is_switching_page(self) -> bool:
        return self.SWITCHING_PAGE

    @mixin_target("client.goto")
    def goto(self, page: str, data: dict = None,
             override: bool = False, window_config: dict = {}) -> None:
        if self.PAGE and self.PAGE.name == page and not override:
            return

        entry = self.PAGES.get_entry(page)
        if not entry:
            self.log("warning", f"goto() called with unregistered page '{page}' — ignoring")
            return

        self.SWITCHING_PAGE = True
        self.log("info", f"initializing / going to page '{page}'")

        if self.PAGE:
            old_entry = self.PAGES.get_entry(self.PAGE.name)

            if hasattr(self.PAGE, "stop"):
                self.PAGE.stop()
            self.iterate_event_callables(
                "on_leave",
                {"from": {"name": self.PAGE.name, "data": self.PAGE.data},
                 "to":   {"name": page, "data": data}},
            )
            self.PAGE.hide()

            #the previous page instance is fully torn down here, not
            #just hidden. Leaving it merely hidden (the old behaviour)
            #meant it stayed alive indefinitely as a hidden child of
            #page_host — normally harmless, but during plugin hot
            #reload the OLD page class/instance could end up coexisting
            #with a freshly reloaded one with the same key, which is
            #what produced the leftover blank window: two real QWidget
            #instances under page_host, one of them stale and orphaned
            #from its now-unloaded module.
            self.PAGE.setParent(None)
            self.PAGE.deleteLater()
            if old_entry:
                old_entry.instance = None

        entry.page_class = self.MIXINS.apply_mixins_to(entry.page_class)
        self.PAGE = entry.page_class(self, data)
        entry.instance = self.PAGE

        self.PAGE.setParent(self.page_host)
        w = int(self.SETTINGS.application.window.size.value[0])
        h = int(self.SETTINGS.application.window.size.value[1])
        self.PAGE.setGeometry(0, 0, w, h)
        self.PAGE.show()
        self.PAGE.raise_()
        self.OVERLAYS.raise_()

        if window_config:
            self.configure(**window_config)

        if hasattr(self.PAGE, "start"):
            self.PAGE.start()

        self.SWITCHING_PAGE = False
        self.iterate_event_callables("on_visit", {"page": {"name": page, "data": data}})

    ##BUILD

    @mixin_target("client.build.setup")
    def internal_build_setup(self) -> None:
        pass

    @mixin_target("client.build")
    def build(self) -> None:
        self.log("info", "Building Application...")
        self.BUILT = False

        w = int(self.SETTINGS.application.window.size.value[0])
        h = int(self.SETTINGS.application.window.size.value[1])

        self.window.setWindowTitle(self.WINDOW_NAME)
        self.window.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        set_style(self.window, "main", "main-window", object_tag="QMainWindow")

        #register fonts
        fonts_dir = Path("src") / "assets" / "fonts"
        for font_file in fonts_dir.glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(font_file))

        self.window.resize(w, h)
        self.window.move(
            (self.app.primaryScreen().size().width()  - w) // 2,
            (self.app.primaryScreen().size().height() - h) // 2,
        )

        self.page_host.setGeometry(0, 0, w, h)
        self.page_host.setParent(self.window)
        self.page_host.show()

        self.OVERLAYS.setParent(self.window)
        self.OVERLAYS.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # NOTE: deliberately NOT setting WA_TransparentForMouseEvents here.
        # OverlayManager now manages its own click-passthrough via a mask
        # that tracks its visible children (see src/ui/overlays.py). Setting
        # this attribute on the manager itself would make Qt skip it — and
        # everything inside it — during hit-testing, regardless of the mask.
        self.OVERLAYS.setGeometry(0, 0, w, h)
        self.OVERLAYS.show()
        self.OVERLAYS.raise_()

        self.internal_build_setup()

        self.window.show()
        self.BUILT = True

        def sync_overlay():
            self.OVERLAYS.raise_()
            self.NOTIFICATION_MANAGER.reset_initial_delay(1.0)

        QTimer.singleShot(300, sync_overlay)

        self.start_all_backend_services()

        self.log("info", f"Startup Time: {round(time.time() - self.START_TIME, 3)}s")

    ##WINDOW

    @mixin_target("client.configure")
    def configure(self, x: int = None, y: int = None,
                  w: int = None, h: int = None,
                  maximizable: bool = True,
                  bgcolor: str = None,
                  re_center: bool = False) -> None:
        if x is not None: self.window.move(x, self.window.y())
        if y is not None: self.window.move(self.window.x(), y)
        if w is not None: self.window.resize(w, self.window.height())
        if h is not None: self.window.resize(self.window.width(), h)
        if bgcolor:
            set_style(self.window, "main", "main-window", object_tag="QMainWindow",
                      override={"*": {"background-color": bgcolor}})
        if re_center and not (x or y):
            screen = self.app.primaryScreen().size()
            self.window.move(
                (screen.width()  - self.window.width())  // 2,
                (screen.height() - self.window.height()) // 2,
            )
        self.log("info", f"Configuration changed to {(x, y, w, h, maximizable, bgcolor)}")

    def toggle_fullscreen(self, event=None) -> None:
        if self.window.isFullScreen():
            self.window_should_lock = False
            self.window_locked      = False
            self.window.showNormal()
        else:
            self.window_should_lock = True
            self.window_locked      = False
            self.window.showFullScreen()

    def title(self, text: str = "") -> None:
        title = f"{self.WINDOW_NAME} | {text}" if text else self.WINDOW_NAME
        self.window.setWindowTitle(title)
        self.log("info", f"Title changed to '{title}'")

    def get_window(self):
        return self.window

    def core(self):
        return self.window

    def on_window_resized(self, new_w: int, new_h: int) -> None:
        self.page_host.setGeometry(0, 0, new_w, new_h)
        self.OVERLAYS.setGeometry(0, 0, new_w, new_h)
        self.OVERLAYS.update_geometry(new_w, new_h)
        if self.PAGE:
            self.PAGE.setGeometry(0, 0, new_w, new_h)

    ##BACKEND

    def resync_time(self) -> None:
        if _platform.system() != "Windows":
            return
        try:
            self.log("info", "Resyncing Machine Time ...")
            result = subprocess.run(
                ["schtasks", "/run", "/tn", "ResyncTime"],
                text=True, check=True,
            )
            msg = result.stdout.strip() if result.stdout else "Assumed Completion."
            self.log("info", f"Time Resync Results: {msg}")
        except subprocess.CalledProcessError as e:
            self.log("warning", f"Failed to Resync Time: {e}")

    def start_api_service(self) -> None:
        self.backend = FlaskApp(self)
        self.THREADS.create(
            "__backend_service_thread",
            FlaskService,
            self,
            self.backend,
        )
        self.THREADS.start("__backend_service_thread")

    def start_all_backend_services(self) -> None:
        self.start_api_service()
        self.start_update()
        if self.STT:
            self.STT.start()

    ##UPDATE THREAD

    @mixin_target("client.start_update")
    def start_update(self) -> None:
        self.log("info", "Update Thread Starting")
        self.THREADS.create(
            name   = "__client_update_thread",
            target = self.update_thread,
        )
        self.THREADS.start("__client_update_thread")

    def update_thread(self, stop_event) -> None:
        while not stop_event.is_set():
            if self.BUILT:
                if self.RESTART:
                    break

                #hourly GC and time resync
                if time.time() - self.LAST_COLLECTION >= 3600:
                    self.LAST_COLLECTION = time.time()

                    before_mb       = self._process.memory_info().rss / (1024 * 1024)
                    overlays_before = len(self.OVERLAYS.children())

                    # Gives plugins a chance to drop their own dead
                    # references — cached widgets, stale Panel
                    # instances, anything sitting in a dict/list that's
                    # outlived its usefulness — BEFORE the actual
                    # collection pass below, so that pass has something
                    # real to reclaim. Runs on this thread, same as
                    # on_update — if a handler touches a widget, it
                    # needs to hop onto call_on_ui() itself.
                    #
                    # IMPORTANT: this only ever helps with Python-level
                    # reference cycles. It does nothing for a QWidget
                    # that's still alive because it's still parented to
                    # something — gc.collect() has no visibility into
                    # (or power over) Qt's C++ ownership. That kind of
                    # leak needs an actual setParent(None)/deleteLater()
                    # somewhere (see Panel._destroy() in
                    # src/ui/overlays.py for the pattern), not a GC pass.
                    # on_collection is for the former; don't expect it
                    # to fix the latter on its own.
                    self.iterate_event_callables("on_collection", None, True)
                    collected = gc.collect()

                    after_mb       = self._process.memory_info().rss / (1024 * 1024)
                    overlays_after = len(self.OVERLAYS.children())

                    self.log(
                        "info",
                        f"[Collection] gc freed {collected} objects — "
                        f"RSS {before_mb:.1f}MB -> {after_mb:.1f}MB ({after_mb - before_mb:+.1f}MB), "
                        f"OVERLAYS children {overlays_before} -> {overlays_after}"
                        + (f" (+{overlays_after - overlays_before}, worth investigating if this keeps climbing)"
                           if overlays_after > overlays_before else ""),
                    )

                    self.resync_time()

                #fire initialized callables once
                if not self.get_state("initialized"):
                    self.set_state("initialized", True)
                    self.call_on_ui(
                        lambda: (
                            self.iterate_event_callables("initialized", None, True),
                            self.PLUGIN.build_plugins(),
                        )
                    )

                #navigate to home page once
                if not self.get_state("home_page_setup"):
                    self.set_state("home_page_setup", True)
                    def goto_default():
                        target = self.DEFAULT_PAGE
                        if not target or not self.has_page(target):
                            if target:
                                self.log("warning", f"Default page '{target}' not registered — showing RootPage")
                            else:
                                self.log("info", "No default page set — showing RootPage")
                            target = "#root"
                        self.goto(target)
                    self.call_on_ui(goto_default)

                #track window size changes
                def check_size():
                    if not self.BUILT:
                        return
                    w      = self.window.width()
                    h      = self.window.height()
                    stored = self.SETTINGS.application.window.size.value
                    if w > stored[0]:
                        self.SETTINGS.application.window.size.value = [w, h]

                self.call_on_ui(check_size)

                self.call_on_ui(self.NOTIFICATION_MANAGER.update)

                self.iterate_event_callables("on_update", None, True)
                self._check_interaction_timeout()

                #auto fullscreen lock
                if self.SETTINGS.application.window.auto_lock and \
                        not self.window_locked and self.window_should_lock:
                    self.window_locked = True

                    def go_fullscreen():
                        self.window.showFullScreen()
                        w = self.window.width()
                        h = self.window.height()
                        self.SETTINGS.application.window.size.value = [w, h]
                        self.dump(self.SETTINGS.as_dict(), self.DATA)

                    self.call_on_ui(go_fullscreen)

                time.sleep(0.05)

        if self.RESTART:
            self.call_on_ui(self.stop)

    ##HELPERS

    def uuid(self) -> str:
        return str(_uuid.uuid4())

    def open(self, dialog) -> None:
        self.DIALOG.open(dialog)

    def close(self, event=None, dialog=None) -> None:
        self.DIALOG.close()

    def register_asset(self, key: str, asset: Asset, forced_type: str) -> None:
        if not forced_type and not asset.is_dir():
            for ext in asset.suffixes:
                t = ext.upper().lstrip(".")
                self.ASSETS.setdefault(t, {})[key] = asset
        elif forced_type:
            self.ASSETS.setdefault(forced_type.upper(), {})[key] = asset
        else:
            t = "FOLDER" if asset.is_dir() else "FILE"
            self.ASSETS.setdefault(t, {})[key] = asset

    def asset(self, type_: str, key: str):
        typed = self.ASSETS.get(type_)
        return typed.get(key) if typed else None

    def show_runtime_state(self) -> None:
        print("\n--- Threads ---")
        for t in thread_enum():
            print(f"{t.name} (Alive={t.is_alive()}, Daemon={t.daemon})")
        print("\n--- Processes ---")
        for p in multiprocessing.active_children():
            print(f"PID={p.pid}, Alive={p.is_alive()}")

    @mixin_target("client.update")
    def update(self, fn: Optional[Callable] = None,
               callback_on_except: Optional[Callable] = None):
        if not self.BUILT:
            return
        if fn:
            self.call_on_ui(fn)
        else:
            if self.PAGE:
                self.call_on_ui(self.PAGE.update)

    @mixin_target("client.load")
    def load(self, path) -> dict:
        self.log("info", f"Loading -> {Path(path).name}")
        with open(path, "r") as f:
            return json.load(f)

    @mixin_target("client.dump")
    def dump(self, obj, path) -> None:
        self.log("info", f"Dumping -> {path}")
        with open(path, "w") as f:
            json.dump(obj, f, indent=4)

    def load_or_create_client_id(self) -> str:
        id_path = self.DATAPATH / "client.id"
        if id_path.exists():
            return id_path.read_text().strip()
        #generate short human-readable ID: 4 groups of 4 hex chars
        raw       = _uuid.uuid4().hex.upper()
        client_id = f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"
        id_path.write_text(client_id)
        return client_id

    def create_user_data_files(self) -> None:
        if not self.DATAPATH.exists():
            self.log("info", f"Creating DATA Folder @ {self.DATAPATH}")
            self.DATAPATH.mkdir(parents=True, exist_ok=True)
        if not self.DATA.exists():
            self.log("info", f"Creating DATA file @ {self.DATA}")
            shutil.copy(
                Path("src") / "assets" / "data" / "new-template.json",
                self.DATA,
            )

    ##LIFECYCLE

    def stop(self, event=None) -> None:
        self.log("info", "Closing Client ...")
        self.iterate_event_callables("on_close", event)
        self.PLUGIN.unload_plugins()
        self.dump(self.SETTINGS.as_dict(), self.DATA)
        self.cleanup()
        self.window.hide()
        self.app.quit()

    @mixin_target("client.cleanup")
    def cleanup(self) -> None:
        self.log("info", "Running Cleanup")
        if self.STT:
            self.STT.stop()
        for thread_key in self.THREADS.threads:
            if self.THREADS.is_active(thread_key):
                self.log("info", f"Stopping Thread: {thread_key}")
                self.THREADS.stop(thread_key)
                self.THREADS.wait_for_stop(thread_key)
        self.log("info", "Cleanup Finished!")

    def restart(self) -> None:
        self.RESTART = True

    def run(self) -> None:
        self.log("info", f"Running Application -> {APP_NAME}")
        self.resync_time()
        self.build()
        exit_code = self.app.exec()

        if self.UPDATE:
            subprocess.Popen([sys.executable])

        if self.RESTART:
            subprocess.Popen([sys.executable] + ["force"])

        if self.LOG:
            self.LOG.close()

        sys.exit(exit_code)