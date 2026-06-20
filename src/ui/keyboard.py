from __future__ import annotations
from typing import Callable

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QGridLayout,
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint, pyqtSignal, QTimer
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen

from src.styling import make_font, SIZES, set_style


# ── Key button ────────────────────────────────────────────────────────────────

class _Key(QPushButton):
    def __init__(self, label: str, action: str = None, wide: bool = False):
        super().__init__(label)
        self.action = action or label
        self.setFont(make_font(SIZES.S2, bold=False))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        w = 100 if wide else 52
        self.setFixedSize(w, 52)
        set_style(self, "keyboard", "key-button")
        # OVERLAYS (this keyboard's parent) defaults to
        # WA_TransparentForMouseEvents=True for click-passthrough when
        # no dialog is open — the keyboard is shown directly via
        # show_keyboard(), never through DialogManager, so that
        # attribute is never flipped off. It cascades to every child
        # that doesn't explicitly clear it on itself, which is exactly
        # why every key press was being swallowed before reaching here.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

    def mousePressEvent(self, event):
        # Temporary diagnostic — confirms whether Qt is delivering the
        # press event to this widget at all. Remove once the actual
        # click issue is confirmed fixed.
        print(f"[_Key DIAG] mousePressEvent received on key '{self.action}'")
        super().mousePressEvent(event)


# ── Base keyboard ─────────────────────────────────────────────────────────────

class KeyboardPopup(QWidget):
    """
    Floating keyboard that types into a target QLineEdit.
    Slides up from the bottom of the overlay.
    """

    submitted = pyqtSignal(str)   # emitted on Enter/Done

    def __init__(self, client, target: QLineEdit, parent: QWidget = None, label: str = ""):
        # NOTE: parent is intentionally NOT passed to super().__init__()
        # here. This widget must be added to OverlayManager's TOPMOST
        # layer via overlay.add("TOPMOST", kb) — see make_keyboard()'s
        # caller in settings.py — rather than just parented directly.
        # OverlayManager tracks everything added through add()/insert()
        # in self._layers and re-stacks all of them in a fixed order
        # every time ANYTHING is added or removed anywhere
        # (_enforce_z_order). A widget that's only ever setParent()'d
        # directly (the old behaviour) is invisible to that tracking —
        # it never gets raised again once something else triggers a
        # re-stack, which is exactly why clicks could land on some
        # other overlay widget instead of the keyboard once anything
        # else (a notification, a dialog) was added or removed while
        # the keyboard was open.
        super().__init__()
        self._overlay_parent = parent   # kept for show_keyboard()/close_keyboard() geometry only
        self.client = client
        self.target = target
        self._caps  = False
        self._label_text = label

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("kb_popup")
        set_style(self, "keyboard", "keyboard-popup", object_tag="QWidget#kb_popup")
        # See the matching note in _Key.__init__ — OVERLAYS (this
        # widget's parent) defaults to transparent-for-mouse-events,
        # which cascades down unless explicitly cleared here too.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        # Shows which setting is being edited, e.g. "Widget Margin" —
        # this is what's being edited; the preview row below it (just
        # under) shows the live text value as you type, same as before.
        if self._label_text:
            self._label_lbl = QLabel(self._label_text)
            self._label_lbl.setFont(make_font(SIZES.S1, bold=True))
            set_style(self._label_lbl, "common", "text-muted")
            outer.addWidget(self._label_lbl)

        # Preview bar
        preview_row = QHBoxLayout()
        self._preview = QLabel()
        self._preview.setFont(make_font(SIZES.S3))
        set_style(self._preview, "common", "text-strong")
        self._refresh_preview()

        close_btn = _Key("✕", "close", wide=False)
        close_btn.clicked.connect(self.close_keyboard)

        preview_row.addWidget(self._preview, stretch=1)
        preview_row.addWidget(close_btn)
        outer.addLayout(preview_row)

        # Build key rows
        self._key_grid = QGridLayout()
        self._key_grid.setSpacing(6)
        outer.addLayout(self._key_grid)

        self._build_keys()

        # Animation
        self._anim = QPropertyAnimation(self, b"pos")
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ── Key layout ────────────────────────────────────────────────────────────

    def _build_keys(self) -> None:
        rows = self._key_rows()
        for r, row in enumerate(rows):
            col = 0
            for key_data in row:
                if isinstance(key_data, tuple):
                    label, action, wide = key_data
                else:
                    label = action = key_data
                    wide = False
                btn = _Key(label, action, wide=wide)
                btn.clicked.connect(lambda _, a=action: self._press(a))
                span = 2 if wide else 1
                self._key_grid.addWidget(btn, r, col, 1, span)
                col += span

    def _key_rows(self) -> list:
        """Override in subclasses for different layouts."""
        return [
            ["1","2","3","4","5","6","7","8","9","0"],
            ["q","w","e","r","t","y","u","i","o","p"],
            ["a","s","d","f","g","h","j","k","l"],
            [("⇧","shift",False),"z","x","c","v","b","n","m",("⌫","backspace",False)],
            [("⎵","space",True),(".",".",False),("-","-",False),("_","_",False),
             ("Done","done",True)],
        ]

    # ── Key handling ──────────────────────────────────────────────────────────

    def _press(self, action: str) -> None:
        # Temporary diagnostic — confirms the clicked signal chain
        # reached _press at all. Remove once confirmed fixed.
        print(f"[KeyboardPopup DIAG] _press called with action='{action}'")
        t = self.target
        if action == "backspace":
            cur = t.cursorPosition()
            if cur > 0:
                text = t.text()
                t.setText(text[:cur-1] + text[cur:])
                t.setCursorPosition(cur - 1)
        elif action == "space":
            self._insert(" ")
        elif action == "shift":
            self._caps = not self._caps
            self._rebuild_caps()
        elif action == "done":
            self.submitted.emit(t.text())
            self.close_keyboard()
        elif action == "close":
            self.close_keyboard()
        elif len(action) == 1:
            self._insert(action.upper() if self._caps else action)
            if self._caps:
                self._caps = False
                self._rebuild_caps()
        self._refresh_preview()

    def _insert(self, char: str) -> None:
        t = self.target
        cur  = t.cursorPosition()
        text = t.text()
        t.setText(text[:cur] + char + text[cur:])
        t.setCursorPosition(cur + 1)

    def _rebuild_caps(self) -> None:
        # Re-render single-char key labels for caps state
        for i in range(self._key_grid.count()):
            item = self._key_grid.itemAt(i)
            if item and item.widget():
                btn = item.widget()
                if isinstance(btn, _Key) and len(btn.action) == 1 and btn.action.isalpha():
                    btn.setText(btn.action.upper() if self._caps else btn.action)

    def _refresh_preview(self) -> None:
        text = self.target.text() if self.target else ""
        if len(text) > 40:
            text = "…" + text[-40:]
        self._preview.setText(text or " ")

    # ── Show / hide ───────────────────────────────────────────────────────────

    def show_keyboard(self) -> None:
        parent = self._overlay_parent
        if not parent:
            return

        # Register with OverlayManager's layer system instead of just
        # being parented — this is what keeps the keyboard correctly
        # stacked above everything else even after other overlay
        # widgets are later added/removed. See the note in __init__.
        if hasattr(parent, "add"):
            parent.add("TOPMOST", self)
        else:
            # Fallback for a plain QWidget parent with no layer API
            self.setParent(parent)
            self.show()

        ph = parent.height()

        # Size to the keyboard's OWN natural content width, not the
        # parent's full width. setFixedWidth(parent_width) used to
        # stretch every key column to fill that width via QGridLayout's
        # default column stretching, spreading keys far apart from each
        # other instead of keeping their natural 52px/100px sizes.
        self.adjustSize()
        kw = self.sizeHint().width()
        kh = self.sizeHint().height()
        self.setFixedSize(kw, kh)

        # Centre horizontally within the parent
        kx = (parent.width() - kw) // 2
        self.move(kx, ph)          # start off-screen below
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(QPoint(kx, ph))
        self._anim.setEndValue(QPoint(kx, ph - kh))
        self._anim.start()

        # Temporary diagnostic — confirms the keyboard's actual final
        # geometry/parent/visibility state. Remove once the click issue
        # is confirmed fixed. If isVisible() is False here, or
        # geometry() doesn't match what's expected, the widget genuinely
        # isn't where it appears to be.
        print(
            f"[KeyboardPopup DIAG] parent={self.parent()!r} "
            f"geometry={self.geometry()} isVisible={self.isVisible()} "
            f"transparentForMouse={self.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)}"
        )

        def _diag_keys():
            first_item = self._key_grid.itemAt(0)
            if first_item and first_item.widget():
                k = first_item.widget()
                print(
                    f"[_Key DIAG] first key geometry={k.geometry()} "
                    f"mapped_to_global_topleft={k.mapToGlobal(QPoint(0,0))} "
                    f"isVisible={k.isVisible()} isEnabled={k.isEnabled()} "
                    f"transparentForMouse={k.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)}"
                )
        QTimer.singleShot(250, _diag_keys)   # after the slide-in animation settles

    def close_keyboard(self) -> None:
        parent = self._overlay_parent
        ph = parent.height() if parent else 1000
        kx = self.x()
        self._anim.stop()
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(QPoint(kx, ph))

        def _finish():
            self.hide()
            if parent and hasattr(parent, "remove"):
                parent.remove("TOPMOST", self)
            if getattr(self.client, "ACTIVE_KEYBOARD", None) is self:
                self.client.ACTIVE_KEYBOARD = None
            self.deleteLater()

        self._anim.finished.connect(_finish)
        self._anim.start()


# ── Numpad (int / float only) ─────────────────────────────────────────────────

class NumpadPopup(KeyboardPopup):
    """Compact numpad for int / float / numeric settings."""

    def _key_rows(self) -> list:
        return [
            ["7", "8", "9"],
            ["4", "5", "6"],
            ["1", "2", "3"],
            [("±","negate",False), "0", ("⌫","backspace",False)],
            [(".", ".", False), ("Done","done",True)],
        ]

    def _press(self, action: str) -> None:
        if action == "negate":
            text = self.target.text()
            if text.startswith("-"):
                self.target.setText(text[1:])
            else:
                self.target.setText("-" + text)
            self._refresh_preview()
        else:
            super()._press(action)


# ── Factory ───────────────────────────────────────────────────────────────────

def make_keyboard(client, target: QLineEdit, setting_type: str,
                  parent: QWidget, label: str = "") -> KeyboardPopup:
    """Return the appropriate keyboard for the setting type."""
    numeric_types = {"int", "float", "numeric", "list[int]", "list[float]"}
    if setting_type in numeric_types:
        kb = NumpadPopup(client, target, parent, label=label)
    else:
        kb = KeyboardPopup(client, target, parent, label=label)
    return kb