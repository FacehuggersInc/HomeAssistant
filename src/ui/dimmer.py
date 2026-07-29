from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor

if TYPE_CHECKING:
    from src.main import Client


class Dimmer(QWidget):
    """
    Display brightness: the real thing where possible, a black wash otherwise.

    `src/ui/backlight.py` tries sysfs, systemd-logind, brightnessctl/light and
    DDC/CI in that order. When one of them answers, this drives it - the
    backlight actually changes, the panel draws less power, and a dark room
    stays dark. When none does, it paints over the window instead, which works
    everywhere and fails nowhere.

    The two are not exclusive. Hardware handles the range it covers; the
    overlay covers below `floor`, because plenty of monitors at brightness
    zero are still far too bright for a bedroom at 3am.

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

        # Below this the overlay takes over, because the hardware has run out
        # of range. 0 means the hardware covers everything.
        self.floor = 0
        self.backlight = None
        self._start_backlight()

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    ## -- hardware

    def _start_backlight(self) -> None:
        from src.ui.backlight import BacklightController
        try:
            mode = str(self.client.setting(
                "application.backlight.mode.value", "auto"))
            device = str(self.client.setting(
                "application.backlight.device.value", ""))
            self.floor = max(0, min(90, int(self.client.setting(
                "application.backlight.floor.value", 0))))
        except Exception:
            mode, device = "auto", ""

        self.backlight = BacklightController(log=self.client.log,
                                             preferred=mode, device=device)
        # Probing runs on its own thread: ddcutil detect alone can take
        # seconds, and this is called while the window is being built.
        self.backlight.start()

    def hardware_available(self) -> bool:
        return bool(self.backlight is not None and self.backlight.available())

    def describe(self) -> dict:
        if self.backlight is None:
            return {"available": False, "backend": "overlay"}
        detail = self.backlight.describe()
        detail["floor"] = self.floor
        detail["brightness"] = self.brightness()
        return detail

    def _split(self, percent: int) -> tuple:
        """
        (hardware percent, overlay level) for a wanted brightness.

        Above the floor the hardware does all of it and the overlay is off.
        Below it the hardware sits at the floor and the overlay makes up the
        difference - which is the only way to get darker than the panel's own
        minimum.
        """
        percent = max(0, min(100, int(percent)))
        if not self.hardware_available():
            return percent, 1.0 - (percent / 100.0)
        if not self.floor or percent >= self.floor:
            return percent, 0.0
        # percent == floor -> no wash, percent == 0 -> full wash
        return self.floor, (self.floor - percent) / float(self.floor)

    ## -- level

    def level(self) -> float:
        return self._level

    def brightness(self) -> int:
        """
        The level as a brightness percentage, which is how it is presented.

        The wanted value, not the overlay's - with hardware in play the
        overlay is off for most of the range, and deriving the number from it
        would report 100% at every level above the floor.
        """
        wanted = getattr(self, "_wanted_percent", None)
        if wanted is not None:
            return int(wanted)
        return int(round((1.0 - self._level) * 100))

    def set_brightness(self, percent: int) -> None:
        self.stop_animation()
        self._apply(percent)

    def _apply(self, percent: int) -> None:
        """Split a wanted brightness between the hardware and the overlay."""
        percent = max(0, min(100, int(percent)))
        self._wanted_percent = percent
        hardware, overlay = self._split(percent)
        if self.backlight is not None:
            # Returns immediately; the worker coalesces and rate limits.
            self.backlight.set(hardware)
        self.set_level(overlay)

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
        start = self.brightness()
        if start == percent:
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
            self._apply(int(round(start + (percent - start) * eased)))
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
