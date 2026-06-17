from __future__ import annotations
from typing import Callable

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QGridLayout,
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen

from src.styling import COLORS, make_font, SIZES


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
        self.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS.DARK.BGLIGHT};
                color: {COLORS.DARK.TEXT.IMPORTANT};
                border: 1px solid {COLORS.DARK.BORDER.NORMAL};
                border-radius: 6px;
                font-size: {SIZES.S2}px;
            }}
            QPushButton:hover  {{ background: {COLORS.DARK.BG}; }}
            QPushButton:pressed{{ background: rgba(255,255,255,8); }}
        """)


# ── Base keyboard ─────────────────────────────────────────────────────────────

class KeyboardPopup(QWidget):
    """
    Floating keyboard that types into a target QLineEdit.
    Slides up from the bottom of the overlay.
    """

    submitted = pyqtSignal(str)   # emitted on Enter/Done

    def __init__(self, client, target: QLineEdit, parent: QWidget = None):
        super().__init__(parent)
        self.client = client
        self.target = target
        self._caps  = False

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            QWidget#kb_popup {{
                background: {COLORS.DARK.BG};
                border-top: 1px solid {COLORS.DARK.BORDER.NORMAL};
                border-radius: 0px;
            }}
        """)
        self.setObjectName("kb_popup")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        # Preview bar
        preview_row = QHBoxLayout()
        self._preview = QLabel()
        self._preview.setFont(make_font(SIZES.S3))
        self._preview.setStyleSheet(
            f"color: {COLORS.DARK.TEXT.IMPORTANT}; background: transparent;"
        )
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
        parent = self.parent()
        if not parent:
            return
        pw, ph = parent.width(), parent.height()
        self.setFixedWidth(pw)
        self.adjustSize()
        kh = self.sizeHint().height()
        self.setFixedHeight(kh)
        self.move(0, ph)          # start off-screen below
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(QPoint(0, ph))
        self._anim.setEndValue(QPoint(0, ph - kh))
        self._anim.start()

    def close_keyboard(self) -> None:
        parent = self.parent()
        ph = parent.height() if parent else 1000
        self._anim.stop()
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(QPoint(0, ph))
        self._anim.finished.connect(self.hide)
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
                  parent: QWidget) -> KeyboardPopup:
    """Return the appropriate keyboard for the setting type."""
    numeric_types = {"int", "float", "numeric", "list[int]", "list[float]"}
    if setting_type in numeric_types:
        kb = NumpadPopup(client, target, parent)
    else:
        kb = KeyboardPopup(client, target, parent)
    return kb