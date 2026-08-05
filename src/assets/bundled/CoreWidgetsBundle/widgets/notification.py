from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QSizePolicy, QScroller,
    QGraphicsOpacityEffect,
)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen

from src.ui.widget import Widget
from src.ui.overlays import Panel
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons, icon as resolve_icon
from src.styling import make_font, set_style, get_style_sheet, style_scrollbar

if TYPE_CHECKING:
    from src.main import Client


# ── Notification history item ─────────────────────────────────────────────────

class NotificationHistoryItem(QFrame):
    """
    One entry, with a cross and a swipe.

    Both, rather than one: the cross is 24px and this is a wall panel read
    from a step away, and a swipe is what a hand already expects a
    notification to answer to.
    """

    #How far sideways before it goes. About a third of the panel.
    SWIPE_DISTANCE = 150
    #And how much more sideways than up before it is a swipe rather than the
    #list being scrolled past it.
    SWIPE_BIAS = 1.4

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

        dismiss_btn = QPushButton("✕")
        dismiss_btn.setFixedSize(24, 24)
        set_style(dismiss_btn, "notification", "notification-dismiss")
        dismiss_btn.clicked.connect(self._remove)
        layout.addWidget(dismiss_btn)

    ## -- swiping it away

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._swipe_from = event.globalPosition().toPoint()
            self._swiping = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        start = getattr(self, "_swipe_from", None)
        if start is None:
            super().mouseMoveEvent(event)
            return

        delta = event.globalPosition().toPoint() - start
        if not getattr(self, "_swiping", False):
            if abs(delta.x()) < 12 \
                    or abs(delta.x()) < abs(delta.y()) * self.SWIPE_BIAS:
                super().mouseMoveEvent(event)
                return
            # Decided once, and taken off the scroller. The list is driven by
            # a QScroller on the viewport, which claims a drag the moment it
            # passes its own start distance - so a sideways drag has to be
            # claimed here first, and the scroller told to let go of the one
            # it may already have started.
            self._swiping = True
            self._stop_scroller()

        self.move(self.x() + delta.x() - getattr(self, "_swipe_shift", 0), self.y())
        self._swipe_shift = delta.x()
        # Fading as it goes, so the gesture says what it is doing before it
        # is finished doing it.
        gone = min(1.0, abs(delta.x()) / float(self.SWIPE_DISTANCE))
        self._fade(1.0 - gone * 0.8)

    def mouseReleaseEvent(self, event) -> None:
        start = getattr(self, "_swipe_from", None)
        swiping = getattr(self, "_swiping", False)
        self._swipe_from = None
        self._swiping = False

        if start is not None and swiping:
            travelled = abs((event.globalPosition().toPoint() - start).x())
            if travelled >= self.SWIPE_DISTANCE:
                self._remove()
                return
            # Not far enough. Back where it was, at full strength.
            self.move(self.x() - getattr(self, "_swipe_shift", 0), self.y())
            self._swipe_shift = 0
            self._fade(1.0)
            return
        super().mouseReleaseEvent(event)

    def _fade(self, amount: float) -> None:
        effect = self.graphicsEffect()
        if effect is None:
            effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(effect)
        try:
            effect.setOpacity(max(0.0, min(1.0, amount)))
        except Exception:
            pass

    def _stop_scroller(self) -> None:
        try:
            viewport = self.parent()
            while viewport is not None and not isinstance(viewport, QScrollArea):
                viewport = viewport.parent()
            if viewport is not None:
                QScroller.scroller(viewport.viewport()).stop()
        except Exception:
            pass

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

    def open(self) -> bool:
        """
        Open the list, if there is anything to open it on.

        Here rather than on the widget, so a caller does not have to reach
        through `history.manager` and hope. The manager is a widget on the
        home page: it can be absent, and after a page rebuild it can be a
        Python object whose C++ half has gone - which is a hard crash rather
        than an AttributeError.
        """
        if not self.is_manager_alive():
            return False

        manager = self.manager

        def show():
            try:
                manager.open_history()
            except RuntimeError:
                pass   # deleted between the check and the call

        # On the UI thread, whoever asked. A skill runs on the assistant's
        # thread, and building a panel from there gives it no parent - the
        # scrim is a sibling of the panel, so it then has nothing to size
        # itself against and the open dies with an AttributeError.
        #
        # The answer is still returned from here: whether there is a manager
        # to open it on is a question this thread can answer, and it is what
        # the caller needs to know.
        try:
            self.client.call_on_ui(show)
        except Exception:
            return False
        return True

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

        manager = self.manager

        def apply():
            try:
                manager.hide()
                panel = manager._panel
                if panel and panel.open:
                    panel.toggle()
            except RuntimeError:
                pass

        # Same reason as open(): the skill that empties this runs on the
        # assistant's thread, and hiding a widget from there is a Qt call from
        # the wrong side.
        try:
            self.client.call_on_ui(apply)
        except Exception:
            pass


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

        self._btn = IconButton(Icons.BELL, self.open_history, size=self.SIZE // 2)
        self._btn.setParent(self)
        self._btn.move(0, 0)
        self._btn.resize(self.SIZE, self.SIZE)

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

    def open_history(self, event=None) -> None:
        """
        Show the list. Public, because a skill opens it too.

        The underscore said "mine alone" and it was not - the notifications
        skill called it across a plugin boundary, where a private name is a
        promise nobody made.
        """
        if self._panel is None:
            self._panel = NotificationPanel(self)
        self._panel.toggle()
        self.client.TIMEOUTS.start(self._dialog_timeout_id)


# ── Notification panel ──────────────────────────────────────────────────────

class NotificationPanel(Panel):

    WIDTH = Panel.DEFAULT_WIDTH   #shared by every panel — see Panel.apply_frosted_style()

    def __init__(self, manager: NotificationCenterWidget):
        super().__init__(manager.client, width=self.WIDTH, edge="right",
                         dismiss_on_outside_click=True)
        self.manager = manager
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setObjectName("notif_panel")
        self.apply_frosted_style()

        outer = self.content_layout
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        #header — title and clear-history. Tapping outside closes it.
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        title_lbl = QLabel("Notifications")
        title_lbl.setFont(make_font(20, bold=True))
        set_style(title_lbl, "common", "text-strong")
        header.addWidget(title_lbl)
        header.addStretch()

        self._clear_btn = QPushButton("Clear all")
        self._clear_btn.setFixedHeight(30)
        set_style(self._clear_btn, "notification", "notification-clear")
        self._clear_btn.clicked.connect(self._clear_all)
        header.addWidget(self._clear_btn)

        outer.addLayout(header)

        #scrollable list of history items
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        set_style(scroll, "notification", "notification-scroll", object_tag="QScrollArea")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        style_scrollbar(scroll)
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


    def dismiss(self) -> None:
        """A press beside the panel. This one slides itself, so it toggles."""
        if self.open:
            self.toggle()

    def toggle(self) -> None:
        self._sync_geometry()   #account for any window resize since last toggle

        self._anim.stop()

        if self.open:
            self._release_scrim()
            self._anim.setStartValue(self.pos())
            self._anim.setEndValue(self._hidden_pos)
            self._anim.finished.connect(self.hide)
            self._anim.finished.connect(lambda: self._anim.finished.disconnect())
            self.open = False
        else:
            self.move(self._hidden_pos)
            self.refresh_backdrop()
            # This toggle does not go through open_panel(), which is where the
            # base builds the catcher that closes it on a press beside it.
            self._build_scrim()
            self.show()
            self.raise_()
            self._populate()   #refresh contents each time it's opened
            self._anim.setStartValue(self._hidden_pos)
            self._anim.setEndValue(self._shown_pos)
            self.open = True

        self._anim.start()

    def _clear_all(self) -> None:
        """
        Empty the history.

        No confirmation. A notification has already been seen by the time it
        is in here - this is the pile of them, not the thing itself - and a
        dialog in front of "clear the list I have already read" is a tap for
        nothing. Anything that still matters is somewhere other than a
        notification.
        """
        self.manager.history.clear()
        self._populate()

    def _populate(self) -> None:
        self._clear_btn.setVisible(bool(self.manager.history.items))
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