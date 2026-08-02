"""
Setting up what an action tile runs.

Two panes. On the left, what was chosen and how the tile looks - a fixed
column, because it is read rather than worked in. On the right, the arguments,
which is where the work happens and where the room is needed.

Big on purpose. This is the one dialog on the panel with a list somebody
scrolls, a grid they pick from, and a form they fill in, all at once, and a
wall panel is read from further away than a desk.
"""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame,
    QScrollArea, QSizePolicy, QScroller, QPushButton, QCheckBox,
    QComboBox,
)

from src.styling import (
    set_style, make_font, SIZES, style_scrollbar, get_style_sheet)
from src.ui.overlays import BaseDialog
from src.ui.controls.buttons import IconButton, ActionButton
from src.ui.dialogs_look import Swatch
from src.ui.keyboard import make_keyboard
from src.ui.grid_dialog import ItemGridDialog, GridItem
from src.ui.icons import icon
from .action_arguments import ArgumentList, _Field
from .action_rules_ui import RuleList
from . import action_sources, action_runner

if TYPE_CHECKING:
    from src.main import Client


#What a tile can be coloured. The same set the paper widgets use, so a panel
#does not grow a second palette.
COLOURS = [
    "#4f9de0", "#3ec08a", "#e8c35a", "#e0855a",
    "#e0559d", "#9d7ae0", "#5ad0e0", "#8a8a8a",
]

#Icons offered for a tile. Broad rather than exhaustive - anything registered
#can be pointed at, so this is a starting look rather than a picture of what
#the thing does.
ICONS = [
    "mdi.gesture-tap-button", "mdi.play", "mdi.stop", "mdi.refresh",
    "mdi.power", "mdi.lightbulb-outline", "mdi.volume-high", "mdi.bell-outline",
    "mdi.calendar", "mdi.timer-outline", "mdi.weather-partly-cloudy", "mdi.rss",
    "mdi.home-outline", "mdi.cog-outline", "mdi.web", "mdi.api",
    "mdi.download", "mdi.upload", "mdi.magnify", "mdi.star-outline",
    "mdi.heart-outline", "mdi.check", "mdi.close", "mdi.dots-horizontal",
]


def _one_line(value) -> str:
    """
    A value as a single line, for a row that has one.

    The pretty-printed form belongs in the answer pane where there is room
    for it; in a list row its newlines come out as literal gaps and the
    subtitle stops being a subtitle.
    """
    text = " ".join(str(action_runner._shorten(value)).split())
    return text if len(text) <= 70 else text[:69] + "\u2026"


class ActionSetupDialog(BaseDialog):
    """One dialog: what it runs, how it looks, and what it is called with."""

    #Wider and taller than an ordinary dialog. Two panes side by side need the
    #width, and the argument list needs the height to be worth scrolling.
    WIDTH = 2360
    MAX_HEIGHT = 1400

    #The left column. Fixed, because it holds a fixed set of controls and the
    #arguments should get every pixel that is left.
    SIDE_WIDTH = 380
    #The answer pane. Fixed like the left one: what comes back is read rather
    #than worked in, and a column that keeps its width is one somebody can
    #learn the shape of.
    ANSWER_WIDTH = 620
    #How tall the two panes are. The argument list is the reason - a list
    #worth scrolling needs to show enough rows that scrolling it is obviously
    #possible.
    PANE_HEIGHT = 940

    #The path button. Stated, like every other control here - see
    #action_arguments for why nothing inherits the platform palette.
    CHECK_CSS = """
        QCheckBox { color: #e8ecf4; spacing: 8px; }
        QCheckBox::indicator { width: 22px; height: 22px; border-radius: 6px;
                               border: 1px solid rgba(255,255,255,60);
                               background: rgba(255,255,255,10); }
        QCheckBox::indicator:checked { background: #2ff08e;
                                       border-color: #2ff08e; }
    """
    NAME_CSS = """
        QPushButton { background: transparent; border: none;
                      color: #f0f0f4; text-align: left; padding: 0; }
        QPushButton:hover { color: #6fa8e0; }
    """
    PATH_CSS = """
        QPushButton { background: rgba(255,255,255,14);
                      border: 1px solid rgba(255,255,255,26);
                      border-radius: 8px; color: #e8ecf4;
                      text-align: left; padding: 0 12px; }
        QPushButton:hover { border-color: rgba(255,255,255,60); }
    """

    def __init__(self, client: "Client", runnable, action: dict = None,
                 on_saved: Callable = None, on_rechoose: Callable = None):
        super().__init__(client, "Set up this action",
                         "Not everything answers with something a tile can "
                         "show. Try it below to see what comes back - and if "
                         "you need an exact behaviour, a plugin of your own "
                         "will serve you better than this will.")
        self.runnable = runnable
        self.on_saved = on_saved
        self.on_rechoose = on_rechoose
        self.saved = dict(action or {})
        saved = dict(action or {})

        self.chosen_colour = saved.get("colour") or COLOURS[0]
        self.chosen_icon = saved.get("icon") or runnable.icon
        self.label_text = saved.get("label") or runnable.label

        # Three columns, or two with the answer underneath.
        #
        # Below about 1580 the three cannot hold their minimums however they
        # are shrunk - a left pane of icons, a middle of argument rows and an
        # answer wide enough to read JSON in simply do not fit. Shrinking
        # them all only moves the point where each becomes useless, so past
        # it the answer goes under the middle instead, where it still has the
        # width it needs and gives up height it can afford.
        self.stacked = self.width() < self.THREE_COLUMN_MIN

        panes = QHBoxLayout()
        panes.setSpacing(16)
        panes.addWidget(self._left(), stretch=0)

        if self.stacked:
            column = QVBoxLayout()
            column.setSpacing(12)
            column.addWidget(self._middle(saved), stretch=3)
            column.addWidget(self._right(saved), stretch=2)
            panes.addLayout(column, stretch=1)
        else:
            panes.addWidget(self._middle(saved), stretch=1)
            panes.addWidget(self._right(saved), stretch=0)

        # Asked for on the widget rather than through the layout. The base
        # dialog adds `content` without a stretch and puts a spacer under it,
        # so nothing inside can claim height by asking - the panes have to be
        # tall enough on their own.
        holder = QWidget()
        set_style(holder, "common", "transparent")
        holder.setLayout(panes)
        # Asked for, not demanded. A minimum taller than the dialog is
        # allowed to be pushes the whole thing past its own maximum, which is
        # how a clamped dialog still runs off the bottom of the screen.
        # Whatever is left after the title, blurb and buttons - and no floor
        # under it. A floor here is the same mistake as the scroll areas
        # below: it is a number that looks safe and is exactly what stops a
        # clamped dialog fitting a small screen.
        holder.setMinimumHeight(max(0, min(self.PANE_HEIGHT,
                                           self.maximumHeight() - 190)))
        holder.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Expanding)
        self.content.addWidget(holder, stretch=1)

        self.add_button("Save", self._save, "primary")
        # A way back to the list without cancelling. Somebody who picked the
        # wrong endpoint should not have to close, find the tile, and start
        # again to reach the one beside it.
        self.add_button("Choose something else", self._choose_again,
                        "secondary")
        self.add_button("Cancel", self.close, "secondary")

    ## -- left: what it is, and how it looks

    #What the middle pane must keep. Below this the argument rows stop being
    #usable - a name, a kind, a value and a delete do not fit in less.
    MIDDLE_MIN = 520
    #The narrowest a three-column layout can be: both side panes at full
    #width, the middle at its minimum, and the gaps between them.
    THREE_COLUMN_MIN = SIDE_WIDTH + ANSWER_WIDTH + MIDDLE_MIN + 64

    def _share(self, wanted: int) -> int:
        """
        A side pane's width, shrunk if the middle would not fit otherwise.

        Both sides give up the same proportion, so they stay balanced rather
        than one collapsing while the other keeps its size.
        """
        if getattr(self, "stacked", False):
            # Stacked: the answer is under the middle and has the whole width
            # to itself, so only the left pane is a column and it keeps its
            # size.
            return int(wanted)
        spare = self.width() - self.MIDDLE_MIN - 64
        both = self.SIDE_WIDTH + self.ANSWER_WIDTH
        if spare >= both or both <= 0:
            return int(wanted)
        # Never below half: a pane squeezed past that is a column of clipped
        # controls, and at that point the dialog wants a different layout
        # rather than a narrower one.
        return max(int(wanted * 0.55), int(wanted * spare / both))

    def _left(self) -> QWidget:
        host = QWidget()
        # Narrowed with the dialog rather than held. The dialog is clamped to
        # the screen, so on a small one the three fixed panes would add up to
        # more than there is and the middle would be squeezed to nothing -
        # the middle being the part somebody is actually working in.
        host.setFixedWidth(self._share(self.SIDE_WIDTH))
        set_style(host, "common", "transparent")

        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)

        column.addWidget(self._summary())
        column.addWidget(self._heading("Colour"))
        column.addWidget(self._colours())
        column.addWidget(self._heading("Icon"))
        column.addWidget(self._icons())
        column.addWidget(self._heading("Keeping it up to date"))
        column.addWidget(self._polling())
        column.addStretch()
        return host

    #How often a polling tile runs. Offered rather than typed: the useful
    #range is narrow and a number typed into a keyboard here is a number
    #somebody has to think about.
    POLL_CHOICES = [(15, "every 15 seconds"), (30, "every 30 seconds"),
                    (60, "every minute"), (300, "every 5 minutes"),
                    (900, "every 15 minutes")]

    def _polling(self) -> QWidget:
        """
        Whether the tile runs itself, or waits to be pressed.

        Offered only for things that read. Polling something that changes the
        world would change it on a timer with nobody asking, and polling a
        page would keep opening it - so both say so instead of offering a
        switch that should not be flipped.
        """
        host = QWidget()
        set_style(host, "common", "transparent")
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        if self.runnable.opens_page:
            column.addWidget(self._note(
                "This one answers with a page, so it opens when pressed and "
                "cannot be kept up to date on a timer."))
            self.poll_box = None
            return host
        if self.runnable.danger:
            column.addWidget(self._note(
                "This one changes something when it runs. It can still be "
                "put on a timer, but it will do that every time."))

        self.poll_box = QCheckBox("Run it on a timer, without pressing")
        self.poll_box.setFont(make_font(SIZES.S1))
        self.poll_box.setChecked(bool(self.saved.get("poll")))
        self.poll_box.setStyleSheet(self.CHECK_CSS)
        self.poll_box.stateChanged.connect(self._poll_toggled)
        column.addWidget(self.poll_box)

        self.poll_every = QComboBox()
        for seconds, label in self.POLL_CHOICES:
            self.poll_every.addItem(label, seconds)
        index = self.poll_every.findData(
            int(self.saved.get("poll_seconds") or 30))
        self.poll_every.setCurrentIndex(index if index >= 0 else 1)
        self.poll_every.setFixedHeight(36)
        self.poll_every.setFont(make_font(SIZES.S1))
        self.poll_every.setStyleSheet(get_style_sheet("settings_combobox"))
        column.addWidget(self.poll_every)
        self._poll_toggled()
        return host

    def _note(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(make_font(SIZES.S1))
        label.setWordWrap(True)
        label.setStyleSheet("color:#e8c35a;background:transparent;")
        return label

    def _poll_toggled(self) -> None:
        if self.poll_box is not None:
            self.poll_every.setEnabled(self.poll_box.isChecked())

    def _heading(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(make_font(SIZES.S1, bold=True))
        set_style(label, "common", "text-muted")
        return label

    def _summary(self) -> QWidget:
        """What was chosen, and where it came from."""
        card = QFrame()
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(card, "settings", "setting-block")

        column = QVBoxLayout(card)
        column.setContentsMargins(14, 12, 14, 12)
        column.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.preview = QLabel()
        self.preview.setFixedSize(44, 44)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.preview, "common", "transparent")
        top.addWidget(self.preview)

        # Pressed to rename. What a tile is called is the one thing on it a
        # person reads from across a room, and the registered name is rarely
        # what they would call it.
        self.name_button = QPushButton(self.label_text)
        self.name_button.setFont(make_font(SIZES.S3, bold=True))
        self.name_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.name_button.setStyleSheet(self.NAME_CSS)
        self.name_button.clicked.connect(self._rename)
        top.addWidget(self.name_button, stretch=1)
        column.addLayout(top)

        where = QLabel(self.runnable.source)
        where.setFont(make_font(SIZES.S1))
        where.setWordWrap(True)
        set_style(where, "common", "text-muted")
        column.addWidget(where)

        # Which registry, in a sentence. The badge in the list is one word and
        # the icon is a shape; neither says what the difference between them
        # actually is, and that difference decides whether a browser can reach
        # this at all.
        kind = QLabel(action_sources.about(self.runnable.kind))
        kind.setFont(make_font(SIZES.S1))
        kind.setWordWrap(True)
        kind.setStyleSheet("color:#6fa8e0;background:transparent;")
        column.addWidget(kind)

        if self.runnable.description:
            about = QLabel(self.runnable.description)
            about.setFont(make_font(SIZES.S1))
            about.setWordWrap(True)
            set_style(about, "common", "text-muted")
            column.addWidget(about)

        # Said plainly, because it is the difference between a tile that
        # shows something and one that only does something.
        if self.runnable.opens_page:
            note = "Answers with a page. Pressing the tile opens it."
        else:
            note = "Answers with data, if anything."
        if self.runnable.danger:
            note += "  This one changes something when it runs."
        told = QLabel(note)
        told.setFont(make_font(SIZES.S1))
        told.setWordWrap(True)
        told.setStyleSheet("color:#e8c35a;background:transparent;")
        column.addWidget(told)

        self._draw_preview()
        return card

    def _rename(self) -> None:
        """Call it whatever it is for, rather than whatever it is called."""
        holder = _Field(self.label_text, self._took_name)
        self.client.dialog(make_keyboard(
            self.client, holder, "text", label="What is this tile called?",
            description="Shown on the tile itself."))

    def _took_name(self, text: str) -> None:
        self.label_text = str(text or "").strip() or self.runnable.label
        self.name_button.setText(self.label_text)

    def _draw_preview(self) -> None:
        try:
            self.preview.setPixmap(
                icon(self.chosen_icon, color=self.chosen_colour).pixmap(34, 34))
        except Exception:
            pass

    #How many swatches fit across the left pane. Eight at their own size
    #needs 458px and the pane is 380 - they were simply running off the edge.
    COLOUR_COLUMNS = 6

    def _colours(self) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        self.swatches = []
        for index, colour in enumerate(COLOURS):
            swatch = Swatch(colour, colour == self.chosen_colour,
                            self._pick_colour)
            grid.addWidget(swatch, index // self.COLOUR_COLUMNS,
                           index % self.COLOUR_COLUMNS)
            self.swatches.append(swatch)
        grid.setColumnStretch(self.COLOUR_COLUMNS, 1)
        return host

    def _pick_colour(self, colour: str) -> None:
        self.chosen_colour = colour
        for swatch in self.swatches:
            swatch.chosen = swatch.colour == colour
            swatch.update()
        self._draw_preview()

    def _icons(self) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        self.glyphs = {}
        columns = 6
        for index, name in enumerate(ICONS):
            # Sized for a finger, not a cursor. A wall panel is pointed at.
            button = IconButton(name, lambda n=name: self._pick_icon(n),
                                size=26)
            grid.addWidget(button, index // columns, index % columns)
            self.glyphs[name] = button
        grid.setColumnStretch(columns, 1)
        return host

    def _pick_icon(self, name: str) -> None:
        self.chosen_icon = name
        self._draw_preview()

    ## -- right: what it is called with

    def _middle(self, saved: dict) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")

        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)

        title = QLabel("Arguments")
        title.setFont(make_font(SIZES.S2, bold=True))
        set_style(title, "common", "text-strong")
        column.addWidget(title)

        blurb = QLabel(
            "Filled in from what the function declared. A row left at its "
            "default is passed as that default.")
        blurb.setFont(make_font(SIZES.S1))
        blurb.setWordWrap(True)
        set_style(blurb, "common", "text-muted")
        column.addWidget(blurb)

        self.arguments = ArgumentList(
            self.client,
            arguments=self.runnable.to_dict().get("arguments", []),
            values=saved.get("values") or {},
        )
        self.arguments.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Expanding)
        column.addWidget(self.arguments, stretch=1)

        rules_title = QLabel("How the tile should look")
        rules_title.setFont(make_font(SIZES.S2, bold=True))
        set_style(rules_title, "common", "text-strong")
        column.addWidget(rules_title)

        self.rules = RuleList(self.client, saved.get("rules") or [])
        self.rules.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Expanding)
        column.addWidget(self.rules, stretch=1)

        # Dragged rather than only scrolled. Every list on this panel is
        # touched, and a scrollbar six pixels wide is not a handle.
        for scroll in self.arguments.findChildren(QScrollArea):
            style_scrollbar(scroll)
            try:
                QScroller.grabGesture(
                    scroll.viewport(),
                    QScroller.ScrollerGestureType.LeftMouseButtonGesture)
            except Exception:
                pass
        return host

    ## -- trying it

    def _right(self, saved: dict) -> QWidget:
        """
        Run it, and show what came back with room to read it.

        Its own column rather than a strip under the arguments. The answer is
        the thing being studied while the rules above it are written - a path
        is chosen by looking at the shape of what came back - and ninety
        pixels of it was a keyhole.

        There is no dry run and this does not pretend otherwise. Everything is
        written to the tile before the button is pressed, so a test that
        restarts the panel is recoverable rather than lost work.
        """
        host = QWidget()
        if self.stacked:
            # Under the middle now, so it takes the width rather than
            # claiming a column's worth of it.
            host.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Expanding)
        else:
            host.setFixedWidth(self._share(self.ANSWER_WIDTH))
        set_style(host, "common", "transparent")

        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)

        title = QLabel("What it answers with")
        title.setFont(make_font(SIZES.S2, bold=True))
        set_style(title, "common", "text-strong")
        column.addWidget(title)

        self.try_button = ActionButton(
            "mdi.play", "Try it for real", self._try, kind="secondary")
        column.addWidget(self.try_button)

        self.verdict = QLabel("Nothing tried yet.")
        self.verdict.setFont(make_font(SIZES.S1))
        self.verdict.setWordWrap(True)
        set_style(self.verdict, "common", "text-muted")
        column.addWidget(self.verdict)

        # The answer, with the column to itself. Monospaced, because what is
        # being read here is a shape - which keys sit inside which - and that
        # is what proportional text hides.
        self.answer = QLabel("")
        # A fixed-width family, by name: make_font takes `family`, not a
        # `mono` flag. What is being read here is a shape - which keys sit
        # inside which - and proportional text is what hides that.
        self.answer.setFont(make_font(SIZES.S1, family="monospace"))
        self.answer.setWordWrap(False)
        self.answer.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.answer.setAlignment(Qt.AlignmentFlag.AlignTop
                                 | Qt.AlignmentFlag.AlignLeft)
        self.answer.setStyleSheet(
            "color: rgba(232,236,244,190); background: transparent;"
            " padding: 8px;")

        self.answer_scroll = QScrollArea()
        self.answer_scroll.setWidgetResizable(True)
        self.answer_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.answer_scroll.setSizePolicy(QSizePolicy.Policy.Expanding,
                                         QSizePolicy.Policy.Expanding)
        self.answer_scroll.setStyleSheet(
            "QScrollArea { background: rgba(0,0,0,60);"
            " border: 1px solid rgba(255,255,255,20); border-radius: 10px; }")
        self.answer_scroll.setWidget(self.answer)
        style_scrollbar(self.answer_scroll)
        for viewport in (self.answer_scroll.viewport(),):
            try:
                QScroller.grabGesture(
                    viewport,
                    QScroller.ScrollerGestureType.LeftMouseButtonGesture)
            except Exception:
                pass
        column.addWidget(self.answer_scroll, stretch=1)

        # What to read out of it. Under the answer, because it is chosen by
        # looking at what is above it.
        caption = QLabel("Which part the rules look at")
        caption.setFont(make_font(SIZES.S1, bold=True))
        set_style(caption, "common", "text-muted")
        column.addWidget(caption)

        # The button holds the PATH and nothing else. It used to carry the
        # path, the value and the state all at once - so with no path chosen
        # it printed the whole answer across a control, and what pressing it
        # would do was anybody's guess.
        self.chosen_path = str(saved.get("path") or "")
        self.path_button = QPushButton()
        self.path_button.setFont(make_font(SIZES.S1))
        self.path_button.setFixedHeight(40)
        self.path_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.path_button.setStyleSheet(self.PATH_CSS)
        self.path_button.clicked.connect(self._pick_path)
        column.addWidget(self.path_button)

        # What it resolves to right now, which is a reading rather than a
        # control and belongs under one rather than inside it.
        self.path_value = QLabel("")
        self.path_value.setFont(make_font(SIZES.S1))
        self.path_value.setWordWrap(True)
        set_style(self.path_value, "common", "text-muted")
        column.addWidget(self.path_value)

        #The last answer, so a path can be resolved against it without
        #running the thing again. Set before the first read of it, which is
        #the line below.
        self.last = None
        self._show_path_value()
        return host

    def _try(self) -> None:
        # Saved first, always. Running this can open a page, restart the
        # panel or take the screen somewhere else, and the work put into this
        # dialog should still be here afterwards.
        self._remember()

        self.verdict.setText("Running\u2026")
        result = action_runner.run(self.client, self._action())
        self.last = result

        self.verdict.setText(result.describe())
        self.verdict.setStyleSheet(
            "background: transparent; color: "
            + ("#e0855a" if not result.ok else "#3ec08a") + ";")

        self.answer.setText(result.preview or "")
        self.answer_scroll.setVisible(bool(result.preview))
        self._show_path_value()

        # Handed on, so "suggest" has something real to suggest from rather
        # than a guess about the shape.
        rules = getattr(self, "rules", None)
        if rules is not None:
            rules.answer = result.value if result.ok else None
            rules.answer_path = self.chosen_path

    def _pick_path(self) -> None:
        """Pick something to read out of the last answer."""
        if self.last is None or self.last.kind != action_runner.GOT_DATA:
            self.client.simple_notify(
                "mdi.information-outline", "Action",
                "Try it first - the paths come from what it answered with.")
            return

        offered = action_runner.paths_in(self.last.value)
        items = [GridItem(key="", label="The whole answer",
                          subtitle="However it came back",
                          icon="mdi.code-braces")]
        for path in offered:
            found, value = action_runner.follow(self.last.value, path)
            items.append(GridItem(
                key=path, label=path,
                subtitle=_one_line(value) if found else "\u2014",
                icon="mdi.subdirectory-arrow-right"))

        self.client.dialog(ItemGridDialog(
            self.client, title="What should the tile read?",
            body="Taken from what it just answered with. The tile shows this "
                 "value, and treats it as on or off for its own state.",
            items=items, layout="list", on_chosen=self._took_path,
            choose_text="Read this", search_hint="Search the answer"))

    def _took_path(self, item) -> None:
        self.chosen_path = item.key
        self.path_button.setText(self.chosen_path or "the whole answer")
        self._show_path_value()

    def _show_path_value(self) -> None:
        """The chosen path on the button, and what it resolves to under it."""
        self.path_button.setText(
            f"  {self.chosen_path}" if self.chosen_path
            else "  Choose a part of the answer…")

        if self.last is None or not self.last.ok:
            self.path_value.setText(
                "Try it above, then pick what the rules should read. "
                "Left alone they read the whole answer.")
            return

        found, value = action_runner.follow(self.last.value, self.chosen_path)
        if not found:
            self.path_value.setText("Not in that answer.")
            return
        state = "on" if action_runner.as_state(value) else "off"
        self.path_value.setText(
            f"= {_one_line(value)}   ·   reads {state}")

    ## -- leaving

    def _action(self) -> dict:
        """Everything the tile needs, as it stands right now."""
        action = self.runnable.to_dict()
        action.update({
            "label": self.label_text,
            "icon": self.chosen_icon,
            "colour": self.chosen_colour,
            "values": self.arguments.values(),
            "path": getattr(self, "chosen_path", ""),
            "rules": self.rules.values() if hasattr(self, "rules") else [],
            "poll": bool(self.poll_box.isChecked()) if self.poll_box else False,
            "poll_seconds": (self.poll_every.currentData()
                             if self.poll_box else 30),
        })
        return action

    def _remember(self) -> None:
        """
        Hand what is here back to the tile without closing.

        Called before a test rather than only on Save. Running an action can
        open a page, restart the panel, or take the screen somewhere else -
        and the answer to that should be "come back and carry on", not "start
        again".
        """
        if callable(self.on_saved):
            try:
                self.on_saved(self._action())
            except Exception as e:
                self.client.log("warning",
                                f"[Action] Could not save before testing: {e}")

    def _choose_again(self) -> None:
        """
        Back to the list of things to run.

        What is here is written first, so a look at the alternatives and a
        change of mind both leave the tile as it was rather than empty.
        """
        self._remember()
        self.close()
        if callable(self.on_rechoose):
            self.on_rechoose()

    def _save(self) -> None:
        action = self._action()
        self.close()
        if callable(self.on_saved):
            self.on_saved(action)
