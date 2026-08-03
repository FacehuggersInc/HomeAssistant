"""
Building the arguments a runnable is called with.

A scrollable list of rows. Each row is a name, a kind, and a value; picking
`json` turns the value into a nested list of the same rows, so an object can
be as deep as it needs without a second kind of editor.

The rows start filled in from the function's own signature - names it declared
and defaults it would have used - because those are the two things a person
cannot see from outside and would otherwise have to guess.
"""

from __future__ import annotations

import json
from typing import Any, Callable, TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QSizePolicy, QComboBox, QPushButton, QLineEdit,
)

from src.styling import (
    set_style, make_font, SIZES, style_scrollbar, get_style_sheet)
from src.ui.keyboard import make_keyboard
from src.ui.controls.buttons import ActionButton, IconButton

if TYPE_CHECKING:
    from src.main import Client


#What a value can be. `json` nests; the rest are leaves.
KINDS = [
    ("text", "Text"),
    ("number", "Number"),
    ("boolean", "Yes / no"),
    ("none", "Nothing"),
    ("json", "Object"),
]

#How deep an object may nest. A limit rather than none: a person who has built
#six levels of object in a tile dialog has outgrown this and wants a plugin,
#which is exactly what the warning at the top says.
MAX_DEPTH = 4


def coerce(kind: str, raw: Any) -> Any:
    """One row's stored value, as the kind it claims to be."""
    if kind == "none":
        return None
    if kind == "boolean":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if kind == "number":
        try:
            text = str(raw).strip()
            return int(text) if text.lstrip("-").isdigit() else float(text)
        except (TypeError, ValueError):
            return 0
    if kind == "json":
        return raw if isinstance(raw, dict) else {}
    return "" if raw is None else str(raw)


def kind_of(value: Any) -> str:
    """The kind a saved value looks like, for rebuilding an editor from it."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "json"
    return "text"


def describe(kind: str, value: Any) -> str:
    """What a row shows for its value when it is not being edited."""
    if kind == "none":
        return "nothing"
    if kind == "boolean":
        return "yes" if value else "no"
    if kind == "json":
        count = len(value or {})
        return f"{count} field" + ("s" if count != 1 else "")
    text = str(value)
    if not text:
        return "empty"
    return text if len(text) <= 40 else text[:39] + "\u2026"


class _Field(QLineEdit):
    """
    A line edit the keyboard writes into, and nothing else.

    The keyboard needs a target with `text()` and `setText()` and reports by
    writing to it, so the shortest honest way to hear an answer is a real
    field that is never shown. It stays parented to nothing and is dropped
    once it has answered.
    """

    def __init__(self, current: str, took: Callable):
        super().__init__(str(current or ""))
        self._took = took
        self.setVisible(False)
        self.textChanged.connect(self._changed)

    def _changed(self, text: str) -> None:
        try:
            self._took(text)
        finally:
            # One answer. The keyboard writes once and closes; anything after
            # that is not this question being answered again.
            try:
                self.textChanged.disconnect(self._changed)
            except TypeError:
                pass


class ArgumentRow(QFrame):
    """One argument: its name, what kind of thing it is, and its value."""

    HEIGHT = 56
    CONTROL_H = 40

    #Stated rather than inherited - see the comment where these are applied.
    NAME_CSS = """
        QPushButton { background: transparent; border: none;
                      color: #e8ecf4; text-align: left; padding: 0 4px; }
        QPushButton:hover { color: #ffffff; }
    """
    VALUE_CSS = """
        QPushButton { background: rgba(255,255,255,14);
                      border: 1px solid rgba(255,255,255,26);
                      border-radius: 8px; color: #e8ecf4;
                      text-align: left; padding: 0 12px; }
        QPushButton:hover { border-color: rgba(255,255,255,60); }
        QPushButton:disabled { color: rgba(232,236,244,90); }
    """
    ADD_CSS = """
        QPushButton { background: transparent;
                      border: 1px dashed rgba(255,255,255,40);
                      border-radius: 8px; color: rgba(232,236,244,150);
                      text-align: left; padding: 0 10px; }
        QPushButton:hover { border-color: rgba(255,255,255,90);
                            color: #e8ecf4; }
    """
    DROP_CSS = """
        QPushButton { background: transparent; border: none;
                      color: rgba(232,236,244,110);
                      font-size: 15px; border-radius: 13px; }
        QPushButton:hover { background: rgba(224,85,85,60); color: #ffffff; }
    """

    def __init__(self, client: "Client", name: str, kind: str, value: Any,
                 on_changed: Callable, on_remove: Callable = None,
                 depth: int = 0, required: bool = False):
        super().__init__()
        self.client = client
        self.name = str(name or "")
        self.kind = kind if kind in dict(KINDS) else "text"
        self.value = value
        self.depth = depth
        self.required = required
        self.on_changed = on_changed
        self.on_remove = on_remove
        #Rows of a nested object, when this one is `json`.
        self.children_rows: list = []

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(self, "settings", "setting-block")

        column = QVBoxLayout(self)
        column.setContentsMargins(10, 6, 10, 6)
        column.setSpacing(4)

        line = QHBoxLayout()
        line.setSpacing(8)

        # Every colour stated. A bare QPushButton or QComboBox picks up the
        # platform palette, which on this one is black on grey - readable on
        # a desktop and invisible on a dark panel.
        self.name_button = QPushButton(self._name_text())
        self.name_button.setFont(make_font(SIZES.S2, bold=True))
        self.name_button.setMinimumWidth(150)
        self.name_button.setFixedHeight(self.CONTROL_H)
        self.name_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.name_button.setStyleSheet(self.NAME_CSS)
        self.name_button.clicked.connect(self._edit_name)
        line.addWidget(self.name_button)

        self.kind_box = QComboBox()
        for key, label in KINDS:
            if key == "json" and depth >= MAX_DEPTH:
                # No deeper. Offering it and then refusing is worse than not
                # offering it.
                continue
            self.kind_box.addItem(label, key)
        index = self.kind_box.findData(self.kind)
        if index >= 0:
            self.kind_box.setCurrentIndex(index)
        self.kind_box.currentIndexChanged.connect(self._kind_picked)
        self.kind_box.setFixedWidth(140)
        self.kind_box.setFixedHeight(self.CONTROL_H)
        self.kind_box.setFont(make_font(SIZES.S1))
        self.kind_box.setStyleSheet(get_style_sheet("settings_combobox"))
        line.addWidget(self.kind_box)

        self.value_button = QPushButton(describe(self.kind, self.value))
        self.value_button.setFont(make_font(SIZES.S1))
        self.value_button.setFixedHeight(self.CONTROL_H)
        self.value_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.value_button.setStyleSheet(self.VALUE_CSS)
        self.value_button.clicked.connect(self._edit_value)
        line.addWidget(self.value_button, stretch=1)

        if on_remove is not None and not required:
            # Quiet until it is wanted. A row's own delete is not the thing
            # somebody came to this dialog to press, and at the size of the
            # controls beside it that is what it looked like.
            drop = QPushButton("\u2715")
            drop.setFixedSize(26, 26)
            drop.setCursor(Qt.CursorShape.PointingHandCursor)
            drop.setStyleSheet(self.DROP_CSS)
            drop.clicked.connect(lambda: on_remove(self))
            line.addWidget(drop)
        column.addLayout(line)

        # Where a nested object's own rows go.
        self.nest_host = QWidget()
        set_style(self.nest_host, "common", "transparent")
        self.nest = QVBoxLayout(self.nest_host)
        self.nest.setContentsMargins(22, 2, 0, 2)
        self.nest.setSpacing(4)
        column.addWidget(self.nest_host)

        # An object's own Add. Inside the object it belongs to, so which one
        # a field is being added to is where the button is rather than
        # something to work out.
        self.add_field = QPushButton("+  Add a field")
        self.add_field.setFont(make_font(SIZES.S1))
        self.add_field.setFixedHeight(30)
        self.add_field.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_field.setStyleSheet(self.ADD_CSS)
        self.add_field.clicked.connect(self._add_field)
        column.addWidget(self.add_field)

        self._sync_nest()

    ## -- name

    def _name_text(self) -> str:
        return f"{self.name}{' *' if self.required else ''}" or "(unnamed)"

    def _edit_name(self) -> None:
        if self.required:
            # Named by the function itself. Renaming it means the call misses
            # an argument it has to have.
            self.client.simple_notify(
                "mdi.information-outline", "Arguments",
                f"'{self.name}' is required by name and cannot be renamed.")
            return

        self._ask("Argument name", "The name the function expects.",
                  self.name, self._took_name)

    def _took_name(self, text: str) -> None:
        self.name = str(text or "").strip()
        self.name_button.setText(self._name_text())
        self.on_changed()

    def _ask(self, label: str, description: str, current: Any,
             took: Callable, numeric: bool = False) -> None:
        """
        Open the keyboard on a field, and nothing in between.

        Straight to it. Going through an input dialog means a press opens a
        dialog whose only content is a field, and pressing THAT opens the
        keyboard - two taps and two overlays to type one word.
        """
        holder = _Field("" if current is None else str(current), took)
        self.client.dialog(make_keyboard(
            self.client, holder, "int" if numeric else "text",
            label=label, description=description))

    ## -- kind

    def _kind_picked(self) -> None:
        chosen = self.kind_box.currentData()
        if chosen == self.kind:
            return
        self.kind = chosen
        # Converted rather than cleared, so switching kind by accident does
        # not throw away what was typed.
        self.value = coerce(self.kind, self.value)
        self.value_button.setText(describe(self.kind, self.value))
        self._sync_nest()
        self.on_changed()

    ## -- value

    def _edit_value(self) -> None:
        if self.kind == "none":
            return
        if self.kind == "json":
            # Nothing. An object is edited by its own Add button and by the
            # remove on each field - pressing a summary that reads "2 fields"
            # to add a third is a control that does not say what it does.
            return
        if self.kind == "boolean":
            self.value = not bool(self.value)
            self.value_button.setText(describe(self.kind, self.value))
            self.on_changed()
            return

        self._ask(self.name or "Value", "What this argument is set to.",
                  self.value, self._took_value,
                  numeric=(self.kind == "number"))

    def _took_value(self, text: str) -> None:
        self.value = coerce(self.kind, text)
        self.value_button.setText(describe(self.kind, self.value))
        self.on_changed()

    ## -- nesting

    def _sync_nest(self) -> None:
        """Show the object's own rows, or hide the space entirely."""
        while self.nest.count():
            item = self.nest.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.children_rows = []

        if self.kind != "json":
            self.nest_host.setVisible(False)
            self.add_field.setVisible(False)
            self.value_button.setEnabled(self.kind != "none")
            return

        self.nest_host.setVisible(True)
        self.add_field.setVisible(True)
        # A summary, not a button. The object is edited below it.
        self.value_button.setEnabled(False)
        self.value_button.setText(describe(self.kind, self.value))

        for name, value in (self.value or {}).items():
            row = ArgumentRow(self.client, name, kind_of(value), value,
                              on_changed=self._nest_changed,
                              on_remove=self._drop_field,
                              depth=self.depth + 1)
            self.nest.addWidget(row)
            self.children_rows.append(row)

    def _add_field(self) -> None:
        self._ask("Field name", f"A field inside '{self.name}'.", "",
                  self._took_field)

    def _took_field(self, text: str) -> None:
        name = str(text or "").strip()
        if not name:
            return
        values = dict(self.value or {})
        values[name] = ""
        self.value = values
        self._sync_nest()
        self.on_changed()

    def _drop_field(self, row: "ArgumentRow") -> None:
        values = dict(self.value or {})
        values.pop(row.name, None)
        self.value = values
        self._sync_nest()
        self.on_changed()

    def _nest_changed(self) -> None:
        self.value = {row.name: row.current()
                      for row in self.children_rows if row.name}
        self.value_button.setText(describe(self.kind, self.value))
        self.on_changed()

    ## -- reading

    def current(self) -> Any:
        if self.kind == "json":
            return {row.name: row.current()
                    for row in self.children_rows if row.name}
        return coerce(self.kind, self.value)


class ArgumentList(QWidget):
    """
    Every argument, in a scrollable list with an add button.

    Built from the signature where there is one. A row the function declared
    is marked required and keeps its name; anything added by hand can be
    called whatever the person likes, because a function taking `**kwargs`
    accepts names this cannot know.
    """

    def __init__(self, client: "Client", arguments: list = None,
                 values: dict = None, on_changed: Callable = None):
        super().__init__()
        self.client = client
        self.on_changed = on_changed or (lambda: None)
        self.rows: list = []

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

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
        # No minimum, like the rules list beside it. A scroll area is the one
        # thing in a dialog that can always give up height - that is what it
        # is for - and a minimum here is what stops a clamped dialog fitting
        # a small screen.
        scroll.setMinimumHeight(0)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Expanding)
        style_scrollbar(scroll)
        scroll.setWidget(host)
        column.addWidget(scroll, stretch=1)

        self.empty = QLabel("This one takes no arguments.")
        self.empty.setFont(make_font(SIZES.S1))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.empty, "common", "text-muted")
        column.addWidget(self.empty)

        add = ActionButton("mdi.plus", "Add an argument", self._add,
                           kind="secondary")
        column.addWidget(add)

        self._fill(arguments or [], values or {})

    def _fill(self, arguments: list, values: dict) -> None:
        declared = []
        for argument in arguments:
            name = argument.get("name") if isinstance(argument, dict) else argument.name
            kind = argument.get("kind") if isinstance(argument, dict) else argument.kind
            required = (argument.get("required") if isinstance(argument, dict)
                        else argument.required)
            default = (argument.get("default") if isinstance(argument, dict)
                       else argument.default)
            value = values.get(name, default)
            declared.append(name)
            self._append(name, kind, value, required=bool(required))

        # Anything SAVED that the signature does not declare.
        #
        # An argument added by hand is passed as a keyword and is not in the
        # function's signature, so building the list from the signature alone
        # gives it no row - and `values()` reads the rows, so the next save
        # writes it out of existence. It survived being set and disappeared
        # the second time the dialog was opened.
        for name, value in (values or {}).items():
            if name in declared:
                continue
            # No declared kind - there is nothing declaring it - so the
            # editor is chosen from the value itself, the same way a nested
            # object rebuilds its rows.
            self._append(name, kind_of(value), value)
        self._sync_empty()

    def _append(self, name: str, kind: str, value: Any,
                required: bool = False) -> None:
        row = ArgumentRow(self.client, name, kind, value,
                          on_changed=self._changed, on_remove=self._remove,
                          required=required)
        self.list_layout.addWidget(row)
        self.rows.append(row)

    def _add(self) -> None:
        holder = _Field("", self._took_new)
        self.client.dialog(make_keyboard(
            self.client, holder, "text", label="Argument name",
            description="The name the function expects. Anything extra is "
                        "passed as a keyword, which only works if it "
                        "accepts one."))

    def _took_new(self, text: str) -> None:
        name = str(text or "").strip()
        if not name:
            return
        self._append(name, "text", "")
        self._sync_empty()
        self._changed()

    def _remove(self, row: ArgumentRow) -> None:
        if row in self.rows:
            self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._sync_empty()
        self._changed()

    def _sync_empty(self) -> None:
        self.empty.setVisible(not self.rows)

    def _changed(self) -> None:
        self.on_changed()

    ## -- reading

    def values(self) -> dict:
        """What the call should be made with."""
        return {row.name: row.current() for row in self.rows if row.name}

    def as_json(self) -> str:
        try:
            return json.dumps(self.values(), indent=1)
        except (TypeError, ValueError):
            return "{}"
