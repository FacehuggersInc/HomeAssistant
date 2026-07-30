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

from src.styling import make_font, SIZES, set_style, line_height
from src.ui.controls.buttons import ActionButton, action_column, row_menu
from src.ui.icons import Icons, icon as resolve_icon
from src.system import wifi

if TYPE_CHECKING:
    from src.main import Client


#How often the up/down figures update while the section is on screen.
RATE_INTERVAL_MS = 1000
#How often the network list refreshes itself, without a rescan.
LIST_INTERVAL_MS = 15000


def _row_height(size: int, bold: bool = False) -> int:
    """A single line of this font, plus nothing. See styling.line_height()."""
    return line_height(size, bold)


def _signal_icon(bars: int) -> str:
    """
    Signal as the icon set's own strength glyphs.

    Blocks drawn out of \u2588 and \u2581 worked, but they read as a text
    decoration beside real icons rather than as a meter - and they sit on the
    text baseline, so a row of them looks like a font problem.
    """
    return {0: Icons.WIFI_OFF, 1: Icons.WIFI_1, 2: Icons.WIFI_2,
            3: Icons.WIFI_3, 4: Icons.WIFI_4}.get(max(0, min(4, int(bars))),
                                                  Icons.WIFI)


def _security_icon(network) -> str:
    return Icons.LOCK_OPEN if network.open else Icons.LOCK


def _chip(text: str, icon_name: str = "", tone: str = "chip") -> QWidget:
    """
    A small labelled fact.

    Networks carry four or five of these - security, band, whether it is saved -
    and a run of them joined by middots reads as one long sentence to be parsed.
    Separate chips are scannable, which is what a list of networks is for.
    """
    holder = QWidget()
    set_style(holder, "common", "transparent")
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(5)

    if icon_name:
        glyph = QLabel()
        glyph.setPixmap(resolve_icon(icon_name, color="#9aa3b2").pixmap(14, 14))
        glyph.setFixedSize(14, 14)
        row.addWidget(glyph)

    label = QLabel(str(text))
    label.setFont(make_font(SIZES.S1))
    label.setFixedHeight(18)
    label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    set_style(label, "settings", tone)
    row.addWidget(label)

    holder.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return holder


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

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)

        self._ssid_icon = QLabel()
        self._ssid_icon.setFixedSize(22, 22)
        self._ssid_icon.hide()
        title_row.addWidget(self._ssid_icon)

        self._ssid = QLabel()
        self._ssid.setFont(make_font(SIZES.S3, bold=True))
        self._ssid.setFixedHeight(_row_height(SIZES.S3, True))
        self._ssid.setSizePolicy(QSizePolicy.Policy.Preferred,
                                 QSizePolicy.Policy.Fixed)
        set_style(self._ssid, "common", "text-strong")
        title_row.addWidget(self._ssid)
        title_row.addStretch()
        self._current_layout.addLayout(title_row)

        #the facts about this connection, as chips rather than one long line
        self._chips = QHBoxLayout()
        self._chips.setContentsMargins(0, 0, 0, 0)
        self._chips.setSpacing(8)
        self._current_layout.addLayout(self._chips)

        self._detail = QLabel()
        self._detail.setFont(make_font(SIZES.S1))
        self._detail.setWordWrap(True)
        set_style(self._detail, "common", "text-muted")
        self._current_layout.addWidget(self._detail)

        self._rates = QLabel()
        self._rates.setFont(make_font(SIZES.S2, bold=True))
        self._rates.setFixedHeight(_row_height(SIZES.S3, True))
        self._rates.setSizePolicy(QSizePolicy.Policy.Preferred,
                                  QSizePolicy.Policy.Fixed)
        set_style(self._rates, "settings", "wifi-rates")
        self._current_layout.addWidget(self._rates)

        # In its own row, aligned right, so the card reads name -> facts ->
        # action rather than putting a full-width button under the name.
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 6, 0, 0)
        action_row.addStretch()
        self._disconnect = ActionButton(Icons.LINK_OFF, "Disconnect",
                                       self._on_disconnect, kind="secondary")
        action_row.addWidget(self._disconnect)
        self._current_layout.addLayout(action_row)

        #the list header, with its own scan button
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        heading = QLabel("Networks in range")
        heading.setFont(make_font(SIZES.M1, bold=True))
        heading.setFixedHeight(_row_height(SIZES.S3, True))
        heading.setSizePolicy(QSizePolicy.Policy.Preferred,
                              QSizePolicy.Policy.Fixed)
        set_style(heading, "common", "text-strong")
        header.addWidget(heading)
        header.addStretch()

        self._scan_button = ActionButton(
            Icons.MAGNIFY, "Scan", lambda: self._refresh(rescan=True),
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
        self._ssid.setText(connection.ssid)
        self._ssid_icon.setPixmap(
            resolve_icon(_signal_icon(connection.bars),
                         color="#8fe3b0").pixmap(22, 22))
        self._ssid_icon.show()

        while self._chips.count():
            item = self._chips.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
                item.widget().deleteLater()
        facts = []
        facts.append((connection.security or "Open",
                      Icons.LOCK if connection.security else Icons.LOCK_OPEN))
        if connection.signal:
            facts.append((f"{connection.signal}%", Icons.SIGNAL))
        if connection.ip_address:
            facts.append((connection.ip_address, Icons.EARTH))
        if connection.interface:
            facts.append((connection.interface, Icons.TUNE))
        for text, glyph in facts:
            self._chips.addWidget(_chip(text, glyph))
        self._chips.addStretch()

        self._detail.hide()
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

        # A signal meter of its own, at the left edge, so the strength of every
        # network in the list can be compared down a column instead of read
        # out of each row separately.
        meter = QLabel()
        meter.setPixmap(resolve_icon(_signal_icon(network.bars),
                                     color="#c8cedb").pixmap(24, 24))
        meter.setFixedSize(24, 24)
        row.addWidget(meter)

        column = QVBoxLayout()
        column.setSpacing(3)

        name = QLabel(network.ssid)
        name.setFont(make_font(SIZES.S2, bold=True))
        name.setFixedHeight(_row_height(SIZES.S2, True))
        name.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        set_style(name, "common", "text-strong")
        column.addWidget(name)

        chips = QHBoxLayout()
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(8)
        chips.addWidget(_chip(network.security or "Open",
                              _security_icon(network)))
        if network.frequency:
            chips.addWidget(_chip(network.frequency, Icons.SIGNAL))
        if network.known:
            # Its own chip, tinted: this is the difference between one tap and
            # typing a password, which is the most useful thing in the row.
            chips.addWidget(_chip("Saved", Icons.CHECK_CIRCLE, "chip-known"))
        chips.addStretch()
        column.addLayout(chips)

        row.addLayout(column, stretch=1)

        # One glyph, not a row of words. Every row of this list would otherwise
        # repeat "Forget" and "Join", and those labels are the widest thing in
        # each row - the first thing to be cut off on a narrow panel, and the
        # least worth reading by the third row.
        row.addWidget(row_menu(self.client, network.ssid, [
            ("Join this network", lambda n=network: self._on_join(n),
             Icons.LINK, "primary"),
            ("Forget it", lambda s=network.ssid: self._on_forget(s),
             Icons.DELETE_OUTLINE, "destructive") if network.known else None,
        ]))

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
