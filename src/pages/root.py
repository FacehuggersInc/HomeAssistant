from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QPen

from src.ui.page import PageFramework
from src.ui.controls.drawer import Drawer
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons
from src.styling import make_font, add_text_shadow, set_style

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
    Shown when no plugin has registered a default page, or as an
    in-between screen while a plugin is mid-reload.

    Displays a subtle grid background and a message in the centre. The
    message is configurable via the `data` dict passed to
    client.goto("#root", data={...}) — this is what lets a plugin (or
    PluginManager during a hot reload) show something contextual like
    "CoreWidgetsBundle is reloading..." instead of the generic
    "no home page installed" message, so there's a visible difference
    between "nothing is registered at all" and "something is
    temporarily unavailable mid-reload".

    data keys (all optional):
        title     : str  — headline text. Default: "No home page installed"
        body      : str  — supporting text, \\n for line breaks.
        hint      : str  — monospace path/hint line shown below the body.
        show_hint : bool — set False to hide the hint line entirely.
                    Default: True (only matters if hint is also set or
                    defaulted)

    Still provides a drawer with Settings, Fullscreen, and Close
    regardless of what message is showing.
    """

    DEFAULT_TITLE = "No home page installed"
    DEFAULT_BODY  = (
        "Install a plugin that registers a page, or add\n"
        "CoreWidgetsBundle for the default home experience."
    )
    DEFAULT_HINT  = "src/assets/bundled/CoreWidgetsBundle"

    def __init__(self, client: "Client", data=None):
        super().__init__(key="#", client=client, data=data)

        data = data or {}
        title_text = data.get("title", self.DEFAULT_TITLE)
        body_text  = data.get("body",  self.DEFAULT_BODY)
        hint_text  = data.get("hint",  self.DEFAULT_HINT)
        show_hint  = data.get("show_hint", True)

        w = int(client.SETTINGS.application.window.size.value[0])
        h = int(client.SETTINGS.application.window.size.value[1])
        self.setFixedSize(w, h)
        set_style(self, "common", "page-background")

        # ── Grid background ───────────────────────────────────────────────────
        self._grid = _GridBackground(self)
        self._grid.setGeometry(0, 0, w, h)

        # ── Centre message ────────────────────────────────────────────────────
        centre = QWidget(self)
        set_style(centre, "common", "transparent")
        centre.setFixedWidth(600)

        layout = QVBoxLayout(centre)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel(title_text)
        title.setFont(make_font(28, bold=True))
        set_style(title, "common", "text-strong")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        title.setWordWrap(True)
        add_text_shadow(title, blur=8)

        body = QLabel(body_text)
        body.setFont(make_font(16, bold=False))
        set_style(body, "common", "text-muted")
        body.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        body.setWordWrap(True)
        add_text_shadow(body, blur=6)

        layout.addWidget(title)
        layout.addWidget(body)

        if show_hint and hint_text:
            hint = QLabel(hint_text)
            hint.setFont(make_font(13, bold=False, family="monospace"))
            set_style(hint, "root", "root-hint")
            hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
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