from __future__ import annotations
import socket
import platform
import copy
from threading import Thread
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QGridLayout,
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QScrollArea, QLineEdit, QTextEdit, QComboBox, QFrame, QSizePolicy,
    QScroller,
)
from PyQt6.QtCore import Qt, QSize, QPropertyAnimation, QEasingCurve, pyqtProperty, QUrl, QTimer
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QPixmap, QIcon, QDesktopServices

from src.mixins import mixin_target
from src.settings import Settings, scrub_secrets
from src import settings as setting_groups
from src.ui.page import PageFramework
from src.ui.widget import WidgetFramework
from src.ui.controls.buttons import ActionButton, action_column, row_menu
from src.ui.icons import Icons, icon, resolve_plugin_icon
from src.styling import line_height, COLORS, SIZES, make_font, set_style, get_style_sheet, style_scrollbar
from src.ui.keyboard import make_keyboard

if TYPE_CHECKING:
    from src.main import Client


FIELD_BG           = QColor(255, 255, 255, 40)
FIELD_BORDER       = QColor(255, 255, 255, 55)
FIELD_BORDER_FOCUS = QColor(COLORS.PRIMARY.LIGHT)

## HELPERS

def deps_venv_path() -> str:
    from src.plugin import dependencies as deps
    return deps.venv_path()


def format_name(name: str) -> str:
    # An empty name is a real case: a SettingBlock holding a plugin's own
    # widget has no setting and no key, and w[0] on the empty string it split
    # into raised IndexError - which the caller swallowed, so the block simply
    # never appeared and said nothing about why.
    if not name:
        return ""
    for sep in ("_", "-"):
        if sep in name:
            return " ".join(w.capitalize() for w in name.split(sep))
    return " ".join(f"{w[0].upper()}{w[1:]}" for w in name.split(" ") if w)


class GridBackground(QWidget):
    GRID_SPACING = 32
    DOT_RADIUS   = 1

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(255, 255, 255, 18)))
        p.setPen(Qt.GlobalColor.transparent)
        s, r = self.GRID_SPACING, self.DOT_RADIUS
        for x in range(s, self.width(), s):
            for y in range(s, self.height(), s):
                p.drawEllipse(x - r, y - r, r * 2, r * 2)

## CONTROLS

class ToggleSwitch(QWidget):
    W, H = 72, 36

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked  = checked
        self._thumb_x  = float(self.W - self.H + 4) if checked else float(4)
        self._callbacks: list = []

        # The third argument is the PARENT. Without it the animation belongs
        # to nothing, outlives the widget it animates, and fires `finished`
        # into an object that has gone - which inside a Qt signal aborts the
        # process rather than raising.
        self._anim = QPropertyAnimation(self, b"thumbX", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

    def _get_thumb(self) -> float: return self._thumb_x
    def _set_thumb(self, v: float) -> None:
        self._thumb_x = v; self.update()
    thumbX = pyqtProperty(float, _get_thumb, _set_thumb)

    def isChecked(self) -> bool: return self._checked

    def setChecked(self, val: bool) -> None:
        self._checked = val
        self._anim.stop()
        self._anim.setStartValue(self._thumb_x)
        self._anim.setEndValue(float(self.W - self.H + 4) if val else 4.0)
        self._anim.start()

    def mousePressEvent(self, event):
        self.setChecked(not self._checked)
        for cb in self._callbacks: cb(self._checked)

    def connect(self, cb): self._callbacks.append(cb)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QColor(COLORS.PRIMARY.LIGHT) if self._checked else QColor(FIELD_BG)
        border = QColor(COLORS.PRIMARY.LIGHT) if self._checked else QColor(FIELD_BORDER)
        p.setBrush(QBrush(track)); p.setPen(QPen(border, 1.5))
        p.drawRoundedRect(0, 0, self.W, self.H, self.H // 2, self.H // 2)
        p.setBrush(QBrush(QColor("white"))); p.setPen(Qt.GlobalColor.transparent)
        thumb_size = self.H - 8
        p.drawEllipse(int(self._thumb_x), 4, thumb_size, thumb_size)


class BodyField(QFrame):
    """
    Multi-line text for settings that hold a paragraph rather than a line -
    a system prompt, a template, a note. A QLineEdit shows one line of a
    three-line value and hides the rest behind the caret.
    """

    # A prompt or template is the usual content; the old 132px showed about
    # three lines of it.
    MIN_HEIGHT = 220
    MAX_HEIGHT = 460

    def __init__(self, setting, on_change=None, client=None,
                 label: str = "", description: str = ""):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(self, "settings", "body-field")
        self.client = client
        self._label = label or "Edit value"
        self._description = description or str(setting.get("description", "") or "")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        self.edit = QTextEdit()
        self.edit.setPlainText(str(setting["value"]))
        self.edit.setFont(make_font(SIZES.S2))
        self.edit.setAcceptRichText(False)
        self.edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.edit.setFrameShape(QFrame.Shape.NoFrame)
        set_style(self.edit, "settings", "body-input")
        layout.addWidget(self.edit)

        def changed():
            setting["value"] = self.edit.toPlainText()
            self._fit()
            if on_change:
                on_change()

        self.edit.textChanged.connect(changed)

        # Same as Field: read-only display, tap opens the editor.
        self.edit.setReadOnly(True)
        self.edit.viewport().setCursor(Qt.CursorShape.PointingHandCursor)

        def _open_keyboard(event=None):
            if event is not None and hasattr(event, "accept"):
                event.accept()
            if self.client is None:
                return
            kb = make_keyboard(self.client, self.edit, "body",
                               label=self._label, description=self._description)
            kb.show_keyboard()

        self.edit.mousePressEvent = _open_keyboard
        self.edit.focusInEvent = _open_keyboard
        self._fit()

    def _fit(self):
        # Grow with the content between sensible bounds, so a short prompt does
        # not leave a huge empty box and a long one does not push everything
        # else off the page.
        document = self.edit.document()
        document.setTextWidth(max(200, self.edit.viewport().width()))
        wanted = int(document.size().height()) + 18
        self.setFixedHeight(max(self.MIN_HEIGHT, min(self.MAX_HEIGHT, wanted)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


class Field(QWidget):

    def __init__(self, setting, index=None, is_numeric=False, prefix="", suffix="",
                 on_change=None, label="", description="", client=None,
                 setting_type=""):
        super().__init__()
        self._label = label or "Edit value"
        self._description = description or str(setting.get("description", "") or "")
        # Both of these used to be read off the setting object, which carries
        # neither - so every keyboard call raised AttributeError into a bare
        # `except Exception: pass` and the keyboard silently never opened.
        self._setting_type = setting_type or str(setting.get("type", "string"))
        self.setFixedHeight(44)
        self._bg     = QColor(FIELD_BG)
        self._border = QColor(FIELD_BORDER)
        self._radius = 6

        val = setting["value"] if index is None else setting["value"][index]

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        if prefix:
            pl = QLabel(str(prefix))
            pl.setFont(make_font(SIZES.S2))
            pl.setFixedHeight(44)
            set_style(pl, "settings", "field-affix-prefix")
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.VLine)
            sep.setFixedSize(1, 44)
            set_style(sep, "settings", "field-separator")
            row.addWidget(pl)
            row.addWidget(sep)

        self.client = client
        le = QLineEdit(str(val))
        le.setFont(make_font(SIZES.S3))
        le.setFixedHeight(44)
        # Read-only on purpose: there is no physical keyboard, so the field is
        # a display and a tap target, and all editing happens in the dialog.
        # It also stops a caret blinking in a field nothing can type into.
        le.setReadOnly(True)
        le.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(le, "settings", "field-input", object_tag="QLineEdit")
        _field = self

        def _open_keyboard(event=None):
            if event is not None and hasattr(event, "accept"):
                event.accept()
            if _field.client is None:
                return
            _field._border = QColor(FIELD_BORDER_FOCUS)
            _field.update()
            kb = make_keyboard(_field.client, le, _field._setting_type,
                               label=_field._label,
                               description=_field._description)
            kb.show_keyboard()

        # Bound to the tap, not just to focus: a read-only field still takes
        # focus, but a touch screen has no other way in, and relying on focus
        # alone meant a second tap did nothing.
        #
        # Only the line edit, and the event is accepted. Binding the parent
        # Field as well meant an unaccepted press propagated child to parent
        # and opened the keyboard twice.
        le.mousePressEvent = _open_keyboard
        le.focusInEvent = _open_keyboard

        def _changed(text):
            if is_numeric:
                try:
                    v = float(text) if "." in text else int(text)
                except ValueError:
                    return
                if index is None: setting["value"] = v
                else: setting["value"][index] = v
            else:
                if index is None: setting["value"] = text
                else: setting["value"][index] = text
            if on_change:
                on_change()

        le.textChanged.connect(_changed)
        row.addWidget(le, stretch=1)

        if suffix:
            sep2 = QFrame()
            sep2.setFrameShape(QFrame.Shape.VLine)
            sep2.setFixedSize(1, 44)
            set_style(sep2, "settings", "field-separator")
            sl = QLabel(str(suffix))
            sl.setFont(make_font(SIZES.S2))
            sl.setFixedHeight(44)
            set_style(sl, "settings", "field-affix-suffix")
            row.addWidget(sep2)
            row.addWidget(sl)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(self._bg))
        p.setPen(QPen(self._border, 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), self._radius, self._radius)


#Spellings that mean the same thing. The dispatch below matches on the
#normalised name, so a type it does not recognise falls through every branch
#and the setting is drawn with a label and no editor - which reads as a
#setting that cannot be changed rather than as one that was misspelled.
TYPE_ALIASES = {
    # `group` is not an alias of anything - it is named here only so the list
    # of what the dispatch understands stays in one place.
    "double": "float",
    "str": "string",
    "text": "string",
    "integer": "int",
    "boolean": "bool",
    "number": "float",
}


def normalize_setting_type(raw_t: str) -> str:
    raw_t = str(raw_t or "string").strip().lower()
    t = "list" if raw_t.startswith("list") else raw_t
    return TYPE_ALIASES.get(t, t)


class EnumComponent(QComboBox):
    def __init__(self, setting, on_change=None):
        super().__init__()
        self._setting = setting
        self._filler  = "-" if setting.options and "-" in setting.options[0] else "_"
        self.setFont(make_font(SIZES.S2))
        self.setFixedHeight(44)
        self.setStyleSheet(get_style_sheet("settings_combobox"))
        for option in setting.options:
            self.addItem(format_name(option.strip()), userData=option)
            if option == setting.value:
                self.setCurrentIndex(self.count() - 1)

        def _changed():
            self._setting.__setitem__("value", self.currentData())
            if on_change:
                on_change()
        self.currentIndexChanged.connect(_changed)

#How many rule colours the stylesheet defines for nested groups. Named here
#because the block picks one by number and the sheet has to have it.
GROUP_COLOURS = 3


class GroupComponent(QComboBox):
    """
    The dropdown of a `group` setting.

    Its choices come from the groups themselves rather than an `options` list,
    so there is no second list to disagree with the first. Changing it asks
    the page to draw that block again - which is the whole point of the type,
    and cannot be done from inside a single control.
    """

    def __init__(self, setting, on_change=None, on_group_changed=None):
        super().__init__()
        self._setting = setting
        self.setFont(make_font(SIZES.S2))
        self.setFixedHeight(44)
        self.setStyleSheet(get_style_sheet("settings_combobox"))

        names = setting_groups.group_names(setting)
        chosen = setting_groups.chosen_group(setting)
        for name in names:
            self.addItem(format_name(name.strip()), userData=name)
            if name == chosen:
                self.setCurrentIndex(self.count() - 1)
        # Written back even when it was not chosen, so a value naming a group
        # that no longer exists does not survive the next save.
        if chosen and setting.get("value") != chosen:
            setting.__setitem__("value", chosen)

        def _changed():
            self._setting.__setitem__("value", self.currentData())
            if on_change:
                on_change()
            if on_group_changed:
                on_group_changed()
        self.currentIndexChanged.connect(_changed)


def _text_lines(text: str) -> int:
    """
    A rough line count, for a folded header to say how much is in there.

    Blank lines are not counted: descriptions are written in paragraphs, and
    counting the gaps between them makes a three-paragraph note read as twice
    the length it is.
    """
    return len([line for line in str(text or "").splitlines() if line.strip()])


class SettingBlock(QFrame):
    def __init__(self, client, setting=None, key="", content: QWidget = None,
                 on_group_changed=None, in_groups=None, group_members=None,
                 depth: int = 0):
        super().__init__()
        self.client  = client
        # Which groups claim this setting, if any. A member is drawn inside
        # the selector that owns it, so this is only used to name WHICH group
        # a shared setting belongs to - the containment says the rest.
        self.in_groups = tuple(in_groups or ())
        # The members this block holds, in order, when it is a selector.
        self._group_members = list(group_members or [])
        # How deeply nested this block is, which picks the colour of the rule
        # down its left edge. Groups stack - a backend chooses whether there
        # is a voice at all, and where it runs only means anything once that
        # has said yes - and one colour for every level would leave two runs
        # sharing an edge with nothing to tell them apart.
        self.depth = max(0, int(depth or 0))
        # Handed in rather than reached for. A block is built by the page but
        # is not the page, so it has no way to ask for a redraw of its own -
        # and which settings belong on screen is a decision for the block that
        # owns them, not for one control inside it.
        self._on_group_changed = on_group_changed
        self._setting = setting
        self._initial_value = copy.deepcopy(setting.get("value")) if setting else None
        set_style(self, "settings",
                  "setting-member" if self.in_groups else "setting-block")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.sort_label = (setting.get("name") if setting else None) or format_name(key) or ""
        self.sort_type = normalize_setting_type(setting.get("type", "")) if setting else ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        if content:
            outer.addWidget(content)
            return

        header = QHBoxLayout()
        header.setSpacing(12)
        header.setContentsMargins(0, 0, 0, 0)

        name_lbl = QLabel(setting.get("name") or format_name(key))
        name_lbl.setFont(make_font(SIZES.S2, bold=True))
        set_style(name_lbl, "common", "text-strong")
        header.addWidget(name_lbl)

        if self.in_groups:
            # Named, not just marked. The edge says "this belongs to the
            # choice above"; the chip says which choice - which is the only
            # thing worth knowing about a setting shared between several,
            # because it will still be here after switching.
            badge = QLabel(" / ".join(format_name(name)
                                      for name in self.in_groups))
            badge.setFont(make_font(SIZES.S1, bold=True))
            set_style(badge, "settings", "group-badge")
            header.addWidget(badge)

        self._modified_badge = QLabel("Modified")
        self._modified_badge.setFont(make_font(SIZES.S1, bold=True))
        set_style(self._modified_badge, "settings", "modified-badge")
        header.addWidget(self._modified_badge)
        self._refresh_modified_badge()

        header.addStretch()
        outer.addLayout(header)

        # Folded away, like a plugin readme and for the same reason.
        #
        # A description is several paragraphs on the settings that need one,
        # and rendered inline they push the controls somebody came for off the
        # bottom of the screen - a category of a dozen settings becomes a page
        # of prose with widgets buried in it. The header says how many lines
        # are in there, so a closed one is visibly something rather than
        # nothing.
        #
        # `_Collapsible` is defined below this class. That resolves because it
        # is looked up when a block is BUILT rather than when the module is
        # read, and the page builds nothing at import time.
        self.description_block = None
        desc = str(setting.get("description", "") or "")
        if desc:
            dl = QLabel(desc)
            dl.setFont(make_font(SIZES.S1))
            set_style(dl, "common", "text-muted")
            dl.setWordWrap(True)
            self.description_block = _Collapsible(
                "Description", dl, lines=_text_lines(desc))
            outer.addWidget(self.description_block)

        raw_t  = setting.get("type", "string")
        t      = normalize_setting_type(raw_t)
        prefix = setting.get("prefix", "") or ""
        suffix = setting.get("suffix", "") or ""

        if t == "bool":
            toggle = ToggleSwitch(bool(setting["value"]))
            def _bool_changed(val):
                setting.__setitem__("value", val)
                self._refresh_modified_badge()
            toggle.connect(_bool_changed)
            header.addWidget(toggle)

        elif t == "body":
            outer.addWidget(BodyField(setting, on_change=self._refresh_modified_badge,
                                      client=self.client, label=format_name(key),
                                      description=str(setting.get("description", "") or "")))

        elif t == "secret":
            outer.addWidget(self._build_secret_field(setting))

        elif t == "string":
            outer.addWidget(Field(setting, prefix=prefix, suffix=suffix,
                                  on_change=self._refresh_modified_badge,
                                  label=format_name(key), client=self.client,
                                  setting_type=raw_t,
                                  description=str(setting.get("description", "") or "")))

        elif t == "path":
            field = Field(setting, prefix=prefix, suffix=suffix,
                          on_change=self._refresh_modified_badge,
                          label=format_name(key), client=self.client,
                          setting_type=raw_t,
                          description=str(setting.get("description", "") or ""))
            browse = QPushButton("Browse")
            browse.setFixedSize(80, 44)
            browse.setFont(make_font(SIZES.S1))
            browse.setCursor(Qt.CursorShape.PointingHandCursor)
            browse.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            set_style(browse, "settings", "settings-browse-button")
            def _browse(checked=False, _s=setting, _f=field):
                """
                The panel's own explorer, not the system one.

                `QFileDialog` opens a native window. On a single-monitor
                panel running fullscreen that window goes behind the app,
                where it cannot be seen or reached - so the Browse button
                appeared to do nothing at all.

                Files as well as folders: a path setting is a path to
                whatever it names, and half of them name a file.
                """
                def took(chosen: str) -> None:
                    _s["value"] = chosen
                    for child in _f.children():
                        if isinstance(child, QLineEdit):
                            child.setText(chosen)
                            break
                    self._refresh_modified_badge()

                self.client.browse(
                    on_chosen=took, start=str(_s.get("value") or ""),
                    select="both", title=f"Choose for {format_name(key)}")
            browse.clicked.connect(_browse)
            path_row = QWidget()
            set_style(path_row, "common", "transparent")
            path_hl = QHBoxLayout(path_row)
            path_hl.setContentsMargins(0,0,0,0)
            path_hl.setSpacing(6)
            path_hl.addWidget(field, stretch=1)
            path_hl.addWidget(browse)
            outer.addWidget(path_row)

        elif t in ("int", "float", "numeric"):
            outer.addWidget(Field(setting, is_numeric=True, prefix=prefix, suffix=suffix,
                                   on_change=self._refresh_modified_badge,
                                   label=format_name(key), client=self.client,
                                   setting_type=raw_t,
                                   description=str(setting.get("description", "") or "")))

        elif t == "enum":
            outer.addWidget(EnumComponent(setting, on_change=self._refresh_modified_badge))

        elif t == "group":
            outer.addWidget(GroupComponent(
                setting, on_change=self._refresh_modified_badge,
                on_group_changed=self._on_group_changed))
            box = self._build_group_box()
            if box is not None:
                outer.addWidget(box)

        elif t == "list":
            # is_numeric from raw type or from value content
            list_numeric = "int" in raw_t or "float" in raw_t or "numeric" in raw_t
            for i, val in enumerate(setting["value"]):
                pfx = prefix[i] if isinstance(prefix, list) and i < len(prefix) else (prefix or "")
                sfx = suffix[i] if isinstance(suffix, list) and i < len(suffix) else (suffix or "")
                is_num = list_numeric or not isinstance(val, str)
                outer.addWidget(Field(setting, index=i, is_numeric=is_num,
                                       client=self.client,
                                       setting_type=("int" if is_num else "string"),
                                       label=f"{format_name(key)} [{i + 1}]",
                                       description=str(setting.get("description", "") or ""),
                                       prefix=str(pfx), suffix=str(sfx),
                                       on_change=self._refresh_modified_badge))

    def _build_group_box(self) -> QWidget:
        """
        The members, drawn inside this block.

        Indented behind a rule down the left, so the eye can see where the
        group starts and where it ends without reading anything. A chip on
        each member says every one of them belongs to something; it does not
        say they belong to THIS, and it does not say where they stop.
        """
        if not self._group_members:
            return None
        box = QWidget()
        # Numbered from the depth of the members, not of this block, so the
        # rule beside a member matches the run it is part of. Wrapped rather
        # than clamped: a fourth level would be refused by MAX_GROUP_DEPTH
        # long before it got here, and a modulo keeps this honest if that
        # ever changes.
        level = (self.depth % GROUP_COLOURS) + 1
        set_style(box, "settings", f"setting-group-members-{level}")
        layout = QVBoxLayout(box)
        # The rule is the container's own left border, so this padding is
        # what holds the members off it.
        layout.setContentsMargins(14, 10, 0, 2)
        layout.setSpacing(8)
        for _key, widget in self._group_members:
            layout.addWidget(widget)
        return box

    def _build_secret_field(self, setting) -> QWidget:
        """
        A credential field. The value goes to .env, never into `setting`.

        The settings JSON is written on every save and rendered wholesale in
        this page, so a key left in `value` would leak by default. Nothing is
        stored here: the field shows whether a value exists, and typing one
        writes it straight through to the registry.
        """
        env_key = str(setting.get("env", "") or setting.get("key", "")).strip()
        secrets = self.client.SECRETS

        # Two rows, not one. Field, status badge, Save and Clear side by side
        # left the buttons about 70px wide and unreadable once the panel was
        # any narrower than full width.
        row = QWidget()
        set_style(row, "common", "transparent")
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        if not env_key:
            warning = QLabel("This secret has no 'env' key set in its settings file.")
            warning.setFont(make_font(SIZES.S1))
            warning.setWordWrap(True)
            set_style(warning, "common", "text-muted")
            layout.addWidget(warning)
            return row

        wrapper = QFrame()
        wrapper.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(wrapper, "settings", "setting-block")
        wrap = QHBoxLayout(wrapper)
        wrap.setContentsMargins(2, 2, 2, 2)

        entry = QLineEdit()
        entry.setFont(make_font(SIZES.S2))
        entry.setFixedHeight(44)
        entry.setEchoMode(QLineEdit.EchoMode.Password)
        entry.setPlaceholderText("Set — enter a new value to replace"
                                 if secrets.is_set(env_key) else "Not set")
        set_style(entry, "overlays", "dialog-input")
        wrap.addWidget(entry)
        layout.addWidget(wrapper)

        controls = QWidget()
        set_style(controls, "common", "transparent")
        control_row = QHBoxLayout(controls)
        control_row.setContentsMargins(0, 0, 0, 0)
        control_row.setSpacing(8)

        status = QLabel(secrets.status(env_key))
        status.setFont(make_font(SIZES.S1, bold=True))
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(status, "settings", "registry-count" if secrets.is_set(env_key)
                  else "pending-badge")
        control_row.addWidget(status)
        control_row.addStretch()

        def refresh():
            status.setText(secrets.status(env_key))
            set_style(status, "settings", "registry-count" if secrets.is_set(env_key)
                      else "pending-badge")
            entry.setPlaceholderText("Set — enter a new value to replace"
                                     if secrets.is_set(env_key) else "Not set")
            entry.clear()
            is_set = secrets.is_set(env_key)
            clear_btn.setEnabled(is_set)
            set_style(clear_btn, "settings",
                      "plugin-action-uninstall" if is_set
                      else "plugin-action-uninstall-disabled")

        def save():
            value = entry.text()
            if not value:
                return
            if secrets.set(env_key, value):
                self.client.simple_notify(Icons.SAVE, "Secrets", f"'{env_key}' saved.")
            else:
                self.client.simple_notify("error", "Secrets", f"Could not save '{env_key}'.")
            refresh()

        def clear():
            def do_clear():
                secrets.clear(env_key)
                self.client.simple_notify(Icons.SAVE, "Secrets", f"'{env_key}' cleared.")
                refresh()
            self.client.confirm(
                f"Clear '{env_key}'?",
                "The value is removed from your .env file. Anything using it "
                "stops working until a new one is entered.",
                confirm_text="Clear", destructive=True, on_confirm=do_clear,
            )

        entry.returnPressed.connect(save)

        entry.setReadOnly(True)
        entry.setCursor(Qt.CursorShape.PointingHandCursor)

        def _open_keyboard(event=None):
            if event is not None and hasattr(event, "accept"):
                event.accept()
            kb = make_keyboard(self.client, entry, "string",
                               label=env_key,
                               description=str(setting.get("description", "") or ""))
            kb.show_keyboard()

        entry.mousePressEvent = _open_keyboard
        entry.focusInEvent = _open_keyboard

        clear_btn = QPushButton("Clear")
        clear_btn.setFixedHeight(44)
        clear_btn.setMinimumWidth(110)
        clear_btn.setFont(make_font(SIZES.S2, bold=True))
        clear_btn.setEnabled(secrets.is_set(env_key))
        if clear_btn.isEnabled():
            clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            set_style(clear_btn, "settings", "plugin-action-uninstall")
        else:
            set_style(clear_btn, "settings", "plugin-action-uninstall-disabled")
        clear_btn.clicked.connect(clear)
        control_row.addWidget(clear_btn)

        save_btn = QPushButton("Save")
        save_btn.setFixedHeight(44)
        save_btn.setMinimumWidth(110)
        save_btn.setFont(make_font(SIZES.S2, bold=True))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(save_btn, "settings", "plugin-action-reload")
        save_btn.clicked.connect(save)
        control_row.addWidget(save_btn)

        layout.addWidget(controls)
        return row

    def _refresh_modified_badge(self) -> None:
        is_modified = bool(self._setting and self._setting.get("value") != self._initial_value)
        self._modified_badge.setVisible(is_modified)


# ── Section label ─────────────────────────────────────────────────────────────

def _section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setFont(make_font(SIZES.S1))
    set_style(lbl, "settings", "section-label")
    return lbl


def _divider() -> QFrame:
    d = QFrame()
    d.setFrameShape(QFrame.Shape.HLine)
    d.setFixedHeight(1)
    set_style(d, "settings", "divider")
    return d


# ── Users page ────────────────────────────────────────────────────────────────

def _build_users_page(client) -> list:
    """Approved devices, with a revoke button each."""
    widgets = []

    intro = QLabel(
        "Devices that have been allowed to use this panel's API. Each has its "
        "own token, so revoking one does not affect the others."
    )
    intro.setFont(make_font(SIZES.S1))
    intro.setWordWrap(True)
    set_style(intro, "settings", "settings-hint")
    widgets.append(intro)

    users = client.USERS.all_users()
    if not users:
        empty = QLabel("No devices yet. A device that asks for access will "
                       "raise a dialog on this screen.")
        empty.setFont(make_font(SIZES.S2))
        empty.setWordWrap(True)
        set_style(empty, "common", "text-muted")
        widgets.append(empty)
        return widgets

    for user in users:
        card = QFrame()
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(card, "settings", "setting-block")

        row = QHBoxLayout(card)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)

        column = QVBoxLayout()
        column.setSpacing(1)

        name = QLabel(user.name)
        name.setFont(make_font(SIZES.S2, bold=True))
        set_style(name, "common", "text-strong")
        column.addWidget(name)

        import datetime as _dt
        seen = _dt.datetime.fromtimestamp(user.last_seen).strftime("%d %b, %H:%M")
        # Said out loud, because a device showing its browser name is a device
        # nobody has named yet - and that is a thing to finish, not a state.
        waiting = "  ·  choosing their own name" if user.awaiting_name else ""
        # Permissions on the card, not only in the menu. What a device is
        # ALLOWED to do is the thing somebody scanning this list wants to
        # see, and a capability that is only visible after opening a menu is
        # one nobody audits.
        from src.registries.user_registry import PERMISSIONS
        held = [label for key, label, _h in PERMISSIONS if user.may(key)]
        granted = ("  ·  " + ", ".join(held).lower()) if held else ""
        detail = QLabel(
            f"{user.address or 'unknown address'}  ·  last seen "
            f"{seen}{waiting}{granted}")
        detail.setFont(make_font(SIZES.S1))
        set_style(detail, "common", "text-muted")
        column.addWidget(detail)

        row.addLayout(column, stretch=1)


        def _rename(token=user.token, current=user.name):
            from src.ui.keyboard import KeyboardDialog
            holder = QLineEdit(current)

            def done(text: str):
                if text.strip():
                    client.USERS.rename(token, text.strip())

            client.dialog(KeyboardDialog(client, holder, mode="text",
                                         label="Name", on_done=done))




        def _revoke(token=user.token, label=user.name):
            # Confirmed: the device stops working immediately and has to ask
            # again, which needs somebody standing at the panel to answer.
            client.confirm(
                "Revoke access",
                f"Stop '{label}' using this panel?",
                on_confirm  = lambda: (client.USERS.revoke(token),
                                       client.goto("#settings", override=True)),
                confirm_text= "Revoke",
                cancel_text = "Keep",
                destructive = True,
            )

        # One entry per permission, saying what it currently is rather than
        # what pressing it does. A menu of "Manage plugins" with no state is a
        # menu that has to be opened to find out whether it is on.
        from src.registries.user_registry import PERMISSIONS
        entries = [
            ("Rename this device", _rename, Icons.PENCIL, "secondary"),
        ]
        for key, label, _help in PERMISSIONS:
            holds = user.may(key)

            def _toggle(token=user.token, permission=key, name=user.name,
                        label=label, on=holds):
                if on:
                    client.USERS.revoke_permission(token, permission)
                    client.goto("#settings", override=True)
                    return
                # Asked before granting, not after. This is the panel handing
                # a device the ability to put code on it, and the one moment
                # where the person doing it is definitely in the room.
                client.confirm(
                    f"Allow {label.lower()}?",
                    f"'{name}' will be able to upload, load and reload "
                    f"plugins. Plugins run with the same reach as the panel "
                    f"itself.",
                    on_confirm=lambda: (client.USERS.grant(token, permission),
                                        client.goto("#settings", override=True)),
                    confirm_text="Allow",
                    cancel_text="No",
                    destructive=True,
                )

            entries.append((
                f"{label}: {'on' if holds else 'off'}",
                _toggle,
                Icons.KEY,
                "secondary" if holds else "secondary",
            ))

        entries.append(("Revoke its access", _revoke, Icons.ACCOUNT_REMOVE,
                        "destructive"))
        row.addWidget(row_menu(client, user.name, entries))
        widgets.append(card)

    return widgets


# ── Info page ─────────────────────────────────────────────────────────────────

def _info_label_width(labels, font) -> int:
    """
    Wide enough for the longest label there is.

    Measured rather than picked: the column was a hardcoded 120px, which fits
    "Python" and clips "Approved devices" by half. A number chosen once is a
    number that goes wrong the first time somebody adds a row, and a clipped
    label is not obviously a layout bug from the outside - it reads as a
    truncated name.
    """
    from PyQt6.QtGui import QFontMetrics
    metrics = QFontMetrics(font)
    widest = max((metrics.horizontalAdvance(str(text)) for text in labels),
                 default=0)
    # A little air, and a ceiling so one long label cannot squeeze the values
    # off the card.
    return max(120, min(320, widest + 12))


def _build_panel_name_card(client, setting: dict, label_width: int = 160) -> QFrame:
    """What this panel is called, with a button to change it."""
    card = QFrame()
    card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    set_style(card, "settings", "setting-block")
    card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    row = QHBoxLayout(card)
    row.setContentsMargins(14, 10, 14, 10)
    row.setSpacing(12)

    mark = QLabel()
    mark.setPixmap(icon(Icons.PENCIL, color="#8b93a3").pixmap(QSize(16, 16)))
    mark.setFixedSize(16, 16)
    set_style(mark, "common", "transparent")
    row.addWidget(mark)

    label = QLabel("Panel name")
    label.setFont(make_font(SIZES.S2, bold=True))
    set_style(label, "common", "text-muted")
    label.setFixedWidth(label_width)
    row.addWidget(label)

    def shown() -> str:
        # The fallback is shown as the fallback rather than as the name, so it
        # is clear the panel has not been named yet.
        current = str(setting.get("value") or "").strip()
        return current or f"{client.WINDOW_NAME}  (not named)"

    value = QLabel(shown())
    value.setFont(make_font(SIZES.S2))
    value.setWordWrap(True)
    set_style(value, "common", "text-strong")
    row.addWidget(value, stretch=1)


    def rename():
        from src.ui.keyboard import KeyboardDialog
        holder = QLineEdit(str(setting.get("value") or ""))

        def done(text: str):
            # Collapsed and capped: this ends up in a window title and in an
            # HTML heading, and a name with newlines in it is neither.
            setting["value"] = " ".join(str(text or "").split())[:64]
            try:
                value.setText(shown())
            except RuntimeError:
                pass

        client.dialog(KeyboardDialog(client, holder, mode="text",
                                     label="Panel name", on_done=done))

    row.addWidget(ActionButton(Icons.PENCIL, "Change", rename,
                               kind="secondary"))
    return card


def _build_info_page(client, working=None) -> list:
    import socket, platform

    def _local_ip() -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "unavailable"

    # Each row carries an icon for what it is, not a generic bullet. A page of
    # label-value pairs is read by scanning down the left column, and a picture
    # of the *kind* of thing is faster to land on than the word for it.
    rows = [
        ("Application",  client.WINDOW_NAME,                     Icons.INFO_OUTLINE),
        ("Serving as",   client.panel_name(),                    Icons.EARTH),
        ("Approved devices", str(len(client.USERS.all_users())), Icons.ACCOUNT_MULTIPLE),
        ("Local IP",     _local_ip(),                            Icons.EARTH),
        ("API Port",     "5000",                                 Icons.KEY),
        ("Platform",     f"{platform.system()} {platform.release()}", Icons.TUNE),
        ("Python",       platform.python_version(),              Icons.PUZZLE),
        ("Data Path",    str(client.DATAPATH),                   Icons.SAVE_CONTENT),
    ]

    widgets = []

    # The name, first and editable.
    #
    # Written into the page's working copy rather than into the live settings,
    # the same as every other control here - so it is saved by the Save button
    # and discarded by leaving without one, instead of being a single value
    # that behaves differently from the rest of the page.
    # Walked through the Settings object, not through to_dict().
    #
    # to_dict() rebuilds a fresh dict on every call, so a control that mutates
    # what it returns is writing into a throwaway - the value looks accepted
    # and is gone at Save. The builder resolves live objects the same way, by
    # indexing the pointer.
    name_setting = None
    if working is not None:
        try:
            name_setting = working["application"]["panel_name"]
        except (KeyError, TypeError, AttributeError):
            name_setting = None

    # One width for the name card and every row below it, so the values line up.
    label_font = make_font(SIZES.S2, bold=True)
    label_width = _info_label_width(
        ["Panel name", "Documentation"] + [label for label, _, _ in rows],
        label_font)

    if name_setting is not None:
        widgets.append(
            _build_panel_name_card(client, name_setting, label_width))

    for label, value, glyph in rows:
        card = QFrame()
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(card, "settings", "setting-block")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)

        mark = QLabel()
        mark.setPixmap(icon(glyph, color="#8b93a3").pixmap(QSize(16, 16)))
        mark.setFixedSize(16, 16)
        set_style(mark, "common", "transparent")
        row.addWidget(mark)

        lbl = QLabel(label)
        lbl.setFont(label_font)
        lbl.setMinimumHeight(line_height(SIZES.S2, bold=True))
        set_style(lbl, "common", "text-muted")
        lbl.setFixedWidth(label_width)

        val = QLabel(str(value))
        val.setFont(make_font(SIZES.S2))
        set_style(val, "common", "text-strong")
        val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        val.setWordWrap(True)

        row.addWidget(lbl)
        row.addWidget(val, stretch=1)
        widgets.append(card)

    # API usage hint
    hint = QLabel(
        "The API needs a device token, not a password. A new device asks at "
        "/access/request and a dialog appears here to allow or deny it. "
        "Approved devices are listed under Users, and can be revoked one at a time."
    )
    hint.setFont(make_font(SIZES.S1))
    set_style(hint, "settings", "settings-hint")
    hint.setWordWrap(True)
    widgets.append(hint)

    # Documentation, with the address spelled out. The docs endpoint is the one
    # thing here that needs no client ID, so it can be opened in a browser as
    # written - which is the whole reason it is unauthenticated.
    docs_url = f"http://{_local_ip()}:5000/docs"

    docs_card = QFrame()
    docs_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    set_style(docs_card, "settings", "setting-block")
    docs_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    docs_row = QHBoxLayout(docs_card)
    docs_row.setContentsMargins(14, 10, 14, 10)
    docs_row.setSpacing(12)

    docs_mark = QLabel()
    docs_mark.setPixmap(icon(Icons.INFO_OUTLINE, color="#8b93a3").pixmap(QSize(16, 16)))
    docs_mark.setFixedSize(16, 16)
    set_style(docs_mark, "common", "transparent")
    docs_row.addWidget(docs_mark)

    docs_label = QLabel("Documentation")
    docs_label.setFont(label_font)
    set_style(docs_label, "common", "text-muted")
    docs_label.setFixedWidth(label_width)

    docs_value = QLabel(docs_url)
    docs_value.setFont(make_font(SIZES.S2))
    set_style(docs_value, "common", "text-strong")
    docs_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    docs_value.setWordWrap(True)

    def _open_docs():
        # The panel is a touchscreen with no browser worth using, so opening it
        # here is a courtesy for desktop runs; the address above is the part
        # that matters and copying it is the usual path.
        try:
            QDesktopServices.openUrl(QUrl(docs_url))
        except Exception as e:
            client.log("warning", f"[Settings] Could not open the docs: {e}")

    def _copy_docs():
        try:
            client.app.clipboard().setText(docs_url)
            client.simple_notify("copy", "Documentation", "Address copied.")
        except Exception as e:
            client.log("warning", f"[Settings] Could not copy the docs link: {e}")

    open_btn = QPushButton("Open")
    open_btn.setFont(make_font(SIZES.S1, bold=True))
    open_btn.setFixedHeight(36)
    open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    set_style(open_btn, "overlays", "dialog-button-primary")
    open_btn.clicked.connect(lambda: _open_docs())

    copy_btn = QPushButton("Copy")
    copy_btn.setFont(make_font(SIZES.S1, bold=True))
    copy_btn.setFixedHeight(36)
    copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    set_style(copy_btn, "overlays", "dialog-button-secondary")
    copy_btn.clicked.connect(lambda: _copy_docs())

    docs_row.addWidget(docs_label)
    docs_row.addWidget(docs_value, stretch=1)
    docs_row.addWidget(copy_btn)
    docs_row.addWidget(open_btn)
    widgets.append(docs_card)

    docs_hint = QLabel(
        "Full local documentation, served by the panel itself. No client ID needed. "
        "The same files are in the docs/ folder of the install."
    )
    docs_hint.setFont(make_font(SIZES.S1))
    set_style(docs_hint, "settings", "settings-hint")
    docs_hint.setWordWrap(True)
    widgets.append(docs_hint)

    return widgets

# ── Settings page ─────────────────────────────────────────────────────────────

class _Collapsible(QFrame):
    """
    A header that folds something away.

    Built for plugin readmes, which run to pages: a settings page that opens
    with one expanded has pushed the settings somebody came for off the bottom
    of the screen.
    """

    def __init__(self, title: str, content: QWidget, lines: int = 0,
                 open_at_start: bool = False, on_toggle=None):
        super().__init__()
        # Called after every fold, by hand or in bulk. Optional: a readme has
        # nothing watching it.
        self._on_toggle = on_toggle
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(self, "settings", "settings-collapsible")

        self._content = content
        self._open = bool(open_at_start)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QPushButton()
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setFont(make_font(SIZES.S1, bold=True))
        # Fixed height, or the button stretches to whatever the layout has
        # spare and a one-line header becomes a slab.
        self._header.setFixedHeight(38)
        self._header.setSizePolicy(QSizePolicy.Policy.Preferred,
                                   QSizePolicy.Policy.Fixed)
        set_style(self._header, "settings", "settings-collapsible-header")
        self._title = title
        self._lines = int(lines)
        self._header.clicked.connect(self.toggle)
        outer.addWidget(self._header)

        # Inside the frame's border rather than against it. A readme's first
        # line otherwise sits on the rule and reads as a rendering fault.
        holder = QWidget()
        set_style(holder, "common", "transparent")
        inner = QVBoxLayout(holder)
        inner.setContentsMargins(12, 8, 12, 12)
        inner.setSpacing(0)
        inner.addWidget(content)

        holder.setVisible(self._open)
        self._content = holder
        outer.addWidget(holder)

        self._sync_header()

    def _sync_header(self) -> None:
        # A real icon, not a text triangle. The characters sit on the baseline
        # and render at whatever weight the font has, so they read as
        # punctuation beside every other control in the page.
        self._header.setIcon(icon(
            Icons.CHEVRON_DOWN if self._open else Icons.CHEVRON_RIGHT,
            color="#c8cedb"))
        self._header.setIconSize(QSize(18, 18))
        suffix = ""
        if not self._open and self._lines:
            # Said on the closed header, so it is clear there is something in
            # there and roughly how much.
            suffix = f"   {self._lines} line{'s' if self._lines != 1 else ''}"
        self._header.setText(f"  {self._title}{suffix}")

    def is_open(self) -> bool:
        return self._open

    def toggle(self) -> None:
        self._open = not self._open
        self._content.setVisible(self._open)
        self._sync_header()
        # Told, so a page-wide control can say what the next press will do.
        # Without this, folding the last open one by hand leaves the button
        # offering to collapse a page where nothing is open.
        if callable(self._on_toggle):
            try:
                self._on_toggle()
            except Exception:
                pass

    def set_open(self, value: bool) -> None:
        if bool(value) != self._open:
            self.toggle()


class SettingsPage(PageFramework):

    @mixin_target("settings.__init__")
    def __init__(self, client: "Client", data: dict = None):
        super().__init__(key="#settings", client=client, data=data)

        w = int(client.SETTINGS.application.window.size.value[0])
        h = int(client.SETTINGS.application.window.size.value[1])
        self.setFixedSize(w, h)
        set_style(self, "common", "page-background")

        self.categories: dict[str, dict] = {}   #see new_category()/new_subcategory() for the entry shape
        self._active_sort_mode: str | None = None   #see _build_sort_toolbar()/_sorted_content()
        self._sort_direction: dict[str, str] = {
            "alpha":      "asc",
            "dependants": "asc",
            "type":       "asc",
        }   #every axis always starts a fresh cycle at "asc" — see _click_sort_axis()

        # Dot grid background
        self._grid = GridBackground(self)
        self._grid.setGeometry(0, 0, w, h)

        NAV_W   = 360
        #Room to start a scroll without hitting the nav. A nav button changes
        #the page, so a mis-started drag costs somebody their place.
        NAV_GUTTER = 18
        BAR_H   = 70
        PAD     = 24

        # ── Top bar ───────────────────────────────────────────────────────────
        top_bar = QWidget(self)
        top_bar.setGeometry(0, 0, w, BAR_H)
        set_style(top_bar, "settings", "settings-top-bar")
        self._top_bar = top_bar

        tl = QHBoxLayout(top_bar)
        tl.setContentsMargins(PAD, 0, PAD, 0)
        tl.setSpacing(0)

        # Two buttons, because leaving and saving are two decisions.
        #
        # One control that always did both meant somebody who opened this to
        # read a value and changed one by accident had no way out that did not
        # keep it - and somebody who wanted to leave had to wait for a save
        # they never asked for.
        back_btn = ActionButton(Icons.SAVE_CONTENT, "Save and Return",
                                self.return_and_save, kind="primary",
                                size=44, min_width=210, icon_size=24)
        back_btn.setFont(make_font(SIZES.S3, bold=True))
        self._save_btn = back_btn

        leave_btn = ActionButton(Icons.ARROW_LEFT, "Return",
                                 self.return_without_saving, kind="quiet",
                                 size=44, min_width=130, icon_size=24)
        leave_btn.setFont(make_font(SIZES.S3, bold=True))
        # Kept, for the same reason _save_btn is: leaving is not instant
        # either, and a button that cannot be reached cannot say so.
        self._leave_btn = leave_btn

        # One control rather than two.
        #
        # Collapse-all and expand-all are the same button in two states, and
        # the state is knowable: if anything on screen is open, the useful
        # press closes everything, and once nothing is open the useful press
        # opens it. Two buttons would leave one of them doing nothing
        # whichever way the page happened to be.
        self._descriptions_btn = ActionButton(
            Icons.CHEVRON_RIGHT, "Collapse descriptions",
            self.toggle_all_descriptions, kind="quiet",
            size=44, min_width=250, icon_size=24)
        self._descriptions_btn.setFont(make_font(SIZES.S3, bold=True))

        tl.addWidget(back_btn)
        tl.addSpacing(10)
        tl.addWidget(leave_btn)
        tl.addSpacing(10)
        tl.addWidget(self._descriptions_btn)
        tl.addStretch()

        # ── Body ──────────────────────────────────────────────────────────────
        body = QWidget(self)
        body.setGeometry(0, BAR_H, w, h - BAR_H)
        set_style(body, "common", "transparent")
        self._body = body

        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        # No gap between the two panels.
        #
        # A layout spacing here is dead ground: it belongs to neither widget, so
        # a drag that starts in it scrolls nothing. The gutter is added to the
        # content's own margin instead, below - inside the scroll area, where
        # QScroller has the viewport and a drag anywhere in it works.
        bl.setSpacing(0)

        nav_panel = QWidget()
        nav_panel.setFixedWidth(NAV_W)
        set_style(nav_panel, "settings", "settings-nav-panel")
        nl = QVBoxLayout(nav_panel)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(0)

        # Scrolled, because the list grows with the plugins.
        #
        # Eight bundled plugins plus the system sections is more than fits, and
        # a QVBoxLayout with no room squeezes every child below its fixed
        # height - so the buttons shrank until two of them were one finger
        # wide. A rail that scrolls keeps every entry the size it was built.
        nav_scroll = QScrollArea()
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        nav_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        set_style(nav_scroll, "common", "transparent")
        set_style(nav_scroll.viewport(), "common", "transparent")
        style_scrollbar(nav_scroll)
        # The same bar as the content pane beside it. Without this the nav
        # keeps Qt's own, which is the one pale control on the page.
        QScroller.grabGesture(
            nav_scroll.viewport(),
            QScroller.ScrollerGestureType.LeftMouseButtonGesture)

        nav_inner = QWidget()
        set_style(nav_inner, "common", "transparent")
        inner = QVBoxLayout(nav_inner)
        inner.setContentsMargins(PAD, PAD, PAD, PAD)
        inner.setSpacing(0)

        self._nav_list = QVBoxLayout()
        self._nav_list.setSpacing(6)
        inner.addLayout(self._nav_list)
        inner.addStretch()

        nav_scroll.setWidget(nav_inner)
        nl.addWidget(nav_scroll)
        bl.addWidget(nav_panel)

        self._content_scroll = QScrollArea()
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        style_scrollbar(self._content_scroll)
        QScroller.grabGesture(self._content_scroll.viewport(),
                               QScroller.ScrollerGestureType.LeftMouseButtonGesture)

        self._content_widget = QWidget()
        set_style(self._content_widget, "common", "transparent")
        self._content_layout = QVBoxLayout(self._content_widget)
        # The left margin carries the gutter. It is inside the scroll area, so
        # it is somewhere a drag can start - unlike spacing between the panels,
        # which is dead ground that scrolls nothing. Far enough from the nav
        # that a thumb aiming at the page does not change it.
        self._content_layout.setContentsMargins(PAD + NAV_GUTTER, PAD, PAD, 100)
        self._content_layout.setSpacing(8)
        self._content_layout.addStretch()
        self._content_scroll.setWidget(self._content_widget)
        bl.addWidget(self._content_scroll, stretch=1)

        # Raise order: grid < body < top_bar
        self._grid.lower()

        # ── Features ─────────────────────────────────────────────────────────
        self.add_features({
            "new_category":           self.new_category,
            "new_subcategory":        self.new_subcategory,
            "insert_block":           self.insert_block,
            "insert_plugin_block":    self.insert_plugin_block,
            "new_settings_list":      self.builder,
        })

        self._working_settings = Settings(copy.deepcopy(client.settings_dict()))
        self._generate_settings(self._working_settings, self._working_settings.to_dict())
        self._page_additions()
        self._build_nav()

    # ── Builder ───────────────────────────────────────────────────────────────

    def new_category(self, name: str, controls: list, label: str = None,
                     system: bool = False) -> None:
        """
        A top-level nav entry.

        `system` separates the pages that exist in their own right - Users,
        Plugins, Info - from the ones generated out of the settings file.
        They behave differently enough to be worth telling apart: a settings
        section is a list of values to change and save, while Users is a live
        view of a registry with buttons that act immediately.
        """
        self.categories[name] = {
            "label":      label or format_name(name),
            "content":    controls,
            "subs":       {},
            "plugin":     None,
            "plugin_key": None,
            "icon":       None,
            "readme":     None,
            "pending":    None,
            "system":     bool(system),
        }

    def new_subcategory(self, parent: str, name: str, controls: list,
                         label: str = None, plugin=None, plugin_key: str = None,
                         icon: str = None, readme: str = None,
                         pending=None, conflict=None,
                         inactive: str = "") -> None:
        if parent not in self.categories:
            self.client.log("warning", f"[SettingsPage.new_subcategory] parent category '{parent}' does not exist — call new_category() first")
            return
        self.categories[parent]["subs"][name] = {
            "label":      label or format_name(name),
            "content":    controls,
            "subs":       {},
            "plugin":     plugin,
            "plugin_key": plugin_key,
            "icon":       icon,
            "readme":     readme,
            "pending":    pending,
            "conflict":   conflict,
            # What the nav should say beside the name, for a plugin that is
            # present and not running.
            "inactive":   inactive,
        }

    def insert_block(self, category: str, index: int, content: QWidget) -> None:
        entry = self.categories.get(category)
        if entry:
            entry["content"].insert(index, SettingBlock(self.client, content=content))

    def insert_plugin_block(self, plugin_key: str, index: int,
                            content: QWidget) -> None:
        """
        Add a widget to a plugin's own settings section.

        A plugin's settings are a *subcategory* under "plugins", not a
        top-level category - so insert_block() could never reach one, and a
        plugin addressing its own section by name was silently dropped.
        """
        parent = self.categories.get("plugins")
        if not parent:
            self.client.log("warning",
                            "[SettingsPage] insert_plugin_block: no 'plugins' "
                            "category - nothing to insert into.")
            return

        entry = parent["subs"].get(plugin_key)
        if not entry:
            # Named, because the alternative is a widget that silently never
            # appears and no way to tell which key was wrong.
            self.client.log(
                "warning",
                f"[SettingsPage] insert_plugin_block: no section for "
                f"'{plugin_key}'. Sections are: {sorted(parent['subs'])}")
            return

        entry["content"].insert(index, SettingBlock(self.client, content=content))
        self.client.log("debug",
                        f"[SettingsPage] Added a block to '{plugin_key}' "
                        f"({len(entry['content'])} item(s) now).")

    #How far a group may nest before the indent does more harm than the
    #structure does good. Three colours is also as many as stay apart at a
    #glance on a panel across a room.
    MAX_GROUP_DEPTH = 3

    def _build_group(self, pointer, settings: dict, selector, key: str,
                     path: str, depth: int = 0):
        """
        One selector's members, built and ready to go inside it.

        Recursive, because a member can itself be a selector - `tts_backend`
        chooses whether there is a voice at all, and `tts_where` chooses where
        it runs, which only means anything once the first has said yes.

        Answers `(built, show)` where `built` is `[(key, widget, groups)]` and
        `show` reveals the chosen group and hides the rest.

        EVERY group's members are built, not just the chosen one's. Switching
        then costs a setVisible per block rather than a rebuild - which loses
        the scroll position, drops an open keyboard, and discards widgets
        somebody is looking at while a signal from one of them is in flight.
        The cost is a handful of extra widgets on one page.
        """
        built = []
        if depth >= self.MAX_GROUP_DEPTH:
            self.client.log(
                "warning",
                f"[SettingsPage.builder] '{key}' nests groups more than "
                f"{self.MAX_GROUP_DEPTH} deep - its members are not shown.")
            return built, (lambda chosen=None: None)

        try:
            layout = setting_groups.plan(settings)
        except Exception:
            layout = {"all": {}}
        all_members = layout.get("all") or {}

        for member, belongs in (all_members.get(key) or []):
            try:
                child = pointer
                for part in (f"{path}.{member}" if path else member).split("."):
                    child = child[part]
                node = settings.get(member) or {}
                inner, inner_show = [], None
                if setting_groups.is_group(node):
                    # A selector inside a selector. Its own members are built
                    # now and handed to it, exactly as this one's are.
                    inner, inner_show = self._build_group(
                        pointer, settings, child, member, path, depth + 1)
                block = SettingBlock(
                    client=self.client, setting=child, key=member,
                    in_groups=belongs, depth=depth + 1,
                    on_group_changed=inner_show,
                    group_members=[(k, w) for k, w, _b in inner])
                if inner_show is not None:
                    inner_show()
                built.append((member, block, tuple(belongs)))
            except Exception as e:
                self.client.log(
                    "error",
                    f"[SettingsPage.builder] could not build '{member}' under "
                    f"group '{key}': {e}")

        def show(chosen=None, _obj=selector, _built=built):
            name = chosen or setting_groups.chosen_group(_obj)
            wanted = set(setting_groups.visible_members(
                name, [(k, b) for k, _w, b in _built]))
            for member, widget, _belongs in _built:
                try:
                    widget.setVisible(member in wanted)
                except RuntimeError:
                    # The page went away between the signal and this running.
                    pass

        return built, show

    def builder(self, pointer, data: dict, filter_key: str = "", path: str = "") -> list:
        group = []
        if not isinstance(data, dict):
            self.client.log("warning", f"[SettingsPage.builder] data was not a Dictionary to be read (was {type(data)})")
            return group
        settings = data[filter_key] if filter_key else data

        # Which settings a `group` selector has claimed. Those are drawn
        # underneath it rather than where they were written, so the block does
        # not show the same control twice.
        try:
            layout = setting_groups.plan(settings)
            for selector, name, member in setting_groups.missing_members(settings):
                self.client.log(
                    "warning",
                    f"[SettingsPage.builder] '{selector}' group '{name}' names "
                    f"'{member}', which is not in this block - it will not be "
                    f"shown.")
        except Exception:
            layout = {"order": [], "owned": {}, "groups": {}}
        claimed = layout.get("owned") or {}
        all_members = layout.get("all") or {}

        for key, val in settings.items():
            if key in claimed and claimed[key] != key:
                # Drawn by whichever selector owns it, below.
                continue
            if not isinstance(val, dict):
                self.client.log("warning", f"[SettingsPage.builder] The value under '{key}' was not a Valid object to be built with. (was {type(val)}, meant to be dict)")
                continue
            extended_path = f"{path}.{key}" if path else key
            if val.get("hidden"):
                # Stored but not shown. A plugin that renders its own control
                # for a value still needs somewhere to keep it, and a raw text
                # field beside a proper picker is just a second way to get it
                # wrong.
                continue
            if "type" in val and "value" in val:
                try:
                    obj = pointer
                    for part in extended_path.split("."):
                        obj = obj[part]
                    built, show_group = self._build_group(
                        pointer, settings, obj, key, path, depth=0)

                    # INSIDE the selector, not after it.
                    #
                    # A badge on each member says every one of them belongs to
                    # something; it does not say they belong to THAT, and it
                    # does not say where the group ends. Containment says
                    # both, at a glance, without anything to read.
                    group.append(SettingBlock(
                        client=self.client, setting=obj, key=key,
                        on_group_changed=show_group, depth=0,
                        group_members=[(k, w) for k, w, _b in built]))
                    show_group()
                except Exception as e:
                    self.client.log("error", f"[SettingsPage.builder] an error was thrown under '{extended_path}'/'{key}' when creating SettingBlock: {e}", include_traceback = True)
            else:
                children = self.builder(pointer, settings, key, extended_path)
                if children:
                    if len(path.split(".")) > 1:
                        gap = QWidget()
                        gap.setFixedHeight(6)
                        set_style(gap, "common", "transparent")
                        group.append(gap)
                    group.append(_section_label(format_name(key)))
                    group.append(_divider())
                    group.extend(children)
        return group

    @mixin_target("settings.setup.setting.generation")
    def _generate_settings(self, pointer, grouped_dict: dict) -> None:
        for key in grouped_dict:
            self.new_category(key.lower(), self.builder(pointer, grouped_dict, key, key))
        # Live, like Users: the list changes while it is on screen and joining
        # a network happens immediately rather than on a Save button.
        from src.pages.wifi import build_wifi_page
        self.new_category("wifi", build_wifi_page(self.client),
                          label="Wi-Fi", system=True)
        from src.pages.bluetooth import build_bluetooth_page
        self.new_category("bluetooth", build_bluetooth_page(self.client),
                          label="Bluetooth", system=True)
        from src.pages.logs import build_logs_page
        self.new_category("logs", build_logs_page(self.client),
                          label="Logs", system=True)
        # Live, and holds the microphone open only while a session is
        # running - see MicTestPage.
        from src.pages.mic_test import build_mic_test_page
        self.new_category("mic_test", build_mic_test_page(self.client),
                          label="Microphone test", system=True)
        # Info is always last
        self.new_category("info",
                          _build_info_page(self.client, self._working_settings),
                          system=True)
        # Live, like Info. There is no settings path behind it - the list is
        # whatever the registry holds when the page is built, and the buttons
        # act on the registry rather than writing a value to be saved.
        self.new_category("users", _build_users_page(self.client), system=True)

    def _page_additions(self) -> None:
        plugins = self.client.PLUGIN.get_plugins()

        overview = []
        for plugin, key in plugins:
            icon_value = plugin.config.get_path("plugin.icon", None)
            overview.append(self._build_category_header(
                plugin.config.plugin.name,
                plugin=plugin, plugin_key=key, in_list=True,
                has_content=True, icon=icon_value, readme=None,
            ))

        for item in self.client.PLUGIN.pending_plugins():
            overview.append(self._build_pending_header(item))

        for item in self.client.PLUGIN.conflicting_plugins():
            overview.append(self._build_conflict_header(item))

        self.new_category("plugins", overview, label="Plugins", system=True)

        for plugin, key in plugins:
            blocks = []
            if hasattr(plugin, "settings"):
                blocks = self.builder(plugin.settings, plugin.settings.to_dict(), "", "")
            self.new_subcategory(
                "plugins", key, blocks,
                label=plugin.config.plugin.name,
                plugin=plugin, plugin_key=key,
                icon=plugin.config.get_path("plugin.icon", None),
                readme=plugin.config.get_path("plugin.readme", None),
            )

        for item in self.client.PLUGIN.pending_plugins():
            self.new_subcategory(
                "plugins", item.key, [],
                label=item.name,
                icon=item.icon,
                pending=item,
                inactive="stopped" if not item.missing else "not installed",
            )

        # Keyed by FOLDER, not by key. The key is what it collides on, so a
        # subcategory named after it would land on top of the plugin that won.
        for item in self.client.PLUGIN.conflicting_plugins():
            self.new_subcategory(
                "plugins", f"conflict:{item.folder}", [],
                label=item.name,
                icon=item.icon,
                conflict=item,
                inactive="blocked",
            )

    def _build_conflict_header(self, item) -> QFrame:
        """
        A folder that cannot load because its key belongs to something else.

        No buttons at all. There is nothing to press: installing packages
        will not help, loading it will not work, and the only fix is to change
        the key in its `plugin.toml` or remove the folder - both of which
        happen away from this screen.
        """
        card = QFrame()
        set_style(card, "settings", "category-header-pending")
        card.sort_label = item.name
        card.sort_dependants = 0

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(10)

        if item.icon:
            q_icon = resolve_plugin_icon(item.icon, size=28)
            if q_icon:
                icon_lbl = QLabel()
                icon_lbl.setPixmap(q_icon.pixmap(QSize(28, 28)))
                set_style(icon_lbl, "common", "transparent")
                top_row.addWidget(icon_lbl)

        title = QLabel(item.name)
        title.setFont(make_font(SIZES.M1, bold=True))
        title.setMinimumHeight(line_height(SIZES.M1, bold=True))
        set_style(title, "common", "text-pending")
        top_row.addWidget(title)

        badge = QLabel("CONFLICT")
        badge.setFont(make_font(SIZES.S1, bold=True))
        set_style(badge, "settings", "pending-badge")
        top_row.addWidget(badge)
        top_row.addStretch()
        layout.addLayout(top_row)

        sub = QLabel(f"{item.folder}  \u00b7  key '{item.key}'")
        sub.setFont(make_font(SIZES.S1))
        set_style(sub, "common", "text-muted")
        layout.addWidget(sub)

        owner = ("a plugin that ships with the app"
                 if item.bundled_winner else
                 f"'{Path(str(item.blocked_by)).name}'")
        why = QLabel(
            f"This cannot be loaded. The key '{item.key}' already belongs to "
            f"{owner}, which is scanned first and wins. Change the key in this "
            f"plugin's plugin.toml, or remove the folder.")
        why.setFont(make_font(SIZES.S1))
        why.setWordWrap(True)
        set_style(why, "common", "text-muted")
        layout.addWidget(why)
        return card

    @staticmethod
    def _pending_version(item) -> str:
        try:
            return str((item.config.get("plugin") or {}).get("version") or "")
        except Exception:
            return ""

    def _build_pending_header(self, item) -> QFrame:
        from src.plugin import dependencies as deps

        card = QFrame()
        set_style(card, "settings", "category-header-pending")
        card.sort_label = item.name
        card.sort_dependants = 0

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(10)

        if item.icon:
            q_icon = resolve_plugin_icon(item.icon, size=28)
            if q_icon:
                icon_lbl = QLabel()
                icon_lbl.setPixmap(q_icon.pixmap(QSize(28, 28)))
                set_style(icon_lbl, "common", "transparent")
                top_row.addWidget(icon_lbl)

        title = QLabel(item.name)
        title.setFont(make_font(SIZES.M1, bold=True))
        title.setMinimumHeight(line_height(SIZES.M1, bold=True))
        set_style(title, "common", "text-pending")
        top_row.addWidget(title)

        # Two different states share this card, and they are not the same
        # news. A plugin held back for missing packages is NOT INSTALLED and
        # needs pip; a plugin somebody unloaded is installed, complete and
        # simply not running, and needs a button that says so.
        stopped = not item.missing

        badge = QLabel("STOPPED" if stopped else "NOT INSTALLED")
        badge.setFont(make_font(SIZES.S1, bold=True))
        set_style(badge, "settings", "pending-badge")
        top_row.addWidget(badge)
        top_row.addStretch()

        if stopped:
            load_btn = QPushButton("Load")
            load_btn.setFont(make_font(SIZES.S2, bold=True))
            load_btn.setFixedHeight(44)
            load_btn.setMinimumWidth(100)
            load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            set_style(load_btn, "settings", "plugin-action-install")
            # `_checked` first, and it matters.
            #
            # `clicked` emits a bool, and PyQt fills the lambda's first
            # positional parameter with it. Written as `lambda k=item.key:`
            # the key default is overwritten by False on every press, so this
            # called load_pending_plugin(False) - which finds nothing and
            # reports that the plugin would not load. Every other connection
            # in the tree already takes the flag first; this was the one that
            # did not.
            load_btn.clicked.connect(
                lambda _checked=False, k=item.key: self._load_pending_plugin(k))
            top_row.addWidget(load_btn)
            layout.addLayout(top_row)

            sub = QLabel(f"{item.key}  \u00b7  v{self._pending_version(item)}"
                         if self._pending_version(item) else item.key)
            sub.setFont(make_font(SIZES.S1))
            set_style(sub, "common", "text-muted")
            layout.addWidget(sub)

            note = QLabel("Installed, and not running. Nothing it registers "
                          "is available until it is loaded.")
            note.setFont(make_font(SIZES.S1))
            note.setWordWrap(True)
            set_style(note, "common", "text-muted")
            layout.addWidget(note)

            if item.error:
                err = QLabel(item.error)
                err.setFont(make_font(SIZES.S1))
                err.setWordWrap(True)
                set_style(err, "common", "text-muted")
                layout.addWidget(err)
            return card

        install_btn = QPushButton("Install")
        install_btn.setFont(make_font(SIZES.S2, bold=True))
        install_btn.setFixedHeight(44)
        install_btn.setMinimumWidth(100)

        if not deps.in_venv():
            install_btn.setEnabled(False)
            install_btn.setCursor(Qt.CursorShape.ForbiddenCursor)
            install_btn.setToolTip("Not running inside a virtualenv")
            set_style(install_btn, "settings", "plugin-action-uninstall-disabled")
        else:
            install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            set_style(install_btn, "settings", "plugin-action-install")
            install_btn.clicked.connect(lambda: self._install_pending_plugin(item))
        top_row.addWidget(install_btn)

        layout.addLayout(top_row)

        sub = QLabel(item.key)
        sub.setFont(make_font(SIZES.S1))
        set_style(sub, "common", "text-muted")
        layout.addWidget(sub)

        missing = QLabel("Missing packages: " + ", ".join(item.missing))
        missing.setFont(make_font(SIZES.S1))
        missing.setWordWrap(True)
        set_style(missing, "common", "text-muted")
        layout.addWidget(missing)

        if item.error:
            err = QLabel(item.error)
            err.setFont(make_font(SIZES.S1))
            err.setWordWrap(True)
            set_style(err, "common", "text-muted")
            layout.addWidget(err)

        return card

    def _load_pending_plugin(self, plugin_key: str) -> None:
        """Start a plugin that is installed and stopped."""
        def _go():
            if not self.client.PLUGIN.load_pending_plugin(plugin_key):
                self.client.simple_notify(
                    "error", "Plugins", f"'{plugin_key}' would not load.")
            self._refresh_if_on_settings()
        self.client.call_on_ui(_go)

    def _install_pending_plugin(self, item) -> None:
        def _go() -> None:
            def worker() -> None:
                ok, message = self.client.PLUGIN.install_pending(item.key)
                if not ok:
                    self.client.simple_notify("error", "Plugins", str(message)[:160])
                self.client.call_on_ui(self._refresh_if_on_settings)
            Thread(target=worker, name="__plugin_pip_install", daemon=True).start()
            self.client.simple_notify("download", "Plugins", f"Installing packages for '{item.name}'...")

        self.client.confirm(
            title=f"Install packages for '{item.name}'?",
            body=f"These will be installed into {deps_venv_path()} and the plugin will be loaded.",
            detail="\n  ".join(["Packages:"] + item.missing),
            confirm_text="Install",
            on_confirm=_go,
        )

    def _refresh_if_on_settings(self) -> None:
        if self.client.PAGE is self:
            self.client.goto("#settings", override=True)

    # ── Category header (title card) ────────────────────────────────────────

    def _build_category_header(self, label: str, plugin=None, plugin_key: str = None,
                                in_list: bool = False,
                                has_content: bool = True, icon: str = None,
                                readme: str = None, pending=None,
                                conflict=None) -> QFrame:
        if conflict is not None:
            return self._build_conflict_header(conflict)
        if pending is not None:
            return self._build_pending_header(pending)

        card = QFrame()
        set_style(card, "settings", "category-header" if has_content else "category-header-standalone")

        card.sort_label = label
        card.sort_dependants = len(self.client.PLUGIN.get_dependants(plugin_key)) if plugin_key else 0

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(10)

        if icon:
            q_icon = resolve_plugin_icon(icon, size=28)
            if q_icon:
                icon_lbl = QLabel()
                icon_lbl.setPixmap(q_icon.pixmap(QSize(28, 28)))
                set_style(icon_lbl, "common", "transparent")
                top_row.addWidget(icon_lbl)

        title = QLabel(label)
        title.setFont(make_font(SIZES.M1, bold=True))
        # Guaranteed the room its font needs. Nothing else in this row is as
        # tall, so the row's own hint does not reserve it and the descenders
        # come off the bottom.
        title.setMinimumHeight(line_height(SIZES.M1, bold=True))
        set_style(title, "common", "text-strong")
        top_row.addWidget(title)
        top_row.addStretch()

        if plugin_key:
            # In a list, one glyph; on the plugin's own page, the buttons.
            #
            # The same header serves both, and they want different things. A
            # page devoted to one plugin has room for four labelled buttons and
            # somebody reads them once. Eight plugins in a row repeat the same
            # four words eight times, and those words are the widest thing in
            # each row - the first to be cut off, and the least worth reading
            # again by the third one.
            if in_list:
                top_row.addWidget(row_menu(
                    self.client, label,
                    self._plugin_menu_items(plugin, plugin_key)))
            else:
                actions = self._build_plugin_actions(plugin, plugin_key)
                # Four slots: three always, plus Uninstall when the plugin
                # declares requirements. Reserving the space means a plugin
                # with one and a plugin without still line up.
                top_row.addWidget(action_column(*actions, slots=4))

        layout.addLayout(top_row)

        if plugin_key:
            # The key, and the version beside it.
            #
            # The version was in `plugin.toml` and shown nowhere at all, so
            # the panel could not answer "which one is installed" - which is
            # the first question anybody has after uploading a new one.
            line = plugin_key
            try:
                version = ""
                if plugin is not None and hasattr(plugin, "config"):
                    version = str(plugin.config.get_path("plugin.version", "")
                                  or "")
                if version:
                    line = f"{plugin_key}  \u00b7  v{version}"
            except Exception:
                pass
            sub = QLabel(line)
            sub.setFont(make_font(SIZES.S1))
            set_style(sub, "common", "text-muted")
            layout.addWidget(sub)

            deps_line = self._build_dependency_line(plugin_key)
            if deps_line:
                layout.addWidget(deps_line)

        readme_block = self._build_readme_block(readme)
        if readme_block:
            layout.addWidget(_divider())
            layout.addWidget(readme_block)

        return card

    def _plugin_settings_blocks(self, plugin) -> list:
        """
        Optional widgets a plugin supplies for its own settings page.

        A plugin defines:

            def settings_blocks(self) -> list[QWidget]:
                return [my_card]

        A plugin raising here must not blank its whole settings page, so
        failures are logged and skipped.
        """
        if plugin is None or not hasattr(plugin, "settings_blocks"):
            return []
        try:
            blocks = plugin.settings_blocks() or []
        except Exception as e:
            self.client.log("warning",
                            f"[SettingsPage] settings_blocks() failed for "
                            f"'{getattr(plugin, 'config', {})}': {e}")
            return []
        return [b for b in blocks if isinstance(b, QWidget)]

    def _build_registrations_block(self, plugin_key: str) -> QWidget | None:
        """Cards showing what this plugin owns across every registry."""
        groups = self.client.PLUGIN.registrations(plugin_key)

        container = QWidget()
        set_style(container, "common", "transparent")
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 8)
        outer.setSpacing(10)

        total = sum(len(entries) for _, entries in groups)
        heading = QLabel(
            f"Registered  ·  {total} item{'s' if total != 1 else ''} "
            f"across {len(groups)} registr{'ies' if len(groups) != 1 else 'y'}"
            if groups else "Registered  ·  nothing yet"
        )
        heading.setFont(make_font(SIZES.S2, bold=True))
        set_style(heading, "common", "text-muted")
        outer.addWidget(heading)

        if not groups:
            note = QLabel(
                "This plugin has not registered any pages, endpoints, public "
                "values, skills or mixins."
            )
            note.setFont(make_font(SIZES.S1))
            note.setWordWrap(True)
            set_style(note, "common", "text-muted")
            outer.addWidget(note)
            return container

        # Two columns on anything wide enough; the panel is narrow enough on
        # small screens that one column reads better.
        columns = 2 if self.width() > 900 else 1
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for index, (name, entries) in enumerate(groups):
            grid.addWidget(self._registry_card(name, entries),
                           index // columns, index % columns)
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        outer.addLayout(grid)

        return container

    def _registry_card(self, name: str, entries: list) -> QFrame:
        card = QFrame()
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(card, "settings", "registry-card")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        # Both fixed, at the height the font actually needs.
        #
        # A QLabel defaults to a Preferred policy on both axes, so in a card
        # with spare height the name and the badge stretch to fill it. Pinning
        # them stops that - but pinning them to a number picked by eye clips
        # instead: this title is S3 bold, which needs 31px, and 28 lost the
        # bottom of every descender in it.
        ROW_HEIGHT = line_height(SIZES.S3, bold=True)

        title = QLabel(name)
        title.setFont(make_font(SIZES.S3, bold=True))
        title.setFixedHeight(ROW_HEIGHT)
        title.setAlignment(Qt.AlignmentFlag.AlignLeft
                           | Qt.AlignmentFlag.AlignVCenter)
        title.setSizePolicy(QSizePolicy.Policy.Preferred,
                            QSizePolicy.Policy.Fixed)
        set_style(title, "common", "text-strong")
        top.addWidget(title)

        count = QLabel(str(len(entries)))
        count.setFont(make_font(SIZES.S1, bold=True))
        count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        count.setMinimumWidth(26)
        count.setFixedHeight(ROW_HEIGHT)
        # Maximum, so the badge is as wide as its number and no wider.
        count.setSizePolicy(QSizePolicy.Policy.Maximum,
                            QSizePolicy.Policy.Fixed)
        set_style(count, "settings", "registry-count")
        top.addWidget(count)
        top.addStretch()
        layout.addLayout(top)

        for entry in entries:
            row = QLabel(str(entry))
            row.setFont(make_font(SIZES.S1))
            row.setWordWrap(True)
            row.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            set_style(row, "settings", "registry-entry")
            layout.addWidget(row)

        return card

    def _build_readme_block(self, readme_path: str):
        if not readme_path:
            return None
        path = Path(readme_path)
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8").strip()
        except Exception as e:
            self.client.log("warning", f"[SettingsPage] couldn't read readme '{readme_path}': {e}")
            return None
        if not text:
            return None

        label = QLabel()
        label.setTextFormat(Qt.TextFormat.MarkdownText)
        label.setText(text)
        label.setFont(make_font(SIZES.S1))
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        label.setOpenExternalLinks(True)
        set_style(label, "common", "text-muted")

        # Folded away to start with. A plugin readme can run to pages, and a
        # page that opens on one has pushed the settings somebody came for off
        # the bottom of the screen.
        return _Collapsible("Readme", label, lines=_text_lines(text))


    SORT_AXES = ("alpha", "dependants", "type")

    def _compose_dual_icon(self, name1: str, name2: str, size: int = 20,
                            gap: int = 4, color: str = "white") -> QIcon:
        i1 = icon(name1, color=color).pixmap(QSize(size, size))
        i2 = icon(name2, color=color).pixmap(QSize(size, size))
        canvas = QPixmap(size * 2 + gap, size)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawPixmap(0, 0, i1)
        painter.drawPixmap(size + gap, 0, i2)
        painter.end()
        return QIcon(canvas)

    def _icon_for_axis(self, axis: str) -> QIcon:
        direction = self._sort_direction.get(axis, "asc")
        if axis == "alpha":
            return (self._compose_dual_icon("mdi.alpha-a-box", "mdi.alpha-z-box") if direction == "asc"
                    else self._compose_dual_icon("mdi.alpha-z-box", "mdi.alpha-a-box"))
        concept = {
            "dependants": "mdi.sitemap",
            "type":       "mdi.shape-outline",
        }[axis]
        arrow = "mdi.arrow-down-bold" if direction == "desc" else "mdi.arrow-up-bold"
        return self._compose_dual_icon(concept, arrow)

    @staticmethod
    def _is_sortable(content) -> bool:
        """
        Whether a section has enough sortable blocks to be worth the toolbar.

        Two, not one: a control that reorders a single card does nothing, and
        offering it invites the question of why it appears to have no effect.
        Wi-Fi, Info and Users are live views whose cards carry no sort label at
        all, so they get nothing.
        """
        labelled = [w for w in (content or []) if hasattr(w, "sort_label")]
        return len(labelled) >= 2

    ## -- descriptions, folded and unfolded

    def description_blocks(self) -> list:
        """
        Every foldable description on screen right now.

        Found by walking the page rather than kept in a list. A category
        switch rebuilds the content and throws the old blocks away, and a
        remembered list would then hold widgets Qt has already deleted -
        which is a crash rather than a stale button.
        """
        try:
            return [block.description_block
                    for block in self.findChildren(SettingBlock)
                    if getattr(block, "description_block", None) is not None]
        except Exception as exc:
            self.client.log("debug",
                            f"[SettingsPage] Could not find descriptions: {exc}")
            return []

    def toggle_all_descriptions(self, event=None) -> None:
        """
        Close every description, or open every one when none is open.

        Asked of the page each time rather than tracked, because the folds
        also move one at a time: somebody who opened three by hand and then
        presses this means close them, and a remembered flag would say open.
        """
        blocks = self.description_blocks()
        if not blocks:
            return
        opening = not any(block.is_open() for block in blocks)
        for block in blocks:
            block.set_open(opening)
        self._sync_descriptions_button()

    def _sync_descriptions_button(self) -> None:
        """
        The label says what pressing it will DO next.

        Reads the folds rather than being told. A caller that passes what it
        just did and a caller asking what is next are describing opposite
        things, and one label mapping cannot serve both - so there is one
        source, and it is the page.
        """
        button = getattr(self, "_descriptions_btn", None)
        if button is None:
            return
        blocks = self.description_blocks()
        # Nothing open means the next press opens. Anything open means it
        # closes - the same rule the press itself follows.
        will_open = bool(blocks) and not any(b.is_open() for b in blocks)
        button.setEnabled(bool(blocks))
        # The chevron moves with the label, so the control reads the same way
        # a folded header does: pointing down when the press will open.
        button.set_label(
            "Expand descriptions" if will_open else "Collapse descriptions",
            icon=Icons.CHEVRON_DOWN if will_open else Icons.CHEVRON_RIGHT)

    def _build_sort_toolbar(self, in_plugins_category: bool = False) -> QWidget:
        bar = QWidget()
        set_style(bar, "common", "transparent")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(18)
        layout.addStretch()   #everything added after this gets pushed to the right edge

        captions = {
            "alpha":      "Alphabetical",
            "dependants": "Dependants",
            "type":       "Type",
        }
        axes = [a for a in self.SORT_AXES
                if (a != "dependants" or in_plugins_category)
                and (a != "type" or not in_plugins_category)]
        for axis in axes:
            is_active = self._active_sort_mode == axis

            btn = QPushButton()
            btn.setIcon(self._icon_for_axis(axis))
            btn.setIconSize(QSize(44, 20))
            btn.setFixedSize(64, 44)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            set_style(btn, "settings", "sort-button-active" if is_active else "sort-button")
            # Carried on the widget so _refresh_sort_toolbar() can restyle it
            # without rebuilding the bar.
            btn.setProperty("sort_axis", axis)
            btn.clicked.connect(lambda _, a=axis: self._click_sort_axis(a))

            cap_lbl = QLabel(captions[axis])
            cap_lbl.setFont(make_font(SIZES.S1))
            set_style(cap_lbl, "common", "text-muted")

            pair = QWidget()
            set_style(pair, "common", "transparent")
            pair_layout = QHBoxLayout(pair)
            pair_layout.setContentsMargins(0, 0, 0, 0)
            pair_layout.setSpacing(6)
            pair_layout.addWidget(btn)
            pair_layout.addWidget(cap_lbl)

            layout.addWidget(pair)

        return bar

    #Long enough for QScroller to finish replaying a tap. Short enough that the
    #reorder still reads as a response to the press.
    SORT_REBUILD_DELAY = 160

    def _click_sort_axis(self, axis: str) -> None:
        if self._active_sort_mode != axis:
            self._active_sort_mode = axis
            self._sort_direction[axis] = "asc"
        elif self._sort_direction[axis] == "asc":
            self._sort_direction[axis] = "desc"
        else:
            self._active_sort_mode = None
            self._sort_direction[axis] = "asc"

        # Reordered, not rebuilt.
        #
        # _show_category() takes every widget out of the layout with
        # setParent(None) and puts them all back. The content area has
        # QScroller grabbing LeftMouseButtonGesture, which holds a press,
        # decides whether the finger is scrolling, and **replays** press and
        # release to the child if it was a tap - so a teardown landing inside
        # that replay delivers the replayed press to whichever widget now
        # occupies the position, which is how tapping a sort button opened the
        # keyboard for a setting.
        #
        # Deferring it was not enough, because the teardown is the problem
        # rather than its timing. Moving the blocks within the layout leaves
        # every widget parented and shown throughout, so there is nothing for a
        # replayed press to land on by accident.
        self._reorder_content()

    def _reorder_content(self) -> None:
        """
        Put the content blocks in the sorted order, in place.

        No teardown: each block is removed from the layout and reinserted, so
        it keeps its parent and stays visible. Rebuilding the section instead
        works, and is what a page switch does - but doing it from inside a tap
        is what put a settings field under the finger that had just pressed a
        sort button.
        """
        path = getattr(self, "_active_path", None)
        if path is None:
            return
        cat_key, sub_key = path
        entry = self.categories.get(cat_key)
        if not entry:
            return
        target = entry if sub_key is None else entry["subs"].get(sub_key)
        if not target:
            return

        # Where the blocks start: after the header, the extras and the toolbar.
        blocks = [b for b in target["content"] if isinstance(b, QWidget)]
        if not blocks:
            return
        positions = [self._content_layout.indexOf(b) for b in blocks]
        positions = [i for i in positions if i >= 0]
        if not positions:
            return
        first = min(positions)

        for offset, block in enumerate(self._sorted_content(target["content"])):
            if not isinstance(block, QWidget):
                continue
            self._content_layout.removeWidget(block)
            self._content_layout.insertWidget(
                first + offset, block,
                stretch=1 if getattr(block, "fills_height", False) else 0)

        self._refresh_sort_toolbar()

    def _refresh_sort_toolbar(self) -> None:
        """
        Restyle the toolbar buttons for the axis now active.

        The toolbar is built with the active button already styled, so a
        rebuild used to be how that got updated. Restyling in place keeps the
        buttons the person is pressing exactly where they were.
        """
        toolbar = getattr(self, "_sort_toolbar", None)
        if toolbar is None:
            return
        try:
            for button in toolbar.findChildren(QPushButton):
                axis = button.property("sort_axis")
                if not axis:
                    continue
                button.setIcon(self._icon_for_axis(axis))
                set_style(button, "settings",
                          "sort-button-active"
                          if self._active_sort_mode == axis else "sort-button")
        except RuntimeError:
            pass

    def _sorted_content(self, content: list) -> list:
        if not self._active_sort_mode:
            return content

        sortable  = [w for w in content if hasattr(w, "sort_label")]
        direction = self._sort_direction.get(self._active_sort_mode, "asc")
        reverse   = (direction == "desc")

        if self._active_sort_mode == "alpha":
            sortable.sort(key=lambda w: w.sort_label.lower(), reverse=reverse)
        elif self._active_sort_mode == "dependants":
            sortable.sort(key=lambda w: getattr(w, "sort_dependants", 0), reverse=reverse)
        elif self._active_sort_mode == "type":
            sortable.sort(key=lambda w: getattr(w, "sort_type", ""), reverse=reverse)
        return sortable


    def _build_dependency_line(self, plugin_key: str) -> QLabel | None:
        own_deps  = self.client.PLUGIN.get_dependencies(plugin_key)
        dependants = self.client.PLUGIN.get_dependants(plugin_key)
        if not own_deps and not dependants:
            return None

        parts = []
        if own_deps:
            annotated = [
                key if self.client.PLUGIN.has_plugin(key) else f"{key} (not loaded)"
                for key in own_deps
            ]
            parts.append("Depends on: " + ", ".join(annotated))
        if dependants:
            parts.append("Required by: " + ", ".join(dependants))

        line = QLabel("   •   ".join(parts))
        line.setFont(make_font(SIZES.S1))
        line.setWordWrap(True)
        set_style(line, "common", "text-muted")
        return line

    def _plugin_menu_items(self, plugin, plugin_key: str) -> list:
        """
        The same actions as the buttons, as menu entries.

        Built separately rather than by reading labels off the buttons: a menu
        line has room to say what the action does to *this* plugin, which a
        button on a crowded row does not.
        """
        dependants = self.client.PLUGIN.get_dependants(plugin_key)
        items = [
            ("Copy its key", lambda: self._copy_plugin_key(plugin_key),
             Icons.KEY),
            ("Reload it", lambda: self._reload_plugin(plugin_key),
             Icons.REFRESH),
        ]
        if dependants:
            # Present but honest. Leaving it out entirely would raise the
            # question of why this plugin has fewer options than the last one.
            items.append((f"Cannot unload - needed by {len(dependants)} other",
                          lambda: self.client.alert(
                              "Cannot unload",
                              "Still required by: " + ", ".join(dependants)),
                          Icons.POWER, "quiet"))
        else:
            items.append(("Unload it", lambda: self._unload_plugin(plugin_key),
                          Icons.POWER, "destructive"))

        specs = self.client.PLUGIN.plugin_requirements(plugin_key)
        if specs:
            from src.plugin import dependencies as deps
            removable, kept = deps.removable_for(
                specs, self.client.PLUGIN.other_plugin_requirements(plugin_key))
            if removable:
                items.append((
                    "Uninstall its packages",
                    lambda r=removable, k=kept:
                        self._uninstall_plugin_packages(plugin_key, r, k),
                    Icons.DELETE_OUTLINE, "destructive"))
            else:
                # The same reasoning the button's tooltip carries, said in a
                # place somebody will actually see it - a tooltip on a touch
                # screen is a tooltip nobody reads.
                reasons = "; ".join(f"{n} - {r}" for n, r in kept.items())
                items.append((
                    "Nothing to uninstall",
                    lambda why=reasons: self.client.alert(
                        "Nothing to uninstall",
                        why or "No packages installed."),
                    Icons.DELETE_OUTLINE, "quiet"))
        return items

    def _build_plugin_actions(self, plugin, plugin_key: str) -> list:
        # All the same size, all carrying an icon. Three buttons at 44px with
        # three different minimum widths and three separate stylesheet classes
        # is how a row of them ended up looking hand-placed.
        copy_btn = ActionButton(Icons.KEY, "Copy Key",
                                lambda: self._copy_plugin_key(plugin_key),
                                kind="quiet")
        reload_btn = ActionButton(Icons.REFRESH, "Reload",
                                  lambda: self._reload_plugin(plugin_key),
                                  kind="secondary")

        dependants = self.client.PLUGIN.get_dependants(plugin_key)
        unload_btn = ActionButton(
            Icons.POWER, "Unload",
            None if dependants else (lambda: self._unload_plugin(plugin_key)),
            kind="destructive", enabled=not dependants)
        if dependants:
            unload_btn.setCursor(Qt.CursorShape.ForbiddenCursor)
            # Said, not merely greyed. A disabled button with no reason on it is
            # a dead end.
            unload_btn.setToolTip(
                "Can't unload — required by currently loaded plugin(s): "
                + ", ".join(dependants))

        buttons = [copy_btn, reload_btn, unload_btn]

        specs = self.client.PLUGIN.plugin_requirements(plugin_key)
        if specs:
            buttons.append(self._build_uninstall_button(plugin_key, specs))

        return buttons

    def _build_uninstall_button(self, plugin_key: str, specs: list) -> QPushButton:
        from src.plugin import dependencies as deps

        btn = QPushButton("Uninstall")
        btn.setFont(make_font(SIZES.S2, bold=True))
        btn.setFixedHeight(44)
        btn.setMinimumWidth(100)

        removable, kept = deps.removable_for(
            specs, self.client.PLUGIN.other_plugin_requirements(plugin_key)
        )

        if not removable:
            btn.setEnabled(False)
            btn.setCursor(Qt.CursorShape.ForbiddenCursor)
            reasons = "; ".join(f"{n} — {r}" for n, r in kept.items())
            btn.setToolTip("Nothing to remove. " + (reasons or "No packages installed."))
            set_style(btn, "settings", "plugin-action-uninstall-disabled")
            return btn

        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(btn, "settings", "plugin-action-uninstall")
        btn.setToolTip("Uninstall this plugin's pip packages and unload it")
        btn.clicked.connect(lambda: self._uninstall_plugin_packages(plugin_key, removable, kept))
        return btn

    def _uninstall_plugin_packages(self, plugin_key: str,
                                    removable: list, kept: dict) -> None:
        name = self.client.PLUGIN.plugin_name(plugin_key) or plugin_key
        detail = "Will be removed:\n  " + "\n  ".join(removable)
        if kept:
            detail += "\n\nWill be kept:\n  " + "\n  ".join(
                f"{n} — {r}" for n, r in kept.items()
            )

        def _go() -> None:
            def worker() -> None:
                ok, message = self.client.PLUGIN.uninstall_plugin_packages(plugin_key)
                if not ok:
                    self.client.simple_notify("error", "Plugins", message[:160])
            Thread(target=worker, name="__plugin_pip_uninstall", daemon=True).start()

        self.client.confirm(
            title=f"Uninstall packages for '{name}'?",
            body=("This removes the packages from the virtualenv and unloads "
                  "the plugin. The plugin's files are left alone — it will "
                  "reappear in this list with an Install button."),
            detail=detail,
            confirm_text="Uninstall",
            on_confirm=_go,
            destructive=True,
        )

    def _copy_plugin_key(self, plugin_key: str) -> None:
        self.client.app.clipboard().setText(plugin_key)
        self.client.simple_notify(Icons.COPY, "Settings", f"Copied '{plugin_key}' to clipboard.")

    def _reload_plugin(self, plugin_key: str) -> None:
        self.client.call_on_ui(lambda: self.client.PLUGIN.reload_plugin(plugin_key))

    def _unload_plugin(self, plugin_key: str) -> None:
        def _do():
            if not self.client.PLUGIN.unload_plugin(plugin_key):
                dependants = self.client.PLUGIN.get_dependants(plugin_key)
                detail = (f" — required by: {', '.join(dependants)}" if dependants else "")
                self.client.simple_notify(Icons.WARNING, "Settings",
                                           f"Couldn't unload '{plugin_key}'{detail}.")
                return
            self.client.simple_notify(Icons.DELETE, "Settings", f"'{plugin_key}' was unloaded.")
            self.client.goto("#settings", override=True)
        self.client.call_on_ui(_do)

    # ── Navigation ───────────────────────────────────────────────────────────

    def _make_nav_button(self, label: str, indent: bool, icon: str = None,
                         inactive_plugin: str = "") -> QPushButton:
        """
        One nav entry.

        `inactive_plugin` marks a plugin that is present and not running -
        "stopped" or "blocked". A nav list where a loaded plugin and an
        unloaded one look identical is a list you have to open every entry of
        to find out what is going on, and the answer is on the card behind it
        rather than in the list you are reading.
        """
        if inactive_plugin:
            label = f"{label}  \u00b7  {inactive_plugin}"
        btn = QPushButton(label)
        btn.setFont(make_font(SIZES.S1 if indent else SIZES.S2))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # 48 is the floor for a finger. A sub-item is a little shorter than a
        # top-level one, but not below what can be hit.
        btn.setFixedHeight(48 if indent else 54)
        btn.setCheckable(True)
        if icon:
            q_icon = resolve_plugin_icon(icon)
            if q_icon:
                btn.setIcon(q_icon)
                btn.setIconSize(QSize(18, 18))
        self._apply_nav_style(btn, "inactive", indent)
        if inactive_plugin:
            # Dimmed and italic on top of whatever the state style does, so
            # it still highlights when selected and still reads as "here, but
            # not running" when it is not.
            font = btn.font()
            font.setItalic(True)
            btn.setFont(font)
            btn.setProperty("plugin_state", inactive_plugin)
            set_style(btn, "settings", "settings-nav-inactive")
        return btn

    @mixin_target("settings.setup.tab.generation")
    def _nav_heading(self, text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setFont(make_font(SIZES.S1, bold=True))
        set_style(label, "settings", "settings-nav-heading")
        return label

    def _build_nav(self) -> None:
        self._nav_buttons: dict[tuple, QPushButton] = {}
        first_path = None

        # Two sections. A settings page is a list of values to change and
        # save; System pages are live views that act as you press them, and
        # mixing them in one list gives no clue which is which.
        system = [(k, e) for k, e in self.categories.items() if e.get("system")]
        settings = [(k, e) for k, e in self.categories.items()
                    if not e.get("system")]

        for heading, group in (("System", system), ("Settings", settings)):
            if not group:
                continue
            if self._nav_list.count():
                spacer = QWidget()
                spacer.setFixedHeight(14)
                set_style(spacer, "common", "transparent")
                self._nav_list.addWidget(spacer)
            self._nav_list.addWidget(self._nav_heading(heading))

            for cat_key, entry in group:
                path = (cat_key, None)
                btn = self._make_nav_button(entry["label"], indent=False)
                btn.clicked.connect(lambda _, p=path: self._switch_tab(p))
                self._nav_list.addWidget(btn)
                self._nav_buttons[path] = btn
                # The first entry of the FIRST section, so the page opens on
                # something rather than on whichever happened to be built
                # first.
                if first_path is None:
                    first_path = path

                subs = entry.get("subs") or {}
                if subs:
                    rail = QFrame()
                    set_style(rail, "settings", "settings-nav-rail")
                    rail_layout = QVBoxLayout(rail)
                    rail_layout.setContentsMargins(14, 4, 0, 4)
                    rail_layout.setSpacing(4)
                    for sub_key, sub_entry in subs.items():
                        sub_path = (cat_key, sub_key)
                        sub_btn = self._make_nav_button(
                            sub_entry["label"], indent=True,
                            icon=sub_entry.get("icon"),
                            inactive_plugin=sub_entry.get("inactive") or "")
                        sub_btn.clicked.connect(lambda _, p=sub_path: self._switch_tab(p))
                        rail_layout.addWidget(sub_btn)
                        self._nav_buttons[sub_path] = sub_btn
                    self._nav_list.addWidget(rail)

        # A section asked for by whatever opened the page wins over the first
        # one, so "tap the network in the quick panel" lands on Wi-Fi rather
        # than on the top of the list with the right thing three taps away.
        wanted = self._requested_section()
        if wanted is not None and wanted in self._nav_buttons:
            self._select_path(wanted)
        elif first_path:
            self._select_path(first_path)

    def _requested_section(self):
        """
        The nav path goto() asked for, if it exists.

        Checked against the buttons that were actually built rather than
        trusted: a section can be missing because its plugin failed to load,
        and a stale deep link should land somewhere real instead of on a blank
        page.
        """
        data = getattr(self, "data", None) or {}
        name = str(data.get("section") or "").strip().lower()
        sub = data.get("subsection")

        if not name:
            # Nothing asked for: go back to where this page was.
            #
            # Revoking a user, uninstalling a plugin and saving a calendar all
            # rebuild the page with goto("#settings", override=True) and no
            # data, because what they changed is on the page. Landing on the
            # first section afterwards means the thing you were doing is now
            # three taps away - and you were mid-way through doing it.
            remembered = getattr(self.client, self.LAST_SECTION_ATTR, None)
            if remembered and remembered in self._nav_buttons:
                return remembered
            return None
        path = (name, str(sub).strip().lower() if sub else None)
        if path in self._nav_buttons:
            return path
        if (name, None) in self._nav_buttons:
            return (name, None)
        self.client.log("info", f"[Settings] No '{name}' section to open.")
        return None

    def _apply_nav_style(self, btn: QPushButton, state: str, indent: bool = False) -> None:
        bg = {"active": "rgba(255,255,255,18)",
              "parent": "rgba(255,255,255,8)",
              "inactive": "transparent"}[state]
        clazz = "settings-nav-subbutton" if indent else "settings-nav-button"
        set_style(btn, "settings", clazz, override={"*": {"background": bg}})

    def _switch_tab(self, path: tuple) -> None:
        self._select_path(path)

    def _select_path(self, path: tuple) -> None:
        cat_key, sub_key = path
        for p, btn in self._nav_buttons.items():
            is_active = (p == path)
            is_parent = (not is_active and sub_key is not None and p == (cat_key, None))
            btn.setChecked(is_active)
            self._apply_nav_style(
                btn, "active" if is_active else ("parent" if is_parent else "inactive"),
                indent=(p[1] is not None),
            )
        self._active_path = path
        self._show_category(path)

    #Where the settings page was, kept on the CLIENT.
    #
    #goto() destroys the page, so anything stored on self goes with it - and it
    #is the rebuild that needs to know.
    LAST_SECTION_ATTR = "_settings_last_section"

    def _show_category(self, path: tuple) -> None:
        cat_key, sub_key = path
        try:
            setattr(self.client, self.LAST_SECTION_ATTR, path)
        except Exception:
            pass
        entry = self.categories.get(cat_key)
        if not entry:
            return
        target = entry if sub_key is None else entry["subs"].get(sub_key)
        if not target:
            return

        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        header = self._build_category_header(
            target["label"],
            plugin=target.get("plugin"),
            plugin_key=target.get("plugin_key"),
            has_content=bool(target["content"]),
            icon=target.get("icon"),
            readme=target.get("readme"),
            pending=target.get("pending"),
            conflict=target.get("conflict"),
        )
        self._content_layout.insertWidget(self._content_layout.count() - 1, header)

        # Registrations sit between the plugin's description and its settings,
        # and only on a plugin's own page - a top-level category has no owner
        # key to look anything up with.
        owner = target.get("plugin_key")
        if owner and sub_key is not None:
            block = self._build_registrations_block(owner)
            if block is not None:
                # A block that wants the page gets the page.
                #
                # The layout ends in a stretch, so everything takes its natural
                # height and the spare goes to the bottom. That is right for a
                # column of setting cards and wrong for the log, which is a
                # single view that should fill what is left.
                self._content_layout.insertWidget(
                    self._content_layout.count() - 1, block,
                    stretch=1 if getattr(block, "fills_height", False) else 0)

            # A plugin may contribute its own cards here, between the registry
            # summary and its settings, by defining settings_blocks(). Kept
            # outside the sort toolbar below since these are static content,
            # not sortable setting blocks.
            for extra in self._plugin_settings_blocks(target.get("plugin")):
                self._content_layout.insertWidget(self._content_layout.count() - 1, extra)

        # Only where there is something to sort.
        #
        # Not "only on generated sections": Plugins is a system page and does
        # sort. The test is whether the content actually carries sort labels,
        # which is the same test _sorted_content() uses to do the sorting - so
        # the toolbar cannot appear above content it would not reorder.
        self._sort_toolbar = None
        if self._is_sortable(target["content"]):
            toolbar = self._build_sort_toolbar(
                in_plugins_category=(cat_key == "plugins"))
            self._sort_toolbar = toolbar
            self._content_layout.insertWidget(
                self._content_layout.count() - 1, toolbar)

        for block in self._sorted_content(target["content"]):
            if isinstance(block, QWidget):
                # A block that wants the page gets the page.
                #
                # The layout ends in a stretch, so everything takes its natural
                # height and the spare goes to the bottom. That is right for a
                # column of setting cards and wrong for the log, which is a
                # single view that should fill what is left.
                self._content_layout.insertWidget(
                    self._content_layout.count() - 1, block,
                    stretch=1 if getattr(block, "fills_height", False) else 0)

        # The blocks are new, so the button is out of date and the folds have
        # nothing watching them. Wired here rather than in SettingBlock: a
        # block is built by the page but is not the page, and handing it a
        # reference back would be the one thing it does not otherwise need.
        for block in self.description_blocks():
            block._on_toggle = self._sync_descriptions_button
        self._sync_descriptions_button()

        self._content_scroll.verticalScrollBar().setValue(0)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @mixin_target("settings.timeout")
    def interaction_timeout(self, event=None) -> None:
        self.client.call_on_ui(self._do_interaction_timeout)

    def _do_interaction_timeout(self) -> None:
        self.client.simple_notify(
            Icons.TIMER, "Settings: Timeout",
            "No interaction — returning to home screen."
        )
        self.return_and_save(notify=False)

    def home_page(self) -> str:
        target = self.client.DEFAULT_PAGE or "#root"
        return target if self.client.has_page(target) else "#root"

    def return_without_saving(self, event=None) -> None:
        """
        Leave, keeping whatever was on disk.

        Said out loud when something was changed, rather than silently. A
        control that discards work without a word is one people stop trusting
        after the first time it costs them something.
        """
        # Immediately, before anything slow - exactly as Save and Return does.
        #
        # This button looked ignored where the other one did not, and it is
        # not instant: unsaved() scrubs and compares the whole settings tree,
        # and goto() then tears this page down and builds the home page. On a
        # touch panel a control that does not change when it is touched has
        # not been touched, so it gets touched again.
        self._leaving()

        if self.unsaved():
            self.client.simple_notify(Icons.SETTINGS, "Settings",
                                      "Left without saving - the changes were "
                                      "not kept.")
        self.client.goto(self.home_page())

    def unsaved(self) -> bool:
        """Whether anything on this page differs from what is on disk."""
        try:
            return (scrub_secrets(self._working_settings.to_dict())
                    != scrub_secrets(self.client.SETTINGS.to_dict()))
        except Exception:
            # Cannot tell, so assume there is something. Warning about
            # nothing costs a line; staying quiet costs the changes.
            return True

    # The mixin target belongs to the method plugins mean by "save", which
    # is this one. It sat above whatever happened to be written first,
    # and inserting a helper there moved it silently.
    @mixin_target("settings.save")
    def return_and_save(self, event=None, notify: bool = True) -> None:
        """
        Save and go home, with the going home first.

        **The subscribers are the slow part, not the saving.** Writing the
        file and applying the values takes a moment; `on_settings_saved`
        restarts the assistant, re-reads calendars and rebuilds pages, and all
        of that ran before the page was left. The press looked ignored for
        several seconds, which on a touch panel means it gets pressed again.

        So: acknowledge the press, save, leave, and let the subscribers run
        with the home page already up. Nothing they do needs this page to
        still exist.
        """
        # Immediately, before anything slow. A control that does not change
        # when it is touched has not been touched, as far as anybody can tell.
        self._saving()

        saved = scrub_secrets(self._working_settings.to_dict())
        self.client.dump(saved, self.client.DATA)
        self.client.apply_settings(saved)
        if notify:
            self.client.simple_notify(Icons.SAVE, "Settings", "Settings saved!")

        # The client rather than `self`: goto() destroys this page, and the
        # deferred call must not reach through a widget that has gone.
        client = self.client
        client.goto(self.home_page())
        QTimer.singleShot(0, lambda: client.iterate_event_callables(
            "on_settings_saved", client.SETTINGS))

    def _saving(self) -> None:
        """Say the press landed, before the work that follows it."""
        self._acknowledge("_save_btn", "Saving\u2026")

    def _leaving(self) -> None:
        """The same, for the button that leaves without saving."""
        self._acknowledge("_leave_btn", "Leaving\u2026")

    def _acknowledge(self, attribute: str, label: str) -> None:
        """
        Show a press on one of the top-bar buttons before acting on it.

        One method rather than two nearly identical ones: they differ only in
        which button and which word, and the version of this that existed for
        Save only was written out in full - which is how Return ended up
        without it at all.
        """
        button = getattr(self, attribute, None)
        if button is None:
            return
        try:
            button.setEnabled(False)
            button.set_label(label)
            # Painted now. Qt would otherwise draw this at the end of the
            # event handler - which is after everything this was meant to
            # cover, so nothing would ever be seen.
            self.client.app.processEvents()
        except Exception:
            pass

    def start(self) -> None:
        super().start()
        self.client.subscribe_to_event("on_interaction_timeout", self.interaction_timeout)
        # The Users list is built from the registry, not from a setting, so
        # nothing redraws it when a device is approved from the dialog that
        # appears over this very page.
        self.client.USERS.subscribe(self._users_changed)

    def stop(self) -> None:
        super().stop()
        self.client.unsubscribe_from_event("on_interaction_timeout", self.interaction_timeout)
        self.client.USERS.unsubscribe(self._users_changed)



    def _users_changed(self) -> None:
        """
        Rebuild the Users category in place.

        Not a navigation: re-entering the page would drop whatever category
        the person was reading and scroll them back to the top, which for
        somebody who just approved a device is the wrong place.
        """
        def apply():
            try:
                if self.client.PAGE is not self:
                    return
                entry = self.categories.get("users")
                if entry is None:
                    return
                entry["content"] = _build_users_page(self.client)
                # Redrawn only if it is the category actually on screen -
                # rebuilding the list behind three other pages is work nobody
                # sees, and _show_category tears down the visible content.
                if getattr(self, "_active_path", (None, None))[0] == "users":
                    self._show_category(self._active_path)
            except RuntimeError:
                pass
        self.client.call_on_ui(apply)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        BAR_H = 70
        self._grid.setGeometry(0, 0, w, h)
        self._top_bar.setGeometry(0, 0, w, BAR_H)
        self._body.setGeometry(0, BAR_H, w, h - BAR_H)