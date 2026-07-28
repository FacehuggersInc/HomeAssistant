from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QSizePolicy, QScroller,
)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen

from src.ui.widget import Widget
from src.ui.overlays import Panel
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons, icon as resolve_icon
from src.styling import make_font, set_style

if TYPE_CHECKING:
    from src.main import Client


# ── Notification history item ─────────────────────────────────────────────────

class NotificationHistoryItem(QFrame):

    def __init__(self, history: "NotificationHistory",
                 icon: str, title: str, body: str,
                 timestamp: datetime):
        super().__init__()
        self._history   = history
        self._timestamp = timestamp

        set_style(self, "notification", "notification-history-item")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(12)

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(36, 36)
        set_style(icon_lbl, "common", "transparent")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            icon_lbl.setPixmap(resolve_icon(icon or "bell", color="white").pixmap(24, 24))
        except Exception:
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

    # The list is rendered in full every time the panel opens - one QFrame
    # with six children per entry - so an uncapped history is both unbounded
    # memory and an unbounded build on the UI thread.
    MAX_ITEMS = 50

    def __init__(self, manager: "NotificationCenterWidget"):
        self.manager = manager
        self.client  = manager.client
        self.items:  list[tuple] = []

        if not self.client.public.has("cwb_notifications"):
            self.client.public.expose("corewidgetsbundle", "cwb_notifications", self.items)
        else:
            self.items = self.client.public.cwb_notifications

    def is_manager_alive(self) -> bool:
        if self.manager is None:
            return False
        try:
            from PyQt6 import sip
            return not sip.isdeleted(self.manager)
        except ImportError:
            try:
                self.manager.isVisible()
                return True
            except RuntimeError:
                return False

    def add(self, icon: str, title: str, body: str, timestamp: datetime = None) -> None:
        ts = timestamp or datetime.now()
        self.items.insert(0, (self, icon, title, body, ts))
        # Trimmed in place, for the same reason remove() is - this list is the
        # object published on the public registry.
        if len(self.items) > self.MAX_ITEMS:
            del self.items[self.MAX_ITEMS:]

        if not self.is_manager_alive():
            return

        self.manager.show()
        #refresh the panel's contents if it's already open and visible
        panel = self.manager._panel
        if panel and panel.open:
            panel.refresh_list()

    def remove(self, timestamp: datetime) -> None:
        # Mutated, never rebound. `self.items` IS the list exposed as
        # `cwb_notifications`; assigning a new list here left the registry
        # holding the original, so after one dismissal the two diverged and
        # the published copy kept every entry forever.
        self.items[:] = [i for i in self.items if i[4] != timestamp]
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
        if self._panel is None:
            self._panel = NotificationPanel(self)
        self._panel.toggle()
        self.client.TIMEOUTS.start(self._dialog_timeout_id)


# ── Notification panel ──────────────────────────────────────────────────────

class NotificationPanel(Panel):

    WIDTH = Panel.DEFAULT_WIDTH   #shared by every panel — see Panel.apply_frosted_style()

    def __init__(self, manager: NotificationCenterWidget):
        super().__init__(manager.client, width=self.WIDTH, edge="right")
        self.manager = manager
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setObjectName("notif_panel")
        self.apply_frosted_style()

        outer = self.content_layout
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
        scroll.viewport().setAutoFillBackground(False)
        QScroller.grabGesture(scroll.viewport(),
                               QScroller.ScrollerGestureType.LeftMouseButtonGesture)

        self._list_widget = QWidget()
        set_style(self._list_widget, "common", "transparent")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()

        scroll.setWidget(self._list_widget)
        outer.addWidget(scroll)

        self._populate()


    def toggle(self) -> None:
        self._sync_geometry()   #account for any window resize since last toggle

        self._anim.stop()

        if self.open:
            self._anim.setStartValue(self.pos())
            self._anim.setEndValue(self._hidden_pos)
            self._anim.finished.connect(self.hide)
            self._anim.finished.connect(lambda: self._anim.finished.disconnect())
            self.open = False
        else:
            self.move(self._hidden_pos)
            self.refresh_backdrop()
            self.show()
            self.raise_()
            self._populate()   #refresh contents each time it's opened
            self._anim.setStartValue(self._hidden_pos)
            self._anim.setEndValue(self._shown_pos)
            self.open = True

        self._anim.start()

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