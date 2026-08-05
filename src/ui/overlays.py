from __future__ import annotations
from typing import TYPE_CHECKING, Literal, Optional

from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QSizePolicy,
    QStyleOption, QStyle, QGraphicsScene, QGraphicsPixmapItem, QGraphicsBlurEffect,
    QFrame, QPushButton, QScrollArea,
)
from PyQt6.QtCore import (
    Qt, QEvent, QTimer, QPropertyAnimation, QEasingCurve,
    QPoint, QRect, QRectF, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QPainter, QBrush, QPen, QRegion, QPixmap, QPainterPath,
)

from src.styling import set_style, make_font, SIZES, get_style_sheet, style_scrollbar

if TYPE_CHECKING:
    from src.main import Client

POSITIONS = Literal[
    "top-left", "top-center", "top-right",
    "bottom-left", "bottom-center", "bottom-right",
    "right-center", "left-center",
]

LAYERS = Literal["BACKGROUND", "FOREGROUND", "SYSTEM", "TOPMOST", "DIALOG"]

_LAYER_Z = {
    "BACKGROUND": 0,
    "FOREGROUND":  1,
    "SYSTEM":      2,
    "TOPMOST":     3,
    "DIALOG":      4,
}


# ── Overlay Manager ───────────────────────────────────────────────────────────

def _blur_pixmap(snapshot: QPixmap, radius: float,
                 scale: int = 3) -> Optional[QPixmap]:
    """
    Blur a snapshot, doing the work at a fraction of the size.

    A gaussian blur costs roughly its pixel count, and a full-width panel on a
    1080p screen is around 640,000 pixels - paid on every open, on the UI
    thread, before the panel can appear. Blurring a third-size copy and
    scaling it back is about nine times less work, and the result is a blur
    either way: the detail being thrown away is exactly the detail the blur
    exists to destroy.

    The radius is scaled with it, or the small version comes back sharper.
    """
    if snapshot.isNull():
        return None

    full = snapshot.size()
    working = snapshot
    factor = 1
    if scale > 1 and full.width() > scale * 8 and full.height() > scale * 8:
        factor = scale
        working = snapshot.scaled(
            max(1, full.width() // factor), max(1, full.height() // factor),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)

    scene = QGraphicsScene()
    item  = QGraphicsPixmapItem(working)
    blur  = QGraphicsBlurEffect()
    blur.setBlurRadius(max(1.0, float(radius) / factor))
    item.setGraphicsEffect(blur)
    scene.addItem(item)

    blurred = QPixmap(working.size())
    blurred.fill(Qt.GlobalColor.transparent)
    painter = QPainter(blurred)
    scene.render(painter, QRectF(blurred.rect()), QRectF(working.rect()))
    painter.end()

    if factor > 1:
        blurred = blurred.scaled(
            full, Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
    return blurred


class OverlayManager(QWidget):

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
            # Modals and their click blocker. Above TOPMOST so a notification
            # toast or the voice bar cannot end up covering a dialog.
            "DIALOG":      [],
        }

        self._mask_timer = QTimer(self)
        self._mask_timer.setSingleShot(True)
        self._mask_timer.setInterval(0)
        self._mask_timer.timeout.connect(self._recompute_mask)

        # Held while a panel is animating - see hold_mask().
        self._mask_holds = 0
        self._mask_sweep = None      # ground an animation will cover
        self._mask_watchdog = QTimer(self)
        self._mask_watchdog.setSingleShot(True)
        self._mask_watchdog.timeout.connect(self._mask_hold_expired)

        # Decoration layer, for children that must never take a click.
        #
        # The mask below is what lets a click land on the page underneath, and
        # a QWidget mask clips PAINTING as well as input - so a child kept out
        # of the mask to stay click-through stops being drawn at all. Keeping
        # WA_TransparentForMouseEvents children out of the mask fixed a dead
        # zone over the page and made the voice bar invisible in the same
        # stroke.
        #
        # They get their own host instead: a sibling of this widget that is
        # itself WA_TransparentForMouseEvents, which Qt skips entirely during
        # hit testing, so clicks reach whatever is below with no mask involved.
        # It is kept below this widget in z, so a dialog or panel still covers
        # it and the DIALOG-above-TOPMOST ordering survives. It is masked too,
        # but to where its children CAN paint rather than to input - an
        # unmasked full-window sibling that never clears its own background
        # smears the page behind it.
        self.passthrough = QWidget(self.parentWidget())
        self.passthrough.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.passthrough.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        set_style(self.passthrough, "common", "transparent")
        self.passthrough.setGeometry(self.geometry())
        if self.parentWidget() is not None:
            # Parentless only under a headless test harness, where showing it
            # would pop a stray top-level window.
            self.passthrough.show()

        # Above everything, always. See set_topmost().
        self.topmost_widget = None

        self._recompute_mask()  # Empty mask — no children yet, all clicks pass through

    # ── Layer API ─────────────────────────────────────────────────────────────

    def set_topmost(self, widget: QWidget) -> None:
        """
        One widget that sits over everything, panels and dialogs included.

        For the dimmer, and nothing else so far. A screen dimmed by painting
        over it is dimmed only as far up the stack as the paint reaches, and
        the overlay layers sit above the passthrough host - so a panel opened
        at 3am came up at full strength over a dark room, and looked like the
        dimming had failed rather than been drawn under.

        Given the overlay's own parent rather than a layer inside it: the
        layers are raised beneath this widget's parent, so no amount of
        raising within one can climb past them.

        It has to be WA_TransparentForMouseEvents, or a wash over the screen
        is a screen nobody can touch.
        """
        self.topmost_widget = widget
        if widget is None:
            return
        if not widget.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents):
            widget.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        host = self.parentWidget()
        if host is not None:
            widget.setParent(host)
            widget.setGeometry(self.geometry())
        self._enforce_z_order()

    def _host_for(self, widget: QWidget) -> QWidget:
        """Where a widget should actually live: masked layer, or passthrough."""
        if widget.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents):
            return self.passthrough
        return self

    def add(self, layer: LAYERS, widget: QWidget, update: bool = False) -> None:
        if widget not in self._layers[layer]:
            host = self._host_for(widget)
            widget.setParent(host)
            if host is self.passthrough:
                # childEvent() only sees this widget's own children, so the
                # move/resize/show tracking that keeps the mask current has to
                # be attached here instead.
                widget.installEventFilter(self)
            self._layers[layer].append(widget)
            self._enforce_z_order()
            widget.show()
            self._schedule_mask_update()

    def insert(self, layer: LAYERS, widget: QWidget,
               index: int = -1, update: bool = False) -> None:
        if widget not in self._layers[layer]:
            host = self._host_for(widget)
            widget.setParent(host)
            if host is self.passthrough:
                widget.installEventFilter(self)
            if index < 0 or index >= len(self._layers[layer]):
                self._layers[layer].append(widget)
            else:
                self._layers[layer].insert(index, widget)
            self._enforce_z_order()
            widget.show()
            self._schedule_mask_update()

    def remove(self, layer: LAYERS, widget: QWidget, update: bool = False) -> None:
        if widget in self._layers[layer]:
            self._layers[layer].remove(widget)
            widget.removeEventFilter(self)
            widget.setParent(None)   # type: ignore[arg-type]
            self._schedule_mask_update()

    def get_layer(self, layer: LAYERS) -> list[QWidget]:
        return self._layers[layer]

    # ── Z-order enforcement ───────────────────────────────────────────────────

    def _enforce_z_order(self) -> None:
        # page_host < passthrough < OVERLAYS. build() reparents page_host,
        # which puts it back on top of everything created before it, so the
        # passthrough host has to reassert itself rather than being raised
        # once at construction.
        self.passthrough.raise_()
        self.raise_()
        for layer_name in ("BACKGROUND", "FOREGROUND", "SYSTEM", "TOPMOST", "DIALOG"):
            for widget in self._layers[layer_name]:
                widget.raise_()

        # Last, so it covers the layers as well as the page.
        if self.topmost_widget is not None:
            try:
                self.topmost_widget.raise_()
            except RuntimeError:
                self.topmost_widget = None

    # ── Geometry ──────────────────────────────────────────────────────────────

    def update_geometry(self, w: int, h: int) -> None:
        self.setGeometry(0, 0, w, h)
        self._enforce_z_order()
        self._schedule_mask_update()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        # build() and on_window_resized() both call setGeometry() straight on
        # this widget, so the passthrough host is synced from the resize
        # rather than from update_geometry().
        self.passthrough.setGeometry(self.geometry())
        if self.topmost_widget is not None:
            try:
                self.topmost_widget.setGeometry(self.geometry())
            except RuntimeError:
                self.topmost_widget = None
        self._schedule_mask_update()

    def moveEvent(self, event) -> None:  # type: ignore[override]
        super().moveEvent(event)
        self.passthrough.setGeometry(self.geometry())

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
        if self._mask_holds:
            return
        if not self._mask_timer.isActive():
            self._mask_timer.start()

    def hold_mask(self, sweep: QRect = None, timeout_ms: int = 1500) -> None:
        """
        Stop recomputing the hit mask until release_mask().

        A sliding panel emits a Move event every frame, and each one used to
        schedule a full recompute: findChildren over the overlay, a QRegion
        union, and setMask on a full-screen widget - which forces everything
        composited above the page to repaint. Thirteen times across a 220ms
        slide, while the page underneath is also repainting.

        `sweep` is the ground the animation will cover, and it is not optional
        in practice. **A QWidget mask clips painting as well as input**, so
        freezing the mask at the panel's starting position means the panel is
        masked out of every frame it moves through - it slides in drawing
        nothing and only appears once the mask catches up at the end. The
        swept rect is added to the mask before it is held, so the whole path
        is paintable for the length of the animation.

        Held with a watchdog rather than a bare flag: a hold whose release is
        lost - an animation interrupted, an exception on the way out - would
        otherwise freeze both painting and hit testing for the life of the
        process.
        """
        if sweep is not None and not sweep.isEmpty():
            self._mask_sweep = (self._mask_sweep.united(sweep)
                                if self._mask_sweep is not None else QRect(sweep))
        # Recomputed once, now, so the sweep is actually in the mask before
        # updates stop.
        self._recompute_mask()
        self._mask_holds += 1
        self._mask_watchdog.start(max(200, int(timeout_ms)))

    def release_mask(self) -> None:
        if self._mask_holds > 0:
            self._mask_holds -= 1
        if not self._mask_holds:
            self._mask_watchdog.stop()
            self._mask_sweep = None
            self._recompute_mask()

    def _mask_hold_expired(self) -> None:
        self.client.log("warning",
                        "[Overlays] Mask hold expired without a release - "
                        "recomputing anyway.")
        self._mask_holds = 0
        self._mask_sweep = None
        self._recompute_mask()

    def _recompute_mask(self) -> None:
        region = QRegion()
        for child in self.findChildren(
            QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly
        ):
            if not child.isVisible():
                continue
            # A child that does not want mouse events must not claim its area
            # for the overlay either. The mask decides where OVERLAYS accepts
            # clicks at all, so including a WA_TransparentForMouseEvents child
            # created a dead zone: the overlay took the click, childAt() found
            # nothing willing to handle it, and it never reached the page
            # underneath. Those children live on the passthrough host instead,
            # which is where they are still drawn - see __init__.
            if child.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents):
                continue
            region += QRegion(child.geometry())

        # The ground a running animation covers. A mask clips painting, so
        # without this a panel sliding in is masked out of every frame it
        # moves through and only appears once it stops.
        if self._mask_sweep is not None and not self._mask_sweep.isEmpty():
            region += QRegion(self._mask_sweep)

        self.setMask(self._safe_region(region))
        self._recompute_passthrough_mask()

    def _recompute_passthrough_mask(self) -> None:
        # Masked to where its children CAN paint - geometry, not visible
        # content. Masking any tighter than that is how a widget ends up
        # sliding open and drawing nothing.
        region = QRegion()
        for child in self.passthrough.findChildren(
            QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly
        ):
            if child.isVisible():
                region += QRegion(child.geometry())
        self.passthrough.setMask(self._safe_region(region))

    @staticmethod
    def _safe_region(region: QRegion) -> QRegion:
        # Qt reads an empty QRegion as clearMask() -> full solid rect, which would
        # swallow every click in the app. Use a 1x1 region outside our bounds.
        if region.isEmpty():
            return QRegion(-1, -1, 1, 1)
        return region


# ── Overlayed notification widget ─────────────────────────────────────────────

class OverlayedWidget(QWidget):

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
        # The third argument is the PARENT. Without it the animation belongs
        # to nothing, outlives the widget it animates, and fires `finished`
        # into an object that has gone - which inside a Qt signal aborts the
        # process rather than raising.
        self._anim = QPropertyAnimation(self, b"pos", self)
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
                # Explicit, rather than left to refcount. A toast connects its
                # own signals to its own bound methods, which is a cycle - so
                # dropping the last reference does not free it and it waited
                # on the hourly gc.collect() instead.
                n.deleteLater()
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

    def __init__(self, client: "Client"):
        self.client         = client
        self.dialog_stack:  list[QWidget] = []

        # Blocker — transparent dark overlay that catches clicks outside dialogs
        self.blocker = _ClickBlocker(client)
        self.blocker.clicked.connect(self.close)
        self.client.OVERLAYS.add("DIALOG", self.blocker)
        self.blocker.hide()

    # ── Public API ────────────────────────────────────────────────────────────

    def open(self, dialog: QWidget) -> None:
        # Only the first one.
        #
        # A dialog opening from inside another - a picker over a form - is one
        # interaction continuing, not a second thing arriving. Two chimes in a
        # row for one tap is worse than none.
        if not self.dialog_stack:
            try:
                self.client.AUDIO.play("dialog")
            except Exception:
                pass

        if self.dialog_stack:
            self.dialog_stack[-1].hide()

        # Registered in the DIALOG layer, not merely reparented. A widget that
        # is only a child of OVERLAYS is invisible to _enforce_z_order(), so
        # anything added to a layer afterwards - a notification toast, the
        # voice bar - got raised above it, covering the dialog and handing
        # taps to the click blocker underneath, which closed it.
        self.client.OVERLAYS.add("DIALOG", dialog)
        dialog.show()
        dialog.raise_()
        self.dialog_stack.append(dialog)

        if hasattr(dialog, "center"):
            try:
                dialog.center()
            except Exception:
                pass

        self.blocker.update_geometry()
        self.blocker.show()
        self.blocker.raise_()
        dialog.raise_()  # dialog above blocker

    def get(self) -> Optional[QWidget]:
        return self.dialog_stack[-1] if self.dialog_stack else None

    def close(self, event=None) -> None:
        if not self.dialog_stack:
            return

        # Asked here rather than in the dialog's own close(), because this is
        # the one path everything funnels through - the Done button, the click
        # blocker behind the dialog, and any plugin calling client.dialog
        # close. A guard on the widget only covers the first of those.
        top = self.dialog_stack[-1]
        veto = getattr(top, "can_close", None)
        if callable(veto):
            try:
                if not veto():
                    return
            except Exception:
                pass

        top = self.dialog_stack.pop()
        top.hide()
        self.client.OVERLAYS.remove("DIALOG", top)
        top.setParent(None)  # type: ignore[arg-type]

        # A dialog carries a blurred snapshot of the page the size of itself,
        # and every caller in the tree builds a fresh instance rather than
        # reopening one - so unparenting alone left that pixmap resident until
        # a reference cycle happened to be collected, which is hourly.
        release = getattr(top, "release_backdrop", None)
        if callable(release):
            try:
                release()
            except Exception:
                pass
        if not getattr(top, "REUSABLE", False):
            top.deleteLater()

        if self.dialog_stack:
            self.dialog_stack[-1].show()
            self.dialog_stack[-1].raise_()
        else:
            self.blocker.hide()


# QFrame + WA_StyledBackground are both load-bearing: a plain QWidget subclass
# renders transparent once parented into OVERLAYS, but looks fine standalone.
class BaseDialog(QFrame):

    WIDTH = 620
    MAX_HEIGHT = 720
    BLUR_RADIUS = 28
    # Set True on a subclass that is kept and reopened rather than rebuilt.
    # DialogManager.close() deletes anything without it, because every caller
    # in this tree constructs a fresh dialog per open and the alternative was
    # a full-size backdrop pixmap per dialog waiting on the hourly collection.
    REUSABLE = False
    # Past this, body and detail text scrolls instead of growing. A dialog is
    # capped by MAX_HEIGHT, so without this a long body is simply clipped -
    # a commit message with a paragraph after its summary loses the paragraph.
    SCROLL_BODY_AT = 320
    BODY_MAX_HEIGHT = 260
    DETAIL_MAX_HEIGHT = 220

    def __init__(self, client: "Client", title: str = "", body: str = "",
                 width: int = None, detail: str = None):
        super().__init__()
        self.client = client

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Never wider or taller than there is room for.
        #
        # A dialog declares the size it WANTS; the screen decides what it can
        # have. Without this one written on a large panel simply runs off a
        # smaller one - and what goes over the edge is the buttons, because
        # they sit at the bottom and the right.
        self.setFixedWidth(self._fits_across(width or self.WIDTH))
        self.setMaximumHeight(self._fits_down(self.MAX_HEIGHT))
        set_style(self, "overlays", "dialog-card")

        self._backdrop: Optional[QPixmap] = None
        self._backdrop_offset = QPoint(0, 0)
        self.blur_radius = self.BLUR_RADIUS

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 20)
        outer.setSpacing(12)

        if title:
            outer.addWidget(self.make_title(title))
        if body:
            label = self.make_body(body, muted=True)
            outer.addWidget(
                self._scrollable(label, self.BODY_MAX_HEIGHT)
                if len(body) > self.SCROLL_BODY_AT else label
            )

        # Kept, so a subclass can hand the spare height to its content rather
        # than to the stretch below - see BaseDialog.expand_content().
        self.outer   = outer
        self.content = QVBoxLayout()
        self.content.setSpacing(10)
        outer.addLayout(self.content)

        if detail:
            block = self.make_detail(detail)
            outer.addWidget(
                self._scrollable(block, self.DETAIL_MAX_HEIGHT)
                if len(detail) > self.SCROLL_BODY_AT else block
            )

        outer.addStretch()

        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(10)
        self.buttons.addStretch()
        outer.addLayout(self.buttons)

        self._outer = outer

    def expand_content(self) -> None:
        """
        Give the content area the leftover height instead of the trailing
        stretch.

        The stretch exists so a short dialog's buttons sit under its text
        rather than at the bottom of an empty card. A dialog whose content is
        the point - a map, a list - wants the opposite.
        """
        try:
            self.outer.setStretchFactor(self.content, 1)
            for index in range(self.outer.count()):
                item = self.outer.itemAt(index)
                if item is not None and item.spacerItem() is not None:
                    self.outer.removeItem(item)
                    break
        except Exception:
            pass

    ## -- frosted backdrop
    #
    # The same blurred snapshot the panels use. A dialog sits over the page
    # rather than beside it, so without this it is the one surface in the app
    # that reads as opaque card stock on glass.

    def refresh_backdrop(self) -> None:
        page = getattr(self.client, "PAGE", None)
        if page is None or self.width() <= 0 or self.height() <= 0:
            self._backdrop = None
            return

        # Via global coordinates. mapTo() only works when the target is an
        # ancestor, and it is not - a dialog hangs off OVERLAYS while the page
        # hangs off page_host, so mapTo(page, ...) returned a position in the
        # wrong space and the snapshot came from beside the dialog rather than
        # behind it.
        try:
            top_left = page.mapFromGlobal(self.mapToGlobal(QPoint(0, 0)))
        except Exception:
            self._backdrop = None
            return

        wanted = QRect(top_left, self.size())
        rect   = wanted.intersected(page.rect())
        if rect.isEmpty():
            self._backdrop = None
            return

        # Where the captured region sits inside the dialog. A dialog hanging
        # off the edge of the page grabs less than its full size, and drawing
        # that at 0,0 is what leaves an unfrosted strip down one side.
        self._backdrop_offset = QPoint(rect.x() - top_left.x(),
                                       rect.y() - top_left.y())

        snapshot = page.grab(rect)
        blurred = _blur_pixmap(snapshot, self.blur_radius)
        if blurred is None:
            self._backdrop = None
            return

        self._backdrop = blurred
        self.update()

    def release_backdrop(self) -> None:
        """
        Drop the blurred snapshot of the page behind this dialog.

        Called by DialogManager on close. It is a pixmap the size of the
        dialog, and a closed dialog waiting on the collector to notice it was
        holding one for as much as an hour.
        """
        self._backdrop = None

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if self._backdrop is not None and not self._backdrop.isNull():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            # 14px and inset by one, matching .dialog-card. A square wash under
            # a rounded border leaves the corners bright where the two disagree,
            # and a radius that does not match shows as a white nick on each
            # corner.
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect().adjusted(1, 1, -1, -1)),
                                14.0, 14.0)
            painter.setClipPath(path)
            # Filled first, so any part of the dialog that fell outside the
            # page is the wash colour rather than bare card.
            painter.fillRect(self.rect(), QColor(18, 18, 20, 235))
            painter.drawPixmap(self._backdrop_offset, self._backdrop)
            # A wash over the blur, or text on a bright wallpaper is unreadable.
            painter.fillRect(self.rect(), QColor(18, 18, 20, 205))
            painter.end()
        super().paintEvent(event)

    # Long enough for a sub-page slide to finish. A dialog opened from a
    # widget that first navigates somewhere grabbed the page mid-transition,
    # so the frosting showed where you had just been.
    SETTLE_MS = 320

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        # Queued: the dialog has no final position until the overlay has
        # centred it, and grabbing before that snapshots the wrong region.
        QTimer.singleShot(0, self.refresh_backdrop)
        # And again once anything moving underneath has stopped.
        QTimer.singleShot(self.SETTLE_MS, self.refresh_backdrop)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        QTimer.singleShot(0, self.refresh_backdrop)

    ## -- content helpers

    @staticmethod
    def make_title(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(make_font(SIZES.M2, bold=True))
        set_style(lbl, "common", "text-strong")
        lbl.setWordWrap(True)
        return lbl

    #How much of the screen a dialog may take. Not all of it: an overlay with
    #no margin looks like a page rather than something on top of one, and the
    #scrim behind it stops being visible enough to suggest tapping.
    SCREEN_MARGIN = 48

    def _room(self) -> tuple:
        """The space available, as (width, height). (0, 0) if unknown."""
        host = getattr(self.client, "OVERLAYS", None)
        if host is not None:
            try:
                size = host.size()
                if size.width() > 100 and size.height() > 100:
                    return size.width(), size.height()
            except Exception:
                pass
        # No overlay sized yet - ask the screen. This runs during startup for
        # anything that puts a dialog up before the window has a size.
        try:
            from PyQt6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                return area.width(), area.height()
        except Exception:
            pass
        return 0, 0

    def _fits_across(self, wanted: int) -> int:
        """The width asked for, or what there is room for."""
        room, _ = self._room()
        if not room:
            return int(wanted)
        return min(int(wanted), max(280, room - self.SCREEN_MARGIN * 2))

    def _fits_down(self, wanted: int) -> int:
        """The height asked for, or what there is room for."""
        _, room = self._room()
        if not room:
            return int(wanted)
        return min(int(wanted), max(240, room - self.SCREEN_MARGIN * 2))


    @classmethod
    def _scrollable(cls, widget, max_height: int):
        """Wrap a widget so it scrolls past max_height rather than clipping."""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setMaximumHeight(max_height)
        area.setWidget(widget)
        style_scrollbar(area)
        return area

    @staticmethod
    def make_body(text: str, muted: bool = False) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(make_font(SIZES.S2))
        set_style(lbl, "common", "text-muted" if muted else "text-strong")
        lbl.setWordWrap(True)
        return lbl

    @staticmethod
    def make_detail(text: str) -> QFrame:
        block = QFrame()
        block.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(block, "overlays", "dialog-detail")
        v = QVBoxLayout(block)
        v.setContentsMargins(14, 10, 14, 10)
        lbl = QLabel(text)
        lbl.setFont(make_font(SIZES.S1))
        lbl.setWordWrap(True)
        set_style(lbl, "common", "text-muted")
        v.addWidget(lbl)
        return block

    def add_scroll(self, inner: QWidget, min_height: int = 200) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(min_height)
        style_scrollbar(scroll)
        self.content.addWidget(scroll)
        return scroll

    ## -- buttons

    STYLES = {
        "primary":     "dialog-button-primary",
        "secondary":   "dialog-button-secondary",
        "destructive": "dialog-button-destructive",
        "disabled":    "dialog-button-disabled",
    }

    def set_button_state(self, button, enabled: bool,
                         kind: str = "secondary") -> None:
        """
        Enable or disable a button, and make it *look* it.

        add_button() picks the disabled style at construction only, so a button
        toggled later stayed styled as primary or destructive while refusing
        every tap - which reads as broken rather than as unavailable.
        """
        if button is None:
            return
        button.setEnabled(bool(enabled))
        if enabled:
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            set_style(button, "overlays",
                      self.STYLES.get(kind, self.STYLES["secondary"]))
        else:
            button.setCursor(Qt.CursorShape.ForbiddenCursor)
            set_style(button, "overlays", self.STYLES["disabled"])

    def add_button(self, text: str, on_click, kind: str = "secondary",
                   enabled: bool = True) -> QPushButton:
        btn = QPushButton(text)
        btn.setFont(make_font(SIZES.S2, bold=True))
        btn.setFixedHeight(44)
        btn.setMinimumWidth(120)
        if enabled:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            set_style(btn, "overlays", self.STYLES.get(kind, self.STYLES["secondary"]))
            if on_click:
                btn.clicked.connect(lambda: on_click())
        else:
            btn.setEnabled(False)
            btn.setCursor(Qt.CursorShape.ForbiddenCursor)
            set_style(btn, "overlays", self.STYLES["disabled"])
        self.buttons.addWidget(btn)
        return btn

    def clear_content(self) -> None:
        while self.content.count():
            item = self.content.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def clear_buttons(self) -> None:
        while self.buttons.count():
            item = self.buttons.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.buttons.addStretch()

    ## -- lifecycle

    def close(self) -> None:
        self.client.DIALOG.close()

    def center(self) -> None:
        host = self.client.OVERLAYS
        self.adjustSize()
        # adjustSize() grows a widget to its hint but never shrinks it, so a
        # dialog whose content got smaller keeps its old height and sits
        # off-centre - or off-screen.
        hint = self.sizeHint().height()
        if hint < self.height():
            self.resize(self.width(), hint)
        self.move(max(0, (host.width() - self.width()) // 2),
                  max(0, (host.height() - self.height()) // 2))


class _ClickBlocker(QWidget):

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
        painter.fillRect(self.rect(), QColor(0, 0, 0, 140))

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.clicked.emit()


# ── Panel (generic full-height side panel) ────────────────────────────────────

class _PanelScrim(QWidget):
    """
    The ground beside a panel, which presses land on.

    Barely tinted rather than clear: a panel that swallows a press with no
    sign of why looks broken, and a slight darkening says the panel is in
    front of everything without dimming the page into unreadability.
    """

    #alpha out of 255
    TINT = 46

    def __init__(self, parent: QWidget, panel: "Panel"):
        super().__init__(parent)
        self.panel = panel
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, self.TINT))
        painter.end()

    def mousePressEvent(self, event) -> None:
        # Accepted rather than passed on: the press was "get rid of this", and
        # letting it through would also hit whatever is underneath.
        event.accept()
        try:
            self.panel.dismiss()
        except RuntimeError:
            pass

    def mouseReleaseEvent(self, event) -> None:
        event.accept()


class Panel(QWidget):

    DEFAULT_WIDTH = 680   #shared by TilePanel/NotificationPanel/create_panel() — see apply_frosted_style()
    BLUR_RADIUS   = 28

    def __init__(
        self,
        client:            "Client",
        width:             int  = None,
        edge:              str  = "right",   # "right", "left", "top" or "bottom"
        bgcolor:           str  = "#1e1e1e", #fallback fill ONLY — used if a backdrop snapshot can't be captured
        animation_speed:   int  = 220,
        blur_radius:       int  = None,
        radius:            str  = None,      #CSS border-radius, e.g. "8px" — None means square corners
        key:               str  = None,
        destroy_on_close:  bool = False,
        height:            int  = None,      #None fills the cross axis; set it for a panel that does not reach the far edge
        margin:            int  = 0,         #inset from the screen edges — non-zero makes the panel float
        dismiss_on_outside_click: bool = False,
        blocks_idle:       bool = False,     #hold the idle clock open while this is up
    ):
        super().__init__(client.OVERLAYS)
        self.client            = client
        self.key               = key
        self.edge              = edge if edge in ("right", "left", "top", "bottom") else "right"
        self.margin            = max(0, int(margin))
        # Whether the idle clock runs while this panel is open.
        #
        # Off by default: most panels are a control somebody uses and leaves,
        # and one that stops the panel ever going idle is a panel that has to
        # be dismissed by hand. On for anything meant to be READ - a
        # conversation produces no interaction while somebody reads it, so
        # timing out behind one measures the wrong thing.
        self.blocks_idle       = bool(blocks_idle)
        # Told when this closes, however it closes.
        #
        # A panel goes away by the button, by a press beside it, by a timeout
        # or by whatever owns it - and an owner that only hears about the
        # route it drove itself is an owner still running a conversation
        # nobody can see.
        self.on_closed_hook    = None
        self.floating          = self.margin > 0
        # None on either axis means "fill it, less the margin". A left/right
        # panel still defaults to DEFAULT_WIDTH so existing callers are
        # unchanged; a top/bottom one defaults to the full span instead,
        # because a fixed width across the top is almost never what is wanted.
        if width is None and self.edge in ("left", "right"):
            width = self.DEFAULT_WIDTH
        self.panel_width       = width
        self.panel_height      = height
        self.blur_radius       = blur_radius if blur_radius is not None else self.BLUR_RADIUS
        self.destroy_on_close  = destroy_on_close   #see close_panel()/_destroy() below
        # Whether pressing anywhere else closes it.
        #
        # Opt-in rather than the default: a transient panel put up by the idle
        # rotation is dismissed by that rotation on its own schedule, and
        # closing it on the first touch would swallow the very tap that woke
        # the screen to read it. A panel somebody opened deliberately is the
        # other way round - there is nothing else to press, and no obvious close button
        # is a panel that cannot be got rid of.
        self.dismiss_on_outside_click = bool(dismiss_on_outside_click)
        self._scrim: Optional[QWidget] = None
        self._destroyed        = False
        self._close_connected  = False
        self._slide_connected  = False
        self._mask_held        = False
        self._fallback_bg = QColor(bgcolor)
        self._backdrop: Optional[QPixmap] = None
        self.open         = False
        self._closing     = False

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if not self.objectName():
            self.setObjectName("overlay_panel")
        self.apply_frosted_style(radius)

        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)

        # The third argument is the PARENT. Without it the animation belongs
        # to nothing, outlives the widget it animates, and fires `finished`
        # into an object that has gone - which inside a Qt signal aborts the
        # process rather than raising.
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(animation_speed)

        self.client.OVERLAYS.installEventFilter(self)

        self._hidden_pos = QPoint(0, 0)
        self._shown_pos  = QPoint(0, 0)
        self._sync_geometry()
        self.hide()

    # ── Content ───────────────────────────────────────────────────────────────

    def add_content(self, widget: QWidget) -> None:
        self.content_layout.addWidget(widget)

    def clear_content(self) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── Styling ───────────────────────────────────────────────────────────────

    def apply_frosted_style(self, radius: str = None) -> None:
        line = "1px solid rgba(255,255,255,18)"
        if self.floating:
            # Away from the screen edges the panel reads as a card, so it needs
            # an outline the whole way round rather than one seam.
            override = {"*": {"border": line, "border-radius": radius or "14px"}}
        else:
            inward_side = {
                "right":  "border-left",
                "left":   "border-right",
                "top":    "border-bottom",
                "bottom": "border-top",
            }[self.edge]
            override = {"*": {inward_side: line}}
            if radius:
                override["*"]["border-radius"] = radius
        set_style(self, "panel", "panel-base",
                  object_tag=f"QWidget#{self.objectName()}", override=override)

    # ── Painting ──────────────────────────────────────────────────────────────

    def refresh_backdrop(self) -> None:
        page = getattr(self.client, "PAGE", None)
        if page is None or self.width() <= 0 or self.height() <= 0:
            self._backdrop = None
            return

        rect = QRect(self._shown_pos, self.size()).intersected(page.rect())
        if rect.isEmpty():
            self._backdrop = None
            return

        snapshot = page.grab(rect)
        blurred = _blur_pixmap(snapshot, self.blur_radius)
        if blurred is None:
            self._backdrop = None
            return

        if blurred.size() != self.size():
            padded = QPixmap(self.size())
            padded.fill(Qt.GlobalColor.transparent)
            p = QPainter(padded)
            p.drawPixmap(0, 0, blurred)
            p.end()
            blurred = padded

        self._backdrop = blurred
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        if self._backdrop is not None and not self._backdrop.isNull():
            painter.drawPixmap(0, 0, self._backdrop)
        else:
            painter.fillRect(self.rect(), self._fallback_bg)
        painter.end()

        opt = QStyleOption()
        opt.initFrom(self)
        painter = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, painter, self)

    # ── Geometry ──────────────────────────────────────────────────────────────

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if obj is self.client.OVERLAYS:
            if event.type() == QEvent.Type.Resize:
                self._sync_geometry()
        return super().eventFilter(obj, event)

    ## -- dismissing by pressing elsewhere

    def _build_scrim(self) -> None:
        """
        A full-overlay catcher behind the panel.

        Not an event filter on the overlay, which cannot work: the overlay
        masks itself to where its children can paint, and **a QWidget mask
        clips input as well as painting** - so a press beside the panel never
        reaches the overlay at all, it goes straight to the page underneath.

        A sibling widget covering the whole overlay is inside that mask by
        definition, because the mask is built from its children's geometry.
        """
        if self._scrim is not None or not self.dismiss_on_outside_click:
            return

        # A panel built off the UI thread never got one, and a panel whose
        # overlay was rebuilt lost it. Either way there is nothing for the
        # scrim to cover, and the panel still opens - it just cannot be
        # dismissed by pressing beside it.
        host = self.parentWidget()
        if host is None:
            self.client.log("warning", f"[Panel] {self.key or 'panel'} has no "
                                       f"parent; opening without a scrim.")
            return

        scrim = _PanelScrim(host, self)
        scrim.setGeometry(host.rect())
        scrim.show()
        # Behind the panel, so the panel's own presses are its own.
        scrim.stackUnder(self)
        self._scrim = scrim

    def dismiss(self) -> None:
        """
        Close, however this panel closes.

        The scrim calls this rather than `close_panel()` directly. A panel that
        drives its own slide - the tiles and notification panels both do - has
        a close path of its own, and reaching past it leaves the panel hidden
        while it still believes it is open.
        """
        self.close_panel()

    def _release_scrim(self) -> None:
        scrim, self._scrim = self._scrim, None
        if scrim is None:
            return
        try:
            scrim.hide()
            scrim.setParent(None)
            scrim.deleteLater()
        except RuntimeError:
            pass

    def _sync_geometry(self) -> None:
        ov_w = self.client.OVERLAYS.width()
        ov_h = self.client.OVERLAYS.height()
        m    = self.margin

        w = self.panel_width  if self.panel_width  is not None else max(1, ov_w - m * 2)
        h = self.panel_height if self.panel_height is not None else max(1, ov_h - m * 2)
        w = max(1, min(w, ov_w))
        h = max(1, min(h, ov_h))
        self.setFixedSize(w, h)

        # Hidden is always fully off its own edge, including the margin, so a
        # floating panel does not leave a sliver parked on screen.
        if self.edge == "left":
            y = m if self.panel_height is not None else max(0, (ov_h - h) // 2)
            self._shown_pos  = QPoint(m, y)
            self._hidden_pos = QPoint(-(w + m), y)
        elif self.edge == "top":
            x = max(0, (ov_w - w) // 2)
            self._shown_pos  = QPoint(x, m)
            self._hidden_pos = QPoint(x, -(h + m))
        elif self.edge == "bottom":
            x = max(0, (ov_w - w) // 2)
            self._shown_pos  = QPoint(x, ov_h - h - m)
            self._hidden_pos = QPoint(x, ov_h + m)
        else:  # "right" (default)
            y = m if self.panel_height is not None else max(0, (ov_h - h) // 2)
            self._shown_pos  = QPoint(ov_w - w - m, y)
            self._hidden_pos = QPoint(ov_w + m, y)

        if self.open:
            self.move(self._shown_pos)
            self.refresh_backdrop()   #keep the blur correct across a live resize
        elif not self._closing:
            self.move(self._hidden_pos)

    # ── Show / hide ───────────────────────────────────────────────────────────

    def _hold_mask(self) -> None:
        """
        Balanced, so a double open or a re-entered close cannot leak one.

        The swept rect is the union of where the panel is and where it is
        going, so every frame of the slide is inside the mask and therefore
        paintable - a mask clips painting, not just clicks.
        """
        if self._mask_held:
            return
        try:
            sweep = QRect(self.pos(), self.size()).united(
                QRect(self._shown_pos, self.size())).united(
                QRect(self._hidden_pos, self.size()))
            self.client.OVERLAYS.hold_mask(sweep)
            self._mask_held = True
        except Exception:
            pass

    def _release_mask(self) -> None:
        if not self._mask_held:
            return
        self._mask_held = False
        try:
            self.client.OVERLAYS.release_mask()
        except Exception:
            pass

    def _on_slide_finished(self) -> None:
        self._release_mask()

    def toggle(self) -> None:
        self.close_panel() if self.open else self.open_panel()

    def open_panel(self) -> None:
        if self._destroyed:
            self.client.log("warning", f"[Panel] open_panel() called on an already-destroyed panel (key={self.key}) — ignored.")
            return
        if self.open:
            # Already open, but make sure it can still be closed. A press that
            # arrives mid-slide can leave the catcher behind, and a panel
            # nobody can dismiss is worse than one that opens twice.
            self._build_scrim()
            return
        self._closing = False
        self._sync_geometry()
        self.move(self._hidden_pos)
        self.refresh_backdrop()
        self._build_scrim()
        self.show()
        self.raise_()

        # Held across the slide: every frame of it is a Move event, and each
        # one would otherwise recompute the overlay's hit mask.
        self._hold_mask()
        if not self._slide_connected:
            self._anim.finished.connect(self._on_slide_finished)
            self._slide_connected = True

        self._anim.stop()
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(self._hidden_pos)
        self._anim.setEndValue(self._shown_pos)
        self._anim.start()
        self.open = True

    def close_panel(self, destroy: bool = None) -> None:
        should_destroy = self.destroy_on_close if destroy is None else destroy

        if not self.open:
            if should_destroy:
                self._destroy()
            return

        self.open               = False
        self._closing           = True
        self._destroy_after_close = should_destroy

        self._hold_mask()
        if not self._slide_connected:
            self._anim.finished.connect(self._on_slide_finished)
            self._slide_connected = True

        self._anim.stop()
        self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(self._hidden_pos)
        # Connected once, not per close. stop() does not emit finished, so a
        # panel reopened mid-close left this connected and the next close
        # stacked a second one - _on_closed then ran twice.
        if not self._close_connected:
            self._anim.finished.connect(self._on_closed)
            self._close_connected = True
        self._anim.start()

    def _on_closed(self) -> None:
        if not self._closing:
            # The animation that just finished was an open, not a close.
            #
            # The scrim used to be released BEFORE this check, so a panel
            # opened, closed and reopened faster than the slide takes ended up
            # open with nothing behind it to catch a press - and with no close
            # button either, which is stuck.
            return
        self._release_scrim()
        self.hide()
        self._closing = False
        self._release_mask()
        self._release_backdrop()

        hook, self.on_closed_hook = self.on_closed_hook, None
        if hook is not None:
            # Next turn of the event loop, not here.
            #
            # This runs inside `_anim.finished`, so anything the hook does is
            # done on top of a QPropertyAnimation that is still emitting - and
            # an owner tearing itself down in response will close panels,
            # destroy widgets and start animations from inside that emission.
            # Qt does not survive all of those, and what comes back is a
            # SIGSEGV with nothing in the log rather than an exception
            # anything here could catch.
            def run(call=hook):
                try:
                    call()
                except Exception:
                    pass
            QTimer.singleShot(0, run)

        if getattr(self, "_destroy_after_close", False):
            self._destroy_after_close = False
            self._destroy()

    def _release_backdrop(self) -> None:
        """
        Drop the blurred snapshot while closed.

        It is a pixmap the size of the panel - several MB at 1080p - and it
        was only ever replaced on the next open, so every closed panel in the
        app held one indefinitely for something nobody was looking at.
        """
        self._backdrop = None

    def _destroy(self) -> None:
        self._release_scrim()
        if self._destroyed:
            return
        self._destroyed = True
        self._release_mask()
        self._release_backdrop()
        try:
            self.client.OVERLAYS.removeEventFilter(self)
        except Exception:
            pass
        self.setParent(None)
        self.deleteLater()