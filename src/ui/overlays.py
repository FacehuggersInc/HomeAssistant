from __future__ import annotations
from typing import TYPE_CHECKING, Literal, Optional

from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QSizePolicy
from PyQt6.QtCore import (
    Qt, QEvent, QTimer, QPropertyAnimation, QEasingCurve,
    QPoint, QRect, pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen, QRegion

from src.styling import set_style

if TYPE_CHECKING:
    from src.main import Client

POSITIONS = Literal[
    "top-left", "top-center", "top-right",
    "bottom-left", "bottom-center", "bottom-right",
    "right-center", "left-center",
]

LAYERS = Literal["BACKGROUND", "FOREGROUND", "SYSTEM", "TOPMOST"]

_LAYER_Z = {
    "BACKGROUND": 0,
    "FOREGROUND":  1,
    "SYSTEM":      2,
    "TOPMOST":     3,
}


# ── Overlay Manager ───────────────────────────────────────────────────────────

class OverlayManager(QWidget):
    """
    Full-window transparent widget that floats above all page content.
    Divided into four conceptual layers (BACKGROUND → TOPMOST) enforced
    via raise_()/lower_() rather than separate containers — this avoids
    the nested-Stack approach from the Flet version which required explicit
    update() calls everywhere.

    All overlaid controls are direct children of this widget and are
    positioned absolutely.
    """

    def __init__(self, client: "Client"):
        super().__init__(client.window if hasattr(client, "window") else None)
        self.client = client

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        set_style(self, "common", "transparent")

        # Each layer is a list of QWidget references for tracking
        self._layers: dict[str, list[QWidget]] = {
            "BACKGROUND": [],
            "FOREGROUND":  [],
            "SYSTEM":      [],
            "TOPMOST":     [],
        }

        # ── Mouse passthrough via mask, instead of WA_TransparentForMouseEvents ──
        # NOTE: This widget intentionally never sets WA_TransparentForMouseEvents
        # on itself. That attribute is checked by Qt's hit-testing *before* it
        # even looks at children — so setting it on a full-window container
        # makes every child inside it (dialogs, popups, the keyboard, etc.)
        # unreachable for clicks too, not just the empty background. Toggling
        # it back off to let one dialog receive clicks then makes the *entire*
        # window-sized widget solid, swallowing clicks everywhere else.
        #
        # Instead we keep this widget normal (clickable) and continuously
        # shrink its effective mouse/paint shape down to just the union of its
        # visible children's rectangles via setMask(). Anywhere outside that
        # union, clicks fall straight through to whatever is beneath the
        # window (e.g. the page). Anywhere inside it, the relevant overlay
        # widget gets normal mouse handling. An empty mask (no children) means
        # the whole manager is fully transparent to input, which is the
        # original intent of the old comment below.
        self._mask_timer = QTimer(self)
        self._mask_timer.setSingleShot(True)
        self._mask_timer.setInterval(0)
        self._mask_timer.timeout.connect(self._recompute_mask)
        self._recompute_mask()  # Empty mask — no children yet, all clicks pass through

    # ── Layer API ─────────────────────────────────────────────────────────────

    def add(self, layer: LAYERS, widget: QWidget, update: bool = False) -> None:
        """Add a widget to a layer. The widget becomes a child of this manager."""
        if widget not in self._layers[layer]:
            widget.setParent(self)
            self._layers[layer].append(widget)
            self._enforce_z_order()
            widget.show()
            self._schedule_mask_update()

    def insert(self, layer: LAYERS, widget: QWidget,
               index: int = -1, update: bool = False) -> None:
        """Insert a widget at a specific position in the layer list."""
        if widget not in self._layers[layer]:
            widget.setParent(self)
            if index < 0 or index >= len(self._layers[layer]):
                self._layers[layer].append(widget)
            else:
                self._layers[layer].insert(index, widget)
            self._enforce_z_order()
            widget.show()
            self._schedule_mask_update()

    def remove(self, layer: LAYERS, widget: QWidget, update: bool = False) -> None:
        """Remove a widget from a layer."""
        if widget in self._layers[layer]:
            self._layers[layer].remove(widget)
            widget.setParent(None)   # type: ignore[arg-type]
            self._schedule_mask_update()

    def get_layer(self, layer: LAYERS) -> list[QWidget]:
        return self._layers[layer]

    # ── Z-order enforcement ───────────────────────────────────────────────────

    def _enforce_z_order(self) -> None:
        """Re-stack all children so that higher layers are raised above lower ones."""
        for layer_name in ("BACKGROUND", "FOREGROUND", "SYSTEM", "TOPMOST"):
            for widget in self._layers[layer_name]:
                widget.raise_()

    # ── Geometry ──────────────────────────────────────────────────────────────

    def update_geometry(self, w: int, h: int) -> None:
        self.setGeometry(0, 0, w, h)
        self._schedule_mask_update()

    # ── Mask maintenance ──────────────────────────────────────────────────────
    # Any QWidget that becomes a direct child of this manager is tracked
    # automatically — whether it arrived via add()/insert() or was simply
    # parented straight to this widget elsewhere in the codebase (e.g. the
    # on-screen keyboard popup). We don't need callers to opt in for the
    # passthrough behaviour to work for them.

    def childEvent(self, event) -> None:  # type: ignore[override]
        super().childEvent(event)
        child = event.child()
        if isinstance(child, QWidget):
            if event.type() == QEvent.Type.ChildAdded:
                child.installEventFilter(self)
                self._schedule_mask_update()
            elif event.type() == QEvent.Type.ChildRemoved:
                self._schedule_mask_update()

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if event.type() in (
            QEvent.Type.Move, QEvent.Type.Resize,
            QEvent.Type.Show, QEvent.Type.Hide,
        ):
            self._schedule_mask_update()
        return super().eventFilter(obj, event)

    def _schedule_mask_update(self) -> None:
        """Coalesce bursts of move/resize events (e.g. slide animations,
        ticking every frame) into a single mask recompute per event-loop tick."""
        if not self._mask_timer.isActive():
            self._mask_timer.start()

    def _recompute_mask(self) -> None:
        """Shrink this widget's clickable/paintable shape down to exactly the
        union of its visible direct children. Everything outside that region
        passes mouse events straight through to whatever is beneath it."""
        region = QRegion()
        for child in self.findChildren(
            QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly
        ):
            if child.isVisible():
                region += QRegion(child.geometry())

        if region.isEmpty():
            # IMPORTANT: Qt treats setMask(QRegion()) as identical to
            # clearMask() — it does NOT mean "this widget is invisible to
            # input everywhere," it means "remove the custom mask," which
            # restores the widget's default *solid, full-rectangle* shape.
            # Since OVERLAYS is raised above page content (PAGE/page_host —
            # WidgetFramework, TileGrid, sub-page swipe navigation, etc.)
            # and is window-sized, that default shape would silently start
            # swallowing every click/drag across the whole app the instant
            # no overlay widget happens to be active — which is most of the
            # time. To get a genuinely empty (fully click-through) mask
            # instead, we use a 1x1 region positioned entirely outside our
            # own bounds: it has nonzero area, so Qt doesn't collapse it
            # into "no mask," but it doesn't overlap any of our real
            # on-screen area either, so the net effect is "nothing here."
            region = QRegion(-1, -1, 1, 1)

        self.setMask(region)


# ── Overlayed notification widget ─────────────────────────────────────────────

class OverlayedWidget(QWidget):
    """
    A floating notification card that slides in from off-screen.
    Parented to client.OVERLAYS (a top-level Tool window).

    Accepts either a pre-built content QWidget OR raw data keys
    (icon, title, body) and builds its own content widget.
    """

    dismissed = pyqtSignal()

    def __init__(
        self,
        client:          "Client",
        content:         QWidget = None,
        icon:            str     = "",
        title:           str     = "",
        body:            str     = "",
        bgcolor:         str     = "#1e1e1e",
        width:           int     = 475,
        height:          int     = 110,
        border_radius:   int     = 8,
        animation_speed: int     = 180,
        padding:         int     = 15,
        duration:        int     = None,
        anchor:          POSITIONS = "top-center",
        **_kwargs,
    ):
        # Parent to overlay manager — now a top-level window so parenting works fine
        super().__init__(client.OVERLAYS)
        self.client = client

        self.setFixedSize(width, height)
        self._border_radius = border_radius
        self._bgcolor       = QColor(bgcolor)

        self.pushed    = False
        self.pushing   = False
        self.animating = False
        self.decided   = False

        # Build content
        outer = QVBoxLayout(self)
        outer.setContentsMargins(padding, 6, padding, 6)
        outer.setSpacing(0)
        outer.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        if content:
            # Caller provided a pre-built widget
            outer.addWidget(content)
        elif title or body:
            # Build a simple icon + title + body layout
            row = QHBoxLayout()
            row.setSpacing(8)
            row.setContentsMargins(0, 0, 0, 0)

            if icon:
                from PyQt6.QtWidgets import QLabel as _QL
                from PyQt6.QtGui import QPixmap as _QP
                icon_lbl = _QL()
                icon_lbl.setFixedSize(32, 32)
                icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                set_style(icon_lbl, "common", "transparent")
                # Try to resolve as a registered icon name or mdi.* name
                try:
                    from src.ui.icons import icon as _resolve_icon, resolve as _resolve_name
                    if _resolve_name(icon):
                        q_icon = _resolve_icon(icon, color="white")
                        pixmap = q_icon.pixmap(32, 32)
                        icon_lbl.setPixmap(pixmap)
                    else:
                        # Plain text/emoji fallback
                        icon_lbl.setText(icon[:2])
                        set_style(icon_lbl, "overlays", "toast-icon-fallback")
                except Exception:
                    icon_lbl.setText("🔔")
                    set_style(icon_lbl, "overlays", "toast-icon-fallback")
                row.addWidget(icon_lbl)

            text_col = QVBoxLayout()
            text_col.setSpacing(0)
            text_col.setContentsMargins(0, 0, 0, 0)
            text_col.setAlignment(Qt.AlignmentFlag.AlignVCenter)

            if title:
                title_lbl = QLabel(title)
                set_style(title_lbl, "overlays", "toast-title")
                title_lbl.setWordWrap(False)
                title_lbl.setContentsMargins(0, 0, 0, 0)
                text_col.addWidget(title_lbl)

            if body:
                body_text = body if len(body) <= 90 else body[:87] + "..."
                body_lbl  = QLabel(body_text)
                set_style(body_lbl, "overlays", "toast-body")
                body_lbl.setWordWrap(True)
                body_lbl.setContentsMargins(0, 0, 0, 0)
                text_col.addWidget(body_lbl)

            row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            row.addLayout(text_col)
            outer.addLayout(row)

        # Animation
        self._anim = QPropertyAnimation(self, b"pos")
        self._anim.setDuration(animation_speed)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._hidden_pos, self._shown_pos = self._compute_positions(anchor)
        self.move(self._hidden_pos)
        self.hide()

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(self._bgcolor))
        painter.setPen(QPen(Qt.GlobalColor.transparent))
        painter.drawRoundedRect(self.rect(), self._border_radius, self._border_radius)

    # ── Animation ─────────────────────────────────────────────────────────────

    def push(self) -> None:
        """Animate from hidden position to shown position."""
        if not self.pushing:
            self.pushing = True
            self.show()
            self._anim.stop()
            self._anim.setStartValue(self._hidden_pos)
            self._anim.setEndValue(self._shown_pos)
            self._anim.setEasingCurve(QEasingCurve.Type.OutQuad)
            self._anim.start()
            self.pushed = True

    def dismiss(self) -> None:
        """Animate back to the hidden position then hide."""
        if not self.animating:
            self.animating = True
            self._anim.stop()
            self._anim.setStartValue(self.pos())
            self._anim.setEndValue(self._hidden_pos)
            self._anim.setEasingCurve(QEasingCurve.Type.InQuad)
            self._anim.finished.connect(self._on_dismiss_done)
            self._anim.start()

    def _on_dismiss_done(self) -> None:
        self.hide()
        # Keep animating=True so the update loop detects completion via isVisible()
        self.dismissed.emit()

    # ── Position helpers ──────────────────────────────────────────────────────

    def _compute_positions(self, anchor: str) -> tuple[QPoint, QPoint]:
        """Return (hidden_pos, shown_pos) for the given anchor string."""
        margin = 20

        # Always use the overlay manager's live size
        overlay = self.client.OVERLAYS
        win_w = overlay.width()
        win_h = overlay.height()

        # Hard fallback
        if win_w <= 0: win_w = 1920
        if win_h <= 0: win_h = 1080

        w, h = self.width(), self.height()


        match anchor:
            case "top-left":
                shown  = QPoint(margin, margin)
                hidden = QPoint(margin, margin - h - 10)
            case "top-center":
                shown  = QPoint((win_w - w) // 2, margin)
                hidden = QPoint((win_w - w) // 2, margin - h - 10)
            case "top-right":
                shown  = QPoint(win_w - w - margin, margin)
                hidden = QPoint(win_w - w - margin, margin - h - 10)
            case "bottom-left":
                shown  = QPoint(margin, win_h - h - margin)
                hidden = QPoint(margin, win_h + 10)
            case "bottom-center":
                shown  = QPoint((win_w - w) // 2, win_h - h - margin)
                hidden = QPoint((win_w - w) // 2, win_h + 10)
            case "bottom-right":
                shown  = QPoint(win_w - w - margin, win_h - h - margin)
                hidden = QPoint(win_w - w - margin, win_h + 10)
            case "right-center":
                shown  = QPoint(win_w - w - margin, (win_h - h) // 2)
                hidden = QPoint(win_w - w - margin, margin - h - 10)
            case "left-center":
                shown  = QPoint(margin, (win_h - h) // 2)
                hidden = QPoint(margin, margin - h - 10)
            case _:
                shown  = QPoint((win_w - w) // 2, margin)
                hidden = QPoint((win_w - w) // 2, margin - h - 10)

        return hidden, shown


# ── Notification Manager ──────────────────────────────────────────────────────

class NotificationManager:
    """
    Queue-based notification display.
    update() is called by the client's main thread via call_on_ui.
    """

    def __init__(self, client: "Client",
                 notification_duration: float,
                 delay_between_notifications: float):
        self.client = client
        self.pushing               = False
        self.current_notification: Optional[OverlayedWidget] = None
        self.notifications_queue:  list[dict] = []
        self.notification_duration = notification_duration
        self.delay_between         = delay_between_notifications
        self.notify_timeout        = 0.0
        self.notify_kill_time      = 0.0
        import time
        self._initial_delay = time.time() + 60  # effectively disabled until reset_initial_delay()

    def reset_initial_delay(self, seconds: float = 1.0) -> None:
        import time
        self._initial_delay = time.time() + seconds

    def add_to_queue(self, args: dict) -> None:
        if args not in self.notifications_queue:
            self.notifications_queue.append(args)

    def update(self) -> None:
        import time
        if time.time() < self._initial_delay:
            return

        if self.pushing and self.current_notification:
            n = self.current_notification

            if (time.time() >= self.notify_timeout or n.decided) and not n.animating:
                n.dismiss()

            if n.animating and not n.isVisible():
                self.client.OVERLAYS.remove("SYSTEM", n)
                self.current_notification = None
                self.pushing = False
                self.notify_kill_time = time.time() + self.delay_between

        if self.pushing or time.time() < self.notify_kill_time:
            return

        if (not self.current_notification
                and not self.client.is_switching_page()
                and self.notifications_queue):
            data: dict = self.notifications_queue.pop(0)
            duration = data.get("duration", self.notification_duration)
            self.notify_timeout = time.time() + duration

            notify = OverlayedWidget(self.client, **data)
            self.current_notification = notify
            self.pushing = True
            self.client.OVERLAYS.add("SYSTEM", notify)
            notify.push()

# ── Dialog Manager ────────────────────────────────────────────────────────────

class DialogManager:
    """
    Manages a stack of modal dialog widgets displayed in the SYSTEM overlay
    layer, identical in API to the original DialogManager.

    A semi-transparent blocker widget is shown beneath open dialogs.
    """

    def __init__(self, client: "Client"):
        self.client         = client
        self.dialog_stack:  list[QWidget] = []

        # Blocker — transparent dark overlay that catches clicks outside dialogs
        self.blocker = _ClickBlocker(client)
        self.blocker.clicked.connect(self.close)
        self.client.OVERLAYS.add("SYSTEM", self.blocker)
        self.blocker.hide()

    # ── Public API ────────────────────────────────────────────────────────────

    def open(self, dialog: QWidget) -> None:
        """Push a dialog onto the stack and make it visible."""
        if self.dialog_stack:
            self.dialog_stack[-1].hide()

        dialog.setParent(self.client.OVERLAYS)
        dialog.show()
        dialog.raise_()
        self.dialog_stack.append(dialog)

        self.blocker.update_geometry()
        self.blocker.show()
        self.blocker.raise_()
        dialog.raise_()  # dialog above blocker
        # No flag to flip here anymore — OVERLAYS' mask automatically grows
        # to cover the blocker's (full-screen) geometry the moment it's
        # shown, via the eventFilter/childEvent hooks in OverlayManager.

    def get(self) -> Optional[QWidget]:
        return self.dialog_stack[-1] if self.dialog_stack else None

    def close(self, event=None) -> None:
        """Pop the top dialog off the stack."""
        if not self.dialog_stack:
            return
        top = self.dialog_stack.pop()
        top.hide()
        top.setParent(None)  # type: ignore[arg-type]

        if self.dialog_stack:
            self.dialog_stack[-1].show()
            self.dialog_stack[-1].raise_()
        else:
            self.blocker.hide()
            # No flag to re-enable here either — hiding the blocker fires a
            # Hide event the OVERLAYS mask watches for, so it shrinks back
            # down on its own and clicks pass through again immediately.


class _ClickBlocker(QWidget):
    """Semi-transparent overlay that emits `clicked` when tapped."""

    clicked = pyqtSignal()

    def __init__(self, client: "Client"):
        super().__init__()
        self.client = client
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.update_geometry()

    def update_geometry(self) -> None:
        try:
            w = int(self.client.SETTINGS.application.window.size.value[0])
            h = int(self.client.SETTINGS.application.window.size.value[1])
        except Exception:
            w, h = 800, 480
        self.setGeometry(0, 0, w, h)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 26))  # ~10 % black

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.clicked.emit()


# ── Panel (generic full-height side panel) ────────────────────────────────────

class Panel(QWidget):
    """
    Blank base class for full-height, fixed-width slide-in side panels —
    e.g. a tile drawer or a notification history list. Subclasses (or
    callers, via add_content()) drop their own content into
    self.content_layout; this class only owns geometry, edge-anchoring,
    slide animation, and parenting.

    Lives directly on the overlay system (client.OVERLAYS) rather than a
    page or the bare window, which gets you two things for free:

      - It keeps working across page navigation, since it isn't a child
        of whatever page happened to create it.
      - OverlayManager's mask (see _recompute_mask above) means this
        panel only blocks mouse input over its own rectangle. No
        DialogManager blocker needed — everything else on screen keeps
        working while the panel is open, unless you deliberately want a
        modal panel, in which case open a _ClickBlocker-style widget of
        your own alongside it.

    IMPORTANT — parent first, geometry second, never reparent after:
    this widget is parented to client.OVERLAYS in __init__, before any
    geometry is set, and is never setParent()'d again afterwards.
    Reparenting a QWidget resets its geometry relative to the new
    parent — that was a real bug in an earlier version of
    NotificationPanel (src/assets/bundled/CoreWidgetsBundle/widgets/
    notification.py) when it went through DialogManager.open(), which
    reparents onto the overlay manager *after* the dialog's position was
    already set. Keep that lesson in mind if you ever change how this
    class is parented.
    """

    def __init__(
        self,
        client:          "Client",
        width:           int  = 320,
        edge:            str  = "right",   # "right" or "left"
        bgcolor:         str  = "#1e1e1e",
        animation_speed: int  = 220,
        key:             str  = None,
    ):
        super().__init__(client.OVERLAYS)
        self.client       = client
        self.key          = key
        self.edge         = edge
        self.panel_width  = width
        self._bgcolor     = QColor(bgcolor)
        self.open         = False
        self._closing     = False

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        # Blank by design — subclasses/callers add real content here via
        # add_content(), the same way OverlayedWidget's outer layout works.
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)

        self._anim = QPropertyAnimation(self, b"pos")
        self._anim.setDuration(animation_speed)

        # Stay sized to the window even if it resizes later — OVERLAYS
        # itself is kept in sync with the window by Client.on_window_resized,
        # so watching it here is enough to stay correct without polling.
        self.client.OVERLAYS.installEventFilter(self)

        self._hidden_pos = QPoint(0, 0)
        self._shown_pos  = QPoint(0, 0)
        self._sync_geometry()
        self.hide()

    # ── Content ───────────────────────────────────────────────────────────────

    def add_content(self, widget: QWidget) -> None:
        """Drop a content widget into the panel."""
        self.content_layout.addWidget(widget)

    def clear_content(self) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._bgcolor)

    # ── Geometry ──────────────────────────────────────────────────────────────

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if obj is self.client.OVERLAYS and event.type() == QEvent.Type.Resize:
            self._sync_geometry()
        return super().eventFilter(obj, event)

    def _sync_geometry(self) -> None:
        """Recompute full-height size and the hidden/shown edge positions
        from OVERLAYS' current size. Safe to call any time — keeps a
        closed panel's off-screen position correct too, not just an open
        one, so the next open_panel() slides in from the right place."""
        ov_w = self.client.OVERLAYS.width()
        ov_h = self.client.OVERLAYS.height()
        self.setFixedSize(self.panel_width, ov_h)

        if self.edge == "left":
            self._hidden_pos = QPoint(-self.panel_width, 0)
            self._shown_pos  = QPoint(0, 0)
        else:  # "right" (default)
            self._hidden_pos = QPoint(ov_w, 0)
            self._shown_pos  = QPoint(ov_w - self.panel_width, 0)

        if self.open:
            self.move(self._shown_pos)
        elif not self._closing:
            self.move(self._hidden_pos)

    # ── Show / hide ───────────────────────────────────────────────────────────

    def toggle(self) -> None:
        self.close_panel() if self.open else self.open_panel()

    def open_panel(self) -> None:
        if self.open:
            return
        self._closing = False
        self._sync_geometry()
        self.move(self._hidden_pos)
        self.show()
        self.raise_()

        self._anim.stop()
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(self._hidden_pos)
        self._anim.setEndValue(self._shown_pos)
        self._anim.start()
        self.open = True

    def close_panel(self) -> None:
        if not self.open:
            return
        self.open     = False
        self._closing = True

        self._anim.stop()
        self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(self._hidden_pos)
        self._anim.finished.connect(self._on_closed)
        self._anim.start()

    def _on_closed(self) -> None:
        try:
            self._anim.finished.disconnect(self._on_closed)
        except TypeError:
            pass
        self.hide()
        self._closing = False