# Calendar

Events, holidays, and a calendar sub-page for the home screen.

Everything reads one source. The store is published on the public registry as
`calendar`, so the page, the widgets, the tile, the reminder panels and any
other plugin all ask the same object rather than each keeping their own idea of
what is coming up.

## What it adds

| | |
|---|---|
| Sub-page | A month grid at `(0, 1)` - one swipe down from the widgets. |
| Widgets | **Next event** and **Coming up**. |
| Tile | **Calendar**, a month at a glance. Minimum 5x3. |
| Panels | A reminder card before an event starts, with a map. |
| API | `calendar_add` and `calendar_upcoming`, both authed. |
| Registry | `client.public.calendar` - see `docs/registry.md`. |

## Events

Three sources, kept apart by `source`:

* `local` - made on the panel
* `imported` - posted to the API by something else
* `holiday` - computed from the rules, not stored and not editable

Holidays are worked out rather than fetched. A wall panel is offline often, an
API key for something this static is a poor trade, and the rules do not change.

## Settings

`general.show_holidays`, `general.events_per_day`, `general.week_starts_monday`,
`general.dark_map`, `general.default_location`, and a `reminders` section for
whether reminder panels appear, how far ahead, and how long they stay.

The default location has a **Choose on a map** button beside it. New events
start there, so a household that mostly meets in one place stops retyping it.

## Dependencies

Both come from `requirements.txt` and are assumed present.

* `PyQt6-WebEngine` - the maps in the location picker and event view.
* `cryptography` - encrypts the local place cache.
