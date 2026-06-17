from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QPen

from src.ui.page import PageFramework
from src.ui.controls.drawer import Drawer
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons
from src.styling import make_font, COLORS, add_text_shadow

if TYPE_CHECKING:
    from src.main import Client


class _GridBackground(QWidget):
    """Subtle dot-grid background."""

    GRID_SPACING = 32
    DOT_RADIUS   = 1

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        dot_color = QColor(255, 255, 255, 18)   # very subtle white dots
        painter.setPen(Qt.GlobalColor.transparent)
        painter.setBrush(dot_color)

        s = self.GRID_SPACING
        r = self.DOT_RADIUS
        for x in range(s, self.width(), s):
            for y in range(s, self.height(), s):
                painter.drawEllipse(x - r, y - r, r * 2, r * 2)


class RootPage(PageFramework):
    """
    Shown when no plugin has registered a default page.
    Displays a subtle grid background and a helpful message in the centre.
    Still provides a drawer with Settings, Fullscreen, and Close.
    """

    def __init__(self, client: "Client", data=None):
        super().__init__(key="#", client=client, data=data)

        w = int(client.SETTINGS.application.window.size.value[0])
        h = int(client.SETTINGS.application.window.size.value[1])
        self.setFixedSize(w, h)
        self.setStyleSheet(f"background-color: {COLORS.DARK.BGDARK};")

        # ── Grid background ───────────────────────────────────────────────────
        self._grid = _GridBackground(self)
        self._grid.setGeometry(0, 0, w, h)

        # ── Centre message ────────────────────────────────────────────────────
        centre = QWidget(self)
        centre.setStyleSheet("background: transparent;")
        centre.setFixedWidth(600)

        layout = QVBoxLayout(centre)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("No home page installed")
        title.setFont(make_font(28, bold=True))
        title.setStyleSheet(f"color: {COLORS.DARK.TEXT.IMPORTANT}; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        add_text_shadow(title, blur=8)

        body = QLabel(
            "Install a plugin that registers a page, or add\n"
            "CoreWidgetsBundle for the default home experience."
        )
        body.setFont(make_font(16, bold=False))
        body.setStyleSheet(f"color: {COLORS.DARK.TEXT.MUTED}; background: transparent;")
        body.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        body.setWordWrap(True)
        add_text_shadow(body, blur=6)

        hint = QLabel("src/assets/bundled/CoreWidgetsBundle")
        hint.setFont(make_font(13, bold=False, family="monospace"))
        hint.setStyleSheet(
            f"color: {COLORS.PRIMARY.LIGHT}; background: rgba(255,255,255,8);"
            f"border: 1px solid rgba(255,255,255,15); border-radius: 4px;"
            f"padding: 6px 14px;"
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        layout.addWidget(title)
        layout.addWidget(body)
        layout.addSpacing(8)
        layout.addWidget(hint)

        # Centre the message widget
        centre.adjustSize()
        centre.move(
            (w - centre.width())  // 2,
            (h - centre.height()) // 2,
        )

        # ── Drawer ────────────────────────────────────────────────────────────
        self.drawer = Drawer(client, position="bottom")
        self.drawer.setParent(self)
        self.drawer.place_on_page()

        self.drawer.add_controls([
            IconButton(Icons.SETTINGS,  lambda: client.goto("#settings")),
            IconButton(Icons.FULLSCREEN, client.toggle_fullscreen),
            IconButton(Icons.CLOSE,      client.stop),
        ])

        self.drawer.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self._grid.setGeometry(0, 0, w, h)
        self.drawer.apply_parent_width()