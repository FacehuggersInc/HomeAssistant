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

`source` is `"local"`, `"imported"` or `"holiday"`. Passing it to `upcoming`
narrows to that kind.

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
| `source` | `local`, `imported` or `holiday`. |
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
ID as `?id=` — it is in **Settings → Info** on the panel.

| Address | Does |
|---|---|
| `http://<panel-ip>:5000/public/calendar_form?token=...` | A page you can add an event from, on a phone. |
| `http://<panel-ip>:5000/public/calendar_add?token=...&title=&day=` | Add one directly. GET or POST. |
| `http://<panel-ip>:5000/public/calendar_upcoming?token=...&count=5` | What is coming up, as JSON. |

`/public/` is where every plugin-registered endpoint is served — these are
registered by the Calendar plugin on `client.API_REGISTRY` and disappear with
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
