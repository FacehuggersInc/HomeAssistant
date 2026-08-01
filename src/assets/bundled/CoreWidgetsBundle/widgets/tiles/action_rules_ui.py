"""
Editing the rules that decide how a tile looks.

A list, tried top to bottom, first match wins. Order is the whole of the
logic, so a rule can be moved up and down and the list says plainly that the
one above wins - which is easier to reason about than any arrangement where
the order does not show.
"""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QSizePolicy, QComboBox, QPushButton, QScroller,
)

from src.styling import (
    set_style, make_font, SIZES, style_scrollbar, get_style_sheet)
from src.ui.keyboard import make_keyboard
from src.ui.icons import icon
from PyQt6.QtGui import QFontMetrics
from .action_arguments import _Field
from .action_rules import Rule, TESTS, TEST_LABELS
from . import action_rules

if TYPE_CHECKING:
    from src.main import Client


#What a rule can paint. Empty means "leave the tile as it is".
LOOK_COLOURS = [
    ("", "unchanged"),
    ("#3ec08a", "green"),
    ("#e8c35a", "amber"),
    ("#e0855a", "orange"),
    ("#e05555", "red"),
    ("#4f9de0", "blue"),
    ("#9d7ae0", "violet"),
    ("rgba(232,236,244,90)", "dimmed"),
]

LOOK_ICONS = [
    "", "mdi.check-circle", "mdi.close-circle", "mdi.alert-circle",
    "mdi.help-circle-outline", "mdi.power", "mdi.lightbulb-on",
    "mdi.lightbulb-outline", "mdi.lock", "mdi.lock-open-variant",
    "mdi.door-open", "mdi.door-closed", "mdi.wifi", "mdi.wifi-off",
    "mdi.battery", "mdi.battery-alert", "mdi.thermometer", "mdi.water",
]

#The tests that need something to compare against.
NEEDS_VALUE = ("equals", "contains", "above", "below")


class RuleRow(QFrame):
    """One rule: when it holds, and what the tile looks like then."""

    CONTROL_H = 36

    FLAT_CSS = """
        QPushButton { background: rgba(255,255,255,14);
                      border: 1px solid rgba(255,255,255,26);
                      border-radius: 8px; color: #e8ecf4;
                      text-align: left; padding: 0 10px; }
        QPushButton:hover { border-color: rgba(255,255,255,60); }
        QPushButton:disabled { color: rgba(232,236,244,70);
                               border-color: rgba(255,255,255,14); }
    """
    QUIET_CSS = """
        QPushButton { background: transparent; border: none;
                      color: rgba(232,236,244,110); font-size: 15px;
                      border-radius: 13px; }
        QPushButton:hover { background: rgba(255,255,255,26);
                            color: #ffffff; }
    """
    DROP_CSS = QUIET_CSS.replace("rgba(255,255,255,26);\n                            color",
                                 "rgba(224,85,85,60);\n                            color")

    #The width of the two captions, so When and Show line up under each
    #other. Measured rather than guessed - 42px was fifteen short of either
    #word and clipped both.
    CAPTION_W = 0

    def _caption(self, text: str) -> QLabel:
        """A column heading, wide enough for the widest of them."""
        label = QLabel(text)
        label.setFont(make_font(SIZES.S1))
        set_style(label, "common", "text-muted")
        if not RuleRow.CAPTION_W:
            metrics = QFontMetrics(label.font())
            RuleRow.CAPTION_W = max(metrics.horizontalAdvance(word)
                                    for word in ("When", "Show")) + 8
        label.setFixedWidth(RuleRow.CAPTION_W)
        return label

    def __init__(self, client: "Client", rule: Rule, on_changed: Callable,
                 on_remove: Callable, on_move: Callable, paths: list = None):
        super().__init__()
        self.client = client
        self.rule = rule
        self.on_changed = on_changed
        self.on_remove = on_remove
        self.on_move = on_move
        self.paths = paths or []

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(self, "settings", "setting-block")
        # Two rows of controls at 36px plus the margins between them. Fixed,
        # so a list of rules scrolls instead of every rule shrinking until
        # none of them is readable.
        self.setFixedHeight(self.CONTROL_H * 2 + 30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        column = QVBoxLayout(self)
        column.setContentsMargins(10, 8, 10, 8)
        column.setSpacing(6)

        column.addLayout(self._condition())
        column.addLayout(self._look())

    ## -- when it holds

    def _condition(self) -> QHBoxLayout:
        line = QHBoxLayout()
        line.setSpacing(8)

        line.addWidget(self._caption("When"))

        self.path_button = QPushButton(self.rule.path or "the whole answer")
        self.path_button.setFont(make_font(SIZES.S1))
        self.path_button.setFixedHeight(self.CONTROL_H)
        self.path_button.setMinimumWidth(180)
        self.path_button.setStyleSheet(self.FLAT_CSS)
        self.path_button.clicked.connect(self._edit_path)
        line.addWidget(self.path_button, stretch=1)

        self.test_box = QComboBox()
        for key, label in TESTS:
            self.test_box.addItem(label, key)
        index = self.test_box.findData(self.rule.test)
        if index >= 0:
            self.test_box.setCurrentIndex(index)
        self.test_box.setFixedHeight(self.CONTROL_H)
        self.test_box.setFixedWidth(150)
        self.test_box.setFont(make_font(SIZES.S1))
        self.test_box.setStyleSheet(get_style_sheet("settings_combobox"))
        self.test_box.currentIndexChanged.connect(self._test_picked)
        line.addWidget(self.test_box)

        self.against_button = QPushButton(self.rule.against or "\u2014")
        self.against_button.setFont(make_font(SIZES.S1))
        self.against_button.setFixedHeight(self.CONTROL_H)
        self.against_button.setFixedWidth(130)
        self.against_button.setStyleSheet(self.FLAT_CSS)
        self.against_button.clicked.connect(self._edit_against)
        line.addWidget(self.against_button)
        self._sync_against()

        for glyph, why, call in (("\u2191", "up", lambda: self.on_move(self, -1)),
                                 ("\u2193", "down", lambda: self.on_move(self, 1))):
            button = QPushButton(glyph)
            button.setFixedSize(26, 26)
            button.setStyleSheet(self.QUIET_CSS)
            button.setToolTip(f"Move {why}")
            button.clicked.connect(call)
            line.addWidget(button)

        drop = QPushButton("\u2715")
        drop.setFixedSize(26, 26)
        drop.setStyleSheet(self.DROP_CSS)
        drop.clicked.connect(lambda: self.on_remove(self))
        line.addWidget(drop)
        return line

    def _sync_against(self) -> None:
        """Only the tests that compare against something show a value."""
        needed = self.rule.test in NEEDS_VALUE
        self.against_button.setEnabled(needed)
        self.against_button.setText(
            self.rule.against or ("\u2014" if not needed else "anything"))

    def _test_picked(self) -> None:
        self.rule.test = self.test_box.currentData()
        self._sync_against()
        self.on_changed()

    def _edit_path(self) -> None:
        holder = _Field(self.rule.path, self._took_path)
        self.client.dialog(make_keyboard(
            self.client, holder, "text", label="Which part of the answer?",
            description="A dotted path - weather.today.high, items.0.name. "
                        "Empty means the whole answer."))

    def _took_path(self, text: str) -> None:
        self.rule.path = str(text or "").strip()
        self.path_button.setText(self.rule.path or "the whole answer")
        self.on_changed()

    def _edit_against(self) -> None:
        holder = _Field(self.rule.against, self._took_against)
        self.client.dialog(make_keyboard(
            self.client, holder, "text", label="Compared with",
            description=f"What the value is checked against."))

    def _took_against(self, text: str) -> None:
        self.rule.against = str(text or "").strip()
        self._sync_against()
        self.on_changed()

    ## -- how it looks then

    def _look(self) -> QHBoxLayout:
        line = QHBoxLayout()
        line.setSpacing(8)

        line.addWidget(self._caption("Show"))

        self.label_button = QPushButton(self.rule.label or "the tile's name")
        self.label_button.setFont(make_font(SIZES.S1))
        self.label_button.setFixedHeight(self.CONTROL_H)
        self.label_button.setStyleSheet(self.FLAT_CSS)
        self.label_button.clicked.connect(self._edit_label)
        line.addWidget(self.label_button, stretch=1)

        self.icon_box = self._picker(
            [(name, name.replace("mdi.", "") or "unchanged")
             for name in LOOK_ICONS],
            self.rule.icon, self._icon_picked, 170)
        line.addWidget(self.icon_box)

        self.ink_box = self._picker(LOOK_COLOURS, self.rule.ink,
                                    self._ink_picked, 130, "Icon")
        line.addWidget(self.ink_box)

        self.border_box = self._picker(LOOK_COLOURS, self.rule.border,
                                       self._border_picked, 130, "Edge")
        line.addWidget(self.border_box)

        self.background_box = self._picker(LOOK_COLOURS, self.rule.background,
                                           self._background_picked, 130, "Fill")
        line.addWidget(self.background_box)
        return line

    def _picker(self, options: list, chosen: str, on_pick: Callable,
                width: int, prefix: str = "") -> QComboBox:
        box = QComboBox()
        for value, label in options:
            box.addItem(f"{prefix} {label}".strip(), value)
        index = box.findData(chosen)
        box.setCurrentIndex(index if index >= 0 else 0)
        box.setFixedHeight(self.CONTROL_H)
        box.setFixedWidth(width)
        box.setFont(make_font(SIZES.S1))
        box.setStyleSheet(get_style_sheet("settings_combobox"))
        box.currentIndexChanged.connect(on_pick)
        return box

    def _edit_label(self) -> None:
        holder = _Field(self.rule.label, self._took_label)
        self.client.dialog(make_keyboard(
            self.client, holder, "text", label="What should the tile say?",
            description="Left empty, the tile keeps its own name. "
                        "{value} is replaced by whatever the path above "
                        "reads - so \"{value}C\" on a temperature shows 22C."))

    def _took_label(self, text: str) -> None:
        self.rule.label = str(text or "").strip()
        self.label_button.setText(self.rule.label or "the tile's name")
        self.on_changed()

    def _icon_picked(self) -> None:
        self.rule.icon = self.icon_box.currentData()
        self.on_changed()

    def _ink_picked(self) -> None:
        self.rule.ink = self.ink_box.currentData()
        self.on_changed()

    def _border_picked(self) -> None:
        self.rule.border = self.border_box.currentData()
        self.on_changed()

    def _background_picked(self) -> None:
        self.rule.background = self.background_box.currentData()
        self.on_changed()


class RuleList(QWidget):
    """Every rule, in the order they are tried."""

    def __init__(self, client: "Client", rules: list = None,
                 on_changed: Callable = None):
        super().__init__()
        self.client = client
        self.on_changed = on_changed or (lambda: None)
        self.rows: list = []
        self.paths: list = []

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        blurb = QLabel(
            "Tried top to bottom. The first one that holds decides how the "
            "tile looks; anything below it is not consulted. A name may use "
            "{value} to show what its path read.")
        blurb.setFont(make_font(SIZES.S1))
        blurb.setWordWrap(True)
        set_style(blurb, "common", "text-muted")
        column.addWidget(blurb)

        host = QWidget()
        set_style(host, "common", "transparent")
        self.list_layout = QVBoxLayout(host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # No minimum. A minimum on a scroll area inside a column that is
        # already short is a demand the layout meets by squeezing whatever is
        # under it - which is how the Add and Suggest buttons ended up behind
        # the last rule.
        scroll.setMinimumHeight(0)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Expanding)
        style_scrollbar(scroll)
        try:
            QScroller.grabGesture(
                scroll.viewport(),
                QScroller.ScrollerGestureType.LeftMouseButtonGesture)
        except Exception:
            pass
        scroll.setWidget(host)
        column.addWidget(scroll, stretch=1)

        self.empty = QLabel(
            "No rules. The tile shows its own name and colour whatever comes "
            "back.")
        self.empty.setFont(make_font(SIZES.S1))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.empty, "common", "text-muted")
        column.addWidget(self.empty)

        # Held at their own height, so the list above gives way rather than
        # the buttons being squeezed out from under it.
        bar = QWidget()
        bar.setFixedHeight(42)
        bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        set_style(bar, "common", "transparent")
        buttons = QHBoxLayout(bar)
        buttons.setContentsMargins(0, 6, 0, 0)
        buttons.setSpacing(8)

        add = QPushButton("+  Add a rule")
        add.setFont(make_font(SIZES.S1))
        add.setFixedHeight(36)
        add.setStyleSheet(RuleRow.FLAT_CSS)
        add.clicked.connect(lambda: self.add(Rule()))
        buttons.addWidget(add)

        self.suggest_button = QPushButton("Suggest from the last answer")
        self.suggest_button.setFont(make_font(SIZES.S1))
        self.suggest_button.setFixedHeight(36)
        self.suggest_button.setStyleSheet(RuleRow.FLAT_CSS)
        self.suggest_button.clicked.connect(self._suggest)
        buttons.addWidget(self.suggest_button)
        column.addWidget(bar, stretch=0)

        #Set by the dialog after a test, so suggestions have something real.
        self.answer = None
        self.answer_path = ""

        for raw in rules or []:
            self.add(Rule.from_dict(raw) if isinstance(raw, dict) else raw,
                     quiet=True)
        self._sync_empty()

    ## -- the list

    def add(self, rule: Rule, quiet: bool = False) -> None:
        row = RuleRow(self.client, rule, self._changed, self._remove,
                      self._move, self.paths)
        self.list_layout.addWidget(row)
        self.rows.append(row)
        self._sync_empty()
        if not quiet:
            self._changed()

    def _remove(self, row: RuleRow) -> None:
        if row in self.rows:
            self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._sync_empty()
        self._changed()

    def _move(self, row: RuleRow, by: int) -> None:
        """
        Move one up or down, which is the only way to change what wins.

        Rebuilt rather than reordered in place: the layout holds them in the
        order they were added, and swapping two widgets inside it is more
        fiddly than laying the same rows out again.
        """
        if row not in self.rows:
            return
        at = self.rows.index(row)
        to = max(0, min(len(self.rows) - 1, at + by))
        if to == at:
            return
        self.rows.insert(to, self.rows.pop(at))
        for existing in self.rows:
            self.list_layout.removeWidget(existing)
        for existing in self.rows:
            self.list_layout.addWidget(existing)
        self._changed()

    def _suggest(self) -> None:
        if self.answer is None:
            self.client.simple_notify(
                "mdi.information-outline", "Rules",
                "Try the action first - the suggestions come from what it "
                "answered with.")
            return
        offered = action_rules.suggest(self.answer, self.answer_path)
        if not offered:
            self.client.simple_notify(
                "mdi.information-outline", "Rules",
                "Nothing obvious to suggest for that answer.")
            return
        for rule in offered:
            self.add(rule, quiet=True)
        self._changed()

    def _sync_empty(self) -> None:
        self.empty.setVisible(not self.rows)

    def _changed(self) -> None:
        self.on_changed()

    ## -- reading

    def values(self) -> list:
        return [row.rule.to_dict() for row in self.rows]
