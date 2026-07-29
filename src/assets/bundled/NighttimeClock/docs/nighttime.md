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

### Where it goes back to

`return_to` decides. **`last`** (the default) returns to whatever page the
panel was actually on; **`home`** always goes to the home page.

**Settings is never counted as the last page.** Somebody who changed a setting
at nine and walked away did not *choose* to leave the panel on Settings, and
waking at 2am onto a settings form is nobody's idea of useful. Neither is the
night clock itself, for obvious reasons.

Tracked from `on_visit`, which is the event `goto()` actually fires.

---

## The page

Deliberately almost empty. It is on for eight hours in a dark room, and
everything on it is something glowing at somebody trying to sleep. The time,
the date, and the temperature if the weather widget already knows it.

### Where the weather comes from

**The plugin owns it, not the page.** The page used to read it off the weather
widget — but that widget lives on `sub.home`, and `goto()` **destroys** that
page on the way to this one. So by the time the night page looked, the widget
was either gone or a deleted `QWidget`, and the temperature simply never
appeared.

A plugin outlives every page, so the reading is kept there:

1. borrow from the weather widget if it is alive and has one — it is already
   fetching on its own timer, and a second caller waking the network at three
   in the morning is exactly what a night page should not do;
2. otherwise ask `client.API["weather"]` directly, on a worker thread;
3. refresh at most every 15 minutes.

The page re-reads every 20 seconds while it is up, so a fetch that lands a
second after it opened still shows — and only rebuilds the layers when the
reading actually changed, because that reallocates every particle.

### Environment switches (debug only)

With `debug.enabled` on, quick settings gains **Clear, Cloudy, Rain, Snow,
Fog, Storm, Hail, Drizzle, Windy, Freezing, Full moon, Crescent** and
**Real**. Each forces the environment and shows the clock; Real hands control
back to the actual weather.

The two moon switches shift the **date** the moon is worked out from, since
nothing in the weather can move it — waiting a fortnight to see whether the
gibbous draws correctly is not a workflow.

Waiting for it to snow to find out whether the snow looks right is not a
workflow. They are gated on the flag because they are not controls a household
wants in their quick settings, and turning debug off unregisters them and
clears any forced weather.

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

### The environment

The page draws what the sky is actually doing, using the weather the panel has
**already fetched** for its widget. Nothing here makes a request.

**Layers, not modes.** Weather is not one of five things, it is several at
once — overcast *and* raining *and* blowing a gale — so `layers_for()` returns
a stack drawn back to front, and picking only one of them would throw away the
two that make it look like weather.

| Layer | When | What |
|---|---|---|
| **Stars** | Cloud cover below ~85% | Still points that twinkle. The count falls away with cloud, which is most of what makes an overcast night look overcast. |
| **Clouds** | Cover ≥ 25%, or any precipitation | Soft dark masses drifting across the top. Radial gradients — a cloud with an outline reads as a stain. |
| **Rain** | `rain`, `showers`, or a drizzle/thunder code | Streaks **angled by the real wind**, drawn as a line from where a drop is to where it was, so the trail costs one call. |
| **Snow** | `snowfall > 0` | Six-armed flakes with barbed tips, each with its own sway and rotation. Below ~2px they fall back to dots — arms on a two-pixel flake are three draw calls producing one grey pixel. |
| **Hail** | WMO 96, 99 or 77 | Small, hard, fast, and it **bounces** once off a floor just above the screen edge. The bounce is what makes it read as hail rather than pale rain. |
| **Lightning** | WMO 95, 96 or 99 | Mostly the whole sky flashing, sometimes a drawn bolt. See below. |
| **Fog** | WMO code 45 or 48 only | Drifting banks of haze, low on the screen. Never guessed from cloud cover — a panel claiming fog on a clear cold night is worse than one that never mentions it. Code 48 is rime fog, drawn thicker. |
| **Moon** | Cover below 75%, no precipitation | Tonight's moon at tonight's phase, drawn behind everything because everything else is nearer than it is. |
| **Frost** | At or below freezing, not raining | A sparkle around the edges only — glitter across a clock somebody is reading would be unbearable at 3am. |
| **Fireflies** | Clear-ish, dry nights above 45°F | See below. |

### The moon and the sun

Both are **computed, not fetched**. They are arithmetic on a date and a
position, so spending a request on them would be silly — and this keeps
working with the router off, which matters for the one thing awake in the
house at 4am.

The moon is drawn as a lit disc with an unlit disc cut out of it and slid
sideways, which is exactly how a moon is lit and gives a real crescent and a
real gibbous from the same two shapes. An arc approximation of a gibbous moon
has a straight edge and the eye catches it immediately. At new moon nothing is
drawn at all.

Under the temperature, **"Sunrise in 2h 14m"** — the most useful thing on a
clock at 5am. Above the arctic circle in summer it says nothing rather than
inventing a time.

### Gusts

`wind_gusts_10m` was fetched and ignored. A steady drift reads as a fan; wind
reads as wind when it comes in pushes.

One `Gusts` object is shared by every layer, so rain, snow, cloud and fireflies
all lean **at the same moment** rather than each drifting to its own rhythm. A
gust rises fast and falls away slowly, and on a calm night never fires at all.

### Lightning

Mostly **sheet lightning** — the sky brightening for a moment — and only
sometimes a drawn bolt. That is both what most lightning looks like from
indoors and the cheaper thing to draw, and a jagged line every few seconds
would read as a fault rather than a storm.

Flashes come in bursts of one to three a fraction of a second apart, the way
real ones do; a single clean flash every twenty seconds looks mechanical. Over
about five minutes a storm produces roughly a dozen bursts and the sky is lit
for a few seconds in total.

It is drawn **over** the rain, because a flash lights the rain in front of it.

### Rare things

On a **clear** night only, because a shooting star over an overcast sky is a
bright line with no explanation:

* **Shooting stars** — a streak with a fading trail, roughly twice an hour.
  Rare on purpose: something that happens every few seconds is a screensaver
  effect, and the point of a clock you glance at is that most glances are
  ordinary.
* **Constellations** — one of five named shapes (Orion, The Plough,
  Cassiopeia, Cygnus, Lyra) fading in over fourteen seconds, holding for
  seventy, and fading out. Drawn *over* the star field with its own stars a
  little brighter and the joining lines barely there, so it emerges from the
  sky rather than being pasted on it. Placed to the left or right, never over
  the clock.

The shapes are recognisable outlines rather than true positions — a
constellation at real scale is either off the page or three pixels across.

`sky_events` turns both off.

**Wind drives everything.** `wind_direction_10m` is meteorological — where the
wind comes *from* — so screen drift is the opposite, and north is up. A
northerly pushes rain down the screen; a westerly blows it right. Speed is
clamped rather than literal: 200mph would fling every particle off the page in
a frame.

**Every temperature threshold is Fahrenheit**, converted at the boundary. The
reading arrives in whichever unit `weather.units` asked for, and a bare
`temperature <= 32` means freezing in one scale and a warm afternoon in the
other — so 0°C freezes and 30°C does not, which read as Fahrenheit would have
been the wrong way round.

Cloud cover and temperature tint the background gradient. A freezing night
reads bluer, a warm one warmer, and an overcast sky is never as black as a
clear one.

`weather_effects` turns the whole thing off and leaves the fireflies.

### Fireflies

Slow, pulsing points of light, and deliberately **bright**: a white-hot centre
inside a saturated body inside a wide halo. On a near-black page at 12%
backlight a subtle dot is simply invisible, which rather defeats the point.

Plain objects with a `step()` rather than a `QPropertyAnimation` each — a dozen
animations running all night for something nobody is watching closely is a
dozen timers. One 20fps timer moves every layer and repaints once.

They turn at the edges rather than wrapping, because a dot vanishing on one
side and reappearing on the other reads as a glitch, and they never fade fully
out, because a dot that disappears reads as a dead pixel.

**They stay in when it rains, and when it is cold.** Fireflies in a downpour
would be a lie and in a blizzard an absurd one — and they are insects, so a
firefly drifting over frost is a stranger sight than no fireflies at all. They
appear on a dry night under 70% cloud and **above 45°F**. With no temperature
reading at all they still come out, rather than never appearing on a panel
whose weather is not configured.

Both the effect and the count are settings. Every layer is capped — 220 rain
drops, 170 flakes, 60 fireflies, 240 stars — because the page repaints in full
at 20fps for eight hours.

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
| `weather_effects` | on | Rain, snow, hail, cloud, stars and fog, from the weather already fetched. |
| `sky_events` | on | Shooting stars and constellations on a clear night. |
| `show_moon` | on | Tonight's moon at tonight's phase. |
| `show_sun` | on | How long until the next sunrise or sunset. |
| `return_to` | `last` | Where touching it at night goes: `last` or `home`. |
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
