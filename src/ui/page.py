from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QRect

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
        self.is_active = False

        self.__features__ = Features()

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        # Position animation — used by the parent page to slide between sub-pages
        self._anim = QPropertyAnimation(self, b"pos")
        self._anim.setDuration(250)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

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