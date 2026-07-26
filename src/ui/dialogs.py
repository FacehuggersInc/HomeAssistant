"""
Modal dialogs for plugin pip dependencies.

Two of them:

  DependencyDialog  -- shown once after startup when plugins were held back
                       for missing packages. Lists exactly what would be
                       installed and where, before anything runs.

  ConfirmDialog     -- generic yes/no, used by the Uninstall button.

Both are plain QWidgets handed to client.DIALOG.open(), which reparents them
onto the SYSTEM overlay layer and puts a click blocker underneath.

pip runs on a worker thread -- it is slow enough to freeze the UI for tens of
seconds otherwise -- and every widget touch from that thread goes back
through client.call_on_ui().
"""

from __future__ import annotations

from threading import Thread
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import Qt

from src.styling import make_font, SIZES, set_style
from src.plugin import dependencies as deps

if TYPE_CHECKING:
    from src.main import Client


DIALOG_WIDTH = 640
DIALOG_MAX_HEIGHT = 620


def _title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(make_font(SIZES.M2, bold=True))
    set_style(lbl, "common", "text-strong")
    lbl.setWordWrap(True)
    return lbl


def _body(text: str, muted: bool = False) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(make_font(SIZES.S2))
    set_style(lbl, "common", "text-muted" if muted else "text-strong")
    lbl.setWordWrap(True)
    return lbl


def _button(text: str, style: str, on_click: Callable) -> QPushButton:
    btn = QPushButton(text)
    btn.setFont(make_font(SIZES.S2, bold=True))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFixedHeight(44)
    btn.setMinimumWidth(120)
    set_style(btn, "settings", style)
    btn.clicked.connect(on_click)
    return btn


class _DialogShell(QWidget):
    """Card, fixed width, vertical content, button row pinned at the bottom."""

    def __init__(self, client: "Client"):
        super().__init__()
        self.client = client
        self.setFixedWidth(DIALOG_WIDTH)
        self.setMaximumHeight(DIALOG_MAX_HEIGHT)
        set_style(self, "settings", "dialog-card")

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(24, 22, 24, 20)
        self._outer.setSpacing(14)

        self.content = QVBoxLayout()
        self.content.setSpacing(10)
        self._outer.addLayout(self.content)

        self._outer.addStretch()

        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(10)
        self.buttons.addStretch()
        self._outer.addLayout(self.buttons)

    def clear_content(self) -> None:
        while self.content.count():
            item = self.content.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def clear_buttons(self) -> None:
        while self.buttons.count():
            item = self.buttons.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.buttons.addStretch()

    def center_on(self, host: QWidget) -> None:
        self.adjustSize()
        x = max(0, (host.width() - self.width()) // 2)
        y = max(0, (host.height() - self.height()) // 2)
        self.move(x, y)


class DependencyDialog(_DialogShell):
    """
    'These plugins need packages installed' -> Install All / Not Now.

    Declining does not discard anything: the plugins stay in
    PluginManager.pending, marked declined, and Settings keeps showing them
    greyed out with their own Install button.
    """

    def __init__(self, client: "Client", pending: list):
        super().__init__(client)
        self.pending = pending
        self._installing = False
        self._build_prompt()

    ## -- prompt state

    def _build_prompt(self) -> None:
        self.clear_content()
        self.clear_buttons()

        count = len(self.pending)
        self.content.addWidget(_title(
            f"{count} plugin{'s' if count != 1 else ''} need"
            f"{'' if count != 1 else 's'} additional packages"
        ))
        self.content.addWidget(_body(
            "These plugins were not loaded because packages they declare are "
            "not installed. Review what would be installed before continuing.",
            muted=True,
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        set_style(scroll, "common", "transparent")

        inner = QWidget()
        set_style(inner, "common", "transparent")
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        for item in self.pending:
            v.addWidget(self._plugin_row(item))
        v.addStretch()

        scroll.setWidget(inner)
        scroll.setMinimumHeight(min(260, 76 * max(1, len(self.pending))))
        self.content.addWidget(scroll)

        self.content.addWidget(_body(
            f"Installing into: {deps.venv_path()}",
            muted=True,
        ))
        self.content.addWidget(_body(
            "Only install packages from plugins you trust. Package names come "
            "from the plugin's own plugin.toml.",
            muted=True,
        ))

        self.buttons.addWidget(_button("Not Now", "plugin-action-unload", self._decline))
        self.buttons.addWidget(_button("Install All", "plugin-action-reload", self._install))

    def _plugin_row(self, item) -> QFrame:
        row = QFrame()
        set_style(row, "settings", "setting-block")
        v = QVBoxLayout(row)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(2)

        name = QLabel(item.name)
        name.setFont(make_font(SIZES.S3, bold=True))
        set_style(name, "common", "text-strong")
        v.addWidget(name)

        pkgs = QLabel(", ".join(item.missing))
        pkgs.setFont(make_font(SIZES.S1))
        pkgs.setWordWrap(True)
        set_style(pkgs, "common", "text-muted")
        v.addWidget(pkgs)
        return row

    ## -- actions

    def _decline(self) -> None:
        for item in self.pending:
            item.declined = True
        self.client.log("info", "[Dependencies] Install declined by user.")
        self.client.DIALOG.close()
        self.client.simple_notify(
            "extension", "Plugins",
            f"{len(self.pending)} plugin(s) not loaded. Install from Settings when ready.",
        )

    def _install(self) -> None:
        if self._installing:
            return
        self._installing = True
        self._build_progress()
        Thread(target=self._worker, name="__plugin_pip_install", daemon=True).start()

    def _build_progress(self) -> None:
        self.clear_content()
        self.clear_buttons()
        self.content.addWidget(_title("Installing packages"))
        self.status = _body("Starting pip...", muted=True)
        self.content.addWidget(self.status)
        self.center_on(self.client.OVERLAYS)

    def _set_status(self, text: str) -> None:
        # pip output is verbose; one trimmed line is enough for a wall display
        line = text.strip()
        if len(line) > 90:
            line = line[:87] + "..."
        self.client.call_on_ui(lambda: self.status.setText(line))

    def _worker(self) -> None:
        succeeded, failed = [], []
        for item in list(self.pending):
            self._set_status(f"Installing for {item.name}...")
            ok, _ = self.client.PLUGIN.install_pending(item.key, log=self._set_status)
            (succeeded if ok else failed).append(item)
        self.client.call_on_ui(lambda: self._finish(succeeded, failed))

    def _finish(self, succeeded: list, failed: list) -> None:
        self._installing = False
        self.client.DIALOG.close()

        if succeeded and not failed:
            self.client.simple_notify(
                "check", "Plugins",
                f"Installed and loaded {len(succeeded)} plugin(s).",
            )
        elif succeeded and failed:
            self.client.simple_notify(
                "error", "Plugins",
                f"{len(succeeded)} installed, {len(failed)} failed. See the log.",
            )
        else:
            self.client.simple_notify(
                "error", "Plugins",
                f"Could not install packages for {len(failed)} plugin(s). See the log.",
            )


class ConfirmDialog(_DialogShell):
    """Generic confirm. `detail` renders as a muted block under the body."""

    def __init__(self, client: "Client", title: str, body: str,
                 confirm_text: str, on_confirm: Callable,
                 detail: Optional[str] = None,
                 confirm_style: str = "plugin-action-unload"):
        super().__init__(client)
        self._on_confirm = on_confirm

        self.content.addWidget(_title(title))
        self.content.addWidget(_body(body, muted=True))

        if detail:
            block = QFrame()
            set_style(block, "settings", "setting-block")
            v = QVBoxLayout(block)
            v.setContentsMargins(14, 10, 14, 10)
            lbl = QLabel(detail)
            lbl.setFont(make_font(SIZES.S1))
            lbl.setWordWrap(True)
            set_style(lbl, "common", "text-muted")
            v.addWidget(lbl)
            self.content.addWidget(block)

        self.buttons.addWidget(_button("Cancel", "plugin-action-copy", self.client.DIALOG.close))
        self.buttons.addWidget(_button(confirm_text, confirm_style, self._confirm))

    def _confirm(self) -> None:
        self.client.DIALOG.close()
        self._on_confirm()
