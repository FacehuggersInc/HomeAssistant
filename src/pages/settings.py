from __future__ import annotations
import socket
import platform
import copy
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QScrollArea, QLineEdit, QComboBox, QFrame, QSizePolicy, QFileDialog,
    QScroller,
)
from PyQt6.QtCore import Qt, QSize, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QPixmap, QIcon

from src.mixins import mixin_target
from src.ui.page import PageFramework
from src.ui.widget import WidgetFramework
from src.ui.controls.drawer import Drawer
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons, icon, resolve_plugin_icon
from src.styling import COLORS, SIZES, make_font, set_style, get_style_sheet
from src.ui.keyboard import make_keyboard

if TYPE_CHECKING:
    from src.main import Client

## INTERACTIVE SURFACE COLORS
## -- Field, EnumComponent, and ToggleSwitch's "off" state all share this
## -- translucent white-overlay tier, one step lighter than a
## -- setting-block card, so the editable part of a setting reads as
## -- raised OUT of its card rather than a disconnected solid-gray box.

FIELD_BG           = QColor(255, 255, 255, 40)
FIELD_BORDER       = QColor(255, 255, 255, 55)
FIELD_BORDER_FOCUS = QColor(COLORS.PRIMARY.LIGHT)

## HELPERS

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


class Field(QWidget):
    """
    Input field. Draws its own background via paintEvent so parent
    stylesheet cascades cannot override it.
    """

    def __init__(self, setting, index=None, is_numeric=False, prefix="", suffix="", on_change=None):
        super().__init__()
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

        self.client = setting.client if hasattr(setting, 'client') else None
        le = QLineEdit(str(val))
        le.setFont(make_font(SIZES.S3))
        le.setFixedHeight(44)
        set_style(le, "settings", "field-input", object_tag="QLineEdit")
        _field = self

        def _focus_in(e):
            _field._border = QColor(FIELD_BORDER_FOCUS)
            _field.update()
            QLineEdit.focusInEvent(le, e)
            # Open keyboard popup
            page = _field.window()
            if page and not hasattr(page, "_kb") or (hasattr(page, "_kb") and page._kb is None):
                pass
            try:
                overlay = _field.client.OVERLAYS
                kb = make_keyboard(_field.client, le, _field._setting_type, overlay)
                page._kb = kb
                kb.show_keyboard()
            except Exception:
                pass

        def _focus_out(e):
            _field._border = QColor(FIELD_BORDER)
            _field.update()
            QLineEdit.focusOutEvent(le, e)

        le.focusInEvent  = _focus_in
        le.focusOutEvent = _focus_out

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
    """
    Collapses a raw setting "type" string down to the same category
    SettingBlock actually dispatches on when building a control for it
    — "list[float]"/"list[int]" both become "list", "double" becomes
    "float". Used both there and by the sort toolbar's "type" axis, so
    sorting by type groups things exactly the same way they're already
    grouped by which control renders for them, rather than treating
    e.g. "list[float]" and "list[int]" as two different types just
    because the raw strings differ.
    """
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
        # Snapshot of the value as it was when this block was first
        # built — what _refresh_modified_badge() actually compares
        # against, NOT the template's "default" field. "Modified" means
        # "you've changed this since opening the page", not "differs
        # from the factory default forever" — comparing against the
        # template default would show the badge on startup for any
        # setting you'd legitimately customized in a previous session
        # and saved, which isn't useful information; comparing against
        # this snapshot means it's trivially equal (badge hidden) right
        # when the page loads, and only shows once you actually change
        # something. deepcopy matters here specifically for list-type
        # settings — their Field widgets mutate setting["value"] in
        # place, so a shallow reference here would silently track the
        # live list itself instead of a real snapshot.
        self._initial_value = copy.deepcopy(setting.get("value")) if setting else None
        set_style(self, "settings", "setting-block")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # Read by the sort toolbar (see _sorted_content()) — A-Z/Z-A
        # sorts by this. Not a dependency-having thing, so it just
        # doesn't move under the "dependants" sort mode (sort_dependants
        # is read via getattr(..., 0) there, defaulting everything
        # without it to 0 and leaving relative order stable).
        self.sort_label = (setting.get("name") if setting else None) or format_name(key) or ""
        # Read by the sort toolbar's "type" axis — normalized the same
        # way the widget-dispatch logic below groups things (list[float]
        # and list[int] both count as "list", etc.), so sorting by type
        # groups things exactly the same way they're already grouped by
        # which control renders for them. Doesn't mean anything for
        # plugin-supplied raw content (no real setting dict), so it
        # just defaults to "no type", which sorts as a no-op.
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

        # Live "Modified" badge — shown directly on the block rather
        # than as a sort option, since a sort computed once when the
        # page is built can't reflect an edit you make without leaving
        # and coming back. _refresh_modified_badge() gets called by
        # every control below on every change, live, so this actually
        # tracks the current in-memory value against its declared
        # default in real time.
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

        elif t == "string":
            outer.addWidget(Field(setting, prefix=prefix, suffix=suffix, on_change=self._refresh_modified_badge))

        elif t == "path":
            field = Field(setting, prefix=prefix, suffix=suffix, on_change=self._refresh_modified_badge)
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
                                   on_change=self._refresh_modified_badge))

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
                                       prefix=str(pfx), suffix=str(sfx),
                                       on_change=self._refresh_modified_badge))

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
    """Build the widgets for the Info category."""
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
        # Lets a finger-drag scroll this like a phone, with inertia —
        # a plain QScrollArea only reacts to the scrollbar handle or a
        # mouse wheel, neither usable here since the scrollbar's
        # hidden (ScrollBarAlwaysOff above). Works for a real mouse
        # drag too, so there's no downside to leaving this always on.
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

        # ── Drawer ────────────────────────────────────────────────────────────
        self.drawer = Drawer(client, position="bottom")
        self.drawer.setParent(self)
        self.drawer.place_on_page()
        self.drawer.add_controls([
            IconButton(Icons.HOME,       lambda: client.goto(client.DEFAULT_PAGE or "#root")),
            IconButton(Icons.FULLSCREEN, client.toggle_fullscreen),
            IconButton(Icons.CLOSE,      client.stop),
        ])

        # Raise order: grid < body < top_bar < drawer
        self._grid.lower()
        self.drawer.raise_()

        # ── Features ─────────────────────────────────────────────────────────
        self.add_features({
            "add_drawer_controls":    self.drawer.insert_controls,
            "remove_drawer_controls": self.drawer.remove_controls,
            "new_category":           self.new_category,
            "new_subcategory":        self.new_subcategory,
            "insert_block":           self.insert_block,
            "new_settings_list":      self.builder,
        })

        self._generate_settings(client.SETTINGS, client.settings_dict())
        self._page_additions()
        self._build_nav()

    # ── Builder ───────────────────────────────────────────────────────────────

    def new_category(self, name: str, controls: list, label: str = None) -> None:
        """Register (or replace) a top-level category. `controls` is this
        category's own content, shown below its title-card header."""
        self.categories[name] = {
            "label":      label or format_name(name),
            "content":    controls,
            "subs":       {},
            "plugin":     None,
            "plugin_key": None,
            "icon":       None,
            "readme":     None,
        }

    def new_subcategory(self, parent: str, name: str, controls: list,
                         label: str = None, plugin=None, plugin_key: str = None,
                         icon: str = None, readme: str = None) -> None:
        """
        Register a sub-category nested under an existing top-level
        category — rendered indented beneath it in the nav (connected by
        a small rail, see _build_nav()), with its own title-card header
        and content, same as a top-level category gets.

        Currently used purely to give every plugin its own page under
        "plugins" (see _page_additions()) — pass plugin/plugin_key for
        that case and the header gets the Copy Key / Reload / Unload
        buttons automatically (see _build_category_header()). icon/
        readme come from that same plugin's optional plugin.toml
        fields — see _build_nav()/_build_category_header() for where
        they actually render. Neither plugin/plugin_key/icon/readme is
        plugin-specific otherwise; this mechanism works for any
        category. Only one level of nesting is supported.
        """
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
            # The overview page is a stacked summary of every plugin at
            # once — each plugin's own dedicated subpage already shows
            # its readme, so repeating potentially-long markdown content
            # N times over here would make this page unreasonably tall.
            # readme=None keeps everything else (icon, title, action
            # buttons, dependency line) identical to that subpage's
            # header.
            overview.append(self._build_category_header(
                plugin.config.plugin.name,
                plugin=plugin, plugin_key=key,
                has_content=True, icon=icon_value, readme=None,
            ))
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

    # ── Category header (title card) ────────────────────────────────────────

    def _build_category_header(self, label: str, plugin=None, plugin_key: str = None,
                                has_content: bool = True, icon: str = None,
                                readme: str = None) -> QFrame:
        """The title card shown at the top of every category's and every
        sub-category's content. Plugin sub-categories additionally get
        the Copy Key / Reload / Unload management buttons in the top
        row (see _build_plugin_actions()), an icon next to the title if
        plugin.toml declared one, and its README rendered as markdown
        at the very bottom if it declared that too (see
        _build_readme_block())."""
        card = QFrame()
        set_style(card, "settings", "category-header" if has_content else "category-header-standalone")

        # Read by the sort toolbar (see _sorted_content()) when this
        # header is one entry among several sortable ones — currently
        # only the Plugins overview page (_page_additions()) stacks
        # multiple of these together.
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

    def _build_readme_block(self, readme_path: str) -> QLabel | None:
        """Renders a plugin's optional plugin.toml `readme` file (a
        path, resolved relative to the plugin's own directory by
        PluginManager at load time) as markdown. Returns None if
        there's no path, the file's missing, or it's empty — same
        "just don't show it" approach as _build_dependency_line()."""
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

    # ── Sort toolbar ─────────────────────────────────────────────────────────
    # Shown at the top of every category/sub-category's content (built
    # fresh each time _show_category() renders), sorting whatever's in
    # target["content"] — SettingBlocks normally, or the stacked plugin
    # headers on the Plugins overview page (see _page_additions()).
    #
    # One button per axis (alphabetical, dependants), not one per
    # direction — clicking a button that isn't active yet turns sorting
    # on for that axis at its default direction; clicking the axis
    # that's ALREADY active flips its direction in place instead of
    # turning anything off. The icon shown on each button always
    # reflects its OWN current direction (which way clicking it again
    # would go), regardless of whether that axis is the active one.

    SORT_AXES = ("alpha", "dependants", "type")

    def _compose_dual_icon(self, name1: str, name2: str, size: int = 20,
                            gap: int = 4, color: str = "white") -> QIcon:
        """
        Renders two icon-system glyphs side by side into one QIcon —
        used for the sort buttons below so each one visually shows
        *both ends* of what it sorts (e.g. an A and a Z) instead of
        relying on a text label to explain itself.
        """
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
        # Every other axis uses the same visual language: a glyph for
        # the concept being sorted, plus a direction arrow.
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
        # dependants only means anything for plugin headers (which have
        # a dependency relationship); type only means anything for real
        # SettingBlocks (which have an actual setting type). Plugin
        # headers aren't settings and don't have one, so the two axes
        # are mutually exclusive based on whether we're in the Plugins
        # category at all — never both shown, never both hidden.
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

            # A label, not a tooltip — there's no hover state on a
            # touchscreen, so a tooltip would never actually be seen.
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
        """
        Each axis cycles through 3 states with repeated clicks:
        ascending -> descending -> off (back to the page's normal
        declared order) -> ascending -> ... Switching to a DIFFERENT
        axis always starts that axis fresh at ascending, discarding
        whatever direction it was left on last time, so the cycle is
        always predictable from a cold start regardless of history.
        """
        if self._active_sort_mode != axis:
            self._active_sort_mode = axis
            self._sort_direction[axis] = "asc"
        elif self._sort_direction[axis] == "asc":
            self._sort_direction[axis] = "desc"
        else:
            # last step of the cycle -> off, reset so the next time
            # this axis is picked it starts at the beginning again
            self._active_sort_mode = None
            self._sort_direction[axis] = "asc"
        self._show_category(self._active_path)

    def _sorted_content(self, content: list) -> list:
        """
        Default (no active sort) returns content completely unchanged —
        including structural widgets like section labels/dividers that
        nested settings groups use, since those need to stay anchored
        to their original position relative to the blocks they
        introduce. An active sort instead keeps only the genuinely
        sortable items (anything with a sort_label — see SettingBlock
        and _build_category_header) and drops the structural ones,
        since "sort everything alphabetically" and "preserve these
        section groupings" can't both be true at once. Picking a sort
        axis is an explicit choice to flatten the page in exchange for
        that ordering.
        """
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
        """
        "Depends on: ..." / "Required by: ..." line for a plugin's
        header — this plugin's own declared dependencies (whether or
        not they're currently loaded) and the currently-loaded plugins
        that depend on IT (the same live set unload_plugin() checks
        before refusing to unload). Returns None when there's nothing
        to show, so the header doesn't grow for a plugin with no
        dependency relationships either way.
        """
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
        """Copy Key / Reload / Unload buttons for a plugin's settings
        header — color-coded by how destructive the action is (see
        settings.css) and 44px tall to stay comfortably touch-sized,
        same as every other primary control on this page."""
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
            # Reload stays available on purpose — only a plain unload
            # is blocked, since unload_plugin() itself refuses it while
            # any of these is still loaded (see PluginManager.unload_plugin).
            # Disabling the button here just means there's nothing to
            # click instead of a click that's silently refused.
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

        return [copy_btn, reload_btn, unload_btn]

    def _copy_plugin_key(self, plugin_key: str) -> None:
        self.client.app.clipboard().setText(plugin_key)
        self.client.simple_notify(Icons.COPY, "Settings", f"Copied '{plugin_key}' to clipboard.")

    def _reload_plugin(self, plugin_key: str) -> None:
        # reload_plugin() does its own client.goto() (with override=True)
        # once finished, including the brief "Reloading…" detour and a
        # success notification — nothing else needed here. Deferred via
        # call_on_ui so it runs after this click handler returns rather
        # than nested inside it, same pattern src/backend.py uses for
        # the same call.
        self.client.call_on_ui(lambda: self.client.PLUGIN.reload_plugin(plugin_key))

    def _unload_plugin(self, plugin_key: str) -> None:
        def _do():
            if not self.client.PLUGIN.unload_plugin(plugin_key):
                # Refused — most likely a dependant loaded in the brief
                # window between this button being built and clicked.
                # unload_plugin() already logged why; just surface it.
                dependants = self.client.PLUGIN.get_dependants(plugin_key)
                detail = (f" — required by: {', '.join(dependants)}" if dependants else "")
                self.client.simple_notify(Icons.WARNING, "Settings",
                                           f"Couldn't unload '{plugin_key}'{detail}.")
                return
            self.client.simple_notify(Icons.DELETE, "Settings", f"'{plugin_key}' was unloaded.")
            # Force the settings page to rebuild from scratch — its nav
            # and the now-gone plugin's sub-category would otherwise
            # keep referencing an instance that no longer exists.
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
        # state: "active" (this exact button is selected), "parent" (a
        # child of this category is selected), or "inactive"
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
        )
        self._content_layout.insertWidget(self._content_layout.count() - 1, header)

        toolbar = self._build_sort_toolbar(in_plugins_category=(cat_key == "plugins"))
        self._content_layout.insertWidget(self._content_layout.count() - 1, toolbar)

        for block in self._sorted_content(target["content"]):
            if isinstance(block, QWidget):
                self._content_layout.insertWidget(self._content_layout.count() - 1, block)

        self._content_scroll.verticalScrollBar().setValue(0)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @mixin_target("settings.timeout")
    def interaction_timeout(self, event=None) -> None:
        self.client.simple_notify(
            Icons.TIMER, "Settings: Timeout",
            "No interaction — returning to home screen."
        )
        self.return_and_save(notify=False)

    @mixin_target("settings.save")
    def return_and_save(self, event=None, notify: bool = True) -> None:
        # This used to be missing entirely — the page navigated away and
        # claimed "Settings saved!" without ever writing anything to
        # disk. Every change up to this point only ever lived in the
        # in-memory client.SETTINGS object; it would have been silently
        # lost on the next restart unless the app happened to reach a
        # clean Client.stop() first.
        self.client.dump(self.client.settings_dict(), self.client.DATA)
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
        # Essential, not just tidy — without this, this page instance
        # stays subscribed even after goto() destroys it (e.g. via Save,
        # or navigating away some other way), and the next idle timeout
        # anywhere in the app would call interaction_timeout() on a
        # deleted widget. Same class of bug as Drawer's own auto-close
        # timer outliving the Drawer itself — see Drawer.__init__ in
        # src/ui/controls/drawer.py for the other place this was fixed.
        self.client.unsubscribe_from_event("on_interaction_timeout", self.interaction_timeout)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        BAR_H = 70
        self._grid.setGeometry(0, 0, w, h)
        self._top_bar.setGeometry(0, 0, w, BAR_H)
        self._body.setGeometry(0, BAR_H, w, h - BAR_H)
        self.drawer.apply_parent_width()