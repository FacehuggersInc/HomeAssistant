# The calendar registry

Everything the calendar knows is published on the public registry as
`calendar`. Read it from anywhere:

```python
if self.client.public.has("calendar"):
    api = self.client.public.calendar
    event = api["next_event"]()
```

> **Check first.** The calendar is a plugin. `has("calendar")` is not optional
> in code that has to keep working when it is disabled — and checking for the
> *name* is not the same as checking for a given shape, so guard for the key
> you are about to use if you are unsure.

---

## Reading events

| Call | Returns |
|---|---|
| `on_day(date, include_holidays=True)` | Everything on one day. |
| `in_month(year, month, include_holidays=True)` | `{date: [events]}` — what the grid draws from. |
| `between(start, end, include_holidays=True)` | Everything in a range. |
| `upcoming(count=5, source=None, now=None)` | The next `count`, in order. |
| `get_event(key)` | One event, or `None`. |
| `holidays(year)` | Every holiday in a year. |

`source` is `"local"`, `"imported"`, `"subscribed"` or `"holiday"`. Passing it
to `upcoming` narrows to that kind.

---

## Relative questions

These are the reason the registry exists — the same question asked from a
widget, a tile, a panel and a voice skill should give the same answer.

| Call | Returns |
|---|---|
| `next_event(source=None, now=None)` | The next thing, or `None`. |
| `next_holiday(now=None)` | The next holiday. |
| `next_user_event(now=None)` | The next thing a person added, ignoring holidays. |
| `previous_event(source=None, now=None)` | The most recent one that has finished. |
| `current_event(now=None)` | Something happening right now, if anything is. |
| `time_until(event, now=None)` | A `timedelta`, or `None`. |
| `days_until(event, today=None)` | A whole number of days. |
| `describe_gap(event, now=None)` | `"in 20 minutes"`, `"tomorrow"`, `"in 6 days"`. |
| `describe_duration(event)` | `"45 minutes"`, `"2h 30m"`, `"all day"`. |

`describe_gap` switches to days past a day out, because "in 37 hours" is not
how anyone thinks about Thursday.

---

## Changing things

```python
api["add_event"](title="Dentist", day="2026-08-04", time="14:30",
                 end_time="15:15", location="221 Main St",
                 notes="Bring the form", icon="mdi.tooth", colour="#4f9de0")

api["update_event"](key, title="Dentist check-up")
api["remove_event"](key)
```

All three fire `on_calendar_changed`, so anything showing the calendar
refreshes rather than waiting for its next tick:

```python
self.client.subscribe_to_event("on_calendar_changed", self.refresh)
```

Subscribe in `load()` and unsubscribe in `unload()` — or in a widget's
`teardown()`, which the framework calls on removal.

Holidays cannot be added, updated or removed. They are computed, and
`event.editable` is `False` for them.

---

## An event

| Field | Meaning |
|---|---|
| `title` | Required. |
| `day` | `"YYYY-MM-DD"`. Required. |
| `time`, `end_time` | `"HH:MM"`. Empty `time` means all-day. |
| `location`, `notes` | Optional text. |
| `icon` | An `mdi.` name. |
| `colour` | Hex, or empty to use the colour for its source. |
| `source` | `local`, `imported`, `subscribed` or `holiday`. Must be listed in `SOURCES` — an unlisted value is rewritten to `local` on load. |
| `key` | Generated. Stable for the life of the event. |

And derived:

| Property | Meaning |
|---|---|
| `date` | A `date`, or `None` if unparseable. |
| `starts_at`, `ends_at` | `datetime`s. An event crossing midnight ends the next day. |
| `all_day` | Whether `time` is empty. |
| `editable` | `False` for holidays. |
| `duration()` | A `timedelta`, or `None` when there is no stated end. |

---

## Over the network

The calendar is reachable from anything on the same network. Everything below
is served by the client's Flask backend on **port 5000**, and needs the client
token as `?token=`. Pair a device once and approve it on the panel.

| Address | Does |
|---|---|
| `http://<panel-ip>:5000/public/calendar_form?token=...` | A page you can add an event from, on a phone. |
| `http://<panel-ip>:5000/public/calendar_add?token=...&title=&day=` | Add one directly. GET or POST. |
| `http://<panel-ip>:5000/public/calendar_upcoming?token=...&count=5` | What is coming up, as JSON. |

`/public/` is where every plugin-registered endpoint is served — these are
registered by the Calendar plugin on `client.API` and disappear with
it. See [Backend API](/docs/api).

### The form

`calendar_form` is a single page sized for a phone: title, date, start, end,
icon, location, notes, and the next five events underneath. Bookmark it on the
phone and adding something to the panel is two taps from the home screen.

It POSTs to `calendar_add`, so an event added there fires
`on_calendar_changed` like any other — the month grid, the widgets and the tile
update immediately rather than at their next tick.

The page carries the token that fetched it, so it posts as that same device.
A page left open on a phone is only ever that phone's access, and revoking the
phone under Settings, Users closes it.

### Adding one directly

```bash
curl "http://192.168.1.50:5000/public/calendar_add?token=YOUR_TOKEN\
&title=Dentist&day=2026-08-04&time=14:30&location=221%20Main%20St"
```

```
{"request": "Success", "event": {"key": "...", "title": "Dentist", ...}}
```

Events arriving this way are tagged `source="imported"`, so the panel can tell
them apart from things added on it. A missing title or a malformed day is a
**400** with the reason, not a silently dropped event.

---

## Whose event is it

Every event except a holiday has an `owner`. Two people can have the same thing
at the same time and mean two different things, so **identical events with
different owners are different events** — one person cancelling theirs must not
cancel the other's.

```python
api["add_event"](title="Dentist", day="2026-08-04", owner="Chris")
api["by_owner"]("Chris")          # theirs, plus holidays
api["owners"]()                   # every name that owns something
```

Holidays belong to everybody: `owner` is blank, and they appear in every
person's list.

### The API insists on it

`calendar_add` returns **400** without a `user`. The calling device is already
known — it was approved by name — but a device is not a person, and a shared
tablet in a kitchen is used by the whole house. A name given here overrides the
device's.

```
POST /public/calendar_add?token=...&user=Chris&title=Dentist&day=2026-08-04
```

The phone form asks once and remembers the answer, so it is a question per
device rather than per event.

---

## Subscribed calendars

A feed is mirrored **one way**: it updates the panel and nothing goes back.
That is what makes it safe rather than a limitation being worked around —
nothing syncs in both directions, so nothing can conflict, and the panel stays
the authority for what was added on the panel.

ICS is a real standard, so Google, iCloud and Outlook are one code path. What
differs is only where the URL comes from.

| Provider | Where |
|---|---|
| Google | Settings for that calendar → secret address in iCal format. |
| Apple / iCloud | Share the calendar publicly → a `webcal://` link. |
| Outlook / 365 | Publish the calendar → choose ICS. |

Treat the URL as a password. Anyone holding it can read that calendar, which
is why `subscriptions.json` is written `0600`.

**Google's feed is cached for hours.** Adding something on a phone will not
appear on the panel quickly, and polling faster does not help — it only costs
requests. For something immediate, post to `calendar_add` instead.

### Managing them

| Call | Does |
|---|---|
| `subscriptions()` | Every feed being mirrored. |
| `add_subscription(url, name, colour)` | Start mirroring one. |
| `remove_subscription(key)` | Stop, and delete its events. |
| `sync_subscriptions()` | Re-fetch now, off the UI thread. |
| `reset_subscriptions(key="")` | Clear its events and fetch again from nothing. |

The list appears in two places — **Settings → Calendar → Subscriptions** and
the subscriptions dialog on the calendar page — and both build their rows from
the same `subscription_row()`. Two copies drift immediately - they drifted to
where one offered four actions and the other only Remove.

Each calendar shows who it is for, its event count and last sync — or the
error, if the last one failed — and four buttons:

| Button | Does |
|---|---|
| **Sync** | Fetch now rather than waiting for the timer. |
| **Tidy** | Remove duplicate rows. Nothing added on the panel is touched. |
| **Reset** | Delete this feed's events and fetch them all again. |
| **Remove** | Stop mirroring and delete its events. Confirmed. |

Reset is for when a feed and the panel have drifted apart. It is cheaper to
explain than a repair, and there is nothing here worth repairing — every one
of these events came from somewhere else.

**Add a calendar** asks for the address, a name and whose calendar it is, with
a note on where each provider hides its ICS link. It is reachable from two
places — beside the Subscriptions heading in Settings, and from the
subscriptions dialog on the calendar page — and both open the same
`SubscriptionEditorDialog`.

Two separate dialogs would drift immediately: one grows
the provider hint and the address validation and the other did not, so which
you got depended on where you started from.

Programmatically, `add_subscription()` on the registry does the same job.

There was also briefly a pair of settings fields for this. They were the only
route where saving a page was part of the gesture, which is not how adding a
thing to a list should work.

### How a sync works

1. Fetch the ICS, capped at 8MB.
2. Parse it, unfolding wrapped lines and decoding escapes.
3. Reconcile each recurring series with its exceptions — see below.
4. **Delete every event this feed put here**, then write the new set.
5. Record the time, or the error.

Step 4 is a replacement rather than a merge. The feed is the authority for its
own events, so "what it sent" is the whole answer — and an event deleted
upstream has to disappear here too, which a merge would never notice.

Events carry `source="subscribed"` and the feed's key in `subscription`. They
are read-only in the UI, and the event view says so rather than just omitting
the Edit button: an edit would be undone by the next sync, and a change that
quietly disappears is worse than one that was never offered.

Keys are derived with sha1 rather than `hash()`. Python randomises string
hashing per process, so hash-derived keys changed on every restart and a reload
produced a second copy of everything.

### Series and their exceptions

A calendar does not send a changed occurrence as an edit. It sends the master
`VEVENT` with its `RRULE` **and** a second `VEVENT` carrying the same `UID`
plus a `RECURRENCE-ID` naming the date it replaces.

Treating those as unrelated meant the series generated an occurrence on that
date and the override added another beside it. The date is added to the
master's `skip` list and the override imports as a one-off, so exactly one
appears.

### What is understood

`FREQ` daily, weekly, monthly and yearly, with `UNTIL` or `COUNT`.

A weekly rule listing several days becomes one event per day — that is how
Google writes "every Monday, Wednesday and Friday", and by far the commonest
thing that would otherwise be dropped. `BYMONTH` and `BYMONTHDAY` on a yearly
rule are accepted and ignored, since they only restate what `DTSTART` says;
rejecting them would drop every birthday and anniversary in a feed.

`INTERVAL` above 1, `BYSETPOS`, `BYWEEKNO` and `BYYEARDAY` are **skipped with
a logged reason** rather than imported as something simpler. Showing a
fortnightly meeting every week is worse than not showing it.

### Time zones

The honest weak point. A `Z` value is UTC and is converted to local; a `TZID`
value is taken at face value. A feed from another zone is therefore right only
while that zone's offset matches the one it was written in. Fixing it properly
means carrying zones through the whole store.


## Repeating events and spans

A repeating event is stored **once** and expanded on read. Storing every
occurrence would mean a weekly event filling the file forever, and no way to
change "all of them" afterwards.

| Field | Meaning |
|---|---|
| `repeat` | `daily`, `weekly`, `monthly`, `yearly`, or empty. |
| `repeat_until` | Last day the **series** may occur. Empty means forever. |
| `skip` | Occurrence days that should not appear. |
| `end_day` | Last day of **one occurrence**, for something spanning more than one. |
| `hidden` | Silenced without being deleted. |

### `end_day` is not `repeat_until`

These two answer different questions and are the easiest pair here to confuse:
`end_day` is how long a single occurrence runs, `repeat_until` is when the
series stops.

Putting the series' finishing date into `end_day` makes **every** occurrence a
span that long. A weekly event with a month in `end_day` becomes five
overlapping month-long bars: the month grid draws it on every day, the count
climbs the further ahead you look, and with `repeat_until` left empty it never
stops.

Two things guard against it. The editor refuses to save a span at least as long
as the repeat interval, naming the field that was probably meant. And
`occurrence_span_days` clamps the span so an occurrence can never reach its own
next occurrence — a weekly event tops out at seven days however `end_day` reads,
because the shape can already be on disk or arrive from an ICS feed.

A span shorter than the interval is untouched: a three-day festival repeating
yearly is a perfectly ordinary thing to want.

Monthly clamps rather than skipping: the 31st gives 31 January, 28 February,
31 March. Yearly handles 29 February the same way.

An expanded occurrence is a copy with its own `key` (`<series>@<date>`) and
`series_key` pointing home — so it can be told apart from its series and from
every other occurrence, and anything acting on it can find the stored event.

`expand()` matches a window against the days an occurrence **covers**, not only
the day it starts. An occurrence beginning before the window can still run into
it, and filtering on the start day alone meant `on_day()` returned nothing for a
span that `in_month()` was drawing across the whole week.

| Call | Does |
|---|---|
| `expand(event, start, end)` | The occurrences in a window. |
| `skip_occurrence(series_key, date)` | Hide one. |
| `unskip_occurrence(series_key, date)` | Put it back. |
| `skip_next(series_key, count)` | Hide the next `count` — "not for three weeks". |

### Occurrence keys resolve to their series

An occurrence's key is `<stored>@<date>`, and nothing is stored under it.
`get()`, `remove()` and `update()` all resolve it back to the stored event, so
acting on something the day view handed you works. Without that, `remove()`
matched nothing, returned a falsy value every caller discarded, and deleting an
occurrence looked exactly like nothing happening.

Editing an occurrence edits the **series**. An occurrence carries no recurrence
of its own — `occurrence_on()` clears `repeat` and `repeat_until` — so an editor
that showed those empty fields and saved them would wipe the recurrence off the
series. The event editor loads the stored series instead, and says so.

### The same event stored more than once

`deduplicate()` matches on `day` among other things, so a weekly series starting
on the 28th and an identical one starting on the 4th are two different rows. To
anyone looking at a calendar they are one event appearing twice, and `_collapse()`
shows only one of them per day — so the duplication is invisible until you try to
delete it and the calendar clears only as far as the next row's first occurrence.

| Call | Does |
|---|---|
| `looks_like(event, ignore_day=True)` | Every stored event recognisably the same thing. |
| `remove_matching(event)` | Remove all of them. Returns the count. |

Matched on owner, title, start and end time. A different owner is a different
event — two people can have the same thing at the same time and mean two
different things.

`tools/calendar_repair.py` does this from outside the app: `--duplicates` groups
events that look alike, `--suspect-spans` finds spans that reach their own next
occurrence, `--fix-spans` clears them, and `--remove TITLE` removes every copy.
It reports before it changes anything and backs the file up first.
| `set_hidden(key, True)` | Silence a whole series, or one holiday. |
| `hidden_keys()` | What is currently silenced. |
| `prune(days)` | Drop finished one-off events past a cutoff. |

`prune` never touches a series: a weekly thing that started two years ago is
still that thing, and its first date is not its last. It runs once at startup
against `general.keep_events_for_days`.

An event covering several days appears in `in_month` under **every** day it
covers, not only its first — the grid draws it in each cell it runs through.

---

## Skills

The plugin registers six, all answering from this registry so a spoken answer
and the widget beside it cannot disagree.

*What is next · what is on today · what is on tomorrow · what is on this week ·
when is the next holiday · how long until my next event*

Answers are spoken **and** shown as a notification: text-to-speech needs a key,
and a panel without one should still answer the question. A list reads out at
most three events and counts the rest.

---

## Settings and storage

`api["option"](path, default)` reads one of the plugin's own settings without
needing the plugin object:

```python
if api["option"]("general.show_holidays", True):
    ...
```

`api["store"]` is the `CalendarStore` itself, and `api["reload"]()` re-reads it
from disk. Prefer the named calls — the storage can change shape without every
caller changing with it, and the store cannot.

Events live in `calendar/events.json` in the user data directory, which
survives updates. Place lookups are cached beside it.

## The default location

`general.default_location` is where the picker starts when the caller has
nothing — a new event, or the settings button before one has been chosen. Both
the search field and the map open on it.

Applied in `LocationPickerDialog` rather than at each call site: the settings
page already passes the default and the editor passes the field it is editing,
so a third caller would otherwise have to remember. An explicit value still
wins, so editing an event with a location opens on that location.
