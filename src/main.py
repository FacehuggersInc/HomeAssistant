from __future__ import annotations

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
from threading import Thread, RLock, enumerate as thread_enum
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
from src.registries.secret_registry import SecretRegistry
from src.registries.quick_access_registry import QuickAccessRegistry
from src.registries.user_registry import UserRegistry
from src.backend import FlaskApp, FlaskService
from src.assistant.skill import Skill, SkillIntentEngine
from src.assistant.stt import STTProcessing
from src.assistant.tts import TTSProcessing
from src.ui.overlays import OverlayManager, NotificationManager, DialogManager, Panel
from src.styling import COLORS, load_styles, set_style
from src.constants import (
    APP_NAME, EVENTS, EVENT_LEVELS, CLIENT_EVENT_NAMES,
    EXIT_OK, EXIT_UPDATE, EXIT_RESTART,
    get_data_dir,
)

if _platform.system() != "Windows":
    os.environ["QT_STYLE_OVERRIDE"] = ""


##UI BRIDGE

class UIBridge(QObject):

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

    MIN_INTERACTION_TIMEOUT_MS = 1000

    @mixin_target("client.__init__")
    def __init__(self):
        self.START_TIME  = time.time()
        self.WINDOW_NAME = APP_NAME

        ## -- QT

        # Before the QApplication, and it has to be. QtWebEngine refuses to
        # start without a shared OpenGL context, and the attribute is ignored
        # once the application object exists - which is why an installed
        # PyQt6-WebEngine still fell back to a rendered image.
        if QApplication.instance() is None:
            try:
                QApplication.setAttribute(
                    Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
                # Imported here too: some builds require the module to be
                # loaded before the application is created, not merely before
                # a view is constructed.
                import PyQt6.QtWebEngineCore  # noqa: F401
            except Exception:
                pass

        self.app    = QApplication.instance() or QApplication(sys.argv)
        self.window = AppWindow(self)
        self.bridge = UIBridge()

        self._last_interaction_time = time.time()
        self._interaction_idle      = False
        self._interaction_watcher   = InteractionWatcher(self)
        self.app.installEventFilter(self._interaction_watcher)

        self.BUILT   = False
        self.RESTART = False
        self.UPDATE  = False

        self.LAST_COLLECTION = time.time()
        self._last_slow_tick = 0.0                #see update_thread()'s 1Hz block
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
                "on_assistant_cancelled":   [],
                "on_assistant_fallback":    [],
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

        self.log("info", "[Styling] Loading Styles")
        load_styles()
        

        self.DATAPATH = Asset(get_data_dir(APP_NAME))
        self.DATA     = Asset(self.DATAPATH / f"{APP_NAME.replace(' ', '')}.json")
        self.register_asset("data", self.DATA, "json")
        self.create_user_data_files()

        ## -- CLIENT ID

        # No longer an API credential - devices authenticate with their own
        # tokens. Kept as a stable per-install identifier: the calendar's
        # place cache derives its encryption key from it, and anything else
        # wanting "this machine, consistently" can use it.
        self.CLIENT_ID = self.load_or_create_client_id()

        ## -- SETTINGS

        self.SETTINGS = Dynaconf(settings_files=[str(self.DATA)])
        # Dynaconf's reload() empties its store before repopulating, so a read
        # from another thread landing in that window raises AttributeError.
        # apply_settings()/setting() serialise against that.
        self.SETTINGS_LOCK = RLock()
        bg_asset = Asset(self.SETTINGS.home.images.value)
        bg_asset.mark_uploadable()
        self.register_asset("background_images", bg_asset, "FOLDER")

        ## -- ASSISTANT

        self.ASSIST_VOICE_ACTIVITY_LEVEL = 0.0
        self.ASSIST_STATUS               = "DORMANT"
        self.SKILLS = SkillIntentEngine(self)
        self.STT    = None
        self.TTS    = None
        self._assistant_config: tuple = ()

        ## -- APIS
        self.API_REGISTRY = APIRegistry(self)
        self.SECRETS      = SecretRegistry(self)
        for _key in self.CORE_SECRETS:
            self.SECRETS.register("client", _key)
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
        self.QUICK          = QuickAccessRegistry(self)
        self.USERS          = UserRegistry(
            self, get_data_dir(APP_NAME) / "users.json")
        self.DEFAULT_PAGE   = ""

        from src.pages.settings import SettingsPage
        from src.pages.root import RootPage
        from src.pages.webpage import WebPage
        self.add_page("#settings", "Settings Page", SettingsPage)
        self.add_page("#root",     "Root Page",     RootPage)
        # Registered by the client, so anything can send somebody to a web
        # page without carrying a browser of its own.
        self.add_page("#webpage",  "Web",           WebPage)

        self.PLUGIN = PluginManager(self, self.plugin_dirs)
        self.PLUGIN.load_plugins()
        self.MIXINS.apply_mixins_to(self)

        self.page_host = QWidget(self.window)
        self.page_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.log("debug", "Application Pre-Initialized")

    ##UI BRIDGE

    def call_on_ui(self, fn: Callable) -> None:
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
        if call_type in CLIENT_EVENT_NAMES:
            raise Exception(f"on_call type '{call_type}' is a App ONLY event")
        self.EVENTS["on_call"][call_type] = []

    def trigger_on_call_event_iteration(self, on_call_type: str, event) -> None:
        if on_call_type in CLIENT_EVENT_NAMES:
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
        was_idle = self._interaction_idle
        self._interaction_idle      = False
        self._last_interaction_time = time.time()

        self.iterate_event_callables("on_interaction", event, True)
        if was_idle:
            self.iterate_event_callables("on_fresh_interaction", event, True)

    def _check_interaction_timeout(self) -> None:
        if self._interaction_idle:
            return

        # A page can refuse the idle clock outright. A web page is read at a
        # person's own pace and produces no interaction while it is - so
        # timing out behind one is measuring the wrong thing.
        try:
            if getattr(self.PAGE, "blocks_idle", False):
                self._last_interaction_time = time.time()
                return
        except Exception:
            pass

        # A dialog is a question waiting for an answer. Going idle behind one
        # lets an idle plugin cover it, or dismiss the page underneath it,
        # while the user is still reading - so the clock does not run at all
        # while one is open, and the timer restarts when it closes.
        try:
            if self.DIALOG.get() is not None:
                self._last_interaction_time = time.time()
                return
        except Exception:
            pass

        timeout_ms = self.SETTINGS.get("application.interaction_timeout.value", 5000)

        timeout_ms = max(timeout_ms, self.MIN_INTERACTION_TIMEOUT_MS)

        if time.time() - self._last_interaction_time >= (timeout_ms / 1000):
            self._interaction_idle = True
            self.log("info", f"[Client] on_interaction_timeout fired (idle >= {timeout_ms}ms)")
            self.iterate_event_callables("on_interaction_timeout", None, True)

    ##LOGGING

    def _open_log_file(self) -> None:
        if self.LOG:
            self.LOG.close()

        now     = datetime.now()
        logdir  = Path("logs")
        logpath = logdir / "latest.log"
        ts      = f"{now.year}-{now.month}-{now.day}-{now.hour:02}-{now.minute:02}"
        logdir.mkdir(exist_ok=True)

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
        self.LOGGING_FILE_CREATED = True

    def log(self, level: EVENT_LEVELS, message: str,
            pointer=None, include_traceback: bool = False) -> None:
        now    = datetime.now()
        timeof = f"{now.year}/{now.month}/{now.day} {now.hour:02}:{now.minute:02}:{now.second:02}"

        if self.LOGGING and not self.LOGGING_FILE_CREATED:
            self._open_log_file()

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
        return self.PAGES.get_entry(name)

    def get_page(self):
        return self.PAGE

    def get_pages(self):
        return self.PAGES.keys()

    def add_page(self, key: str, display: str, page_class, owner: str = "client") -> None:
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
        self.OVERLAYS.setGeometry(0, 0, w, h)
        self.OVERLAYS.show()
        self.OVERLAYS.raise_()

        self.internal_build_setup()

        self.build_quick_settings()
        self.build_update_checker()
        self.build_user_approvals()

        self.window.show()
        self.BUILT = True

        def sync_overlay():
            self.OVERLAYS.raise_()
            self.NOTIFICATION_MANAGER.reset_initial_delay(1.0)

        QTimer.singleShot(300, sync_overlay)

        self.start_all_backend_services()

        self.log("info", f"Startup Time: {round(time.time() - self.START_TIME, 3)}s")

        QTimer.singleShot(1200, self.prompt_for_plugin_dependencies)
        QTimer.singleShot(1600, self.start_assistant)
        self.subscribe_to_event("on_settings_saved", self.on_assistant_settings_saved)

    def answer(self, icon: str, title: str, lines: list = None,
               tint: str = "#4f9de0", timeout: int = None,
               speak: str = None) -> None:
        """
        Show an answer, and say it.

        The panel is what a skill uses when it has something to show; a
        notification is for something to report. Both go through here so a
        skill never has to know which UI class does it.
        """
        if speak:
            try:
                self.say(speak)
            except Exception:
                pass

        def build():
            try:
                from src.ui.panels.answer import AnswerPanel
                panel = AnswerPanel(self, icon, title, lines, tint, timeout)
                panel.open_panel()
            except Exception as e:
                # Falls back rather than losing the answer - a spoken reply
                # with nothing on screen is worse than a notification.
                self.log("warning", f"[Client] Answer panel failed: {e}")
                self.simple_notify(icon, title, " ".join(str(l) for l in (lines or [])))

        self.call_on_ui(build)

    ##USERS

    def build_user_approvals(self) -> None:
        """
        Ask about waiting devices, one at a time.

        Polled off the tick rather than pushed from the request, because the
        request arrives on a Flask worker thread and a dialog cannot be built
        there. One at a time because two devices asking together would stack
        two dialogs and the second would be answered blind.
        """
        self._approval_open = False
        self.subscribe_to_event("on_update", self._check_user_approvals)

    def _check_user_approvals(self, event=None) -> None:
        if self._approval_open:
            return
        waiting = self.USERS.waiting()
        if not waiting:
            return

        request = waiting[0]
        self._approval_open = True

        def approve():
            # Approved and held for naming in one step.
            #
            # The device polls every second and a half, so approving without
            # the flag meant it was told "you're in" and had navigated away
            # before anybody answered the second dialog. Held by default and
            # released by whichever end supplies a name.
            self.USERS.approve(request.token, let_user_name=True)
            self._approval_open = False
            self._ask_for_name(request)

        def deny():
            self.USERS.deny(request.token)
            self._approval_open = False

        self.confirm(
            "Allow this device?",
            f"{request.name}\n{request.address or 'unknown address'}",
            on_confirm   = approve,
            on_cancel    = deny,
            confirm_text = "Allow",
            cancel_text  = "Deny",
            detail       = ("It will be able to read and change anything the API "
                            "exposes, including settings and the calendar. You can "
                            "revoke it later under Settings, Users."),
        )

    def _ask_for_name(self, request) -> None:
        """
        Name the device that was just let in.

        The device announced itself as something like "Firefox on Linux",
        which says what it is and nothing about whose it is. Whoever is at the
        panel usually knows; when they do not, the device is asked instead.
        """
        def name_here():
            from src.ui.keyboard import KeyboardDialog
            from PyQt6.QtWidgets import QLineEdit

            holder = QLineEdit(request.name)

            def done(text: str):
                if text.strip():
                    # rename() clears awaiting_name, which is what lets the
                    # device through on its next poll.
                    self.USERS.rename(request.token, text.strip())
                    self.simple_notify("check", "Users",
                                       f"'{text.strip()}' can now connect.")
                else:
                    self.simple_notify("account-question", "Users",
                                       "No name given - the device will be asked.")

            self.dialog(KeyboardDialog(self, holder, mode="text",
                                       label="Name for this user", on_done=done))

        def let_them():
            # Already held; this only says so out loud.
            self.simple_notify("account-question", "Users",
                               "Asked the device to name itself.")

        self.confirm(
            "Who is this?",
            f"{request.name} can now connect. Give them a name, or let them "
            f"choose one themselves.",
            on_confirm   = name_here,
            on_cancel    = let_them,
            confirm_text = "Name them",
            cancel_text  = "Let them decide",
            detail       = "You can rename anyone later under Settings, Users.",
        )

    ##QUICK SETTINGS

    def build_quick_settings(self) -> None:
        """
        Global controls: the dimmer, the top-edge gesture and the panel.

        Built once against the client rather than per page. These used to be a
        drawer each page had to construct for itself, which meant a control
        existed only where someone had remembered to add it.
        """
        from src.ui.dimmer import Dimmer
        from src.ui.quick_settings import QuickSettings
        from src.ui.controls.edge_swipe import TopEdgeSwipe

        # Brightness stand-in. Added through the overlay manager rather than
        # re-parented by hand: add() routes a WA_TransparentForMouseEvents
        # child onto the passthrough layer AND installs the filter that keeps
        # that layer's mask in step with it. Re-parenting directly skips the
        # filter, and the dim would never appear.
        self.DIMMER = Dimmer(self)
        self.OVERLAYS.add("BACKGROUND", self.DIMMER)
        self.DIMMER.sync_geometry()
        self.DIMMER.hide()   # add() shows it; nothing to dim at full brightness

        self.QUICK_SETTINGS = QuickSettings(self)

        self.EDGE_SWIPE = TopEdgeSwipe(self, self.QUICK_SETTINGS.open_panel)
        self.OVERLAYS.add("SYSTEM", self.EDGE_SWIPE)
        self.EDGE_SWIPE.sync_geometry()

        self.subscribe_to_event("on_interaction", self.QUICK_SETTINGS.on_interaction)

    def toggle_quick_settings(self) -> None:
        panel = getattr(self, "QUICK_SETTINGS", None)
        if panel is not None:
            panel.toggle()

    ##UPDATES

    UPDATE_CHECK_STARTUP_DELAY_MS = 20_000   # let the app settle before a network call

    def build_update_checker(self) -> None:
        """
        Poll GitHub for a newer commit and tell the user once when there is one.

        Deliberately not `updater.stage()` on a timer - that downloads the whole
        branch. This is one small API call, and nothing is fetched until the
        user actually asks for it.
        """
        self.UPDATE_AVAILABLE = False
        self.UPDATE_COMMIT = None
        self._update_notified_sha = None

        self._update_timer = QTimer()
        self._update_timer.timeout.connect(lambda: self.check_for_update(quiet=True))

        hours = self.update_check_hours()
        if hours <= 0:
            self.log("info", "[Update] Automatic update checks are turned off.")
            return

        self._update_timer.start(int(hours * 3600 * 1000))
        QTimer.singleShot(self.UPDATE_CHECK_STARTUP_DELAY_MS,
                          lambda: self.check_for_update(quiet=True))

    def update_check_hours(self) -> float:
        try:
            return float(self.setting("application.updates.check_interval.value", 6))
        except (TypeError, ValueError):
            return 6.0

    def check_for_update(self, quiet: bool = False,
                         on_result: Optional[Callable] = None) -> None:
        """
        Run the check off the UI thread.

        `quiet` suppresses the "you are up to date" reply, which is what the
        timer wants and a manual check does not. `on_result(available, commit,
        error)` is handed the outcome on the UI thread for callers that want to
        present it themselves rather than as a notification.
        """
        def finish(available, commit, error):
            if callable(on_result):
                self.call_on_ui(lambda: on_result(available, commit, error))

        def work():
            from src import update_check
            try:
                available, commit, reason = update_check.check()
            except Exception as e:
                self.log("warning", f"[Update] Check failed: {e}")
                if not quiet and on_result is None:
                    self.simple_notify("warning", "Update check", str(e))
                finish(False, None, e)
                return

            self.log("info", f"[Update] {reason}")
            self.UPDATE_AVAILABLE = available
            self.UPDATE_COMMIT = commit

            if not available:
                if not quiet and on_result is None:
                    self.simple_notify("check", "Up to date",
                                       "This is the latest version.")
                self.call_on_ui(self._refresh_update_button)
                finish(False, commit, None)
                return

            # Once per version. The timer keeps firing, and a panel that
            # re-notified every few hours about the same commit would be worse
            # than not notifying at all. The flag is set either way, so a
            # caller presenting its own dialog does not leave the timer free to
            # announce the same commit again afterwards.
            first_time = self._update_notified_sha != commit.sha
            self._update_notified_sha = commit.sha
            if first_time and on_result is None:
                self.simple_notify(
                    "download", "Update available",
                    f"{commit.summary} - {commit.age()}",
                )

            self.call_on_ui(self._refresh_update_button)
            finish(True, commit, None)

        Thread(target=work, name="__update_check", daemon=True).start()

    def _refresh_update_button(self) -> None:
        panel = getattr(self, "QUICK_SETTINGS", None)
        if panel is not None:
            try:
                panel.refresh_update_button()
            except RuntimeError:
                pass

    def begin_update(self, on_staged: Optional[Callable] = None) -> None:
        """
        Download, stage, then restart into the update.

        Shared by the API endpoint and the quick settings button so there is
        one code path that can be got wrong instead of two.
        """
        from src import updater

        def staging_thread():
            self.simple_notify("download", "Update", "Downloading update...")
            try:
                manifest = updater.stage(log=lambda m: self.log("info", f"[Update] {m}"))
            except updater.UpdateError as e:
                self.simple_notify("error", "Update Failed", str(e))
                self.log("error", f"[Client.begin_update] {e}")
                return
            self.simple_notify(
                "check", "Update",
                f"{manifest['file_count']} files staged. Restarting..."
            )
            if callable(on_staged):
                try:
                    on_staged(manifest)
                except Exception:
                    pass
            time.sleep(2)
            self.UPDATE = True
            self.call_on_ui(self.stop)

        Thread(target=staging_thread, name="__update_staging", daemon=True).start()

    ##ASSISTANT

    @property
    def wake_word(self) -> str:
        """Default wake word for skills. Plugins should read this rather than
        hardcoding one."""
        try:
            return str(self.SETTINGS.assistant.wake_word.value).strip().lower() or "alexa"
        except Exception:
            return "alexa"

    def assistant_enabled(self) -> bool:
        try:
            return bool(self.SETTINGS.assistant.enabled.value)
        except Exception:
            return False

    CORE_SECRETS = ("ELEVENLABS_KEY",)

    def secret(self, key: str, default: str = "") -> str:
        """
        Value of a secret the CLIENT owns.

        Scoped deliberately: a plugin reaching for another plugin's key
        through the Client gets the default. Plugins read their own keys with
        self.secret() (see src/plugin/template.py).
        """
        return self.SECRETS.get_for("client", key, default)

    def cancel_assistant(self, reason: str = "") -> bool:
        """Stop listening and return to the wake word. No-op when idle."""
        if self.STT is None:
            return False
        self.STT.cancel(reason)
        return True

    def say(self, text: str, thread: bool = True) -> bool:
        """
        Speak, if speech is available. Returns whether anything was said.

        Skills call this instead of client.TTS.play() so a missing
        ELEVENLABS_KEY degrades to silence rather than an AttributeError on
        None.
        """
        if not text or self.TTS is None or not getattr(self.TTS, "available", False):
            return False
        try:
            self.TTS.play(text, thread=thread)
            return True
        except Exception as e:
            self.log("warning", f"[Assistant] TTS failed: {e}")
            return False

    def assistant_config(self) -> tuple:
        """The settings the running assistant depends on. Compared on save to
        decide whether it needs restarting."""
        return (
            self.assistant_enabled(),
            str(self.setting("assistant.input_device.value", "") or "").strip(),
            str(self.setting("assistant.model.value", "tiny.en") or "tiny.en"),
            self.wake_word,
            int(self.setting("assistant.session_silence.value", 800)),
            bool(self.setting("assistant.tts_enabled.value", True)),
            # Whether a key exists, never the key. Entering one in Settings
            # should bring speech up without a manual restart.
            self.SECRETS.is_set("ELEVENLABS_KEY"),
        )

    def stop_assistant(self) -> None:
        if self.STT is not None:
            try:
                self.STT.stop()
            except Exception as e:
                self.log("warning", f"[Assistant] Error stopping STT: {e}")
            self.STT = None
        self.TTS = None
        self.ASSIST_STATUS = "DORMANT"
        self.ASSIST_VOICE_ACTIVITY_LEVEL = 0.0

    def on_assistant_settings_saved(self, event=None) -> None:
        """
        Restart the assistant when one of its settings changes.

        Without this, switching the model or microphone in Settings did
        nothing until the next launch - and switching to a model that is not
        downloaded yet never prompted, since the prompt lives in
        start_assistant().
        """
        current = self.assistant_config()
        if current == self._assistant_config:
            return

        was_enabled = self._assistant_config[0] if self._assistant_config else False
        self._assistant_config = current

        self.log("info", "[Assistant] Settings changed, restarting.")
        self.stop_assistant()

        if not current[0]:
            if was_enabled:
                self.simple_notify("assistant", "Assistant", "Voice assistant turned off.")
            return

        # Deferred: this fires from inside return_and_save(), which then shows
        # a notification and navigates away. Opening the model-download prompt
        # in the middle of that put it underneath the page switch.
        QTimer.singleShot(600, self.start_assistant)

    def start_assistant(self) -> None:
        from src.assistant import audio

        self._assistant_config = self.assistant_config()

        if not self.assistant_enabled():
            self.log("info", "[Assistant] Disabled in settings.")
            return

        device_name = str(getattr(self.SETTINGS.assistant.input_device, "value", "") or "").strip()
        model = str(getattr(self.SETTINGS.assistant.model, "value", "tiny.en") or "tiny.en")

        ok, reason = audio.available()
        if not ok:
            self.log("warning", f"[Assistant] Audio unavailable: {reason}")
            self.simple_notify("error", "Assistant", "Voice assistant unavailable.")
            self.alert("Voice assistant unavailable",
                       "Speech-to-text could not start. Everything else works normally.",
                       detail=reason)
            return

        for d in audio.input_devices():
            self.log("info", f"[Assistant] Input device {d['index']}: {d['name']}"
                             f"{' (default)' if d['is_default'] else ''}")

        device, note = audio.resolve(device_name)
        if note:
            self.log("warning", f"[Assistant] {note}")
            self.simple_notify("assistant", "Assistant", note)

        ok, reason = audio.probe(device)
        if not ok:
            self.log("warning", f"[Assistant] Microphone probe failed: {reason}")
            self.simple_notify("error", "Assistant", "Microphone unavailable.")
            self.alert("Microphone unavailable",
                       "Speech-to-text could not open the microphone.",
                       detail=reason)
            return

        if not audio.model_is_cached(model):
            size = audio.model_size_hint(model)
            self.confirm(
                "Download speech model?",
                f"The voice assistant needs the '{model}' Whisper model, which "
                f"is not on this machine yet. It downloads once and is reused "
                f"afterwards.",
                detail=f"Model: {model}\nApproximate size: {size}",
                confirm_text="Download",
                cancel_text="Not Now",
                on_confirm=lambda: self._launch_assistant(device, model),
                on_cancel=lambda: self.log("info", "[Assistant] Model download declined."),
            )
            return

        self._launch_assistant(device, model)

    def _start_tts(self) -> None:
        if not self.setting("assistant.tts_enabled.value", True):
            self.TTS = None
            self.log("info", "[Assistant] Spoken replies are disabled in settings.")
            return
        try:
            self.TTS = TTSProcessing(self)
        except Exception as e:
            self.TTS = None
            self.log("warning", f"[Assistant] TTS failed to initialise: {e}")
            return
        if not self.TTS.available:
            self.log("warning", f"[Assistant] TTS unavailable: {self.TTS.error}")
            self.simple_notify("assistant", "Assistant",
                               "Voice replies are off (no ElevenLabs key).")

    def _launch_assistant(self, device, model: str) -> None:
        from src.assistant import audio

        self._start_tts()

        try:
            self.STT = STTProcessing(
                self,
                input_device = device,
                model        = model,
                wake_words   = [self.wake_word],
                session_silence_ms = int(self.setting("assistant.session_silence.value", 800)),
            )
            self.STT.start()
            self.log("info", f"[Assistant] Listening on {audio.describe(device)} "
                             f"for '{self.wake_word}'.")
        except Exception as e:
            self.STT = None
            self.log("error", f"[Assistant] Failed to start: {e}")
            self.simple_notify("error", "Assistant", "Speech-to-text failed to start.")
            self.alert("Voice assistant failed to start",
                       "Everything else works normally.", detail=str(e))

    def prompt_for_plugin_dependencies(self) -> None:
        pending = self.PLUGIN.pending_plugins(include_declined=False)
        if not pending:
            return

        from src.plugin import dependencies as deps
        if not deps.in_venv():
            self.log("warning",
                     "[Dependencies] Not in a virtualenv — skipping the install prompt. "
                     f"{len(pending)} plugin(s) remain unloaded.")
            self.simple_notify(
                "error", "Plugins",
                f"{len(pending)} plugin(s) need packages, but the app is not "
                "running inside a virtualenv."
            )
            return

        from src.ui.dialogs import DependencyDialog
        self.dialog(DependencyDialog(self, pending))

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

        # Both span the window and neither is laid out by anything, so they
        # have to be told.
        for widget in (getattr(self, "DIMMER", None), getattr(self, "EDGE_SWIPE", None)):
            if widget is not None:
                try:
                    widget.sync_geometry()
                except RuntimeError:
                    pass

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
        # STT is started by start_assistant(), after build(), so it can probe
        # the microphone and prompt before touching anything.

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

                    #see "on_collection" in the README for what plugins should do with this
                    self.iterate_event_callables("on_collection", None, True)
                    collected = gc.collect()
                    pruned    = self.TIMEOUTS.prune()

                    after_mb       = self._process.memory_info().rss / (1024 * 1024)
                    overlays_after = len(self.OVERLAYS.children())

                    self.log(
                        "info",
                        f"[Collection] gc freed {collected} objects, pruned {pruned} stale timeouts — "
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
                #
                # Throttled to 1Hz. This and the auto-lock read below both ran
                # on every pass of a 20Hz loop, which meant ~60 queued UI
                # dispatches a second and 40 settings reads a second, forever,
                # to notice a window resize and a flag that changes once.
                now_ts = time.time()
                if now_ts - self._last_slow_tick >= 1.0:
                    self._last_slow_tick = now_ts

                    def check_size():
                        if not self.BUILT:
                            return
                        w      = self.window.width()
                        h      = self.window.height()
                        stored = self.setting("application.window.size.value")
                        if stored and w > stored[0]:
                            self.SETTINGS.application.window.size.value = [w, h]

                    self.call_on_ui(check_size)

                    #auto fullscreen lock
                    auto_lock = self.setting("application.window.auto_lock")
                    if isinstance(auto_lock, dict):
                        auto_lock = auto_lock.get("value")
                    if auto_lock and not self.window_locked and self.window_should_lock:
                        self.window_locked = True

                        def go_fullscreen():
                            self.window.showFullScreen()
                            w = self.window.width()
                            h = self.window.height()
                            self.SETTINGS.application.window.size.value = [w, h]
                            self.dump(self.settings_dict(), self.DATA)

                        self.call_on_ui(go_fullscreen)

                self.call_on_ui(self.NOTIFICATION_MANAGER.update)

                self.iterate_event_callables("on_update", None, True)
                self._check_interaction_timeout()

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

    ##DIALOGS

    def dialog(self, dialog) -> None:
        if QThread.currentThread() is self.app.thread():
            self.DIALOG.open(dialog)
        else:
            self.call_on_ui(lambda: self.DIALOG.open(dialog))

    def close_dialog(self) -> None:
        if QThread.currentThread() is self.app.thread():
            self.DIALOG.close()
        else:
            self.call_on_ui(self.DIALOG.close)

    def alert(self, title: str, body: str = "", ok_text: str = "OK",
              on_close: Optional[Callable] = None,
              detail: str = None) -> None:
        def _build():
            from src.ui.dialogs import AlertDialog
            self.DIALOG.open(AlertDialog(self, title, body, ok_text=ok_text,
                                         on_close=on_close, detail=detail))
        self.call_on_ui(_build)

    def confirm(self, title: str, body: str = "",
                on_confirm: Optional[Callable] = None,
                on_cancel: Optional[Callable] = None,
                confirm_text: str = "Confirm", cancel_text: str = "Cancel",
                destructive: bool = False, detail: str = None) -> None:
        def _build():
            from src.ui.dialogs import ConfirmDialog
            self.DIALOG.open(ConfirmDialog(
                self, title, body, on_confirm=on_confirm, on_cancel=on_cancel,
                confirm_text=confirm_text, cancel_text=cancel_text,
                destructive=destructive, detail=detail,
            ))
        self.call_on_ui(_build)

    def prompt(self, title: str, body: str = "",
               on_submit: Optional[Callable] = None,
               on_cancel: Optional[Callable] = None,
               default: str = "", placeholder: str = "",
               submit_text: str = "OK", cancel_text: str = "Cancel",
               numeric: bool = False, password: bool = False,
               allow_empty: bool = False, detail: str = None) -> None:
        def _build():
            from src.ui.dialogs import InputDialog
            self.DIALOG.open(InputDialog(
                self, title, body, on_submit=on_submit, on_cancel=on_cancel,
                default=default, placeholder=placeholder,
                submit_text=submit_text, cancel_text=cancel_text,
                numeric=numeric, password=password,
                allow_empty=allow_empty, detail=detail,
            ))
        self.call_on_ui(_build)

    def choose(self, title: str, body: str = "", options: list = None,
               on_choose: Optional[Callable] = None,
               on_cancel: Optional[Callable] = None,
               default=None, choose_text: str = "Select",
               cancel_text: str = "Cancel", detail: str = None) -> None:
        def _build():
            from src.ui.dialogs import ChoiceDialog
            self.DIALOG.open(ChoiceDialog(
                self, title, body, options=options or [],
                on_choose=on_choose, on_cancel=on_cancel, default=default,
                choose_text=choose_text, cancel_text=cancel_text, detail=detail,
            ))
        self.call_on_ui(_build)

    def progress(self, title: str, body: str = ""):
        from src.ui.dialogs import ProgressDialog
        if QThread.currentThread() is self.app.thread():
            dialog = ProgressDialog(self, title, body)
            self.DIALOG.open(dialog)
            return dialog
        self.call_on_ui(lambda: self.dialog(ProgressDialog(self, title, body)))
        return None

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

    def setting(self, path: str, default=None):
        """
        Read a setting by dotted path, safely from any thread.

        Background threads should use this rather than touching SETTINGS
        directly: a save on the UI thread can otherwise be mid-flight when the
        read happens.
        """
        with self.SETTINGS_LOCK:
            node = self.SETTINGS
            try:
                for part in path.split("."):
                    node = node[part] if isinstance(node, dict) else getattr(node, part)
                return node
            except (AttributeError, KeyError, TypeError):
                return default

    def apply_settings(self, values: dict) -> None:
        """
        Push saved settings into the live object.

        Deliberately not SETTINGS.reload(): reload drops every key and reads
        the files again, and anything reading from another thread during that
        gap gets an AttributeError. update() only adds and overwrites, so
        there is never a moment where a section is missing.
        """
        with self.SETTINGS_LOCK:
            self.SETTINGS.update(values)

    def settings_dict(self) -> dict:
        with self.SETTINGS_LOCK:
            return {k.lower(): v for k, v in self.SETTINGS.as_dict().items()}

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
        template = Path("src") / "assets" / "data" / "new-template.json"

        if not self.DATAPATH.exists():
            self.log("info", f"Creating DATA Folder @ {self.DATAPATH}")
            self.DATAPATH.mkdir(parents=True, exist_ok=True)

        if not self.DATA.exists():
            self.log("info", f"Creating DATA file @ {self.DATA}")
            shutil.copy(template, self.DATA)
            return

        self.migrate_user_data(template)

    def migrate_user_data(self, template: Path) -> None:
        """
        Fold settings new to the template into the existing data file.

        This used to be skipped entirely once the file existed, which meant a
        setting added by an update never appeared: not in the file, not in
        Settings (the page generates its categories from this data), and not
        readable by the code that added it. The symptom was a feature that
        silently did nothing, since every read was guarded and fell back to a
        default.
        """
        from src.updater import merge_values, added_paths
        from src.settings import scrub_secrets

        try:
            shipped = json.loads(template.read_text(encoding="utf-8"))
            installed = json.loads(self.DATA.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            self.log("warning", f"[Settings] Could not migrate data file: {e}")
            return

        added = added_paths(shipped, installed)
        removed = added_paths(installed, shipped)
        if not added and not removed:
            return

        backup = self.DATA.with_suffix(".json.bak")
        try:
            shutil.copy(self.DATA, backup)
        except OSError as e:
            self.log("warning", f"[Settings] Could not back up data file: {e}")
            return

        merged = scrub_secrets(merge_values(shipped, installed))
        try:
            self.DATA.write_text(json.dumps(merged, indent=1) + "\n", encoding="utf-8")
        except OSError as e:
            self.log("warning", f"[Settings] Could not write migrated data file: {e}")
            return

        for path in added:
            self.log("info", f"[Settings] Added new setting '{path}'")
        for path in removed:
            self.log("info", f"[Settings] Removed obsolete setting '{path}'")
        self.log("info", f"[Settings] Migrated data file (backup at {backup.name})")

    ##LIFECYCLE

    def stop(self, event=None) -> None:
        self.log("info", "Closing Client ...")
        self.iterate_event_callables("on_close", event)
        self.PLUGIN.unload_plugins()
        self.dump(self.settings_dict(), self.DATA)
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

        # Exit codes are the launcher's protocol - see launcher.py.
        if self.UPDATE:
            code = EXIT_UPDATE
        elif self.RESTART:
            code = EXIT_RESTART
        else:
            code = exit_code if exit_code else EXIT_OK

        self.log("info", f"Exiting with code {code}")

        if self.LOG:
            self.LOG.close()

        sys.exit(code)