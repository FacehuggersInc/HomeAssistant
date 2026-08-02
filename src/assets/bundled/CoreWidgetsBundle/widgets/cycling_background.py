from __future__ import annotations
import random
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QPixmap, QPainter

if TYPE_CHECKING:
    from src.main import Client

from src.ui.icons import Icons


class CyclingBackground(QWidget):

    KEY = "cyclingbackground"

    def __init__(self, client: "Client", page):
        super().__init__(page)
        self.client = client
        self.page_  = page

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._animation_ms = int(client.SETTINGS.home.background.background_fade_duration.value)
        self._cycle_delay  = int(client.SETTINGS.home.background.background_cycle_interval.value)
        self._images_path  = Path(client.SETTINGS.home.background.images.value)
        self._used:    set  = set()    # shown since the pool was last exhausted
        self._recent:  list = []       # ordered tail of _used, for the window
        self._history: set  = set()    # _recent as a set, for O(1) lookups
        self._image_cache: list | None = None
        self._image_stamp = None
        self._last_path    = ""
        self._next_path    = ""
        self._pinned       = False

        # _progress: 0.0 = show front only, 1.0 = show back only
        self._progress_val: float = 0.0

        w = int(client.SETTINGS.application.window.size.value[0])
        h = int(client.SETTINGS.application.window.size.value[1])
        self.setGeometry(0, 0, w, h)

        # Two pixmaps — scaled to fill
        self._front_px: QPixmap | None = None
        self._back_px:  QPixmap | None = None

        # Load initial images
        p1 = self._get_next_image_path()
        p2 = self._get_next_image_path()
        if p1:
            self._front_px = self._load_pixmap(p1, w, h)
            self._last_path = p1
        if p2:
            self._back_px = self._load_pixmap(p2, w, h)
            self._next_path = p2

        # Animation drives _progress
        # The third argument is the PARENT. Without it the animation belongs
        # to nothing, outlives the widget it animates, and fires `finished`
        # into an object that has gone - which inside a Qt signal aborts the
        # process rather than raising.
        self._anim = QPropertyAnimation(self, b"crossfadeProgress", self)
        self._anim.setDuration(self._animation_ms)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.finished.connect(self._on_fade_done)

        self._cycle_timer = QTimer(self)
        self._cycle_timer.timeout.connect(self._do_cycle)
        self._cycle_timer.start(self._cycle_delay * 1000)

        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._sync_settings)
        self._sync_timer.start(5000)

        self._check_initial_pin()
        self.lower()

    # ── pyqtProperty for animation ────────────────────────────────────────────

    def _get_progress(self) -> float:
        return self._progress_val

    def _set_progress(self, value: float) -> None:
        self._progress_val = max(0.0, min(1.0, value))
        self.update()

    crossfadeProgress = pyqtProperty(float, _get_progress, _set_progress)

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        p = self._progress_val

        # Draw back image at full opacity (always visible underneath)
        if self._back_px and not self._back_px.isNull():
            painter.setOpacity(1.0)
            painter.drawPixmap(0, 0, self._back_px)

        # Draw front image fading out as progress increases
        if self._front_px and not self._front_px.isNull():
            painter.setOpacity(1.0 - p)
            painter.drawPixmap(0, 0, self._front_px)

    # ── Image loading ─────────────────────────────────────────────────────────

    def _load_pixmap(self, path: str, w: int = 0, h: int = 0) -> QPixmap | None:
        if not path:
            return None
        px = QPixmap(path)
        if px.isNull():
            return None
        # Use widget size if w/h not provided
        if w <= 0: w = self.width()
        if h <= 0: h = self.height()
        if w <= 0 or h <= 0:
            return px  # return unscaled, resizeEvent will fix it
        scaled = px.scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (scaled.width()  - w) // 2
        y = (scaled.height() - h) // 2
        return scaled.copy(x, y, w, h)

    def _rescale_all(self) -> None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        # Re-scale from the source files, not from the already-scaled pixmaps:
        # _load_pixmap takes a path, and passing None returned early and made
        # this a no-op hidden behind an `or` fallback.
        if self._last_path:
            reloaded = self._load_pixmap(self._last_path, w, h)
            if reloaded is not None:
                self._front_px = reloaded
        if self._next_path:
            reloaded = self._load_pixmap(self._next_path, w, h)
            if reloaded is not None:
                self._back_px = reloaded

    # ── Cycle ─────────────────────────────────────────────────────────────────

    def is_pinned(self) -> bool:
        return bool(self._pinned)

    def can_cycle(self) -> bool:
        return not self._pinned

    def toggle_pin(self, event=None) -> None:
        if self._pinned:
            self._pinned = False
            self.client.SETTINGS.home.layout.pinned.value = ""
            self.client.simple_notify(Icons.PIN, "Home", "Wallpaper un-pinned")
        else:
            self._pinned = True
            self.client.SETTINGS.home.layout.pinned.value = self._last_path
            self.client.simple_notify(
                Icons.PIN, "Home",
                f"Wallpaper '{Path(self._last_path).stem}' pinned"
            )

    def cycle(self, event=None) -> None:
        if self._pinned or self._anim.state() == QPropertyAnimation.State.Running:
            return
        self._cycle_timer.start(self._cycle_delay * 1000)
        self._start_fade()

    def _do_cycle(self) -> None:
        if not self._pinned:
            self._start_fade()

    def _start_fade(self) -> None:
        # Load next image into back
        next_path = self._get_next_image_path()
        if next_path:
            w, h = self.width(), self.height()
            if w <= 0: w = int(self.client.SETTINGS.application.window.size.value[0])
            if h <= 0: h = int(self.client.SETTINGS.application.window.size.value[1])
            self._back_px = self._load_pixmap(next_path, w, h)
            self._last_path = next_path
            self._next_path = next_path

        # Animate progress 0 → 1 (front fades out, back revealed)
        self._progress_val = 0.0
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def _on_fade_done(self) -> None:
        # back becomes the new front; pre-load next image into back
        self._front_px = self._back_px
        self._progress_val = 0.0  # reset so front shows fully

        next_path = self._get_next_image_path()
        if next_path:
            w, h = self.width(), self.height()
            self._back_px = self._load_pixmap(next_path, w, h)
            self._next_path = next_path

        self.update()

    def _check_initial_pin(self) -> None:
        try:
            pinned = self.client.SETTINGS.home.layout.pinned.value
            if pinned and Path(pinned).exists():
                self._pinned = True
        except Exception:
            pass

    def _list_images(self) -> list:
        """
        The wallpaper folder, cached against its mtime.

        This used to run a full iterdir() on every call, and it is called
        twice per fade - once to pick the incoming image and again to pre-load
        the one after it. A folder of a few hundred wallpapers made that two
        directory walks per cycle for a listing that almost never changes.
        """
        try:
            stamp = self._images_path.stat().st_mtime
        except OSError:
            self._image_cache = []
            self._image_stamp = None
            return []

        if self._image_stamp == stamp and self._image_cache is not None:
            return self._image_cache

        try:
            self._image_cache = [
                f for f in self._images_path.iterdir()
                if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp")
            ]
        except OSError:
            self._image_cache = []
        self._image_stamp = stamp
        return self._image_cache

    def _get_next_image_path(self) -> str:
        try:
            pinned = self.client.SETTINGS.home.layout.pinned.value
            if pinned and Path(pinned).exists():
                return pinned
        except Exception:
            pass

        all_images = self._list_images()
        if not all_images:
            return ""

        # Sets, not lists. `str(f) not in self._used` inside a comprehension is
        # a linear scan per candidate, so choosing an image was quadratic in
        # the size of the folder.
        available = [f for f in all_images if str(f) not in self._used]
        if not available:
            self._used.clear()
            available = list(all_images)

        available = [f for f in available if str(f) not in self._history] or available
        chosen = random.choice(available)
        self._used.add(str(chosen))

        # Keep the most recent third out of the running, so the same wallpaper
        # does not come back two cycles later.
        self._recent.append(str(chosen))
        window = max(1, len(all_images) // 3)
        if len(self._recent) > window:
            del self._recent[:-window]
        self._history = set(self._recent)

        return str(chosen)

    def _sync_settings(self) -> None:
        try:
            new_dur = int(self.client.SETTINGS.home.background.background_fade_duration.value)
            if new_dur != self._animation_ms:
                self._animation_ms = new_dur
                self._anim.setDuration(new_dur)
            new_delay = int(self.client.SETTINGS.home.background.background_cycle_interval.value)
            if new_delay != self._cycle_delay:
                self._cycle_delay = new_delay
                self._cycle_timer.setInterval(new_delay * 1000)
        except Exception:
            pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        if w > 0 and h > 0:
            self._rescale_all()
            if not self._back_px:
                next_path = self._get_next_image_path()
                if next_path:
                    self._back_px = self._load_pixmap(next_path, w, h)
                    self._next_path = next_path
        self.update()

    def stop(self) -> None:
        self._cycle_timer.stop()
        self._sync_timer.stop()
        self._anim.stop()