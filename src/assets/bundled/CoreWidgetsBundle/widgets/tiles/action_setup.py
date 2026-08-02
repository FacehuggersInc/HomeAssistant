"""
Setting up what an action tile runs.

A setup panel on the left - what was chosen and how the tile looks, read
rather than worked in - and beside it three tabs: the arguments, the rules,
and what the thing answers with.

Tabs rather than columns. Three panes side by side needed about 1580px to
hold their minimums, and the dialog is clamped to the screen; on a smaller one
they were shrunk past the point of being usable and overlapped. Shrinking
things only moves where each becomes useless. One pane at a time is a pane
that always has the room, on any screen, with no breakpoint to get wrong.
"""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame,
    QScrollArea, QSizePolicy, QScroller, QPushButton, QCheckBox,
    QComboBox, QStackedWidget,
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


class _TappableLabel(QLabel):
    """
    A label that can be pressed, for text that has to wrap.

    A `QPushButton` cannot wrap: it elides, or on a narrow pane simply clips.
    The name of an action is the one thing here somebody reads to know what
    they are setting up, and it is whatever a plugin registered - which is
    routinely longer than the panel beside it.
    """

    def __init__(self, text: str, on_press: Callable):
        super().__init__(text)
        self._on_press = on_press
        self.setWordWrap(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Minimum)

    def mousePressEvent(self, event) -> None:
        if callable(self._on_press):
            self._on_press()
        event.accept()


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

    #Wider and taller than an ordinary dialog, and no longer as wide as it
    #was: 2360 was the width three columns needed, and there are two now.
    WIDTH = 1560
    MAX_HEIGHT = 1400

    #The setup panel. Fixed, because it holds a fixed set of controls and the
    #tabs beside it should get every pixel that is left.
    #
    #There is deliberately no PANE_HEIGHT. Height is whatever the dialog has
    #after its title and buttons, handed over by `expand_content()` - a number
    #here would be a floor the card cannot give up, and a floor is what runs a
    #clamped dialog off the bottom of a screen.
    SIDE_WIDTH = 380

    #How tall this would like to be. A want, not a floor - it is capped by
    #`maximumHeight()`, which the base dialog has already fitted to the
    #screen. The argument list is the reason for the number: a list worth
    #scrolling has to show enough rows that scrolling it is obviously
    #possible.
    WANTED_HEIGHT = 940

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
        QLabel { background: transparent; border: none;
                 color: #f0f0f4; padding: 0; }
        QLabel:hover { color: #6fa8e0; }
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
        # No blurb. Every line of it is height taken from the panes, on the
        # one dialog where the panes are the whole point - and what it had to
        # say belongs on the Preview tab, next to the button that does it.
        super().__init__(client, "Set up this action", "")
        self.runnable = runnable
        self.on_saved = on_saved
        self.on_rechoose = on_rechoose
        self.saved = dict(action or {})
        saved = dict(action or {})

        self.chosen_colour = saved.get("colour") or COLOURS[0]
        self.chosen_icon = saved.get("icon") or runnable.icon
        self.label_text = saved.get("label") or runnable.label

        panes = QHBoxLayout()
        panes.setSpacing(16)
        panes.addWidget(self._left(), stretch=0)
        panes.addWidget(self._tabs(saved), stretch=1)

        holder = QWidget()
        set_style(holder, "common", "transparent")
        holder.setLayout(panes)
        holder.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Expanding)
        self.content.addWidget(holder, stretch=1)

        # The leftover height goes to the panes, and NO minimum is set on
        # them.
        #
        # `expand_content()` drops the base dialog's trailing spacer and
        # gives the stretch to `content`, which is the supported way to say
        # "the content is the point". Claiming the space with a minimum
        # instead - `maximumHeight() - 190`, a guess at the title, buttons and
        # margins - is what overlapped the buttons: when the real chrome is
        # taller than the guess, the minimum is larger than what is left, and
        # Qt honours a minimum over a maximum. The card grows past its own cap
        # and the row at the bottom goes with it.
        self.expand_content()

        # A floor, on the DIALOG rather than on anything inside it, and never
        # above its own maximum.
        #
        # `expand_content()` hands over the leftover height but does not ask
        # for any: `center()` shrinks to the size hint, so a card whose panes
        # can all scroll collapses to a band. `maximumHeight()` is already
        # clamped to the screen, so taking the smaller of the two cannot
        # overflow however small the display is.
        self.setMinimumHeight(min(self.maximumHeight(), self.WANTED_HEIGHT))

        self.add_button("Save", self._save, "primary")
        # A way back to the list without cancelling. Somebody who picked the
        # wrong endpoint should not have to close, find the tile, and start
        # again to reach the one beside it.
        self.add_button("Choose something else", self._choose_again,
                        "secondary")
        self.add_button("Cancel", self.close, "secondary")

    ## -- left: what it is, and how it looks

    #What the tab area must keep. Below this the argument rows stop being
    #usable - a name, a kind, a value and a delete do not fit in less.
    TABS_MIN = 520

    def _share(self, wanted: int) -> int:
        """The setup panel's width, given up to the tabs if there is not room."""
        spare = self.width() - self.TABS_MIN - 48
        if spare >= wanted:
            return int(wanted)
        # Never below half: a panel squeezed past that is a column of clipped
        # controls, and at that point it wants a different layout rather than
        # a narrower one.
        return max(int(wanted * 0.55), int(spare))

    def _left(self) -> QWidget:
        """
        What was chosen, how it looks, and how often it runs.

        Scrolled, because the height of it is not this dialog's to know. The
        summary card is sized by text a plugin wrote - a name, a source, a
        description - and on a long one a fixed column squeezes the swatches
        and the icon grid under it until both clip. Scrolling is what a column
        of unknown height does; the panes beside it keep the width either way.
        """
        inner = QWidget()
        set_style(inner, "common", "transparent")

        column = QVBoxLayout(inner)
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

        host = QScrollArea()
        # Narrowed with the dialog rather than held. The dialog is clamped to
        # the screen, and on a small one a fixed panel plus the tabs' minimum
        # add up to more than there is.
        host.setFixedWidth(self._share(self.SIDE_WIDTH))
        host.setWidgetResizable(True)
        host.setFrameShape(QFrame.Shape.NoFrame)
        host.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # No floor. Giving up height is what a scroll area is for, and a
        # minimum on one is what stops a clamped dialog fitting.
        host.setMinimumHeight(0)
        set_style(host, "common", "transparent")
        host.setWidget(inner)
        self._touchable(host)
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
        self.name_button = _TappableLabel(self.label_text, self._rename)
        self.name_button.setFont(make_font(SIZES.S3, bold=True))
        self.name_button.setStyleSheet(self.NAME_CSS)
        top.addWidget(self.name_button, stretch=1)
        # Top, not centre. The icon is one line tall and the name may be
        # three; centred against it, a long name sits low and the row grows
        # around a 44px square with space either side of it.
        top.setAlignment(self.preview, Qt.AlignmentFlag.AlignTop)
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
        # The card is sized to its text and the text just changed. Without
        # this a longer name is clipped until something else forces a layout.
        self.name_button.adjustSize()

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

    ## -- the tabs

    #A button rather than a QTabWidget tab. Nothing here inherits the platform
    #palette - see action_arguments - and a native tab is a small target on a
    #screen that is touched.
    TAB_ON = """
        QPushButton { background: rgba(255,255,255,26);
                      border: 1px solid rgba(255,255,255,70);
                      border-radius: 10px; color: #f0f0f4; padding: 0 18px; }
    """
    TAB_OFF = """
        QPushButton { background: transparent;
                      border: 1px solid rgba(255,255,255,22);
                      border-radius: 10px; color: rgba(232,236,244,150);
                      padding: 0 18px; }
        QPushButton:hover { color: #e8ecf4;
                            border-color: rgba(255,255,255,50); }
    """

    TABS = ("Arguments", "Rules", "Preview")

    def _tabs(self, saved: dict) -> QWidget:
        """The three working panes, one at a time."""
        host = QWidget()
        set_style(host, "common", "transparent")
        host.setMinimumWidth(self.TABS_MIN)

        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)

        strip = QHBoxLayout()
        strip.setSpacing(8)
        self.tab_buttons = []
        for index, name in enumerate(self.TABS):
            button = QPushButton(name)
            button.setFont(make_font(SIZES.S2, bold=True))
            button.setFixedHeight(52)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            # `index=index`, because a lambda closing over the loop variable
            # reads it when it FIRES - so every button would open the last
            # tab.
            button.clicked.connect(
                lambda _checked=False, index=index: self._show_tab(index))
            strip.addWidget(button, stretch=1)
            self.tab_buttons.append(button)
        column.addLayout(strip)

        self.pages = QStackedWidget()
        set_style(self.pages, "common", "transparent")
        self.pages.addWidget(self._arguments_page(saved))
        self.pages.addWidget(self._rules_page(saved))
        self.pages.addWidget(self._preview_page(saved))
        column.addWidget(self.pages, stretch=1)

        self._show_tab(0)
        return host

    def _show_tab(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for position, button in enumerate(self.tab_buttons):
            button.setStyleSheet(self.TAB_ON if position == index
                                 else self.TAB_OFF)

    def _touchable(self, widget: QWidget) -> None:
        """
        Dragged rather than only scrolled.

        Every list here is touched, and a scrollbar six pixels wide is not a
        handle.

        The widget ITSELF counts. `findChildren` does not include the thing it
        is called on, so handing this a scroll area directly - as the left
        column does - would have styled nothing and grabbed no gesture.
        """
        found = list(widget.findChildren(QScrollArea))
        if isinstance(widget, QScrollArea):
            found.append(widget)
        for scroll in found:
            style_scrollbar(scroll)
            try:
                QScroller.grabGesture(
                    scroll.viewport(),
                    QScroller.ScrollerGestureType.LeftMouseButtonGesture)
            except Exception:
                pass

    def _page(self, title: str, blurb: str = "") -> tuple:
        """A tab page with its heading. Returns (widget, layout)."""
        host = QWidget()
        set_style(host, "common", "transparent")
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)

        heading = QLabel(title)
        heading.setFont(make_font(SIZES.S2, bold=True))
        set_style(heading, "common", "text-strong")
        column.addWidget(heading)

        if blurb:
            note = QLabel(blurb)
            note.setFont(make_font(SIZES.S1))
            note.setWordWrap(True)
            set_style(note, "common", "text-muted")
            column.addWidget(note)
        return host, column

    def _arguments_page(self, saved: dict) -> QWidget:
        host, column = self._page(
            "Arguments",
            "Filled in from what the function declared. A row left at its "
            "default is passed as that default.")

        self.arguments = ArgumentList(
            self.client,
            arguments=self.runnable.to_dict().get("arguments", []),
            values=saved.get("values") or {},
        )
        self.arguments.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Expanding)
        column.addWidget(self.arguments, stretch=1)
        self._touchable(self.arguments)
        return host

    def _rules_page(self, saved: dict) -> QWidget:
        host, column = self._page(
            "How the tile should look",
            "Each rule reads the part of the answer chosen under Preview, and "
            "the first one that matches decides what the tile shows.")

        self.rules = RuleList(self.client, saved.get("rules") or [])
        self.rules.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Expanding)
        column.addWidget(self.rules, stretch=1)
        self._touchable(self.rules)
        return host

    ## -- trying it

    def _preview_page(self, saved: dict) -> QWidget:
        """
        Run it, and show what came back with room to read it.

        There is no dry run and this does not pretend otherwise. Everything is
        written to the tile before the button is pressed, so a test that
        restarts the panel is recoverable rather than lost work.
        """
        host, column = self._page(
            "What it answers with",
            "Run it once, then pick the part of the answer the rules should "
            "read. Not everything answers with something a tile can show - "
            "and if you need an exact behaviour, a plugin of your own will "
            "serve you better than this will.")

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
        # After the widgets are in it, not before: a QScrollArea is not a
        # child of `host` until the layout has taken it, so findChildren from
        # further up this method finds nothing.
        self._touchable(host)
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
