# Stickers on the calendar

A sticker stuck to a day box, rather than to the page. It comes back on the
same day when the month is paged away and back, and after a restart.

Press the sticker button in the calendar's header and pick one from the
library. It follows your finger over the month, with the day it would land on
outlined as it moves. **Done** puts it there and asks whether it belongs to an
event.

Nothing is written until Done. A sticker dropped onto today the moment it is
chosen has been placed by the application rather than by anybody, and the
question about the event is then a question about a choice nobody made.

## Arranging them

The sticker layer covers the whole page, so while it is taking the mouse
nothing underneath it receives any - not the toolbar, not a day box. It is
therefore transparent to the mouse until it is asked for: adding a sticker
turns it on, and so does the move button beside the add button.

**Done**, at the top of the page, turns it off again. The control is on the
layer rather than in the toolbar because the toolbar is underneath and cannot
be pressed while the layer is up.

## What it can be stuck to

| Stuck to                                    | Comes back on                               |
|---------------------------------------------|---------------------------------------------|
| a day                                       | that date, and only that one                |
| a holiday                                   | that holiday, every year, wherever it falls |
| an event that repeats yearly and never ends | that day, every year                        |
| an event running across several days        | the last of those days                      |
| any other event                             | wherever that event is                      |

A holiday is its own case because holidays are computed rather than stored: the
key carries the year, so following the key follows one year only. Many of them
move as well - Labor Day is the first Monday in September - so a month-and-day
anchor lands a day or two out. A sticker on one follows the holiday's name and
asks where it falls in whichever year is on screen.

A sticker that has been put down is **held**: it moves freely inside its day
box and does not leave it. A sticker belongs to a day, and letting a drag carry
it into the next box turns a decision somebody made into an accident of where
their finger stopped.

One following an event is held for a second reason: the event decides which day
that is, so dragging it elsewhere would be a claim the event contradicts on its
next occurrence.

Four controls appear on a selected sticker, and two of them are on the one
being placed as well.

| Control     | Does       |
|-------------|------------|
| right of it | Sizes it   |
| left of it  | Turns it   |
| top right   | Removes it |
| top left    | Frees it   |

The size and turn controls sit **outside** the picture rather than on its
corners. A sticker in a day box is about forty pixels across and a control is
thirty-four, so two of them centred on its corners cover the whole thing -
leaving nothing to press that is the sticker itself, and no way to drag it.

Sizing and turning are available while placing, because a sticker is sized and
angled as it is put down - having to place it, press Done and pick it up again
to turn it is three steps for one decision. A turn snaps to square when it is
close, and stops well short of upside down: a sticker nobody can read is not a
sticker.

Freeing takes it off any event it follows, leaves it on the day it was showing,
and lets it be dragged to another one. It settles again wherever it lands.

Deleting an event does not delete the stickers on it. They unstick and stay on
the day they were last shown.

## Where else they appear

A sticker stuck to an event is drawn on the next-event widget and on the
reminder panel for that event, in the corner furthest from the title. It is
sized against whichever surface is drawing it rather than against its own
scale: the scale a sticker carries is a share of a calendar cell, and a cell is
nothing like the shape of a card with an event's name across it.

## Getting around the year

Two more controls sit in the header beside the sticker buttons.

| Control   | Does                                        |
|-----------|---------------------------------------------|
| list      | Everything on this month, grouped by day    |
| magnifier | A year, then a month - two taps to anywhere |

Paging a month at a time suits next week and nothing further. The jump dialog
is chevrons over a year, then twelve evenly sized months in a four by three
grid, with a line naming which is current and which is on screen. Single
chevrons step a year, double step ten.

It is shaped like `DatePickerDialog` in `pickers.py` on purpose - the same
question, so the same layout, and nothing to learn twice. Both size to a
fraction of the screen rather than inheriting `_WideDialog`'s 0.86, which is
right for a month of events and turns twelve buttons into a wall.

## The library

The same one the home screen uses, from `CoreWidgetsBundle`. A sticker
uploaded from a phone at `/public/sticker_add` is available here with nothing
further to do. Videos are not offered - nothing draws one into a day box.

## The file

`stickers.json`, beside `events.json` in the calendar's data directory. Not
`widget_layout.json`: this is calendar data keyed to a day or an event, and it
survives independently of where widgets sit on the home screen.

Position and size are fractions of the day box, so a sticker two thirds across
a Tuesday is two thirds across it on any screen, in any month, however many
weeks that month needs.

```python
# What a plugin can reach, through the calendar's published surface.
api = client.public.calendar

store = api["stickers"]
store.on_day(day)                 # everything on one date
store.for_days(days)              # a whole month grid, in one pass
store.for_event(event.key)        # what is stuck to an event

api["stickers_for"](event.key)    # the same lookup, for a widget
```
