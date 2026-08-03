"""
Drawing something and sticking it on the wall.

The panel already has a sticker system - a folder, a library, placement, and
persistence across a restart - and no way to MAKE one without a phone and an
upload. This is that: a canvas, a few tools, and a Save that writes a
transparent PNG into the same folder and puts it on the home screen.

Strokes are kept as objects rather than painted into a buffer. Undo is then
free, the drawing survives a resize without going soft, and an eraser is just
another stroke with a different composition mode - which means it undoes like
everything else rather than being a hole nothing can take back.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
)
from PyQt6.QtCore import Qt, QPointF, QRectF, QSize
from PyQt6.QtGui import (
    QPainter, QPainterPath, QPen, QColor, QImage, QBrush, QPixmap, QIcon,
)

from src.styling import set_style, make_font, SIZES
from src.ui.overlays import BaseDialog

if TYPE_CHECKING:
    from src.main import Client


#What can be drawn with. Black is deliberately absent: a transparent sticker
#lands on whatever wallpaper is underneath, and black on a dark panel is
#invisible. Somebody who wants it can pick the dark grey.
COLOURS = [
    "#f0f0f4", "#e0483f", "#e0a03f", "#4f9d6a", "#4f9de0", "#8a5fc0",
    "#e07fb0", "#3a3a42",
]

#Brush widths, in canvas pixels.
WIDTHS = [4, 10, 20, 36]

#Nothing smaller than this is worth keeping. A stray tap on the way to a
#button should not become a sticker.
MIN_INK = 8


class _Stroke:
    """One continuous press-drag-release."""

    __slots__ = ("path", "colour", "width", "erase")

    def __init__(self, point: QPointF, colour: str, width: int, erase: bool):
        # QPointF, not QPoint. `QPainterPath` has no QPoint overload, and
        # rounding to whole pixels would cost the smoothness a drawing needs
        # anyway - `event.position()` is already a float.
        self.path = QPainterPath(QPointF(point))
        self.colour = colour
        self.width = width
        self.erase = erase

    def extend(self, point: QPointF) -> None:
        self.path.lineTo(QPointF(point))

    def pen(self) -> QPen:
        pen = QPen(QColor(self.colour), self.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    def bounds(self) -> QRectF:
        """
        Where this stroke actually put ink.

        A path's bounding box is its CENTRE LINE, and the pen paints half its
        width either side of that - so half is the right margin. The comment
        here said half and the arithmetic used the whole width, which put a
        36 pixel border around a 36 pixel brush and nearly doubled a small
        drawing.

        One extra pixel for the antialiased edge, which reaches just past
        where the geometry says it should.
        """
        margin = self.width / 2.0 + 1.0
        return self.path.boundingRect().adjusted(
            -margin, -margin, margin, margin)


class _Canvas(QWidget):
    """Where the drawing happens. Owns the strokes and nothing else."""

    def __init__(self):
        super().__init__()
        self.strokes: list = []
        self.colour = COLOURS[0]
        self.width = WIDTHS[1]
        self.erasing = False
        self._current: _Stroke | None = None
        #The composed ink, and how many finished strokes are in it.
        self._cache: QImage | None = None
        self._cached_upto = 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    ## -- drawing

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._current = _Stroke(event.position(), self.colour,
                                self.width, self.erasing)
        self.strokes.append(self._current)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._current is None:
            return
        self._current.extend(event.position())
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        self._current = None

    def undo(self) -> None:
        if self.strokes:
            self.strokes.pop()
            self._cached_upto = 10 ** 9   # forces a rebuild
            self.update()

    def clear(self) -> None:
        self.strokes = []
        self._current = None
        self._cache = None
        self._cached_upto = 0
        self.update()

    ## -- painting

    def paintEvent(self, event) -> None:
        """
        The board, then the ink composed on top of it.

        The ink is drawn into an offscreen ARGB image rather than straight
        onto this widget, because the eraser uses `CompositionMode_Clear` and
        a widget has no alpha channel to clear TO - so on the widget it
        cleared to black and the eraser looked like a black pen.

        Offscreen it does what it says, and the preview is then rendered by
        exactly the same code as the saved file. A preview that is drawn
        differently from the thing being saved is a preview of nothing.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # A muted board so what is being drawn can be seen. NOT saved.
        painter.fillRect(self.rect(), QColor(255, 255, 255, 16))
        painter.drawImage(0, 0, self._ink_layer())
        painter.end()

    def _ink_layer(self) -> QImage:
        """
        Every stroke, composed on transparency at the canvas size.

        Completed strokes are cached and only the one under the finger is
        redrawn, so the cost of a move event does not grow with the drawing.
        """
        if self._cache is None or self._cache.size() != self.size():
            self._cache = QImage(self.size(),
                                 QImage.Format.Format_ARGB32_Premultiplied)
            self._cache.fill(Qt.GlobalColor.transparent)
            self._cached_upto = 0

        finished = self.strokes[:-1] if self._current is not None else self.strokes
        if self._cached_upto > len(finished):
            # Undone. The cache holds strokes that no longer exist and there
            # is no un-drawing them, so it starts again.
            self._cache.fill(Qt.GlobalColor.transparent)
            self._cached_upto = 0

        if self._cached_upto < len(finished):
            painter = QPainter(self._cache)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint_strokes(painter, finished[self._cached_upto:])
            painter.end()
            self._cached_upto = len(finished)

        if self._current is None:
            return self._cache

        # The live one on a copy, so the cache is not polluted by a stroke
        # that is still growing.
        live = QImage(self._cache)
        painter = QPainter(live)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_strokes(painter, [self._current])
        painter.end()
        return live

    def _paint_strokes(self, painter: QPainter, strokes: list = None) -> None:
        for stroke in (self.strokes if strokes is None else strokes):
            if stroke.erase:
                # The eraser takes ink away rather than painting over it,
                # which is the whole point of a transparent sticker: painting
                # the background colour would leave an opaque smear on it.
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_Clear)
                painter.setPen(QPen(QColor(0, 0, 0, 255), stroke.width,
                                    Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.RoundCap,
                                    Qt.PenJoinStyle.RoundJoin))
            else:
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_SourceOver)
                painter.setPen(stroke.pen())
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(stroke.path)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver)

    ## -- saving

    def ink_bounds(self) -> QRectF:
        """Everything drawn, as one rectangle. Empty if nothing was."""
        drawn = [s for s in self.strokes if not s.erase]
        if not drawn:
            return QRectF()
        bounds = drawn[0].bounds()
        for stroke in drawn[1:]:
            bounds = bounds.united(stroke.bounds())
        return bounds.intersected(QRectF(self.rect()))

    def image(self) -> QImage | None:
        """
        The drawing as a transparent image, cropped to the ink.

        Cropped because the canvas is most of a screen and the drawing is
        usually a corner of it. Saved whole, every sticker would be a
        screen-sized mostly-empty PNG that is awkward to place and slow to
        paint.
        """
        bounds = self.ink_bounds()
        if bounds.isEmpty() or bounds.width() < MIN_INK or bounds.height() < MIN_INK:
            return None

        rect = bounds.toAlignedRect()
        image = QImage(rect.size(), QImage.Format.Format_ARGB32_Premultiplied)
        # Transparent, not the board colour. The board is a drawing aid.
        image.fill(Qt.GlobalColor.transparent)

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(-rect.topLeft())
        self._paint_strokes(painter)
        painter.end()
        return image


class WhiteboardDialog(BaseDialog):
    """A near-fullscreen canvas that saves what is drawn as a sticker."""

    #Almost the whole screen. `BaseDialog` sizes from WIDTH and MAX_HEIGHT and
    #clamps both to what there is room for - it has no WIDTH_RATIO, which is
    #a `_WideDialog` idea, and setting one here did nothing at all.
    #
    #So the fraction is asked for instead: a number larger than any panel,
    #clamped down by `_fits_across` and `_fits_down` to the screen minus their
    #margin. That is as close to fullscreen as this framework goes, and it
    #cannot overflow whatever it is running on.
    WIDTH = 100_000
    MAX_HEIGHT = 100_000

    SWATCH = 46
    TOOL = 46

    def __init__(self, client: "Client", on_saved=None):
        super().__init__(client, "Whiteboard", "")
        self.on_saved = on_saved

        self.canvas = _Canvas()
        set_style(self.canvas, "common", "transparent")
        self.content.addWidget(self.canvas, stretch=1)

        tools = QHBoxLayout()
        tools.setSpacing(8)

        self.swatches = []
        for colour in COLOURS:
            button = QPushButton()
            button.setFixedSize(self.SWATCH, self.SWATCH)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            # `colour=colour`, or every swatch picks the last one.
            button.clicked.connect(
                lambda _checked=False, colour=colour: self._pick_colour(colour))
            tools.addWidget(button)
            self.swatches.append((colour, button))

        tools.addSpacing(12)

        self.width_buttons = []
        for width in WIDTHS:
            button = QPushButton()
            # A drawn dot rather than a bullet character. A glyph scaled by
            # font size is at the mercy of the font: the bullet in this one is
            # small and sits high in its box, so the four sizes came out as
            # four short dashes at slightly different heights rather than as
            # four brush tips.
            button.setIcon(QIcon(self._width_icon(width)))
            button.setIconSize(QSize(self.TOOL, self.TOOL))
            button.setFixedSize(self.TOOL, self.TOOL)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, width=width: self._pick_width(width))
            tools.addWidget(button)
            self.width_buttons.append((width, button))

        tools.addSpacing(12)

        self.eraser = QPushButton("Eraser")
        self.eraser.setFont(make_font(SIZES.S1, bold=True))
        self.eraser.setFixedHeight(self.TOOL)
        self.eraser.setCursor(Qt.CursorShape.PointingHandCursor)
        self.eraser.clicked.connect(self._toggle_eraser)
        tools.addWidget(self.eraser)

        undo = QPushButton("Undo")
        undo.setFont(make_font(SIZES.S1, bold=True))
        undo.setFixedHeight(self.TOOL)
        undo.setCursor(Qt.CursorShape.PointingHandCursor)
        undo.clicked.connect(self._undo)
        set_style(undo, "overlays", "dialog-button-secondary")
        tools.addWidget(undo)

        clear = QPushButton("Clear")
        clear.setFont(make_font(SIZES.S1, bold=True))
        clear.setFixedHeight(self.TOOL)
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.clicked.connect(self._clear)
        set_style(clear, "overlays", "dialog-button-secondary")
        tools.addWidget(clear)

        tools.addStretch()

        self.hint = QLabel("Saved to your stickers, and put on the home screen.")
        self.hint.setFont(make_font(SIZES.S1))
        set_style(self.hint, "common", "text-muted")
        tools.addWidget(self.hint)

        holder = QWidget()
        set_style(holder, "common", "transparent")
        holder.setLayout(tools)
        self.content.addWidget(holder)

        self.add_button("Cancel", self.close, "secondary")
        self.add_button("Save", self._save, "primary")

        self._pick_colour(COLOURS[0])
        self._pick_width(WIDTHS[1])
        self._paint_eraser()
        self.expand_content()
        # The canvas takes everything left over, and the dialog asks for all
        # the height it is allowed - `maximumHeight()` has already been fitted
        # to the screen, so this cannot overflow.
        self.setMinimumHeight(self.maximumHeight())

    ## -- tools

    def _pick_colour(self, colour: str) -> None:
        self.canvas.colour = colour
        # Choosing a colour means drawing with it. Leaving the eraser on
        # after a colour was picked is a tool that ignores what was asked.
        self.canvas.erasing = False
        self._paint_eraser()
        for value, button in self.swatches:
            border = ("3px solid #ffffff" if value == colour
                      else "1px solid rgba(255,255,255,60)")
            button.setStyleSheet(
                f"background:{value};border:{border};border-radius:10px;")

    #The dot for the largest brush, and the smallest one still worth looking
    #at. The widths themselves span 4 to 36 - a ninth - and at that ratio the
    #two smallest dots are a pixel apart and read as the same button.
    DOT_MAX = 30
    DOT_MIN = 8

    def _width_icon(self, width: int) -> QPixmap:
        """
        A filled circle the size of the brush, near enough.

        By the square root rather than in proportion. A brush is a round tip
        and what the eye compares between two of them is their AREA, so the
        square root is closer to how much bigger one looks than the other - and
        it opens the small end out, which is where they were indistinguishable.
        """
        biggest = max(WIDTHS) or 1
        share = (float(width) / biggest) ** 0.5
        diameter = max(self.DOT_MIN, min(self.DOT_MAX,
                                         int(round(self.DOT_MAX * share))))

        ratio = self.devicePixelRatioF() or 1.0
        pixmap = QPixmap(int(self.TOOL * ratio), int(self.TOOL * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor("#f2f2f2")))
        painter.setPen(Qt.PenStyle.NoPen)
        offset = (self.TOOL - diameter) / 2.0
        painter.drawEllipse(QRectF(offset, offset, diameter, diameter))
        painter.end()
        return pixmap

    def _pick_width(self, width: int) -> None:
        self.canvas.width = width
        for value, button in self.width_buttons:
            set_style(button, "overlays",
                      "dialog-button-primary" if value == width
                      else "dialog-button-secondary")

    def _toggle_eraser(self) -> None:
        self.canvas.erasing = not self.canvas.erasing
        self._paint_eraser()

    def _paint_eraser(self) -> None:
        set_style(self.eraser, "overlays",
                  "dialog-button-primary" if self.canvas.erasing
                  else "dialog-button-secondary")

    def _undo(self) -> None:
        self.canvas.undo()

    def _clear(self) -> None:
        self.canvas.clear()

    ## -- saving

    def _save(self) -> None:
        image = self.canvas.image()
        if image is None:
            self.hint.setText("There is nothing drawn yet.")
            return

        data = self._png_bytes(image)
        if not data:
            self.hint.setText("Could not turn that into an image.")
            return

        if not self.client.public.has("stickers"):
            self.hint.setText("The sticker library is not available.")
            return

        name = f"whiteboard-{time.strftime('%Y%m%d-%H%M%S')}.png"
        sticker, reason = self.client.public.stickers["add"](name, data)
        if sticker is None:
            self.hint.setText(reason or "Could not save it.")
            return

        # How big it was drawn, so it can be put up that size. The canvas is
        # nearly the whole screen, so a drawing's pixels on it are very close
        # to its pixels on the home page - which makes "it appeared the size
        # I drew it" the least surprising answer.
        longest = max(image.width(), image.height())

        self.close()
        if callable(self.on_saved):
            self.on_saved(sticker, longest)

    @staticmethod
    def _png_bytes(image: QImage) -> bytes:
        """
        PNG, in memory. The sticker store takes bytes, not a path.

        The QByteArray is held in a NAME. `QBuffer(QByteArray())` hands the
        buffer a pointer to a temporary that Python then collects, and the
        buffer writes into freed memory - which is a segfault rather than an
        exception, so the app disappears with nothing in the log.
        """
        from PyQt6.QtCore import QBuffer, QByteArray

        store = QByteArray()
        buffer = QBuffer(store)
        if not buffer.open(QBuffer.OpenModeFlag.WriteOnly):
            return b""
        # PNG rather than anything else: it is the only common format here
        # that keeps an alpha channel, and the alpha is the point.
        written = image.save(buffer, "PNG")
        buffer.close()
        if not written:
            return b""
        return bytes(store)
