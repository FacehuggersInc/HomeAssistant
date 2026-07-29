# Bundled plugins

Seven plugins ship in `src/assets/bundled/`. They are ordinary plugins with no
special privileges - the same lifecycle, the same registries, the same
`plugin.toml`. Almost everything visible on a fresh install comes from them,
which is deliberate: if the bundled plugins could do something your plugin
cannot, the plugin system would not be finished.

Read them when the documentation runs out. They are the worked examples.

| Key | Name | Provides |
|---|---|---|
| `corewidgetsbundle` | Core Widgets Bundle | The home page, sub-pages, and the widget and tile set. |
| `coreskillsbundle` | Core Skills | Voice skills and the activity bar. |
| `aifallback` | AI Fallback | Answers phrases no skill matched. |
| `idletriggers` | Idle Random Triggers | Runs registered callbacks while the panel is idle. |
| `rssfeeds` | RSS Feeds | Feed fetching, shown through the idle triggers. |
| `calendar` | Calendar | Events, holidays, a calendar sub-page, widgets and a tile. |

---

## Core Widgets Bundle

`corewidgetsbundle` - the largest of the six, and the one to read first.

Registers the home page and its sub-pages, and every widget and tile that
comes with the app:

* **CyclingBackground** - the wallpaper, fading between images on a timer.
  Publishes `cwb_wallpaper` so the [quick settings](quick-settings.md) header
  can cycle and pin it.
* **DateTimeWidget** - the time with the full date beneath it, painted as one
  block rather than stacked labels. Two labels in a column each carried their
  own metrics, shadow and baseline, and the gap between them was whatever the
  layout decided, so it read as two widgets that happened to be near each
  other. Both lines now share a baseline grid and the date is drawn softer, so
  the pair reads as one thing with a heading. No background and no border: it
  sits on the wallpaper, and the wallpaper is the background.

  Spacing is measured on the **ink** - `tightBoundingRect` - rather than on
  ascent and descent. A font box carries leading above and below the glyphs, so
  laying the two lines out from it put around 50px of nothing between a 96px
  time and its date. `LINE_GAP` is now the literal number of clear pixels
  between them.
* **WeatherWidget** - the other half of the default home screen content.
* **ConfigurationBar** - quick access to a few settings from the page itself.
* **StickerWidget** - an image or GIF from the sticker folder, `MULTIPLE` so
  several can be up at once. Chosen from a searchable grid at the panel or sent
  from a phone. See [Stickers](/docs/plugin/corewidgetsbundle/stickers).
* **TimerWidget** - a square that drains as its countdown runs. Transient, and
  deleting it stops the real timer. See
  [Transient widgets and timers](/docs/plugin/corewidgetsbundle/transient-widgets).
* **StickyNote** - a `MULTIPLE` template, so the widgets panel can add as many
  copies as you like, each with its own key and saved text.
* **ClockTile**, **WeatherTile** - tiles for the tile grid, and the worked
  examples of size variants. The weather tile is a glanceable icon over a
  day/night sky gradient at one cell, gains an hourly strip at 2x3, and
  becomes a full readout at 3x3 and above.
* **OpenMeteoAPI** - registered on `client.API["weather"]`, and used by the
  weather widget and the weather voice skill.

It also registers a **Widgets** quick access button, which opens the widgets
panel from anywhere rather than only from the home page.

This is the plugin to copy from. It exercises page registration, widget
registration, `MULTIPLE` templates, mixins, an API class, the public registry
and quick access in one place.

---

## Core Skills

`coreskillsbundle` - voice skills for the built-in assistant, plus the
activity bar along the bottom of the screen.

Skills cover relative dates, opening and clearing notifications, weather, the
next calendar event, timers, and quitting the app.

The wake word comes from Assistant settings unless a skill overrides it.
Skills that speak degrade to silence when TTS is unavailable, so the panel
still works without an ElevenLabs key.

The **activity bar** shows what was heard and what the assistant is doing. It
lives on the passthrough overlay layer, so it can sit over the page without
taking a single touch from it.

See [Voice assistant](assistant.md) for how skills are declared and matched.

---

## AI Fallback

`aifallback` - subscribes to `on_assistant_fallback` and answers phrases no
skill matched, using the OpenAI API.

Replies are rendered as markdown in a chat panel, with per-message and
per-session token counts so the cost of a conversation is visible while you
are having it. Remote images in a reply are fetched and displayed; the panel
closes on a tap outside it, which also cancels the assistant session.

Needs `OPENAI_API_KEY` in `.env`, declared as a `secret` setting. Without it
the plugin loads and says so rather than failing.

The system prompt is a `body` setting, so the assistant's manner is editable
from the Settings page without touching code.

---

## Idle Random Triggers

`idletriggers` - runs registered callbacks at random while the panel is idle,
and rotates through everything registered to it.

Panels passed to it are handled automatically, including being closed again
when interaction resumes. This is what a screensaver-style rotation is built
on.

Uses `on_interaction` and `on_fresh_interaction` to know when idleness starts
and ends. See [Events](events.md).

---

## RSS Feeds

`rssfeeds` - depends on `idletriggers`, and is a good example of a plugin that
declares a dependency and builds on another plugin rather than the client.

Add a feed either by calling `add_rss_feed(plugin_key, url, transformer)`, or
by dropping a JSON file of `{"url": ..., "transformer": ...}` into an
`RSSFeeds/` folder in the working directory.

The `transformer` maps a feed's own shape onto
`{"title": ..., "items": [{"id", "title", "published", "summary", "author"}]}`.
It is optional: leave it out and one is inferred from the feed's data the
first time it is read, then cached.

Feeds are shown as idle panels through `idletriggers`.

---

## Nighttime Clock

Turns the panel into a clock for a dark room, and the brightness down to go
with it. Fades as night approaches, switches to a near-black page with the
time, date and temperature, and comes half-way up when somebody touches it.

* **Schedule** - `schedule.py`, pure arithmetic with no Qt in it, so times that
  cross midnight and fades that start on the previous day are tested directly.
* **Night page** - `#nighttime_clock`. Centred clock, and slow drifting points
  of light over a near-black gradient.
* **Dimming** - drives `client.DIMMER`, which gained `animate_brightness()` for
  this: a panel changing level on its own is startling as a step and
  unremarkable as a fade.
* **Quick access** - a *Night clock* button to reach the page at any hour, and
  an `enabled` setting that turns the whole thing off.
* **Idle triggers** - the page sets `blocks_idle_triggers`, which
  `IdleRandomTriggers` checks. Neither plugin names the other in code.

Full detail in [Nighttime Clock](/docs/plugin/nighttimeclock/nighttime).

## Calendar

`calendar` - depends on `corewidgetsbundle`, and is the largest of the
non-core plugins.

Everything reads one source: the store is published on the public registry as
`calendar`, with the events themselves plus every relative question anything
else asks - `next_event`, `next_holiday`, `next_user_event`, `previous_event`,
`current_event`, `time_until`, `days_until`, `describe_gap`,
`describe_duration`.

Events come from four places and are kept apart by `source`: made in the app
(`local`), posted to the API (`imported`), mirrored from an ICS feed
(`subscribed`), or computed (`holiday`). Holidays are 21 of them, worked out
from the rules rather than fetched, because a wall panel is offline often and
the rules do not change.

* **Calendar sub-page** - a month grid at `(0, 1)`, so it is one swipe down
  from the widgets. Tapping a day opens the day view; tapping an event opens
  it in full, with a map when it has somewhere to be.
* **Pickers** - date, time and location, each a dialog rather than a typed
  field. A time chosen on a stepper cannot be `25:70`.
* **Next event** and **Coming up** widgets - one large upcoming event, or a
  list that fits however many rows it has room for.
* **Calendar tile** - a month at a glance with marked days, minimum 5x3.
* **Reminder panels** - a half-width card with the event, a map and buttons to
  open or edit it, shown inside the lead window and closing itself after a
  timeout.
* **Default location** - a setting with a map picker beside it, used as the
  starting location for new events.
* **Subscriptions** - read-only ICS mirroring from Google, iCloud and Outlook.
  One direction, replace rather than merge, so nothing can conflict.
* **API** - `calendar_add`, `calendar_upcoming`, `calendar_form` (a page sized
  for a phone), `calendar_subscriptions`, `calendar_sync` and `calendar_dump`.
  All authed.

Everything above disappears with the plugin. Nothing in the client depends on
the calendar existing; anything that reads it checks
`client.public.has("calendar")` first.

---

## Reading them

Each has a `readme.md` next to its `main.py`, and any plugin can ship a whole
`docs/` folder. Both show up in the **Plugins** section at the bottom of the
sidebar — bundled or not, since a plugin in `plugins/` documents itself the
same way. See [Plugins](plugins.md).

The layout is worth noticing:

```
CoreWidgetsBundle/
    plugin.toml
    main.py
    settings.json
    readme.md
    pages/          page classes
    widgets/        widget and tile classes
    api/            API classes registered on client.API
```

Nothing enforces that structure - `main.py` and `plugin.toml` are the only
required files - but every bundled plugin follows it, and it scales better
than one long module. See [Plugins](plugins.md).
