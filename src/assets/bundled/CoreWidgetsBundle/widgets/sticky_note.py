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

    LOOK_TITLE = "Note"
    PLACEHOLDER = "Tap to edit"

    def __init__(self, client: "Client", key: str = None, text: str = ""):
        super().__init__(client=client, key=key or self.KEY,
                         width=220, height=200, floating=True)
        self.set_content_size(220, 200, chosen=False)
        self.text = text or self.PLACEHOLDER
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

    def layout_state(self) -> dict:
        state = super().layout_state()
        state["text"] = self.text
        return state

    def apply_layout_state(self, state: dict) -> None:
        super().apply_layout_state(state)
        if isinstance(state, dict):
            self.text = str(state.get("text", self.text))

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
