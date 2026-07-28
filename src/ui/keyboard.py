from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QTextEdit, QPlainTextEdit, QFrame,
)
from PyQt6.QtCore import Qt, QTimer, QEvent

from src.styling import make_font, SIZES, set_style
from src.ui.overlays import BaseDialog

if TYPE_CHECKING:
    from src.main import Client

# Sized for fingers, not a mouse. 64px is roughly a fingertip at arm's length
# on a wall panel; the old 52px grid was usable with a cursor and fiddly
# without one.
KEY_W = 72
KEY_H = 68
GAP = 6

# Rows are offset like a real keyboard, in fractions of a key width. Without
# this every column lines up and muscle memory does not transfer.
# The furthest a row is indented, in key widths. The dialog has to be wide
# enough for the longest row PLUS this, or its right-hand keys are cut off.
MAX_ROW_OFFSET = 0.9

LETTER_ROWS = [
    (0.0, list("1234567890")),
    (0.5, list("qwertyuiop")),
    (0.9, list("asdfghjkl")),
    (0.0, ["shift"] + list("zxcvbnm") + ["backspace"]),
]

SYMBOL_ROWS = [
    (0.0, list("1234567890")),
    (0.5, list("-/:;()$&@\"")),
    (0.9, list(".,?!'#%*+=")),
    (0.0, ["letters"] + list("_<>[]{}") + ["backspace"]),
]

NUMPAD_ROWS = [
    (0.0, list("789")),
    (0.0, list("456")),
    (0.0, list("123")),
    (0.0, ["negate", "0", "backspace"]),
]

GLYPHS = {
    "shift": "⇧", "backspace": "⌫", "space": "space",
    "symbols": "?123", "letters": "ABC", "negate": "±", "clear": "clear",
}

WIDE = {"space": 5.0, "shift": 1.6, "backspace": 1.6,
        "symbols": 1.6, "letters": 1.6, "clear": 1.6}


class _Key(QPushButton):
    # Keys that repeat while held. Deleting a long value one tap at a time is
    # the single most tedious thing this keyboard asks of anyone.
    REPEATING = ("backspace", "space")

    HOLD_DELAY   = 450     # before the first repeat, so a tap is never two
    FIRST_REPEAT = 220     # deliberately slow to start
    MIN_REPEAT   = 45      # about 22 a second, which is as fast as is readable
    ACCEL        = 0.84    # each repeat is a little quicker than the last

    def __init__(self, action: str, label: str = None, units: float = 1.0,
                 width: int = KEY_W, height: int = KEY_H):
        super().__init__(label if label is not None else GLYPHS.get(action, action))
        self.action = action
        self.setFont(make_font(SIZES.S3 if height >= 48 else SIZES.S2))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(int(width * units + GAP * (units - 1)), height)
        set_style(self, "keyboard", self._style_for(action))

        # Set by _make_key. A repeat calls this rather than the button's own
        # click(), for the reason in _fire_repeat.
        self.on_repeat = None

        self._repeat = None
        if action in self.REPEATING:
            # Single-shot and restarted each time, because the interval has to
            # change between firings - a repeating timer keeps its first one.
            self._repeat = QTimer(self)
            self._repeat.setSingleShot(True)
            self._repeat.timeout.connect(self._fire_repeat)
            self._interval = self.FIRST_REPEAT
            self.pressed.connect(self._start_repeat)
            self.released.connect(self._stop_repeat)

    ## -- hold to repeat

    def _start_repeat(self) -> None:
        self._interval = self.FIRST_REPEAT
        self._repeat.start(self.HOLD_DELAY)

    def _fire_repeat(self) -> None:
        # NOT click(). QAbstractButton::click() sets the button back up, so the
        # real release afterwards emits no released() at all - the timer then
        # never stopped, and backspace kept firing forever. Every character
        # typed was deleted within 45ms of arriving, which reads as the
        # keyboard being dead rather than as a stuck key.
        if not self.isDown():
            self._repeat.stop()
            return

        if callable(self.on_repeat):
            self.on_repeat()

        self._interval = max(self.MIN_REPEAT, int(self._interval * self.ACCEL))
        self._repeat.start(self._interval)

    def _stop_repeat(self) -> None:
        if self._repeat is not None:
            self._repeat.stop()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        # The grid is rebuilt on every shift and layer change, so a held key
        # can be taken out from under its own timer.
        self._stop_repeat()
        super().hideEvent(event)

    @staticmethod
    def _style_for(action: str) -> str:
        if action in ("space",):
            return "key-space"
        if action in ("shift", "backspace", "symbols", "letters", "clear", "negate"):
            return "key-modifier"
        return "key-button"


class _MultilinePreview(QTextEdit):
    """
    QTextEdit that quacks like a QLineEdit, so the key handling does not need
    two code paths.
    """

    def __init__(self, text: str = ""):
        super().__init__()
        self.setAcceptRichText(False)
        # Editable on purpose. A read-only text edit moves its cursor but
        # never draws one, so tap-to-position worked while giving no clue
        # where the next key would land. Hardware key input is blocked by
        # KeyboardDialog.eventFilter instead, which leaves the caret visible
        # and blinking without letting a real keyboard bypass the on-screen
        # one.
        self.setReadOnly(False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setText(text)

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, value: str) -> None:
        self.setPlainText(value)
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def setCursorPosition(self, _position: int) -> None:
        self.ensureCursorVisible()


class KeyboardDialog(BaseDialog):
    """
    Full-screen-ish editing dialog: what you are editing, why, the value, and
    a keyboard.

    Replaces a popup that slid up over the bottom of the page - which covered
    the field being edited on a short screen, gave no indication of which
    setting was in play, and had no room for a description.
    """

    WIDTH = KEY_W * 10 + GAP * 9 + 56
    MAX_HEIGHT = 900
    MIN_KEY_H = 44
    MIN_KEY_W = 48         # only reached on a panel too narrow for the preferred size
    SIDE_MARGIN = 32       # breathing room either side of the dialog
    CHROME_W = 56          # dialog padding around the key grid
    MIN_PREVIEW_H = 60     # two or three lines; below this it is pointless
    MAX_KEY_W = 124        # beyond this keys stop being easier to hit

    def __init__(self, client: "Client", target, mode: str = "text",
                 label: str = "", description: str = "", on_done=None):
        # On a short panel the description is the first thing to go. A
        # keyboard running off the bottom of an 800x480 screen is a worse
        # trade than losing a line of explanation.
        available = self._probe_host_height(client)
        if available and available < 560:
            description = ""

        # The widest row is ten keys, but the staggered rows start up to 0.9 of
        # a key in, so the grid really spans 10.9 keys. Solving for ten alone
        # left the right-hand column clipped; adding the allowance afterwards
        # made the dialog wider than the screen.
        span = 10 + MAX_ROW_OFFSET

        host_width = self._probe_host_width(client)
        if host_width:
            usable = host_width - self.SIDE_MARGIN * 2
            key_w = int((usable - self.CHROME_W - GAP * 9) / span)
            key_w = max(KEY_W, min(self.MAX_KEY_W, key_w))
        else:
            key_w = KEY_W

        width = int(key_w * span + GAP * 9 + self.CHROME_W)
        if host_width:
            capped = host_width - self.SIDE_MARGIN
            if width > capped:
                # Clamping the dialog without also shrinking the keys leaves a
                # grid wider than the dialog holding it, and the layout squeezes
                # the rows into each other. The keys give first.
                width = capped
                key_w = max(self.MIN_KEY_W,
                            int((width - self.CHROME_W - GAP * 9) / span))

        super().__init__(client, label or "Edit value", description or "",
                         width=width)
        self.target = target
        # Called with the committed text when Done is pressed. Nothing fires
        # it on cancel - a caller that acts on the value should not act on a
        # value the user declined to give.
        self.on_done = on_done
        self.mode = mode

        # Keys scale to the screen rather than assuming one. At the full size
        # the dialog is ~580px, which leaves nothing on a 600px panel; a
        # keyboard that runs off the bottom is worse than slightly smaller
        # keys. MIN_KEY_H is the floor - below that it stops being touchable
        # and there is no point shrinking further.
        self.multiline = isinstance(target, (QTextEdit, QPlainTextEdit))

        self.key_h = KEY_H
        self.key_w = key_w
        if available and available < 660:
            # Everything that is not keys: title, preview, buttons, margins,
            # plus the description when it survived.
            overhead = 260 if not description else 300
            self.key_h = max(self.MIN_KEY_H, min(KEY_H, int((available - overhead) / 5) - GAP))

        if self.multiline:
            # Editing a paragraph, so reading room beats key height. Keys drop
            # to the touch floor and the preview takes what that frees up -
            # otherwise a 600px panel leaves about four lines of a prompt
            # visible, which is barely better than the single-line field the
            # dialog replaced.
            self.key_h = self.MIN_KEY_H
        self.key_w = key_w

        self._caps = False
        self._shift_latched = False
        self._layer = "letters"

        # A paragraph needs more than one line to be readable, so a multi-line
        # target gets a multi-line preview. Both expose .text()/.setText() so
        # the key handling below does not have to care which it is.
        if self.multiline:
            self.preview = _MultilinePreview(self._read_target())
            self.preview.setFixedHeight(self._preview_height(available))
        else:
            self.preview = QLineEdit(self._read_target())
            self.preview.setFixedHeight(50)
    
        self.preview.setFont(make_font(SIZES.S3))
        # Clickable, so a caret can be placed by tapping. Focus stays off the
        # keys, which have no focus policy of their own.
        self.preview.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.preview.setReadOnly(False)
        self.preview.installEventFilter(self)
        self.preview.setCursor(Qt.CursorShape.IBeamCursor)
        set_style(self.preview, "keyboard", "keyboard-preview")
        self.content.addWidget(self.preview)

        self.keys_host = QWidget()
        set_style(self.keys_host, "common", "transparent")
        self.keys_layout = QVBoxLayout(self.keys_host)
        self.keys_layout.setContentsMargins(0, 4, 0, 0)
        self.keys_layout.setSpacing(GAP)
        # The grid is a fixed-width block centred in the dialog rather than
        # something that fills it. The dialog is sized for a worst case - ten
        # keys plus the largest stagger offset - that no single row actually
        # reaches, so filling left the whole keyboard hard against the left
        # edge with all the slack on the right.
        self.content.addWidget(self.keys_host, 0,
                               Qt.AlignmentFlag.AlignHCenter)

        self._build_keys()

        self.add_button("Paste", self._paste, "secondary")
        self.add_button("Cancel", self.close, "secondary")
        self.add_button("Done", self._done, "primary")

    @staticmethod
    def _probe_host_width(client) -> int:
        try:
            return int(client.OVERLAYS.width())
        except Exception:
            return 0

    @staticmethod
    def _probe_host_height(client) -> int:
        try:
            return int(client.OVERLAYS.height())
        except Exception:
            return 0

    def _host_height(self) -> int:
        return self._probe_host_height(self.client)

    def _preview_height(self, available: int) -> int:
        # A starting guess only. Predicting the exact chrome height - title,
        # wrapped description, button row, margins - is guesswork, so
        # center() measures the built dialog and trims this to fit.
        if not available:
            return 200
        spare = available - (self.key_h + GAP) * 5 - 190
        return max(120, min(300, spare))

    def center(self) -> None:
        # Measure, then fit. The preview is the only elastic part, so it
        # absorbs whatever the rest of the dialog actually turned out to need.
        host = self._host_height()
        if host and self.multiline:
            # Trim until it fits or the preview reaches its floor. One pass is
            # not always enough: shrinking the preview can rewrap the
            # description, which changes the chrome height underneath it.
            for _ in range(3):
                self.adjustSize()
                overflow = self.sizeHint().height() - (host - 16)
                if overflow <= 0 or self.preview.height() <= self.MIN_PREVIEW_H:
                    break
                self.preview.setFixedHeight(
                    max(self.MIN_PREVIEW_H, self.preview.height() - overflow))
                self.updateGeometry()
                # adjustSize() only ever grows a widget to its hint; shrinking
                # needs an explicit resize.
                self.resize(self.width(), self.sizeHint().height())
        super().center()

    ## -- target

    def _read_target(self) -> str:
        if isinstance(self.target, (QTextEdit, QPlainTextEdit)):
            return self.target.toPlainText()
        return self.target.text()

    def _write_target(self, text: str) -> None:
        if isinstance(self.target, (QTextEdit, QPlainTextEdit)):
            self.target.setPlainText(text)
        else:
            self.target.setText(text)

    ## -- layout

    def _rows(self):
        if self.mode == "numeric":
            return NUMPAD_ROWS
        return SYMBOL_ROWS if self._layer == "symbols" else LETTER_ROWS

    def _key_width(self, action: str) -> int:
        """Rendered width of one key, matching what _Key sets on itself."""
        units = WIDE.get(action, 1.0)
        return int(self.key_w * units + GAP * (units - 1))

    def _row_width(self, offset: float, keys: list) -> int:
        if not keys:
            return 0
        return (int(self.key_w * offset)
                + sum(self._key_width(action) for action in keys)
                + GAP * (len(keys) - 1))

    def _grid_width(self) -> int:
        """
        How wide the key grid actually is: the widest row, including the
        stagger offset that pushes it right.

        Measured rather than assumed. WIDTH and the constructor both solve for
        ten keys plus MAX_ROW_OFFSET, which is deliberately generous - the row
        carrying the 0.9 offset only has nine keys, so nothing ever spans the
        full allowance. Centring needs the real number.
        """
        rows = list(self._rows()) + [(0.0, self._bottom_row_keys())]
        return max(self._row_width(offset, keys) for offset, keys in rows)

    def _build_keys(self) -> None:
        while self.keys_layout.count():
            item = self.keys_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())

        for offset, keys in self._rows():
            row = QHBoxLayout()
            row.setSpacing(GAP)
            row.setContentsMargins(0, 0, 0, 0)
            if self.mode == "numeric":
                row.addStretch()
            elif offset:
                row.addSpacing(int(self.key_w * offset))
            for action in keys:
                row.addWidget(self._make_key(action))
            if self.mode == "numeric":
                row.addStretch()
            else:
                row.addStretch()
            self.keys_layout.addLayout(row)

        self.keys_layout.addLayout(self._bottom_row())

        # Recomputed on every rebuild: switching to the symbol layer changes
        # which row is widest, and so does switching between text and numeric.
        self.keys_host.setFixedWidth(self._grid_width())

    def _bottom_row_keys(self) -> list:
        if self.mode == "numeric":
            return [".", "negate", "clear"]
        return ["symbols" if self._layer == "letters" else "letters",
                ",", "space", ".", "clear"]

    def _bottom_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(GAP)
        row.setContentsMargins(0, 0, 0, 0)
        if self.mode == "numeric":
            row.addStretch()
            for action in self._bottom_row_keys():
                row.addWidget(self._make_key(action))
            row.addStretch()
        else:
            for action in self._bottom_row_keys():
                row.addWidget(self._make_key(action))
            row.addStretch()
        return row

    def _make_key(self, action: str) -> _Key:
        label = None
        if len(action) == 1 and action.isalpha():
            label = action.upper() if self._caps else action
        key = _Key(action, label, WIDE.get(action, 1.0),
                   width=self.key_w, height=self.key_h)
        if action == "shift" and self._caps:
            set_style(key, "keyboard", "key-modifier-active")
        key.clicked.connect(lambda _=False, a=action: self._press(a))
        key.on_repeat = lambda a=action: self._press(a)
        return key

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                KeyboardDialog._clear_layout(item.layout())

    ## -- keys

    # Keys that only move the caret. Everything else from a physical keyboard
    # is dropped, so the preview stays editable - and therefore draws a caret
    # - without becoming a second, competing input path.
    # Compared as plain ints: a PyQt6 enum member is not equal to the int
    # event.key() returns, and Qt.Key(value) raises on a keycode outside the
    # enum - which a media or vendor key on a real keyboard will be.
    _NAV_KEYS = {
        Qt.Key.Key_Left.value, Qt.Key.Key_Right.value,
        Qt.Key.Key_Up.value, Qt.Key.Key_Down.value,
        Qt.Key.Key_Home.value, Qt.Key.Key_End.value,
        Qt.Key.Key_PageUp.value, Qt.Key.Key_PageDown.value,
    }

    def eventFilter(self, watched, event) -> bool:
        if watched is getattr(self, "preview", None) and event.type() in (
                QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            if int(event.key()) not in self._NAV_KEYS:
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _focus_preview(self) -> None:
        """
        Put the caret in the preview and leave it there.

        Without this the Done button takes focus when the dialog opens and the
        caret is not drawn until the field is tapped - which reads as the same
        bug it is meant to fix. The keys are NoFocus, so nothing steals it back.
        """
        self.preview.setFocus(Qt.FocusReason.OtherFocusReason)
        self.set_caret(len(self.preview.text()))

    def caret(self) -> int:
        """Where the caret is, so typing happens there rather than at the end."""
        try:
            if self.multiline:
                return self.preview.textCursor().position()
            return self.preview.cursorPosition()
        except Exception:
            return len(self.preview.text())

    def set_caret(self, position: int) -> None:
        text = self.preview.text()
        position = max(0, min(position, len(text)))
        try:
            if self.multiline:
                cursor = self.preview.textCursor()
                cursor.setPosition(position)
                self.preview.setTextCursor(cursor)
            else:
                self.preview.setCursorPosition(position)
        except Exception:
            pass

    def _insert(self, chunk: str) -> None:
        at = self.caret()
        text = self.preview.text()
        self.preview.setText(text[:at] + chunk + text[at:])
        self.set_caret(at + len(chunk))

    def _press(self, action: str) -> None:
        text = self.preview.text()

        if action == "backspace":
            at = self.caret()
            if at <= 0:
                return
            self.preview.setText(text[:at - 1] + text[at:])
            self.set_caret(at - 1)
        elif action == "space":
            self._insert(" ")
        elif action == "clear":
            self.preview.setText("")
            self.set_caret(0)
        elif action == "shift":
            self._caps = not self._caps
            self._shift_latched = False
            self._build_keys()
            return
        elif action in ("symbols", "letters"):
            self._layer = "symbols" if action == "symbols" else "letters"
            self._build_keys()
            return
        elif action == "negate":
            self.preview.setText(text[1:] if text.startswith("-") else "-" + text)
        else:
            char = action.upper() if (self._caps and action.isalpha()) else action
            self._insert(char)
            if self._caps and not self._shift_latched:
                # One-shot shift, the way a phone keyboard behaves: capitalise
                # one letter then drop back rather than staying locked.
                self._caps = False
                self._build_keys()



    ## -- lifecycle

    def _paste(self) -> None:
        """
        Insert the clipboard at the caret.

        Typing a URL or an address on a touch keyboard is the slowest thing
        this app asks of anyone, and half the time it was copied from
        somewhere a moment ago.
        """
        try:
            from PyQt6.QtWidgets import QApplication
            text = QApplication.clipboard().text()
        except Exception as e:
            self.client.log("debug", f"[Keyboard] Clipboard unreadable: {e}")
            return

        if not text:
            self.client.simple_notify("mdi.clipboard-outline", "Keyboard",
                                      "Nothing on the clipboard.")
            return

        # Single-line fields get one line - a pasted paragraph would otherwise
        # arrive with newlines the field cannot show.
        if self.mode != "body":
            text = text.replace("\r", " ").replace("\n", " ").strip()

        at = self.caret()
        current = self.preview.text()
        self.preview.setText(current[:at] + text + current[at:])
        self.set_caret(at + len(text))

    ## -- lifecycle

    def _done(self) -> None:
        text = self.preview.text()
        self._write_target(text)
        self.close()
        # After the write and after the close, so a handler that opens another
        # dialog is not stacking it under this one on its way out.
        if callable(self.on_done):
            try:
                self.on_done(text)
            except Exception as e:
                self.client.log("warning", f"[Keyboard] on_done failed: {e}")

    def show_keyboard(self) -> None:
        """Kept for callers written against the old popup."""
        # Exactly one keyboard at a time. A tap fires mousePressEvent on the
        # line edit AND, unaccepted, on its parent, and focusInEvent besides -
        # so a single tap could stack two or three identical dialogs. Closing
        # the top one revealed the next, which reads as a button that needs
        # clicking twice.
        for existing in getattr(self.client.DIALOG, "dialog_stack", []):
            if isinstance(existing, KeyboardDialog):
                return
        self.client.dialog(self)
        QTimer.singleShot(0, self.center)
        QTimer.singleShot(0, self._focus_preview)

    def close_keyboard(self) -> None:
        self.close()


def make_keyboard(client: "Client", target, setting_type: str,
                  parent: QWidget = None, label: str = "",
                  description: str = "") -> KeyboardDialog:
    numeric_types = {"int", "float", "numeric", "list[int]", "list[float]"}
    mode = "numeric" if setting_type in numeric_types else "text"
    return KeyboardDialog(client, target, mode=mode,
                          label=label, description=description)
