from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QScrollArea, QLineEdit, QComboBox, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint, pyqtProperty
from PyQt6.QtGui import QPainter, QColor, QBrush

from src.mixins import mixin_target
from src.ui.page import PageFramework
from src.ui.widget import WidgetFramework
from src.ui.controls.drawer import Drawer
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons
from src.styling import COLORS, SIZES, make_font, add_text_shadow

if TYPE_CHECKING:
    from src.main import Client


# ── Helpers ───────────────────────────────────────────────────────────────────

def format_name(name: str) -> str:
    for sep in ("_", "-"):
        if sep in name:
            return " ".join(w.capitalize() for w in name.split(sep))
    return " ".join(f"{w[0].upper()}{w[1:]}" for w in name.split(" "))


# ── Toggle switch ─────────────────────────────────────────────────────────────

class _ToggleSwitch(QWidget):
    """Custom painted pill-shaped toggle switch."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(56, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = checked
        self._thumb_x = float(30) if checked else float(4)

        self._anim = QPropertyAnimation(self, b"thumbX")
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        self._callbacks: list = []

    def _get_thumb(self) -> float:
        return self._thumb_x

    def _set_thumb(self, val: float) -> None:
        self._thumb_x = val
        self.update()

    thumbX = pyqtProperty(float, _get_thumb, _set_thumb)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, val: bool) -> None:
        self._checked = val
        self._anim.stop()
        self._anim.setStartValue(self._thumb_x)
        self._anim.setEndValue(30.0 if val else 4.0)
        self._anim.start()
        self.update()

    def mousePressEvent(self, event) -> None:
        self.setChecked(not self._checked)
        for cb in self._callbacks:
            cb(self._checked)

    def connect(self, cb) -> None:
        self._callbacks.append(cb)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Track
        track = QColor(COLORS.PRIMARY.LIGHT if self._checked else COLORS.DARK.BGDARK)
        border = QColor(COLORS.PRIMARY.LIGHT if self._checked else COLORS.DARK.BORDER.NORMAL)
        p.setBrush(QBrush(track))
        p.setPen(border)
        p.drawRoundedRect(0, 0, 56, 28, 14, 14)

        # Thumb
        p.setBrush(QBrush(QColor("white")))
        p.setPen(Qt.GlobalColor.transparent)
        p.drawEllipse(int(self._thumb_x), 4, 20, 20)


# ── Inline input row (input + suffix/prefix as one unit) ─────────────────────

class _InputRow(QWidget):
    """QLineEdit with an optional prefix label and suffix label inside a shared border."""

    def __init__(self, line_edit: QLineEdit, prefix: str = "", suffix: str = ""):
        super().__init__()
        self.setStyleSheet(f"""
            QWidget {{
                background: {COLORS.DARK.BGDARK};
                border: 1px solid {COLORS.DARK.BORDER.NORMAL};
                border-radius: 6px;
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Remove border from line edit — outer widget provides it
        line_edit.setStyleSheet(f"""
            QLineEdit {{
                background: transparent;
                color: inherit;
                border: none;
                padding: 8px 8px;
            }}
            QLineEdit:focus {{ border: none; }}
        """)

        def _label(text: str, muted: bool = True) -> QLabel:
            lbl = QLabel(text)
            lbl.setFont(make_font(SIZES.S2))
            color = COLORS.DARK.TEXT.MUTED if muted else COLORS.DARK.TEXT.IMPORTANT
            lbl.setStyleSheet(
                f"color: {color}; background: transparent; padding: 8px 10px;"
            )
            return lbl

        if prefix:
            layout.addWidget(_label(prefix, muted=False))
        layout.addWidget(line_edit, stretch=1)
        if suffix:
            layout.addWidget(_label(suffix))


# ── Setting components ────────────────────────────────────────────────────────

def _make_input_style(color: str) -> str:
    return f"""
        QLineEdit {{
            background: {COLORS.DARK.BGDARK};
            color: {color};
            border: 1px solid {COLORS.DARK.BORDER.NORMAL};
            border-radius: 6px;
            padding: 8px 12px;
            font-size: {SIZES.S3}px;
        }}
        QLineEdit:focus {{ border-color: {COLORS.PRIMARY.LIGHT}; }}
    """


class _StringComponent(QLineEdit):
    def __init__(self, setting, index=None):
        super().__init__()
        self._setting = setting
        self._index   = index
        val = setting["value"] if index is None else setting["value"][index]
        self.setText(str(val))
        self.setFont(make_font(SIZES.S3))
        self.setStyleSheet(_make_input_style("#a8d8a8"))
        self.textChanged.connect(self._changed)

    def _changed(self, text):
        if self._index is None:
            self._setting["value"] = text
        else:
            self._setting["value"][self._index] = text


class _NumericComponent(QLineEdit):
    def __init__(self, setting, index=None):
        super().__init__()
        self._setting = setting
        self._index   = index
        val = setting["value"] if index is None else setting["value"][index]
        self.setText(str(val))
        self.setFont(make_font(SIZES.S3))
        self.setStyleSheet(_make_input_style("#f4a261"))
        self.textChanged.connect(self._changed)

    def _changed(self, text):
        try:
            val = float(text) if "." in text else int(text)
        except ValueError:
            return
        if self._index is None:
            self._setting["value"] = val
        else:
            self._setting["value"][self._index] = val


class _PathComponent(QLineEdit):
    def __init__(self, setting):
        super().__init__()
        self._setting = setting
        self.setText(str(setting["value"]))
        self.setFont(make_font(SIZES.S3))
        self.setStyleSheet(_make_input_style("#90caf9"))
        self.textChanged.connect(lambda t: self._setting.__setitem__("value", t))


class _EnumComponent(QComboBox):
    def __init__(self, setting):
        super().__init__()
        self._setting = setting
        self._filler  = "-" if "-" in setting.options[0] else ("_" if "_" in setting.options[0] else " ")
        self.setFont(make_font(SIZES.S2))
        self.setStyleSheet(f"""
            QComboBox {{
                background: {COLORS.DARK.BGDARK};
                color: {COLORS.DARK.TEXT.IMPORTANT};
                border: 1px solid {COLORS.DARK.BORDER.NORMAL};
                border-radius: 6px;
                padding: 8px 12px;
                min-height: 40px;
            }}
            QComboBox::drop-down {{ border: none; width: 30px; }}
            QComboBox QAbstractItemView {{
                background: {COLORS.DARK.BG};
                color: {COLORS.DARK.TEXT.IMPORTANT};
                border: 1px solid {COLORS.DARK.BORDER.NORMAL};
                selection-background-color: {COLORS.DARK.BGLIGHT};
            }}
        """)
        for option in setting.options:
            self.addItem(format_name(option.strip()), userData=option)
            if option == setting.value:
                self.setCurrentIndex(self.count() - 1)
        self.currentIndexChanged.connect(self._changed)

    def _changed(self):
        self._setting["value"] = self.currentData()


# ── Setting block ─────────────────────────────────────────────────────────────

class SettingBlock(QFrame):
    def __init__(self, client, setting=None, key="", content: QWidget = None):
        super().__init__()
        self.setStyleSheet(f"""
            QFrame {{
                background: {COLORS.DARK.BGLIGHT};
                border-radius: 6px;
                border: none;
            }}
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        if content:
            outer.addWidget(content)
            return

        # Header row
        header = QHBoxLayout()
        header.setSpacing(12)
        header.setContentsMargins(0, 0, 0, 0)

        name_lbl = QLabel(setting.get("name") or format_name(key))
        name_lbl.setFont(make_font(SIZES.S2, bold=True))
        name_lbl.setStyleSheet(f"color: {COLORS.DARK.TEXT.IMPORTANT}; background: transparent;")
        header.addWidget(name_lbl)
        header.addStretch()
        outer.addLayout(header)

        desc = setting.get("description", "")
        if desc:
            desc_lbl = QLabel(desc)
            desc_lbl.setFont(make_font(SIZES.S1))
            desc_lbl.setStyleSheet(f"color: {COLORS.DARK.TEXT.MUTED}; background: transparent;")
            desc_lbl.setWordWrap(True)
            outer.addWidget(desc_lbl)

        t = setting.get("type", "string")
        prefix = str(setting.get("prefix", "")) if setting.get("prefix") else ""
        suffix = str(setting.get("suffix", "")) if setting.get("suffix") else ""

        if t == "bool":
            toggle = _ToggleSwitch(bool(setting["value"]))
            toggle.connect(lambda val: setting.__setitem__("value", val))
            header.addWidget(toggle)

        elif t == "string":
            comp = _StringComponent(setting)
            outer.addWidget(_InputRow(comp, prefix, suffix) if (prefix or suffix) else comp)

        elif t in ("int", "float", "numeric"):
            comp = _NumericComponent(setting)
            outer.addWidget(_InputRow(comp, prefix, suffix) if (prefix or suffix) else comp)

        elif t == "enum":
            outer.addWidget(_EnumComponent(setting))

        elif t == "list":
            for i, val in enumerate(setting["value"]):
                pfx = prefix[i] if isinstance(prefix, list) and i < len(prefix) else prefix
                sfx = suffix[i] if isinstance(suffix, list) and i < len(suffix) else suffix
                comp = (_StringComponent if isinstance(val, str) else _NumericComponent)(setting, index=i)
                outer.addWidget(_InputRow(comp, pfx, sfx) if (pfx or sfx) else comp)

        elif t == "path":
            comp = _PathComponent(setting)
            outer.addWidget(_InputRow(comp, prefix, suffix) if (prefix or suffix) else comp)


# ── Plugin group ──────────────────────────────────────────────────────────────

class _PluginGroup(QFrame):
    """Groups a plugin's name, meta info, and its setting blocks visually."""

    def __init__(self, plugin, key: str, blocks: list):
        super().__init__()
        self.setStyleSheet(f"""
            QFrame {{
                background: {COLORS.DARK.BG};
                border: 1px solid {COLORS.DARK.BORDER.NORMAL};
                border-radius: 8px;
            }}
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setStyleSheet(f"""
            QWidget {{
                background: {COLORS.DARK.BGLIGHT};
                border-radius: 8px 8px 0px 0px;
            }}
        """)
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(16, 12, 16, 12)
        h_layout.setSpacing(2)

        name_lbl = QLabel(plugin.config.plugin.name)
        name_lbl.setFont(make_font(SIZES.M1, bold=True))
        name_lbl.setStyleSheet(f"color: {COLORS.DARK.TEXT.IMPORTANT}; background: transparent;")

        from src.main import Client  # avoid circular at module level
        meta_lbl = QLabel(f"{key}")
        meta_lbl.setFont(make_font(SIZES.S1))
        meta_lbl.setStyleSheet(f"color: {COLORS.DARK.TEXT.MUTED}; background: transparent;")

        h_layout.addWidget(name_lbl)
        h_layout.addWidget(meta_lbl)
        layout.addWidget(header)

        # Blocks area
        if blocks:
            body = QWidget()
            body.setStyleSheet("QWidget { background: transparent; }")
            b_layout = QVBoxLayout(body)
            b_layout.setContentsMargins(12, 12, 12, 12)
            b_layout.setSpacing(6)
            for block in blocks:
                if isinstance(block, QWidget):
                    b_layout.addWidget(block)
            layout.addWidget(body)


# ── Settings page ─────────────────────────────────────────────────────────────

class SettingsPage(PageFramework):

    @mixin_target("settings.__init__")
    def __init__(self, client: "Client", data: dict = None):
        super().__init__(key="#settings", client=client, data=data)

        w = int(client.SETTINGS.application.window.size.value[0])
        h = int(client.SETTINGS.application.window.size.value[1])
        self.setFixedSize(w, h)
        self.setStyleSheet(f"background-color: {COLORS.DARK.BGDARK};")

        self.categories: dict[str, list] = {}

        # ── Top bar ───────────────────────────────────────────────────────────
        # Sits above everything — gives clock widget a clear header to land on
        top_bar = QWidget(self)
        top_bar.setGeometry(0, 0, w, 70)
        top_bar.setStyleSheet(f"background: {COLORS.DARK.BGDARK};")
        top_bar.raise_()

        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(32, 0, 32, 0)
        top_layout.setSpacing(16)

        back_btn = QPushButton("← Save & Return")
        back_btn.setFont(make_font(SIZES.M1, bold=True))
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.setFixedHeight(48)
        back_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS.PRIMARY.DARK};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 0 24px;
            }}
            QPushButton:hover  {{ background: {COLORS.PRIMARY.LIGHT}; }}
            QPushButton:pressed{{ background: {COLORS.PRIMARY.DARK}; }}
        """)
        back_btn.clicked.connect(self.return_and_save)

        settings_title = QLabel("Settings")
        settings_title.setFont(make_font(SIZES.M3, bold=True))
        settings_title.setStyleSheet(
            f"color: {COLORS.DARK.TEXT.IMPORTANT}; background: transparent;"
        )

        top_layout.addWidget(back_btn)
        top_layout.addStretch()
        top_layout.addWidget(settings_title)

        # ── Main body (below top bar) ─────────────────────────────────────────
        body = QWidget(self)
        body.setGeometry(0, 70, w, h - 70)
        body.setStyleSheet("background: transparent;")

        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(1)

        # Left nav
        nav_panel = QWidget()
        nav_panel.setFixedWidth(280)
        nav_panel.setStyleSheet(f"background: {COLORS.DARK.BG};")
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(16, 16, 16, 16)
        nav_layout.setSpacing(4)

        self._nav_list = QVBoxLayout()
        self._nav_list.setSpacing(4)
        self._nav_list.setContentsMargins(0, 0, 0, 0)
        nav_layout.addLayout(self._nav_list)
        nav_layout.addStretch()

        body_layout.addWidget(nav_panel)

        # Right content
        self._content_scroll = QScrollArea()
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: {COLORS.DARK.BGDARK}; }}
            QScrollBar:vertical {{
                background: {COLORS.DARK.BGDARK}; width: 6px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {COLORS.DARK.BORDER.NORMAL}; border-radius: 3px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        self._content_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._content_widget = QWidget()
        self._content_widget.setStyleSheet(f"background: {COLORS.DARK.BGDARK};")
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(28, 24, 28, 100)
        self._content_layout.setSpacing(8)
        self._content_layout.addStretch()
        self._content_scroll.setWidget(self._content_widget)

        body_layout.addWidget(self._content_scroll, stretch=1)

        # ── Widget manager + Drawer ───────────────────────────────────────────
        self.widget_manager = WidgetFramework(
            client, "#settings",
            padding=client.SETTINGS.home.widget_margin.value
        )
        self.widget_manager.setParent(self)
        self.widget_manager.setGeometry(0, 0, w, h)
        self.widget_manager.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.widget_manager.show()
        self.widget_manager.raise_()

        self.drawer = Drawer(client, position="bottom")
        self.drawer.setParent(self)
        self.drawer.place_on_page()
        self.drawer.add_controls([
            IconButton(Icons.HOME,       lambda: client.goto(client.DEFAULT_PAGE or "#root")),
            IconButton(Icons.FULLSCREEN, client.toggle_fullscreen),
            IconButton(Icons.CLOSE,      client.stop),
        ])
        self.drawer.raise_()

        # ── Timeout ───────────────────────────────────────────────────────────
        self._timeout_id = client.TIMEOUTS.add(
            60 * 5, self.interaction_timeout,
            "settings_interaction:timeout", autostart=True
        )

        # ── Features ─────────────────────────────────────────────────────────
        self.add_features({
            "add_widgets":            self.widget_manager.add,
            "remove_widget":          self.widget_manager.remove,
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
            return group
        settings = data[filter_key] if filter_key else data
        for key, val in settings.items():
            if not isinstance(val, dict):
                continue
            extended_path = f"{path}.{key}" if path else key
            if "type" in val and "value" in val:
                try:
                    setting_obj = pointer
                    for part in extended_path.split("."):
                        setting_obj = setting_obj[part]
                    group.append(SettingBlock(self.client, setting_obj, key))
                except Exception:
                    pass
            else:
                children = self.builder(pointer, settings, key, extended_path)
                if children:
                    if len(path.split(".")) > 1:
                        gap = QWidget()
                        gap.setFixedHeight(6)
                        gap.setStyleSheet("background: transparent;")
                        group.append(gap)
                    lbl = QLabel(format_name(key).upper())
                    lbl.setFont(make_font(SIZES.S1))
                    lbl.setStyleSheet(
                        f"color: {COLORS.DARK.TEXT.MUTED}; background: transparent;"
                        f"letter-spacing: 2px; padding-top: 4px;"
                    )
                    divider = QFrame()
                    divider.setFrameShape(QFrame.Shape.HLine)
                    divider.setFixedHeight(1)
                    divider.setStyleSheet(f"background: {COLORS.DARK.BORDER.HIGHLIGHT};")
                    group.append(lbl)
                    group.append(divider)
                    group.extend(children)
        return group

    @mixin_target("settings.setup.setting.generation")
    def _generate_settings(self, pointer, grouped_dict: dict) -> None:
        for key in grouped_dict:
            self.new_category(key.lower(), self.builder(pointer, grouped_dict, key, key))

    def _page_additions(self) -> None:
        plugin_groups = []
        for plugin, key in self.client.plugin_manager.get_plugins():
            if not hasattr(plugin, "settings"):
                continue
            blocks = self.builder(plugin.settings, plugin.settings.to_dict(), "", "")
            plugin_groups.append(_PluginGroup(plugin, key, blocks))
        self.new_category("plugins", plugin_groups)

    @mixin_target("settings.setup.tab.generation")
    def _build_nav(self) -> None:
        first = True
        for key in self.categories:
            btn = QPushButton(format_name(key))
            btn.setFont(make_font(SIZES.S2))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(44)
            btn.setCheckable(True)
            btn.setStyleSheet(self._nav_style(False))
            btn.clicked.connect(lambda _, k=key, b=btn: self._switch_tab(k, b))
            self._nav_list.addWidget(btn)
            if first:
                btn.setChecked(True)
                btn.setStyleSheet(self._nav_style(True))
                self._active_nav_btn = btn
                self._show_category(key)
                first = False

    def _nav_style(self, active: bool) -> str:
        bg = COLORS.DARK.BGLIGHT if active else "transparent"
        return f"""
            QPushButton {{
                background: {bg};
                color: {COLORS.DARK.TEXT.IMPORTANT};
                border: none;
                border-radius: 6px;
                padding: 0 12px;
                text-align: left;
            }}
            QPushButton:hover {{ background: {COLORS.DARK.BGLIGHT}; }}
        """

    def _switch_tab(self, key: str, btn: QPushButton) -> None:
        if self._active_nav_btn:
            self._active_nav_btn.setChecked(False)
            self._active_nav_btn.setStyleSheet(self._nav_style(False))
        btn.setChecked(True)
        btn.setStyleSheet(self._nav_style(True))
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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        # Resize top bar and body
        for child in self.children():
            from PyQt6.QtWidgets import QWidget as _QW
            if isinstance(child, _QW) and child.geometry().y() == 0 and child.geometry().height() == 70:
                child.setGeometry(0, 0, w, 70)
            elif isinstance(child, _QW) and child.geometry().y() == 70:
                child.setGeometry(0, 70, w, h - 70)
        self.widget_manager.setGeometry(0, 0, w, h)
        self.widget_manager.update_geometry()
        self.drawer.apply_parent_width()