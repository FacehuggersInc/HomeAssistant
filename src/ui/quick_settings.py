from __future__ import annotations
from typing import TYPE_CHECKING

from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QFrame, QSlider, QScrollArea,
)
from PyQt6.QtCore import Qt, QEvent, QPoint, QRect, QTimer

from src.styling import make_font, SIZES, set_style
from src.ui.overlays import Panel
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons
from src.system import volume as system_volume

if TYPE_CHECKING:
    from src.main import Client


class QuickAccessButton(QWidget):
    """An icon over a label, built fresh from a registry entry each time."""

    def __init__(self, client: "Client", entry, on_pressed):
        super().__init__()
        self.client = client
        self.entry  = entry

        self.setFixedSize(98, 84)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 6)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.button = IconButton(entry.icon, lambda: on_pressed(entry), size=20)
        self.button.setEnabled(entry.enabled)
        layout.addWidget(self.button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.label = QLabel(entry.label)
        self.label.setFont(make_font(SIZES.S1))
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        set_style(self.label, "common",
                  "text-strong" if entry.enabled else "text-muted")
        layout.addWidget(self.label)

        self.apply_state()

    def apply_state(self) -> None:
        set_style(self, "quick", "quick-tile-on" if self.entry.active()
                  else "quick-tile-off")


class _Card(QFrame):
    """
    One of the two sub-panels below the header.

    The body scrolls. The panel is pinned to roughly a third of the screen,
    and on a short display that is genuinely less room than the controls want
    - scrolling here is what lets the panel keep its proportion instead of
    growing to fit whatever happens to be registered.
    """

    def __init__(self, title: str):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(self, "quick", "quick-card")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(6)

        heading = QLabel(title)
        heading.setFont(make_font(SIZES.S1, bold=True))
        set_style(heading, "common", "text-muted")
        outer.addWidget(heading)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        set_style(self.scroll, "common", "transparent")

        self._body = QWidget()
        set_style(self._body, "common", "transparent")
        self.layout_ = QVBoxLayout(self._body)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(8)

        self.scroll.setWidget(self._body)
        outer.addWidget(self.scroll)


class _LabelledSlider(QWidget):
    """A system control: icon, name, slider, live readout."""

    def __init__(self, icon_name: str, title: str, value: int, on_change):
        super().__init__()
        self._on_change = on_change

        row = QVBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        self.icon = IconButton(icon_name, lambda: None, size=14)
        self.icon.setEnabled(False)
        self.icon.setCursor(Qt.CursorShape.ArrowCursor)
        top.addWidget(self.icon)

        name = QLabel(title)
        name.setFont(make_font(SIZES.S2))
        set_style(name, "common", "text-strong")
        top.addWidget(name)
        top.addStretch()

        self.readout = QLabel(f"{value}%")
        self.readout.setFont(make_font(SIZES.S1))
        set_style(self.readout, "common", "text-muted")
        top.addWidget(self.readout)
        row.addLayout(top)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(max(0, min(100, int(value))))
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Qt does not grow a slider to fit a handle described in a stylesheet -
        # the widget keeps its own sizeHint, about 22px, and anything taller is
        # clipped top and bottom. The handle is 32px over a 14px groove, so the
        # widget has to be told.
        self.slider.setMinimumHeight(38)
        set_style(self.slider, "quick", "quick-slider")
        self.slider.valueChanged.connect(self._changed)
        row.addWidget(self.slider)

    def _changed(self, value: int) -> None:
        self.readout.setText(f"{value}%")
        try:
            self._on_change(value)
        except Exception:
            pass


class QuickSettings(Panel):
    """
    The one global controls surface, reachable from every page.

    Everything here used to live on a per-page drawer, which meant a control
    had to be added to each page that wanted it and was simply missing from
    the ones that did not. This is registered once against the client and
    opened by a swipe from the top edge, so it behaves the same everywhere.
    """

    HEIGHT_RATIO = 1 / 3
    MIN_HEIGHT   = 200    # only bites below a ~600px display; the cards scroll
    MARGIN       = 18
    AUTO_CLOSE   = 25     # seconds

    def __init__(self, client: "Client"):
        super().__init__(
            client,
            edge             = "top",
            height           = max(self.MIN_HEIGHT,
                                   int(client.window.height() * self.HEIGHT_RATIO)),
            margin           = self.MARGIN,
            radius           = "16px",
            key              = "__quick_settings",
            animation_speed  = 240,
            destroy_on_close = False,
        )
        self._built = False
        self._tiles: list[QuickAccessButton] = []

        self._body = QWidget()
        set_style(self._body, "common", "transparent")
        self._layout = QVBoxLayout(self._body)
        self._layout.setContentsMargins(16, 12, 16, 14)
        self._layout.setSpacing(10)
        self.add_content(self._body)

        self._build_header()
        self._build_cards()

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick_clock)

        self._timeout_id = self.client.TIMEOUTS.add(
            self.AUTO_CLOSE, self.close_panel, "__timeout_quick_settings")

        self.client.QUICK.subscribe(self.rebuild_quick_access)

    ## -- header

    def _build_header(self) -> None:
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)

        title = QLabel("Quick Settings")
        title.setFont(make_font(SIZES.S3, bold=True))
        set_style(title, "common", "text-strong")
        titles.addWidget(title)

        self._clock = QLabel(self._now())
        self._clock.setFont(make_font(SIZES.S2))
        set_style(self._clock, "common", "text-muted")
        titles.addWidget(self._clock)

        header.addLayout(titles)
        header.addStretch()

        # The controls that used to sit in every page's drawer.
        self._btn_wallpaper = IconButton(Icons.IMAGE, self._cycle_wallpaper, size=24)
        self._btn_pin       = IconButton(Icons.PIN, self._pin_wallpaper, size=24)
        self._btn_full      = IconButton(Icons.FULLSCREEN, self._toggle_fullscreen, size=24)
        self._btn_settings  = IconButton(Icons.SETTINGS, self._open_settings, size=24)
        self._btn_quit      = IconButton(Icons.CLOSE, self._quit, size=24)

        for button in (self._btn_wallpaper, self._btn_pin, self._btn_full,
                       self._btn_settings, self._btn_quit):
            header.addWidget(button)

        self._layout.addLayout(header)

    def _now(self) -> str:
        return datetime.now().strftime("%A  %H:%M")

    def _tick_clock(self) -> None:
        try:
            self._clock.setText(self._now())
        except RuntimeError:
            self._clock_timer.stop()

    ## -- cards

    def _build_cards(self) -> None:
        cards = QHBoxLayout()
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(12)

        # Left: whatever has registered itself. The card already scrolls.
        self._quick_card = _Card("Quick Access")

        self._quick_host = QWidget()
        set_style(self._quick_host, "common", "transparent")
        self._quick_grid = QGridLayout(self._quick_host)
        self._quick_grid.setContentsMargins(0, 0, 0, 0)
        self._quick_grid.setSpacing(8)
        self._quick_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._quick_card.layout_.addWidget(self._quick_host)

        self._quick_empty = QLabel("Nothing registered yet.")
        self._quick_empty.setFont(make_font(SIZES.S2))
        set_style(self._quick_empty, "common", "text-muted")
        self._quick_card.layout_.addWidget(self._quick_empty)
        self._quick_empty.hide()
        self._quick_card.layout_.addStretch()

        cards.addWidget(self._quick_card, stretch=3)

        # Right: things the app itself owns.
        self._system_card = _Card("System")

        self._brightness = _LabelledSlider(
            Icons.BRIGHTNESS, "Brightness",
            self.client.DIMMER.brightness(), self._set_brightness)
        self._system_card.layout_.addWidget(self._brightness)

        if system_volume.available():
            current = system_volume.get_volume()
            self._volume = _LabelledSlider(
                Icons.VOLUME_UP, "Volume",
                current if current >= 0 else 50, self._set_volume)
            self._system_card.layout_.addWidget(self._volume)
        else:
            # Hidden rather than shown dead: on a wall panel there is no
            # console to check why a control does nothing.
            self._volume = None
            self.client.log("info", "[QuickSettings] No system volume backend "
                                    "found - the volume slider is hidden.")

        self._system_card.layout_.addStretch()
        cards.addWidget(self._system_card, stretch=2)

        self._layout.addLayout(cards)

    ## -- quick access tiles

    def rebuild_quick_access(self) -> None:
        while self._quick_grid.count():
            item = self._quick_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._tiles = []

        entries = self.client.QUICK.entries()
        self._quick_empty.setVisible(not entries)
        self._quick_host.setVisible(bool(entries))

        columns = 4
        for index, entry in enumerate(entries):
            tile = QuickAccessButton(self.client, entry, self._entry_pressed)
            self._quick_grid.addWidget(tile, index // columns, index % columns)
            self._tiles.append(tile)

    def _entry_pressed(self, entry) -> None:
        try:
            entry.press()
        except Exception as e:
            self.client.log("warning", f"[QuickSettings] '{entry.uid}' failed: {e}")
        # State may have flipped, and the press counts as interaction.
        self.refresh_states()
        self._restart_timeout()

    def refresh_states(self) -> None:
        for tile in list(self._tiles):
            try:
                tile.apply_state()
            except RuntimeError:
                self._tiles.remove(tile)

    ## -- built in controls

    def _on_sub_home(self) -> bool:
        """Whether the wallpaper controls have anything to act on right now."""
        return bool(self.client.public.has("cwb_wallpaper"))

    def _refresh_header(self) -> None:
        # Hidden off sub.home: these act on the cycling background, and the
        # publication only exists while that page is built.
        on_home = self._on_sub_home()
        self._btn_wallpaper.setVisible(on_home)
        self._btn_pin.setVisible(on_home)

        if on_home:
            pinned = self._wallpaper_state("is_pinned")
            self._btn_pin.update_icon(Icons.UNPIN if pinned else Icons.PIN)
            # Cycling a pinned wallpaper is a no-op, so the button says so.
            self._btn_wallpaper.setEnabled(self._wallpaper_state("can_cycle"))

        self._btn_full.update_icon(
            Icons.FULLSCREEN_EXIT if self.client.window.isFullScreen()
            else Icons.FULLSCREEN)

    def _wallpaper_state(self, name: str) -> bool:
        try:
            check = self.client.public.cwb_wallpaper.get(name)
            return bool(check()) if callable(check) else False
        except Exception:
            return False

    def _wallpaper_action(self, name: str) -> None:
        try:
            action = self.client.public.cwb_wallpaper.get(name)
        except Exception:
            action = None
        if not callable(action):
            return
        try:
            action()
        except Exception as e:
            self.client.log("warning", f"[QuickSettings] Wallpaper '{name}' failed: {e}")
        self._refresh_header()
        self._restart_timeout()

    def _cycle_wallpaper(self) -> None:
        self._wallpaper_action("cycle")

    def _pin_wallpaper(self) -> None:
        self._wallpaper_action("toggle_pin")

    def _toggle_fullscreen(self) -> None:
        self.client.toggle_fullscreen()
        self._refresh_header()
        self._restart_timeout()

    def _open_settings(self) -> None:
        self.close_panel()
        self.client.goto("#settings")

    def _quit(self) -> None:
        self.close_panel()
        self.client.stop()

    ## -- sliders

    def _set_brightness(self, percent: int) -> None:
        self.client.DIMMER.set_brightness(percent)
        self._restart_timeout()

    def _set_volume(self, percent: int) -> None:
        system_volume.set_volume(percent)
        self._restart_timeout()

    ## -- lifecycle

    def _restart_timeout(self) -> None:
        try:
            self.client.TIMEOUTS.start(self._timeout_id)
        except Exception:
            pass

    def open_panel(self) -> None:
        # Rebuilt on every open rather than kept in sync: entries can come and
        # go with plugin loads while this is closed, and the sliders can be
        # moved by something else entirely.
        self.rebuild_quick_access()
        self._refresh_header()
        self._sync_sliders()
        self._tick_clock()
        super().open_panel()
        self._clock_timer.start()
        self._restart_timeout()

    def close_panel(self, destroy: bool = None) -> None:
        self._clock_timer.stop()
        try:
            self.client.TIMEOUTS.cancel(self._timeout_id)
        except Exception:
            pass
        super().close_panel(destroy)

    def _sync_sliders(self) -> None:
        # blockSignals, or setting the position fires valueChanged and writes
        # the value straight back to the thing it was just read from.
        self._brightness.slider.blockSignals(True)
        self._brightness.slider.setValue(self.client.DIMMER.brightness())
        self._brightness.readout.setText(f"{self.client.DIMMER.brightness()}%")
        self._brightness.slider.blockSignals(False)

        if self._volume is not None:
            current = system_volume.get_volume()
            if current >= 0:
                self._volume.slider.blockSignals(True)
                self._volume.slider.setValue(current)
                self._volume.readout.setText(f"{current}%")
                self._volume.slider.blockSignals(False)

    ## -- dismissal

    DISMISS_EVENTS = (QEvent.Type.MouseButtonPress, QEvent.Type.TouchBegin)

    def on_interaction(self, event) -> None:
        """Close on a press anywhere outside the panel."""
        try:
            if not self.open or self._closing:
                return
            if event is None or event.type() not in self.DISMISS_EVENTS:
                return
            if self.client.DIALOG.get() is not None:
                return

            point = self._global_point(event)
            if point is None:
                return
            if QRect(self.mapToGlobal(QPoint(0, 0)), self.size()).contains(point):
                self._restart_timeout()
                return
            self.client.call_on_ui(self.close_panel)
        except RuntimeError:
            pass
        except Exception as e:
            self.client.log("warning", f"[QuickSettings] Interaction check failed: {e}")

    @staticmethod
    def _global_point(event):
        try:
            return event.globalPosition().toPoint()
        except Exception:
            pass
        try:
            points = event.points()
            if points:
                return points[0].globalPosition().toPoint()
        except Exception:
            pass
        return None
