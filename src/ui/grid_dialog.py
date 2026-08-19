from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QScrollArea, QSizePolicy, QScroller,
)
from PyQt6.QtCore import (Qt, QSize, QTimer, QPoint, QObject, QRunnable,
                          QThreadPool, pyqtSignal)
from PyQt6.QtGui import (QPixmap, QMovie, QPainter, QColor, QPen,
                         QFontMetrics, QImage)

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


class _PreviewSignals(QObject):
    """The one way a decoded picture gets back to the UI thread."""

    #(generation, item key, the picture)
    ready = pyqtSignal(int, str, QImage)


class _PreviewJob(QRunnable):
    """
    Decode one picture, off the UI thread.

    A QPixmap cannot be made anywhere but the UI thread, so the work that
    can be moved is the part that takes the time: reading the file and
    scaling it. A 1200x800 photo costs about 12ms to decode and under 1ms to
    turn into a pixmap once decoded, so this moves nearly all of it.

    `generation` is what makes it interruptible. A job started for one folder
    that lands after somebody has walked into another is dropped on arrival
    rather than drawn into a grid it does not belong to.
    """

    __slots__ = ("signals", "generation", "key", "path", "size")

    def __init__(self, signals, generation: int, key: str, path: str,
                 size: int):
        super().__init__()
        self.signals = signals
        self.generation = generation
        self.key = key
        self.path = path
        self.size = size

    def run(self) -> None:
        try:
            picture = QImage(self.path)
            if picture.isNull():
                return
            # Scaled here too. Handing back the full-size image would move
            # the decode off the UI thread and leave the scale on it, and
            # scaling a 1200x800 down is not the cheap half.
            picture = picture.scaled(
                self.size, self.size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self.signals.ready.emit(self.generation, self.key, picture)
        except Exception:
            # A file that went, or one Qt has no plugin for. The tile keeps
            # the placeholder it already drew.
            pass


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


#Drawn placeholders, by (icon name, size). See _Tile._placeholder.
_PLACEHOLDERS: dict = {}
_PLACEHOLDER_LIMIT = 120


class _Tile(QFrame):
    """One cell: a preview with its name under it."""

    #how far a finger may travel and still count as a tap rather than a scroll
    DRAG_DISTANCE = 12

    def __init__(self, item: GridItem, size: int, on_pick: Callable,
                 lines: int = 1, defer_preview: bool = False):
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

        self._load_preview(size, defer=defer_preview)

    def wants_preview(self) -> str:
        """
        The file this would decode, if somebody else does it. Empty if not.

        Asked by the dialog, which hands the work to a thread. A tile cannot
        do it itself: the decode is the slow part and doing it here is doing
        it on the UI thread, which is what made a folder of photos take a
        second to open.
        """
        item = self.item
        if item.pixmap is not None and not item.pixmap.isNull():
            return ""
        if item.preview and not item.animated:
            return str(item.preview)
        return ""

    def apply_preview(self, picture: QImage) -> None:
        """A decoded picture, arriving from the loader."""
        try:
            self.preview.setPixmap(QPixmap.fromImage(picture))
        except RuntimeError:
            # The tile went while its picture was in flight.
            pass

    def _load_preview(self, size: int, defer: bool = False) -> None:
        item = self.item

        if item.pixmap is not None and not item.pixmap.isNull():
            self.preview.setPixmap(self._fit(item.pixmap, size))
            return

        if defer and item.preview and not item.animated:
            # The placeholder now, the picture when it is ready. Something
            # in the box immediately is what stops the grid arriving empty
            # and filling in from the top.
            self.preview.setPixmap(self._placeholder(item, size))
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

        Cached by icon and size, because it depends on nothing else.

        Painting one costs about a millisecond - a fill, an antialiased
        rounded rect, an icon lookup and a blit - and a folder of two hundred
        text files asked for two hundred identical ones. That was most of the
        wait when a folder opened, and all of it again on every re-sort.
        """
        name = item.icon or "mdi.file-outline"
        cached = _PLACEHOLDERS.get((name, size))
        if cached is not None:
            return cached

        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255, 45), 1, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(1, 1, size - 2, size - 2, 10, 10)
        try:
            glyph = resolve_icon(item.icon or "mdi.file-outline",
                                 color="rgba(255,255,255,150)")
            # Most of the tile rather than a third of it.
            #
            # A folder or a file has no picture to show, so the glyph IS the
            # tile - and at 0.4 it was a small mark in a large dashed box,
            # which reads as a preview that failed to load.
            side = int(size * 0.62)
            painter.drawPixmap((size - side) // 2, (size - side) // 2,
                               glyph.pixmap(side, side))
        except Exception:
            pass
        painter.end()

        if len(_PLACEHOLDERS) < _PLACEHOLDER_LIMIT:
            # Bounded, so a long session browsing many kinds of file does not
            # keep every pixmap it ever drew. Past the limit it simply stops
            # caching rather than evicting: the icons in use are the ones
            # already in here, and the ones past it are the rare kinds.
            _PLACEHOLDERS[(name, size)] = pixmap
        return pixmap


    def mouseDoubleClickEvent(self, event) -> None:
        """
        The second tap of a double.

        Qt sends this INSTEAD of a second press, so a widget watching only
        press and release hears one tap where somebody made two - and the
        dialog above, which decides what a double means, never gets the
        chance. It is passed up as an ordinary pick; the timing is judged in
        one place rather than in each widget.
        """
        self.on_pick(self)
        event.accept()

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
            if max(abs(moved.x()), abs(moved.y())) > self.DRAG_DISTANCE:
                # A scroll, not a tap. Selecting here is what made flicking
                # through the grid pick whatever was under your finger.
                event.ignore()
                return
        self.on_pick(self)


#One badge style per kind of thing, so the column reads as a column rather
#than as a word repeated in the same grey.
def _badge(ink: str, fill: str) -> str:
    return (f"QLabel {{ color: {ink}; background: {fill};"
            f" border-radius: 9px; padding: 3px 9px; }}")


#Every word a badge can hold, so one width fits all of them and the column
#does not step in and out down the list.
BADGE_WORDS = ("Endpoint", "Page", "Public", "Player", "Quick", "Users",
               "Audio")

_BADGE_W = 0


def _badge_width(font) -> int:
    """The width the widest badge word needs, measured once."""
    global _BADGE_W
    if not _BADGE_W:
        metrics = QFontMetrics(font)
        _BADGE_W = max(metrics.horizontalAdvance(word)
                       for word in BADGE_WORDS) + 22
    return _BADGE_W


BADGE_CSS = {
    "Endpoint": _badge("#8fc7f5", "rgba(79,157,224,45)"),
    "Page":     _badge("#f5d98f", "rgba(232,195,90,45)"),
    "Public":   _badge("#a8e6c4", "rgba(62,192,138,45)"),
    "Player":   _badge("#d9bff5", "rgba(157,122,224,45)"),
    "Quick":    _badge("#f5c8a8", "rgba(224,133,90,45)"),
    "Users":    _badge("#a8dbe6", "rgba(90,208,224,45)"),
    "Audio":    _badge("#e6b8d4", "rgba(224,85,157,45)"),
    "_default": _badge("rgba(232,236,244,190)", "rgba(255,255,255,20)"),
}


class _Row(QFrame):
    """
    One item as a full-width row: preview, name, subtitle, badge.

    A grid is for scanning something you recognise by sight - a sticker, an
    icon. A list is for reading, and some things can only be told apart by
    reading them: two endpoints on the same plugin differ by their path and by
    nothing else, and at tile size a path is three letters and an ellipsis.

    The same `GridItem` either way, and the same tap-versus-scroll rule, so a
    caller picks a shape rather than a different dialog.
    """

    HEIGHT = 66
    PREVIEW = 52
    DRAG_DISTANCE = 12

    def __init__(self, item: GridItem, on_pick: Callable,
                 defer_preview: bool = False):
        self._defer = bool(defer_preview)
        super().__init__()
        self.item = item
        self.on_pick = on_pick
        self.selected = False
        self._press = None
        self._movie = None

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        set_style(self, "overlays", "grid-tile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        line = QHBoxLayout(self)
        line.setContentsMargins(12, 8, 14, 8)
        line.setSpacing(12)

        self.preview = QLabel()
        self.preview.setFixedSize(self.PREVIEW, self.PREVIEW)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.preview, "common", "transparent")
        self._fill_preview()
        line.addWidget(self.preview)

        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(1)

        name = QLabel(item.label)
        name.setFont(make_font(SIZES.S2, bold=True))
        set_style(name, "common", "text-strong")
        words.addWidget(name)

        if item.subtitle:
            under = QLabel(item.subtitle)
            under.setFont(make_font(SIZES.S1))
            set_style(under, "common", "text-muted")
            words.addWidget(under)
        line.addLayout(words, stretch=1)

        if item.badge:
            # Stated, not looked up. `grid-badge` is not a rule that exists,
            # so set_style found nothing and the label kept the platform
            # palette - black on a dark row.
            badge = QLabel(str(item.badge))
            badge.setFont(make_font(SIZES.S1, bold=True))
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # Measured, and the same for every badge so the column lines up.
            # Fixed rather than minimum - a minimum with nothing above it
            # lets the label take the whole row and the badge becomes a
            # stripe with a word in the middle. 84 was a guess, and "Endpoint"
            # is wider than it.
            badge.setFixedWidth(_badge_width(badge.font()))
            badge.setStyleSheet(BADGE_CSS.get(
                str(item.badge), BADGE_CSS["_default"]))
            line.addWidget(badge)

    def wants_preview(self) -> str:
        """The file this would decode. See _Tile.wants_preview."""
        path = getattr(self.item, "preview", "")
        return str(path) if path else ""

    def apply_preview(self, picture: QImage) -> None:
        try:
            self.preview.setPixmap(QPixmap.fromImage(picture))
        except RuntimeError:
            pass

    def _fill_preview(self) -> None:
        """A picture if there is one, otherwise the item's icon."""
        path = getattr(self.item, "preview", "")
        if path and not self._defer:
            picture = QPixmap(str(path))
            if not picture.isNull():
                self.preview.setPixmap(picture.scaled(
                    self.PREVIEW, self.PREVIEW,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
                return
        # `resolve_icon`, which is what this module imported it as. Calling
        # it `icon` raised NameError into the except below, so every row drew
        # an empty square and said nothing about why.
        try:
            # Sized from the row's preview box rather than a fixed 26: the
            # box is 44 across and a 26px glyph sat in the middle of it with
            # a ring of nothing around it.
            glyph = int(self.PREVIEW * 0.82)
            self.preview.setPixmap(resolve_icon(
                self.item.icon or "mdi.circle-outline").pixmap(glyph, glyph))
        except Exception as e:
            print(f"[GridDialog] Could not draw {self.item.icon!r}: {e}")

    ## -- the same interface the grid tiles answer to


    def mouseDoubleClickEvent(self, event) -> None:
        """
        The second tap of a double.

        Qt sends this INSTEAD of a second press, so a widget watching only
        press and release hears one tap where somebody made two - and the
        dialog above, which decides what a double means, never gets the
        chance. It is passed up as an ordinary pick; the timing is judged in
        one place rather than in each widget.
        """
        self.on_pick(self)
        event.accept()

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
        if start is None:
            return
        moved = event.globalPosition().toPoint() - start
        if max(abs(moved.x()), abs(moved.y())) <= self.DRAG_DISTANCE:
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

    #How many tiles get BUILT at once. Not how many are found.
    #
    #A tile is about half a millisecond of widget, so a folder of eight
    #hundred is most of a second before anything appears - and every re-sort
    #pays it again. Two hundred fills any screen this runs on several times
    #over; the rest arrive on a press, and the search is right there.
    DRAW_CAP = 200

    #How close two taps have to be to count as one double tap.
    #
    #Longer than a mouse's usual 400: this is a finger on a wall panel,
    #often at arm's length, and a double tap that has to be quick is a double
    #tap somebody does three times before it takes.
    DOUBLE_TAP_MS = 600

    #(key, label, keyfunc) - keyfunc takes a GridItem
    #(key, label, keyfunc) or (key, label, keyfunc, icon)
    DEFAULT_SORTS = [
        ("az", "A\u2013Z", lambda i: i.label.lower(), "mdi.sort-alphabetical-ascending"),
        ("za", "Z\u2013A", lambda i: i.label.lower(), "mdi.sort-alphabetical-descending"),
    ]

    #What a folder sorts by. Kept when the shortcuts and the path bar are
    #turned off, because sorting is the one part of browsing that a plain
    #picker still wants.
    BROWSE_SORTS = [
        ("az", "A\u2013Z", lambda i: i.label.lower(),
         "mdi.sort-alphabetical-ascending"),
        ("newest", "Newest", lambda i: -getattr(i, "modified", 0.0),
         "mdi.sort-calendar-descending"),
        ("largest", "Largest", lambda i: -getattr(i, "size_bytes", 0),
         "mdi.sort-numeric-descending"),
    ]

    def __init__(self, client: "Client", title: str = "Choose",
                 body: str = "", items: list = None,
                 on_chosen: Callable = None, on_search: Callable = None,
                 choose_text: str = "Use this", empty_text: str = "Nothing here yet.",
                 search_hint: str = "Search", extra_button: tuple = None,
                 sorts: list = None, on_delete: Callable = None,
                 delete_text: str = "Delete", label_lines: int = 1,
                 layout: str = "grid", browse=None, select: str = "both",
                 multiple: bool = False, show_hidden_toggle: bool = None):
        #"grid" to scan by sight, "list" to tell things apart by reading.
        self.layout_mode = "list" if str(layout).lower() == "list" else "grid"

        # Browsing is this dialog with more of it turned on.
        #
        # A file explorer IS a searchable grid of things to pick one of - the
        # only differences are that the set changes as you walk into it, and
        # that there is a rail of places to jump to. Written as a second
        # dialog they would be two of everything: two tile widgets, two
        # searches, two sorts, and two places for a fix to be needed.
        #
        # `browse` is the folder to start in, or None for the fixed-set
        # behaviour every existing caller already gets.
        self.browsing = browse is not None
        self.select_kind = str(select or "both").lower()
        self.multiple = bool(multiple) and self.browsing
        self._picked: dict = {}
        # Set by the buttons, never by the click blocker. See can_close().
        self._leaving = False
        #The last tap, for telling a double from two singles.
        self._last_tap_key = ""
        self._last_tap_at = 0.0

        #Pictures are decoded off the UI thread and arrive afterwards.
        #
        #A 1200x800 photo costs about 12ms to read and scale, against 0.09ms
        #to build the tile that holds it - so a folder of eighty photos spent
        #a second decoding before it drew anything at all, and every sort
        #change spent it again.
        #
        #`_generation` is what makes it interruptible: a picture decoded for
        #one folder that lands after somebody walked into another is dropped
        #rather than drawn into a grid it does not belong to.
        self._previews = None
        self._generation = 0
        self._pool = None
        #How many tiles may be built. Raised by the button at the end.
        self._drawn_cap = self.DRAW_CAP
        self._found = 0
        self._show_hidden = False
        self._search_note = ""
        # The toggle is a free-browsing control. A dialog picking from one
        # folder has nothing to reveal, and an option that does nothing is an
        # option somebody presses twice to check.
        self._hidden_toggle = (self.browsing if show_hidden_toggle is None
                               else bool(show_hidden_toggle))
        self.folder = None
        if self.browsing:
            from pathlib import Path as _Path
            try:
                start = _Path(browse).expanduser()
                self.folder = start if start.is_dir() else _Path.home()
            except Exception:
                self.folder = _Path.home()

        host = getattr(client, "OVERLAYS", None)
        width = self.MIN_WIDTH
        try:
            if host is not None and host.width() > 0:
                width = max(self.MIN_WIDTH, int(host.width() * self.WIDTH_RATIO))
        except Exception:
            pass
        super().__init__(client, title, body, width=width)

        # After super(): a QThreadPool parented to this needs the QObject
        # underneath to exist, and the dialog's own __init__ runs before it.
        self._previews = _PreviewSignals()
        self._previews.ready.connect(self._preview_arrived)
        self._pool = QThreadPool(self)
        # Two, not one per core. These are decodes competing with the UI
        # thread on a panel that is drawing at the same time, and the point
        # is a grid that appears now rather than pictures that finish first.
        self._pool.setMaxThreadCount(2)

        self.on_chosen = on_chosen
        self.on_search = on_search
        self.on_delete = on_delete
        # How many lines a name gets. One for a grid of pictures, two when the
        # name is what somebody is reading.
        self._label_lines = max(1, min(3, int(label_lines or 1)))
        if sorts:
            self._sorts = list(sorts)
        else:
            self._sorts = list(self.BROWSE_SORTS if self.browsing
                               else self.DEFAULT_SORTS)
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

        # Where you are, and the way back up.
        #
        # Above the search rather than beside it: the path is what somebody
        # reads first to know whether they are lost, and a bar that shares a
        # line with a text field is a bar nobody notices.
        self._path_label = None
        self._up_button = None
        if self.browsing:
            # Close together, so the controls read as one bar rather than as
            # four separate things stacked down the dialog. The gap between
            # them was costing the height that the files need.
            self.content.setSpacing(4)

            path_row = QHBoxLayout()
            path_row.setSpacing(8)

            self._up_button = QPushButton()
            self._up_button.setFixedSize(46, 46)
            self._up_button.setIcon(resolve_icon("mdi.arrow-up",
                                                 color="#e6e6ec"))
            self._up_button.setIconSize(QSize(20, 20))
            self._up_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._up_button.setToolTip("Up one folder")
            set_style(self._up_button, "settings", "plugin-action-copy")
            self._up_button.clicked.connect(self._go_up)
            path_row.addWidget(self._up_button)

            # A button, not a label: tapping the path is how somebody types
            # one, and there is no keyboard on this panel but the one the
            # dialog opens.
            self._path_label = QPushButton("")
            self._path_label.setFixedHeight(46)
            self._path_label.setFont(make_font(SIZES.S1))
            self._path_label.setCursor(Qt.CursorShape.PointingHandCursor)
            self._path_label.setToolTip("Tap to type a path")
            set_style(self._path_label, "settings", "body-field")
            # A path reads from the left. Centred it looks like a button's
            # label rather than like the address it is, and a long one
            # trimmed from the left then has its end floating in the middle.
            self._path_label.setStyleSheet(
                self._path_label.styleSheet() +
                " QPushButton { text-align: left; padding-left: 12px; }")
            self._path_label.clicked.connect(self._type_path)
            path_row.addWidget(self._path_label, stretch=1)

            if self._hidden_toggle:
                self._hidden_button = QPushButton("Hidden")
                self._hidden_button.setFixedHeight(46)
                self._hidden_button.setFont(make_font(SIZES.S1, bold=True))
                self._hidden_button.setCursor(Qt.CursorShape.PointingHandCursor)
                self._hidden_button.setToolTip("Show files beginning with a dot")
                set_style(self._hidden_button, "settings",
                          "plugin-action-copy")
                self._hidden_button.clicked.connect(self._toggle_hidden)
                path_row.addWidget(self._hidden_button)

            # No margins on the holder's layout.
            #
            # A QHBoxLayout defaults to 9px all round, so every row put 18px
            # of its own between itself and the next one - four times the
            # spacing the dialog had asked for, and nothing about the
            # spacing setting could reach it.
            path_row.setContentsMargins(0, 0, 0, 0)
            path_holder = QWidget()
            set_style(path_holder, "common", "transparent")
            path_holder.setLayout(path_row)
            self.content.addWidget(path_holder)

        # Search
        row = QHBoxLayout()
        row.setSpacing(8)
        self.search = _SearchField(self._open_keyboard)
        self.search.setPlaceholderText(search_hint)
        self.search.setFont(make_font(SIZES.S2))
        self.search.setFixedHeight(46)
        set_style(self.search, "settings", "body-field")
        # Inside the field rather than beside it. A bare rounded box is the
        # same shape as the path bar above it, and the two are for different
        # things - the glyph is what says which is which at a glance.
        #
        # A child QLabel, not addAction(). Qt sizes an action's icon from the
        # style - about 16px, which on a 46px field reads as a speck - and
        # there is no way to ask it for a bigger one. A label is drawn at
        # whatever size it is given, and the field's text is moved over to
        # make room for it.
        try:
            glyph = QLabel(self.search)
            glyph.setPixmap(resolve_icon("mdi.magnify", color="#9a9aa6")
                            .pixmap(24, 24))
            glyph.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            set_style(glyph, "common", "transparent")
            glyph.setFixedSize(24, 24)
            glyph.move(14, (46 - 24) // 2)
            self.search.setTextMargins(46, 0, 0, 0)
        except Exception as e:
            self.client.log("debug", f"[Browse] No search glyph: {e}")
        row.addWidget(self.search, stretch=1)

        self.clear_button = QPushButton("Clear")
        self.clear_button.setFixedHeight(46)
        self.clear_button.setMinimumWidth(96)
        self.clear_button.setFont(make_font(SIZES.S2, bold=True))
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(self.clear_button, "settings", "plugin-action-copy")
        self.clear_button.clicked.connect(self._clear_search)
        row.addWidget(self.clear_button)

        if self.browsing:
            row.setContentsMargins(0, 0, 0, 0)
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
            # No "Sort" caption.
            #
            # The buttons say A-Z, Newest, Largest. A word in front of them
            # explaining that those are sorts is a word nobody needed, and it
            # is the only label on the row that is not pressable - which is
            # exactly the thing a finger tries first.
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
                    # Big enough to be read as the thing that says which way
                    # round the sort goes. At 15 it was a smudge beside the
                    # word and the word did all the work.
                    button.setIconSize(QSize(20, 20))
                # Sized to its own text plus the padding the stylesheet adds,
                # rather than a guessed minimum - "Biggest" and "A-Z" are very
                # different widths and a fixed one clipped the longer.
                metrics = QFontMetrics(button.font())
                button.setMinimumWidth(
                    metrics.horizontalAdvance(label) + (40 if glyph else 0) + 40)
                button.clicked.connect(
                    lambda _=False, k=key: self._set_sort(k))
                self._sort_buttons[key] = button
                sort_row.addWidget(button)
            sort_row.addStretch()
            if self.browsing:
                sort_row.setContentsMargins(0, 0, 0, 0)
            sort_holder = QWidget()
            set_style(sort_holder, "common", "transparent")
            sort_holder.setLayout(sort_row)
            self.content.addWidget(sort_holder)
            self._paint_sort_buttons()

        self._grid_host = QWidget()
        set_style(self._grid_host, "common", "transparent")
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 2 if self.browsing else 6, 0, 6)
        self._grid.setSpacing(8)
        if self.layout_mode == "list":
            # One column that takes the width, so a row is a row rather than
            # a tile-sized thing sitting at the left of an empty line.
            self._grid.setColumnStretch(0, 1)
            self._grid.setSpacing(6)
            self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)
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

        if self.browsing:
            # The rail beside the items rather than above them: it is a list
            # of places and the items are a list of things, and stacking two
            # lists costs the height that the things need.
            middle = QHBoxLayout()
            middle.setContentsMargins(0, 0, 0, 0)
            middle.setSpacing(10)
            middle.addWidget(self._build_rail())
            middle.addWidget(self._scroll, stretch=1)
            middle_holder = QWidget()
            set_style(middle_holder, "common", "transparent")
            middle_holder.setLayout(middle)
            self.content.addWidget(middle_holder, stretch=1)
        else:
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

        self.add_button("Cancel", self.leave, "secondary")
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

        if self.browsing:
            self.open_folder(self.folder)
        else:
            self.rebuild(self._all_items)

    ## -- browsing

    def _build_rail(self) -> QWidget:
        """
        The places worth jumping to, down the left.

        Built once. What is on it - the asset folders, a plugged-in drive -
        changes when a plugin loads or somebody plugs something in, and
        neither happens while a dialog is open.
        """
        from src.ui import file_source

        rail = QVBoxLayout()
        rail.setContentsMargins(8, 8, 8, 8)
        rail.setSpacing(3)

        first = True
        for section, entries in file_source.shortcut_groups(self.client):
            heading = QLabel(section)
            heading.setFont(make_font(SIZES.S1, bold=True))
            set_style(heading, "common", "text-muted")
            # Room above each heading but the first, so a section reads as a
            # new group rather than as more of the list above it.
            heading.setContentsMargins(6, 2 if first else 12, 0, 4)
            rail.addWidget(heading)
            first = False
            self._add_rail_buttons(rail, entries)

        rail.addStretch()

        holder = QWidget()
        # Its own panel rather than buttons floating beside the files.
        #
        # The rail is a different KIND of thing from the grid - places rather
        # than contents - and with no edge between them a tap near the
        # boundary is a guess about which list it belongs to.
        holder.setLayout(rail)
        set_style(holder, "common", "transparent")

        # Scrolled, not squashed.
        #
        # A QVBoxLayout given less height than its children need does not
        # shrink them - they have fixed heights - it overlaps them. Three
        # groups of shortcuts plus a couple of drives is taller than a
        # dialog on a 600px panel, so the rail has to be able to scroll or
        # the buttons sit on top of each other.
        rail_scroll = QScrollArea()
        # Wide enough for the buttons AND the bar.
        #
        # The scrollbar is drawn inside the viewport, so it takes its width
        # from the buttons rather than from the panel - the column narrowed
        # by exactly the bar the moment there was enough in the rail to need
        # one. 6px of bar plus the panel's own border and padding.
        rail_scroll.setFixedWidth(self.RAIL_WIDTH)
        rail_scroll.setWidget(holder)
        rail_scroll.setWidgetResizable(True)
        rail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        rail_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rail_scroll.viewport().setAutoFillBackground(False)
        set_style(rail_scroll.viewport(), "common", "transparent")
        QScroller.grabGesture(
            rail_scroll.viewport(),
            QScroller.ScrollerGestureType.LeftMouseButtonGesture)

        # Its own panel rather than buttons floating beside the files.
        #
        # The rail is a different KIND of thing from the grid - places rather
        # than contents - and with no edge between them a tap near the
        # boundary is a guess about which list it belongs to. On the scroll
        # area, so the edge is around the whole rail rather than around a
        # column that scrolls inside it.
        rail_scroll.setStyleSheet(
            "QScrollArea { background: rgba(255,255,255,0.035);"
            " border: 1px solid rgba(255,255,255,0.07);"
            " border-radius: 14px; }")
        # AFTER the panel styling, never before.
        #
        # `setStyleSheet` REPLACES. style_scrollbar appends to whatever is
        # already there, so called first it is wiped by the line above and
        # the rail gets the platform's own bar - which is the exact trap
        # style_scrollbar's docstring describes.
        style_scrollbar(rail_scroll)
        return rail_scroll

    #How wide the rail is, and what a button costs beyond its text.
    #
    #The second is measured rather than guessed: a QPushButton's size hint is
    #its text plus its icon, its padding and the stylesheet's own - about 35
    #here - and the text budget is what is left of the viewport after the
    #panel's border and the scrollbar, which is drawn INSIDE the viewport and
    #so takes its width from the buttons.
    RAIL_WIDTH = 214
    #Measured, and there are three things between the text and the rail's
    #edge rather than one: the button's own padding and icon (44), the
    #column's margins (8 each side), and the scrollbar drawn inside the
    #viewport (6). 68 covers all three with a couple of pixels spare, rather
    #than landing exactly on the edge where a font a hair wider starts it
    #scrolling sideways again.
    RAIL_CHROME = 68

    @property
    def _rail_text_width(self) -> int:
        """How wide a rail label may be before it is shortened."""
        return max(80, self.RAIL_WIDTH - self.RAIL_CHROME)

    def _add_rail_buttons(self, rail, entries) -> None:
        """One section's places."""
        metrics = None
        for label, path, glyph in entries:
            # Shortened to fit, with the whole name in the tooltip.
            #
            # A QPushButton asks for whatever width its text needs and never
            # gives it back, so one long asset name - "Nighttime Wallpapers"
            # wants 308px - makes the whole column wider than the rail and
            # the rail scrolls sideways. Nothing about widening it helps:
            # the next name is longer.
            button = QPushButton()
            if metrics is None:
                metrics = QFontMetrics(make_font(SIZES.S1, bold=True))
            shown = metrics.elidedText(str(label), Qt.TextElideMode.ElideRight,
                                       self._rail_text_width)
            button.setText(f"  {shown}")
            button.setFixedHeight(42)
            button.setFont(make_font(SIZES.S1, bold=True))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIcon(resolve_icon(glyph, color="#9a9aa6"))
            button.setIconSize(QSize(19, 19))
            # The name AND where it goes, since the name may be shortened.
            button.setToolTip(f"{label}\n{path}" if shown != label
                              else str(path))
            set_style(button, "settings", "plugin-action-copy")
            # Left, not centred.
            #
            # Qt centres the icon and the label together, so a column of
            # these has every glyph at a different x - the rail reads as a
            # ragged edge rather than as a list, and there is nothing to run
            # an eye down. The stylesheet is appended rather than replaced,
            # or it would take the shared look with it.
            button.setStyleSheet(
                button.styleSheet() +
                " QPushButton { text-align: left; padding-left: 10px; }")
            button.clicked.connect(
                lambda _=False, target=path: self.open_folder(target))
            rail.addWidget(button)

    def open_folder(self, folder) -> None:
        """Go there, and forget anything picked in the folder being left."""
        from pathlib import Path
        from src.ui import file_source

        folder = Path(folder)
        items, problem = file_source.entries(
            folder, show_hidden=self._show_hidden, select=self.select_kind)

        if problem:
            # Said and stayed put. A dialog that closes or jumps somewhere
            # else because a folder would not open has taken the person
            # further from what they were doing.
            self.status.setText(problem)
            return

        self.folder = folder
        self._search_note = ""
        self.search.blockSignals(True)
        self.search.setText("")
        self.search.blockSignals(False)
        # A selection belongs to the folder it was made in. Carrying it
        # across means confirming a list of files somebody can no longer see.
        self._picked.clear()
        self._chosen = None
        self._drawn_cap = self.DRAW_CAP
        self._all_items = items
        self._paint_path()
        self.rebuild(items)
        self._offer_this_folder()

    def _offer_this_folder(self) -> None:
        """
        A folder picker can always be confirmed.

        Standing in a folder IS the choice, so the button is live from the
        moment one opens rather than only after somebody taps a row. Waiting
        for a tap means a folder with nothing in it can never be chosen -
        and an empty folder is exactly the one somebody has just made to put
        something in.
        """
        if not (self.browsing and self.select_kind == "folder"):
            return
        if self._picked or self._chosen is not None:
            return
        self.set_button_state(self._confirm, True, "primary")
        self.status.setText(f"Use {self.folder.name or self.folder}")

    def _go_up(self) -> None:
        from pathlib import Path

        if self.folder is None:
            return
        parent = Path(self.folder).parent
        if parent != self.folder:
            self.open_folder(parent)

    def _paint_path(self) -> None:
        if self._path_label is None or self.folder is None:
            return
        from pathlib import Path

        text = str(self.folder)
        # Trimmed from the left, so the end - the part somebody is standing
        # in - is what survives.
        if len(text) > 58:
            text = "\u2026" + text[-57:]
        self._path_label.setText(f"  {text}")
        if self._up_button is not None:
            at_top = Path(self.folder).parent == Path(self.folder)
            self._up_button.setEnabled(not at_top)

    def _type_path(self) -> None:
        """The keyboard, then go there if it exists."""
        from src.ui.keyboard import make_keyboard

        field = QLineEdit(str(self.folder or ""))
        try:
            keyboard = make_keyboard(
                self.client, field, "string", label="Go to",
                description="A folder to open. Relative paths are from here.")

            def done(_text=None):
                from src.ui import file_source

                target = file_source.resolve(field.text(), current=self.folder)
                if target is None:
                    self.status.setText(f"No folder called {field.text()!r}.")
                    return
                self.open_folder(target if target.is_dir() else target.parent)

            keyboard.on_done = done
            self.client.dialog(keyboard)
        except Exception as e:
            self.client.log("warning", f"[Browse] Keyboard failed: {e}")

    def _toggle_hidden(self) -> None:
        self._show_hidden = not self._show_hidden
        try:
            self.set_button_state(self._hidden_button, self._show_hidden,
                                  "secondary")
        except Exception:
            pass
        self.open_folder(self.folder)

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

        if self.browsing:
            from src.ui import file_source

            if not text:
                self._search_note = ""
                self.rebuild(self._all_items)
                return
            # From here downwards, never above. A search that can leave the
            # folder somebody is standing in gives results they cannot place.
            found, note = file_source.search(
                self.folder, text, show_hidden=self._show_hidden,
                select=self.select_kind)
            self._search_note = note
            self.rebuild(found)
            return

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

    def _preview_arrived(self, generation: int, key: str,
                         picture: QImage) -> None:
        """One decoded picture, if it is still wanted."""
        if generation != self._generation:
            return
        for tile in self._tiles:
            if tile.item.key == key:
                tile.apply_preview(picture)
                return

    def _start_previews(self) -> None:
        """Hand every visible picture to the pool."""
        if self._pool is None:
            return
        size = self.TILE if self.layout_mode == "grid" else _Row.PREVIEW
        for tile in self._tiles:
            wanted = tile.wants_preview()
            if not wanted:
                continue
            self._pool.start(_PreviewJob(self._previews, self._generation,
                                         tile.item.key, wanted, size))

    def rebuild(self, items: list) -> None:
        # Anything still queued was for the list being replaced.
        #
        # `clear()` drops what has not started; the two that are running
        # finish and are thrown away on arrival by the generation check.
        # Waiting for them would be the pause this exists to remove.
        self._generation += 1
        try:
            if self._pool is not None:
                self._pool.clear()
        except Exception:
            pass

        items = self._sorted(items)
        self._found = len(items)
        shown = items[:self._drawn_cap]
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

        if self.layout_mode == "list":
            # One per row, full width. `_tiles` either way - everything else
            # here selects, clears and counts them without caring which shape
            # they are.
            for index, item in enumerate(shown):
                row = _Row(item, self._pick, defer_preview=True)
                self._grid.addWidget(row, index, 0)
                self._tiles.append(row)
        else:
            columns = self._columns()
            for index, item in enumerate(shown):
                tile = _Tile(item, self.TILE, self._pick,
                             lines=self._label_lines, defer_preview=True)
                self._grid.addWidget(tile, index // columns, index % columns)
                self._tiles.append(tile)

        # Tiles first, pictures after. The grid is on screen and scrollable
        # before a single file is read.
        self._start_previews()

        hidden = len(items) - len(shown)
        if hidden > 0:
            # The rest are a press away rather than gone. A cap that cannot
            # be lifted is a folder with things in it nobody can reach.
            more = QPushButton(f"Show the other {hidden}")
            more.setFixedHeight(44)
            more.setFont(make_font(SIZES.S1, bold=True))
            more.setCursor(Qt.CursorShape.PointingHandCursor)
            set_style(more, "settings", "plugin-action-copy")
            more.clicked.connect(self._draw_more)
            row_at = (len(shown) if self.layout_mode == "list"
                      else len(shown) // max(1, self._columns()) + 1)
            self._grid.addWidget(more, row_at, 0, 1,
                                 1 if self.layout_mode == "list"
                                 else self._columns())

        if not items:
            self.status.setText(self.empty_text)
        elif hidden > 0:
            self.status.setText(
                f"Showing {len(shown)} of {len(items)} \u00b7 search to narrow")
        else:
            self.status.setText(f"{len(items)} item" + ("s" if len(items) != 1 else ""))

    def _draw_more(self) -> None:
        """Another capful, from wherever the list currently is."""
        self._drawn_cap += self.DRAW_CAP
        self.rebuild(self._current_items())

    def _current_items(self) -> list:
        """Whatever the grid is showing from - the folder, or a search."""
        text = self.search.text().strip()
        if not text:
            return self._all_items
        if self.browsing:
            from src.ui import file_source

            found, _note = file_source.search(
                self.folder, text, show_hidden=self._show_hidden,
                select=self.select_kind)
            return found
        words = [w for w in text.lower().split() if w]
        return [i for i in self._all_items
                if all(w in i.haystack() for w in words)]

    def _pick(self, tile: _Tile) -> None:
        import time as _time

        item = tile.item

        # One tap selects, two open. Always, whatever the dialog wants.
        #
        # The gesture cannot depend on what is being picked. A folder that
        # opens on one tap here and selects on one tap there is two rules to
        # learn from the same-looking screen, and the one somebody learns
        # first is the one they are wrong about in the other.
        #
        # So: double tap is the way in, everywhere. A single tap selects when
        # folders are selectable and says how to get in when they are not.
        now = _time.monotonic()
        doubled = (item.key == self._last_tap_key
                   and (now - self._last_tap_at) * 1000 <= self.DOUBLE_TAP_MS)
        self._last_tap_key, self._last_tap_at = item.key, now

        if self.browsing and getattr(item, "is_dir", False):
            if doubled:
                # Not a tap in the new folder, so the first tap there is a
                # first tap.
                self._last_tap_key, self._last_tap_at = "", 0.0
                self.open_folder(item.path)
                return
            if self.select_kind == "file":
                # Nothing to select, so the tap teaches the gesture rather
                # than doing nothing at all.
                self.status.setText(f"Double tap to open '{item.label}'.")
                return

        if self.multiple:
            if item.key in self._picked:
                del self._picked[item.key]
                tile.set_selected(False)
            else:
                self._picked[item.key] = item
                tile.set_selected(True)
            remaining = list(self._picked.values())
            self._chosen = remaining[0] if len(remaining) == 1 else None
            count = len(remaining)
            self.set_button_state(self._confirm, count > 0, "primary")
            self.set_button_state(self._delete_button, count == 1,
                                  "destructive")
            # Named from what is STILL selected, not from what was just
            # tapped - untapping down to one would otherwise look up the item
            # that had just been removed.
            self.status.setText(
                "" if not count else
                (remaining[0].label if count == 1 else f"{count} selected"))
            return

        # Tapping what is already selected puts it back.
        #
        # Otherwise the only way out of a selection is to pick something
        # else, which is not the same thing - somebody who tapped the wrong
        # row and wants nothing selected has to leave one selected anyway.
        if self._chosen is not None and self._chosen.key == item.key:
            tile.set_selected(False)
            self._chosen = None
            self.set_button_state(self._confirm, False, "primary")
            self.set_button_state(self._delete_button, False, "destructive")
            self.status.setText("")
            # A folder picker still has the folder itself to offer.
            self._offer_this_folder()
            return

        for other in self._tiles:
            other.set_selected(other is tile)
        self._chosen = item
        self.set_button_state(self._confirm, True, "primary")
        self.set_button_state(self._delete_button, True, "destructive")
        self.status.setText(item.label)

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
        """
        Hand back what was picked, as full paths when browsing.

        A folder picker with nothing selected answers with the folder
        somebody is standing in. That is what walking into it meant, and
        making them tap it in its own parent first is a step that exists only
        because the code found it easier.
        """
        if self.browsing and self.select_kind == "folder" and not self._picked \
                and self._chosen is None:
            from src.ui import file_source

            self._chosen = file_source.to_item(self.folder)

        chosen = self._chosen
        picked = list(self._picked.values()) if self.multiple else None
        self.leave()

        if not callable(self.on_chosen):
            return
        if self.multiple:
            if picked:
                self.on_chosen(picked)
            return
        if chosen is not None:
            self.on_chosen(chosen)

    def can_close(self) -> bool:
        """
        Not by tapping outside it, but yes by pressing a button.

        `DialogManager` asks this on every close - the blocker behind the
        dialog AND the dialog's own buttons, since both go through the one
        path. So a flat refusal would take Cancel with it, and the only way
        out would be the panel.

        `_leaving` is what tells them apart: the buttons set it, the blocker
        does not. A file explorer is a place somebody walks around in, and
        the taps that miss a tile are exactly the ones that land on the
        blocker - losing it to one of those costs them everywhere they had
        walked to.
        """
        return (not self.browsing) or self._leaving

    def leave(self) -> None:
        """Close on purpose. What every button in this dialog calls."""
        self._leaving = True
        self.close()

    ## -- lifecycle

    def closeEvent(self, event) -> None:
        # Every tile may be running a QMovie. Left running they keep decoding
        # frames for a dialog nobody can see.
        for tile in self._tiles:
            tile.stop()
        super().closeEvent(event)
