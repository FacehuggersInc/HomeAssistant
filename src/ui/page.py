from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import (Qt, pyqtSignal, QPropertyAnimation, QEasingCurve,
                          QRect, QThread)

from src.settings import Settings

if TYPE_CHECKING:
    from src.main import Client


# ── Features dict (unchanged from original) ──────────────────────────────────

class Features(Settings):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


# ── Page Framework ────────────────────────────────────────────────────────────

class PageFramework(QWidget):

    page_entered = pyqtSignal()
    page_left    = pyqtSignal()

    def __init__(
        self,
        key:    str,
        client: "Client",
        data:   Optional[dict] = None,
    ):
        super().__init__()
        self.name   = key
        self.client = client
        self.data   = data or {}

        self.__features__ = Features()

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Hide by default; PageHost makes the active page visible
        self.hide()

    # ── Features API (unchanged) ──────────────────────────────────────────────

    def has_feature(self, feature_key: str) -> bool:
        return bool(self.__features__.get(feature_key))

    def add_features(self, features: dict) -> None:
        for key, value in features.items():
            if not self.has_feature(key):
                self.__features__[key] = value

    def remove_features(self, features: list[str]) -> None:
        for key in features:
            if self.has_feature(key):
                del self.__features__[key]

    def features(self, feature: str = None, *args, **kwargs):
        if not feature:
            return self.__features__
        for feat in self.__features__:
            if feat == feature:
                return self.__features__[feat](*args, **kwargs)
        return None

    # ── Lifecycle hooks ───────────────────────────────────────────────────────

    def start(self) -> None:
        self.page_entered.emit()

    def stop(self) -> None:
        self.page_left.emit()

    # ── Sizing ────────────────────────────────────────────────────────────────

    def apply_window_size(self) -> None:
        if self.client and self.client.BUILT:
            w, h = self.client.SETTINGS.application.window.size.value
            self.setFixedSize(int(w), int(h))


# ── Sub-Page Framework ────────────────────────────────────────────────────────

class SubPageFramework(QWidget):

    def __init__(
        self,
        client: "Client",
        key:    str,
        coord:  tuple[int, int] = (0, 0),
    ):
        super().__init__()
        self.name      = key
        self.client    = client
        self.coord     = coord

        # Set before anything can assign through the property below.
        self._is_active = False
        # The first set_active() always runs its hook, even when the value
        # matches. A page that starts inactive has to be *told* so once, or
        # whatever its constructor started keeps running because the state
        # already looked correct.
        self._active_applied = False

        self.__features__ = Features()

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        # Position animation — used by the parent page to slide between sub-pages
        # The third argument is the PARENT. Without it the animation belongs
        # to nothing, outlives the widget it animates, and fires `finished`
        # into an object that has gone - which inside a Qt signal aborts the
        # process rather than raising.
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(250)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    # ── Active state ──────────────────────────────────────────────────────────
    #
    # A sub-page grid keeps every page constructed and slides between them, so
    # an inactive page is fully built and fully off screen. Anything it does on
    # a timer is work for something nobody can see - and with one of every
    # bundled widget and tile placed, that was the largest steady cost in the
    # app while it sat idle.
    #
    # This is a property rather than a plain attribute so that the existing
    # `page.is_active = True` assignments, and any third-party page written
    # against them, drive the hooks without needing to know they exist.

    @property
    def is_active(self) -> bool:
        return self._is_active

    @is_active.setter
    def is_active(self, state: bool) -> None:
        self.set_active(state)

    def set_active(self, state: bool) -> None:
        state = bool(state)
        if state == self._is_active and self._active_applied:
            return
        self._is_active      = state
        self._active_applied = True

        def apply():
            try:
                self.on_activated() if state else self.on_deactivated()
            except RuntimeError:
                pass    # page deleted between the call and this running
            except Exception as e:
                self.client.log(
                    "warning",
                    f"[SubPage] {self.name} on_{'activated' if state else 'deactivated'} "
                    f"failed: {e}",
                    include_traceback=True,
                )

        # The hooks start and stop QTimers, and Qt refuses that across threads
        # with "Timers cannot be started from another thread" - which is a
        # warning followed by a crash, not an exception you can catch.
        # remove_sub_page() is documented as being called from unload(), and
        # unload() runs on whichever thread asked for it: the UI thread from
        # the settings page, but a Flask worker from the API.
        if self._on_ui_thread():
            apply()
        else:
            self.client.call_on_ui(apply)

    def _on_ui_thread(self) -> bool:
        try:
            return QThread.currentThread() is self.client.app.thread()
        except Exception:
            return True     # no app to compare against; assume direct is fine

    def on_activated(self) -> None:
        """Came on screen. Start timers and subscriptions here."""
        pass

    def on_deactivated(self) -> None:
        """Went off screen. Stop anything that would keep running unseen."""
        pass

    def teardown(self) -> None:
        """
        About to be destroyed. Unsubscribe from anything on the event bus.

        Called by the parent page from remove_sub_page() and from its own
        stop(), so it runs whether the page is removed by a plugin unloading
        or destroyed by navigating away.
        """
        pass

    # ── Features API (identical to PageFramework) ─────────────────────────────

    def has_feature(self, feature_key: str) -> bool:
        return bool(self.__features__.get(feature_key))

    def add_features(self, features: dict) -> None:
        for key, value in features.items():
            if not self.has_feature(key):
                self.__features__[key] = value

    def remove_features(self, features: list[str]) -> None:
        for key in features:
            if self.has_feature(key):
                del self.__features__[key]

    def features(self, feature: str = None, *args, **kwargs):
        if not feature:
            return self.__features__
        for feat in self.__features__:
            if feat == feature:
                return self.__features__[feat](*args, **kwargs)
        return None

    # ── Sizing ────────────────────────────────────────────────────────────────

    def apply_window_size(self) -> None:
        if self.client and self.client.BUILT:
            w, h = self.client.SETTINGS.application.window.size.value
            self.setFixedSize(int(w), int(h))

    # ── Animation ─────────────────────────────────────────────────────────────

    def animate_to(self, x: int, y: int) -> None:
        self._anim.stop()
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(self._make_point(x, y))
        self._anim.start()

    def move_to(self, x: int, y: int) -> None:
        self._anim.stop()
        self.move(x, y)

    @staticmethod
    def _make_point(x: int, y: int):
        from PyQt6.QtCore import QPoint
        return QPoint(x, y)

    # ── Tick ──────────────────────────────────────────────────────────────────

    def tick(self) -> None:
        pass