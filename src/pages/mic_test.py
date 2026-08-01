"""
A page for hearing what the microphone hears.

Nothing else. No wake word, no skills, no assistant - a session is started by
hand, every phrase transcribed lands in a list, and it stops when told. The
point is to separate "the microphone is not working" from "the wake word is
not matching" from "the skill is not firing", which is impossible while all
three are in the way of each other.

It is not always listening. A page that opened a microphone the moment
somebody scrolled past it would be a page nobody trusts.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QSizePolicy, QScroller,
)

from src.styling import (
    set_style, make_font, SIZES, style_scrollbar, get_style_sheet)
from src.ui.controls.buttons import ActionButton

if TYPE_CHECKING:
    from src.main import Client


#How many phrases to keep. Enough to see a pattern, few enough to read.
KEEP = 60


class MicTestPage(QWidget):
    """Start listening, see what came back, stop."""

    def __init__(self, client: "Client"):
        super().__init__()
        self.client = client
        self.listening = False
        self.started_at = 0.0
        self.heard = 0
        #Set while a session is running, so the STT hands phrases here
        #instead of routing them to skills.
        self._hooked = False

        set_style(self, "common", "transparent")
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)

        column.addWidget(self._blurb())
        column.addLayout(self._controls())
        column.addWidget(self._log(), stretch=1)

        # The clock on the session, and the only thing that runs while this
        # page is up and idle.
        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._update_status)

    ## -- the page

    def _blurb(self) -> QLabel:
        label = QLabel(
            "Say something while a session is running and it appears below, "
            "exactly as the transcriber heard it. Nothing is routed to a "
            "skill and no wake word is needed \u2014 this is only about "
            "whether the microphone and the model are working.")
        label.setFont(make_font(SIZES.S1))
        label.setWordWrap(True)
        set_style(label, "common", "text-muted")
        return label

    def _controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        self.start_button = ActionButton(
            "mdi.microphone", "Start a session", self.start, kind="primary")
        row.addWidget(self.start_button)

        self.stop_button = ActionButton(
            "mdi.stop", "Stop", self.stop, kind="secondary")
        self.stop_button.setEnabled(False)
        row.addWidget(self.stop_button)

        self.clear_button = ActionButton(
            "mdi.notification-clear-all", "Clear", self.clear,
            kind="secondary")
        row.addWidget(self.clear_button)

        self.status = QLabel("Not listening.")
        self.status.setFont(make_font(SIZES.S1))
        self.status.setWordWrap(True)
        set_style(self.status, "common", "text-muted")
        row.addWidget(self.status, stretch=1)
        return row

    def _log(self) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")
        self.lines = QVBoxLayout(host)
        self.lines.setContentsMargins(10, 8, 10, 8)
        self.lines.setSpacing(4)
        self.lines.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.empty = QLabel("Nothing heard yet.")
        self.empty.setFont(make_font(SIZES.S1))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.empty, "common", "text-muted")
        self.lines.addWidget(self.empty)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Expanding)
        scroll.setStyleSheet(
            "QScrollArea { background: rgba(0,0,0,60);"
            " border: 1px solid rgba(255,255,255,20); border-radius: 10px; }")
        scroll.setWidget(host)
        style_scrollbar(scroll)
        try:
            QScroller.grabGesture(
                scroll.viewport(),
                QScroller.ScrollerGestureType.LeftMouseButtonGesture)
        except Exception:
            pass
        self.scroll = scroll
        return scroll

    ## -- the session

    def start(self) -> None:
        """Open the microphone, and put everything it hears in the list."""
        if self.listening:
            return
        stt = getattr(self.client, "STT", None)
        if stt is None:
            self._say("The assistant is not running, so there is no "
                      "microphone to listen with.")
            return

        try:
            stt.add_listener(self._heard)
        except Exception as e:
            self._say(f"Could not listen: {e}")
            return

        self._hooked = True
        self.listening = True
        self.started_at = time.time()
        self.heard = 0
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._tick.start()
        self._update_status()
        self.client.log("info", "[MicTest] Session started.")

    def stop(self) -> None:
        """Stop, and leave what was heard on screen."""
        self._release()
        self.listening = False
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._tick.stop()
        self._update_status()
        self.client.log("info", f"[MicTest] Session stopped, {self.heard} "
                                f"phrase(s) heard.")

    def _release(self) -> None:
        """Unhook, whatever left the session running."""
        if not self._hooked:
            return
        self._hooked = False
        try:
            self.client.STT.remove_listener(self._heard)
        except Exception:
            pass

    def clear(self) -> None:
        # The empty label is taken out with everything else and put back, so
        # it ends up at the top rather than under whatever was left. Removing
        # it from the layout does not hide it, which is why the visibility is
        # set after it is re-added rather than before.
        while self.lines.count():
            item = self.lines.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None and widget is not self.empty:
                widget.setParent(None)
                widget.deleteLater()
        self.lines.addWidget(self.empty)
        self.empty.setParent(self.lines.parentWidget())
        self.empty.setVisible(True)
        self.empty.show()
        self.heard = 0
        self._update_status()

    ## -- what came back

    def _heard(self, phrase: str) -> None:
        """One transcript. Called from the STT's thread."""
        self.client.call_on_ui(lambda: self._add(phrase))

    def _add(self, phrase: str) -> None:
        text = str(phrase or "").strip()
        if not text:
            return
        self.heard += 1
        self.empty.setVisible(False)

        row = QLabel(f"{time.strftime('%H:%M:%S')}   {text}")
        row.setFont(make_font(SIZES.S1, family="monospace"))
        row.setWordWrap(True)
        row.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        row.setStyleSheet("color:#e8ecf4;background:transparent;")
        self.lines.addWidget(row)

        # Oldest first out. A test that runs for an hour should not turn into
        # a thousand labels nobody scrolls back through.
        while self.lines.count() > KEEP + 1:
            item = self.lines.takeAt(1)
            widget = item.widget() if item is not None else None
            if widget is not None and widget is not self.empty:
                widget.setParent(None)
                widget.deleteLater()

        self._update_status()
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _say(self, message: str) -> None:
        self.status.setText(message)

    def _update_status(self) -> None:
        if not self.listening:
            if self.heard:
                self._say(f"Stopped. {self.heard} phrase(s) heard.")
            else:
                self._say("Not listening.")
            return
        seconds = int(time.time() - self.started_at)
        mode = "hardware" if self._hardware() else "software"
        self._say(f"Listening \u2014 {seconds}s, {self.heard} phrase(s). "
                  f"Microphone processing: {mode}.")

    def _hardware(self) -> bool:
        try:
            return str(self.client.setting(
                "assistant.mic_processing.value", "software")) == "hardware"
        except Exception:
            return False

    ## -- leaving

    def hideEvent(self, event) -> None:
        # The microphone is not held open by a page nobody is looking at.
        # Somebody who navigated away is not still testing.
        super().hideEvent(event)
        if self.listening:
            self.stop()


def build_mic_test_page(client: "Client") -> QWidget:
    """The settings category body."""
    return MicTestPage(client)
