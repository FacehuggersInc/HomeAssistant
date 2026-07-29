from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor

if TYPE_CHECKING:
    from src.main import Client


class Dimmer(QWidget):
    """
    A black wash over the whole window, standing in for monitor brightness.

    Real backlight control needs a DDC/CI channel the display may not expose,
    root on most Linux setups, and a different API on every platform. Painting
    over the window gets the same result for a wall panel in a dark room, with
    nothing to fail at runtime.

    It lives on OVERLAYS.passthrough because that layer is
    WA_TransparentForMouseEvents - Qt skips it entirely during hit testing, so
    a full-screen widget can sit over everything without swallowing a single
    touch. On the masked overlay layer this would have blocked the whole UI.
    """

    # Never fully black. At 100% dim the screen would be unreadable and the
    # only way back would be to find a control that cannot be seen.
    MAX_DIM_ALPHA = 200

    def __init__(self, client: "Client"):
        super().__init__()
        self.client = client
        self._level = 0.0          # 0.0 = untouched, 1.0 = as dark as it goes
        self._anim_timer = None

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    ## -- level

    def level(self) -> float:
        return self._level

    def brightness(self) -> int:
        """The level as a brightness percentage, which is how it is presented."""
        return int(round((1.0 - self._level) * 100))

    def set_brightness(self, percent: int) -> None:
        self.stop_animation()
        self.set_level(1.0 - (max(0, min(100, int(percent))) / 100.0))

    def animate_brightness(self, percent: int, duration_ms: int = 900,
                           on_done=None) -> None:
        """
        Ease to a brightness rather than snapping to it.

        A wall panel changing level on its own - going to sleep, waking when
        somebody walks past - is startling as a step change and unremarkable
        as a fade, which is the whole point of it being automatic.

        Stepped on a timer rather than a QPropertyAnimation: the level is a
        plain float, not a Qt property, and exposing one only to animate it
        would be more machinery than a ~30 step interpolation.
        """
        percent = max(0, min(100, int(percent)))
        target = 1.0 - (percent / 100.0)
        start = self._level
        if abs(target - start) < 0.001:
            self.stop_animation()
            if callable(on_done):
                on_done()
            return

        duration_ms = max(1, int(duration_ms))
        steps = max(1, min(60, duration_ms // 30))
        self.stop_animation()

        state = {"i": 0}

        def step():
            state["i"] += 1
            progress = state["i"] / steps
            # Ease out, so it settles rather than arriving at full speed.
            eased = 1 - (1 - progress) ** 3
            self.set_level(start + (target - start) * eased)
            if state["i"] >= steps:
                self.stop_animation()
                if callable(on_done):
                    on_done()

        from PyQt6.QtCore import QTimer
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(max(16, duration_ms // steps))
        self._anim_timer.timeout.connect(step)
        self._anim_timer.start()

    def stop_animation(self) -> None:
        timer = getattr(self, "_anim_timer", None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
            self._anim_timer = None

    def set_level(self, level: float) -> None:
        level = max(0.0, min(1.0, float(level)))
        if abs(level - self._level) < 0.001:
            return
        self._level = level

        if level <= 0.0:
            # Hidden rather than transparent: the passthrough layer masks
            # itself to its visible children, so hiding this shrinks the mask
            # back off the screen instead of leaving a full-window region
            # being composited for nothing.
            self.hide()
            return

        self.sync_geometry()
        self.show()
        self.raise_()
        self.update()

    ## -- geometry

    def sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(0, 0, parent.width(), parent.height())

    ## -- painting

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if self._level <= 0.0:
            return
        painter = QPainter(self)
        painter.fillRect(
            self.rect(),
            QColor(0, 0, 0, int(self._level * self.MAX_DIM_ALPHA)),
        )
