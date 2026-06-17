from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QRect

from src.settings import Settings

if TYPE_CHECKING:
    from src.main import Client


# ── Features dict (unchanged from original) ──────────────────────────────────

class Features(Settings):
    """Exposes page capabilities to plugins and other pages."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


# ── Page Framework ────────────────────────────────────────────────────────────

class PageFramework(QWidget):
    """
    Base class for top-level pages (e.g. HomePage, SettingsPage).

    Lifecycle
    ---------
    start()  — called by Client.goto() after the page is made visible.
    stop()   — called by Client.goto() before navigating away.

    Features API
    ------------
    Identical to the original: plugins call page.features().add_widgets(...)
    etc. to add content via the features dict.

    Threading
    ---------
    Pages no longer have a threaded_update background thread.
    Use QTimer within the page or its sub-pages instead.
    """

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
        """Called after the page becomes the active visible page."""
        self.page_entered.emit()

    def stop(self) -> None:
        """Called before navigating away from this page."""
        self.page_left.emit()

    # ── Sizing ────────────────────────────────────────────────────────────────

    def apply_window_size(self) -> None:
        """Resize to match the current client window dimensions."""
        if self.client and self.client.BUILT:
            w, h = self.client.SETTINGS.application.window.size.value
            self.setFixedSize(int(w), int(h))


# ── Sub-Page Framework ────────────────────────────────────────────────────────

class SubPageFramework(QWidget):
    """
    Base class for sub-pages within a page (e.g. SubHomePage, SubTilesPage).

    Sub-pages are children of the PageHost's page widget and use a 2-D
    coordinate system identical to the original.  The PageHost moves them
    with QPropertyAnimation on pos rather than manually setting left/top.

    coord=(0,0) is the default/home sub-page.
    coord=(1,0) is one page to the right.
    coord=(0,1) is one page down.
    """

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
        """Slide this sub-page to the given position."""
        self._anim.stop()
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(self._make_point(x, y))
        self._anim.start()

    def move_to(self, x: int, y: int) -> None:
        """Instantly move this sub-page (no animation)."""
        self._anim.stop()
        self.move(x, y)

    @staticmethod
    def _make_point(x: int, y: int):
        from PyQt6.QtCore import QPoint
        return QPoint(x, y)

    # ── Tick ──────────────────────────────────────────────────────────────────

    def tick(self) -> None:
        """
        Called by the parent page's QTimer while this sub-page is active.
        Override to drive per-frame updates.
        """
        pass