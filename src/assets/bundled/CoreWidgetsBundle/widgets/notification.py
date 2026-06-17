from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen

from src.ui.widget import Widget
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons
from src.styling import COLORS, make_font, make_background_qss

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

        self.setStyleSheet(f"""
            QFrame {{
                background: {COLORS.DARK.BG};
                border-radius: 4px;
                border: none;
            }}
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(12)

        # Icon
        icon_lbl = QLabel(icon[:1].upper() if icon else "🔔")
        icon_lbl.setFont(make_font(20))
        icon_lbl.setStyleSheet("color: white; background: transparent;")
        icon_lbl.setFixedSize(36, 36)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_lbl)

        # Text
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.setContentsMargins(0, 0, 0, 0)

        title_row = QHBoxLayout()
        title_lbl = QLabel(title)
        title_lbl.setFont(make_font(14, bold=True))
        title_lbl.setStyleSheet(f"color: {COLORS.DARK.TEXT.IMPORTANT}; background: transparent;")

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
        time_lbl.setStyleSheet(f"color: {COLORS.DARK.TEXT.MUTED}; background: transparent;")
        time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        title_row.addWidget(title_lbl)
        title_row.addStretch()
        title_row.addWidget(time_lbl)

        body_lbl = QLabel(body[:120] + ("…" if len(body) > 120 else ""))
        body_lbl.setFont(make_font(13))
        body_lbl.setStyleSheet(f"color: {COLORS.DARK.TEXT.MUTED}; background: transparent;")
        body_lbl.setWordWrap(True)

        text_col.addLayout(title_row)
        text_col.addWidget(body_lbl)
        layout.addLayout(text_col)

        # Dismiss button
        dismiss_btn = QPushButton("✕")
        dismiss_btn.setFixedSize(24, 24)
        dismiss_btn.setStyleSheet("""
            QPushButton {
                color: rgba(255,255,255,120);
                background: transparent;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover { color: white; }
        """)
        dismiss_btn.clicked.connect(self._remove)
        layout.addWidget(dismiss_btn)

    def _remove(self) -> None:
        self._history.remove(self._timestamp)
        self.setParent(None)   # type: ignore[arg-type]


# ── Notification history ───────────────────────────────────────────────────────

class NotificationHistory:
    def __init__(self, manager: "NotificationCenterWidget"):
        self.manager = manager
        self.client  = manager.client
        self.items:  list[tuple] = []

        if not self.client.public.has("cwb_notifications"):
            self.client.public.expose("corewidgetsbundle", "cwb_notifications", self.items)
        else:
            self.items = self.client.public.cwb_notifications

    def _get_dialog(self) -> "NotificationDialog | None":
        dialog = self.client.DIALOG.get()
        if isinstance(dialog, NotificationDialog):
            return dialog
        return None

    def add(self, icon: str, title: str, body: str, timestamp: datetime = None) -> None:
        ts = timestamp or datetime.now()
        self.items.insert(0, (self, icon, title, body, ts))
        self.manager.show()
        dialog = self._get_dialog()
        if dialog:
            dialog.refresh_list()

    def remove(self, timestamp: datetime) -> None:
        self.items = [i for i in self.items if i[4] != timestamp]
        if not self.items:
            self.manager.hide()
            dialog = self._get_dialog()
            if dialog:
                self.client.DIALOG.close()

    def clear(self) -> None:
        self.items.clear()
        self.manager.hide()
        dialog = self._get_dialog()
        if dialog:
            self.client.DIALOG.close()


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
        self._dot.setStyleSheet("background: #3b82f6; border-radius: 6px;")
        self._dot.hide()

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
        dialog = self.client.DIALOG.get()
        if isinstance(dialog, NotificationDialog):
            self.client.DIALOG.close()

    def _open_history(self, event=None) -> None:
        self.client.DIALOG.open(NotificationDialog(self))
        self.client.TIMEOUTS.start(self._dialog_timeout_id)


# ── Notification dialog ───────────────────────────────────────────────────────

class NotificationDialog(QWidget):
    """
    Slide-in panel listing notification history.
    Parented and positioned by the DialogManager.
    """

    def __init__(self, manager: NotificationCenterWidget):
        super().__init__()
        self.manager = manager
        self.client  = manager.client

        margin = int(self.client.SETTINGS.home.widget_margin.value)
        win_h  = int(self.client.SETTINGS.application.window.size.value[1])

        w = 475
        h = win_h - (margin * 3) - 55

        self.setFixedSize(w, h)
        self.setStyleSheet(f"""
            QWidget {{
                background: {COLORS.DARK.BG};
                border-radius: 8px;
            }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        # Header
        header = QHBoxLayout()
        title_lbl = QLabel("Notifications")
        title_lbl.setFont(make_font(20, bold=True))
        title_lbl.setStyleSheet(
            f"color: {COLORS.DARK.TEXT.IMPORTANT}; background: transparent;"
        )

        clear_btn = QPushButton("Clear history")
        clear_btn.setFixedWidth(120)
        clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0,0,0,50);
                color: {COLORS.DARK.TEXT.MUTED};
                border: none;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 13px;
            }}
            QPushButton:hover {{ background: rgba(255,255,255,15); }}
        """)
        clear_btn.clicked.connect(self.manager.history.clear)

        header.addWidget(title_lbl)
        header.addStretch()
        header.addWidget(clear_btn)
        outer.addLayout(header)

        # Scrollable list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()

        scroll.setWidget(self._list_widget)
        outer.addWidget(scroll)

        self._populate()

        # Position: top-right, below the notification center button
        win_w  = int(self.client.SETTINGS.application.window.size.value[0])
        self.move(win_w - w - margin, (margin * 2) + 55)

    def _populate(self) -> None:
        # Clear existing items (except the stretch)
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