from __future__ import annotations
import socket
import platform
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QScrollArea, QLineEdit, QComboBox, QFrame, QSizePolicy, QFileDialog,
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtProperty, QPoint
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen

from src.mixins import mixin_target
from src.ui.page import PageFramework
from src.ui.widget import WidgetFramework
from src.ui.controls.drawer import Drawer
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons
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


class DragScrollArea(QScrollArea):
    """
    QScrollArea with manual drag-to-scroll, for touchscreens.

    QScrollArea only responds to mouse wheel / scrollbar dragging by
    default — no touch-drag support at all, which is the actual cause
    of "can't touch-scroll the settings page" on a touchscreen device.

    QScroller (Qt's built-in fix for this) was tried and rejected here:
    QScroller.ScrollerGestureType.LeftMouseButtonGesture installs an
    APPLICATION-WIDE event filter that intercepts every left mouse
    press/move/release first, to decide whether it's a scroll-drag,
    before letting it through to whatever's actually under the cursor
    — this broke clicking on the on-screen keyboard popup, which lives
    in a completely different parent (client.OVERLAYS) but still had
    its clicks intercepted by that global filter.
    QScroller.ScrollerGestureType.TouchGesture avoids the global mouse
    filter, but only engages for genuine QTouchEvents — if the target
    hardware reports touch input as synthesized mouse events (common on
    embedded panels without a real touch driver, which this hardware
    appears to be), TouchGesture has nothing to grab and never scrolls
    at all.

    This class sidesteps both problems: it's just an ordinary
    mousePressEvent/mouseMoveEvent/mouseReleaseEvent override on ITSELF
    (well, its viewport), entirely local to this one widget. No global
    filter, no gesture recognizer — it can never intercept or delay a
    click meant for some other widget anywhere else in the app.
    """

    DRAG_THRESHOLD = 6   # pixels of movement before a press counts as a drag, not a click

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.viewport().installEventFilter(self)
        self._drag_active   = False
        self._drag_start_y  = 0
        self._scroll_start  = 0

    def eventFilter(self, watched, event):
        # Installed on the viewport specifically (not via a global
        # filter) — this ONLY ever sees events already destined for
        # this scroll area's own viewport, never anything from another
        # widget elsewhere in the app.
        if watched is self.viewport():
            etype = event.type()
            if etype == event.Type.MouseButtonPress:
                return self._on_press(event)
            elif etype == event.Type.MouseMove:
                return self._on_move(event)
            elif etype == event.Type.MouseButtonRelease:
                return self._on_release(event)
        return super().eventFilter(watched, event)

    def _on_press(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self._drag_active  = False   # not yet a drag — just a candidate press
        self._drag_start_y = event.globalPosition().toPoint().y()
        self._scroll_start = self.verticalScrollBar().value()
        return False   # let the press still reach whatever's under it (e.g. a button)

    def _on_move(self, event) -> bool:
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return False
        dy = event.globalPosition().toPoint().y() - self._drag_start_y
        if not self._drag_active and abs(dy) >= self.DRAG_THRESHOLD:
            self._drag_active = True
        if self._drag_active:
            self.verticalScrollBar().setValue(self._scroll_start - dy)
            return True   # consume — this is now a scroll drag, not a click-drag
        return False

    def _on_release(self, event) -> bool:
        was_dragging = self._drag_active
        self._drag_active = False
        # If we actually dragged, swallow the release so it doesn't
        # ALSO register as a click on whatever's underneath (e.g. a
        # Field gaining focus and popping the keyboard open just from
        # scrolling past it).
        return was_dragging



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

    def __init__(self, client, setting, index=None, is_numeric=False, prefix="", suffix="", label=""):
        super().__init__()
        self.setFixedHeight(44)
        self._bg     = QColor(FIELD_BG)
        self._border = QColor(FIELD_BORDER)
        self._radius = 6
        self.client  = client
        self._label  = label   # display name shown on the keyboard popup when this field is focused
        # make_keyboard() uses this to decide numpad vs full keyboard —
        # it was never being set at all before, which meant opening the
        # keyboard threw AttributeError every single time (silently
        # swallowed by a bare except below, at the call site in
        # _focus_in further down this file).
        self._setting_type = "numeric" if is_numeric else "string"

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

        le = QLineEdit(str(val))
        le.setFont(make_font(SIZES.S3))
        le.setFixedHeight(44)
        set_style(le, "settings", "field-input", object_tag="QLineEdit")
        _field = self

        def _focus_in(e):
            _field._border = QColor(FIELD_BORDER_FOCUS)
            _field.update()
            QLineEdit.focusInEvent(le, e)
            # Open keyboard popup. _field.client used to always be None
            # here (Field guessed it via hasattr(setting, 'client'),
            # but the setting object never had one) — every exception
            # this triggered downstream was then silently swallowed by
            # a bare "except Exception: pass" below, which is why
            # nothing ever visibly happened. client is now passed into
            # Field properly, and exceptions are no longer hidden.
            try:
                overlay = _field.client.OVERLAYS

                # Close whatever keyboard (if any) is already open
                # before opening a new one. Without this, focusing a
                # different field while a keyboard was already showing
                # opened a SECOND keyboard on top of the first instead
                # of replacing it — each Field's focus handler is its
                # own private closure with no shared state, so this has
                # to be tracked somewhere global. client.ACTIVE_KEYBOARD
                # is that single shared slot.
                previous_kb = _field.client.ACTIVE_KEYBOARD
                if previous_kb is not None:
                    previous_kb.close_keyboard()

                kb = make_keyboard(_field.client, le, _field._setting_type, overlay, label=_field._label)
                _field.client.ACTIVE_KEYBOARD = kb
                kb.show_keyboard()
            except Exception as exc:
                _field.client.log("error", f"[Settings] Failed to open keyboard popup: {exc}", include_traceback=True)

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


class EnumComponent(QComboBox):
    def __init__(self, setting):
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
        self.currentIndexChanged.connect(
            lambda: self._setting.__setitem__("value", self.currentData())
        )

class SettingBlock(QFrame):
    def __init__(self, client, setting=None, key="", content: QWidget = None):
        super().__init__()
        self.client = client
        set_style(self, "settings", "setting-block")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

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

        display_name = setting.get("name") or format_name(key)
        name_lbl = QLabel(display_name)
        name_lbl.setFont(make_font(SIZES.S2, bold=True))
        set_style(name_lbl, "common", "text-strong")
        header.addWidget(name_lbl)
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
        # Normalise: "list[float]" → "list", "list[int]" → "list" etc.
        t      = "list" if raw_t.startswith("list") else raw_t
        # Normalise: "float" and "double" → "float", keep "int"
        if t in ("double",): t = "float"
        prefix = setting.get("prefix", "") or ""
        suffix = setting.get("suffix", "") or ""

        if t == "bool":
            toggle = ToggleSwitch(bool(setting["value"]))
            toggle.connect(lambda val: setting.__setitem__("value", val))
            header.addWidget(toggle)

        elif t == "string":
            outer.addWidget(Field(self.client, setting, prefix=prefix, suffix=suffix, label=display_name))

        elif t == "path":
            field = Field(self.client, setting, prefix=prefix, suffix=suffix, label=display_name)
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
            outer.addWidget(Field(self.client, setting, is_numeric=True, prefix=prefix, suffix=suffix, label=display_name))

        elif t == "enum":
            outer.addWidget(EnumComponent(setting))

        elif t == "list":
            # is_numeric from raw type or from value content
            list_numeric = "int" in raw_t or "float" in raw_t or "numeric" in raw_t
            for i, val in enumerate(setting["value"]):
                pfx = prefix[i] if isinstance(prefix, list) and i < len(prefix) else (prefix or "")
                sfx = suffix[i] if isinstance(suffix, list) and i < len(suffix) else (suffix or "")
                is_num = list_numeric or not isinstance(val, str)
                outer.addWidget(Field(self.client, setting, index=i, is_numeric=is_num,
                                       prefix=str(pfx), suffix=str(sfx), label=display_name))



class PluginGroup(QFrame):
    def __init__(self, plugin, key: str, blocks: list):
        super().__init__()
        set_style(self, "settings", "plugin-group")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Group header
        hdr = QWidget()
        set_style(hdr, "settings", "plugin-group-header")
        hl = QVBoxLayout(hdr)
        hl.setContentsMargins(16, 12, 16, 12)
        hl.setSpacing(2)

        nl = QLabel(plugin.config.plugin.name)
        nl.setFont(make_font(SIZES.M1, bold=True))
        set_style(nl, "common", "text-strong")

        ml = QLabel(key)
        ml.setFont(make_font(SIZES.S1))
        set_style(ml, "common", "text-muted")

        hl.addWidget(nl)
        hl.addWidget(ml)
        layout.addWidget(hdr)

        if blocks:
            body = QWidget()
            set_style(body, "common", "transparent")
            bl = QVBoxLayout(body)
            bl.setContentsMargins(12, 12, 12, 12)
            bl.setSpacing(6)
            for block in blocks:
                if isinstance(block, QWidget):
                    bl.addWidget(block)
            layout.addWidget(body)


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

        self.categories: dict[str, list] = {}

        # Dot grid background
        self._grid = GridBackground(self)
        self._grid.setGeometry(0, 0, w, h)

        NAV_W   = 280
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
        self._content_scroll = DragScrollArea()
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setStyleSheet(get_style_sheet("settings_scroll"))
        self._content_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

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

        # ── Timeout ───────────────────────────────────────────────────────────
        self._timeout_id = client.TIMEOUTS.add(
            60 * 5, self.interaction_timeout,
            "settings_interaction:timeout", autostart=True
        )

        # ── Features ─────────────────────────────────────────────────────────
        self.add_features({
            "add_drawer_controls":    self.drawer.insert_controls,
            "remove_drawer_controls": self.drawer.remove_controls,
            "new_category":           self.new_category,
            "insert_block":           self.insert_block,
            "new_settings_list":      self.builder,
        })

        self._generate_settings(client.SETTINGS, client.SETTINGS.as_dict())
        self._page_additions()
        self._build_nav()
        self._active_nav_btn: QPushButton | None = None

    # ── Builder ───────────────────────────────────────────────────────────────

    def new_category(self, name: str, controls: list) -> None:
        self.categories[name] = controls

    def insert_block(self, category: str, index: int, content: QWidget) -> None:
        if self.categories.get(category):
            self.categories[category].insert(
                index, SettingBlock(self.client, content=content)
            )

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
        groups = []
        plugins = self.client.PLUGIN.get_plugins()
        for plugin, key in plugins:
            if not hasattr(plugin, "settings"):
                continue
            blocks = self.builder(plugin.settings, plugin.settings.to_dict(), "", "")
            groups.append(PluginGroup(plugin, key, blocks))
        self.new_category("plugins", groups)

    @mixin_target("settings.setup.tab.generation")
    def _build_nav(self) -> None:
        first = True
        for key in self.categories:
            btn = QPushButton(format_name(key))
            btn.setFont(make_font(SIZES.S2))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(44)
            btn.setCheckable(True)
            self._apply_nav_style(btn, False)
            btn.clicked.connect(lambda _, k=key, b=btn: self._switch_tab(k, b))
            self._nav_list.addWidget(btn)
            if first:
                btn.setChecked(True)
                self._apply_nav_style(btn, True)
                self._active_nav_btn = btn
                self._show_category(key)
                first = False

    def _apply_nav_style(self, btn: QPushButton, active: bool) -> None:
        bg = "rgba(255,255,255,18)" if active else "transparent"
        set_style(btn, "settings", "settings-nav-button", override={"*": {"background": bg}})

    def _switch_tab(self, key: str, btn: QPushButton) -> None:
        if self._active_nav_btn:
            self._active_nav_btn.setChecked(False)
            self._apply_nav_style(self._active_nav_btn, False)
        btn.setChecked(True)
        self._apply_nav_style(btn, True)
        self._active_nav_btn = btn
        self._show_category(key)
        self.client.TIMEOUTS.start(self._timeout_id)

    def _show_category(self, key: str) -> None:
        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        for block in self.categories.get(key, []):
            if isinstance(block, QWidget):
                self._content_layout.insertWidget(
                    self._content_layout.count() - 1, block
                )
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
        self.client.TIMEOUTS.cancel(self._timeout_id)
        self.client.iterate_event_callables("on_settings_saved", self.client.SETTINGS)
        if notify:
            self.client.simple_notify(Icons.SAVE, "Settings", "Settings saved!")
        target = self.client.DEFAULT_PAGE or "#root"
        if not self.client.has_page(target):
            target = "#root"
        self.client.goto(target)

    def start(self) -> None:
        super().start()
        self.client.TIMEOUTS.start(self._timeout_id)

    def stop(self) -> None:
        super().stop()
        self.client.TIMEOUTS.cancel(self._timeout_id)
        self.drawer.stop()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        BAR_H = 70
        self._grid.setGeometry(0, 0, w, h)
        self._top_bar.setGeometry(0, 0, w, BAR_H)
        self._body.setGeometry(0, BAR_H, w, h - BAR_H)
        self.drawer.apply_parent_width()