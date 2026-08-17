# Calendar

Events, holidays, and a calendar sub-page for the home screen.

Everything reads one source. The store is published on the public registry as
`calendar`, so the page, the widgets, the tile, the reminder panels and any
other plugin all ask the same object rather than each keeping their own idea of
what is coming up.

## What it adds

|               |                                                                                                                 |
|---------------|-----------------------------------------------------------------------------------------------------------------|
| Sub-page      | A month grid at `(0, 1)` - one swipe down from the widgets.                                                     |
| Widgets       | **Next event** and **Coming up**.                                                                               |
| Tile          | **Calendar**, a month at a glance. Minimum 5x3.                                                                 |
| Panels        | A reminder card before an event starts, with a map, and Snooze.                                                 |
| Skills        | Six: what is next, today, tomorrow, this week, next holiday, how long. All answer on a panel.                   |
| API           | `calendar_add` and `calendar_upcoming`, both authed.                                                            |
| Subscriptions | Read-only mirrors of Google, iCloud or Outlook calendars. Managed from the calendar page, a phone, or settings. |
| Registry      | `client.public.calendar` - see `docs/registry.md`.                                                              |
| Stickers      | Pictures placed on the month grid - see `docs/stickers.md`.                                                     |

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

## Widgets

**Next event** is one event, large: what it is, how long until it, and when.
The gap alone answers "when" only loosely — "Tomorrow" still leaves somebody
needing to know whether to be somewhere at nine or at four — so the day and the
time are shown together, with the frame where the event has an end:
`Tomorrow  ·  2:05 PM - 3:30 PM`.

**Coming up** is today and the next two days with what is on each, one line per
event, the start time on the right. Just the start there: a row has one line to
say it in, and the start is the part being looked for.

Times are shown on a **12-hour clock**, minutes dropped on the hour — `3 PM`,
`9:30 AM`, `All day`. The panel is read at a glance from across a room, and
`15:00` is a number to convert before it is a time. The stored clock stays
24-hour; only the reading of it changes.

Both open the event when tapped, and a tap is measured rather than assumed: a
release counts as one only if the finger travelled less than `DRAG_DISTANCE`
**and** was down for less than the widget framework's own hold. Distance alone
cannot tell a tap from somebody pressing and waiting for the handles, because a
finger held still travels nothing — the threshold is shared with the framework
so that the moment a press stops being a tap is the moment the handles appear.

## Subscribed calendars

**Settings → Calendar → Subscriptions → Add a calendar**, then paste the ICS
address. Google, iCloud and Outlook all publish one; see `docs/registry.md` for
where each hides it.

A feed is mirrored one way and its events are read-only here. Each calendar in
the list has **Sync**, **Tidy**, **Reset** and **Remove** beside it.

Google's ICS feed is cached for hours, so this is for the standing shape of a
week — recurring meetings, term dates, birthdays — not for something added on a
phone two minutes ago. For that, post to `calendar_add`.

### The address has to be the feed

Google shows two things next to each other under **Integrate calendar**: an
address ending `basic.ics`, and a link that opens Google. Only the first is a
calendar. The second — the `?cid=` one — is a web page, and it **fetches
perfectly**: the panel used to get an HTML document back, find no events in it,
and report a clean sync of nothing, once an hour, indefinitely.

Two things now stop that:

- A body with no `BEGIN:VCALENDAR` in it is an **error on the subscription**,
  not a sync that found nothing. It says so in red beside the calendar.
- A `?cid=` link is **converted when it is added**. The parameter is the
  calendar's own address in base64 and the feed for it is a fixed URL, so the
  conversion is exact rather than a guess. Nothing else is guessed at: a wrong
  guess fetches something and looks like it worked, which is the failure being
  fixed.

### What the list tells you

Each calendar shows how many events came from it, so **a feed that syncs
cleanly and yields nothing looks different from one that is working**. Never
synced, synced and found nothing, and synced and found events are three states
and used to read as one.

It also says whether the address is **secret** or **public**. A Google private
feed carries a token in its path and iCloud's published links are a token and
nothing else; anybody holding one can read that calendar, which is worth
saying on a page that lists them.

## Settings

| Section         |                                                                                          |
|-----------------|------------------------------------------------------------------------------------------|
| `general`       | Holidays, events shown per day, week start, dark map, how long finished events are kept. |
| `reminders`     | Whether panels appear, how far ahead, how long they stay, how long Snooze defers.        |
| `subscriptions` | Refresh interval. Calendars themselves are managed in the list above it.                 |

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
