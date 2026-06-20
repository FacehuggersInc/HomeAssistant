from __future__ import annotations

from PyQt6.QtWidgets import (
    QPushButton, QToolButton, QMenu, QSizePolicy,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon

from src.styling import STYLES, make_font, add_text_shadow, set_style
from src.ui.icons import icon as resolve_to_icon, resolve as resolve_name


# ── Icon Button ───────────────────────────────────────────────────────────────

class IconButton(QPushButton):
    """
    A flat icon button backed by qtawesome MDI icons.

    `icon` accepts:
      - An Icons constant   : Icons.CLOSE
      - A registered name   : "close", "settings", "bell"
      - A raw MDI name      : "mdi.alarm"
      - A QIcon directly    : qta.icon("mdi.star", color="gold")
    """

    def __init__(
        self,
        icon,
        func,
        size:          int   = 40,
        color:         str   = "white",
        color_hover:   str   = "rgba(255,255,255,200)",
        visible:       bool  = True,
        data                 = None,
    ):
        super().__init__()
        self._data = data
        self._size = size

        self.setFixedSize(size * 2, size * 2)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._set_icon(icon, color, size)

        set_style(self, "buttons", "icon-button",
                  override={"*": {"border-radius": f"{size}px"}})

        self.setVisible(visible)
        self.clicked.connect(lambda _checked=False: func())

    def _set_icon(self, icon_arg, color: str, size: int) -> None:
        if isinstance(icon_arg, QIcon):
            q_icon = icon_arg
        elif isinstance(icon_arg, str):
            q_icon = resolve_to_icon(icon_arg, color=color)
        else:
            from src.ui.icons import icon as _icon
            q_icon = _icon("mdi.help-circle", color=color)

        self.setIcon(q_icon)
        self.setIconSize(QSize(size + 8, size + 8))
        add_text_shadow(self, blur=6, offset_x=1, offset_y=1)

    def update_icon(self, icon_arg, color: str = "white") -> None:
        """Swap the icon at runtime (e.g. play/pause toggle)."""
        self._set_icon(icon_arg, color, self._size)

    @property
    def data(self):
        return self._data


# ── Icon + Text Button ────────────────────────────────────────────────────────

class IconAndTextButton(QPushButton):
    """Button with an MDI icon and a text label side-by-side."""

    def __init__(
        self,
        text:           str,
        style_key:      str,
        text_position:  str,
        width:          int,
        icon,
        func,
        bgcolor:        str = "transparent",
        radius:         int = 2,
        icon_color:     str = "white",
    ):
        super().__init__()
        self.setFixedWidth(width)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        style  = STYLES[style_key]
        q_font = make_font(style["size"], style.get("bold", False))
        color  = style["color"]

        if isinstance(icon, QIcon):
            q_icon = icon
        elif isinstance(icon, str):
            q_icon = resolve_to_icon(icon, color=icon_color)
        else:
            q_icon = QIcon()

        self.setIcon(q_icon)
        self.setIconSize(QSize(28, 28))
        self.setFont(q_font)
        self.setText(text)

        layout_dir = (
            Qt.LayoutDirection.RightToLeft
            if text_position == "left"
            else Qt.LayoutDirection.LeftToRight
        )
        self.setLayoutDirection(layout_dir)

        set_style(self, "buttons", "icon-text-button", override={
            "*": {"background": bgcolor, "color": color, "border-radius": f"{radius}px"},
        })

        self.clicked.connect(lambda _checked=False: func())

    def set_text(self, text: str) -> None:
        self.setText(text)


# ── Dropdown Button ───────────────────────────────────────────────────────────

class DropdownButton(QToolButton):
    """A flat icon button that opens a popup menu."""

    def __init__(
        self,
        icon,
        items:      list[tuple[str, callable]],
        size:       int  = 40,
        visible:    bool = True,
        icon_color: str  = "white",
        data             = None,
    ):
        super().__init__()
        self._data  = data
        self._size  = size
        self._items = items

        self.setFixedSize(size * 2, size * 2)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        if isinstance(icon, QIcon):
            q_icon = icon
        elif isinstance(icon, str):
            q_icon = resolve_to_icon(icon, color=icon_color)
        else:
            q_icon = QIcon()

        self.setIcon(q_icon)
        self.setIconSize(QSize(size + 8, size + 8))

        set_style(self, "buttons", "dropdown-button",
                  override={"*": {"border-radius": f"{size}px"}})

        self.setVisible(visible)
        self._menu = QMenu(self)
        set_style(self._menu, "buttons", "dropdown-menu")
        self._rebuild_menu()
        self.setMenu(self._menu)

    def _rebuild_menu(self) -> None:
        self._menu.clear()
        for label, cb in self._items:
            action = self._menu.addAction(label)
            action.triggered.connect(cb)

    def set_items(self, items: list[tuple[str, callable]]) -> None:
        self._items = items
        self._rebuild_menu()

    def clear_items(self) -> None:
        self._items = []
        self._menu.clear()

    @property
    def data(self):
        return self._data