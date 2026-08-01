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
| Panels | A reminder card before an event starts, with a map, and Snooze. |
| Skills | Six: what is next, today, tomorrow, this week, next holiday, how long. |
| API | `calendar_add` and `calendar_upcoming`, both authed. |
| Subscriptions | Read-only mirrors of Google, iCloud or Outlook calendars. Managed from the calendar page, a phone, or settings. |
| Registry | `client.public.calendar` - see `docs/registry.md`. |
| Stickers | Pictures placed on the month grid - see `docs/stickers.md`. |

## Events

An event can repeat (daily, weekly, monthly or yearly), span several days, or
both. A repeating event is stored once and expanded on read - so changing it
changes every occurrence, and the file does not grow a row a week forever.

Individual occurrences can be silenced without touching the series: *not for
the next three Wednesdays* is one call. A whole series, or a holiday you never
care about, can be hidden without being deleted.

Finished one-off events are pruned at startup past
`general.keep_events_for_days`. Series are never pruned.

Four sources, kept apart by `source`:

* `local` - made on the panel
* `imported` - posted to the API by something else
* `subscribed` - mirrored from an external calendar, and read-only here
* `holiday` - computed from the rules, not stored and not editable

Everything but a holiday has an **owner**. Identical events with different
owners are different events, so one person cancelling theirs leaves the other
alone. The API refuses an event with no `user`.

Holidays are worked out rather than fetched. A wall panel is offline often, an
API key for something this static is a poor trade, and the rules do not change.

## Subscribed calendars

**Settings → Calendar → Subscriptions → Add a calendar**, then paste the ICS
address. Google, iCloud and Outlook all publish one; see `docs/registry.md` for
where each hides it.

A feed is mirrored one way and its events are read-only here. Each calendar in
the list has **Sync**, **Tidy**, **Reset** and **Remove** beside it.

Google's ICS feed is cached for hours, so this is for the standing shape of a
week — recurring meetings, term dates, birthdays — not for something added on a
phone two minutes ago. For that, post to `calendar_add`.

## Settings

| Section | |
|---|---|
| `general` | Holidays, events shown per day, week start, dark map, how long finished events are kept. |
| `reminders` | Whether panels appear, how far ahead, how long they stay, how long Snooze defers. |
| `subscriptions` | Refresh interval. Calendars themselves are managed in the list above it. |

Two are stored but not shown as fields, because the plugin draws a better
control for them:

* **Default location** — set with the *Choose on a map* button. New events
  start there, so a household that mostly meets in one place stops retyping it.
* The subscription list itself, which is a list with buttons rather than a
  value to edit.

A setting marked `hidden: true` in `settings.json` behaves normally in every
other way — it saves, migrates and reads back — it simply has no field. A raw
text box beside a proper picker is a second way to get the same value wrong.

## Dependencies

Both come from `requirements.txt` and are assumed present.

* `PyQt6-WebEngine` - the maps in the location picker and event view.
* `cryptography` - encrypts the local place cache.
