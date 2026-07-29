# Nighttime Clock

A clock for a dark room, and the brightness to go with it.

> **Provided by a plugin.** Disable `nighttimeclock` and the schedule, the
> dimming and the page all go with it. The panel is left at full brightness on
> unload — a plugin going away should not leave the screen dark with nothing
> running to bring it back.

---

## The three parts

Kept apart because they are much easier to reason about that way:

* **The schedule** — `schedule.py`. What time it is in the day/night cycle,
  and how bright the panel should be. Pure arithmetic, no Qt, no client, so
  the awkward parts are tested directly.
* **The brightness** — follows the schedule, unless somebody has just touched
  the panel, in which case it stays half up until they stop.
* **The page** — switches at the boundaries, and back again once the room
  settles.

---

## The cycle

| Phase | When | Brightness |
|---|---|---|
| **Day** | Between the day time and the start of the fade | 100% |
| **Dimming** | The lead time before night | Fades 100% → night level |
| **Night** | Between the night time and the day time | The night level |

Defaults are **21:00** and **07:00**, with a **60 minute** fade down to
**12%**.

The fade is the reason it is not jarring: the last hour before bed gets
gradually dimmer rather than the room changing at nine o'clock exactly. Turn
`dim_enabled` off and the panel stays at full until the night time, then drops
straight to the night level — the page still switches either way.

### Times that cross midnight

`21:00` to `07:00` is not `start <= now < end` — that comparison is false for
every minute of the night. Since crossing midnight is the *normal* case here,
`in_window()` handles it directly, and a fade that begins on the previous day
(a night time of `00:30` with an hour of lead starts fading at `23:30`) works
for the same reason.

Setting the two times equal means **never night**, rather than always.

---

## Being touched

Only during the night; in the day the panel is already at full, and during the
fade the whole point is that it is on its way down.

The first interaction brings it to `woken_brightness` (**55%** by default) in
about a third of a second — fast enough to feel like a response rather than a
transition — and **leaves the clock for the home page**. Somebody touching a
wall panel at 2am wants to *use* it, and the clock is the one thing they could
already read from across the room.

While it is awake the schedule does not pull the brightness back down.

It settles back when **either** of two things happens:

* the panel's own idle timeout fires, or
* `settle_seconds` (**20s**) of quiet passes.

Whichever comes first. The second exists because the idle timeout is a
general-purpose setting and is usually longer than glancing at a clock
warrants. The countdown is re-armed on every interaction, so it measures quiet
rather than time since the first touch.

Settling goes back to the clock as well as back down, so the sequence is
symmetrical: touch it and you get the home page at half brightness, leave it
and you get the clock at night brightness.

At the day time it goes to full brightness and to the home page — not to
wherever the panel happened to be when night fell. Waking at 2am onto the
Settings page because that is where it was left at nine is not what anybody
means by going back.

---

## The page

Deliberately almost empty. It is on for eight hours in a dark room, and
everything on it is something glowing at somebody trying to sleep. The time,
the date, and the temperature if the weather widget already knows it.

**It does not fetch its own weather.** A second caller waking the network at
three in the morning is exactly what a night page should not do, so it reads
whatever the weather widget last fetched and shows nothing if there is nothing.

### Idle triggers do not run here

The page carries `blocks_idle_triggers = True`, and `IdleRandomTriggers`
checks for it — a screensaver over a screensaver is nobody's idea of restful.

The check lives in **that** plugin, alongside its existing `invalid_pages`
route, so neither plugin imports or names the other in code. Any page can
refuse the same way.

It is deliberately **not** `blocks_idle`. That stops the idle clock entirely,
and going idle is exactly what brings the panel back to this page after
somebody has looked at it. Interactions still time out here; only the triggers
are held off.

### Fireflies

Slow, pulsing points of light that drift across the page, with a near-black
gradient behind them.

Plain objects with a `step()` rather than a `QPropertyAnimation` each — a dozen
animations running all night for something nobody is watching closely is a
dozen timers. One 20fps timer moves all of them and repaints once.

They turn at the edges rather than wrapping, because a dot vanishing on one
side and reappearing on the other reads as a glitch, and they never fade fully
out, because a dot that disappears reads as a dead pixel.

Both the effect and the count are settings. More is prettier and costs more to
draw; the page repaints in full on every step.

---

## Settings

| Key | Default | Meaning |
|---|---|---|
| `enabled` | on | The whole thing, including the dimming. Off restores full brightness and leaves the clock. |
| `night_time` | `21:00` | When the night clock takes over. |
| `day_time` | `07:00` | When it hands back. |
| `dim_enabled` | on | Fade down as night approaches. |
| `dim_lead_minutes` | 60 | How long the fade takes. |
| `night_brightness` | 12% | Level once it is night and nobody is about. |
| `woken_brightness` | 55% | Level after somebody touches it at night. |
| `settle_seconds` | 20 | Quiet needed before it dims again. |
| `fireflies` | on | The drifting lights. |
| `firefly_count` | 16 | How many. |

Times are 24 hour, `HH:MM`. Anything unparseable falls back to the default
rather than raising — a panel that refuses to start because of `9pm` in the
wrong box is worse than one that uses `21:00` and carries on.

Changing any of them applies at once rather than at the next boundary, since
somebody who just changed the night time wants to see whether they got it
right.

---

## Reaching it by hand

**Quick settings → Night clock** switches onto the page and back off it, at
any hour. Off the clock in the day is simply the home page at full brightness;
off it during the night behaves exactly like being touched, settle timer and
all.

The button is hidden while `enabled` is off.

## From elsewhere

```python
if client.public.has("nighttime"):
    client.public.nighttime["is_night"]()     # True/False
    client.public.nighttime["phase"]()        # "day" | "dimming" | "night"
    client.public.nighttime["describe"]()     # "Night until 07:00."
    client.public.nighttime["go_night"]()     # force it, now
    client.public.nighttime["go_day"]()
```

`GET /public/nighttime` answers the same as JSON, plus the current and target
brightness.

---

## The dimmer

This plugin drives `client.DIMMER`, which uses **real backlight control**
where the machine allows it and falls back to a black wash over the window
where it does not — see [Screen brightness](backlight.md). Run
`hactl.py backlight --survey` to find out which route your panel got.

On a wall panel that route is usually DDC/CI over the HDMI cable, which is
slow: a write takes tens to hundreds of milliseconds. The fade is still
smooth, because the overlay does the interpolation and the hardware is sent
only the value it settles on.

It gained `animate_brightness(percent, duration_ms)` for this: a wall panel
changing level **on its own** is startling as a step change and unremarkable as
a fade. `set_brightness()` still snaps, and cancels any animation in flight.
