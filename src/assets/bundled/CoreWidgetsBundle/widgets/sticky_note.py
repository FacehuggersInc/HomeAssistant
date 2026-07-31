from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFontMetrics

from src.ui.widget import Widget
from src.styling import make_font, SIZES

if TYPE_CHECKING:
    from src.main import Client

COLOURS = ["#f2d675", "#f29e7b", "#a8d8a0", "#9cc4f0", "#e0a8d8"]


class StickyNote(Widget):
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

    def __init__(self, client: "Client", key: str = None, text: str = ""):
        super().__init__(client=client, key=key or self.KEY,
                         width=220, height=200, floating=True)
        self.set_content_size(220, 200)
        self.text = text or "Tap to edit"
        self.colour = COLOURS[0]
        self.font_size = self.FONT_SIZES[1]
        # No WA_TranslucentBackground. That attribute is for top-level windows;
        # on a child widget it stops the background being cleared between
        # paints, so repeatedly resizing left the previous frames behind as
        # artifacts. A plain child widget draws nothing where its paintEvent
        # does not, which is the transparency this needs.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    ## EDITING

    def on_activate(self) -> None:
        from src.ui.keyboard import make_keyboard
        from PyQt6.QtWidgets import QTextEdit

        buffer = QTextEdit()
        buffer.setPlainText("" if self.text == "Tap to edit" else self.text)

        def committed():
            self.text = buffer.toPlainText().strip() or "Tap to edit"
            self.update()
            framework = self._framework()
            if framework is not None:
                framework.save_layout()

        buffer.textChanged.connect(committed)
        keyboard = make_keyboard(
            self.client, buffer, "body",
            label="Sticky Note",
            description="Written on the note. Hold the note to move, resize or rotate it.",
        )
        keyboard.show_keyboard()

    def _framework(self):
        node = self.parent()
        while node is not None and not hasattr(node, "save_layout"):
            node = node.parent()
        return node

    #Point sizes, not a scale factor. A note somebody wrote two words on wants
    #big text; a list of six wants small, and "medium" means nothing without
    #knowing the widget size.
    FONT_SIZES = (13, 17, 22, 28)

    def chrome_button(self):
        return ("mdi.palette-outline", "Look", self.open_style)

    def open_style(self) -> None:
        """Swatches and a stepper, not a list of rows to read."""
        from src.ui.dialogs_look import LookDialog

        self.client.dialog(LookDialog(
            self.client, "Note", COLOURS, self.colour,
            self.font_size, self.FONT_SIZES,
            on_colour=self.set_colour, on_size=self.set_font_size))

    def set_colour(self, colour: str) -> None:
        self.colour = str(colour)
        self.update()
        self._save()

    def set_font_size(self, size: int) -> None:
        self.font_size = int(size)
        self.update()
        self._save()

    def _save(self) -> None:
        try:
            framework = self.parent()
            if framework is not None and hasattr(framework, "save_layout"):
                framework.save_layout()
        except Exception:
            pass

    def cycle_colour(self) -> None:
        index = (COLOURS.index(self.colour) + 1) % len(COLOURS) if self.colour in COLOURS else 0
        self.colour = COLOURS[index]
        self.update()

    ## PERSISTENCE

    def layout_state(self) -> dict:
        state = super().layout_state()
        state["text"] = self.text
        state["colour"] = self.colour
        state["font_size"] = self.font_size
        return state

    def apply_layout_state(self, state: dict) -> None:
        super().apply_layout_state(state)
        if isinstance(state, dict):
            self.text = str(state.get("text", self.text))
            self.colour = str(state.get("colour", self.colour))
            try:
                self.font_size = int(state.get("font_size", self.font_size))
            except (TypeError, ValueError):
                pass

    ## PAINT

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Rotation is paint-only now: a QWidget has no transform, so a widget
        # that rotates has to draw itself rotated. This is why ROTATABLE is
        # opt-in - a widget built from child widgets cannot do it, its
        # children would keep painting square.
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
