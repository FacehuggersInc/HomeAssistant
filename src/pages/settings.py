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
    QScrollArea, QLineEdit, QTextEdit, QComboBox, QFrame, QSizePolicy, QFileDialog,
    QScroller,
)
from PyQt6.QtCore import Qt, QSize, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QPixmap, QIcon

from src.mixins import mixin_target
from src.settings import Settings, scrub_secrets
from src.ui.page import PageFramework
from src.ui.widget import WidgetFramework
from src.ui.icons import Icons, icon, resolve_plugin_icon
from src.styling import COLORS, SIZES, make_font, set_style, get_style_sheet
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
    for sep in ("_", "-"):
        if sep in name:
            return " ".join(w.capitalize() for w in name.split(sep))
    return " ".join(f"{w[0].upper()}{w[1:]}" for w in name.split(" "))


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

        self._anim = QPropertyAnimation(self, b"thumbX")
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


def normalize_setting_type(raw_t: str) -> str:
    t = "list" if raw_t.startswith("list") else raw_t
    if t in ("double",):
        t = "float"
    return t


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

class SettingBlock(QFrame):
    def __init__(self, client, setting=None, key="", content: QWidget = None):
        super().__init__()
        self.client  = client
        self._setting = setting
        self._initial_value = copy.deepcopy(setting.get("value")) if setting else None
        set_style(self, "settings", "setting-block")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.sort_label = (setting.get("name") if setting else None) or format_name(key) or ""
        self.sort_type = normalize_setting_type(setting.get("type", "")) if setting else ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        if content:
            outer.addWidget(content)
            return

        # Header
        header = QHBoxLayout()
        header.setSpacing(12)
        header.setContentsMargins(0, 0, 0, 0)

        name_lbl = QLabel(setting.get("name") or format_name(key))
        name_lbl.setFont(make_font(SIZES.S2, bold=True))
        set_style(name_lbl, "common", "text-strong")
        header.addWidget(name_lbl)

        self._modified_badge = QLabel("Modified")
        self._modified_badge.setFont(make_font(SIZES.S1, bold=True))
        set_style(self._modified_badge, "settings", "modified-badge")
        header.addWidget(self._modified_badge)
        self._refresh_modified_badge()

        header.addStretch()
        outer.addLayout(header)

        desc = setting.get("description", "")
        if desc:
            dl = QLabel(desc)
            dl.setFont(make_font(SIZES.S1))
            set_style(dl, "common", "text-muted")
            dl.setWordWrap(True)
            outer.addWidget(dl)

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
                from PyQt6.QtWidgets import QFileDialog
                current = str(_s["value"])
                chosen = QFileDialog.getExistingDirectory(None, "Select folder", current)
                if chosen:
                    _s["value"] = chosen
                    for child in _f.children():
                        if isinstance(child, QLineEdit):
                            child.setText(chosen)
                            break
                    self._refresh_modified_badge()
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


# ── Info page ─────────────────────────────────────────────────────────────────

def _build_info_page(client) -> list:
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

    rows = [
        ("Application",  client.WINDOW_NAME),
        ("Client ID",    client.CLIENT_ID),
        ("Local IP",     _local_ip()),
        ("API Port",     "5000"),
        ("Platform",     f"{platform.system()} {platform.release()}"),
        ("Python",       platform.python_version()),
        ("Data Path",    str(client.DATAPATH)),
    ]

    widgets = []
    for label, value in rows:
        card = QFrame()
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(card, "settings", "setting-block")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)

        lbl = QLabel(label)
        lbl.setFont(make_font(SIZES.S2, bold=True))
        set_style(lbl, "common", "text-muted")
        lbl.setFixedWidth(120)

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
        f"Client ID is required as ?id=CLIENT_ID on all control and asset API requests."
    )
    hint.setFont(make_font(SIZES.S1))
    set_style(hint, "settings", "settings-hint")
    hint.setWordWrap(True)
    widgets.append(hint)

    return widgets

# ── Settings page ─────────────────────────────────────────────────────────────

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

        back_btn = QPushButton("← Save and Return")
        back_btn.setFont(make_font(SIZES.S3, bold=True))
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.setFixedHeight(44)
        set_style(back_btn, "settings", "settings-back-button")
        back_btn.clicked.connect(self.return_and_save)

        tl.addWidget(back_btn)
        tl.addStretch()

        # ── Body ──────────────────────────────────────────────────────────────
        body = QWidget(self)
        body.setGeometry(0, BAR_H, w, h - BAR_H)
        set_style(body, "common", "transparent")
        self._body = body

        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        # Nav panel
        nav_panel = QWidget()
        nav_panel.setFixedWidth(NAV_W)
        set_style(nav_panel, "settings", "settings-nav-panel")
        nl = QVBoxLayout(nav_panel)
        nl.setContentsMargins(PAD, PAD, PAD, PAD)
        nl.setSpacing(4)

        self._nav_list = QVBoxLayout()
        self._nav_list.setSpacing(4)
        nl.addLayout(self._nav_list)
        nl.addStretch()
        bl.addWidget(nav_panel)

        # Content scroll
        self._content_scroll = QScrollArea()
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setStyleSheet(get_style_sheet("settings_scroll"))
        self._content_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        QScroller.grabGesture(self._content_scroll.viewport(),
                               QScroller.ScrollerGestureType.LeftMouseButtonGesture)

        self._content_widget = QWidget()
        set_style(self._content_widget, "common", "transparent")
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(PAD, PAD, PAD, 100)
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
            "new_settings_list":      self.builder,
        })

        self._working_settings = Settings(copy.deepcopy(client.settings_dict()))
        self._generate_settings(self._working_settings, self._working_settings.to_dict())
        self._page_additions()
        self._build_nav()

    # ── Builder ───────────────────────────────────────────────────────────────

    def new_category(self, name: str, controls: list, label: str = None) -> None:
        self.categories[name] = {
            "label":      label or format_name(name),
            "content":    controls,
            "subs":       {},
            "plugin":     None,
            "plugin_key": None,
            "icon":       None,
            "readme":     None,
            "pending":    None,
        }

    def new_subcategory(self, parent: str, name: str, controls: list,
                         label: str = None, plugin=None, plugin_key: str = None,
                         icon: str = None, readme: str = None,
                         pending=None) -> None:
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
        }

    def insert_block(self, category: str, index: int, content: QWidget) -> None:
        entry = self.categories.get(category)
        if entry:
            entry["content"].insert(index, SettingBlock(self.client, content=content))

    def builder(self, pointer, data: dict, filter_key: str = "", path: str = "") -> list:
        group = []
        if not isinstance(data, dict):
            self.client.log("warning", f"[SettingsPage.builder] data was not a Dictionary to be read (was {type(data)})")
            return group
        settings = data[filter_key] if filter_key else data
        for key, val in settings.items():
            if not isinstance(val, dict):
                self.client.log("warning", f"[SettingsPage.builder] The value under '{key}' was not a Valid object to be built with. (was {type(val)}, meant to be dict)")
                continue
            extended_path = f"{path}.{key}" if path else key
            if "type" in val and "value" in val:
                try:
                    obj = pointer
                    for part in extended_path.split("."):
                        obj = obj[part]
                    group.append(SettingBlock(client=self.client, setting=obj, key=key))
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
        # Info is always last
        self.new_category("info", _build_info_page(self.client))

    def _page_additions(self) -> None:
        plugins = self.client.PLUGIN.get_plugins()

        overview = []
        for plugin, key in plugins:
            icon_value = plugin.config.get_path("plugin.icon", None)
            overview.append(self._build_category_header(
                plugin.config.plugin.name,
                plugin=plugin, plugin_key=key,
                has_content=True, icon=icon_value, readme=None,
            ))

        for item in self.client.PLUGIN.pending_plugins():
            overview.append(self._build_pending_header(item))

        self.new_category("plugins", overview, label="Plugins")

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
            )

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
        set_style(title, "common", "text-pending")
        top_row.addWidget(title)

        badge = QLabel("NOT INSTALLED")
        badge.setFont(make_font(SIZES.S1, bold=True))
        set_style(badge, "settings", "pending-badge")
        top_row.addWidget(badge)
        top_row.addStretch()

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
                                has_content: bool = True, icon: str = None,
                                readme: str = None, pending=None) -> QFrame:
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
        set_style(title, "common", "text-strong")
        top_row.addWidget(title)
        top_row.addStretch()

        if plugin_key:
            for btn in self._build_plugin_actions(plugin, plugin_key):
                top_row.addWidget(btn)

        layout.addLayout(top_row)

        if plugin_key:
            sub = QLabel(plugin_key)
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

        title = QLabel(name)
        title.setFont(make_font(SIZES.S3, bold=True))
        set_style(title, "common", "text-strong")
        top.addWidget(title)

        count = QLabel(str(len(entries)))
        count.setFont(make_font(SIZES.S1, bold=True))
        count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        count.setMinimumWidth(26)
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

    def _build_readme_block(self, readme_path: str) -> QLabel | None:
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
        return label


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

    def _click_sort_axis(self, axis: str) -> None:
        if self._active_sort_mode != axis:
            self._active_sort_mode = axis
            self._sort_direction[axis] = "asc"
        elif self._sort_direction[axis] == "asc":
            self._sort_direction[axis] = "desc"
        else:
            self._active_sort_mode = None
            self._sort_direction[axis] = "asc"
        self._show_category(self._active_path)

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

    def _build_plugin_actions(self, plugin, plugin_key: str) -> list[QPushButton]:
        copy_btn = QPushButton("Copy Key")
        copy_btn.setFont(make_font(SIZES.S2, bold=True))
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setFixedHeight(44)
        copy_btn.setMinimumWidth(100)
        set_style(copy_btn, "settings", "plugin-action-copy")
        copy_btn.clicked.connect(lambda: self._copy_plugin_key(plugin_key))

        reload_btn = QPushButton("Reload")
        reload_btn.setFont(make_font(SIZES.S2, bold=True))
        reload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reload_btn.setFixedHeight(44)
        reload_btn.setMinimumWidth(90)
        set_style(reload_btn, "settings", "plugin-action-reload")
        reload_btn.clicked.connect(lambda: self._reload_plugin(plugin_key))

        unload_btn = QPushButton("Unload")
        unload_btn.setFont(make_font(SIZES.S2, bold=True))
        unload_btn.setFixedHeight(44)
        unload_btn.setMinimumWidth(90)

        dependants = self.client.PLUGIN.get_dependants(plugin_key)
        if dependants:
            unload_btn.setEnabled(False)
            unload_btn.setCursor(Qt.CursorShape.ForbiddenCursor)
            unload_btn.setToolTip(
                "Can't unload — required by currently loaded plugin(s): "
                + ", ".join(dependants)
            )
            set_style(unload_btn, "settings", "plugin-action-unload-disabled")
        else:
            unload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            set_style(unload_btn, "settings", "plugin-action-unload")
            unload_btn.clicked.connect(lambda: self._unload_plugin(plugin_key))

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

    def _make_nav_button(self, label: str, indent: bool, icon: str = None) -> QPushButton:
        btn = QPushButton(label)
        btn.setFont(make_font(SIZES.S1 if indent else SIZES.S2))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(40 if indent else 44)
        btn.setCheckable(True)
        if icon:
            q_icon = resolve_plugin_icon(icon)
            if q_icon:
                btn.setIcon(q_icon)
                btn.setIconSize(QSize(18, 18))
        self._apply_nav_style(btn, "inactive", indent)
        return btn

    @mixin_target("settings.setup.tab.generation")
    def _build_nav(self) -> None:
        self._nav_buttons: dict[tuple, QPushButton] = {}
        first_path = None

        for cat_key, entry in self.categories.items():
            path = (cat_key, None)
            btn = self._make_nav_button(entry["label"], indent=False)
            btn.clicked.connect(lambda _, p=path: self._switch_tab(p))
            self._nav_list.addWidget(btn)
            self._nav_buttons[path] = btn
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
                    sub_btn = self._make_nav_button(sub_entry["label"], indent=True, icon=sub_entry.get("icon"))
                    sub_btn.clicked.connect(lambda _, p=sub_path: self._switch_tab(p))
                    rail_layout.addWidget(sub_btn)
                    self._nav_buttons[sub_path] = sub_btn
                self._nav_list.addWidget(rail)

        if first_path:
            self._select_path(first_path)

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

    def _show_category(self, path: tuple) -> None:
        cat_key, sub_key = path
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
        )
        self._content_layout.insertWidget(self._content_layout.count() - 1, header)

        # Registrations sit between the plugin's description and its settings,
        # and only on a plugin's own page - a top-level category has no owner
        # key to look anything up with.
        owner = target.get("plugin_key")
        if owner and sub_key is not None:
            block = self._build_registrations_block(owner)
            if block is not None:
                self._content_layout.insertWidget(self._content_layout.count() - 1, block)

            # A plugin may contribute its own cards here, between the registry
            # summary and its settings, by defining settings_blocks(). Kept
            # outside the sort toolbar below since these are static content,
            # not sortable setting blocks.
            for extra in self._plugin_settings_blocks(target.get("plugin")):
                self._content_layout.insertWidget(self._content_layout.count() - 1, extra)

        toolbar = self._build_sort_toolbar(in_plugins_category=(cat_key == "plugins"))
        self._content_layout.insertWidget(self._content_layout.count() - 1, toolbar)

        for block in self._sorted_content(target["content"]):
            if isinstance(block, QWidget):
                self._content_layout.insertWidget(self._content_layout.count() - 1, block)

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

    @mixin_target("settings.save")
    def return_and_save(self, event=None, notify: bool = True) -> None:
        saved = scrub_secrets(self._working_settings.to_dict())
        self.client.dump(saved, self.client.DATA)
        self.client.apply_settings(saved)
        self.client.iterate_event_callables("on_settings_saved", self.client.SETTINGS)
        if notify:
            self.client.simple_notify(Icons.SAVE, "Settings", "Settings saved!")
        target = self.client.DEFAULT_PAGE or "#root"
        if not self.client.has_page(target):
            target = "#root"
        self.client.goto(target)

    def start(self) -> None:
        super().start()
        self.client.subscribe_to_event("on_interaction_timeout", self.interaction_timeout)

    def stop(self) -> None:
        super().stop()
        self.client.unsubscribe_from_event("on_interaction_timeout", self.interaction_timeout)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        BAR_H = 70
        self._grid.setGeometry(0, 0, w, h)
        self._top_bar.setGeometry(0, 0, w, BAR_H)
        self._body.setGeometry(0, BAR_H, w, h - BAR_H)