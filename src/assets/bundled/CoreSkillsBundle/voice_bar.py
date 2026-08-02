from __future__ import annotations

import math
import random
import time
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, pyqtProperty
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QFontMetrics

from src.styling import make_font, SIZES

if TYPE_CHECKING:
    from src.main import Client

ACCENT = {
    "CANCELLED": "#8a8a8a",
    "LISTENING": "#da091f",
    "THINKING":  "#2d6cc0",
    "ACTING":    "#1f7a4d",
    "HEARD":     "#cfcfcf",
}

VISIBLE_STATES = ("LISTENING", "THINKING", "ACTING")

BARS = 5
BAR_WIDTH = 4
BAR_GAP = 3
BAR_MAX = 20
BAR_MIN = 3


class VoiceBar(QWidget):
    # Floating pill above the bottom edge: level meter on the left, status or
    # transcript on the right. Replaces the full-width edge bar, which was easy
    # to miss and had nowhere to put text.

    HEIGHT = 52
    MAX_WIDTH = 560
    MIN_WIDTH = 240
    BOTTOM_MARGIN = 28
    # A fixed hold is wrong either way: too short to read a long sentence, too
    # long for "yes". Scales with reading time, floored by the user setting.
    #How long the transcribing stage stays up on its own.
    #
    #Generous, because a big model on a slow panel is the case this exists
    #for: it must not fade halfway through the thing it is reporting. The
    #next stage replaces it as soon as the text arrives, so being too long
    #costs nothing and being too short is the bug.
    TRANSCRIBING_MS = 20000

    HOLD_BASE_MS = 2000
    HOLD_PER_CHAR_MS = 70
    HOLD_MAX_MS = 20000
    MIN_VISIBLE_MS = 900   # never flash and vanish on a fast state bounce

    def __init__(self, client: "Client", parent: QWidget = None):
        super().__init__(parent)
        self.client = client

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # NOT WA_TranslucentBackground - that is a top-level window attribute,
        # and on a child it stops the background being cleared between paints.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFixedHeight(self.HEIGHT)

        self._status = "DORMANT"
        self._level = 0.0
        self._opacity = 0.0
        self._text = ""
        self._accent = QColor(ACCENT["LISTENING"])
        self._phase = 0.0
        self._heights = [float(BAR_MIN)] * BARS
        self._margin = 0
        self._shown_at = 0.0

        self._font = make_font(SIZES.S2)
        self._metrics = QFontMetrics(self._font)

        # The third argument is the PARENT. Without it the animation belongs
        # to nothing, outlives the widget it animates, and fires `finished`
        # into an object that has gone - which inside a Qt signal aborts the
        # process rather than raising.
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(260)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._fade = QPropertyAnimation(self, b"bar_opacity", self)
        self._fade.setDuration(240)
        self._fade.setEasingCurve(QEasingCurve.Type.InOutQuad)

        #Whether the waiting message is what is currently showing.
        self._transcribing = False
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._release)

        # Steady frame rate for the meter; on_update is a background thread and
        # ticks at whatever rate the client happens to run at.
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self.hide()

    ## -- animated property

    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, value: float) -> None:
        self._opacity = max(0.0, min(1.0, float(value)))
        if self._opacity <= 0.01:
            if self.isVisible():
                self.hide()
        elif not self.isVisible():
            self.show()
        self.update()

    bar_opacity = pyqtProperty(float, _get_opacity, _set_opacity)

    ## -- state

    def apply_state(self, status: str, level: float) -> None:
        changed = status != self._status
        self._status = status
        self._level = max(0.0, min(1.0, float(level or 0.0)))

        if not changed:
            return

        if status == "LISTENING":
            self._accent = QColor(ACCENT["LISTENING"])
            # Stopped, because the live status is now what keeps it up and
            # _release() defers to that. It restarts when the status leaves a
            # visible state - see the else branch.
            self._hold.stop()
            self._set_text("Listening…")
            self._reveal()
        elif status == "THINKING":
            self._accent = QColor(ACCENT["THINKING"])
            self._hold.stop()
            self._set_text("Thinking…")
            self._reveal()
        elif status == "ACTING":
            self._accent = QColor(ACCENT["ACTING"])
            self._reveal()
        elif not self._hold.isActive():
            # Nothing to say and no transcript being held: drop away.
            self._dismiss()
        # A hold that is still running keeps whatever it is showing until it
        # expires, and _release() then dismisses because the status is no
        # longer visible.

    def hold_ms(self, text: str) -> int:
        """How long to keep a transcript up, from the user floor and its length."""
        try:
            floor = float(self.client.setting("assistant.feedback.voice_bar_hold.value", 6)) * 1000
        except Exception:
            floor = 6000
        estimate = self.HOLD_BASE_MS + len(text) * self.HOLD_PER_CHAR_MS
        return int(min(self.HOLD_MAX_MS, max(floor, estimate)))

    def show_heard(self, text: str) -> None:
        """A finished transcript. Held long enough to read, then dismissed."""
        text = " ".join(str(text or "").split())
        if not text:
            return
        self._accent = QColor(ACCENT["HEARD"])
        self._set_text(f"\u201c{text}\u201d")
        self._reveal()
        self._hold.start(self.hold_ms(text))

    def show_cancelled(self) -> None:
        """Acknowledge backing out, then drop away shortly after."""
        self._accent = QColor(ACCENT["CANCELLED"])
        self._set_text("Never mind")
        self._reveal()
        self._hold.start(1600)

    def show_transcribing(self) -> None:
        """
        Audio captured, the model running. The stage that covers the wait.

        Held on a generous timer rather than left to the status. A big model
        takes seconds, and this is the one stage where nothing further will
        arrive until it finishes - so it has to stay up on its own.
        """
        self._accent = QColor(ACCENT.get("THINKING", ACCENT["LISTENING"]))
        self._transcribing = True
        self._set_text("Working out what you said\u2026")
        self._reveal()
        self._hold.start(self.TRANSCRIBING_MS)

    def done_transcribing(self) -> None:
        """
        Take the waiting message down, if that is still what is showing.

        Guarded on the text, not just called. By the time this arrives the
        phrase may already have replaced it, and clearing then would remove
        the one thing worth reading a moment after it appeared.
        """
        if not self._transcribing:
            return
        self._transcribing = False
        self._hold.stop()
        self._release()

    def show_understood(self, phrase: str) -> None:
        """
        The middle of the three: it heard a phrase and is working out what it
        means.

        Between "listening" and the confirmation. Without this the bar goes
        straight from listening to the answer, so everything between - the
        text arriving, the skills being searched - happens behind a pill that
        still says listening, and a search that finds nothing looks like a
        microphone that heard nothing.
        """
        self._transcribing = False
        self._accent = QColor(ACCENT.get("THINKING", ACCENT["LISTENING"]))
        text = str(phrase or "").strip()
        self._set_text(f"\u201c{text}\u201d" if text else "Thinking\u2026")
        self._reveal()
        # No hold. This is a stage, not a message - the next stage replaces
        # it, and if nothing does, the status going idle takes it down.
        self._hold.stop()

    def show_matched(self, what: str) -> None:
        """
        Say that something was understood, and what.

        This fires when a SKILL has matched, which is the moment the panel
        starts acting - not the moment it woke. It used to read
        "Alexa - listening…", which was wrong twice over: it had finished
        listening, and the word it named was the skill's own rather than the
        one the person says.

        Held on a timer like every other message. It used to stop the timer
        and start none, leaving the pill up until a STATUS CHANGE took it
        down - and a request that arrives already answered never produces one.
        """
        self._transcribing = False
        self._accent = QColor(ACCENT.get("ACTING", ACCENT["LISTENING"]))
        text = str(what or "").strip()
        self._set_text(text if text else "Got it\u2026")
        self._reveal()
        self._hold.start(self.hold_ms(text))

    #The old name, for anything still calling it.
    show_woke = show_matched

    def check_still_wanted(self) -> None:
        """
        Take the pill down if nothing is keeping it up.

        Called on every poll, including the ones where the status did not
        change - which is exactly when a stuck pill happens. `apply_state`
        returns early on an unchanged status, so a message shown between two
        polls has nothing to dismiss it; this is that.
        """
        if self._status in VISIBLE_STATES:
            return
        if self._hold.isActive():
            return
        # Visible by opacity, not by isVisible(): the widget is faded rather
        # than hidden, so it stays "visible" to Qt at zero opacity and this
        # would ask to dismiss something already gone on every poll.
        if self._opacity > 0.01:
            self._dismiss()

    def _set_text(self, text: str) -> None:
        self._text = text
        self._resize_to_text()
        self.update()

    def _release(self) -> None:
        # The assistant may still be busy when the timer fires; in that case
        # the live status keeps the pill up and this is a no-op.
        if self._status in VISIBLE_STATES:
            return
        self._dismiss()

    ## -- reveal / dismiss

    def _reveal(self) -> None:
        if self.parentWidget() is None:
            return
        rising = not self.isVisible()
        self._reposition(animate=True, rising=rising)
        self._fade.stop()
        self._fade.setStartValue(self._opacity)
        self._fade.setEndValue(1.0)
        self.show()
        self.raise_()
        self._shown_at = time.monotonic()
        self._fade.start()

    def _dismiss(self) -> None:
        # Guarantee a readable minimum on screen. Status can flip
        # LISTENING -> LIVE within a few hundred ms when a wake word is
        # rejected, which otherwise shows a pill for one frame.
        remaining = self.MIN_VISIBLE_MS - (time.monotonic() - self._shown_at) * 1000
        if self._opacity > 0.01 and remaining > 0:
            QTimer.singleShot(int(remaining), self._dismiss)
            return

        self._slide.stop()
        self._fade.stop()
        self._fade.setStartValue(self._opacity)
        self._fade.setEndValue(0.0)
        self._fade.start()

    ## -- geometry

    def _target_pos(self) -> QPoint:
        host = self.parentWidget()
        if host is None:
            return self.pos()
        x = (host.width() - self.width()) // 2
        y = host.height() - self.height() - self.BOTTOM_MARGIN - self._margin
        return QPoint(max(0, x), max(0, y))

    def _resize_to_text(self) -> None:
        host = self.parentWidget()
        meter = BARS * BAR_WIDTH + (BARS - 1) * BAR_GAP
        wanted = 20 + meter + 14 + self._metrics.horizontalAdvance(self._text) + 22
        limit = self.MAX_WIDTH
        if host is not None:
            limit = min(self.MAX_WIDTH, max(self.MIN_WIDTH, host.width() - 96))
        self.setFixedWidth(int(max(self.MIN_WIDTH, min(limit, wanted))))
        if self.isVisible():
            self.move(self._target_pos())

    def _reposition(self, animate: bool, rising: bool = False) -> None:
        target = self._target_pos()
        if not animate:
            self.move(target)
            return
        if rising:
            # Rise into place rather than blinking in, so it reads as arriving.
            self.move(QPoint(target.x(), target.y() + 24))
        self._slide.stop()
        self._slide.setStartValue(self.pos())
        self._slide.setEndValue(target)
        self._slide.start()

    def anchor(self, host: QWidget, margin: int = 0) -> None:
        self._margin = margin
        self._resize_to_text()
        self.move(self._target_pos())

    ## -- animation

    def _tick(self) -> None:
        if self._opacity <= 0.01:
            return

        self._phase += 0.22

        for i in range(BARS):
            if self._status == "LISTENING":
                # Centre bars swing widest, plus jitter so steady speech does
                # not look like a frozen gauge.
                weight = 1.0 - abs(i - (BARS - 1) / 2) / BARS
                target = BAR_MIN + (BAR_MAX - BAR_MIN) * self._level * weight
                target *= 0.75 + random.random() * 0.5
            elif self._status == "THINKING":
                target = BAR_MIN + (BAR_MAX - BAR_MIN) * 0.5 * (
                    1 + math.sin(self._phase + i * 0.7)) * 0.5
            else:
                target = BAR_MIN + 2

            target = max(BAR_MIN, min(BAR_MAX, target))
            self._heights[i] += (target - self._heights[i]) * 0.35

        self.update()

    ## -- paint

    def paintEvent(self, event) -> None:
        if self._opacity <= 0.01:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        inset = 4
        radius = (self.height() - inset * 2) / 2

        # Shadow drawn by hand: a QGraphicsEffect would conflict with painting
        # our own alpha, and only one effect can be set on a widget at a time.
        painter.setPen(Qt.PenStyle.NoPen)
        for step in range(3, 0, -1):
            painter.setBrush(QBrush(QColor(0, 0, 0, int(18 * self._opacity))))
            painter.drawRoundedRect(
                inset - step, inset - step + 2,
                self.width() - (inset - step) * 2,
                self.height() - (inset - step) * 2,
                radius + step, radius + step,
            )

        body = QColor("#1c1c1c")
        body.setAlphaF(0.94 * self._opacity)
        painter.setBrush(QBrush(body))
        edge = QColor(self._accent)
        edge.setAlphaF(0.55 * self._opacity)
        painter.setPen(QPen(edge, 1.4))
        painter.drawRoundedRect(inset, inset,
                                self.width() - inset * 2,
                                self.height() - inset * 2,
                                radius, radius)

        painter.setPen(Qt.PenStyle.NoPen)
        accent = QColor(self._accent)
        accent.setAlphaF(self._opacity)
        painter.setBrush(QBrush(accent))

        x = inset + 16
        centre = self.height() / 2
        for height in self._heights:
            painter.drawRoundedRect(
                int(x), int(centre - height / 2),
                BAR_WIDTH, int(height), BAR_WIDTH / 2, BAR_WIDTH / 2,
            )
            x += BAR_WIDTH + BAR_GAP

        if self._text:
            text_x = int(x + 12)
            available = self.width() - text_x - inset - 14
            if available > 0:
                painter.setFont(self._font)
                colour = QColor("#f2f2f2")
                colour.setAlphaF(self._opacity)
                painter.setPen(QPen(colour))
                painter.drawText(
                    text_x, 0, available, self.height(),
                    int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                    self._metrics.elidedText(self._text, Qt.TextElideMode.ElideRight, available),
                )

        painter.end()
