from __future__ import annotations

from threading import Thread
from typing import TYPE_CHECKING, Callable, Iterable, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QLineEdit,
    QButtonGroup, QRadioButton, QSizePolicy,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics

from src.styling import make_font, SIZES, set_style
from src.ui.overlays import BaseDialog
from src.plugin import dependencies as pipdeps

if TYPE_CHECKING:
    from src.main import Client


## -- GENERIC ----------------------------------------------------------------

class AlertDialog(BaseDialog):

    def __init__(self, client: "Client", title: str, body: str = "",
                 ok_text: str = "OK", on_close: Callable = None,
                 detail: str = None):
        super().__init__(client, title, body, detail=detail)
        self._on_close = on_close
        self.add_button(ok_text, self._dismiss, "primary")

    def _dismiss(self) -> None:
        self.close()
        if self._on_close:
            self._on_close()


class ConfirmDialog(BaseDialog):

    def __init__(self, client: "Client", title: str, body: str = "",
                 on_confirm: Callable = None, on_cancel: Callable = None,
                 confirm_text: str = "Confirm", cancel_text: str = "Cancel",
                 destructive: bool = False, detail: str = None):
        super().__init__(client, title, body, detail=detail)
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel

        self.add_button(cancel_text, self._cancel, "secondary")
        self.add_button(confirm_text, self._confirm,
                        "destructive" if destructive else "primary")

    def _confirm(self) -> None:
        self.close()
        if self._on_confirm:
            self._on_confirm()

    def _cancel(self) -> None:
        self.close()
        if self._on_cancel:
            self._on_cancel()


class InputDialog(BaseDialog):

    def __init__(self, client: "Client", title: str, body: str = "",
                 on_submit: Callable = None, on_cancel: Callable = None,
                 default: str = "", placeholder: str = "",
                 submit_text: str = "OK", cancel_text: str = "Cancel",
                 numeric: bool = False, password: bool = False,
                 allow_empty: bool = False, detail: str = None):
        super().__init__(client, title, body, detail=detail)
        self._on_submit = on_submit
        self._on_cancel = on_cancel
        self._allow_empty = allow_empty
        self._keyboard = None
        self._kb_label = title
        self._kb_description = body

        wrapper = QFrame()
        wrapper.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(wrapper, "overlays", "dialog-input-wrapper")
        wrap_layout = QVBoxLayout(wrapper)
        wrap_layout.setContentsMargins(2, 2, 2, 2)

        self.field = QLineEdit(default)
        self.field.setFont(make_font(SIZES.S3))
        self.field.setFixedHeight(48)
        self.field.setPlaceholderText(placeholder)
        if password:
            self.field.setEchoMode(QLineEdit.EchoMode.Password)
        set_style(self.field, "overlays", "dialog-input")
        wrap_layout.addWidget(self.field)
        self.content.addWidget(wrapper)

        self._numeric = numeric
        self.field.setReadOnly(True)
        self.field.setCursor(Qt.CursorShape.PointingHandCursor)

        def _open_keyboard(event=None):
            self._show_keyboard()

        self.field.mousePressEvent = _open_keyboard
        self.field.focusInEvent = _open_keyboard
        self.field.returnPressed.connect(self._submit)

        self.add_button(cancel_text, self._cancel, "secondary")
        self._submit_btn = self.add_button(submit_text, self._submit, "primary")

        self.field.textChanged.connect(self._sync_submit)
        self._sync_submit(self.field.text())

    def _sync_submit(self, text: str) -> None:
        self._submit_btn.setEnabled(self._allow_empty or bool(text.strip()))

    def _show_keyboard(self) -> None:
        try:
            from src.ui.keyboard import make_keyboard
            self._keyboard = make_keyboard(
                self.client, self.field,
                "int" if self._numeric else "string",
                label=self._kb_label, description=self._kb_description,
            )
            self._keyboard.show_keyboard()
        except Exception as e:
            self.client.log("debug", f"[InputDialog] no on-screen keyboard: {e}")

    def _hide_keyboard(self) -> None:
        if self._keyboard is None:
            return
        try:
            self._keyboard.hide()
            self._keyboard.setParent(None)
            self._keyboard.deleteLater()
        except Exception:
            pass
        self._keyboard = None

    def _submit(self) -> None:
        value = self.field.text()
        if not self._allow_empty and not value.strip():
            return
        self._hide_keyboard()
        self.close()
        if self._on_submit:
            self._on_submit(value)

    def _cancel(self) -> None:
        self._hide_keyboard()
        self.close()
        if self._on_cancel:
            self._on_cancel()


class ChoiceDialog(BaseDialog):

    # A radio button does not wrap its text - a long option is simply clipped
    # by the card around it. Rather than capping what a caller may write, the
    # dialog measures its longest option and widens to fit, bounded by the
    # screen. The ratio matches the wide calendar dialogs, so the two look
    # like the same kind of surface.
    MAX_WIDTH_RATIO = 0.86

    #indicator + its margin + the row's own padding + the card's margins,
    #plus room for a scrollbar when the list is long enough to need one
    _ROW_CHROME = 22 + 12 + 28 + 48 + 18

    def __init__(self, client: "Client", title: str, body: str = "",
                 options: Iterable = (), on_choose: Callable = None,
                 on_cancel: Callable = None, default=None,
                 choose_text: str = "Select", cancel_text: str = "Cancel",
                 detail: str = None):
        options = list(options)
        super().__init__(client, title, body,
                         width=self._fit_width(client, options), detail=detail)
        self._on_choose = on_choose
        self._on_cancel = on_cancel
        self._values = []

        inner = QWidget()
        set_style(inner, "common", "transparent")
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for index, option in enumerate(options):
            if isinstance(option, (tuple, list)) and len(option) == 2:
                value, label = option
            else:
                value = label = option
            self._values.append(value)

            radio = QRadioButton(str(label))
            radio.setFont(make_font(SIZES.S2))
            radio.setCursor(Qt.CursorShape.PointingHandCursor)
            # A whole row rather than a radio dot to aim at - this is read and
            # tapped standing at a wall panel, not clicked with a mouse.
            radio.setMinimumHeight(56)
            radio.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Fixed)
            set_style(radio, "overlays", "dialog-choice")
            self._group.addButton(radio, index)
            v.addWidget(radio)

            if (default is not None and value == default) or (default is None and index == 0):
                radio.setChecked(True)

        v.addStretch()
        # Sized to the taller rows, or the list scrolls when it did not need to
        # and the options below the fold are the ones nobody knows are there.
        self.add_scroll(inner, min_height=min(360, max(72, 64 * max(1, len(self._values)))))

        self.add_button(cancel_text, self._cancel, "secondary")
        self.add_button(choose_text, self._choose, "primary")

    @classmethod
    def _fit_width(cls, client, options: list) -> int:
        """
        Wide enough for the longest option, never wider than the screen.

        Measured rather than guessed: the labels come from callers, including
        plugins, and one that runs a few words long was being cut off by the
        card with no indication there was more to read.
        """
        widest = 0
        try:
            metrics = QFontMetrics(make_font(SIZES.S2))
            for option in options:
                if isinstance(option, (tuple, list)) and len(option) == 2:
                    label = option[1]
                else:
                    label = option
                widest = max(widest, metrics.horizontalAdvance(str(label)))
        except Exception:
            return cls.WIDTH

        wanted = widest + cls._ROW_CHROME

        ceiling = cls.WIDTH
        try:
            host = getattr(client, "OVERLAYS", None)
            if host is not None and host.width() > 0:
                ceiling = max(cls.WIDTH, int(host.width() * cls.MAX_WIDTH_RATIO))
        except Exception:
            pass

        return max(cls.WIDTH, min(wanted, ceiling))

    def _choose(self) -> None:
        index = self._group.checkedId()
        self.close()
        if self._on_choose and 0 <= index < len(self._values):
            self._on_choose(self._values[index])

    def _cancel(self) -> None:
        self.close()
        if self._on_cancel:
            self._on_cancel()


class DurationPickerDialog(BaseDialog):
    """
    A length of time, as steppers rather than a list of presets.

    The same control the calendar uses for a time of day, asking a different
    question: a preset list can only ever offer what somebody guessed in
    advance, and "seven minutes" is not an unreasonable thing to want.

    Sized to its content - three steppers and a row of buttons do not need a
    screen-wide dialog, and stretching them apart separates the numbers being
    compared.
    """

    WIDTH = 560
    MINUTE_STEP = 1

    def __init__(self, client: "Client", title: str = "How long?",
                 body: str = "", seconds: float = 300,
                 on_chosen: Callable = None, choose_text: str = "Start",
                 show_seconds: bool = True):
        super().__init__(client, title, body)
        self.on_chosen = on_chosen

        total = max(0, int(seconds or 0))
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)

        from src.ui.controls.stepper import Stepper

        row = QHBoxLayout()
        row.setSpacing(18)
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.hours = Stepper("Hours", min(23, hours), 0, 23, wrap=False,
                             on_change=lambda _: self._update_readout())
        self.minutes = Stepper("Minutes", minutes, 0, 59,
                               on_change=lambda _: self._update_readout(),
                               step=self.MINUTE_STEP)
        row.addWidget(self.hours)
        row.addWidget(self._colon())
        row.addWidget(self.minutes)

        self.seconds = None
        if show_seconds:
            self.seconds = Stepper("Seconds", secs, 0, 59,
                                   on_change=lambda _: self._update_readout(),
                                   step=5)
            row.addWidget(self._colon())
            row.addWidget(self.seconds)

        holder = QWidget()
        set_style(holder, "common", "transparent")
        holder.setLayout(row)
        self.content.addWidget(holder)

        self.readout = QLabel("")
        self.readout.setFont(make_font(SIZES.S2))
        self.readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.readout, "common", "text-muted")
        self.content.addWidget(self.readout)

        self.add_button("Cancel", self.close, "secondary")
        self._confirm = self.add_button(choose_text, self._choose, "primary")
        self._update_readout()

    def _colon(self) -> QLabel:
        colon = QLabel(":")
        colon.setFont(make_font(SIZES.L1, bold=True))
        set_style(colon, "common", "text-muted")
        return colon

    def total_seconds(self) -> int:
        total = self.hours.value * 3600 + self.minutes.value * 60
        if self.seconds is not None:
            total += self.seconds.value
        return int(total)

    def _update_readout(self) -> None:
        total = self.total_seconds()
        if total <= 0:
            self.readout.setText("Set a length above zero.")
        else:
            self.readout.setText(_describe_duration(total))
        # Disabling it says why nothing happens on a tap, where a dialog that
        # simply refused to close would not.
        button = getattr(self, "_confirm", None)
        if button is not None:
            button.setEnabled(total > 0)

    def _choose(self) -> None:
        total = self.total_seconds()
        if total <= 0:
            return
        self.close()
        if callable(self.on_chosen):
            self.on_chosen(total)


def _describe_duration(seconds: int) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if minutes:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    if secs:
        parts.append(f"{secs} second" + ("s" if secs != 1 else ""))
    return " ".join(parts) or "0 seconds"


class ProgressDialog(BaseDialog):

    def __init__(self, client: "Client", title: str, body: str = ""):
        super().__init__(client, title, body)
        self.status = self.make_body("Working...", muted=True)
        self.content.addWidget(self.status)

    def set_status(self, text: str) -> None:
        line = " ".join(str(text).split())
        if len(line) > 90:
            line = line[:87] + "..."
        self.client.call_on_ui(lambda: self.status.setText(line))


## -- PLUGIN DEPENDENCIES ----------------------------------------------------

class DependencyDialog(BaseDialog):

    def __init__(self, client: "Client", pending: list):
        count = len(pending)
        super().__init__(
            client,
            title=(f"{count} plugin{'s' if count != 1 else ''} need"
                   f"{'' if count != 1 else 's'} additional packages"),
            body=("These plugins were not loaded because packages they declare "
                  "are not installed. Review what would be installed before "
                  "continuing."),
        )
        self.pending = pending
        self._installing = False
        self._build_prompt()

    def _build_prompt(self) -> None:
        inner = QWidget()
        set_style(inner, "common", "transparent")
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        for item in self.pending:
            v.addWidget(self._plugin_row(item))
        v.addStretch()
        self.add_scroll(inner, min_height=min(240, 78 * max(1, len(self.pending))))

        self.content.addWidget(self.make_body(
            f"Installing into: {pipdeps.venv_path()}", muted=True))
        self.content.addWidget(self.make_body(
            "Only install packages from plugins you trust. Package names come "
            "from the plugin's own plugin.toml.", muted=True))

        self.add_button("Not Now", self._decline, "secondary")
        self.add_button("Install All", self._install, "primary")

    def _plugin_row(self, item) -> QFrame:
        row = QFrame()
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(row, "overlays", "dialog-detail")
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

    def _decline(self) -> None:
        for item in self.pending:
            item.declined = True
        self.client.log("info", "[Dependencies] Install declined by user.")
        self.close()
        self.client.simple_notify(
            "extension", "Plugins",
            f"{len(self.pending)} plugin(s) not loaded. Install from Settings when ready.",
        )

    def _install(self) -> None:
        if self._installing:
            return
        self._installing = True
        self.clear_content()
        self.clear_buttons()
        self.status = self.make_body("Starting pip...", muted=True)
        self.content.addWidget(self.status)
        self.center()
        Thread(target=self._worker, name="__plugin_pip_install", daemon=True).start()

    def _set_status(self, text: str) -> None:
        line = " ".join(str(text).split())
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
        self.close()
        if succeeded and not failed:
            self.client.simple_notify("check", "Plugins",
                                      f"Installed and loaded {len(succeeded)} plugin(s).")
        elif succeeded and failed:
            self.client.simple_notify("error", "Plugins",
                                      f"{len(succeeded)} installed, {len(failed)} failed. See the log.")
        else:
            self.client.simple_notify("error", "Plugins",
                                      f"Could not install packages for {len(failed)} plugin(s). See the log.")


class ActionSheet(BaseDialog):
    """
    A row's actions, as things big enough to hit.

    A `QMenu` is desktop furniture: its items are the height of a line of text,
    it opens wherever the pointer happens to be, and it expects a press-drag-
    release that a finger does not perform. On a wall panel it is a row of
    targets a few millimetres tall.

    This is the same list as full-width rows in a dialog the panel already knows
    how to show - centred, dimmed behind, sized for a thumb. **One tap acts**,
    with no Select step: these are the actions that used to be buttons, and a
    button does not ask twice.

    A destructive entry keeps its own colour, so the tap that loses something
    does not look like the tap beside it.
    """

    #Comfortably past the ~44px usually quoted as a minimum touch target, since
    #this panel is looked at and prodded from standing height.
    ROW_HEIGHT = 56
    WIDTH = 520

    def __init__(self, client: "Client", title: str, items: list):
        super().__init__(client, title=title, width=self.WIDTH)
        from src.ui.controls.buttons import ActionButton

        for entry in items:
            if not entry:
                continue
            label, callback = entry[0], entry[1]
            glyph = entry[2] if len(entry) > 2 else ""
            kind = entry[3] if len(entry) > 3 else "secondary"

            button = ActionButton(glyph, label, None, kind=kind,
                                  size=self.ROW_HEIGHT, icon_size=22)
            button.setFont(make_font(SIZES.S2, bold=True))
            # Full width: a row that only spans its text is a smaller target
            # than the row it replaced, which defeats the point.
            button.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Fixed)
            button.clicked.connect(
                lambda _=False, fn=callback: self._pick(fn))
            self.content.addWidget(button)

        self.add_button("Cancel", self.close, kind="secondary")

    def _pick(self, callback) -> None:
        # Closed first, so a callback that opens its own dialog - Forget asks
        # for confirmation - is not fighting this one on the way out.
        self.close()
        try:
            callback()
        except Exception as e:
            self.client.log("warning", f"[ActionSheet] {e}")
