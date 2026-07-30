"""
The Bluetooth section of Settings.

Built like the Wi-Fi one and for the same reasons: a live view whose list
changes while it is on screen, where connecting happens immediately rather than
on a Save button.

BlueZ answers over D-Bus, which is fast enough that a read is not a subprocess -
but pairing and connecting are not, so those go on a worker.
"""

from __future__ import annotations

from threading import Thread
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer

from src.styling import make_font, SIZES, set_style, line_height
from src.ui.controls.buttons import ActionButton, action_column, row_menu
from src.ui.icons import Icons, icon as resolve_icon
from src.pages.wifi import _chip, _row_height
from src.system import bluetooth, requirements

if TYPE_CHECKING:
    from src.main import Client


LIST_INTERVAL_MS = 5000
#How long a scan runs before it is stopped. Discovery keeps the radio busy and
#drains anything battery-powered nearby, so it is not left on.
SCAN_SECONDS = 20


def _device_icon(device) -> str:
    """
    BlueZ's own Icon hint, mapped to something in the icon set.

    Falls back rather than guessing from the name: a device called "Office" is
    not necessarily a speaker, and a wrong picture is worse than a generic one.
    """
    hint = (device.icon or "").lower()
    if "headset" in hint or "headphone" in hint:
        return Icons.HEADPHONES
    if "audio" in hint or "speaker" in hint:
        return Icons.VOLUME_UP
    if "phone" in hint:
        return Icons.CELLPHONE
    if "input" in hint or "mouse" in hint or "keyboard" in hint:
        return Icons.TUNE
    return Icons.BLUETOOTH_CONNECTED if device.connected else Icons.BLUETOOTH


class BluetoothSection(QWidget):
    """The adapter, what is connected, and what else is around."""

    def __init__(self, client: "Client"):
        super().__init__()
        self.client = client
        set_style(self, "common", "transparent")

        self._devices: list = []
        self._powered = False
        self._busy = False

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)

        self._intro = QLabel()
        self._intro.setFont(make_font(SIZES.S1))
        self._intro.setWordWrap(True)
        set_style(self._intro, "settings", "settings-hint")
        self._layout.addWidget(self._intro)

        #the adapter, as a switch
        self._power_card = QFrame()
        self._power_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(self._power_card, "settings", "setting-block")
        power_row = QHBoxLayout(self._power_card)
        power_row.setContentsMargins(14, 10, 14, 10)
        power_row.setSpacing(12)

        self._power_label = QLabel("Bluetooth")
        self._power_label.setFont(make_font(SIZES.S3, bold=True))
        self._power_label.setFixedHeight(_row_height(SIZES.S3, True))
        self._power_label.setSizePolicy(QSizePolicy.Policy.Preferred,
                                        QSizePolicy.Policy.Fixed)
        set_style(self._power_label, "common", "text-strong")
        power_row.addWidget(self._power_label)
        power_row.addStretch()

        self._power_button = ActionButton(Icons.POWER, "Turn on",
                                        self._toggle_power, kind="primary")
        power_row.addWidget(self._power_button)
        self._layout.addWidget(self._power_card)

        #the list
        header = QHBoxLayout()
        heading = QLabel("Devices")
        heading.setFont(make_font(SIZES.M1, bold=True))
        heading.setFixedHeight(_row_height(SIZES.S3, True))
        heading.setSizePolicy(QSizePolicy.Policy.Preferred,
                              QSizePolicy.Policy.Fixed)
        set_style(heading, "common", "text-strong")
        header.addWidget(heading)
        header.addStretch()

        self._scan_button = ActionButton(Icons.MAGNIFY, "Scan", self._scan,
                                        kind="secondary")
        header.addWidget(self._scan_button)
        self._layout.addLayout(header)

        self._list = QVBoxLayout()
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(8)
        self._layout.addLayout(self._list)

        self._empty = QLabel()
        self._empty.setFont(make_font(SIZES.S2))
        self._empty.setWordWrap(True)
        set_style(self._empty, "common", "text-muted")
        self._layout.addWidget(self._empty)
        self._layout.addStretch()

        self._timer = QTimer(self)
        self._timer.setInterval(LIST_INTERVAL_MS)
        self._timer.timeout.connect(self._refresh)

        #stops discovery when the section is left with a scan running
        self._scan_stop = QTimer(self)
        self._scan_stop.setSingleShot(True)
        self._scan_stop.setInterval(SCAN_SECONDS * 1000)
        self._scan_stop.timeout.connect(self._end_scan)

        # Not asked here. Whether Bluetooth exists costs a round trip to the
        # system bus, and this is constructed while the Settings page is being
        # built - so asking froze the page rather than the page appearing and
        # then filling in.
        self._intro.setText("Looking for a Bluetooth adapter\u2026")
        self._power_card.hide()
        self._scan_button.hide()

    ## -- lifecycle

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh()
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()
        if self._scan_stop.isActive():
            self._scan_stop.stop()
            # Discovery left running keeps the radio busy and drains anything
            # battery-powered in range, long after nobody is looking at it.
            Thread(target=bluetooth.stop_scan, name="__bt_stop",
                   daemon=True).start()

    def _apply_availability(self, reason: str = "") -> None:
        if not reason:
            self._power_card.show()
            self._intro.setText(
                "Devices paired with this panel, and anything else in range. "
                "Tap a device to connect it.")
            return

        requirement = requirements.get(reason)
        self._intro.setText(requirement.message() if requirement
                            else "Bluetooth is not available on this machine.")
        self._power_card.hide()
        self._scan_button.hide()
        self._empty.setText("")

    ## -- reading

    def _refresh(self) -> None:
        """Everything is read on a worker, including whether there is anything
        to read. The first answer is a bus round trip."""
        if self._busy:
            return
        if bluetooth.known() and bluetooth.missing():
            self._apply("", False, [], bluetooth.missing())
            return
        self._busy = True

        def work():
            reason, powered, found = "", False, []
            try:
                reason = bluetooth.missing()
                if not reason:
                    state = bluetooth.snapshot()
                    powered, found = state.powered, state.devices
            except Exception as e:
                reason = "bluetooth"
                self.client.log("warning", f"[Bluetooth] Could not read: {e}")
            finally:
                self._busy = False
            self.client.call_on_ui(
                lambda: self._apply("", powered, found, reason))

        Thread(target=work, name="__bt_refresh", daemon=True).start()

    def _apply(self, _unused, powered: bool, found: list,
               reason: str = "") -> None:
        try:
            self._apply_availability(reason)
            if reason:
                return
            self._powered = powered
            self._devices = found or []
            self._render_power()
            self._render_list()
        except RuntimeError:
            pass

    def _render_power(self) -> None:
        self._power_button.set_label("Turn off" if self._powered else "Turn on")
        # Turning something off is not destructive and not the main action of
        # the page, so it steps back to secondary rather than staying green.
        self._power_button.set_kind("secondary" if self._powered else "primary")
        self._power_label.setText(
            "Bluetooth is on" if self._powered else "Bluetooth is off")
        self._scan_button.setVisible(self._powered)

    def _render_list(self) -> None:
        while self._list.count():
            item = self._list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        if not self._powered:
            self._empty.setText("Turn Bluetooth on to see devices.")
            return
        if not self._devices:
            self._empty.setText("Nothing paired or in range. Tap Scan to look, "
                                "and put the device into pairing mode first.")
            return
        self._empty.setText("")
        for device in self._devices:
            self._list.addWidget(self._build_row(device))

    def _build_row(self, device) -> QWidget:
        card = QFrame()
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(card, "settings", "setting-block")

        row = QHBoxLayout(card)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)

        # What kind of thing it is, from BlueZ's own hint. A row of headphones
        # and speakers is read by shape long before it is read by name.
        glyph = QLabel()
        glyph.setPixmap(resolve_icon(_device_icon(device),
                                     color="#8fe3b0" if device.connected
                                     else "#c8cedb").pixmap(24, 24))
        glyph.setFixedSize(24, 24)
        row.addWidget(glyph)

        column = QVBoxLayout()
        column.setSpacing(3)

        name = QLabel(device.label)
        name.setFont(make_font(SIZES.S2, bold=True))
        name.setFixedHeight(_row_height(SIZES.S2, True))
        name.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        set_style(name, "common", "text-strong")
        column.addWidget(name)

        chips = QHBoxLayout()
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(8)
        if device.connected:
            chips.addWidget(_chip("Connected", Icons.CHECK_CIRCLE, "chip-known"))
        elif device.known:
            # The difference between one tap and putting the thing into pairing
            # mode first, which is the most useful fact in the row.
            chips.addWidget(_chip("Paired", Icons.CHECK_CIRCLE, "chip-known"))
        else:
            chips.addWidget(_chip("New", Icons.PLUS_CIRCLE))
        if device.has_battery:
            chips.addWidget(_chip(
                f"{device.battery}%",
                Icons.BATTERY_ALERT if device.battery <= 20 else Icons.BATTERY,
                "chip-low" if device.battery <= 20 else "chip"))
        chips.addStretch()
        column.addLayout(chips)

        row.addLayout(column, stretch=1)

        row.addWidget(row_menu(self.client, device.label, [
            ("Disconnect" if device.connected else "Connect",
             lambda d=device: self._toggle(d),
             Icons.LINK_OFF if device.connected else Icons.LINK,
             "secondary" if device.connected else "primary"),
            ("Forget this device", lambda d=device: self._forget(d),
             Icons.DELETE_OUTLINE, "destructive") if device.known else None,
        ]))

        return card

    ## -- acting

    def _toggle_power(self) -> None:
        wanted = not self._powered

        def work():
            ok, message = bluetooth.set_powered(wanted)
            def done():
                if not ok:
                    self.client.alert("Bluetooth", message)
                self._refresh()
            self.client.call_on_ui(done)

        Thread(target=work, name="__bt_power", daemon=True).start()

    def _scan(self) -> None:
        self._scan_button.setEnabled(False)
        self._scan_button.setText("Scanning\u2026")
        self._scan_stop.start()

        def work():
            bluetooth.start_scan()
            self.client.call_on_ui(self._refresh)

        Thread(target=work, name="__bt_scan", daemon=True).start()

    def _end_scan(self) -> None:
        def work():
            bluetooth.stop_scan()
            def done():
                try:
                    self._scan_button.setEnabled(True)
                    self._scan_button.setText("Scan")
                except RuntimeError:
                    return
                self._refresh()
            self.client.call_on_ui(done)

        Thread(target=work, name="__bt_scan_end", daemon=True).start()

    def _toggle(self, device) -> None:
        joining = not device.connected
        dialog = self.client.progress(
            "Bluetooth",
            f"{'Connecting to' if joining else 'Disconnecting'} "
            f"{device.label}\u2026")

        def work():
            if joining:
                ok, message = bluetooth.connect(device)
            else:
                ok, message = bluetooth.disconnect(device)

            def done():
                try:
                    self.client.close_dialog()
                except Exception:
                    pass
                if not ok:
                    self.client.alert("Bluetooth", message)
                self._refresh()

            self.client.call_on_ui(done)

        Thread(target=work, name="__bt_connect", daemon=True).start()

    def _forget(self, device) -> None:
        def confirmed():
            def work():
                ok, message = bluetooth.forget(device)
                def done():
                    if not ok:
                        self.client.alert("Bluetooth", message)
                    self._refresh()
                self.client.call_on_ui(done)
            Thread(target=work, name="__bt_forget", daemon=True).start()

        self.client.confirm(
            f"Forget {device.label}?",
            "The pairing goes, so it stops reconnecting on its own and will "
            "have to be paired again.",
            on_confirm=confirmed, destructive=True)


def build_bluetooth_page(client: "Client") -> list:
    return [BluetoothSection(client)]
