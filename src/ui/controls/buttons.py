from __future__ import annotations

from PyQt6.QtWidgets import (
    QPushButton, QToolButton, QMenu, QSizePolicy, QWidget, QHBoxLayout,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon, QFontMetrics

from src.styling import STYLES, SIZES, make_font, add_text_shadow, set_style
from src.ui.icons import icon as resolve_to_icon, resolve as resolve_name


# ── Icon Button ───────────────────────────────────────────────────────────────

class IconButton(QPushButton):

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
        self._set_icon(icon_arg, color, self._size)

    @property
    def data(self):
        return self._data


# ── Icon + Text Button ────────────────────────────────────────────────────────

class IconAndTextButton(QPushButton):

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
        """
        Entries are (label, callback), or (label, callback, icon).

        The three-part form exists so a menu can carry the same icons the
        buttons it replaced did - a list of bare words is harder to pick from
        than the row of buttons it came from, which defeats the point.
        """
        self._menu.clear()
        for entry in self._items:
            label, cb = entry[0], entry[1]
            glyph = entry[2] if len(entry) > 2 else None
            action = self._menu.addAction(label)
            if glyph:
                action.setIcon(resolve_to_icon(glyph, color="#e8ecf4")
                               if isinstance(glyph, str) else glyph)
            action.triggered.connect(lambda _=False, fn=cb: fn())

    def set_items(self, items: list[tuple[str, callable]]) -> None:
        self._items = items
        self._rebuild_menu()

    def clear_items(self) -> None:
        self._items = []
        self._menu.clear()

    @property
    def data(self):
        return self._data

class ActionButton(QPushButton):
    """
    An icon and a label, at the same size as every other one.

    Built because every page was making its own. A `QPushButton` with
    `setFixedHeight(38)` written out at each call site drifts - one page uses 38,
    the next 40, a third leaves it to the layout - and a row of them ends up
    uneven for no reason anybody chose. Worse, none of them carried an icon, so
    a page of buttons read as a wall of similar words.

    `kind` picks the meaning rather than the colour: what a button does to the
    thing it belongs to is the decision, and the palette follows from it.

    * `primary`     - the thing this row is for. Join, Connect, Save.
    * `secondary`   - a reasonable alternative. Disconnect, Rename, Cancel.
    * `destructive` - loses something. Forget, Revoke, Delete.
    * `quiet`       - navigation and toggles that change nothing.
    """

    HEIGHT = 40
    ICON = 18
    #A FLOOR, not a width. Below this a short label makes a stub of a button
    #beside a long one, so "Join" is padded out to match "Disconnect".
    MIN_WIDTH = 118
    #What the label needs beyond itself: the glyph, the gap after it, and the
    #padding the stylesheet puts either side.
    PADDING = 26
    KINDS = ("primary", "secondary", "destructive", "quiet")

    def __init__(self, icon, label: str, func=None,
                 kind: str = "secondary", size: int = None,
                 min_width: int = None, enabled: bool = True,
                 icon_size: int = None):
        super().__init__()
        self.kind = kind if kind in self.KINDS else "secondary"
        self._icon_name = icon
        self._label = str(label or "")

        self.setFixedHeight(int(size or self.HEIGHT))
        glyph = int(icon_size or self.ICON)
        # Measured, not assumed.
        #
        # 118 was a floor that most labels overflow - "Copy Key" needs 167 and
        # "Save and Return" 254 - and a QPushButton squeezed below its text
        # clips rather than shrinking the text. The floor still applies, so a
        # row of short labels lines up; a long one simply asks for what it
        # needs.
        wanted = self.MIN_WIDTH
        if label:
            metrics = QFontMetrics(make_font(SIZES.S1, bold=True))
            wanted = max(wanted,
                         glyph + self.PADDING
                         + metrics.horizontalAdvance(f" {label}"))
        self.setMinimumWidth(int(wanted if min_width is None
                                 else max(wanted, min_width)))
        # Fixed vertically, or a button in a card with spare height stretches
        # into a slab; Preferred across, so a long label still fits.
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFont(make_font(SIZES.S1, bold=True))
        # Scales with the button. A taller button with a bigger label and an
        # 18px glyph beside it reads as a small icon that was forgotten about.
        self.setIconSize(QSize(glyph, glyph))
        self.setText(f" {self._label}" if self._label else "")
        self._apply()

        self.setEnabled(bool(enabled))
        if func is not None:
            self.clicked.connect(lambda _=False: func())

    def _apply(self) -> None:
        colour = {
            "primary":     "#0f2418",
            "secondary":   "#e8ecf4",
            "destructive": "#ffd9d9",
            "quiet":       "#b9bec9",
        }[self.kind]
        if self._icon_name:
            self.setIcon(resolve_to_icon(self._icon_name, color=colour))
        set_style(self, "buttons", f"action-{self.kind}")

    def set_kind(self, kind: str) -> None:
        """Change what the button means, and its look with it."""
        if kind in self.KINDS and kind != self.kind:
            self.kind = kind
            self._apply()

    def set_label(self, label: str, icon=None) -> None:
        self._label = str(label or "")
        self.setText(f" {self._label}" if self._label else "")
        if icon is not None:
            self._icon_name = icon
        self._apply()


def action_column(*buttons, slots: int = 2, spacing: int = 8) -> QWidget:
    """
    A fixed-width tray for a row's actions.

    Rows in a list do not all have the same actions - a saved network has
    Forget beside Join, a new one only has Join - so right-aligning them puts
    the last button in a different place on every row and the column zigzags
    down the page. Every button being the same width does not help: it is the
    *count* that differs.

    So the tray is always `slots` buttons wide, whatever it holds, and short
    rows are padded on the left. The right edge then lines up all the way down,
    and the primary action is always the last thing before it.
    """
    tray = QWidget()
    set_style(tray, "common", "transparent")
    row = QHBoxLayout(tray)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)

    given = [b for b in buttons if b is not None]

    # The slot width is a FLOOR, not the answer.
    #
    # Fixing the tray at MIN_WIDTH * slots and putting wider buttons in it
    # squeezes them below their own text, and a QPushButton too narrow for its
    # label clips rather than shrinking the text - which is why "Save and
    # Return" arrived as "Save and Retu".
    #
    # Rows in one list usually carry the same labels, so they still line up.
    # One that genuinely needs more gets more, because a row that is readable
    # and slightly out of line beats a tidy column of cut-off words.
    floor = (ActionButton.MIN_WIDTH * slots) + (spacing * max(0, slots - 1))
    needed = 0
    if given:
        needed = (sum(b.minimumWidth() for b in given)
                  + spacing * (len(given) - 1))
    tray.setFixedWidth(max(floor, needed))
    tray.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

    # Padded on the left, so what is present stays hard against the right edge.
    row.addStretch()
    for button in given:
        row.addWidget(button)
    return tray


def row_menu(client, title: str, items, size: int = 22) -> IconButton:
    """
    The actions for one row of a list, behind a single glyph.

    A row of labelled buttons works on a page that has one row. In a **list** it
    does not: every row repeats the same words, the words are the widest thing
    in each row, and on a narrow panel they are the first thing cut off. They
    are also the least useful part - nobody is reading them again by the third
    row.

    Tapping opens an `ActionSheet`, not a `QMenu`. A QMenu's items are the
    height of a line of text and it expects a press-drag-release that a finger
    does not perform; on a wall panel that is a row of targets a few
    millimetres tall. The sheet is the same list as full-width rows in a dialog
    this panel already knows how to centre and dim behind.

    `items` are (label, callback[, icon[, kind]]). None entries are dropped, so
    a caller can build the list conditionally.
    """
    entries = [i for i in items if i]

    def open_sheet():
        from src.ui.dialogs import ActionSheet
        client.dialog(ActionSheet(client, title, entries))

    return IconButton("dots-vertical", open_sheet, size=size,
                      color="#c8cedb")
