from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush

from src.styling import make_font
from .paper import PaperWidget, COLOURS

if TYPE_CHECKING:
    from src.main import Client

#Re-exported: the endpoint that puts a note up offers this palette, and
#importing it from here keeps that page reading the note's own set.
COLOURS = COLOURS


class StickyNote(PaperWidget):
    # Self-painted on purpose: everything is drawn in paintEvent rather than
    # composed from child widgets, so rotating it stays legible and there are
    # no child hit targets to fall out of alignment. Editing happens in the
    # keyboard dialog, which handles multi-line text properly.

    KEY = "sticky-note"
    NAME = "Sticky Note"
    DESCRIPTION = "A note you can move, resize, rotate and edit."
    ICON = "sticky_note_2"

    RESIZABLE = True
    ROTATABLE = True
    FLOATABLE = True
    REMOVABLE = True
    MULTIPLE = True           # stays in the panel; Add makes another note

    MIN_W, MIN_H = 140, 120
    MAX_W, MAX_H = 640, 560
    DEFAULT_ANCHOR = "top-left:0"

    #What a new note is, at 20pt. A note at 30pt is scaled up from these
    #rather than being the same box with bigger writing in it - see
    #PaperWidget.fit_to_text.
    BASE_W, BASE_H = 220, 200
    BASE_FONT = 20

    #What paintEvent takes out of the box before it draws any text: the
    #body inset, and then the text inset inside that. Kept here rather than
    #buried in the paint, because the fit below has to agree with it exactly
    #- a box measured against different padding than it is drawn with is a
    #box the text does not fit in.
    PAD_X = 8 + 24
    PAD_Y = 8 + 20

    #The shortest a note may be, as a fraction of how wide it is. Two words
    #need almost no height, and a box that is 220 by 120 stops reading as
    #paper and starts reading as a label.
    #
    #Modest on purpose. At 0.82 every note came out near-square with the text
    #in the top third and a lot of empty paper under it - the floor was
    #deciding the height rather than the words were. This is enough to keep a
    #two-word note from being a strip and no more.
    SQUARISH = 0.62

    #And the tallest, the same way. Past this the box is a column of text
    #rather than a note, and widening it costs nothing until MAX_W.
    TALLEST = 1.35

    LOOK_TITLE = "Note"
    PLACEHOLDER = "Tap to edit"

    def __init__(self, client: "Client", key: str = None, text: str = ""):
        super().__init__(client=client, key=key or self.KEY,
                         width=self.BASE_W, height=self.BASE_H, floating=True)
        self.set_content_size(self.BASE_W, self.BASE_H, chosen=False)
        self.text = text or self.PLACEHOLDER
        # A note made at a size other than the default starts at a box that
        # suits it, rather than being resized a moment after it appears.
        self.fit_to_text()
        # No WA_TranslucentBackground. That attribute is for top-level windows;
        # on a child widget it stops the background being cleared between
        # paints, so repeatedly resizing leaves the previous frames behind as
        # artifacts. A plain child widget draws nothing where its paintEvent
        # does not, which is the transparency this needs.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    ## EDITING

    def on_activate(self) -> None:
        from src.ui.keyboard import make_keyboard
        from PyQt6.QtWidgets import QTextEdit

        buffer = QTextEdit()
        buffer.setPlainText("" if self.text == self.PLACEHOLDER else self.text)

        def committed():
            self.text = buffer.toPlainText().strip() or self.PLACEHOLDER
            self.update()
            self._save()

        buffer.textChanged.connect(committed)
        keyboard = make_keyboard(
            self.client, buffer, "body",
            label="Sticky Note",
            description="Written on the note. Hold the note to move, resize "
                        "or rotate it.",
        )
        keyboard.show_keyboard()

    ## PERSISTENCE

    def fit_to_text(self) -> None:
        """
        A box that holds the text, measured rather than scaled.

        The base class scales from a default size, which is right for a widget
        whose contents are not known - and wrong here, because a note IS its
        text. Scaling gave a note with four lines at 24pt the same box as one
        with four words, and the text was cut off at the bottom.

        Measured with `QFontMetrics`, which needs no parent, no layout and no
        paint - only a QApplication. The earlier reasoning that this had to
        run before anything could be measured was wrong: what cannot be
        measured yet is the WIDGET, and this measures the text.

        Widened before it is given up on. A long note in a narrow box is very
        tall; the same note a third wider is a shape that fits on a wall.
        """
        if self.has_chosen_size():
            return

        from PyQt6.QtCore import QRect
        from PyQt6.QtGui import QFontMetrics

        metrics = QFontMetrics(make_font(self.font_size))
        flags = int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
                    | Qt.TextFlag.TextWordWrap)
        text = self.text or self.PLACEHOLDER

        # The width starts from the TEXT, not from the font.
        #
        # Scaling the default width by the font size answered "how big are
        # the letters" and not "how much is written" - so one long line at
        # 30pt wrapped into a tall narrow column when the box could simply
        # have been wide enough to hold it. The longest line is what a note
        # is as wide as.
        scale = max(0.5, float(self.font_size) / float(self.BASE_FONT or 20))
        floor_w = max(self.MIN_W, min(self.MAX_W, int(self.BASE_W * scale)))

        # The longest line is the CEILING, not the starting point. Starting
        # there made a forty-character note six hundred pixels wide - one line
        # of text and a banner rather than a note. Past the longest line the
        # extra width is empty paper, so there is no reason to go wider.
        lines = text.splitlines() or [text]
        longest = max((metrics.horizontalAdvance(line) for line in lines),
                      default=0)
        ceiling = max(floor_w, min(self.MAX_W, longest + self.PAD_X))



        # Start narrow and widen only while the block is a column. A note
        # twice as tall as it is wide is a strip of paper; the same words a
        # little wider are a note.
        #
        # Nothing is done about a line that cannot wrap. Qt breaks at
        # punctuation, so a pasted link wraps at its slashes on its own -
        # widening for one only produced the emptiest note on the page. A
        # single unbroken two-hundred-character word would overflow, and is
        # not a note.
        width = floor_w
        height = self.MIN_H
        for _attempt in range(10):
            room = max(40, width - self.PAD_X)
            wrapped = metrics.boundingRect(
                QRect(0, 0, room, 10_000), flags, text)
            height = wrapped.height() + self.PAD_Y
            # Wide enough for the longest line as well as short enough to
            # read. A pasted link is one word and cannot wrap, so it does not
            # make the block tall - it runs off the edge, and only widening
            # helps.
            if height <= width * self.TALLEST or width >= ceiling:
                break
            width = min(ceiling, int(width * 1.2))

        width = max(self.MIN_W, min(self.MAX_W, width))
        height = max(height, int(width * self.SQUARISH))
        height = max(self.MIN_H, min(self.MAX_H, height))

        if (width, height) == tuple(self.content_size()):
            return
        self.set_content_size(width, height, chosen=False)
        try:
            self.setFixedSize(*self.rotated_bounds())
            self.updateGeometry()
        except Exception:
            # Not laid out yet, which is the usual case when this runs from
            # apply_layout_state. The size is recorded either way.
            pass
        self.update()

    def sizeHint(self):
        """
        The box `fit_to_text` worked out.

        Not optional, and the reason a note placed from a phone came out a
        small square whatever its font. `place()` calls `_fit_to_content`,
        which for a resizable widget with no CHOSEN size throws the measured
        size away and takes `sizeHint()` instead - and a self-painted widget
        with no layout reports -1, so the note fell back to its minimum.

        The checklist never hit this because it has always had one. This is
        the same fix, saying the same thing: what the content measured.
        """
        from PyQt6.QtCore import QSize

        width, height = self.content_size()
        return QSize(max(self.MIN_W, min(self.MAX_W, int(width) or self.BASE_W)),
                     max(self.MIN_H, min(self.MAX_H, int(height) or self.BASE_H)))

    def minimumSizeHint(self):
        """A floor, not a fit - the smallest a note is allowed to be."""
        from PyQt6.QtCore import QSize

        return QSize(self.MIN_W, self.MIN_H)

    def on_look_changed(self) -> None:
        # The text size decides how much fits, so the box follows it - unless
        # somebody has dragged one, which fit_to_text checks.
        self.fit_to_text()
        super().on_look_changed()

    def layout_state(self) -> dict:
        state = super().layout_state()
        state["text"] = self.text
        return state

    def apply_layout_state(self, state: dict) -> None:
        super().apply_layout_state(state)
        if isinstance(state, dict):
            self.text = str(state.get("text", self.text))
        # After the text, and after the base has restored any chosen size.
        #
        # The base already called fit_to_text once, before the text was read -
        # so it measured the placeholder and gave every restored note the same
        # box. This is the one that counts, which is why _refit() sits at the
        # end of the checklist's version for exactly the same reason.
        self.fit_to_text()

    ## PAINT

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Rotation is paint-only: a QWidget has no transform, so a widget that
        # rotates has to draw itself rotated. This is why ROTATABLE is opt-in -
        # a widget built from child widgets cannot do it, its children would
        # keep painting square.
        # apply_rotation moves the origin to the content area, so everything
        # below draws at content size and lands centred and rotated.
        self.apply_rotation(painter)
        content_w, content_h = self.content_size()

        body = QRectF(2, 2, content_w - 8, content_h - 8)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 60)))
        painter.drawRoundedRect(body.adjusted(3, 4, 3, 4), 4, 4)

        painter.setBrush(QBrush(QColor(self.colour)))
        painter.drawRoundedRect(body, 4, 4)

        # A folded corner, so it reads as paper rather than a coloured box.
        fold = 22
        painter.setBrush(QBrush(QColor(0, 0, 0, 40)))
        painter.drawPolygon(
            body.bottomRight().toPoint(),
            (body.bottomRight() - QRectF(0, 0, fold, 0).bottomRight()).toPoint(),
            (body.bottomRight() - QRectF(0, 0, 0, fold).bottomRight()).toPoint(),
        )

        painter.setPen(QPen(QColor("#2a2a2a")))
        painter.setFont(make_font(self.font_size))
        text_rect = body.adjusted(12, 10, -12, -10).toRect()
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
                | Qt.TextFlag.TextWordWrap),
            self.text,
        )
        painter.end()
