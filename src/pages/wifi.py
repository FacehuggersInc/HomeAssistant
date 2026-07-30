"""
The Wi-Fi section of Settings.

A live view rather than a list of saved values: the network list changes while
you are looking at it, and joining one happens immediately rather than on a
Save button. That puts it with Users and Plugins rather than with the sections
generated out of the settings file.

Everything that shells out does so on a worker. A scan takes seconds - long
enough to freeze the page if it ran on the UI thread, and a frozen panel looks
broken rather than busy.
"""

from __future__ import annotations

from threading import Thread
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer

from src.styling import make_font, SIZES, set_style
from src.system import wifi

if TYPE_CHECKING:
    from src.main import Client


#How often the up/down figures update while the section is on screen.
RATE_INTERVAL_MS = 1000
#How often the network list refreshes itself, without a rescan.
LIST_INTERVAL_MS = 15000
ROW_HEIGHT = 30


def _bar_glyph(bars: int) -> str:
    """Signal as blocks. A picture of the number, at a glance."""
    filled = max(0, min(4, int(bars)))
    return "\u2588" * filled + "\u2581" * (4 - filled)


class WifiSection(QWidget):
    """Current connection, live throughput, and what else is in range."""

    def __init__(self, client: "Client"):
        super().__init__()
        self.client = client
        set_style(self, "common", "transparent")

        self._networks: list = []
        self._current: Optional[wifi.Connection] = None
        self._previous_counters = None
        self._scanning = False
        self._busy = False

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)

        self._intro = QLabel()
        self._intro.setFont(make_font(SIZES.S1))
        self._intro.setWordWrap(True)
        set_style(self._intro, "settings", "settings-hint")
        self._layout.addWidget(self._intro)

        #the connected card, rebuilt in place
        self._current_card = QFrame()
        self._current_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(self._current_card, "settings", "setting-block")
        self._current_layout = QVBoxLayout(self._current_card)
        self._current_layout.setContentsMargins(14, 12, 14, 12)
        self._current_layout.setSpacing(6)
        self._layout.addWidget(self._current_card)

        self._ssid = QLabel()
        self._ssid.setFont(make_font(SIZES.S3, bold=True))
        self._ssid.setFixedHeight(ROW_HEIGHT)
        self._ssid.setSizePolicy(QSizePolicy.Policy.Preferred,
                                 QSizePolicy.Policy.Fixed)
        set_style(self._ssid, "common", "text-strong")
        self._current_layout.addWidget(self._ssid)

        self._detail = QLabel()
        self._detail.setFont(make_font(SIZES.S1))
        self._detail.setWordWrap(True)
        set_style(self._detail, "common", "text-muted")
        self._current_layout.addWidget(self._detail)

        self._rates = QLabel()
        self._rates.setFont(make_font(SIZES.S2, bold=True))
        self._rates.setFixedHeight(ROW_HEIGHT)
        self._rates.setSizePolicy(QSizePolicy.Policy.Preferred,
                                  QSizePolicy.Policy.Fixed)
        set_style(self._rates, "settings", "wifi-rates")
        self._current_layout.addWidget(self._rates)

        self._disconnect = QPushButton("Disconnect")
        self._disconnect.setFont(make_font(SIZES.S1, bold=True))
        self._disconnect.setFixedHeight(38)
        self._disconnect.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(self._disconnect, "overlays", "dialog-button-secondary")
        self._disconnect.clicked.connect(self._on_disconnect)
        self._current_layout.addWidget(self._disconnect)

        #the list header, with its own scan button
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        heading = QLabel("Networks in range")
        heading.setFont(make_font(SIZES.M1, bold=True))
        heading.setFixedHeight(ROW_HEIGHT)
        heading.setSizePolicy(QSizePolicy.Policy.Preferred,
                              QSizePolicy.Policy.Fixed)
        set_style(heading, "common", "text-strong")
        header.addWidget(heading)
        header.addStretch()

        self._scan_button = QPushButton("Scan")
        self._scan_button.setFont(make_font(SIZES.S1, bold=True))
        self._scan_button.setFixedHeight(38)
        self._scan_button.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(self._scan_button, "overlays", "dialog-button-secondary")
        self._scan_button.clicked.connect(lambda: self._refresh(rescan=True))
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

        # Both stopped while the section is off screen. A wall panel spends most
        # of its time on the home page, and neither a byte counter nor a network
        # list is worth a subprocess a second when nobody is reading it.
        self._rate_timer = QTimer(self)
        self._rate_timer.setInterval(RATE_INTERVAL_MS)
        self._rate_timer.timeout.connect(self._tick_rates)

        self._list_timer = QTimer(self)
        self._list_timer.setInterval(LIST_INTERVAL_MS)
        self._list_timer.timeout.connect(lambda: self._refresh(rescan=False))

        self._apply_availability()

    ## -- lifecycle

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not wifi.available():
            return
        self._previous_counters = None
        self._refresh(rescan=False)
        self._rate_timer.start()
        self._list_timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._rate_timer.stop()
        self._list_timer.stop()

    def _apply_availability(self) -> None:
        if not wifi.available():
            self._intro.setText(
                "This machine has no wireless tooling installed, so there is "
                "nothing to show. Installing NetworkManager would let the panel "
                "list networks and join them.")
            self._current_card.hide()
            self._scan_button.hide()
            self._empty.setText("")
            return

        if wifi.can_connect():
            self._intro.setText(
                "The network this panel is on, and what else is in range. "
                "Tap a network to join it.")
        else:
            # Honest about the ceiling rather than offering a button that
            # cannot work: NetworkManager is what stores the credential and
            # brings the link back after a reboot.
            self._intro.setText(
                "The network this panel is on. Joining a different one needs "
                "NetworkManager, which is not installed - without it a "
                "connection would not survive a reboot.")
            self._scan_button.hide()

    ## -- reading

    def _refresh(self, rescan: bool = False) -> None:
        """Re-read the connection and the list, off the UI thread."""
        if self._busy or not wifi.available():
            return
        self._busy = True
        if rescan:
            self._scanning = True
            self._scan_button.setEnabled(False)
            self._scan_button.setText("Scanning\u2026")

        def work():
            current, networks = None, []
            try:
                current = wifi.current()
                networks = wifi.scan(rescan=rescan)
            except Exception as e:
                self.client.log("warning", f"[Wifi] Could not read the network: {e}")
            finally:
                self._busy = False
            self.client.call_on_ui(lambda: self._apply(current, networks))

        Thread(target=work, name="__wifi_refresh", daemon=True).start()

    def _apply(self, current, networks) -> None:
        try:
            self._current = current
            self._networks = networks or []
            self._scanning = False
            self._scan_button.setEnabled(True)
            self._scan_button.setText("Scan")
            self._render_current()
            self._render_list()
        except RuntimeError:
            # The page has gone while the worker was out.
            pass

    def _render_current(self) -> None:
        if self._current is None:
            self._ssid.setText("Not connected")
            self._detail.setText("No wireless network." if wifi.available() else "")
            self._rates.setText("")
            self._disconnect.hide()
            return

        connection = self._current
        self._ssid.setText(f"{_bar_glyph(connection.bars)}  {connection.ssid}")
        bits = []
        if connection.security:
            bits.append(connection.security)
        else:
            bits.append("open network")
        if connection.signal:
            bits.append(f"{connection.signal}% signal")
        if connection.ip_address:
            bits.append(connection.ip_address)
        if connection.interface:
            bits.append(connection.interface)
        self._detail.setText("  \u00b7  ".join(bits))
        self._disconnect.setVisible(wifi.can_connect())

    def _render_list(self) -> None:
        while self._list.count():
            item = self._list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        if not wifi.can_connect():
            self._empty.setText("")
            return
        others = [n for n in self._networks if not n.active]
        if not others:
            self._empty.setText("Nothing else in range. Tap Scan to look again.")
            return
        self._empty.setText("")
        for network in others:
            self._list.addWidget(self._build_row(network))

    def _build_row(self, network) -> QWidget:
        card = QFrame()
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(card, "settings", "setting-block")

        row = QHBoxLayout(card)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)

        column = QVBoxLayout()
        column.setSpacing(1)

        name = QLabel(f"{_bar_glyph(network.bars)}  {network.ssid}")
        name.setFont(make_font(SIZES.S2, bold=True))
        name.setFixedHeight(ROW_HEIGHT)
        name.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        set_style(name, "common", "text-strong")
        column.addWidget(name)

        bits = [network.security or "open"]
        if network.frequency:
            bits.append(network.frequency)
        if network.known:
            # Said, because it is the difference between one tap and typing a
            # password.
            bits.append("saved")
        detail = QLabel("  \u00b7  ".join(bits))
        detail.setFont(make_font(SIZES.S1))
        detail.setFixedHeight(ROW_HEIGHT)
        detail.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        set_style(detail, "common", "text-muted")
        column.addWidget(detail)

        row.addLayout(column, stretch=1)

        if network.known:
            forget = QPushButton("Forget")
            forget.setFont(make_font(SIZES.S1, bold=True))
            forget.setFixedHeight(38)
            forget.setCursor(Qt.CursorShape.PointingHandCursor)
            set_style(forget, "overlays", "dialog-button-destructive")
            forget.clicked.connect(
                lambda _=False, s=network.ssid: self._on_forget(s))
            row.addWidget(forget)

        join = QPushButton("Join")
        join.setFont(make_font(SIZES.S1, bold=True))
        join.setFixedHeight(38)
        join.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(join, "overlays", "dialog-button-primary")
        join.clicked.connect(lambda _=False, n=network: self._on_join(n))
        row.addWidget(join)

        return card

    ## -- throughput

    def _tick_rates(self) -> None:
        """
        The up and down figures, once a second.

        Read straight from the kernel's counters, so this is a file read rather
        than a subprocess and can afford to be on the UI thread.
        """
        if self._current is None:
            return
        try:
            latest = wifi.counters(self._current.interface)
            if latest is None:
                self._rates.setText("")
                return
            if self._previous_counters is None:
                # The counters are cumulative since boot, so the first sample is
                # not a rate. Showing one would report the whole uptime's
                # traffic as this second's.
                self._previous_counters = latest
                self._rates.setText("\u2193 \u2014      \u2191 \u2014")
                return
            down, up = wifi.rates(self._previous_counters, latest)
            self._previous_counters = latest
            self._rates.setText(
                f"\u2193 {wifi.human_rate(down)}      \u2191 {wifi.human_rate(up)}")
        except RuntimeError:
            self._rate_timer.stop()

    ## -- acting

    def _on_join(self, network) -> None:
        if network.known or network.open:
            # A saved profile already holds the credential, and an open network
            # has none to hold.
            self._join(network.ssid, "")
            return

        self.client.prompt(
            f"Join {network.ssid}", f"{network.security} password:",
            on_submit=lambda text, s=network.ssid: self._join(s, text),
            password=True)

    def _join(self, ssid: str, password: str) -> None:
        dialog = self.client.progress("Connecting", f"Joining {ssid}\u2026")

        def work():
            ok, message = wifi.connect(ssid, password)

            def done():
                try:
                    self.client.close_dialog()
                except Exception:
                    pass
                if ok:
                    self.client.alert("Connected", message)
                else:
                    self.client.alert("Could not connect", message)
                self._previous_counters = None
                self._refresh(rescan=False)

            self.client.call_on_ui(done)

        Thread(target=work, name="__wifi_join", daemon=True).start()

    def _on_disconnect(self) -> None:
        def work():
            ok, message = wifi.disconnect()
            def done():
                if not ok:
                    self.client.alert("Could not disconnect", message)
                self._previous_counters = None
                self._refresh(rescan=False)
            self.client.call_on_ui(done)
        Thread(target=work, name="__wifi_disconnect", daemon=True).start()

    def _on_forget(self, ssid: str) -> None:
        def confirmed():
            def work():
                ok, message = wifi.forget(ssid)
                def done():
                    if not ok:
                        self.client.alert("Could not forget it", message)
                    self._refresh(rescan=False)
                self.client.call_on_ui(done)
            Thread(target=work, name="__wifi_forget", daemon=True).start()

        self.client.confirm(
            f"Forget {ssid}?",
            "The panel will stop rejoining it on its own, and the password "
            "will have to be entered again.",
            on_confirm=confirmed, destructive=True)


def build_wifi_page(client: "Client") -> list:
    """The section's contents, for new_category()."""
    return [WifiSection(client)]
