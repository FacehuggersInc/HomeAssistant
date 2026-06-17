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
from PyQt6.QtCore import (
    Qt, QTimer, pyqtSignal, QObject,
)
from PyQt6.QtGui import QFontDatabase, QColor

import psutil

from src.threading import ThreadManager
from src.enums import clear_events, get_global_events, TriggerAppEvent, Asset
from src.timing import TimeoutScheduler
from src.mixins import MixinManager, mixin_target
from src.plugin.loader import PluginManager
from src.backend import FlaskApp, FlaskService
from src.api.spotify import SpotifyAPI
from src.api.wordnik import WordnikAPI
from src.assistant.skill import Skill, SkillIntentEngine
from src.assistant.stt import STTProcessing
from src.assistant.tts import TTSProcessing
from src.ui.overlays import OverlayManager, NotificationManager, DialogManager
from src.styling import COLORS, make_background_qss, THEME_GRADIENT_QSS

EVENT_LEVELS = Literal["debug", "info", "warning", "error", "critical"]
EVENTS = Literal[
    "initialized", "on_focus", "on_un_focus", "on_visit", "on_leave",
    "on_update", "on_minimize", "on_maximize", "on_fullscreen",
    "on_state_change", "on_close", "on_settings_saved",
    "on_woke_assistant", "on_assistant_transcribed",
]
APP_NAME = "Desktop Home Assistant"


# ── Cross-platform data directory ─────────────────────────────────────────────

def _get_data_dir(app_name: str) -> Path:
    system = _platform.system()
    if system == "Windows":
        base = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        # Linux (Arch, Mint, Ubuntu, etc.) — XDG compliant
        base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / app_name.replace(" ", "")


# ── Public Registry (unchanged) ───────────────────────────────────────────────

class PublicRegistry:
    """Central registry for plugin-exposed classes, variables and etc."""

    def __init__(self):
        self.__exposed: dict[str, list[str]] = {}

    def has(self, name: str) -> bool:
        return hasattr(self, name)

    def expose(self, plugin: str, name: str, value, overwrite: bool = False):
        if hasattr(self, name) and not overwrite:
            print(f"PublicRegistry.expose cannot expose {name}, it's already exposed")
        self.__exposed.setdefault(plugin, [])
        if name not in self.__exposed[plugin]:
            self.__exposed[plugin].append(name)
        setattr(self, name, value)

    def unexpose(self, plugin: str, name: str):
        if plugin in self.__exposed and name in self.__exposed[plugin]:
            delattr(self, name)
            self.__exposed[plugin].remove(name)

    def clear(self, plugin: str):
        if plugin not in self.__exposed:
            return
        for key in self.__exposed[plugin]:
            if hasattr(self, key):
                delattr(self, key)
        del self.__exposed[plugin]

    def list(self, plugin: str = None) -> dict:
        if plugin:
            return {name: getattr(self, name) for name in self.__exposed.get(plugin, [])}
        return {p: [n for n in names] for p, names in self.__exposed.items()}


# ── UI signal bridge ──────────────────────────────────────────────────────────

class _UIBridge(QObject):
    """
    Marshals callables from background threads onto the Qt main thread.
    Uses QueuedConnection explicitly so cross-thread emission is safe.
    """
    _ui_call = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        # QueuedConnection is required for cross-thread signal delivery
        self._ui_call.connect(
            self._execute,
            Qt.ConnectionType.QueuedConnection,
        )

    def _execute(self, fn) -> None:
        try:
            fn()
        except Exception as e:
            import traceback
            print(f"[UIBridge] error executing {fn}: {e}")
            traceback.print_exc()

    def dispatch(self, fn: Callable) -> None:
        self._ui_call.emit(fn)


# ── Main Window ───────────────────────────────────────────────────────────────

class _AppWindow(QMainWindow):
    """
    The actual QMainWindow.  Owned by Client — not subclassed by plugins.
    Forwards window events back to Client.
    """

    def __init__(self, client: "Client"):
        super().__init__()
        self._client = client

    def closeEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()            # let Client.stop() handle teardown
        self._client.stop()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if self._client.BUILT:
            if self.isMinimized():
                self._client.iterate_event_callables("on_minimize", event, True)
            elif self.isMaximized():
                self._client.iterate_event_callables("on_maximize", event, True)
            elif self.isFullScreen():
                self._client.iterate_event_callables("on_fullscreen", event, True)

    def focusInEvent(self, event) -> None:   # type: ignore[override]
        super().focusInEvent(event)
        if self._client.BUILT:
            self._client.iterate_event_callables("on_focus", event, True)

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        super().focusOutEvent(event)
        if self._client.BUILT:
            self._client.iterate_event_callables("on_un_focus", event, True)

    def resizeEvent(self, event) -> None:    # type: ignore[override]
        super().resizeEvent(event)
        if self._client.BUILT:
            self._client._on_window_resized(event.size().width(), event.size().height())


# ── Client ────────────────────────────────────────────────────────────────────

class Client:
    """
    Central application object.  Owns the QApplication, QMainWindow,
    all managers, and the plugin system.

    Usage
    -----
    client = Client()
    client.run()        # blocks until the window is closed
    """

    @mixin_target("client.__init__")
    def __init__(self):
        self.__START_TIME = time.time()
        self.WINDOW_NAME  = APP_NAME

        # ── Qt application ────────────────────────────────────────────────────
        self._app     = QApplication.instance() or QApplication(sys.argv)
        self._window  = _AppWindow(self)
        self._bridge  = _UIBridge()

        self.BUILT    = False
        self.RESTART  = False

        self.__LAST_COLLECTION = time.time()

        # ── Events / States ───────────────────────────────────────────────────
        self.__EVENTS: dict = {
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
            },
        }

        self.__LOGGING               = True
        self.__LOGGING_FILE_CREATED  = False
        self.__LOG: Optional[TextIO] = None

        self.THREADS  = ThreadManager()
        self.TIMEOUTS = TimeoutScheduler(self)

        self.window_locked       = False
        self.window_should_lock  = True

        # ── Paths ─────────────────────────────────────────────────────────────
        self.ASSETS: dict = {}

        self.DATAPATH = Asset(_get_data_dir(APP_NAME))
        self.DATA     = Asset(self.DATAPATH / f"{APP_NAME.replace(' ', '')}.json")
        self.register_asset("data", self.DATA, "json")
        self.create_user_data_files()

        # ── External data ─────────────────────────────────────────────────────
        self.SETTINGS = Dynaconf(settings_files=[str(self.DATA)])
        self.register_asset(
            "background_images",
            Asset(self.SETTINGS.home.images.value),
            "FOLDER",
        )

        # ── Assistant ─────────────────────────────────────────────────────────
        self.ASSIST_VOICE_ACTIVITY_LEVEL = 0.0
        self.ASSIST_STATUS               = "DORMANT"
        self.SKILLS = SkillIntentEngine(self)
        self.STT    = None   # STTProcessing(self)  — disabled
        self.TTS    = None   # TTSProcessing(self)   — disabled

        # ── APIs ──────────────────────────────────────────────────────────────
        self.API: dict = {
            "wordnik": WordnikAPI(self),
        }

        # ── Overlay / dialog managers ─────────────────────────────────────────
        # _overlay_manager is the transparent QWidget that sits on top of everything.
        # It is created now but parented to the window during build().
        self._overlay_manager = OverlayManager(self)
        self.OVERLAYS         = self._overlay_manager
        self.DIALOG           = DialogManager(self)
        self.__NOTIFICATION_MANAGER = NotificationManager(
            self,
            self.SETTINGS.notifications.notification_duration.value,
            self.SETTINGS.notifications.notification_queue_delay.value,
        )

        # ── Plugins / Mixins ──────────────────────────────────────────────────
        self.mixin_manager  = MixinManager(self)
        self.public         = PublicRegistry()
        self.plugin_dirs    = [
            Asset(Path("src") / "assets" / "bundled"),
            Asset("plugins"),
        ]
        # ── Pages — must exist before plugins call add_page() in load() ──────
        self.SWITCHING_PAGE  = False
        self.PAGE            = None
        self.PAGES: dict     = {}
        self.DEFAULT_PAGE    = ""    # set by plugins; empty = show RootPage

        # Core framework pages
        from src.pages.settings import SettingsPage
        from src.pages.root import RootPage
        self.add_page("#settings", "Settings Page", SettingsPage)
        self.add_page("#root",     "Root Page",     RootPage)

        self.plugin_manager = PluginManager(self, self.plugin_dirs)
        self.plugin_manager.load_plugins()
        self.mixin_manager.apply_mixins_to(self)



        # PageHost — a QStackedWidget-style container; all pages live here
        self._page_host = QWidget(self._window)
        self._page_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.log("debug", "Application Pre-Initialized")

    # ── UI bridge ─────────────────────────────────────────────────────────────

    def call_on_ui(self, fn: Callable) -> None:
        """
        Schedule fn() to run on the Qt main thread.
        Safe to call from any background thread.
        """
        self._bridge.dispatch(fn)

    # ── Events ───────────────────────────────────────────────────────────────

    def set_state(self, state_name: str, state) -> None:
        self.__EVENTS["states"][state_name] = state

    def get_state(self, state_name: str):
        return self.__EVENTS["states"].get(state_name)

    def subscribe_to_event(self, on_call_type: EVENTS, callable_: Callable,
                           call_index: int = -1) -> None:
        if callable_ in self.__EVENTS["on_call"][on_call_type]:
            self.__EVENTS["on_call"][on_call_type].remove(callable_)
        self.__EVENTS["on_call"][on_call_type].insert(call_index, callable_)

    def unsubscribe_from_event(self, on_call_type: EVENTS, callable_: Callable) -> None:
        try:
            self.__EVENTS["on_call"][on_call_type].remove(callable_)
        except Exception:
            pass

    def create_on_call_event(self, call_type: str) -> None:
        if call_type in EVENTS:
            raise Exception(f"on_call type '{call_type}' is a App ONLY event")
        self.__EVENTS["on_call"][call_type] = []

    def trigger_on_call_event_iteration(self, on_call_type: str, event) -> None:
        if on_call_type in EVENTS:
            raise Exception(f"on_call type '{on_call_type}' is a Client ONLY event")
        self.iterate_event_callables(on_call_type, event)

    def iterate_event_callables(self, on_call_type: EVENTS, event,
                                hide_logging: bool = False) -> None:
        if not hide_logging:
            self.log("info", f"Event '{on_call_type}' was called")
        to_be_removed = []
        for callable_ in self.__EVENTS["on_call"].get(on_call_type, []):
            try:
                callable_(event)
            except Exception as e:
                self.log("error", f"'{str(callable_)}' had an error: {e}")
                to_be_removed.append((on_call_type, callable_))
        for type_, callable_ in to_be_removed:
            self.unsubscribe_from_event(type_, callable_)

    # ── Logging ───────────────────────────────────────────────────────────────

    def log(self, level: EVENT_LEVELS, message: str,
            pointer=None, include_traceback: bool = False) -> None:
        now    = datetime.now()
        timeof = f"{now.year}/{now.month}/{now.day} {now.hour:02}:{now.minute:02}:{now.second:02}"

        if self.__LOGGING and not self.__LOGGING_FILE_CREATED:
            logdir  = Path("logs")
            logpath = logdir / "latest.log"
            ts      = f"{now.year}-{now.month}-{now.day}-{now.hour:02}-{now.minute:02}"
            logdir.mkdir(exist_ok=True)

            self.__LOGGING_FILE_CREATED = True
            if logpath.exists():
                with open(logpath, "r") as lf:
                    lines = lf.readlines()
                lasttimeof = lines[0].strip() if lines else ts
                renamed = logdir / f"{lasttimeof}.log"
                if renamed.exists():
                    renamed.unlink()
                logpath.rename(renamed)

            self.__LOG = open(logpath, "a")
            self.__LOG.write(f"{ts}\n")

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

        if self.__LOGGING and self.__LOG:
            self.__LOG.write(f"{log_line}\n")
            if include_traceback:
                self.__LOG.write(f"{trace.strip()}\n")
            self.__LOG.flush()

    # ── Notifications / overlays ──────────────────────────────────────────────

    def overlay(self, args: dict) -> None:
        """Create a floating notification card from an args dict."""
        self.__NOTIFICATION_MANAGER.add_to_queue(args)

    def simple_notify(self, icon, title: str, body: str,
                      history: bool = True) -> None:
        """Show a simple icon + title + body notification."""
        if history and self.public.has("notification_history"):
            self.public.notification_history.add(icon, title, body, datetime.now())

        # Pass raw data — OverlayedWidget builds its own content widget
        # so there are no parenting issues with the content being pre-built
        self.__NOTIFICATION_MANAGER.add_to_queue({
            "icon":   icon,
            "title":  title,
            "body":   body,
            "bgcolor": COLORS.DARK.BG,
            "height":  90,
            "padding": 10,
            "anchor":  self.SETTINGS.notifications.notification_position.value,
        })

    # ── Paging ────────────────────────────────────────────────────────────────

    def action(self, feature_path: str, *args, **kwargs):
        if self.PAGE and self.BUILT:
            features = self.PAGE.features()
            if len(features) > 0:
                return features.get_path(feature_path)["call"](*args, **kwargs)

    def has_page(self, query: str) -> bool:
        return bool(self.PAGES.get(query))

    def get_page_data(self, name: str) -> dict:
        return self.PAGES.get(name)

    def get_page(self):
        return self.PAGE

    def get_pages(self):
        return self.PAGES.keys()

    def add_page(self, key: str, display: str, un_initialized_page) -> None:
        self.PAGES[key] = {
            "display": display,
            "object":  un_initialized_page,
        }

    def is_switching_page(self) -> bool:
        return self.SWITCHING_PAGE

    @mixin_target("client.goto")
    def goto(self, page: str, data: dict = None,
             override: bool = False, window_config: dict = {}) -> None:
        """Navigate to a registered page."""
        if self.PAGE and self.PAGE.name == page and not override:
            return

        self.SWITCHING_PAGE = True
        self.log("info", f"initializing / going to page '{page}'")

        # Stop current page
        if self.PAGE:
            if hasattr(self.PAGE, "stop"):
                self.PAGE.stop()
            self.iterate_event_callables(
                "on_leave",
                {"from": {"name": self.PAGE.name, "data": self.PAGE.data},
                 "to":   {"name": page, "data": data}},
            )
            self.PAGE.hide()

        # Initialize new page
        self.PAGES[page]["object"] = self.mixin_manager.apply_mixins_to(
            self.PAGES[page]["object"]
        )
        self.PAGE = self.PAGES[page]["object"](self, data)

        # Ensure page is sized and parented
        self.PAGE.setParent(self._page_host)
        w, h = int(self.SETTINGS.application.window.size.value[0]), \
               int(self.SETTINGS.application.window.size.value[1])
        self.PAGE.setGeometry(0, 0, w, h)
        self.PAGE.show()
        self.PAGE.raise_()
        self._overlay_manager.raise_()  # overlays always on top

        if window_config:
            self.configure(**window_config)

        if hasattr(self.PAGE, "start"):
            self.PAGE.start()

        self.SWITCHING_PAGE = False
        self.iterate_event_callables("on_visit", {"page": {"name": page, "data": data}})

    # ── Build ─────────────────────────────────────────────────────────────────

    @mixin_target("client.build.setup")
    def _internal_build_setup(self) -> None:
        """Called after the window is configured but before pages are added."""
        pass

    @mixin_target("client.build")
    def build(self) -> None:
        """Configure the QMainWindow and start all background services."""
        self.log("info", "Building Application...")
        self.BUILT = False

        w = int(self.SETTINGS.application.window.size.value[0])
        h = int(self.SETTINGS.application.window.size.value[1])

        # Window chrome
        self._window.setWindowTitle(self.WINDOW_NAME)
        self._window.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                                    Qt.WindowType.WindowStaysOnTopHint)
        self._window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._window.setStyleSheet(
            f"QMainWindow {{ background-color: {COLORS.DARK.BGDARK}; }}"
        )

        # Register fonts
        fonts_dir = Path("src") / "assets" / "fonts"
        for font_file in fonts_dir.glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(font_file))

        # Size
        self._window.resize(w, h)
        self._window.move(
            (self._app.primaryScreen().size().width()  - w) // 2,
            (self._app.primaryScreen().size().height() - h) // 2,
        )

        # page_host fills the window
        self._page_host.setGeometry(0, 0, w, h)
        self._page_host.setParent(self._window)
        self._page_host.show()

        # overlay manager is a child of _window, sitting above _page_host
        # WA_TransparentForMouseEvents is toggled on/off dynamically:
        # transparent when nothing is showing, opaque when a notification/dialog is active
        self._overlay_manager.setParent(self._window)
        self._overlay_manager.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._overlay_manager.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._overlay_manager.setGeometry(0, 0, w, h)
        self._overlay_manager.show()
        self._overlay_manager.raise_()

        self._internal_build_setup()

        self._window.show()
        self.BUILT = True

        # Defer overlay sync until after compositor places the window
        def _sync_overlay():
            self._overlay_manager.raise_()
            self.__NOTIFICATION_MANAGER.reset_initial_delay(1.0)

        QTimer.singleShot(300, _sync_overlay)

        self._start_all_backend_services()

        self.log("info", f"Startup Time: {round(time.time() - self.__START_TIME, 3)}s")

    # ── Window ────────────────────────────────────────────────────────────────

    @mixin_target("client.configure")
    def configure(self, x: int = None, y: int = None,
                  w: int = None, h: int = None,
                  maximizable: bool = True,
                  bgcolor: str = None,
                  re_center: bool = False) -> None:
        if x is not None: self._window.move(x, self._window.y())
        if y is not None: self._window.move(self._window.x(), y)
        if w is not None: self._window.resize(w, self._window.height())
        if h is not None: self._window.resize(self._window.width(), h)
        if bgcolor:
            self._window.setStyleSheet(
                f"QMainWindow {{ background-color: {bgcolor}; }}"
            )
        if re_center and not (x or y):
            screen = self._app.primaryScreen().size()
            self._window.move(
                (screen.width()  - self._window.width())  // 2,
                (screen.height() - self._window.height()) // 2,
            )
        self.log("info", f"Configuration changed to {(x, y, w, h, maximizable, bgcolor)}")

    def toggle_fullscreen(self, event=None) -> None:
        if self._window.isFullScreen():
            # Currently fullscreen — exit fullscreen
            self.window_should_lock = False
            self.window_locked      = False
            self._window.showNormal()
        else:
            # Currently windowed — go fullscreen
            self.window_should_lock = True
            self.window_locked      = False  # let auto-lock fire
            self._window.showFullScreen()

    def title(self, text: str = "") -> None:
        title = f"{self.WINDOW_NAME} | {text}" if text else self.WINDOW_NAME
        self._window.setWindowTitle(title)
        self.log("info", f"Title changed to '{title}'")

    def window(self):
        return self._window

    def core(self):
        return self._window

    # ── Window resize callback ────────────────────────────────────────────────

    def _on_window_resized(self, new_w: int, new_h: int) -> None:
        """Called by _AppWindow.resizeEvent."""
        self._page_host.setGeometry(0, 0, new_w, new_h)
        # Reposition the overlay window to match
        self._overlay_manager.setGeometry(0, 0, new_w, new_h)
        self._overlay_manager.update_geometry(new_w, new_h)
        if self.PAGE:
            self.PAGE.setGeometry(0, 0, new_w, new_h)

    # ── Backend ───────────────────────────────────────────────────────────────

    def _resync_time(self) -> None:
        if _platform.system() != "Windows":
            return  # Linux handles NTP via systemd-timesyncd automatically
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

    def _start_all_backend_services(self) -> None:
        self.start_api_service()
        self._start_update()
        if self.STT:
            self.STT.start()

    # ── Update loop ───────────────────────────────────────────────────────────

    @mixin_target("client.start_update")
    def _start_update(self) -> None:
        """Create the background update thread."""
        self.log("info", "Update Thread Starting")
        self.THREADS.create(
            name   = "__client_update_thread",
            target = self.__update_thread,
        )
        self.THREADS.start("__client_update_thread")

    def __update_thread(self, stop_event) -> None:
        while not stop_event.is_set():
            if self.BUILT:
                if self.RESTART:
                    break

                # Hourly GC + time resync
                if time.time() - self.__LAST_COLLECTION >= 3600:
                    self.__LAST_COLLECTION = time.time()
                    gc.collect()
                    self._resync_time()

                # Fire "initialized" callables once
                if not self.get_state("initialized"):
                    self.set_state("initialized", True)
                    self.call_on_ui(
                        lambda: (
                            self.iterate_event_callables("initialized", None, True),
                            self.plugin_manager.build_plugins(),
                        )
                    )

                # Navigate to home page once
                if not self.get_state("home_page_setup"):
                    self.set_state("home_page_setup", True)
                    def _goto_default():
                        target = self.DEFAULT_PAGE
                        if not target or not self.has_page(target):
                            if target:
                                self.log("warning", f"Default page '{target}' not registered — showing RootPage")
                            else:
                                self.log("info", "No default page set — showing RootPage")
                            target = "#root"
                        self.goto(target)
                    self.call_on_ui(_goto_default)

                # Track window size changes
                def _check_size():
                    if not self.BUILT:
                        return
                    w = self._window.width()
                    h = self._window.height()
                    stored = self.SETTINGS.application.window.size.value
                    if w > stored[0]:
                        self.SETTINGS.application.window.size.value = [w, h]

                self.call_on_ui(_check_size)

                # Notification manager tick
                self.call_on_ui(self.__NOTIFICATION_MANAGER.update)

                # User subscribed on_update callables
                self.iterate_event_callables("on_update", None, True)

                # Auto fullscreen / lock
                if self.SETTINGS.application.window.auto_lock and \
                        not self.window_locked and self.window_should_lock:
                    self.window_locked = True

                    def _go_fullscreen():
                        self._window.showFullScreen()
                        w = self._window.width()
                        h = self._window.height()
                        self.SETTINGS.application.window.size.value = [w, h]
                        self.dump(self.SETTINGS.as_dict(), self.DATA)

                    self.call_on_ui(_go_fullscreen)

                time.sleep(0.05)

        if self.RESTART:
            self.call_on_ui(self.stop)

    # ── Core helpers ──────────────────────────────────────────────────────────

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
        """
        Schedule a UI update.  Pass a callable that performs the Qt update,
        or None to trigger a generic repaint of the current page.
        """
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

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def stop(self, event=None) -> None:
        """Gracefully stop the application."""
        self.log("info", "Closing Client ...")
        self.iterate_event_callables("on_close", event)
        self.plugin_manager.unload_plugins()
        self.dump(self.SETTINGS.as_dict(), self.DATA)
        self.cleanup()
        self._window.hide()
        self._app.quit()

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
        """Start the application. Blocks until the window is closed."""
        self.log("info", f"Running Application -> {APP_NAME}")
        self._resync_time()
        self.build()
        exit_code = self._app.exec()

        if self.__LOG:
            self.__LOG.close()

        if self.RESTART:
            self.log("warning", "Restarting ...")
            subprocess.Popen([sys.executable] + sys.argv)

        sys.exit(exit_code)