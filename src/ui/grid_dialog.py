from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QScrollArea, QSizePolicy, QScroller,
)
from PyQt6.QtCore import Qt, QSize, QTimer, QPoint
from PyQt6.QtGui import QPixmap, QMovie, QPainter, QColor, QPen, QFontMetrics

from src.ui.overlays import BaseDialog
from src.ui.icons import icon as resolve_icon
from src.styling import make_font, SIZES, set_style, get_style_sheet, style_scrollbar

if TYPE_CHECKING:
    from src.main import Client


class _SearchField(QLineEdit):
    """
    Read-only, and opens the on-screen keyboard when tapped.

    Its own mouse handlers rather than a hit test on the dialog: the field
    sits inside the dialog's content layout, so its geometry() is in that
    parent's coordinates and comparing it against a dialog-space click never
    matched.

    Deliberately **not** wired to focus. keyboard.md asks fields to bind
    focusInEvent as well as the press, because there focus arrives from a tap.
    Here it does not: a QLineEdit is the first focusable thing in the dialog,
    so Qt hands it focus on show, and opening the keyboard from that meant the
    picker greeted you with a keyboard over a grid you had not seen yet.
    NoFocus, and the tap is the only way in.
    """

    def __init__(self, on_tap):
        super().__init__()
        self._on_tap = on_tap
        self.setReadOnly(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # NoFocus, and no focusInEvent hook. A QLineEdit is the first
        # focusable thing in the dialog, so Qt gave it focus on show - and
        # opening the keyboard from focus meant the picker greeted you with a
        # keyboard over an empty grid every single time.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def mousePressEvent(self, event) -> None:
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        event.accept()
        if callable(self._on_tap):
            self._on_tap()


class GridItem:
    """
    One thing in an ItemGridDialog.

    Deliberately not tied to files. `preview` is a path when there is one, but
    a source with no local file - a search API returning URLs - passes
    `pixmap` instead, or neither and gets the placeholder.
    """

    #What a tile draws with, when nothing else is given.
    #
    #A caller passing `kind` gets a sensible preview without having to know
    #which icon this dialog would have picked - and a set of mixed kinds reads
    #as a set rather than as whatever each caller happened to choose.
    KINDS = {
        "image":    "mdi.image-outline",
        "sound":    "mdi.music-note",
        "page":     "mdi.file-document-outline",
        "link":     "mdi.link-variant",
        "place":    "mdi.map-marker-outline",
        "person":   "mdi.account-outline",
        "event":    "mdi.calendar-blank-outline",
        "device":   "mdi.cellphone-link",
        "plugin":   "mdi.puzzle-outline",
        "file":     "mdi.file-outline",
    }

    def __init__(self, key: str, label: str, preview: str = "",
                 subtitle: str = "", badge: str = "", icon: str = "",
                 pixmap: QPixmap = None, data=None, animated: bool = False,
                 kind: str = ""):
        self.key = str(key)
        self.label = str(label)
        self.preview = str(preview or "")
        self.subtitle = str(subtitle or "")
        self.badge = str(badge or "")
        self.icon = str(icon or "")
        self.pixmap = pixmap
        self.data = data
        self.animated = bool(animated)
        # What this IS, which decides the fallback icon and can be searched on.
        self.kind = str(kind or "").strip().lower()
        if not self.icon and self.kind in self.KINDS:
            self.icon = self.KINDS[self.kind]

    def haystack(self) -> str:
        # The kind is searchable too, so "sound" finds every sound in a mixed
        # list without the caller having to put the word in each label.
        return (f"{self.label} {self.subtitle} {self.badge} "
                f"{self.kind} {self.key}").lower()

    @classmethod
    def kinds(cls) -> list:
        """Every kind this dialog draws an icon for."""
        return sorted(cls.KINDS)


class _Tile(QFrame):
    """One cell: a preview with its name under it."""

    #how far a finger may travel and still count as a tap rather than a scroll
    DRAG_SLOP = 12

    def __init__(self, item: GridItem, size: int, on_pick: Callable,
                 lines: int = 1):
        super().__init__()
        self.item = item
        self.on_pick = on_pick
        self._movie: Optional[QMovie] = None
        self.selected = False
        self._press: Optional[QPoint] = None

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(size + 14)
        set_style(self, "overlays", "grid-tile")

        column = QVBoxLayout(self)
        column.setContentsMargins(7, 7, 7, 6)
        column.setSpacing(5)

        self.preview = QLabel()
        self.preview.setFixedSize(size, size)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setScaledContents(False)
        set_style(self.preview, "common", "transparent")
        column.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignHCenter)

        # One line by default, two when the names carry the meaning.
        #
        # A picture identifies a sticker, so eliding its filename costs
        # nothing; a list of documents or people is the opposite, and a name
        # cut mid-word is a name somebody cannot find. `label_lines` is how a
        # caller says which of the two it has.
        name = QLabel()
        name.setFont(make_font(SIZES.S1, bold=True))
        name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        name.setToolTip(item.label)
        set_style(name, "common", "text-strong")

        # Measured, not assumed. This was a flat 18px per line while the font
        # it uses is taller than that, so every name lost its descenders and a
        # two-line one lost most of its second row.
        metrics = QFontMetrics(name.font())
        line_height = metrics.height()

        if lines > 1:
            name.setWordWrap(True)
            name.setFixedHeight(line_height * lines)
            name.setText(item.label)
        else:
            name.setFixedHeight(line_height)
            name.setText(metrics.elidedText(
                item.label, Qt.TextElideMode.ElideRight, size + 6))
        column.addWidget(name)

        if item.badge:
            badge = QLabel(item.badge.upper())
            badge.setFont(make_font(SIZES.S1, bold=True))
            badge.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            set_style(badge, "common", "text-muted")
            column.addWidget(badge)

        self._load_preview(size)

    def _load_preview(self, size: int) -> None:
        item = self.item

        if item.pixmap is not None and not item.pixmap.isNull():
            self.preview.setPixmap(self._fit(item.pixmap, size))
            return

        if item.preview:
            if item.animated:
                movie = QMovie(item.preview)
                if movie.isValid():
                    # Scaled by the movie, not the label: a QLabel scaling a
                    # QMovie re-scales every frame and is visibly slower.
                    movie.setScaledSize(QSize(size, size))
                    movie.setCacheMode(QMovie.CacheMode.CacheNone)
                    self._movie = movie
                    self.preview.setMovie(movie)
                    movie.start()
                    return
            pixmap = QPixmap(item.preview)
            if not pixmap.isNull():
                self.preview.setPixmap(self._fit(pixmap, size))
                return

        self.preview.setPixmap(self._placeholder(item, size))

    @staticmethod
    def _fit(pixmap: QPixmap, size: int) -> QPixmap:
        return pixmap.scaled(size, size,
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)

    @staticmethod
    def _placeholder(item: GridItem, size: int) -> QPixmap:
        """
        For anything with no preview to show - a video with no poster frame,
        or a file Qt has no plugin for. A tile drawn as nothing looks broken;
        one drawn as an icon looks deliberate.
        """
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255, 45), 1, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(1, 1, size - 2, size - 2, 10, 10)
        try:
            glyph = resolve_icon(item.icon or "mdi.file-outline",
                                 color="rgba(255,255,255,150)")
            side = int(size * 0.4)
            painter.drawPixmap((size - side) // 2, (size - side) // 2,
                               glyph.pixmap(side, side))
        except Exception:
            pass
        painter.end()
        return pixmap

    def set_selected(self, state: bool) -> None:
        if state == self.selected:
            return
        self.selected = state
        set_style(self, "overlays",
                  "grid-tile-selected" if state else "grid-tile")

    def stop(self) -> None:
        if self._movie is not None:
            self._movie.stop()
            self._movie = None

    def mousePressEvent(self, event) -> None:
        self._press = event.globalPosition().toPoint()
        # Ignored, so the gesture can still reach the scroller behind this.
        event.ignore()

    def mouseReleaseEvent(self, event) -> None:
        start, self._press = self._press, None
        if start is not None:
            moved = event.globalPosition().toPoint() - start
            if max(abs(moved.x()), abs(moved.y())) > self.DRAG_SLOP:
                # A scroll, not a tap. Selecting here is what made flicking
                # through the grid pick whatever was under your finger.
                event.ignore()
                return
        self.on_pick(self)


class ItemGridDialog(BaseDialog):
    """
    A searchable grid of things to pick one of.

    Written for the sticker library, but deliberately generic: anything with
    more items than a list can show and a name worth searching - a folder, an
    icon set, a search API's results - is the same dialog. Pass `items` for a
    fixed set, or `on_search` for a source that answers queries itself.

    Picking is two steps, a tap to select and a button to confirm, rather than
    a tap that acts immediately. On a touch screen a grid of small tiles is
    exactly where a mis-tap happens, and this one places something on the
    person's home screen.
    """

    WIDTH_RATIO  = 0.78
    HEIGHT_RATIO = 0.80
    MIN_WIDTH    = 640
    #Smaller than it was, because the point of this dialog is finding
    #something: at 176 a 2560-wide panel showed twelve tiles, and twelve is a
    #list somebody scrolls rather than a grid they scan.
    TILE         = 124
    #ms after the last keystroke before a search runs
    SEARCH_DEBOUNCE = 180

    #(key, label, keyfunc) - keyfunc takes a GridItem
    #(key, label, keyfunc) or (key, label, keyfunc, icon)
    DEFAULT_SORTS = [
        ("az", "A\u2013Z", lambda i: i.label.lower(), "mdi.sort-alphabetical-ascending"),
        ("za", "Z\u2013A", lambda i: i.label.lower(), "mdi.sort-alphabetical-descending"),
    ]

    def __init__(self, client: "Client", title: str = "Choose",
                 body: str = "", items: list = None,
                 on_chosen: Callable = None, on_search: Callable = None,
                 choose_text: str = "Use this", empty_text: str = "Nothing here yet.",
                 search_hint: str = "Search", extra_button: tuple = None,
                 sorts: list = None, on_delete: Callable = None,
                 delete_text: str = "Delete", label_lines: int = 1):
        host = getattr(client, "OVERLAYS", None)
        width = self.MIN_WIDTH
        try:
            if host is not None and host.width() > 0:
                width = max(self.MIN_WIDTH, int(host.width() * self.WIDTH_RATIO))
        except Exception:
            pass
        super().__init__(client, title, body, width=width)

        self.on_chosen = on_chosen
        self.on_search = on_search
        self.on_delete = on_delete
        # How many lines a name gets. One for a grid of pictures, two when the
        # name is what somebody is reading.
        self._label_lines = max(1, min(3, int(label_lines or 1)))
        self._sorts = list(sorts) if sorts else list(self.DEFAULT_SORTS)
        self._sort_key = self._sorts[0][0] if self._sorts else ""
        self._all_items = list(items or [])
        self._tiles: list = []
        self._chosen: Optional[GridItem] = None
        self.empty_text = empty_text

        try:
            if host is not None and host.height() > 0:
                self.MAX_HEIGHT = max(420, int(host.height() * self.HEIGHT_RATIO))
                self.setMinimumHeight(self.MAX_HEIGHT)
        except Exception:
            pass

        # Search
        row = QHBoxLayout()
        row.setSpacing(8)
        self.search = _SearchField(self._open_keyboard)
        self.search.setPlaceholderText(search_hint)
        self.search.setFont(make_font(SIZES.S2))
        self.search.setFixedHeight(46)
        set_style(self.search, "settings", "body-field")
        row.addWidget(self.search, stretch=1)

        self.clear_button = QPushButton("Clear")
        self.clear_button.setFixedHeight(46)
        self.clear_button.setMinimumWidth(96)
        self.clear_button.setFont(make_font(SIZES.S2, bold=True))
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(self.clear_button, "settings", "plugin-action-copy")
        self.clear_button.clicked.connect(self._clear_search)
        row.addWidget(self.clear_button)

        holder = QWidget()
        set_style(holder, "common", "transparent")
        holder.setLayout(row)
        self.content.addWidget(holder)

        # Sorting. Buttons rather than a dropdown: a dropdown on a touch panel
        # is two taps and a list to aim at, and there are rarely more than four.
        self._sort_buttons: dict = {}
        if len(self._sorts) > 1:
            sort_row = QHBoxLayout()
            sort_row.setSpacing(6)
            caption = QLabel("Sort")
            caption.setFont(make_font(SIZES.S1))
            set_style(caption, "common", "text-muted")
            sort_row.addWidget(caption)
            for entry in self._sorts:
                key, label, _fn = entry[0], entry[1], entry[2]
                glyph = entry[3] if len(entry) > 3 else ""
                # Smaller and quieter than the search field above them.
                #
                # These are how somebody arranges what they are looking at, not
                # what they came to do - and at 46px in bold they competed with
                # the items for attention.
                button = QPushButton(label)
                button.setFixedHeight(36)
                button.setFont(make_font(SIZES.S1, bold=True))
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                if glyph:
                    button.setIcon(resolve_icon(glyph, color="#9a9aa6"))
                    button.setIconSize(QSize(15, 15))
                # Sized to its own text plus the padding the stylesheet adds,
                # rather than a guessed minimum - "Biggest" and "A-Z" are very
                # different widths and a fixed one clipped the longer.
                metrics = QFontMetrics(button.font())
                button.setMinimumWidth(
                    metrics.horizontalAdvance(label) + (34 if glyph else 0) + 40)
                button.clicked.connect(
                    lambda _=False, k=key: self._set_sort(k))
                self._sort_buttons[key] = button
                sort_row.addWidget(button)
            sort_row.addStretch()
            sort_holder = QWidget()
            set_style(sort_holder, "common", "transparent")
            sort_holder.setLayout(sort_row)
            self.content.addWidget(sort_holder)
            self._paint_sort_buttons()

        # Grid
        self._grid_host = QWidget()
        set_style(self._grid_host, "common", "transparent")
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 6, 0, 6)
        self._grid.setSpacing(8)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop |
                                Qt.AlignmentFlag.AlignLeft)

        self._scroll = QScrollArea()
        style_scrollbar(self._scroll)
        # Given its height before anything is in it.
        #
        # The dialog was sized to its contents, and the contents arrive after
        # it is shown - previews load, the grid lays out, and the whole box
        # stretched open in front of somebody who had already pressed Add. A
        # scroll area with a floor fills the dialog immediately and the items
        # appear inside it.
        self._scroll.setMinimumHeight(320)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Expanding)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._grid_host)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Expanding)
        # get_style() matches a class against a selector base, so a rule
        # written as `.thin-scroll QScrollBar:vertical` never matched and
        # this kept Qt's default bar. The sheet is applied whole instead.
        # The viewport is a separate widget from the scroll area and fills
        # itself by default - which is the white block behind the tiles. The
        # same two lines every other scrolling surface in this app uses.
        self._scroll.viewport().setAutoFillBackground(False)
        set_style(self._scroll.viewport(), "common", "transparent")
        QScroller.grabGesture(self._scroll.viewport(),
                              QScroller.ScrollerGestureType.LeftMouseButtonGesture)
        self.content.addWidget(self._scroll, stretch=1)

        self.status = QLabel("")
        self.status.setFont(make_font(SIZES.S1))
        self.status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        set_style(self.status, "common", "text-muted")
        self.content.addWidget(self.status)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.SEARCH_DEBOUNCE)
        self._debounce.timeout.connect(self._apply_search)

        self.add_button("Cancel", self.close, "secondary")
        if extra_button:
            label, handler = extra_button
            self.add_button(label, handler, "secondary")
        self._delete_button = None
        if callable(self.on_delete):
            self._delete_button = self.add_button(
                delete_text, self._confirm_delete, "destructive")
            self.set_button_state(self._delete_button, False, "destructive")
        self._confirm = self.add_button(choose_text, self._confirm_choice,
                                        "primary")
        self.set_button_state(self._confirm, False, "primary")

        # Hands the spare height to the content rather than to BaseDialog's
        # trailing stretch. Without it the scroll area collapses to nothing and
        # the dialog shows as an empty frosted card - which is exactly what an
        # empty library looks like, so it hides the real problem too.
        self.expand_content()

        self.rebuild(self._all_items)

    ## -- search

    def _open_keyboard(self) -> None:
        try:
            from src.ui.keyboard import make_keyboard
            keyboard = make_keyboard(self.client, self.search, "string",
                                     label="Search",
                                     description="Type to narrow the list.")
            keyboard.on_done = lambda _text=None: self._search_changed()
            keyboard.show_keyboard()
        except Exception as e:
            self.client.log("warning", f"[ItemGrid] Could not open keyboard: {e}")

    def _search_changed(self) -> None:
        self._debounce.start()

    def _clear_search(self) -> None:
        self.search.setText("")
        self._apply_search()

    def _apply_search(self) -> None:
        text = self.search.text().strip()
        if callable(self.on_search):
            try:
                self.rebuild(list(self.on_search(text) or []))
                return
            except Exception as e:
                self.client.log("warning", f"[ItemGrid] Search failed: {e}")
                self.rebuild([])
                return

        if not text:
            self.rebuild(self._all_items)
            return
        words = [w for w in text.lower().split() if w]
        self.rebuild([i for i in self._all_items
                      if all(w in i.haystack() for w in words)])

    ## -- sorting

    def _set_sort(self, key: str) -> None:
        if key == self._sort_key:
            return
        self._sort_key = key
        self._paint_sort_buttons()
        self._apply_search()

    def _paint_sort_buttons(self) -> None:
        for key, button in self._sort_buttons.items():
            set_style(button, "settings",
                      "sort-button-active" if key == self._sort_key
                      else "sort-button")

    def _sorted(self, items: list) -> list:
        entry = next((s for s in self._sorts if s[0] == self._sort_key), None)
        if entry is None:
            return list(items)
        keyfunc = entry[2]
        try:
            ordered = sorted(items, key=keyfunc)
        except Exception as e:
            self.client.log("warning", f"[ItemGrid] Could not sort by "
                                       f"{self._sort_key!r}: {e}")
            return list(items)
        # A trailing "za"/"desc" reverses, so a caller declares one keyfunc
        # per ordering rather than one per direction.
        if self._sort_key.endswith("za") or self._sort_key.endswith("_desc"):
            ordered.reverse()
        return ordered

    ## -- grid

    def _columns(self) -> int:
        usable = max(self.TILE + 20, self.width() - 80)
        return max(1, usable // (self.TILE + 30))

    def rebuild(self, items: list) -> None:
        items = self._sorted(items)
        for tile in self._tiles:
            tile.stop()
            tile.setParent(None)
            tile.deleteLater()
        self._tiles = []

        self._chosen = None
        if hasattr(self, "_confirm"):
            self.set_button_state(self._confirm, False, "primary")
        if hasattr(self, "_delete_button"):
            self.set_button_state(self._delete_button, False, "destructive")

        columns = self._columns()
        for index, item in enumerate(items):
            tile = _Tile(item, self.TILE, self._pick,
                         lines=self._label_lines)
            self._grid.addWidget(tile, index // columns, index % columns)
            self._tiles.append(tile)

        if not items:
            self.status.setText(self.empty_text)
        else:
            self.status.setText(f"{len(items)} item" + ("s" if len(items) != 1 else ""))

    def _pick(self, tile: _Tile) -> None:
        for other in self._tiles:
            other.set_selected(other is tile)
        self._chosen = tile.item
        self.set_button_state(self._confirm, True, "primary")
        self.set_button_state(self._delete_button, True, "destructive")
        self.status.setText(tile.item.label)

    def _confirm_delete(self) -> None:
        """
        Asked first. This removes a file, and the grid is exactly the sort of
        surface where a mis-tap lands on the wrong tile.
        """
        chosen = self._chosen
        if chosen is None or not callable(self.on_delete):
            return
        self.client.confirm(
            "Delete", f"Delete '{chosen.label}'? This cannot be undone.",
            on_confirm=lambda: self._do_delete(chosen),
            confirm_text="Delete", cancel_text="Keep", destructive=True)

    def _do_delete(self, item: GridItem) -> None:
        try:
            removed = self.on_delete(item)
        except Exception as e:
            self.client.log("warning", f"[ItemGrid] Delete failed: {e}",
                            include_traceback=True)
            removed = False
        if removed is False:
            self.status.setText(f"Could not delete {item.label}.")
            return
        self._all_items = [i for i in self._all_items if i.key != item.key]
        self._apply_search()
        self.status.setText(f"Deleted {item.label}.")

    def _confirm_choice(self) -> None:
        chosen = self._chosen
        self.close()
        if chosen is not None and callable(self.on_chosen):
            self.on_chosen(chosen)

    ## -- lifecycle

    def closeEvent(self, event) -> None:
        # Every tile may be running a QMovie. Left running they keep decoding
        # frames for a dialog nobody can see.
        for tile in self._tiles:
            tile.stop()
        super().closeEvent(event)
