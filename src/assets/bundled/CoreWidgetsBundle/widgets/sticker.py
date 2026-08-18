from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QMovie, QPainter, QColor, QPen

from src.ui.widget import Widget, FLOATING

if TYPE_CHECKING:
    from src.main import Client


class StickerWidget(Widget):
    """
    An image from the sticker folder, stuck on the home screen.

    `MULTIPLE`, like the sticky note: the panel entry is a template and every
    Add places another sticker with its own key and its own saved state. That
    is what lets several different stickers be on screen at once and come back
    after a restart.

    Self-painted so a still and an animation are the same widget: a QMovie
    frame and a QPixmap both end up as one drawImage call, and there is no
    child label whose scaling has to be kept in step with the widget.
    """

    KEY         = "sticker"
    NAME        = "Sticker"
    ICON        = "mdi.sticker-emoji"
    DESCRIPTION = "An image or GIF from your sticker folder."

    RESIZABLE = True
    #A picture. Dragging a corner freely squashes it, and nobody drags a
    #corner meaning to distort what is inside.
    KEEP_ASPECT = True

    #All the way to the glass.
    #
    #The framework keeps floating widgets inside the page margin, which is
    #right for a card and wrong for a sticker: half a cat peering in from the
    #corner is the point of the thing, and stopping it 24px short reads as the
    #drag having caught on something.
    EDGE_PADDING = 0
    ROTATABLE = True
    FLOATABLE = True
    REMOVABLE = True
    MULTIPLE  = True        # each Add is another sticker

    MIN_W, MIN_H = 48, 48
    #Room to be genuinely large. 640 was the binding constraint on every
    #choice above "normal": on a 2560px panel it is a quarter of the width, so
    #"huge" came out the same size as "large".
    MAX_W, MAX_H = 1600, 1600
    #Floating, not anchored. A sticker is stuck where somebody put it;
    #an anchor zone is for things that belong in a corner.
    DEFAULT_ANCHOR = FLOATING

    DEFAULT_SIDE = 180

    def __init__(self, client: "Client", sticker: str = "",
                 longest_side: int = 0, delete_after: bool = False, **kwargs):
        kwargs.pop("width", None)
        kwargs.pop("height", None)
        # How big it starts, longest edge in pixels. 0 means the class default.
        self._longest_side = int(longest_side or 0) or self.DEFAULT_SIDE
        # Whether the file goes when the sticker does. Off unless asked for -
        # a temporary sticker is about the screen, not the library.
        self.delete_after = bool(delete_after)
        super().__init__(client=client, key=kwargs.pop("key", None) or self.KEY,
                         width=self.DEFAULT_SIDE, height=self.DEFAULT_SIDE,
                         floating=True, **kwargs)

        self.sticker_name = ""
        self._pixmap: Optional[QPixmap] = None
        self._movie: Optional[QMovie] = None
        self._scaled: Optional[QPixmap] = None
        self._scaled_for = None

        if sticker:
            self.set_sticker(sticker)

    ## -- source

    def _store(self):
        try:
            if self.client.public.has("stickers"):
                return self.client.public.stickers["store"]
        except Exception:
            pass
        return None

    def set_sticker(self, name: str) -> None:
        """Point this sticker at a file in the sticker folder."""
        self._release()
        self.sticker_name = str(name or "")
        if not self.sticker_name:
            self.update()
            return

        store = self._store()
        entry = store.get(self.sticker_name) if store is not None else None
        if entry is None:
            # The file was removed while a saved layout still referenced it.
            # Kept as a name rather than cleared, so a sticker restored from a
            # folder that is temporarily unavailable is not silently forgotten.
            self.client.log("warning",
                            f"[Sticker] '{self.sticker_name}' is not in the "
                            f"sticker folder.")
            self.update()
            return

        path = str(entry.path)
        if entry.kind == "animated":
            # Parented to this widget. Without a parent the movie's lifetime
            # is only the Python reference, so it keeps running after the
            # widget's C++ object has gone and fires frameChanged into
            # something deleted - which aborts rather than raising, because it
            # happens inside a Qt signal.
            movie = QMovie(path, parent=self)
            if movie.isValid() and movie.frameCount() != 1:
                movie.setCacheMode(QMovie.CacheMode.CacheNone)
                # Guarded as well as parented. A frame already queued when the
                # widget went is still delivered, and there is nothing left to
                # repaint.
                movie.frameChanged.connect(self._on_frame)
                self._movie = movie
                # Sized from the source before the movie is scaled to it.
                #
                # This branch used to return without fitting at all, so an
                # animated sticker kept the placeholder square it was built
                # with and every size choice produced the same 180px - the
                # still-image path below was the only one that ever read
                # `longest_side`.
                self._fit_to_source()
                self._apply_movie_size()
                movie.start()
                self.update()
                return
            # A .webp whose plugin cannot animate, or a one-frame gif. Falls
            # back to a still rather than showing nothing - the build's webp
            # plugin deciding this is exactly what cannot be assumed.
            self.client.log("debug",
                            f"[Sticker] '{self.sticker_name}' is not animating; "
                            f"showing a still frame.")

        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self._pixmap = pixmap
            self._fit_to_source()
        self.update()

    def _release(self) -> None:
        self._stop_movie()
        self._pixmap = None
        self._scaled = None
        self._scaled_for = None

    ## -- sizing

    def edge_padding(self):
        return self.EDGE_PADDING

    def content_inset(self):
        """
        How much of this widget is transparent, on each side.

        A sticker is a rectangle containing a shape. Measuring the shape means
        the drag limit applies to what can be seen, so an image with a wide
        empty margin can be pushed that much further before it stops.

        Cached against the current frame's size: reading an alpha channel is
        cheap once and not worth doing on every mouse move.
        """
        pixmap = (self._movie.currentPixmap() if self._movie is not None
                  else self._pixmap)
        if pixmap is None or pixmap.isNull():
            return (0, 0, 0, 0)

        key = (pixmap.cacheKey(), self.width(), self.height())
        if getattr(self, "_inset_key", None) == key:
            return self._inset

        inset = (0, 0, 0, 0)
        try:
            image = pixmap.toImage()
            if image.hasAlphaChannel():
                # The opaque bounding box, in the pixmap's own pixels, scaled
                # to how big the widget is drawing it.
                box = self._opaque_box(image)
                if box is not None:
                    left, top, right, bottom = box
                    sx = self.width() / max(1, image.width())
                    sy = self.height() / max(1, image.height())
                    inset = (int(left * sx), int(top * sy),
                             int(right * sx), int(bottom * sy))
        except Exception:
            inset = (0, 0, 0, 0)

        self._inset_key = key
        self._inset = inset
        return inset

    @staticmethod
    def _opaque_box(image):
        """
        (left, top, right, bottom) transparent margins, or None.

        Sampled rather than exhaustive. A 512x512 image is a quarter of a
        million pixels and this only needs to know where the shape roughly
        starts; a stride of four is sixteen times less work and cannot be off
        by more than three pixels, which nobody can see at the edge of a
        screen.
        """
        step = 4
        threshold = 8            # count anything all but invisible as empty
        width, height = image.width(), image.height()
        if width < step or height < step:
            return None

        first_x, last_x = width, -1
        first_y, last_y = height, -1
        for y in range(0, height, step):
            for x in range(0, width, step):
                if image.pixelColor(x, y).alpha() > threshold:
                    if x < first_x: first_x = x
                    if x > last_x:  last_x = x
                    if y < first_y: first_y = y
                    if y > last_y:  last_y = y

        if last_x < 0:
            # Entirely transparent. Reporting the whole thing as margin would
            # let it be dragged completely off, so it is treated as solid.
            return None
        return (first_x, first_y,
                max(0, width - 1 - last_x), max(0, height - 1 - last_y))

    def _source_size(self) -> Optional[QSize]:
        if self._movie is not None:
            size = self._movie.currentPixmap().size()
            if not (size.isValid() and size.width() > 0):
                # Nothing has been decoded yet. currentPixmap() is empty until
                # the movie has a frame, so asking for one is what makes the
                # source size readable - and this runs before start().
                try:
                    self._movie.jumpToFrame(0)
                    size = self._movie.currentPixmap().size()
                except Exception:
                    size = QSize()
            if size.isValid() and size.width() > 0:
                return size
        if self._pixmap is not None and not self._pixmap.isNull():
            return self._pixmap.size()
        return None

    def _fit_to_source(self) -> None:
        """
        Start at the sticker's own aspect ratio, longest side DEFAULT_SIDE.

        A square default would letterbox every sticker that is not square,
        and the person would then have to resize each one by hand.
        """
        size = self._source_size()
        if size is None or size.width() <= 0 or size.height() <= 0:
            return
        longest = max(size.width(), size.height())
        target = max(self.MIN_W, min(self.MAX_W,
                                     getattr(self, "_longest_side", 0)
                                     or self.DEFAULT_SIDE))
        scale = target / float(longest)
        width = max(self.MIN_W, min(self.MAX_W, int(size.width() * scale)))
        height = max(self.MIN_H, min(self.MAX_H, int(size.height() * scale)))
        self.set_content_size(width, height)
        bounds_w, bounds_h = self.rotated_bounds(width, height)
        self.setFixedSize(bounds_w, bounds_h)

    def _on_frame(self, _index: int = 0) -> None:
        try:
            self.update()
        except RuntimeError:
            # The widget has gone. Stop the movie so it is not asked again.
            movie, self._movie = self._movie, None
            if movie is not None:
                try:
                    movie.stop()
                except RuntimeError:
                    pass

    def closeEvent(self, event) -> None:
        self._stop_movie()
        super().closeEvent(event)

    def _stop_movie(self) -> None:
        movie, self._movie = self._movie, None
        if movie is None:
            return
        try:
            movie.frameChanged.disconnect(self._on_frame)
        except (TypeError, RuntimeError):
            pass
        try:
            movie.stop()
        except RuntimeError:
            pass

    def _apply_movie_size(self) -> None:
        if self._movie is None:
            return
        content_w, content_h = self.content_size()
        self._movie.setScaledSize(QSize(int(content_w), int(content_h)))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._scaled = None
        self._apply_movie_size()

    ## -- painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.apply_rotation(painter)

        content_w, content_h = self.content_size()

        if self._movie is not None:
            frame = self._movie.currentPixmap()
            if not frame.isNull():
                painter.drawPixmap(0, 0, frame)
                painter.end()
                return

        if self._pixmap is not None and not self._pixmap.isNull():
            key = (int(content_w), int(content_h))
            if self._scaled is None or self._scaled_for != key:
                # Scaled once per size, not per paint. A sticker is repainted
                # whenever anything above it composites, and rescaling a large
                # source each time is the expensive part.
                self._scaled = self._pixmap.scaled(
                    int(content_w), int(content_h),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                self._scaled_for = key
            offset_x = int((content_w - self._scaled.width()) / 2)
            offset_y = int((content_h - self._scaled.height()) / 2)
            painter.drawPixmap(offset_x, offset_y, self._scaled)
            painter.end()
            return

        self._paint_missing(painter, content_w, content_h)
        painter.end()

    def _paint_missing(self, painter: QPainter, width: float,
                       height: float) -> None:
        """A sticker with nothing behind it says so rather than being blank."""
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(1, 1, int(width) - 2, int(height) - 2, 10, 10)
        painter.setPen(QPen(QColor(255, 255, 255, 170)))
        painter.drawText(0, 0, int(width), int(height),
                         int(Qt.AlignmentFlag.AlignCenter),
                         self.sticker_name or "No sticker")

    ## -- choosing

    @classmethod
    def choose_before_add(cls, client, then) -> None:
        """
        Asked before the panel adds one: which sticker?

        Called by WidgetFramework._add_copy_from_panel, which defers building
        the widget until `then(**kwargs)` is called - so cancelling the dialog
        leaves nothing behind rather than placing an empty frame.
        """
        from src.ui.grid_dialog import ItemGridDialog, GridItem

        store = None
        try:
            if client.public.has("stickers"):
                store = client.public.stickers["store"]
        except Exception:
            store = None

        if store is None:
            client.alert("Stickers",
                         "The sticker library is not available.")
            return

        items = []
        for sticker in store.all_stickers(refresh=True):
            items.append(GridItem(
                key=sticker.name,
                label=sticker.label,
                preview=str(sticker.path),
                animated=(sticker.kind == "animated"),
                badge="" if sticker.kind == "still" else sticker.kind,
                icon=("mdi.play-circle-outline" if sticker.kind == "video"
                      else "mdi.image-outline"),
                data=sticker,
            ))

        empty = (f"No stickers yet. Upload some at "
                 f"/public/sticker_add, or drop files into "
                 f"{store.directory}.")

        # Keyed off the Sticker on `data`, so the dialog stays generic.
        sorts = [
            ("az",      "A\u2013Z",   lambda i: i.label.lower(),
             "mdi.sort-alphabetical-ascending"),
            ("za",      "Z\u2013A",   lambda i: i.label.lower(),
             "mdi.sort-alphabetical-descending"),
            ("newest",  "Newest",     lambda i: -i.data.modified(),
             "mdi.clock-outline"),
            ("biggest", "Biggest",    lambda i: -i.data.size(),
             "mdi.arrow-expand"),
            ("kind",    "Type",       lambda i: (i.data.kind, i.label.lower()),
             "mdi.shape-outline"),
        ]

        def delete(item) -> bool:
            removed = bool(store.remove(item.key))
            if removed:
                client.log("info", f"[Stickers] Deleted '{item.key}'.")
            return removed

        client.dialog(ItemGridDialog(
            client, title="Choose a sticker", items=items,
            on_chosen=lambda item: then(sticker=item.key),
            on_delete=delete,
            sorts=sorts,
            choose_text="Place it",
            delete_text="Delete file",
            search_hint="Search stickers",
            empty_text=empty,
        ))

    ## -- persistence

    def display_name(self) -> str:
        from pathlib import Path as _Path
        if self.sticker_name:
            return _Path(self.sticker_name).stem
        return self.NAME

    def layout_state(self) -> dict:
        state = super().layout_state()
        # Which image this is, so a permanent sticker comes back as itself
        # rather than as an empty frame.
        state["sticker"] = self.sticker_name
        return state

    def apply_layout_state(self, state: dict) -> None:
        super().apply_layout_state(state)
        name = str(state.get("sticker", "") or "")
        if name and name != self.sticker_name:
            self.set_sticker(name)
            # The saved size wins over the source's own ratio: the person may
            # have resized it.
            width = int(state.get("w", self.width()))
            height = int(state.get("h", self.height()))
            if width > 0 and height > 0:
                self.set_content_size(
                    max(self.MIN_W, min(self.MAX_W, width)),
                    max(self.MIN_H, min(self.MAX_H, height)))
                bounds_w, bounds_h = self.rotated_bounds()
                self.setFixedSize(bounds_w, bounds_h)
                self._apply_movie_size()

    ## -- lifecycle

    def delete_source(self) -> bool:
        """
        Remove the file this sticker came from.

        Called when a temporary sticker that asked for it goes away - by its
        timeout, or by being dismissed early. Deliberately **not** in
        teardown(): that also runs when the page is rebuilt on navigation,
        which would empty the library every time somebody opened Settings.
        """
        if not self.delete_after or not self.sticker_name:
            return False
        store = self._store()
        if store is None:
            return False
        removed = bool(store.remove(self.sticker_name))
        if removed:
            self.client.log("info",
                            f"[Sticker] Removed '{self.sticker_name}' along "
                            f"with its temporary widget.")
        return removed

    def on_dismissed(self) -> bool:
        """
        The delete handle was pressed.

        Returns False, so the framework still takes the widget away - unlike a
        timer, a sticker has no service doing that for it. The file only goes
        if this sticker was placed as temporary and asked for it.
        """
        self.delete_source()
        return False

    def teardown(self) -> None:
        self._release()
        self.stop_tick()
