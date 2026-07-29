from __future__ import annotations
from typing import TYPE_CHECKING

from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QFrame, QSlider, QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import Qt, QEvent, QPoint, QRect, QTimer

from src.styling import make_font, SIZES, set_style
from src.ui.overlays import Panel
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons, icon as resolve_icon
from src.system import volume as system_volume


def _local_ip() -> str:
    """
    The address another machine on the network can reach this one on.

    A UDP connect to a public address with nothing sent - it only needs the
    routing table to pick an interface, so it works with no network traffic
    and no internet.
    """
    import socket
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        address = probe.getsockname()[0]
        probe.close()
        return address
    except Exception:
        return "localhost"

if TYPE_CHECKING:
    from src.main import Client


class QuickAccessButton(QWidget):
    """
    An icon over a label, built fresh from a registry entry each time.

    **The whole tile is the target.** It used to be an `IconButton` with a
    caption under it, so only the 20px glyph took a press and the 98x84 tile
    around it was inert - which on a touch screen means most of what looks
    like a button does nothing.
    """

    #how far a finger may travel and still count as a tap
    DRAG_SLOP = 14

    def __init__(self, client: "Client", entry, on_pressed):
        super().__init__()
        self.client = client
        self.entry  = entry
        self.on_pressed = on_pressed
        self._press = None
        self._down = False

        # Fixed height, flexible width. Fixed on both axes is what left four
        # tiles huddled against the left edge of a panel wide enough for
        # eight, with the rest of the row empty.
        self.setFixedHeight(84)
        self.setMinimumWidth(88)
        # Capped as well as flexible. Stretching to fill was the fix for four
        # tiles bunched on the left, but with three entries on a wide panel it
        # would have given three tiles 700px across, which is worse.
        self.setMaximumWidth(168)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if entry.enabled:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 6)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # A label with the glyph, not a button: the tile takes the press, and
        # a button in here would swallow it over its own small rectangle.
        self.glyph = QLabel()
        self.glyph.setFixedSize(28, 28)
        self.glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.glyph, "common", "transparent")
        try:
            colour = "#ffffff" if entry.enabled else "rgba(255,255,255,90)"
            self.glyph.setPixmap(resolve_icon(entry.icon, color=colour)
                                 .pixmap(22, 22))
        except Exception:
            pass
        layout.addWidget(self.glyph, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.label = QLabel(entry.label)
        self.label.setFont(make_font(SIZES.S1))
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        set_style(self.label, "common",
                  "text-strong" if entry.enabled else "text-muted")
        layout.addWidget(self.label)

        # So every press lands on the tile rather than on whichever child
        # happened to be under the finger.
        for child in (self.glyph, self.label):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.apply_state()

    ## -- input

    def mousePressEvent(self, event) -> None:
        if not self.entry.enabled:
            return
        self._press = event.globalPosition().toPoint()
        self._down = True
        self.apply_state()

    def mouseReleaseEvent(self, event) -> None:
        start, self._press = self._press, None
        was_down, self._down = self._down, False
        self.apply_state()
        if not was_down or not self.entry.enabled:
            return
        if start is not None:
            moved = event.globalPosition().toPoint() - start
            if max(abs(moved.x()), abs(moved.y())) > self.DRAG_SLOP:
                return      # a scroll of the card, not a tap on the tile
        self.on_pressed(self.entry)

    def apply_state(self) -> None:
        if not self.entry.enabled:
            set_style(self, "quick", "quick-tile-disabled")
            return
        if self._down:
            set_style(self, "quick", "quick-tile-pressed")
            return
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
        self._checking_update = False
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
        self._btn_update    = IconButton(Icons.DOWNLOAD, self._show_update, size=24)
        self._btn_wallpaper = IconButton(Icons.IMAGE, self._cycle_wallpaper, size=24)
        self._btn_pin       = IconButton(Icons.PIN, self._pin_wallpaper, size=24)
        self._btn_full      = IconButton(Icons.FULLSCREEN, self._toggle_fullscreen, size=24)
        self._btn_docs      = IconButton("mdi.book-open-variant", self._open_docs, size=24)
        self._btn_settings  = IconButton(Icons.SETTINGS, self._open_settings, size=24)
        self._btn_quit      = IconButton(Icons.CLOSE, self._quit, size=24)

        for button in (self._btn_update, self._btn_wallpaper, self._btn_pin,
                       self._btn_full, self._btn_docs, self._btn_settings,
                       self._btn_quit):
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
        # Centred, not left-hugged. AlignLeft with fixed-width tiles left the
        # rest of the row empty; centring means a row that cannot fill the
        # width sits in the middle of it rather than against one edge.
        self._quick_grid.setAlignment(Qt.AlignmentFlag.AlignTop
                                      | Qt.AlignmentFlag.AlignHCenter)
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
        self._quick_columns_used = 0

        entries = self.client.QUICK.entries()
        self._quick_empty.setVisible(not entries)
        self._quick_host.setVisible(bool(entries))

        columns = self._quick_columns(len(entries))
        for index, entry in enumerate(entries):
            tile = QuickAccessButton(self.client, entry, self._entry_pressed)
            self._quick_grid.addWidget(tile, index // columns, index % columns)
            self._tiles.append(tile)

        # Equal stretch on every column, so a part-filled last row lines up
        # with the one above instead of spreading out to fill it.
        for column in range(columns):
            self._quick_grid.setColumnStretch(column, 1)
        for column in range(columns, 16):
            self._quick_grid.setColumnStretch(column, 0)
        self._quick_columns_used = columns

    #the width one tile wants before the grid starts adding another column
    TILE_TARGET = 116

    def _quick_columns(self, count: int) -> int:
        """
        How many across, from the room there actually is.

        Was four regardless of panel width. On a 2560px screen that is four
        tiles in the left third and nothing in the other two.
        """
        usable = self._quick_host.width()
        if usable <= 0:
            usable = max(0, self.width() - 96)
        if usable <= 0:
            return min(4, max(1, count))
        columns = max(1, int(usable // self.TILE_TARGET))
        # Never more columns than entries, or the last ones stretch absurdly.
        return max(1, min(columns, max(1, count)))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Only when the answer actually changed: rebuilding the grid on every
        # frame of the open animation would be visible.
        try:
            entries = self.client.QUICK.entries()
        except Exception:
            return
        if not entries:
            return
        if self._quick_columns(len(entries)) != getattr(
                self, "_quick_columns_used", 0):
            self.rebuild_quick_access()

    def _entry_pressed(self, entry) -> None:
        try:
            entry.press()
        except Exception as e:
            self.client.log("warning", f"[QuickSettings] '{entry.uid}' failed: {e}")

        if getattr(entry, "closes_panel", False):
            # Nothing to watch flip, and whatever it did is behind this panel.
            self.close_panel()
            return

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
        self.refresh_update_button()

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

    def _open_docs(self) -> None:
        """
        Ask where. Both answers are reasonable and neither is always right.

        On the panel itself the built-in page is what you want; on a desktop
        run, the real browser is better and the address is worth having.
        """
        url = f"http://{_local_ip()}:5000/docs"
        self._restart_timeout()

        def here():
            self.close_panel()
            # Locked to the docs. The panel is a shared screen in a hallway,
            # and "open the documentation" should not also be a way to browse
            # anywhere from it.
            self.client.goto("#webpage", data={
                "url": url, "home": url,
                "lock_base": f"http://{_local_ip()}:5000/docs",
                "lock_address": True,
            })

        def elsewhere():
            try:
                from PyQt6.QtGui import QDesktopServices
                from PyQt6.QtCore import QUrl
                QDesktopServices.openUrl(QUrl(url))
            except Exception:
                pass
            self.client.simple_notify("mdi.book-open-variant", "Documentation", url)

        self.client.confirm(
            "Documentation",
            "Open the docs here on the panel, or in a browser?",
            on_confirm   = here,
            on_cancel    = elsewhere,
            confirm_text = "Open here",
            cancel_text  = "In a browser",
            detail       = url,
        )

    def _open_settings(self) -> None:
        self.close_panel()
        self.client.goto("#settings")

    def _quit(self) -> None:
        self.close_panel()
        self.client.stop()

    ## -- updates

    # Colour is the whole signal now that the button is always present: plain
    # white for "nothing known", brand green once something is waiting.
    UPDATE_IDLE_COLOR = "white"
    UPDATE_READY_COLOR = "#2ff08e"

    def refresh_update_button(self) -> None:
        """Called both when the panel opens and when a background check lands."""
        try:
            if self._checking_update:
                return   # mid-check; leave the button as it is until it lands
            ready = bool(getattr(self.client, "UPDATE_AVAILABLE", False))
            self._btn_update.setEnabled(True)
            self._btn_update.update_icon(
                Icons.DOWNLOAD,
                self.UPDATE_READY_COLOR if ready else self.UPDATE_IDLE_COLOR)
        except RuntimeError:
            pass

    def _show_update(self) -> None:
        self._restart_timeout()

        commit = getattr(self.client, "UPDATE_COMMIT", None)
        if getattr(self.client, "UPDATE_AVAILABLE", False) and commit is not None:
            self._open_update_dialog(commit)
            return

        if self._checking_update:
            return   # a second tap while one is already in flight

        # Nothing known yet - the periodic check may not have run, or may have
        # failed. Ask now rather than telling the user there is no update when
        # the truth is that nobody has looked.
        self._checking_update = True
        try:
            self._btn_update.setEnabled(False)
            self._btn_update.update_icon(Icons.REFRESH, self.UPDATE_IDLE_COLOR)
        except RuntimeError:
            pass
        self.client.check_for_update(quiet=True, on_result=self._update_checked)

    def _update_checked(self, available, commit, error) -> None:
        self._checking_update = False
        self.refresh_update_button()

        if error is not None:
            self.client.simple_notify("warning", "Update check", str(error))
            return

        if available and commit is not None:
            self._open_update_dialog(commit)
        else:
            self.client.simple_notify("check", "Up to date",
                                      "This is the latest version.")

    def _open_update_dialog(self, commit) -> None:
        body = (f"{commit.summary}\n\n"
                f"by {commit.author}, {commit.age()}\n"
                f"commit {commit.short}")
        # Only when there is more than the summary - an expandable detail
        # holding a copy of the line above it is just noise.
        detail = commit.message if commit.message != commit.summary else None

        def start():
            # The panel goes away with the restart, so close it first rather
            # than leaving it animating over a dying window.
            self.close_panel()
            self.client.begin_update()

        self.client.confirm(
            "Update available",
            body,
            on_confirm  = start,
            confirm_text= "Update now",
            cancel_text = "Later",
            detail      = detail,
        )

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
