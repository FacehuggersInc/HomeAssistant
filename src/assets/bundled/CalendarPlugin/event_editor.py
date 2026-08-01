from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Callable

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QTextEdit, QPushButton, QSizePolicy, QScrollArea, QScroller,
    QFrame,
)
from PyQt6.QtCore import Qt

from .dialogs import _WideDialog
from src.ui.keyboard import KeyboardDialog
from src.ui.controls.buttons import IconButton
from src.styling import make_font, SIZES, set_style, get_style_sheet

if TYPE_CHECKING:
    from src.main import Client


# A short, opinionated set. An icon picker with six hundred glyphs in it is
# unusable on a touchscreen, and these cover what people actually put on a
# kitchen calendar.
ICON_CHOICES = [
    "mdi.calendar", "mdi.cake-variant", "mdi.airplane", "mdi.stethoscope",
    "mdi.school", "mdi.briefcase", "mdi.silverware-fork-knife", "mdi.music",
    "mdi.car", "mdi.gift", "mdi.heart", "mdi.dumbbell",
    "mdi.movie-open", "mdi.paw", "mdi.tooth", "mdi.hammer-wrench",
]


class _Field(QWidget):
    """
    A labelled value that opens something when tapped.

    `opens` decides what: the on-screen keyboard by default, or one of the
    pickers. A time typed on a keyboard has to be parsed and rejected; a time
    chosen on a stepper cannot be wrong in the first place.
    """

    def __init__(self, client: "Client", label: str, value: str = "",
                 placeholder: str = "", numeric: bool = False,
                 opens: str = "keyboard", editor=None, multiline: bool = False,
                 clearable: bool = False):
        super().__init__()
        self.client    = client
        self.numeric   = numeric
        self.label     = label
        self.opens     = opens
        self.editor    = editor
        self.multiline = multiline
        self.clearable = clearable

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        if multiline:
            row.setAlignment(Qt.AlignmentFlag.AlignTop)

        name = QLabel(label)
        name.setFont(make_font(SIZES.S1))
        # Wrapped and wider rather than a hard 90px. Anything longer than
        # "Location" was silently clipped, which is how a field ends up
        # meaning something different to the person reading it than to the
        # person who named it.
        name.setFixedWidth(132)
        name.setWordWrap(True)
        name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        set_style(name, "common", "text-muted")
        row.addWidget(name)

        if multiline:
            self.edit = QTextEdit()
            self.edit.setPlainText(value)
            self.edit.setPlaceholderText(placeholder)
            # Was 110 while the row around it grew to whatever the dialog had
            # spare, so the section was tall and the box inside it was not.
            self.edit.setMinimumHeight(150)
            self.edit.setSizePolicy(QSizePolicy.Policy.Expanding,
                                    QSizePolicy.Policy.Expanding)
        else:
            self.edit = QLineEdit(value)
            self.edit.setPlaceholderText(placeholder)
            self.edit.setFixedHeight(44)

        self.edit.setFont(make_font(SIZES.S2))
        self.edit.setReadOnly(True)          # typed through the on-screen keyboard
        self.edit.setCursor(Qt.CursorShape.PointingHandCursor)
        # The edit covers the whole row, so it - not the row - is what a tap
        # lands on. Made mouse-transparent so the press reaches _Field, which
        # is what knows which dialog to open.
        self.edit.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        set_style(self.edit, "settings", "body-field")
        row.addWidget(self.edit, stretch=1)

        # Every picker returns a value, so without this there is no way back to
        # empty once one has been chosen - and "no end time" and "does not stop
        # repeating" are both ordinary things to mean.
        self.clear_button = None
        if clearable:
            self.clear_button = IconButton("mdi.close", self.clear, size=18)
            self.clear_button.setToolTip(f"Clear {label.lower()}")
            row.addWidget(self.clear_button)
            self._sync_clear()

    def _sync_clear(self) -> None:
        """The clear button is only there when there is something to clear."""
        if self.clear_button is not None:
            self.clear_button.setVisible(bool(self.value().strip()))

    def clear(self) -> None:
        self.set_value("")

    def mouseReleaseEvent(self, event) -> None:
        if self.opens == "time":
            from .pickers import TimePickerDialog
            floor = ""
            if self.editor is not None:
                floor = self.editor.paired_time(self)
            self.client.dialog(TimePickerDialog(
                self.client, self.value(), title=f"{self.label} time",
                on_chosen=self.set_value, floor=floor))
        elif self.opens == "date":
            from datetime import date
            from .pickers import DatePickerDialog
            current = None
            try:
                current = date.fromisoformat(self.value())
            except ValueError:
                pass
            self.client.dialog(DatePickerDialog(
                self.client, current, on_chosen=lambda d: self.set_value(d.isoformat())))
        elif self.opens == "location":
            from .pickers import LocationPickerDialog
            self.client.dialog(LocationPickerDialog(
                self.client, self.value(), on_chosen=self.set_value))
        else:
            self._open_keyboard()

    def set_value(self, value) -> None:
        text = str(value or "")
        if self.multiline:
            self.edit.setPlainText(text)
        else:
            self.edit.setText(text)
        self._sync_clear()
        if self.editor is not None and hasattr(self.editor, "on_field_changed"):
            self.editor.on_field_changed()

    def _open_keyboard(self) -> None:
        self.client.dialog(KeyboardDialog(
            self.client, self.edit,
            mode="numeric" if self.numeric else "text",
            label=self.label,
        ))

    def value(self) -> str:
        if self.multiline:
            return self.edit.toPlainText().strip()
        return self.edit.text().strip()


class IconPicker(QWidget):
    """A small grid of glyphs, one selected."""

    #How many fit across the editor. Eight 48px buttons and their spacing is
    #426px, comfortably inside the 732 the dialog has between its margins, and
    #two rows of eight reads better than one row of sixteen.
    COLUMNS = 8

    def __init__(self, chosen: str = "mdi.calendar"):
        super().__init__()
        self.chosen = chosen
        self.glyphs: dict = {}

        wrap = QVBoxLayout(self)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setSpacing(6)

        label = QLabel("Icon")
        label.setFont(make_font(SIZES.S1))
        set_style(label, "common", "text-muted")
        wrap.addWidget(label)

        # A grid that wraps, not one long row.
        #
        # Sixteen 48px targets with 6px between them is 858px; the editor is
        # 780 wide and 732 inside its margins, so the last two were cut off the
        # right-hand edge. Wrapping fits any width and costs one extra row of
        # height, which the dialog has.
        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setContentsMargins(0, 0, 0, 0)
        from src.ui.controls.buttons import IconButton
        for index, name in enumerate(ICON_CHOICES):
            # 26, not 15. These are picked with a finger on a wall panel, and
            # sixteen 30px targets in a row is a lottery.
            button = IconButton(name, lambda n=name: self.choose(n), size=26)
            self.glyphs[name] = button
            grid.addWidget(button, index // self.COLUMNS,
                           index % self.COLUMNS)
        # The trailing column takes the slack, so a part-filled last row lines
        # up under the one above rather than spreading out.
        grid.setColumnStretch(self.COLUMNS, 1)
        wrap.addLayout(grid)
        self.choose(chosen)

    def choose(self, name: str) -> None:
        self.chosen = name
        for key, button in self.glyphs.items():
            button.update_icon(key, "#2ff08e" if key == name else "white")


EVENT_COLOURS = [
    "", "#4f9de0", "#3778c2", "#3fa86a", "#2f8f6a", "#d8a24a", "#c07a2a",
    "#d05f5f", "#a63d3d", "#a97fe0", "#7b56b8", "#43b0b0", "#2f8f8f",
    "#c96fa0", "#9c4a78", "#8a8f99", "#5c6270", "#e0d24f",
]

# Remembered across dialogs so a household that colours everything the same
# way does not re-pick it every time. Not a setting - it is a convenience, and
# a stale one costs a single tap.
_LAST_COLOUR = {"value": ""}


def _swatch_style(colour: str, selected: bool) -> str:
    border = "#2ff08e" if selected else "rgba(255,255,255,45)"
    fill = colour or "rgba(255,255,255,16)"
    return (f"background:{fill};border:3px solid {border};"
            f"border-radius:10px;color:#f2f2f2;")


class ColourPickerDialog(_WideDialog):
    """The full set, as a grid. Too many to sit in a row on the editor."""

    WIDTH_RATIO  = 0.44
    HEIGHT_RATIO = 0.0
    MIN_WIDTH    = 420

    def __init__(self, client: "Client", chosen: str = "", on_chosen=None):
        super().__init__(client, "Event colour",
                         "Auto uses the colour for its kind.")
        self.chosen = chosen
        self.on_chosen = on_chosen
        # NOT self.buttons. BaseDialog already owns that name for the layout
        # its add_button() appends to, and shadowing it with a dict turns the
        # next add_button call into an AttributeError.
        self.swatches: dict = {}

        grid = QGridLayout()
        grid.setSpacing(8)
        for index, value in enumerate(EVENT_COLOURS):
            button = QPushButton("Auto" if not value else "")
            button.setFixedSize(96, 60)
            button.setFont(make_font(SIZES.S1, bold=True))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, v=value: self._pick(v))
            self.swatches[value] = button
            grid.addWidget(button, index // 6, index % 6)

        holder = QWidget()
        set_style(holder, "common", "transparent")
        holder.setLayout(grid)
        self.content.addWidget(holder, alignment=Qt.AlignmentFlag.AlignCenter)

        self._restyle()
        self.add_button("Cancel", self.close, "secondary")

    def _restyle(self) -> None:
        for key, button in self.swatches.items():
            button.setStyleSheet(_swatch_style(key, key == self.chosen))

    def _pick(self, value: str) -> None:
        self.chosen = value
        _LAST_COLOUR["value"] = value
        self._restyle()
        if callable(self.on_chosen):
            self.on_chosen(value)
        self.close()


class ColourPicker(QWidget):
    """One swatch on the editor. Tapping it opens the full grid."""

    def __init__(self, client: "Client", chosen: str = ""):
        super().__init__()
        self.client = client
        self.chosen = chosen or _LAST_COLOUR["value"]

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        label = QLabel("Colour")
        label.setFont(make_font(SIZES.S1))
        label.setFixedWidth(90)
        set_style(label, "common", "text-muted")
        row.addWidget(label)

        self.swatch = QPushButton("")
        self.swatch.setFixedSize(120, 44)
        self.swatch.setFont(make_font(SIZES.S1, bold=True))
        self.swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.swatch.clicked.connect(self._open)
        row.addWidget(self.swatch)
        row.addStretch()
        self._show()

    def _show(self) -> None:
        self.swatch.setText("Auto" if not self.chosen else "")
        self.swatch.setStyleSheet(_swatch_style(self.chosen, True))

    def _open(self) -> None:
        self.client.dialog(ColourPickerDialog(
            self.client, self.chosen, on_chosen=self._chose))

    def _chose(self, value: str) -> None:
        self.chosen = value
        self._show()


REPEATS = [("", "Once"), ("daily", "Daily"), ("weekly", "Weekly"),
           ("monthly", "Monthly"), ("yearly", "Yearly")]


class _Chooser(QWidget):
    """A labelled row of mutually exclusive buttons."""

    def __init__(self, label: str, options: list, chosen: str = ""):
        super().__init__()
        self.chosen = chosen
        self.buttons: dict = {}

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        name = QLabel(label)
        name.setFont(make_font(SIZES.S1))
        # Matches _Field's label column, so the value side of every row in the
        # dialog starts at the same x.
        name.setFixedWidth(132)
        name.setWordWrap(True)
        set_style(name, "common", "text-muted")
        row.addWidget(name)

        for value, text in options:
            button = QPushButton(text)
            button.setFont(make_font(SIZES.S1, bold=True))
            button.setFixedHeight(42)
            button.setMinimumWidth(84)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, v=value: self.choose(v))
            self.buttons[value] = button
            row.addWidget(button)
        row.addStretch()
        self.choose(chosen)

    def choose(self, value: str) -> None:
        self.chosen = value
        for key, button in self.buttons.items():
            set_style(button, "overlays",
                      "dialog-button-primary" if key == value
                      else "dialog-button-secondary")


class _OwnerPicker(QWidget):
    """
    Whose event this is, chosen rather than typed.

    Same reasoning as the form: three spellings of one name is three people as
    far as the store is concerned, and every one of them owns some of the
    events.
    """

    def __init__(self, client: "Client", chosen: str = ""):
        super().__init__()
        self.client = client
        self.chosen = chosen
        self.buttons: dict = {}

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        label = QLabel("For")
        label.setFont(make_font(SIZES.S1))
        label.setFixedWidth(90)
        set_style(label, "common", "text-muted")
        row.addWidget(label)

        names = []
        try:
            names = client.USERS.names()
        except Exception:
            pass

        if not names:
            empty = QLabel("Nobody named yet - approve a device first.")
            empty.setFont(make_font(SIZES.S1))
            empty.setWordWrap(True)
            set_style(empty, "common", "text-muted")
            row.addWidget(empty, stretch=1)
            return

        for name in names:
            button = QPushButton(name)
            button.setFont(make_font(SIZES.S1, bold=True))
            button.setFixedHeight(42)
            button.setMinimumWidth(96)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, n=name: self.choose(n))
            self.buttons[name] = button
            row.addWidget(button)
        row.addStretch()

        self.choose(chosen if chosen in names else names[0])

    def choose(self, name: str) -> None:
        self.chosen = name
        for key, button in self.buttons.items():
            set_style(button, "overlays",
                      "dialog-button-primary" if key == name
                      else "dialog-button-secondary")


class EventEditorDialog(_WideDialog):
    """Add an event. Day comes in from wherever it was opened."""

    WIDTH = 780

    def __init__(self, client: "Client", day: date = None,
                 on_saved: Callable = None, event=None):
        # One dialog for both. An edit form that is a separate class from the
        # add form drifts apart field by field.
        #
        # An occurrence carries no recurrence of its own - occurrence_on()
        # clears `repeat` and `repeat_until` so a generated copy cannot be
        # mistaken for a series. Editing one therefore has to load the STORED
        # event behind it, or the repeat fields render empty and saving writes
        # that emptiness back over the series, silently destroying the
        # recurrence.
        self.editing_series = False
        if event is not None and getattr(event, "series_key", ""):
            try:
                series = client.public.calendar["get_event"](event.series_key)
            except Exception:
                series = None
            if series is not None:
                event = series
                self.editing_series = True

        self.event = event
        if event is not None:
            try:
                # The series' own start, not the occurrence that was tapped -
                # saving with the occurrence's date in the field would move the
                # whole series to that day.
                day = date.fromisoformat(event.day)
            except (ValueError, TypeError):
                day = day or date.today()
        day = day or date.today()

        super().__init__(client,
                         "Edit event" if event is not None else "New event",
                         day.strftime("%A %d %B %Y"))
        self.day      = day
        self.on_saved = on_saved

        body = QVBoxLayout()
        body.setSpacing(10)

        if self.editing_series:
            note = QLabel("This event repeats — changes here apply to every "
                          "occurrence, and the date below is when the series "
                          "starts.")
            note.setFont(make_font(SIZES.S1))
            note.setWordWrap(True)
            set_style(note, "common", "text-muted")
            body.addWidget(note)

        existing = event
        self.title_field    = _Field(client, "Title",
                                     value=(existing.title if existing else ""),
                                     placeholder="What is it?", editor=self)
        self.date_field     = _Field(client, "Date", value=day.isoformat(),
                                     opens="date", editor=self)
        self.time_field     = _Field(client, "Start",
                                     value=(existing.time if existing else ""),
                                     placeholder="Tap to choose, or leave for all day",
                                     opens="time", editor=self, clearable=True)
        self.end_field      = _Field(client, "End",
                                     value=(existing.end_time if existing else ""),
                                     placeholder="Optional",
                                     opens="time", editor=self, clearable=True)
        # A new event starts at the default; an existing one keeps its own,
        # including an intentionally empty one.
        default_location = ""
        if existing is None:
            try:
                default_location = client.public.calendar["option"](
                    "general.default_location", "") or ""
            except Exception:
                default_location = ""

        self.location_field = _Field(client, "Location",
                                     value=(existing.location if existing
                                            else default_location),
                                     placeholder="Tap to search a map",
                                     opens="location", editor=self, clearable=True)
        # A body field, so the keyboard opens in its multi-line layout and a
        # note longer than one line is actually readable back.
        self.notes_field    = _Field(client, "Notes",
                                     value=(existing.notes if existing else ""),
                                     placeholder="Optional",
                                     editor=self, multiline=True)

        for field in (self.title_field, self.date_field, self.time_field,
                      self.end_field, self.location_field, self.notes_field):
            body.addWidget(field)

        # Repeat and the last day sit together: both answer "how long does
        # this go on for", and separating them made the editor read as two
        # unrelated halves.
        self.repeat = _Chooser("Repeats", REPEATS,
                               existing.repeat if existing else "")
        body.addWidget(self.repeat)

        # These two are adjacent and both take a date, which is exactly how a
        # series-end date ends up in the span field. The caption carries the
        # distinction rather than the labels, which have a fixed column to fit
        # in and were being clipped.
        caption = QLabel("'Last day' is how long ONE occurrence runs. "
                         "'Stop repeating' is when the series ends.")
        caption.setFont(make_font(SIZES.S1))
        caption.setWordWrap(True)
        set_style(caption, "common", "text-muted")
        body.addWidget(caption)

        self.end_day_field = _Field(client, "Last day",
                                    value=(existing.end_day if existing else ""),
                                    placeholder="Same day - tap only if it runs over several days",
                                    opens="date", editor=self, clearable=True)
        body.addWidget(self.end_day_field)

        self.until_field = _Field(client, "Stop repeating",
                                  value=(existing.repeat_until if existing else ""),
                                  placeholder="Forever - tap to end the series",
                                  opens="date", editor=self, clearable=True)
        body.addWidget(self.until_field)

        self.icons = IconPicker(existing.icon if existing else "mdi.calendar")
        body.addWidget(self.icons)

        self.colours = ColourPicker(client, existing.colour if existing else "")
        self.owner = _OwnerPicker(client, existing.owner if existing else "")
        body.addWidget(self.owner)
        body.addWidget(self.colours)

        holder = QWidget()
        set_style(holder, "common", "transparent")
        holder.setLayout(body)

        # Scrolled, because the dialog has a ceiling and this does not.
        #
        # A dialog is capped at a share of the screen; the form is a dozen
        # fields, a two-row icon grid and a colour grid, and on a shorter
        # display that is more than the cap. Without somewhere to scroll, the
        # excess is simply cut off - and what sits near the bottom is the icon
        # row, which is exactly what was disappearing.
        scroll = QScrollArea()
        scroll.setStyleSheet(get_style_sheet("scrollbar"))
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        set_style(scroll, "common", "transparent")
        set_style(scroll.viewport(), "common", "transparent")
        # The same drag-to-scroll as everywhere else on this panel.
        QScroller.grabGesture(
            scroll.viewport(),
            QScroller.ScrollerGestureType.LeftMouseButtonGesture)
        self.content.addWidget(scroll, stretch=1)

        # Above the buttons and in red. It was a muted line at the bottom of a
        # tall dialog, which is indistinguishable from nothing happening.
        self.error = QLabel("")
        self.error.setFont(make_font(SIZES.S2, bold=True))
        self.error.setWordWrap(True)
        self.error.setStyleSheet(
            "color:#f0a0a0;background:rgba(176,52,52,60);"
            "border:1px solid rgba(224,138,138,120);border-radius:8px;padding:8px 12px;")
        self.error.hide()
        self.content.addWidget(self.error)

        self.add_button("Save", self._save, "primary")
        self.add_button("Cancel", self.close, "secondary")

    def _fields(self) -> dict:
        start = _clean_clock(self.time_field.value())
        end   = _clean_clock(self.end_field.value())
        return {
            "title":    self.title_field.value(),
            "time":     start or "",
            "end_time": end or "",
            "location": self.location_field.value(),
            "notes":    self.notes_field.value(),
            "icon":     self.icons.chosen,
            "colour":   self.colours.chosen,
            "owner":    self.owner.chosen,
            "end_day":  self.end_day_field.value(),
            "repeat":   self.repeat.chosen,
            "repeat_until": self.until_field.value() if self.repeat.chosen else "",
        }

    def paired_time(self, field) -> str:
        """
        The other half of the pair, so a picker opens near it.

        Choosing an end time of 00:00 when the event starts at 14:30 is never
        what anyone wants, and it is a dozen taps back from there.
        """
        if field is self.end_field:
            return self.time_field.value()
        if field is self.time_field:
            return self.end_field.value()
        return ""

    def _complain(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()
        # Notified as well as shown - on a tall dialog the label can be below
        # where the eye is, and a toast is where the user is already looking.
        try:
            self.client.simple_notify("alert", "Calendar", message)
        except Exception:
            pass

    def on_field_changed(self) -> None:
        self.error.hide()

    def _save(self) -> None:
        title = self.title_field.value()
        if not title:
            # Refused rather than saved as "Untitled" - an event with no name
            # is indistinguishable from every other one on the grid.
            self._complain("An event needs a title. Tap the Title field to add one.")
            return

        start = _clean_clock(self.time_field.value())
        end   = _clean_clock(self.end_field.value())
        if self.time_field.value() and start is None:
            self._complain("That start time is not a valid time of day.")
            return

        from datetime import date as _date
        try:
            chosen_day = _date.fromisoformat(self.date_field.value())
        except ValueError:
            chosen_day = self.day

        fields = self._fields()
        fields["day"] = chosen_day.isoformat()

        # A range that ends before it starts is a typo, not an intention.
        if fields["end_day"]:
            try:
                if _date.fromisoformat(fields["end_day"]) < chosen_day:
                    self._complain("The last day is before the first one.")
                    return
            except ValueError:
                fields["end_day"] = ""

        # "Last day" and "Stop repeating" sit next to each other and answer
        # different questions: how long ONE occurrence runs, and when the
        # SERIES stops. Putting the series' finishing date in "Last day" turns
        # every occurrence into a span that long, so a weekly event draws
        # overlapping month-long bars across every day of the calendar and the
        # count climbs the further ahead you look. Refused rather than
        # silently reinterpreted - only the person entering it knows which
        # they meant.
        if fields["end_day"] and fields["repeat"]:
            try:
                span_days = (_date.fromisoformat(fields["end_day"]) - chosen_day).days
            except ValueError:
                span_days = 0
            gap = _repeat_gap_days(fields["repeat"],
                                   int(fields.get("repeat_interval") or 1))
            if gap and span_days >= gap:
                self._complain(
                    f"This repeats {fields['repeat']}, but 'Last day' makes each "
                    f"occurrence {span_days + 1} days long - so they would run into "
                    f"each other.\n\n"
                    f"'Last day' is for one occurrence that spans several days. "
                    f"To stop the series on a date, clear it with the x beside "
                    f"it and use 'Stop repeating' instead."
                )
                return

        try:
            api = self.client.public.calendar
            if self.event is not None:
                api["update_event"](self.event.key, **fields)
                self.client.trigger_on_call_event_iteration(
                    "on_calendar_changed", self.event)
            else:
                api["add_event"](**fields)
        except Exception as e:
            self.client.log("warning", f"[Calendar] Could not add event: {e}")
            self._complain("Could not save that event.")
            return

        if callable(self.on_saved):
            try:
                self.on_saved()
            except Exception:
                pass
        self.close()


def _repeat_gap_days(repeat: str, interval: int = 1) -> int:
    """
    Days between one occurrence and the next, or 0 for a rule with no gap.

    Approximate for monthly and yearly on purpose - it is used to catch a span
    that is obviously longer than its own recurrence, and the shortest month
    is the safe bound to compare against.
    """
    interval = max(1, int(interval or 1))
    return {"daily": 1, "weekly": 7, "monthly": 28, "yearly": 365}.get(
        repeat, 0) * interval


def _clean_clock(text: str):
    """'830' and '8:30' both mean half past eight; anything else is refused."""
    text = (text or "").strip()
    if not text:
        return ""
    digits = text.replace(":", "")
    if not digits.isdigit() or len(digits) not in (3, 4):
        return None
    hour, minute = int(digits[:-2]), int(digits[-2:])
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"
