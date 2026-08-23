from __future__ import annotations

import gc
import inspect
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
from contextlib import contextmanager
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
from src.registries.context_registry import ContextRegistry
from src.registries.public_registry import PublicRegistry
from src.registries.page_registry import PageRegistry
from src.registries.secret_registry import SecretRegistry
from src.registries.quick_access_registry import QuickAccessRegistry
from src.bookmarks import BookmarkStore
from src.registries.audio_registry import AudioRegistry
from src.registries.player_registry import PlayerRegistry
from src.registries.status_registry import StatusRegistry
from src.registries.cancel_registry import CancelRegistry
from src.registries.user_registry import UserRegistry
from src.registries.service_registry import ServiceRegistry, Restart
from src.registries.package_registry import PackageRegistry
from src.backend import FlaskApp, FlaskService
from src.assistant.skill import Skill, SkillIntentEngine
from src.assistant.stt import STTProcessing
from src.ui.overlays import OverlayManager, NotificationManager, DialogManager, Panel
from src.styling import COLORS, load_styles, set_style
from src.constants import (
    APP_NAME, EVENTS, EVENT_LEVELS, CLIENT_EVENT_NAMES,
    EXIT_OK, EXIT_UPDATE, EXIT_RESTART,
    get_data_dir, record_exit_intent,
)

if _platform.system() != "Windows":
    os.environ["QT_STYLE_OVERRIDE"] = ""


##UI BRIDGE

class UIBridge(QObject):

    ui_call = pyqtSignal(object)

    def __init__(self, log=None):
        super().__init__()
        # A logger handed in, not reached for.
        #
        # This is a bare QObject: it has no client and no log() of its own, so
        # calling self.log() here raised AttributeError **inside the exception
        # handler** - out of a Qt slot, taking the application with it. A
        # queued callable that merely failed became a crash, and the failure
        # that started it never got written down.
        self._log = log
        #QueuedConnection required for safe cross-thread signal delivery
        self.ui_call.connect(self.execute, Qt.ConnectionType.QueuedConnection)

    def _report(self, message: str) -> None:
        """Never raises. This runs while something else is already wrong."""
        try:
            if self._log is not None:
                self._log("warning", message)
                return
        except Exception:
            pass
        print(message)

    def execute(self, fn) -> None:
        try:
            fn()
        except Exception as e:
            self._report(f"[UIBridge] Error executing {fn}: {e}")
            try:
                traceback.print_exc()
            except Exception:
                pass

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

#Whether an event handler will take a second argument.
#
#Some events carry more than the thing that happened - `on_assistant_fallback`
#carries the phrase AND what was last asked. Every handler written before that
#takes one argument, and calling those with two raises a TypeError that
#`iterate_event_callables` reads as a broken handler and UNSUBSCRIBES. A
#plugin would be quietly disconnected by an event gaining an argument, which
#is the most expensive way to add one.
#
#Cached on the underlying function: bound methods are rebuilt on every
#attribute access, so caching the bound object would never hit.
_ARITY_CACHE: dict = {}


def _accepts_two(callable_) -> bool:
    key = getattr(callable_, "__func__", callable_)
    try:
        cached = _ARITY_CACHE.get(key)
    except TypeError:
        cached = None                       # unhashable: ask every time
        key = None
    if cached is not None:
        return cached

    try:
        parameters = list(inspect.signature(callable_).parameters.values())
    except (TypeError, ValueError):
        # Builtins and C callables have no readable signature. One argument
        # is what every existing handler takes, so that is the safe guess.
        return False

    accepts = False
    positional = 0
    for parameter in parameters:
        if parameter.kind is parameter.VAR_POSITIONAL:
            accepts = True
            break
        if parameter.kind in (parameter.POSITIONAL_ONLY,
                              parameter.POSITIONAL_OR_KEYWORD):
            positional += 1
    accepts = accepts or positional >= 2

    if key is not None:
        _ARITY_CACHE[key] = accepts
    return accepts


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
        # The client's own log, so a failed UI callable is written down
        # rather than printed and lost.
        self.bridge = UIBridge(log=self.log)

        self._last_interaction_time = time.time()
        #when the tap sound last played, so a two-finger touch is
        #one sound rather than two a millisecond apart
        self._last_tap_sound = 0.0
        self._interaction_idle      = False
        # When this process came up, for /ping. There was no way to ask how
        # long the panel had been running without reading the log.
        self.STARTED_AT             = time.time()
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
                #One event for everything the web page does. A separate event
                #per kind would mean a subscriber wanting two of them
                #registering twice and a new kind being a new event name that
                #nothing is listening for.
                "on_web_event":             [],
                "on_transcribing_assistant": [],
                "on_transcribed_assistant":  [],
                "on_heard_assistant":       [],
                "on_woke_assistant":        [],
                "on_assistant_transcribed": [],
                "on_assistant_cancelled":   [],
                "on_assistant_fallback":    [],
                #Heard, understood to be English, and judged not to have
                #been said to the panel at all - see addressed.py. Its own
                #event because "nothing wanted it" and "nobody was talking
                #to us" are different things, and a display that shows the
                #first for the second is reporting a failure that did not
                #happen.
                "on_assistant_unaddressed": [],
                #The counterpart of the fallback: a skill took the phrase.
                #Both, so a display can tell "understood" from "nothing
                #wanted it" rather than only being told about the failure.
                "on_skill_called":          [],
                #The microphone was muted or unmuted at the mixer.
                "on_mic_mute_changed":      [],
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
        # Long-running work that is not just a thread: the speech process and
        # the receiver thread that only means anything while it is alive. One
        # lifecycle for both, so a shutdown does not have to know which is
        # which. See docs/services.md.
        self.SERVICES = ServiceRegistry(self)
        # Buildable setup bundles for other machines. Owner-keyed, so a
        # plugin's packages go when the plugin does. See docs/packages.md.
        self.PACKAGES = PackageRegistry(self)
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

        # Sounds, uploadable.
        #
        # The registry looks here for anything registered by key, so putting a
        # file in is all it takes - and the upload page already knows how to
        # take a folder asset, so this needs no endpoint of its own.
        from src.registries.audio_registry import AUDIO_DIR
        audio_asset = Asset(AUDIO_DIR)
        audio_asset.mark_uploadable()
        self.register_asset("sounds", audio_asset, "FOLDER")
        # Plugins, uploadable but GUARDED.
        #
        # This is the one asset whose contents are executed. Everything else
        # that can be uploaded is data - a sound, a wallpaper, a sticker - and
        # the worst a bad one does is look wrong. A plugin runs with the same
        # reach as the app, so being permitted to upload is deliberately not
        # enough on its own: somebody has to be at the panel.
        #
        # Deletable is NOT set. Adding a plugin is recoverable by removing
        # what was added; emptying the folder from a phone takes every plugin
        # on the panel with it, and that is not a mistake anybody makes on
        # purpose.
        # Uploads waiting to be shown, and installs waiting to be agreed to.
        # Both hold state between two requests, so they live on the client
        # rather than inside the Flask app - a blueprint rebuilt on a settings
        # change would take the pending questions with it.
        from src.plugin.staging import Staging
        from src.plugin.gate import InstallGate
        self.PLUGIN_STAGING = Staging()
        self.PLUGIN_GATE = InstallGate(self)

        plugins_asset = Asset(cwd / "plugins")
        plugins_asset.mark_uploadable()
        plugins_asset.mark_guarded()
        self.register_asset("plugins", plugins_asset, "FOLDER")
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
        bg_asset = Asset(self.SETTINGS.home.background.images.value)
        bg_asset.mark_uploadable()
        # Wallpapers get swapped out, so they can go as well as arrive.
        bg_asset.mark_deletable()
        self.register_asset("background_images", bg_asset, "FOLDER")

        ## -- ASSISTANT

        # What the assistant is doing, what it last said, and whatever is
        # currently listening and speaking, all live on SERVICES.STT and
        # SERVICES.TTS.
        self.SKILLS = SkillIntentEngine(self)
        self.register_speech_providers()
        from src.assistant import tts_package
        tts_package.register(self)
        from src.assistant import judge_package
        judge_package.register(self)

        ## -- APIS
        # One registry for both: the HTTP endpoints a plugin serves, and the
        # API classes it provides. These were `API` and a plain `API`
        # dict beside it - two things called "the API" with different rules,
        # and nothing owned the dict, so an unloaded plugin left its objects
        # behind for anything still holding a reference to call into.
        self.API = APIRegistry(self)
        # What was last asked and answered. Written here and nowhere else -
        # see registries/context_registry.py for why it is not per-plugin.
        self.CONTEXT      = ContextRegistry(self)
        self.SECRETS      = SecretRegistry(self)
        for _key in self.CORE_SECRETS:
            self.SECRETS.register("client", _key)
        # Here, where SECRETS exists, and not beside the speech providers
        # where it reads better: this runs during construction, and an
        # attribute that is assigned further down __init__ does not exist yet
        # however sensible the line looks.
        #
        # A voice can depend on a credential, and a credential never appears
        # in the settings file - so nothing that watches settings sees one
        # arrive. Without this, pasting a key correctly leaves the backend
        # exactly as broken as it was.
        self.SECRETS.subscribe(self._secret_changed)

        ## -- OVERLAYS
        self.OVERLAYS             = OverlayManager(self)
        self.DIALOG               = DialogManager(self)
        self.NOTIFICATION_MANAGER = NotificationManager(
            self,
            self.SETTINGS.notifications.toasts.notification_duration.value,
            self.SETTINGS.notifications.toasts.notification_queue_delay.value,
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
        # What is playing, whatever is playing it. Backends register;
        # widgets and skills talk to this rather than to a backend.
        self.AUDIO          = AudioRegistry(self)
        self.fill_device_options()
        self.BOOKMARKS      = BookmarkStore(self)
        self.PLAYER         = PlayerRegistry(self)
        # What the panel is busy with, as a row of icons. See
        # src/registries/status_registry.py.
        self.STATUS         = StatusRegistry(self)
        # Puts the panel's own speech on it. Started with the assistant,
        # since there is nothing to watch before that.
        self.SPEECH_STATUS  = None
        # What "stop" means right now. Whatever can be cancelled
        # registers its own words and its own condition.
        self.CANCEL         = CancelRegistry(self)
        # An answer panel is one of them, and it is registered HERE rather
        # than by whichever plugin put it up. Any plugin can raise an answer,
        # a long one is read aloud for as long as it takes, and "stop" has
        # to mean the same thing whoever asked - a per-plugin registration
        # would work for that plugin's answers and silently not for anyone
        # else's.
        self.CANCEL.register(
            "client", "answer_panel",
            keywords=["stop", "nevermind", "never mind", "quiet", "be quiet",
                      "shut up", "enough", "thats enough", "cancel that",
                      "stop talking", "stop reading"],
            handler=self._cancel_answer,
            is_active=self._answer_is_open,
            # Under the AI fallback's own panel, which is a conversation and
            # a bigger thing to be closing.
            priority=40,
            description="stop reading the answer and close it",
        )
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

        # The language model, before the plugins rather than during them.
        #
        # Every Skill a plugin declares is built against it, so it loads on
        # the first one either way. Doing it here means the several seconds
        # of a first-run download - see nlp.download() - happen at a moment
        # with a log line either side, instead of halfway through loading
        # whichever plugin happened to declare the first skill, where the
        # failure reads as that plugin's fault.
        from src.assistant import nlp
        nlp.set_log(self.log)
        nlp.preload()

        self.PLUGIN = PluginManager(self, self.plugin_dirs)
        self.PLUGIN.load_plugins()
        self.MIXINS.apply_mixins_to(self)

        self.page_host = QWidget(self.window)
        self.page_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


    ##UI BRIDGE

    #What the microphone was last known to be doing, and when that was asked.
    #
    #Cached because every read is a subprocess and the quick panel asks each
    #button for its state on every press and every open - and because the
    #answer has to be available the INSTANT the button is pressed, which is
    #before the mixer has been told anything.
    MIC_STATE_TTL = 2.0

    def mic_muted(self) -> bool:
        """
        Whether the microphone itself is muted.

        The device, not the panel's use of it. Blocking the transcript would
        leave a live microphone in the room with a button claiming otherwise,
        which is the one thing a mute control must never do.
        """
        cached, asked = getattr(self, "_mic_state", (None, 0.0))
        if cached is not None and (time.time() - asked) < self.MIC_STATE_TTL:
            return bool(cached)
        answer = self._read_mic_muted()
        self._mic_state = (answer, time.time())
        return bool(answer)

    def _read_mic_muted(self):
        from src.system import volume as system_volume
        try:
            return system_volume.mic_muted(
                str(self.setting("audio.devices.input_device.value", "")))
        except Exception:
            return None

    def mic_mute_available(self) -> bool:
        """Whether there is a mixer that can answer for the microphone."""
        return self._read_mic_muted() is not None

    def set_mic_muted(self, muted: bool) -> None:
        """
        Mute or unmute the input. On a thread - every backend is a subprocess.

        The cache is moved FIRST, before the mixer is touched. The quick
        panel asks every button for its state the moment a press returns, and
        a press that only starts the work returns before anything has
        changed - so the button read the old value and sat there saying the
        microphone was still on.

        Corrected afterwards from what the mixer actually did, and the panel
        told again if the two disagree, so a refusal shows rather than
        leaving a button lying about a live microphone.
        """
        muted = bool(muted)
        device = str(self.setting("audio.devices.input_device.value", ""))
        self._mic_state = (muted, time.time())

        def apply():
            from src.system import volume as system_volume
            worked = False
            try:
                worked = system_volume.set_mic_muted(muted, device)
            except Exception as e:
                self.log("warning", f"[Audio] Microphone mute failed: {e}")

            truth = self._read_mic_muted()
            self._mic_state = (truth, time.time())
            if worked and truth is muted:
                self.log("info", f"[Audio] Microphone "
                                 f"{'muted' if muted else 'unmuted'}.")
            else:
                self.log("warning", f"[Audio] The microphone did not "
                                    f"{'mute' if muted else 'unmute'}.")
            self.iterate_event_callables("on_mic_mute_changed", bool(truth))
            self.refresh_quick_states()

        Thread(target=apply, name="__mic_mute", daemon=True).start()

    def toggle_mic_muted(self) -> None:
        self.set_mic_muted(not self.mic_muted())

    def refresh_quick_states(self) -> None:
        """
        Ask the quick panel to re-read every button.

        For anything that finishes after the press does. The panel refreshes
        itself when a press returns, which is the wrong moment for work that
        was handed to a thread.
        """
        def apply():
            try:
                panel = getattr(self, "QUICK_SETTINGS", None)
                if panel is not None:
                    panel.refresh_states()
            except Exception:
                pass
        self.call_on_ui(apply)

    def apply_minimum_volume(self) -> None:
        """
        Raise the system volume to the floor in settings, if it is under it.

        Only ever up. It is a minimum rather than a level, so somebody turning
        the panel down by hand still works - down to the point where a reply
        would go unheard, which is the case this exists for.

        On a thread, because every backend here is a subprocess: `wpctl`,
        `pactl` or `amixer`. Two shells out on the UI thread is a visible
        stall on a panel this size, and this runs on every wake.
        """
        try:
            floor = int(self.setting("audio.devices.minimum_volume.value", 0) or 0)
        except (TypeError, ValueError):
            return
        if floor <= 0:
            return

        def apply():
            from src.system import volume as system_volume
            try:
                if not system_volume.available():
                    return
                current = system_volume.get_volume()
                # -1 or anything unreadable: left alone. A backend that cannot
                # say where the volume is cannot be trusted to be told, and
                # guessing here would set it on every wake.
                if current is None or current < 0 or current >= floor:
                    return
                if system_volume.set_volume(floor):
                    self.log("info", f"[Audio] Volume was {current}%, raised to "
                                     f"the {floor}% minimum.")
            except Exception as e:
                self.log("debug", f"[Audio] Could not apply the volume "
                                  f"minimum: {e}")

        Thread(target=apply, name="__minimum_volume", daemon=True).start()

    #How often to check that a connected Bluetooth speaker is the one being
    #played through.
    #
    #Slow on purpose. There is no signal for this - BlueZ publishes the
    #connection and the sound server publishes the sink, and neither tells
    #this application - so it is a poll, and a poll on a wall panel runs for
    #months. Two `pactl` reads every fifteen seconds is nothing; the same
    #two every second is a subprocess pair forever.
    #
    #Fifteen seconds is also about how long somebody takes to wonder why the
    #speaker is not playing, which is the delay that actually matters.
    BLUETOOTH_FOLLOW_SECONDS = 15

    def follow_bluetooth_audio(self) -> None:
        """
        Put the sound where the speaker is, and keep it there.

        Both jobs at once, because they are the same check: a speaker that
        has just connected and a speaker that has been connected all along
        while something else took the default look identical from here, and
        both want the same correction.

        On a thread, for the reason the volume minimum is: every backend is a
        subprocess, and this runs on a timer.
        """
        if not self.setting("audio.devices.follow_bluetooth.value", True):
            return
        if getattr(self, "_bt_follow_busy", False):
            return
        self._bt_follow_busy = True

        def work():
            try:
                from src.system import audio_follow

                def announce(title: str, body: str) -> None:
                    # Back to the UI thread. A toast builds widgets, and this
                    # runs on a worker - the same reason the check itself is
                    # off the UI thread in the first place.
                    self.call_on_ui(
                        lambda: self.simple_notify("bluetooth", title, body))

                audio_follow.apply(log=self.log, announce=announce)
            except Exception as e:
                self.log("debug", f"[Audio] Bluetooth follow failed: {e}")
            finally:
                self._bt_follow_busy = False

        Thread(target=work, name="__bt_follow", daemon=True).start()

    def build_bluetooth_follow(self) -> None:
        """
        Start the check that keeps a Bluetooth speaker in charge of the sound.

        The timer runs whatever the setting says, and the setting is read at
        each tick rather than here - so turning it off takes effect without a
        restart, and so does turning it back on. A timer that only exists
        when a setting was true at boot is a setting that half works.
        """
        self._bt_follow_busy = False
        self._bt_follow_timer = QTimer()
        self._bt_follow_timer.timeout.connect(self.follow_bluetooth_audio)
        self._bt_follow_timer.start(self.BLUETOOTH_FOLLOW_SECONDS * 1000)
        # Once at startup too, without waiting out the first interval: a panel
        # rebooting with the speaker already on should come up playing
        # through it.
        QTimer.singleShot(2000, self.follow_bluetooth_audio)

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
                                hide_logging: bool = False, extra=None) -> None:
        if not hide_logging:
            self.log("info", f"Event '{on_call_type}' was called")
        to_be_removed = []
        for callable_ in self.EVENTS["on_call"].get(on_call_type, []):
            try:
                if extra is None or not _accepts_two(callable_):
                    callable_(event)
                else:
                    callable_(event, extra)
            except Exception as e:
                self.log("error", f"'{str(callable_)}' had an error: {e}")
                to_be_removed.append((on_call_type, callable_))
        for type_, callable_ in to_be_removed:
            self.unsubscribe_from_event(type_, callable_)

    ##INTERACTION

    #Qt events that count as a tap for the purpose of making a noise.
    #
    #Not every interaction: on_interaction fires on MouseMove and TouchUpdate
    #too, so a drag across the screen would be a machine gun. A press is the
    #moment somebody meant something.
    TAP_EVENT_TYPES = (QEvent.Type.MouseButtonPress, QEvent.Type.TouchBegin)
    #And no faster than this, in seconds. A two-finger touch is two events a
    #millisecond apart and should be one sound.
    TAP_MIN_GAP = 0.08

    def _tap_sound(self, event) -> None:
        """A quiet click, on a press."""
        try:
            if event is None or event.type() not in self.TAP_EVENT_TYPES:
                return
            now = time.time()
            if now - getattr(self, "_last_tap_sound", 0.0) < self.TAP_MIN_GAP:
                return
            self._last_tap_sound = now
            self.AUDIO.play("tap")
        except Exception:
            # Never worth interrupting an interaction over.
            pass

    def _on_global_interaction(self, event) -> None:
        was_idle = self._interaction_idle
        self._interaction_idle      = False
        self._last_interaction_time = time.time()
        self._tap_sound(event)

        self.iterate_event_callables("on_interaction", event, True)
        if was_idle:
            self.iterate_event_callables("on_fresh_interaction", event, True)

    def debug_mode(self) -> bool:
        """
        Whether the app is in debug mode.

        One flag the whole app reads, rather than each plugin inventing its
        own: anything that wants a developer-only control, an extra log line
        or a way to force a state it would otherwise have to wait for should
        gate it on this.

        A method rather than an attribute, so it follows the setting without
        anything having to be told the setting changed.
        """
        try:
            return bool(self.setting("debug.enabled.value", False))
        except Exception:
            return False

    def reset_interaction_timeout(self, wake: bool = True) -> None:
        """
        Treat now as the last interaction, without there having been one.

        For something the panel did that a person is expected to look at - a
        timer finishing, an alarm - where the idle clock would otherwise be
        measuring the wrong thing. Nobody has touched the screen, but the panel
        going to sleep over the answer it just produced is not what anyone
        wants.

        `wake` also brings it out of idle if it is already there, so an idle
        plugin's screensaver is dismissed rather than left covering the thing
        that just happened.

        Safe from any thread: the event is marshalled, because
        `on_fresh_interaction` subscribers close panels and touch widgets and
        this is reachable from the update thread.
        """
        was_idle = self._interaction_idle
        self._last_interaction_time = time.time()
        if not wake:
            return
        self._interaction_idle = False
        if was_idle:
            self.call_on_ui(
                lambda: self.iterate_event_callables(
                    "on_fresh_interaction", None, True))

    def _check_interaction_timeout(self) -> None:
        if self._interaction_idle:
            return

        # A dialog holds the clock open, whatever is behind it.
        #
        # Every dialog: somebody reading a map, choosing a bookmark or picking
        # a colour is looking at the screen and producing no interaction while
        # they decide. Timing out under them and switching pages throws away
        # what they were part way through - and the minimap did exactly that.
        #
        # Here rather than in each dialog, because a dialog that forgot would
        # be the one it happened under.
        try:
            if self.DIALOG.dialog_stack:
                self._last_interaction_time = time.time()
                return
        except Exception:
            pass

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

        # A panel can refuse it too, the same way a page can.
        #
        # Panels were not considered at all: a conversation covering the
        # screen is read for as long as it takes to read, produces no
        # interaction while it is, and was timed out from under the person
        # reading it.
        try:
            host = self.OVERLAYS
            if host is not None:
                from src.ui.overlays import Panel
                for panel in host.findChildren(Panel):
                    if panel.isVisible() and getattr(panel, "blocks_idle", False):
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

    def _debug_logging(self) -> bool:
        """
        Whether debug lines are wanted.

        Cached after the settings exist, and true before they do - a failure
        during startup is the one time the detail is most wanted, and the
        setting cannot be read yet to say otherwise.
        """
        cached = getattr(self, "_debug_on", None)
        if cached is not None:
            return cached
        try:
            value = bool(self.SETTINGS.debug.enabled.value)
        except Exception:
            return True
        self._debug_on = value
        return value

    def _open_log_file(self) -> None:
        if self.LOG:
            self.LOG.close()

        from src.constants import KEEP_LOGS, LOG_DIR

        now     = datetime.now()
        # LOG_DIR, not Path("logs"). A relative path writes beside whatever
        # the process was started from, while crash.py writes beside the
        # install - two folders, and a prune that empties the wrong one.
        logdir  = LOG_DIR
        logpath = logdir / "latest.log"
        ts      = f"{now.year}-{now.month}-{now.day}-{now.hour:02}-{now.minute:02}"
        logdir.mkdir(parents=True, exist_ok=True)

        if logpath.exists():
            with open(logpath, "r") as lf:
                lines = lf.readlines()
            lasttimeof = lines[0].strip() if lines else ts
            renamed = logdir / f"{lasttimeof}.log"
            if renamed.exists():
                renamed.unlink()
            logpath.rename(renamed)

        self._prune_logs(logdir, KEEP_LOGS)

        self.LOG = open(logpath, "a")
        self.LOG.write(f"{ts}\n")
        self.LOGGING_FILE_CREATED = True

    @staticmethod
    def _prune_logs(folder, keep: int) -> None:
        """
        Keep the newest `keep` rotated logs and delete the rest.

        Only the rotated ones. `latest.log` is being written to, `crash.log`
        is the record of something that went wrong and is worth more than any
        of these, and `startup.log` belongs to the launcher - deleting any of
        the three by counting files in a folder would be deleting the ones
        somebody actually wants.

        By modified time rather than by the timestamp in the name: the name
        comes from the first line of the previous log and a truncated or
        hand-edited file can produce any name at all.
        """
        try:
            rotated = [entry for entry in folder.glob("*.log")
                       if entry.is_file()
                       and entry.name not in ("latest.log", "crash.log",
                                              "startup.log")]
        except OSError:
            return

        rotated.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
        for entry in rotated[max(0, int(keep)):]:
            try:
                entry.unlink()
            except OSError:
                # A log somebody has open, or a folder that went read-only.
                # Losing an old log is not worth failing a launch over.
                pass

    def log(self, level: EVENT_LEVELS, message: str,
            pointer=None, include_traceback: bool = False) -> None:
        # Debug lines are dropped unless debug is on.
        #
        # There was no level filtering at all, so every diagnostic in the tree
        # printed and was written on every launch - which is why the log reads
        # as noise and why the useful lines in it are hard to find. The calls
        # are worth keeping: several of them are what located a bug in the
        # first place. Being able to turn them off is what was missing.
        if level == "debug" and not self._debug_logging():
            return

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
                      history: bool = True, sound: str = None,
                      urgent: bool = False) -> None:
        # Held back while do not disturb is on, unless it is urgent.
        #
        # Still written to the history: the point is not to be interrupted, not
        # to lose what happened. Something marked urgent is shown anyway -
        # anything that cannot wait should not be silenced by a mode meant for
        # the evening.
        if self.do_not_disturb() and not urgent:
            if history and self.public.has("notification_history"):
                try:
                    self.public.notification_history.add(
                        icon, title, body, datetime.now())
                except Exception:
                    pass
            return

        # A sound is opt-in and by KEY.
        #
        # Not a path and not a default: a panel that chimes at every
        # notification is a panel somebody turns the speakers off on, and then
        # the one notification worth hearing is silent too.
        if sound:
            try:
                self.AUDIO.play(sound)
            except Exception as e:
                self.log("debug", f"[Audio] Could not play '{sound}': {e}")

        if history and self.public.has("notification_history"):
            self.public.notification_history.add(icon, title, body, datetime.now())
        self.NOTIFICATION_MANAGER.add_to_queue({
            "icon":    icon,
            "title":   title,
            "body":    body,
            "bgcolor": COLORS.DARK.BG,
            "height":  90,
            "padding": 10,
            "anchor":  self.SETTINGS.notifications.toasts.notification_position.value,
        })

    ##PANELS

    def create_panel(self, content: QWidget = None, width: int = None,
                      edge: str = "right", bgcolor: str = "#1e1e1e",
                      key: str = None, destroy_on_close: bool = True,
                      on_created: Optional[Callable[[Panel], None]] = None,
                      dismiss_on_outside_click: bool = False,
                      height: int = None, margin: int = 0,
                      blocks_idle: bool = False,
                      on_closed: Optional[Callable[[], None]] = None
                      ) -> Optional[Panel]:
        """
        Build a panel and slide it in.

        `height` and `margin` are forwarded rather than left to the caller to
        set afterwards: open_panel() computes where the panel slides TO before
        on_created runs, so a size applied after the fact animates to the old
        position and lands cut off.
        """
        def _build() -> Panel:
            panel = Panel(self, width=width, edge=edge, bgcolor=bgcolor, key=key,
                           destroy_on_close=destroy_on_close,
                           dismiss_on_outside_click=dismiss_on_outside_click,
                           height=height, margin=margin,
                           blocks_idle=blocks_idle)
            panel.on_closed_hook = on_closed
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
        """
        Call a feature on whatever page is on screen.

        `get_path` answers with the registered value, which for a method is
        the bound method - so it is called, not subscripted.
        """
        if self.PAGE and self.BUILT:
            features = self.PAGE.features()
            if len(features) > 0:
                return features.get_path(feature_path)(*args, **kwargs)

    def has_page(self, query: str) -> bool:
        return self.PAGES.has_page(query)

    def panel_name(self) -> str:
        """
        What this panel is called.

        One accessor rather than a value read in five places: the name appears
        in Info, in the window title and on every page the panel serves, and
        those drifting apart is the whole reason it is worth naming.

        Empty falls back to the application name, so a panel nobody has named
        reads as something rather than as a blank heading.
        """
        try:
            name = str(self.SETTINGS.application.panel_name.value or "").strip()
        except Exception:
            name = ""
        return name or self.WINDOW_NAME

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
        from src.system import safemode
        note = safemode.describe()
        if note:
            # Said loudly. A panel started with something switched off and then
            # forgotten about is a panel with a mysteriously missing feature.
            self.log("warning", f"[SafeMode] {note}")
        self.log("info", "Building Application...")
        self.BUILT = False

        w = int(self.SETTINGS.application.window.size.value[0])
        h = int(self.SETTINGS.application.window.size.value[1])

        self.window.setWindowTitle(self.panel_name())
        self.window.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        set_style(self.window, "main", "main-window", object_tag="QMainWindow")

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
        self.drop_plugin_sections()
        self.register_core_sounds()
        self.report_missing_tooling()
        self.build_update_checker()
        self.build_bluetooth_follow()
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

    def _answer_panels(self) -> list:
        """Every answer currently on screen."""
        try:
            from src.ui.panels.answer import AnswerPanel
            host = self.OVERLAYS
            if host is None:
                return []
            return [p for p in host.findChildren(AnswerPanel) if p.open]
        except Exception:
            return []

    def _answer_is_open(self) -> bool:
        return bool(self._answer_panels())

    def _cancel_answer(self) -> None:
        """
        Stop reading an answer, and take it down.

        Both halves. Closing the card while the reply carries on reading is
        a voice with nothing on screen behind it, and stopping the voice
        while the card sits there is somebody having to tap it as well.
        """
        stopped = False
        try:
            tts = getattr(self, "TTS", None)
            if tts is not None:
                stopped = bool(tts.stop())
        except Exception as e:
            self.log("debug", f"[Client] Could not stop speech: {e}")

        # Marked as an interruption, the same as a wake word spoken over a
        # reply. A reply cut off mid-word still has audio in the room and in
        # the output buffer after `stop()` returns, and without this the
        # first thing captured afterwards is that tail - transcribed, matched
        # against skills, and acted on. The word that did the cancelling was
        # heard; what follows it is the panel, not the person.
        stt = getattr(self, "STT", None)
        if stt is not None:
            try:
                stt.interrupted_at = time.time()
                # Whatever else was going to be said is not coming, so the
                # self-hearing grace would only suppress the next real thing.
                stt.spoke_until = 0.0
                if stopped and hasattr(stt, "note_interrupted"):
                    stt.note_interrupted()
            except Exception as e:
                self.log("debug", f"[Client] Could not mark the stop: {e}")

        for panel in self._answer_panels():
            try:
                self.call_on_ui(panel.close_panel)
            except Exception:
                pass

    def answer(self, icon: str, title: str, lines: list = None,
               tint: str = "#4f9de0", timeout: int = None,
               speak: str = None, on_closed=None, on_built=None,
               image: bytes = None, caption: str = None,
               action: tuple = None, hold_open=None) -> None:
        """
        Show an answer, and say it.

        The panel is what a skill uses when it has something to show; a
        notification is for something to report. Both go through here so a
        skill never has to know which UI class does it.
        """
        # Recorded against whichever turn the intent engine opened, so a
        # skill gets its context kept without doing anything about it. The
        # title and the lines rather than the spoken form: the spoken one is
        # abbreviated on purpose, and what somebody is looking at is what
        # "that" refers to.
        try:
            shown = ". ".join([str(title)] + [str(line) for line in (lines or [])])
            self.CONTEXT.record_answer(shown, speak or "")
        except Exception:
            pass

        if speak:
            try:
                # Given the shape of a sentence if it is too short to be one.
                # A two-word answer is finished before a room has noticed
                # anybody is talking, and what gets missed is the front.
                from src.assistant.speakable import flavour
                self.say(flavour(speak))
            except Exception:
                try:
                    self.say(speak)
                except Exception:
                    pass

        # Captured out here, not inside build(): build() runs on the UI
        # thread whenever it gets there, and by then something else may have
        # spoken. The token that belongs to this answer is the one taken when
        # THIS answer spoke.
        owner = self.speech_owner() if speak else 0

        def build():
            try:
                from src.ui.panels.answer import AnswerPanel
                panel = AnswerPanel(self, icon, title, lines, tint, timeout,
                                    image=image, caption=caption, action=action,
                                    hold_open=hold_open)
                # What this panel is entitled to silence. Without it, a panel
                # closing on its timeout stops whatever is talking - which by
                # then may be a different answer entirely.
                panel.speech_owner = owner
                # Told when it goes, so a caller whose answer stands for
                # something still happening - a timer making a noise - can
                # deal with that when the answer is dismissed.
                panel.on_closed = on_closed
                # And handed the panel, for a caller that may need to close it
                # itself. An alarm cancelled from somewhere else leaves its
                # own panel on screen otherwise, still offering to silence
                # something that has already gone.
                if callable(on_built):
                    try:
                        on_built(panel)
                    except Exception as e:
                        self.log("warning",
                                 f"[Answer] on_built failed: {e}")
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
            # Approved, but held twice over: awaiting_name so the device does
            # not walk off with no name, and awaiting_decision so it does not
            # walk off to the NAMING page while the question below is still on
            # screen. Settled by whichever branch answers it.
            self.USERS.approve(request.token, let_user_name=True,
                               pending_decision=True)
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
                    # rename() clears both holds, which is what lets the
                    # device through on its next poll.
                    self.USERS.rename(request.token, text.strip())
                    self.simple_notify("check", "Users",
                                       f"'{text.strip()}' can now connect.")
                else:
                    # Nothing typed: fall through to letting the device ask.
                    self.USERS.settle_decision(request.token, let_user_name=True)
                    self.simple_notify("account-question", "Users",
                                       "No name given - the device will be asked.")

            self.dialog(KeyboardDialog(self, holder, mode="text",
                                       label="Name for this user", on_done=done))

        def let_them():
            # Releases the decision hold, leaving the naming hold in place -
            # which is the point at which the device is sent to name itself.
            self.USERS.settle_decision(request.token, let_user_name=True)
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
        # Over everything rather than in a layer. A wash painted under the
        # panels dims the page and leaves them bright, which reads as the
        # dimming not working at all.
        self.OVERLAYS.set_topmost(self.DIMMER)
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

    #Sounds the client itself may play. Names, not files: none of these exist
    #yet, and registering a key against a file that has not been drawn is the
    #point - a plugin declares what it wants and the sound arrives later.
    #(key, file or files, volume, what it is for)
    #
    #Volumes are set here rather than left to whoever recorded the file. A tap
    #is heard a hundred times an hour and an alarm perhaps twice a day; the
    #difference between them is not something to leave to whichever sample
    #happened to be normalised louder.
    #No extensions. A bare name matches whatever format is actually in
    #`.audio` - .oga, .wav, .flac - so the person putting sounds there does not
    #have to convert them to whatever this file happened to guess.
    CORE_SOUNDS = (
        ("tap", ["tap-1", "tap-2", "tap-3"], 0.25,
         "A tap on the screen. One of the variations at random."),
        ("refresh",     "refresh",     0.45, "Something reloading"),
        ("timer_alarm", "timer-alarm", 0.90, "A timer finishing"),
        ("event_now",   "event-now",   0.70, "An event starting"),
        ("dialog",      "dialog",      0.30,
         "A dialog opening"),
        ("notify",      "notify",      0.40,
         "An event coming up, and notifications that ask for a sound"),
    )

    ## THE WEB PAGE

    #What a web event can be. Named here so a subscriber can check against
    #something rather than against a string it hopes is spelled the same.
    WEB_EVENTS = ("loaded", "changed", "home", "refreshed", "bookmarked",
                  "unbookmarked", "error")

    #Where the web page opens when nobody says otherwise.
    BOOKMARKS_HOME = "http://127.0.0.1:5000/webhome"

    def web_event(self, kind: str, url: str = "", title: str = "",
                  **extra) -> None:
        """
        Say that something happened in the web page.

        One event with a `kind` rather than one event per kind: a subscriber
        that wants two of them would otherwise register twice, and adding a
        third kind later would be a new event name that nothing is listening
        for.
        """
        kind = str(kind or "").strip().lower()
        if kind not in self.WEB_EVENTS:
            self.log("debug", f"[WebPage] Unknown web event '{kind}'.")
            return
        payload = {"kind": kind, "url": str(url or ""),
                   "title": str(title or ""), **extra}
        self.iterate_event_callables("on_web_event", payload, True)

    def choose_bookmark(self, on_chosen) -> None:
        """
        Pick one of the saved addresses, or go and make one.

        On the client because the list is: a widget, a tile and anything added
        later all want the same picker, and three copies of it would be three
        chances to disagree about what a bookmark looks like.

        With none saved there is nothing to choose FROM, so this opens the
        home page instead - a dialog saying "nothing here" and closing again
        leaves somebody exactly where they were.
        """
        from src.ui.grid_dialog import ItemGridDialog, GridItem

        marks = []
        try:
            marks = self.BOOKMARKS.all()
        except Exception as e:
            self.log("warning", f"[Bookmarks] Could not list: {e}")

        if not marks:
            self.goto("#webpage", data={"url": self.BOOKMARKS_HOME},
                      override=True)
            self.simple_notify(
                "bookmark-outline", "Bookmarks",
                "Nothing saved yet. Open a page and press the star.",
                history=False)
            return

        items = []
        for mark in marks:
            preview = self.BOOKMARKS.icon_path(mark)
            items.append(GridItem(
                key=mark.url, label=mark.label,
                preview=str(preview) if preview else "",
                subtitle=mark.host, icon="mdi.bookmark", data=mark))

        self.dialog(ItemGridDialog(
            self, title="Choose a bookmark", items=items,
            on_chosen=lambda item: on_chosen(item.key),
            choose_text="Use this",
            search_hint="Search bookmarks",
            empty_text="Nothing saved yet."))

    ## QUIET MODES

    def do_not_disturb(self) -> bool:
        """
        Whether notifications and sounds are being held back.

        A read rather than a stored flag on this object: the setting is what
        anything else reads, and two places holding the answer is how they
        disagree.
        """
        try:
            return bool(self.setting("audio.quiet.do_not_disturb.value", False))
        except Exception:
            return False

    def sounds_muted(self) -> bool:
        """
        Whether the panel makes any noise of its own.

        Do not disturb implies this. The reverse is not true: somebody can want
        a silent panel that still shows its notifications, which is the common
        case on a desk.
        """
        if self.do_not_disturb():
            return True
        try:
            return bool(self.setting("audio.quiet.mute_sounds.value", False))
        except Exception:
            return False

    def set_do_not_disturb(self, on: bool) -> bool:
        return self._set_quiet("audio.quiet.do_not_disturb.value", bool(on))

    def set_sounds_muted(self, on: bool) -> bool:
        return self._set_quiet("audio.quiet.mute_sounds.value", bool(on))

    def _set_quiet(self, path: str, on: bool) -> bool:
        try:
            self.apply_settings({path: on})
        except Exception as e:
            self.log("warning", f"[Client] Could not set {path}: {e}")
            return False
        # Silence takes effect now, not at the end of whatever is playing.
        if on:
            try:
                self.AUDIO.stop_all()
            except Exception:
                pass
        return True

    def drop_plugin_sections(self) -> int:
        """
        Remove any top-level settings section that belongs to a plugin.

        A plugin's settings live in its own file. One written into the client's
        - by apply_settings() with a dotted path, which is easy to reach for
        and wrong - leaves a key the settings page renders as an empty section
        beside Application and Home, because the real settings are elsewhere.

        Dropped rather than merged: there is nothing in it to keep.
        """
        try:
            keys = set(self.PLUGIN.plugins.keys())
        except Exception:
            return 0
        if not keys:
            return 0

        removed = []
        with self.SETTINGS_LOCK:
            for key in list(keys):
                try:
                    section = self.SETTINGS.get(key, None)
                except Exception:
                    continue
                if section is None:
                    continue
                try:
                    self.SETTINGS.pop(key, None)
                    removed.append(key)
                except Exception:
                    continue

        for key in removed:
            self.log("info", f"[Settings] Dropped '{key}' from the client's "
                             f"settings; it belongs to the plugin.")
        return len(removed)

    def register_core_sounds(self) -> None:
        """
        The client's own keys, so anything can play one without registering it.

        Registered whether or not the files exist: a key with nothing behind it
        is silent and says so once in the log, which is the behaviour a panel
        somebody has not put sounds into should have.
        """
        for key, filename, volume, description in self.CORE_SOUNDS:
            self.AUDIO.register("client", key, filename, volume=volume,
                                description=description)
        absent = self.AUDIO.missing()
        if absent:
            self.log("info", f"[Audio] {len(absent)} of "
                             f"{len(self.AUDIO.keys())} sounds have no file "
                             f"yet: {', '.join(absent[:6])}"
                             + ("..." if len(absent) > 6 else ""))

    def report_missing_tooling(self) -> None:
        """
        Say once, at startup, what the panel cannot do on this machine.

        Each control already explains itself when pressed, but that only helps
        somebody who thinks to press it. A control that has never worked here is
        one nobody presses, so its requirement is never read - and the log is on
        a machine they may not be sitting at.

        Notified rather than dialogued: a stack of modal dialogs on a fresh
        install is worse than the gap they describe, and a notification goes to
        the history where it can be read later.
        """
        import platform
        if platform.system() != "Linux":
            # Every one of these is a Linux service. Saying they are missing on
            # Windows would be reporting the platform as a fault.
            return

        def work():
            # On a worker, because finding out costs real time.
            #
            # bluetooth.missing() is a round trip to the system bus and, if
            # BlueZ is not running, a service activation attempt. The others
            # shell out. Doing any of it here would add that to a startup that
            # already has a browser engine and a speech model to get through.
            from src.system import media_keys, requirements, safemode
            from src.system import volume as system_volume
            from src.system import wifi

            missing = []
            try:
                if not media_keys.available():
                    missing.append("media_keys")
                if not system_volume.available():
                    missing.append("volume")
                if not wifi.available():
                    missing.append("wifi")
                if not safemode.no_bluetooth():
                    from src.system import bluetooth
                    reason = bluetooth.missing()
                    if reason:
                        missing.append(reason)
            except Exception as e:
                self.log("debug", f"[Requirements] Could not check: {e}")
                return

            def announce():
                for key in missing:
                    entry = requirements.get(key)
                    if entry is None:
                        continue
                    self.log("info", f"[Requirements] {entry.title()}")
                    self.simple_notify("assistant", entry.title(),
                                       entry.message())

            self.call_on_ui(announce)

        Thread(target=work, name="__requirements_report", daemon=True).start()

    def build_update_checker(self) -> None:
        """
        Poll GitHub for a newer commit and tell the user once when there is one.

        Deliberately not `updater.stage()` on a timer - that downloads the whole
        branch. This is one small API call, and nothing is fetched until the
        user actually asks for it.
        """
        self.UPDATE_AVAILABLE = False
        self.UPDATE_DETAIL = ""
        self.UPDATE_LATEST = None
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
            # Kept as text as well, for anything that cannot render a commit -
            # the dashboard says what is waiting rather than that something is.
            self.UPDATE_DETAIL = str(reason or "")
            # And as plain data, so a caller can tell one commit from another.
            # A dashboard holding "an update is ready" from an hour ago has no
            # way to notice a newer one landed without something to compare.
            try:
                self.UPDATE_LATEST = commit.as_dict() if commit else None
            except Exception:
                self.UPDATE_LATEST = None

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

    # Everything in this section is the assistant's own state, and it lives on
    # SERVICES.STT and SERVICES.TTS. What is left here is the names the rest
    # of the tree already says.
    #
    # `say()` and `answer()` stay because they read correctly at this level -
    # "the panel says something" is a client-level verb in a way that a status
    # field never was, and every skill in the docs calls the short one.

    def register_speech_providers(self) -> None:
        """
        Say who supplies listening and speaking on a stock panel.

        These sit at the bottom of the stack, so a plugin that claims one and
        later unloads always uncovers something that works rather than leaving
        the panel with nothing until it is restarted.

        Both are watched: a claim that nothing rebuilt would leave the panel
        running the implementation it already had, which looks from outside
        like the claim not having worked.
        """
        def parakeet(client, **kwargs):
            return STTProcessing(
                client,
                # Named rather than left to the default, because which process
                # gets spawned is the single most consequential thing here and
                # it should be readable.
                process = "parakeet",
                **kwargs)

        def speaking(client):
            """
            Which voice backends to try, in order.

            One or the other rather than both. A panel set to speak on
            another machine and quietly falling back to the local model when
            that machine is off would be a panel that works, badly, for
            reasons nobody can see - and the local model is the thing being
            moved off this hardware in the first place.
            """
            choice = str(client.setting("audio.speech.tts_backend.value", "auto")
                         or "auto").strip().lower()
            if choice == "off":
                return []

            where = str(client.setting("audio.speech.tts_where.value", "local")
                        or "local").strip().lower()
            port = client.setting("audio.speech.tts_port.value", 8770)

            if choice == "deepgram":
                # Where it runs is not a question for a voice that is not run
                # anywhere near here, so this is decided before `where`.
                from src.assistant.tts_deepgram import DeepgramTTSProcessing
                return [("Deepgram Aura", DeepgramTTSProcessing)]

            if where == "socket":
                from src.assistant.tts_socket import SocketTTSProcessing
                host = str(client.setting("audio.speech.tts_host.value", "")
                           or "").strip()
                return [(f"Speech at {host or 'nowhere'}:{port}",
                         SocketTTSProcessing)]

            if where == "subprocess":
                # Started by the service registry, which owns its lifetime -
                # it goes down when the panel does, and comes back if it
                # dies. Nothing here waits for it: SocketJudge answers "not
                # ready" until the model is open, and the rules decide until
                # then.
                # The same server, on this machine. The work still costs what
                # it costs; what changes is that it no longer happens inside
                # the interpreter the screen and the microphone share.
                from src.assistant.tts_socket import SocketTTSProcessing
                client.start_speech_process()
                return [(f"Speech beside the panel on :{port}",
                         lambda c: SocketTTSProcessing(c, "127.0.0.1", port))]

            from src.assistant.tts_pocket import PocketTTSProcessing
            return [("Pocket TTS", PocketTTSProcessing)]

        def judging(client):
            """
            What decides whether an utterance was meant for the panel.

            One or the other, never both, and never a silent fallback from a
            remote judge to a local one: a panel quietly running a model it
            was told to run somewhere else would be a panel that works,
            slowly, for a reason nobody can see.
            """
            choice = str(client.setting("assistant.wake.judge_backend.value",
                                        "off") or "off").strip().lower()
            if choice == "off":
                return []

            where = str(client.setting("assistant.wake.judge_where.value",
                                       "local") or "local").strip().lower()
            port = client.setting("assistant.wake.judge_port.value", 8771)

            if where == "socket":
                from src.assistant.judge_socket import SocketJudge
                host = str(client.setting("assistant.wake.judge_host.value", "")
                           or "").strip()
                return [(f"A judge at {host or 'nowhere'}:{port}", SocketJudge)]

            if where == "subprocess":
                # The same script another machine would run. One
                # implementation rather than two: a local mode with its own
                # code path is a second thing to keep working, and the socket
                # one is the tested one.
                from src.assistant.judge_socket import SocketJudge
                client.start_judge_process()
                return [(f"A judge beside the panel on :{port}",
                         lambda c: SocketJudge(c, "127.0.0.1", port))]

            from src.assistant.judge_local import LocalJudge
            return [("Qwen, inside the panel", LocalJudge)]

        self.SERVICES.provide("client", "assistant.stt", parakeet,
                              "Parakeet, in a child process")
        self.SERVICES.provide("client", "assistant.tts", speaking,
                              "The panel's own voice")
        self.SERVICES.provide("client", "assistant.judge", judging,
                              "Whether somebody was talking to the panel")
        self.SERVICES.watch_provider("assistant.stt", self._speech_provider_changed)
        self.SERVICES.watch_provider("assistant.tts", self._speech_provider_changed)

    #What the local speech server is registered as, and how hard it tries.
    SPEECH_SERVICE = "assistant.tts.process"
    SPEECH_RESTART = Restart(backoff=(0.0, 5.0, 30.0), window=120.0)

    def start_speech_process(self) -> None:
        """
        Run the speech server beside the panel, on the loopback.

        The same script a second machine would run. One implementation rather
        than two: a local mode with its own code path is a second thing to
        keep working, and the socket one is the tested one.

        Registered rather than started directly, so it is supervised like
        anything else - restarted if it stops, and taken down with the panel.
        """
        port = self.setting("audio.speech.tts_port.value", 8770)
        voice = str(self.setting("audio.speech.tts_voice.value", "")
                    or "").strip()
        # Always a real language. `default` was an option here once and was
        # never one of the model's - it went looking for weights by that name
        # and would not load at all. An install that has not been through a
        # settings merge may still hold it, so it is translated rather than
        # trusted. The local backend does the same in _configured_language().
        language = str(self.setting("audio.speech.tts_language.value", "")
                       or "").strip().lower()
        if language in ("", "default", "auto", "none"):
            language = "english"

        def argv():
            # A factory, so a restart takes the voice as it is now rather
            # than as it was when the assistant last started.
            from pathlib import Path as _Path
            script = (_Path(__file__).resolve().parent
                      / "assistant" / "tts-socket-process.py")
            out = [sys.executable, str(script),
                   "--host", "127.0.0.1", "--port", str(port)]
            if voice:
                out += ["--voice", voice]
            out += ["--language", language]
            return out

        def gone(code, restarting):
            if restarting:
                return
            self.log("error", f"[TTS] The speech process exited ({code}) and "
                              f"is not coming back. Nothing will be spoken.")
            self.simple_notify("error", "Assistant",
                               "The speech process stopped. Replies will be "
                               "silent until the assistant is restarted.")

        self.SERVICES.spawn("client", self.SPEECH_SERVICE, command=argv,
                            on_exit=gone, restart=self.SPEECH_RESTART)
        self.SERVICES.start(self.SPEECH_SERVICE)

    def stop_speech_process(self) -> None:
        """Stop it, if it is running. A no-op in the other two modes."""
        try:
            self.SERVICES.stop(self.SPEECH_SERVICE)
        except Exception:
            pass

    JUDGE_SERVICE = "assistant.judge.process"
    #Slower to give up than the speech one. This is optional: a judge that
    #will not start costs the panel the rules, which is what it has anyway,
    #so there is no reason to keep hammering a machine that cannot run it.
    JUDGE_RESTART = Restart(backoff=(0.0, 10.0, 60.0), window=300.0)

    def start_judge_process(self) -> None:
        """
        Run the judge beside the panel, on the loopback.

        The same script a second machine would run, for the same reason the
        speech one is: a local mode with its own code path is a second thing
        to keep working, and only one of them gets used enough to notice when
        it breaks.
        """
        port = self.setting("assistant.wake.judge_port.value", 8771)
        try:
            port = int(port or 8771)
        except (TypeError, ValueError):
            port = 8771
        model = str(self.setting("assistant.wake.judge_model.value", "")
                    or "onnx-community/Qwen3-1.7B-ONNX").strip()

        def argv():
            # A factory, so a restart takes the model as it is now rather
            # than as it was when the panel started.
            from pathlib import Path as _Path
            script = (_Path(__file__).resolve().parent
                      / "assistant" / "judge-socket-process.py")
            return [sys.executable, str(script),
                    "--host", "127.0.0.1", "--port", str(port),
                    "--model", model,
                    # int8 beside the panel: this is the same machine, with
                    # the same screen and microphone on it, as the local
                    # backend. The remote package asks for fp16.
                    "--file", "onnx/model_q4.onnx"]

        def gone(code, restarting):
            if restarting:
                return
            # Info rather than a notification. Nothing is broken: every
            # utterance goes to the rules, which is how the panel behaves
            # with the judge turned off, and a popup for a feature degrading
            # to its own default is a popup nobody can act on.
            self.log("info", f"[Judge] The judge process exited ({code}) and "
                             f"is not coming back. The rules decide.")

        self.SERVICES.spawn("client", self.JUDGE_SERVICE, command=argv,
                            on_exit=gone, restart=self.JUDGE_RESTART)
        self.SERVICES.start(self.JUDGE_SERVICE)

    def stop_judge_process(self) -> None:
        """Stop it, if it is running. A no-op in the other two modes."""
        try:
            self.SERVICES.stop(self.JUDGE_SERVICE)
        except Exception:
            pass

    def _secret_changed(self, key: str) -> None:
        """
        A credential was written. Rebuild the voice if it might want it.

        Only the voice, and only when it is not working: a backend that is
        already speaking has no reason to be torn down because some other
        key changed, and rebuilding the microphone for a credential it never
        reads is several seconds of a deaf panel for nothing.
        """
        if not self.BUILT:
            return
        try:
            if self.SERVICES.TTS.available:
                return
        except Exception:
            pass
        self.log("info", f"[Assistant] '{key}' changed - trying the voice again.")
        self.call_on_ui(self.rebuild_voice)

    def _speech_provider_changed(self, name: str) -> None:
        """
        Somebody took over listening or speaking, or gave it back.

        Nothing to do until the panel is up: the providers are registered
        during construction, and restarting an assistant that has not started
        is how a launch ends up running twice.
        """
        if not self.BUILT:
            return

        # Only the half that changed. A plugin taking over the voice has no
        # bearing on the microphone, and stopping the speech process to
        # rebuild something else is several seconds of a deaf panel for no
        # reason.
        #
        # Read off the facade rather than written out. The capability name
        # lives on the thing that owns it, and a copy here would go on
        # matching the old one after a rename - quietly restarting the wrong
        # half, which is the failure this branch exists to prevent.
        if name == self.SERVICES.TTS.PROVIDER:
            self.log("info", f"[Assistant] '{name}' changed hands - "
                             f"rebuilding the voice.")
            self.call_on_ui(self.rebuild_voice)
            return

        self.log("info", f"[Assistant] '{name}' changed hands - restarting.")
        # Marshalled: a claim arrives from a plugin's load(), which is not the
        # UI thread, and the restart opens dialogs.
        self.call_on_ui(self.restart_assistant)

    def restart_assistant(self) -> None:
        """Stop the assistant and start it again, from wherever it is now."""
        self.stop_assistant()
        # Deferred for the same reason a settings save is: whatever asked for
        # this is usually mid-way through something that navigates, and a
        # model-download prompt opened inside that lands underneath it.
        QTimer.singleShot(600, self.start_assistant)

    @property
    def ASSIST_STATUS(self) -> str:
        return self.SERVICES.STT.status

    @ASSIST_STATUS.setter
    def ASSIST_STATUS(self, value: str) -> None:
        self.SERVICES.STT.status = value

    @property
    def ASSIST_VOICE_ACTIVITY_LEVEL(self) -> float:
        return self.SERVICES.STT.level

    @ASSIST_VOICE_ACTIVITY_LEVEL.setter
    def ASSIST_VOICE_ACTIVITY_LEVEL(self, value) -> None:
        self.SERVICES.STT.level = value

    @property
    def wake_word(self) -> str:
        """Default wake word for skills. Plugins should read this rather than
        hardcoding one."""
        return self.SERVICES.STT.wake_word

    def assistant_enabled(self) -> bool:
        from src.system import safemode
        if safemode.no_assistant():
            self.log("info", "[Assistant] Off (HA_NO_ASSISTANT).")
            return False
        try:
            return bool(self.SETTINGS.assistant.enabled.value)
        except Exception:
            return False

    #Secrets the client itself owns, as opposed to a plugin's.
    #
    #Empty: speech is local now and needs no key. Kept as a declaration rather
    #than removed, since anything the client adds later belongs here and
    #secret() reads it.
    #Secrets the panel itself declares, as opposed to a plugin's.
    #
    #Registered whether or not the backend that uses one is selected, so the
    #field is there to paste a key into before choosing the voice that needs
    #it - the other way round is a backend that cannot start and a settings
    #page with nowhere to fix it.
    CORE_SECRETS = ("DEEPGRAM_API_KEY",)

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
        return self.SERVICES.STT.cancel(reason)

    def thinking(self, why: str = ""):
        """
        Hold the assistant pill at "Thinking…" while something slow runs.

        A context manager. See SpeechFacade.thinking().
        """
        return self.SERVICES.STT.thinking(why)

    def note_spoken(self, text: str) -> None:
        """Remember something the panel is about to say."""
        self.SERVICES.TTS.note_spoken(text)

    def recent_spoken(self) -> list:
        """What the panel has said lately, newest first, as (text, when)."""
        return self.SERVICES.TTS.recent_spoken()

    def speech_owner(self) -> int:
        """The token for the most recent thing said, or 0."""
        return self.SERVICES.TTS.owner()

    def say(self, text: str, thread: bool = True, voice: str = "") -> bool:
        """
        Speak. Returns whether a person actually heard it.

        Called instead of the backend directly, so a panel with speech turned
        off, a voice that failed to load, or sounds muted all degrade to
        silence rather than raising on None.

        The answer is what a caller decides on: False means show the message
        instead.

        `voice` is for this sentence only and is checked against what the
        running backend offers - see `VoiceFacade.voice_options()`.
        """
        return self.SERVICES.TTS.say(text, thread=thread, voice=voice)

    def fill_device_options(self) -> None:
        """
        Put the real devices into the two device dropdowns.

        Done at startup, because the settings page reads `options` when it
        builds the control and a list written into the template would be
        whatever machine the template was made on.

        A device that is saved but not currently connected is kept in the
        list. Dropping it would silently rewrite the setting to whatever came
        first, so a panel booted with its speaker unplugged would forget which
        speaker it had.
        """
        try:
            for path, direction in (("audio.devices.output_device", "output"),
                                    ("audio.devices.input_device", "input")):
                # Walked, not split once. `partition` leaves
                # "devices.output_device" as a single attribute name, which
                # exists nowhere - so this raised on every start and the whole
                # device check was skipped with one warning to show for it.
                setting = self.SETTINGS
                for part in path.split("."):
                    setting = getattr(setting, part)
                found = self.AUDIO.devices(direction)
                saved = str(getattr(setting, "value", "") or "").strip()
                if saved and saved not in found:
                    if self.AUDIO.is_helper(saved):
                        # Never a device. It was an ALSA plugin offered by an
                        # earlier version of this list, and something that
                        # opens without complaining and then routes audio
                        # nowhere is exactly how a panel ends up hearing
                        # nothing with no error to show for it.
                        self.log("warning",
                                 f"[Audio] '{saved}' is not a device - "
                                 f"following the system default instead.")
                        setting.value = self.AUDIO.DEFAULT_DEVICE
                    else:
                        # Unknown, but it could be hardware that is simply
                        # unplugged. Kept, so a panel booted without its
                        # speaker does not forget which speaker it had.
                        found.append(saved)
                setting.options = found
                # Said out loud. "I set it in the app and nothing changed" has
                # two causes that look identical - the change not saving, and
                # the device never being offered - and only this tells them
                # apart without a screenshot.
                self.log("info",
                         f"[Audio] {direction} options: {found} "
                         f"(currently '{saved or 'Default'}')")
        except Exception as e:
            self.log("warning", f"[Audio] Could not list devices: {e}")

    def assistant_config(self) -> tuple:
        """The settings the running assistant depends on. Compared on save to
        decide whether it needs restarting."""
        return self.SERVICES.STT.config()

    def _start_speech_status(self) -> None:
        """
        Show what speech is doing, in the Quick Settings row.

        Neither STT nor TTS emits anything when it changes state, and adding
        signals to both would mean touching the audio path in two processes
        to fix a display. A poll is a fraction of a second late, which is
        what a display can afford.
        """
        if self.SPEECH_STATUS is not None:
            return
        try:
            from src.assistant.speech_status import SpeechStatus

            self.SPEECH_STATUS = SpeechStatus(self)
            self.SPEECH_STATUS.start()
        except Exception as e:
            # A display failing to start is not a reason for the assistant
            # not to run.
            self.log("warning", f"[Status] Speech status not started: {e}")
            self.SPEECH_STATUS = None

    def stop_assistant(self) -> None:
        # Its icons first. One left behind says the panel is busy with
        # something that has stopped existing.
        if self.SPEECH_STATUS is not None:
            try:
                self.SPEECH_STATUS.stop()
            except Exception:
                pass
            self.SPEECH_STATUS = None

        self.SERVICES.STT.stop()
        self.SERVICES.TTS.detach()
        # Before the processes below, because a socket backend pointed at the
        # loopback is talking to one of them.
        self.SERVICES.JUDGE.stop()
        # Taken down with the assistant. Left running it would hold the port
        # against the one the next start spawns, and the second would find it
        # taken and be silent for a reason nobody would look for here.
        self.stop_speech_process()
        self.stop_judge_process()
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
        previous = self.SERVICES.STT.remembered()

        if current == previous:
            # Listening is unaffected, so the voice is rebuilt on its own if
            # it needs to be. Its settings have nothing to do with the
            # microphone's, and rebuilding it costs a second rather than the
            # several the speech process takes to come back - so picking a
            # different voice must not leave the panel deaf while it does.
            self.restart_voice_if_changed()
            # And the judge, separately again. It is a third thing with its
            # own settings and its own cost to rebuild, and it is optional -
            # so a change to it must not take the other two down.
            self.restart_judge_if_changed()
            return

        # Listening IS restarting, and start_assistant() builds the voice as
        # part of that. Rebuilding it here too would take the voice down, put
        # it back, and take it down again a line later - which on `subprocess`
        # means spawning a speech process only to terminate it. Remembering
        # the voice settings is enough; the restart applies them.
        self.SERVICES.TTS.remember()
        # The judge is rebuilt by the same restart, for the same reason.
        self.SERVICES.JUDGE.remember()

        was_enabled = previous[0] if previous else False
        self.SERVICES.STT.remember(current)

        # Index 2 is the phrase model - see assistant_config().
        #
        # Choosing a model in Settings is asking for it, so a "Not Now" from
        # some earlier start does not stand. Without this the decline is
        # permanent and invisible: re-picking the model does nothing, says
        # nothing, and quietly runs the fallback.
        if previous and previous[2] != current[2]:
            self.forget_declined_model(current[2])

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

    def rebuild_voice(self) -> None:
        """Take the voice down and build it again, leaving listening alone."""
        self.SERVICES.TTS.detach()
        self.stop_speech_process()
        self.SERVICES.TTS.start()
        self.SERVICES.TTS.remember()

    def restart_voice_if_changed(self) -> bool:
        """
        Rebuild the voice, and nothing else, when a voice setting changed.

        Answers whether it did. The microphone, the speech process and the
        wake word are untouched: a panel that stops listening because
        somebody picked a different voice is a panel that punishes the
        smallest setting on the page.
        """
        current = self.SERVICES.TTS.config()
        if current == self.SERVICES.TTS.remembered():
            return False
        self.SERVICES.TTS.remember(current)

        if not self.BUILT:
            # Before the first start there is nothing to rebuild, and the
            # start that follows will build it against these settings anyway.
            return False

        self.log("info", "[Assistant] Voice settings changed, rebuilding it.")
        # Stopped whatever the new setting is. Moving from `subprocess` to
        # anything else leaves a process holding the port otherwise, and the
        # next one to want it is silent for a reason nobody would look for
        # here. start_speech_process() puts it back when it is wanted.
        self.rebuild_voice()
        return True

    def restart_judge_if_changed(self) -> bool:
        """
        Rebuild the judge, and nothing else, when a judge setting changed.

        Its own comparison for the same reason the voice has one: a panel
        that stops listening for several seconds because somebody moved the
        judge's timeout is a panel that punishes the smallest setting on the
        page. Nothing here touches the microphone or the voice.
        """
        current = self.SERVICES.JUDGE.config()
        if current == self.SERVICES.JUDGE.remembered():
            return False
        self.SERVICES.JUDGE.remember(current)

        if not self.BUILT:
            # Before the first start there is nothing to rebuild, and the
            # start that follows builds it against these settings anyway.
            return False

        self.log("info", "[Judge] Settings changed, rebuilding it.")
        self.SERVICES.JUDGE.stop()
        # Stopped whatever the new setting is, for the reason the voice is:
        # moving off `subprocess` otherwise leaves a process holding the port
        # against the next one to want it. start_judge_process() puts it back
        # when the provider asks for it.
        self.stop_judge_process()
        self.SERVICES.JUDGE.start()
        return True

    def start_assistant(self) -> None:
        from src.assistant import audio

        self.SERVICES.STT.remember()
        self.SERVICES.TTS.remember()
        self.SERVICES.JUDGE.remember()

        if not self.assistant_enabled():
            self.log("info", "[Assistant] Disabled in settings.")
            return

        device_name = str(getattr(self.SETTINGS.audio.devices.input_device, "value", "") or "").strip()
        # "Default" is the dropdown's way of saying no preference, which is
        # what working_input() already means by an empty string.
        if device_name.lower() == "default":
            device_name = ""
        model = str(getattr(self.SETTINGS.assistant.speech.model, "value",
                            "parakeet-v3") or "parakeet-v3")

        if not self.speech_stack_ready(model):
            return

        ok, reason = audio.available()
        if not ok:
            self.log("warning", f"[Assistant] Audio unavailable: {reason}")
            self.simple_notify("error", "Assistant", "Voice assistant unavailable.")
            self.alert("Voice assistant unavailable",
                       "Speech-to-text could not start. Everything else works normally.",
                       detail=reason)
            return

        for d in audio.input_devices():
            self.log("info", f"[Assistant] Input device {d['index']}: {d['name']} "
                             f"- {d['channels']}ch, {d.get('hostapi', '?')}"
                             f"{' (default)' if d['is_default'] else ''}")

        # Logged stage by stage from here on.
        #
        # Each of these can hang rather than fail: opening a microphone waits
        # on the audio server, and loading a model waits on disk and on the
        # network the first time. A log that stops between two of them says
        # only "somewhere in the assistant", which is not enough to act on -
        # and the whole point of a startup log is that it is the only thing
        # left after a freeze.
        # Each candidate is opened in turn until one answers, rather than
        # trusting the one the system calls `default`.
        #
        # A listed device is not an openable one, and `default` can point at
        # something that blocks with no error and no end - which froze the panel
        # here, during startup, with nothing in the log after the attempt began.
        # The weights are dealt with below, after a microphone is found, by
        # speech_model_ready(). They used to be fetched here, unconditionally
        # and on this thread: 600MB pulled without asking, during startup, on
        # the UI thread - which is the panel frozen for however long the
        # download takes, doing the one thing the comment underneath it says
        # it exists to avoid.

        # Each candidate is opened in turn until one answers, rather than
        # trusting the one the system calls `default`.
        self.log("info", "[Assistant] Looking for an input that opens...")

        # Said out loud, but only once something has actually gone wrong.
        #
        # Each device that will not answer costs the probe timeout, so a search
        # that falls back twice is a quiet quarter of a minute with nothing on
        # screen - which looks broken rather than busy. A machine whose first
        # attempt works says nothing at all.
        announced = {"said": False}

        def attempted(name: str, ok: bool, reason: str) -> None:
            if ok or announced["said"]:
                return
            announced["said"] = True
            self.simple_notify(
                "microphone", "Assistant",
                f"'{name}' would not open. Trying the other audio inputs\u2026")

        device, chosen, note = audio.working_input(device_name,
                                                  on_attempt=attempted)
        if note:
            self.log("warning", f"[Assistant] {note}")
            self.simple_notify("assistant", "Assistant", note)

        if not chosen:
            self.log("warning", f"[Assistant] No usable microphone. {note}")
            self.simple_notify("error", "Assistant", "Microphone unavailable.")
            self.alert("Microphone unavailable",
                       "Speech-to-text could not open any audio input. "
                       "Everything else works normally.",
                       detail=note)
            return
        self.log("info", f"[Assistant] Listening through '{chosen}'.")
        if announced["said"]:
            # Only worth a second notification if the first one warned that
            # something was wrong. Otherwise this is a startup step nobody
            # needs told about.
            self.simple_notify("microphone", "Assistant",
                               f"Listening through '{chosen}'.")

        if not self.speech_model_ready(model, device, chosen):
            # Asked, declined, or downloading. Whatever happens next happens
            # from there.
            return

        self.log("info", f"[Assistant] Loading '{model}'. The first load on a "
                         f"cold cache is slow.")
        # Said on screen, not only in the log.
        #
        # Loading a speech model reads hundreds of megabytes and pins a core
        # while it does; the panel goes sluggish for a few seconds and, with
        # nothing on screen, that reads as the panel having broken rather than
        # as it being busy. A notification rather than a dialog: it is
        # information, and a modal box nobody asked for in the middle of
        # startup is worse than the pause it describes.
        try:
            self.simple_notify(
                "brain", "Assistant",
                f"Loading the '{model}' speech model. The panel may be slow "
                f"for a few seconds - the first load is the slow one.",
                history=False)
        except Exception:
            pass
        self._launch_assistant(device, model, chosen)
        self._start_speech_status()

    ## -- SPEECH MODEL DOWNLOADS -----------------------------------------
    #
    # One question, asked once: this model is not on disk, download it or
    # not. It used to be asked on every start and every settings save, and
    # for a Parakeet it was asked even when the weights WERE on disk, because
    # the cache check only knew about whisper.

    def speech_stack_ready(self, model: str) -> bool:
        """
        Whether the two things the assistant is made of are here.

        openWakeWord spots the word and Parakeet transcribes the phrase.
        There is no third option and nothing to fall back to, so a missing
        piece is a dialog naming it rather than a panel that comes up,
        listens, and never answers.
        """
        from src.assistant import parakeet, wake_spotter

        def unavailable(what: str, detail: str) -> bool:
            self.log("warning", f"[Assistant] {what}: {detail}")
            self.simple_notify("error", "Assistant", "Voice assistant unavailable.")
            self.alert("Voice assistant unavailable", what,
                       detail=detail)
            return False

        if not parakeet.is_parakeet(model):
            # The enum only offers Parakeets. Reaching here means a settings
            # file edited by hand, or one whose migration did not run.
            return unavailable(
                f"'{model}' is not a speech model this panel can use.",
                "assistant.speech.model must be parakeet-v3 or parakeet-v2.")

        ok, why = parakeet.available()
        if not ok:
            return unavailable(
                "The transcriber is not installed.", why)

        ok, why = wake_spotter.available()
        if not ok:
            return unavailable(
                "The wake word spotter is not installed.", why)

        word = str(self.wake_word or "")
        if not wake_spotter.model_for(word):
            # Checked here rather than left to the child, which would start,
            # find no model, and stop again - so the panel would report a
            # microphone problem for what is a one-dropdown fix.
            return unavailable(
                f"There is no wake word model for '{word}'.",
                "openWakeWord ships models for: alexa, hey jarvis, "
                "hey mycroft, hey rhasspy. Pick one of those in Settings.")

        return True

    def parakeet_precision(self) -> str:
        """Which Parakeet weights this panel wants. See the setting."""
        return str(self.setting("assistant.speech.parakeet_precision.value", "int8")
                   or "int8").strip().lower()

    def declined_models_path(self) -> Path:
        return self.DATAPATH / "speech-models.declined"

    def declined_models(self) -> set:
        """Model names the user has already said no to."""
        try:
            return {line.strip() for line
                    in self.declined_models_path().read_text().splitlines()
                    if line.strip()}
        except Exception:
            return set()

    def note_declined_model(self, model: str) -> None:
        try:
            self.declined_models_path().write_text(
                "\n".join(sorted(self.declined_models() | {model})) + "\n")
        except Exception as e:
            # Said out loud. Failing quietly here means the prompt returns on
            # the next start, which is the behaviour being replaced - and it
            # would look like the record was never written for a reason
            # nobody could see.
            self.log("warning", f"[Assistant] Could not remember that "
                                f"'{model}' was declined: {e}")

    def forget_declined_model(self, model: str) -> None:
        """
        Drop the record, because the question no longer applies.

        Called when the model turns up on disk and when it is chosen afresh
        in Settings. Without the second, one "Not Now" is permanent: picking
        the same model again would be met with silence and a quiet fallback,
        with nothing anywhere saying why.
        """
        remaining = self.declined_models() - {model}
        if remaining == self.declined_models():
            return
        try:
            path = self.declined_models_path()
            if remaining:
                path.write_text("\n".join(sorted(remaining)) + "\n")
            else:
                path.unlink(missing_ok=True)
        except Exception as e:
            self.log("warning", f"[Assistant] Could not clear the declined "
                                f"record for '{model}': {e}")

    def speech_model_ready(self, model: str, device,
                           chosen: str = "") -> bool:
        """
        Whether the assistant can start on this model now.

        False does not mean failure - it means somebody else starts it: the
        dialog when it is answered, or the download thread when it finishes.
        """
        from src.assistant import audio, parakeet

        precision = self.parakeet_precision()
        self.log("info", f"[Assistant] Checking for the '{model}' model...")
        if audio.model_is_cached(model, precision):
            self.forget_declined_model(model)
            return True

        if model in self.declined_models():
            # Nothing to fall back to. The assistant is Parakeet, so no
            # weights means no assistant - said once here rather than left as
            # a panel that simply never answers.
            self.log("info", f"[Assistant] '{model}' is not downloaded and was "
                             f"declined. The assistant is not starting.")
            return False

        size = (parakeet.size_hint(precision) if parakeet.is_parakeet(model)
                else audio.model_size_hint(model))
        body = (f"The voice assistant needs the '{model}' speech model, which "
                f"is not on this machine yet. It downloads once and is reused "
                f"afterwards. Until it is here the assistant cannot listen.")

        self.confirm(
            "Download speech model?", body,
            detail=f"Model: {model}\nApproximate size: {size}",
            confirm_text="Download",
            cancel_text="Not Now",
            on_confirm=lambda: self.download_speech_model(model, device, chosen),
            on_cancel=lambda: self.decline_speech_model(model, device),
        )
        return False

    def decline_speech_model(self, model: str, device) -> None:
        """
        Said no. Remembered, and the assistant does not start.

        There is no smaller model to run instead - the panel transcribes with
        Parakeet or not at all. Said on screen as well as in the log, because
        a microphone that is on and an assistant that never answers look
        identical from across the room.
        """
        self.note_declined_model(model)
        self.log("info", f"[Assistant] '{model}' download declined - "
                         f"the assistant is off.")
        self.simple_notify(
            "assistant", "Assistant",
            f"The voice assistant is off until the {model} model is "
            f"downloaded. Change the model in Settings to be asked again.")

    def download_speech_model(self, model: str, device,
                              chosen: str = "") -> None:
        """
        Fetch the weights, then start.

        On a thread, because this is the UI thread and the download is
        several minutes. And here rather than in the speech process, which
        loads its model before its socket exists - a download there is time
        during which the panel can say nothing at all.
        """
        from src.assistant import parakeet

        self.forget_declined_model(model)

        if not parakeet.is_parakeet(model):
            # Not one of ours. Nothing to fetch and nothing to start; the
            # model enum only offers Parakeets, so this is a settings file
            # edited by hand or left behind by a migration that did not run.
            self.log("warning", f"[Assistant] '{model}' is not a Parakeet "
                                f"model - nothing to download.")
            return

        precision = self.parakeet_precision()
        self.simple_notify(
            "mdi.cloud-download-outline", "Assistant",
            f"Downloading the {model} speech model, "
            f"{parakeet.size_hint(precision)}. The assistant starts when it "
            f"finishes.", False)

        def fetch(stop_event):
            ok, why = parakeet.fetch(model, log=self.log, precision=precision)

            def finished():
                if ok:
                    self.log("info", f"[Assistant] '{model}' is ready.")
                else:
                    # Not recorded as a decline. They said yes; the network
                    # said no. Asking again next start is the right answer to
                    # a download that failed rather than one refused.
                    self.log("warning", f"[Assistant] {why}")
                    self.simple_notify(
                        "mdi.alert-outline", "Assistant",
                        f"Could not download {model}, so the voice assistant "
                        f"is off. It will try again next start.")
                    return
                self._launch_assistant(device, model, chosen)

            self.call_on_ui(finished)

        self.THREADS.create("__speech_model_download_thread", fetch)
        self.THREADS.start("__speech_model_download_thread")

    def _launch_assistant(self, device, model: str, chosen: str = "") -> None:
        from src.assistant import audio

        # Before anything can speak. A machine that picked its own output on
        # boot inherits whatever volume that device was left at, and a first
        # reply nobody can hear is indistinguishable from one that never came.
        self.apply_minimum_volume()

        self.SERVICES.TTS.start()

        # Alongside the voice, and before listening. It is optional and the
        # rules decide without it, so nothing below waits on it and a failure
        # is a log line rather than an assistant that will not start - see
        # JudgeFacade.start(), which reports rather than raises.
        self.SERVICES.JUDGE.start()
        self.SERVICES.JUDGE.remember()

        try:
            # Built by whoever provides `assistant.stt` rather than named
            # here, so a plugin that claimed it is what starts.
            source = self.SERVICES.STT.build(
                input_device = device,
                # The name as well as the index. The child runs its own
                # PortAudio and enumerates separately, and the two lists have
                # been seen to disagree - the same index naming a different
                # device in each. A name it can look up itself is the only
                # thing that survives that.
                input_device_name = chosen,
                model        = model,
                wake_words   = [self.wake_word],
                session_silence_ms = int(self.setting("assistant.wake.session_silence.value", 800)),
            )
            if source is None:
                self.log("error", "[Assistant] Nothing provides speech "
                                  "recognition.")
                self.simple_notify("error", "Assistant",
                                   "Nothing provides speech recognition.")
                return
            provider = self.SERVICES.STT.provider()
            self.SERVICES.STT.start()
            self.log("info", f"[Assistant] Listening on {audio.describe(device)} "
                             f"for '{self.wake_word}', through "
                             f"{provider.description or provider.owner}.")
        except Exception as e:
            self.SERVICES.STT.detach()
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
        name = self.panel_name()
        title = f"{name} | {text}" if text else name
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

    #What the census watches. Each is something that should settle at a
    #steady number: a count that climbs every hour is the leak.
    CENSUS_EVENTS = ("on_update", "on_interaction", "on_settings_saved",
                     "on_collection", "on_visit")

    def log_accumulation(self) -> None:
        """
        Say what has piled up, once an hour, before anything is collected.

        A panel that gets slower the longer it runs and comes back after a
        restart is accumulating something. Which something is not answerable
        by reading the source - every candidate looks bounded until it is
        counted - so it is counted here, in the one place that already runs on
        a slow timer and already reports memory.

        Logged as a warning rather than info: these lines are only worth
        anything when somebody goes looking after the panel felt slow, and
        `debug` is off by default while `info` is where the ordinary
        lifecycle noise lives.
        """
        try:
            import gc as _gc

            subscribers = {}
            for name in self.CENSUS_EVENTS:
                try:
                    subscribers[name] = len(self.EVENTS["on_call"][name])
                except Exception:
                    continue

            widgets = timers = 0
            try:
                from PyQt6.QtWidgets import QWidget
                from PyQt6.QtCore import QTimer
                for obj in _gc.get_objects():
                    if isinstance(obj, QWidget):
                        widgets += 1
                    elif isinstance(obj, QTimer):
                        timers += 1
            except Exception:
                pass

            threads = 0
            try:
                threads = len(thread_enum())
            except Exception:
                pass

            timeouts = 0
            try:
                timeouts = len(getattr(self.TIMEOUTS, "timeouts", ()) or ())
            except Exception:
                pass

            self.log(
                "warning",
                "[Census] " + ", ".join(f"{k} {v}" for k, v in subscribers.items())
                + f" | widgets {widgets}, timers {timers}, threads {threads}, "
                f"timeouts {timeouts}, tracked objects {len(_gc.get_objects())}"
            )

            # Only the ones that moved, so a steady panel says nothing more and
            # a climbing one names what is climbing.
            previous = getattr(self, "_last_census", None)
            now = dict(subscribers, widgets=widgets, timers=timers,
                       threads=threads, timeouts=timeouts)
            if previous:
                grown = {k: (previous[k], v) for k, v in now.items()
                         if k in previous and v > previous[k]}
                if grown:
                    self.log("warning", "[Census] Grown since the last hour: "
                             + ", ".join(f"{k} {was}->{is_}"
                                         for k, (was, is_) in grown.items()))
            self._last_census = now
        except Exception as e:
            self.log("warning", f"[Census] Could not take a census: {e}")

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

                    # Counted BEFORE anything is released, so this is what an
                    # hour of running actually accumulated rather than what
                    # survived the tidy-up.
                    self.log_accumulation()

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

                # A wake nobody followed up on. The STT process resets itself
                # eventually, but until it does the panel refuses new wake
                # words - so it is watched here as well, on the client's own
                # clock.
                if self.SERVICES.STT.running:
                    try:
                        self.SERVICES.STT.check_wake_timeout()
                    except Exception:
                        pass

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

    def browse(self, on_chosen=None, start=None, select: str = "both",
               multiple: bool = False, title: str = "",
               choose_text: str = "", layout: str = "list") -> None:
        """
        The file explorer.

        `on_chosen` is handed a full path as a string, or a list of them when
        `multiple`. A path rather than the dialog's own item type: a caller
        wanting a file wants a file, and making them know what a `GridItem`
        is to get one is an implementation detail escaping.

        `select` is "file", "folder" or "both". `start` is where to open, and
        defaults to home.

        Nothing happens if it is cancelled - a picker that calls back with
        nothing is one every caller has to guard, and none of them would.
        """
        from pathlib import Path

        from src.ui.grid_dialog import ItemGridDialog

        kind = str(select or "both").lower()
        if not title:
            title = {"file": "Choose a file",
                     "folder": "Choose a folder"}.get(kind, "Choose")
        if not choose_text:
            choose_text = "Use these" if multiple else "Use this"

        def answer(chosen):
            if not callable(on_chosen):
                return
            try:
                if isinstance(chosen, list):
                    on_chosen([str(item.key) for item in chosen])
                elif chosen is not None:
                    on_chosen(str(chosen.key))
            except Exception as e:
                self.log("warning", f"[Browse] Handler failed: {e}")

        try:
            where = Path(start).expanduser() if start else Path.home()
            if not where.is_dir():
                where = where.parent if where.parent.is_dir() else Path.home()
        except Exception:
            where = Path.home()

        self.dialog(ItemGridDialog(
            self, title=title, browse=where, select=kind, multiple=multiple,
            layout=layout, choose_text=choose_text, on_chosen=answer))

    def pick_file(self, on_chosen=None, start=None, multiple: bool = False,
                  title: str = "") -> None:
        """One file, or several. See browse()."""
        self.browse(on_chosen=on_chosen, start=start, select="file",
                    multiple=multiple, title=title)

    def pick_folder(self, on_chosen=None, start=None, title: str = "") -> None:
        """
        One folder. See browse().

        With nothing selected this answers with the folder somebody is
        standing in, so opening it and pressing the button is a valid way to
        choose one.
        """
        self.browse(on_chosen=on_chosen, start=start, select="folder",
                    title=title)

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
            # Straight to the keyboard.
            #
            # InputDialog is a field whose only behaviour is opening one, so
            # asking for a password meant a dialog, a tap, a keyboard, and a
            # Done on each - four steps to type one word. The field was never
            # the point; the keyboard is.
            from PyQt6.QtWidgets import QLineEdit
            from src.ui.keyboard import KeyboardDialog

            field = QLineEdit(default)
            if placeholder:
                field.setPlaceholderText(placeholder)
            if password:
                field.setEchoMode(QLineEdit.EchoMode.Password)

            # KeyboardDialog hands the committed text over. Reading the field
            # instead worked by accident when it happened to be written back,
            # and not at all when it was not.
            def done(text: str = "") -> None:
                value = str(text if text is not None else "")
                if not value.strip() and not allow_empty:
                    if on_cancel:
                        on_cancel()
                    return
                if on_submit:
                    on_submit(value)

            self.DIALOG.open(KeyboardDialog(
                self, field,
                mode="numeric" if numeric else "text",
                label=title, description=detail or body,
                on_done=done))
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
        # Coerced, because a str registered fine and then failed a long way
        # from here: the asset routes do `path / filename`, and the type error
        # surfaced as a 500 on a download rather than as a bad registration.
        if not isinstance(asset, Path):
            asset = Asset(str(asset))

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
        # Separately, because a subprocess.Popen child is not a
        # multiprocessing one and active_children() never sees it - so the
        # speech process was absent from the one place that lists what is
        # running.
        print("\n--- Services ---")
        for entry in self.SERVICES.snapshot():
            mark = " ORPHANED" if entry["orphaned"] else ""
            pid = f", PID={entry['pid']}" if entry["pid"] else ""
            print(f"{entry['name']} ({entry['kind']}, {entry['owner']}, "
                  f"Alive={entry['running']}{pid}){mark}")
        for held in self.SERVICES.providers.values():
            print(f"{held.name} (provider, {held.owner}) "
                  f"{held.description}".rstrip())

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
        from src.updater import merge_values, added_paths, structure_differs
        from src.settings import scrub_secrets

        try:
            shipped = json.loads(template.read_text(encoding="utf-8"))
            installed = json.loads(self.DATA.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            self.log("warning", f"[Settings] Could not migrate data file: {e}")
            return

        added = added_paths(shipped, installed)
        removed = added_paths(installed, shipped)
        # Not just added and removed keys.
        #
        # An existing setting whose OPTIONS changed is the other way a file
        # goes stale - a new model in a dropdown is not a new key, and the
        # panel builds every dropdown from the file rather than the template.
        # Without this, adding one to the template did nothing on any install
        # that already existed.
        changed = structure_differs(shipped, installed)
        if not added and not removed and not changed:
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
        self.finish_ui_work()
        self.dump(self.settings_dict(), self.DATA)
        self.cleanup()
        self.window.hide()
        self.app.quit()

    def finish_ui_work(self) -> None:
        """
        Run what is still queued for the UI thread, and honour deleteLater.

        `call_on_ui` posts; it does not run. Anything still posted when
        `app.quit()` returns from `exec()` is discarded - so a plugin whose
        unload ends in `call_on_ui(self.player.stop)` never stops its player,
        and nothing anywhere says the work was dropped.

        That matters most for the embedded browser. A QWebEnginePage left
        alive is torn down by the interpreter after Qt has finished with it,
        and Chromium does not survive being taken apart in that order: the
        process segfaults AFTER a completely clean shutdown, which the
        launcher then reads as a crash and restarts.

        `deleteLater` needs its own pass. It posts a DeferredDelete event,
        which `processEvents` deliberately does not deliver - so the objects
        the line above just asked to delete are still alive without it.
        """
        try:
            # Queued calls first: they are what asks for the deletions.
            self.app.processEvents()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            # And once more, for anything the deletions themselves posted.
            self.app.processEvents()
        except Exception as e:
            self.log("debug", f"Could not finish queued UI work: {e}")

    @mixin_target("client.cleanup")
    def cleanup(self) -> None:
        self.log("info", "Running Cleanup")
        # Silence first, and wait for it.
        #
        # A playback thread still inside PortAudio when the library is torn
        # down takes the process down with it - SIGABRT on an assertion about
        # a mutex being destroyed while it is held. These are daemon threads,
        # so nothing else waits for them.
        try:
            stopped = self.AUDIO.stop_all(wait=0.75)
            if stopped:
                self.log("debug", f"[Audio] Stopped {stopped} sound(s).")
        except Exception as e:
            self.log("debug", f"[Audio] Could not stop cleanly: {e}")
        self.SERVICES.STT.stop()
        # Newest registration first - see ServiceRegistry.stop_all(). The
        # speech process is stopped by STT.stop() above, which goes through
        # here too, so this is whatever else is left.
        try:
            self.SERVICES.stop_all()
        except Exception as e:
            self.log("warning", f"[Services] Could not stop cleanly: {e}")
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

        # Written before the interpreter starts tearing Qt down, because that
        # is what kills this process when it dies. Everything above has
        # already finished; a segfault from here on says nothing about
        # whether the run succeeded, and the launcher needs to be able to
        # tell the difference between that and a real crash.
        record_exit_intent(code)

        if self.LOG:
            self.LOG.close()

        sys.exit(code)