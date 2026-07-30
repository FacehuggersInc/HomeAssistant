"""
The panel's own log, on the panel.

There is nowhere else to read it from. A wall panel has no terminal, and the
machine it runs on is usually not the one somebody is standing at - so a log
that only exists in `logs/latest.log` is a log nobody reads until they go and
find the machine.

Coloured by level rather than prettified: on a page of six hundred grey lines
the one that says WARN is the only thing anybody is looking for.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QScroller, QVBoxLayout,
    QWidget,
)

from src.styling import COLORS, SIZES, make_font, set_style
from src.ui.controls.buttons import ActionButton, action_column
from src.ui.icons import Icons

if TYPE_CHECKING:
    from src.main import Client

#Where log() writes. Read rather than hooked into: a page that subscribes to
#every log line has to filter, buffer and trim on the UI thread, and the file
#is already doing all three.
LOG_PATH = Path("logs") / "latest.log"

#How many lines to show. A whole session can be tens of thousands, and a text
#widget holding all of them costs more to scroll than the log is worth.
TAIL_LINES = 800

#How often to re-read while the section is on screen.
REFRESH_MS = 2000

#[2026/7/30 07:51:10][WARN][Backlight] ...
LINE = re.compile(r"^\[(?P<when>[^\]]+)\]\[(?P<level>[A-Z]+)\](?P<rest>.*)$")

#A [Tag] at the start of the message, which is how every line in this codebase
#says which subsystem it came from.
TAG = re.compile(r"^\s*(\[[^\]]+\])")

LEVEL_COLORS = {
    "ERRO": "#ff8b8b",
    "CRIT": "#ff8b8b",
    "WARN": "#ffcf7a",
    "INFO": "#8fd3ff",
    "DEBU": "#8a8a92",
}
DEFAULT_COLOR = "#c8cedb"
TIME_COLOR = "#6b6b73"
TAG_COLOR = "#9ad9b4"


def _tail(path: Path, limit: int = TAIL_LINES) -> list:
    """
    The last `limit` lines, without reading the whole file into memory.

    A session that has been up for days produces a file large enough that
    reading it whole to show the end of it is worth avoiding - and this runs on
    a timer.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return []

    # Roughly 200 bytes a line, with room to spare. Read from there to the end
    # and drop the first line, which is probably cut in half.
    window = min(size, limit * 260)
    try:
        with open(path, "rb") as handle:
            handle.seek(max(0, size - window))
            data = handle.read()
    except OSError:
        return []

    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if window < size and lines:
        lines = lines[1:]
    return lines[-limit:]


class LogView(QPlainTextEdit):
    """The log, coloured by level."""

    def __init__(self, client: "Client"):
        super().__init__()
        self.client = client
        self.setReadOnly(True)
        # Not selectable, so a drag scrolls.
        #
        # Read-only still allows selection, and on a touch screen a drag across
        # the text highlights it rather than moving the view - which on the one
        # page made entirely of text is the only gesture that matters. There is
        # no keyboard here to copy with anyway.
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setMaximumBlockCount(TAIL_LINES + 50)
        self.setFrameShape(QFrame.Shape.NoFrame)

        font = QFont("monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPixelSize(SIZES.S1)
        self.setFont(font)
        set_style(self, "settings", "log-view")

        # The same drag-to-scroll the rest of the panel uses.
        QScroller.grabGesture(self.viewport(),
                              QScroller.ScrollerGestureType.LeftMouseButtonGesture)
        # Left as-needed. Lines do not wrap, so turning the horizontal bar off
        # would put the end of a long line out of reach - QScroller drags both
        # axes and picks whichever the finger is actually moving along.
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._shown = 0
        self._follow = True
        self.verticalScrollBar().valueChanged.connect(self._scrolled)

    def _scrolled(self, value: int) -> None:
        """
        Follow the end only while the reader is already there.

        A page that jumps to the bottom every two seconds cannot be read: the
        line somebody scrolled up to look at slides away mid-sentence.
        """
        bar = self.verticalScrollBar()
        self._follow = value >= bar.maximum() - 4

    def _format(self, color: str, bold: bool = False) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        if bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        return fmt

    def _append(self, line: str) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        match = LINE.match(line)
        if not match:
            # A traceback, a bare print that got in, or a line from before the
            # format settled. Shown as it is rather than dropped - an
            # unparseable line is often the interesting one.
            cursor.insertText(line + "\n", self._format(DEFAULT_COLOR))
            return

        level = match.group("level")
        rest = match.group("rest")
        cursor.insertText(f"[{match.group('when')}] ",
                          self._format(TIME_COLOR))
        cursor.insertText(f"{level:<4} ",
                          self._format(LEVEL_COLORS.get(level, DEFAULT_COLOR),
                                       bold=True))

        tag = TAG.match(rest)
        if tag:
            cursor.insertText(tag.group(1), self._format(TAG_COLOR))
            rest = rest[tag.end():]
        cursor.insertText(rest + "\n",
                          self._format(LEVEL_COLORS.get(level, DEFAULT_COLOR)
                                       if level in ("ERRO", "CRIT", "WARN")
                                       else DEFAULT_COLOR))

    def load(self, force: bool = False) -> int:
        """
        Re-read the tail and append whatever is new.

        Only the new lines are appended, not the whole tail again: redrawing
        eight hundred lines every two seconds is what makes a log page unusable
        to scroll.
        """
        lines = _tail(LOG_PATH)
        if force:
            self.clear()
            self._shown = 0

        if self._shown > len(lines):
            # The file was rotated out from under us - a restart renames
            # latest.log and starts a new one.
            self.clear()
            self._shown = 0

        for line in lines[self._shown:]:
            self._append(line)
        self._shown = len(lines)

        if self._follow:
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())
        return len(lines)


class LogSection(QWidget):
    """The Logs section: a header, the view, and a place to say it is empty."""

    #Read by the settings page when it inserts this.
    #
    #The content layout ends in a stretch, which is right for a column of
    #cards and wrong for a single view - a log occupying 200px with empty
    #space under it shows about eight lines.
    fills_height = True

    def __init__(self, client: "Client"):
        super().__init__()
        self.client = client
        set_style(self, "common", "transparent")

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(12)
        self._status = QLabel("")
        self._status.setFont(make_font(SIZES.S2))
        set_style(self._status, "common", "text-muted")
        header.addWidget(self._status)
        header.addStretch()
        header.addWidget(action_column(
            ActionButton(Icons.REFRESH, "Reload",
                         lambda: self._refresh(force=True), kind="secondary"),
            slots=1))
        column.addLayout(header)

        self.view = LogView(client)
        column.addWidget(self.view, stretch=1)

        # Only while it is on screen. A timer re-reading a file every two
        # seconds behind a section nobody is looking at is work for nothing.
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh(force=True)
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def _refresh(self, force: bool = False) -> None:
        try:
            count = self.view.load(force=force)
        except Exception as e:
            self._status.setText(f"Could not read the log: {e}")
            return

        if not count:
            self._status.setText(
                f"Nothing in {LOG_PATH} yet. Logging to file may be off.")
        else:
            self._status.setText(
                f"Last {count} lines of {LOG_PATH}. Debug lines appear only "
                f"when debug is enabled.")


def build_logs_page(client: "Client") -> list:
    """The Logs section, as the settings page wants it: a list of widgets."""
    return [LogSection(client)]
