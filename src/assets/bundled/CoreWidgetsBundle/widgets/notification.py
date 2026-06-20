from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen

from src.ui.widget import Widget
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons, icon as resolve_icon
from src.styling import make_font, set_style

if TYPE_CHECKING:
    from src.main import Client


# ── Notification history item ─────────────────────────────────────────────────

class NotificationHistoryItem(QFrame):
    """A single swipe-dismissible notification row."""

    def __init__(self, history: "NotificationHistory",
                 icon: str, title: str, body: str,
                 timestamp: datetime):
        super().__init__()
        self._history   = history
        self._timestamp = timestamp

        #card background — deliberately LIGHTER than NotificationPanel's
        #own background (COLORS.DARK.BG) so each item reads as a
        #distinct card rather than blending into the panel behind it.
        #Using the same colour as the parent panel was the original bug
        #here: visually there was no contrast between "this is a
        #notification card" and "this is empty panel background".
        set_style(self, "notification", "notification-history-item")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(12)

        # Icon — resolved through the same Icons/qtawesome system every
        # other icon in the app uses, instead of just taking the first
        # character of the icon string (which is what was happening
        # before: "check" became "C", "download" became "D", etc. —
        # never an actual icon at all)
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(36, 36)
        set_style(icon_lbl, "common", "transparent")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            icon_lbl.setPixmap(resolve_icon(icon or "bell", color="white").pixmap(24, 24))
        except Exception:
            #resolve_icon() already falls back to a generic icon for
            #unresolvable names, so this is only a last-resort guard
            #against something unexpected (e.g. a non-string icon arg)
            icon_lbl.setPixmap(resolve_icon("bell", color="white").pixmap(24, 24))
        layout.addWidget(icon_lbl)

        # Text
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.setContentsMargins(0, 0, 0, 0)

        title_row = QHBoxLayout()
        title_lbl = QLabel(title)
        title_lbl.setFont(make_font(14, bold=True))
        set_style(title_lbl, "common", "text-strong")

        # Time ago
        diff     = datetime.now() - timestamp
        secs     = int(diff.total_seconds())
        mins     = secs  // 60
        hours    = mins  // 60
        if hours > 0:   time_str = f"{hours}h ago"
        elif mins > 0:  time_str = f"{mins}m ago"
        else:           time_str = f"{secs}s ago"

        time_lbl = QLabel(time_str)
        time_lbl.setFont(make_font(12))
        set_style(time_lbl, "common", "text-muted")
        time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        title_row.addWidget(title_lbl)
        title_row.addStretch()
        title_row.addWidget(time_lbl)

        body_lbl = QLabel(body[:120] + ("…" if len(body) > 120 else ""))
        body_lbl.setFont(make_font(13))
        set_style(body_lbl, "common", "text-muted")
        body_lbl.setWordWrap(True)

        text_col.addLayout(title_row)
        text_col.addWidget(body_lbl)
        layout.addLayout(text_col)

        # Dismiss button
        dismiss_btn = QPushButton("✕")
        dismiss_btn.setFixedSize(24, 24)
        set_style(dismiss_btn, "notification", "notification-dismiss")
        dismiss_btn.clicked.connect(self._remove)
        layout.addWidget(dismiss_btn)

    def _remove(self) -> None:
        self._history.remove(self._timestamp)
        self.setParent(None)   # type: ignore[arg-type]


# ── Notification history ───────────────────────────────────────────────────────

class NotificationHistory:
    """
    Tracks notification history independently of any single
    NotificationCenterWidget instance.

    This object is exposed globally via client.public (see
    NotificationCenterWidget.__init__), which means it can OUTLIVE the
    specific widget instance that created it. The home page (and its
    NotificationCenterWidget) gets destroyed and rebuilt every time
    Client.goto() navigates away and back, or a plugin reloads — but
    nothing clears client.public.notification_history on a normal page
    navigation, only on plugin unload. So self.manager can end up
    pointing at a widget whose underlying C++ object has already been
    deleted by Qt, while this Python object is still very much alive
    and still the one client.simple_notify() reaches for.

    is_manager_alive() below guards every method that touches
    self.manager for exactly this reason — calling .show()/.hide() or
    reading an attribute off a deleted QWidget raises
    "RuntimeError: wrapped C/C++ object ... has been deleted", which
    is silent and easy to trigger just by calling simple_notify() from
    any page other than the one whose widget originally created this
    history object.
    """

    def __init__(self, manager: "NotificationCenterWidget"):
        self.manager = manager
        self.client  = manager.client
        self.items:  list[tuple] = []

        if not self.client.public.has("cwb_notifications"):
            self.client.public.expose("corewidgetsbundle", "cwb_notifications", self.items)
        else:
            self.items = self.client.public.cwb_notifications

    def is_manager_alive(self) -> bool:
        """True if self.manager's underlying Qt widget still exists."""
        if self.manager is None:
            return False
        try:
            from PyQt6 import sip
            return not sip.isdeleted(self.manager)
        except ImportError:
            # sip unavailable for some reason — fall back to a plain
            # attribute access, which itself raises RuntimeError on a
            # deleted widget and is caught the same way
            try:
                self.manager.isVisible()
                return True
            except RuntimeError:
                return False

    def add(self, icon: str, title: str, body: str, timestamp: datetime = None) -> None:
        ts = timestamp or datetime.now()
        self.items.insert(0, (self, icon, title, body, ts))

        if not self.is_manager_alive():
            return

        self.manager.show()
        #refresh the panel's contents if it's already open and visible
        panel = self.manager._panel
        if panel and panel.open:
            panel.refresh_list()

    def remove(self, timestamp: datetime) -> None:
        self.items = [i for i in self.items if i[4] != timestamp]
        if not self.items and self.is_manager_alive():
            self.manager.hide()
            panel = self.manager._panel
            if panel and panel.open:
                panel.toggle()

    def clear(self) -> None:
        self.items.clear()
        if not self.is_manager_alive():
            return
        self.manager.hide()
        panel = self.manager._panel
        if panel and panel.open:
            panel.toggle()


# ── Notification center widget ────────────────────────────────────────────────

class NotificationCenterWidget(Widget):
    """
    Bell icon in the top-right corner.
    Shows a blue dot when there are unread notifications.
    Opens a dialog listing all history items.
    """

    SIZE = 55

    def __init__(self, client: "Client"):
        super().__init__(
            client = client,
            key    = "notification-center",
            anchor = "top-right:0",
            width  = self.SIZE,
            height = self.SIZE,
        )

        self._dialog_timeout_id = client.TIMEOUTS.add(
            30, self._close_dialog,
            f"notify_center_dialog:{client.uuid()}"
        )

        # Reuse the existing NotificationHistory if one was already
        # exposed by a previous NotificationCenterWidget instance,
        # re-pointing its manager at THIS fresh widget instead of
        # creating a brand new, disconnected history object. The home
        # page (and this widget) gets destroyed and rebuilt on every
        # navigation away-and-back or plugin reload — without this,
        # client.public.notification_history would keep pointing at
        # whichever widget instance last constructed a NotificationHistory,
        # and that instance's underlying Qt object is gone the moment
        # you navigate elsewhere. is_manager_alive() in NotificationHistory
        # is still the real safety net for whenever NO home page exists
        # at all (e.g. simple_notify() called from Settings), but
        # re-linking here keeps that gap as small as possible.
        if client.public.has("notification_history"):
            self.history = client.public.notification_history
            self.history.manager = self
        else:
            self.history = NotificationHistory(self)
            client.public.expose(
                "corewidgetsbundle", "notification_history",
                self.history, overwrite=True
            )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._btn = IconButton(Icons.BELL, self._open_history, size=self.SIZE // 2)
        self._btn.setParent(self)
        self._btn.move(0, 0)
        self._btn.resize(self.SIZE, self.SIZE)

        # Blue dot
        self._dot = QWidget(self)
        self._dot.setGeometry(self.SIZE - 18, 5, 13, 13)
        set_style(self._dot, "notification", "notification-dot")
        self._dot.hide()

        self._panel: "NotificationPanel | None" = None

        if self.history.items:
            self.show_dot()

    def hide_dot(self) -> None:
        self._dot.hide()

    def show_dot(self) -> None:
        self._dot.show()
        self._dot.raise_()

    # Keep old names for compatibility
    def hide(self) -> None:
        self.hide_dot()

    def show(self) -> None:
        self.show_dot()
        super().show()

    def _close_dialog(self) -> None:
        if self._panel and self._panel.open:
            self._panel.toggle()

    def _open_history(self, event=None) -> None:
        #lazily build the panel once, then just toggle it open/closed
        #from then on — same pattern TilePanel uses
        if self._panel is None:
            self._panel = NotificationPanel(self)
        self._panel.toggle()
        self.client.TIMEOUTS.start(self._dialog_timeout_id)


# ── Notification panel ──────────────────────────────────────────────────────

class NotificationPanel(QWidget):
    """
    Slide-in panel listing notification history, anchored to the
    right edge of the screen — same approach as TilePanel
    (src/ui/widgets/tile_panel.py), which is known to position and
    animate correctly.

    Why not DialogManager: NotificationDialog used to be parented and
    shown via client.DIALOG.open(), which reparents the widget onto the
    overlay manager AFTER its position was already set in __init__.
    Reparenting a QWidget resets its geometry relative to the new
    parent, which is what caused the dialog to always appear centred
    instead of at the top-right corner. This panel sidesteps that
    entirely by parenting itself directly to the overlay manager up
    front, then sliding in/out via QPropertyAnimation the same way
    TilePanel does — no DialogManager involved at all.
    """

    WIDTH = 475

    def __init__(self, manager: NotificationCenterWidget):
        super().__init__(manager.client.window)
        self.manager = manager
        self.client  = manager.client
        self.open    = False

        margin = int(self.client.SETTINGS.home.widget_margin.value)
        win_w  = int(self.client.SETTINGS.application.window.size.value[0])
        win_h  = int(self.client.SETTINGS.application.window.size.value[1])

        self.setFixedSize(self.WIDTH, win_h - (margin * 3) - 55)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("notif_panel")
        set_style(self, "notification", "notification-panel", object_tag="QWidget#notif_panel")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        #header — title, close button, clear-history button
        header = QHBoxLayout()
        title_lbl = QLabel("Notifications")
        title_lbl.setFont(make_font(20, bold=True))
        set_style(title_lbl, "common", "text-strong")

        close_btn = QPushButton("\u2715")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(close_btn, "notification", "notification-panel-close")
        close_btn.clicked.connect(self.toggle)

        clear_btn = QPushButton("Clear history")
        clear_btn.setFixedWidth(120)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(clear_btn, "notification", "notification-panel-clear")
        clear_btn.clicked.connect(self.manager.history.clear)

        header.addWidget(title_lbl)
        header.addStretch()
        header.addWidget(clear_btn)
        header.addWidget(close_btn)
        outer.addLayout(header)

        #scrollable list of history items
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        set_style(scroll, "notification", "notification-scroll", object_tag="QScrollArea")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._list_widget = QWidget()
        set_style(self._list_widget, "common", "transparent")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()

        scroll.setWidget(self._list_widget)
        outer.addWidget(scroll)

        self._populate()

        #start fully off-screen past the right edge — slid into view by toggle()
        self.move(win_w, (margin * 2) + 55)
        self.hide()

        self.anim = QPropertyAnimation(self, b"pos")
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def toggle(self) -> None:
        """Slide the panel in if closed, or out if open — same approach as TilePanel.toggle()."""
        margin = int(self.client.SETTINGS.home.widget_margin.value)
        win_w  = int(self.client.SETTINGS.application.window.size.value[0])
        y      = (margin * 2) + 55

        self.anim.stop()

        if self.open:
            self.anim.setStartValue(self.pos())
            self.anim.setEndValue(QPoint(win_w, y))
            self.anim.finished.connect(self.hide)
            self.anim.finished.connect(lambda: self.anim.finished.disconnect())
            self.open = False
        else:
            self.move(win_w, y)
            self.show()
            self.raise_()
            self._populate()   #refresh contents each time it's opened
            self.anim.setStartValue(QPoint(win_w, y))
            self.anim.setEndValue(QPoint(win_w - self.WIDTH - margin, y))
            self.open = True

        self.anim.start()

    def _populate(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for entry in self.manager.history.items:
            _, icon, title, body, ts = entry
            row = NotificationHistoryItem(self.manager.history, icon, title, body, ts)
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)

    def refresh_list(self) -> None:
        self._populate()