# Astronomy

Where the sun and moon are, worked out rather than asked for.

A **library plugin**: no page, no widget, no skill. It exists so more than one
plugin can share `astronomy.py`.

## Why it is a plugin

Core Widgets loads before Nighttime Clock, so the night clock could not own
this - a dependency in that direction is a cycle. Having no dependencies of
its own, this can sit under both.

It is not in `src/` because it is not the panel's own machinery. Nothing in
the client needs to know where the moon is, and this can be uninstalled.

## What it exposes

Under the key `astronomy`, on the public registry:

| Name                       | Answers                                             |
|----------------------------|-----------------------------------------------------|
| `sun_times(lat, lon)`      | `(sunrise, sunset)` as UTC-aware datetimes.         |
| `next_sun_event(lat, lon)` | `(name, when, seconds)` for whichever is next.      |
| `describe_wait(seconds)`   | `2h 14m`, for saying it.                            |
| `moon_phase()`             | `0..1`, new through full and back.                  |
| `moon_name()`              | `Waning gibbous`, and the rest.                     |
| `moon_illumination()`      | `0..1` lit.                                         |
| `moon_waxing()`            | Growing or shrinking.                               |
| `moon_age()`               | Days since the new moon.                            |
| `module`                   | The module itself, for a caller doing several sums. |

```python
if self.client.public.has("astronomy"):
    rise, sets = self.client.public.astronomy["sun_times"](lat, lon)
```

Declare `astronomy` in your `dependencies` so it has loaded and exposed
before your `load()` runs.

## Two things it will not do

**No network.** It is arithmetic on a date and a position, so it works with
the router off and is never the thing that wakes it.

**No timezone guessing.** `sun_times` answers in UTC with a timezone
attached. Comparing one of those to `datetime.now()` raises rather than
returning False, so convert before you compare:

```python
local = when.astimezone().replace(tzinfo=None)
```
