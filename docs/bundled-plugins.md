# Bundled plugins

Nine plugins ship in `src/assets/bundled/`. They are ordinary plugins with no
special privileges - the same lifecycle, the same registries, the same
`plugin.toml`. Almost everything visible on a fresh install comes from them,
which is deliberate: if the bundled plugins could do something your plugin
cannot, the plugin system would not be finished.

Read them when the documentation runs out. They are the worked examples.

| Key                 | Name                 | Provides                                                   |
|---------------------|----------------------|------------------------------------------------------------|
| `corewidgetsbundle` | Core Widgets Bundle  | The home page, sub-pages, and the widget and tile set.     |
| `coreskillsbundle`  | Core Skills          | Voice skills and the activity bar.                         |
| `aifallback`        | AI Fallback          | Answers phrases no skill matched.                          |
| `idletriggers`      | Idle Random Triggers | Runs registered callbacks while the panel is idle.         |
| `rssfeeds`          | RSS Feeds            | Feed fetching, shown through the idle triggers.            |
| `nighttimeclock`    | Nighttime Clock      | A full-screen clock page for after hours.                  |
| `musicplugin`       | Music                | Playing music by voice, and the now-playing card.          |
| `calendar`          | Calendar             | Events, holidays, a calendar sub-page, widgets and a tile. |
| `astronomy`         | Astronomy            | Sun and moon arithmetic, for anything that asks.           |


## Core Widgets Bundle

`corewidgetsbundle` - the largest of the nine, and the one to read first.

Registers the home page and its sub-pages, and every widget and tile that
comes with the app:

* **CyclingBackground** - the wallpaper, fading between images on a timer.
  Publishes `cwb_wallpaper` so the
  [configuration bar](widgets.md#the-configuration-bar) can cycle and pin it.
* **DateTimeWidget** - the time with the full date beneath it, painted as one
  block rather than stacked labels. Two labels in a column would each carry
  their own metrics, shadow and baseline, and read as two widgets that happen
  to be near each other; sharing a baseline grid, with the date drawn softer,
  makes the pair one thing with a heading. No background and no border: it sits
  on the wallpaper, and the wallpaper is the background.

  Spacing is measured on the **ink** - `tightBoundingRect` - rather than on
  ascent and descent. A font box carries leading above and below the glyphs, so
  laying the lines out from it puts around 50px of nothing between a 96px time
  and its date. `LINE_GAP` is the literal number of clear pixels between
  them.
* **WeatherWidget** - the other half of the default home screen content. The
  temperature carries its unit; `weather.units` picks fahrenheit or celsius and
  is sent to the API rather than converted afterwards.
* **ConfigurationBar** - quick access from the page itself: notifications, the
  widgets panel, a timer, an alarm, the whiteboard, and the wallpaper.
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


## Core Skills

`coreskillsbundle` - voice skills for the built-in assistant, plus the
activity bar along the bottom of the screen.

Skills cover the time and relative dates, opening and clearing notifications,
weather, the sun and moon, looking a word up, searching Wikipedia, converting
units, the next calendar event, timers, and quitting the app.

Every skill it registers, and one way to reach each:

| Group         | Skill                        | Said as                            |
|---------------|------------------------------|------------------------------------|
| Time and date | `tell-time`                  | "what time is it"                  |
|               | `tell-relative-date`         | "what day is it"                   |
| Notifications | `notifications-open`         | "open my notifications"            |
|               | `notifications-empty`        | "clear my notifications"           |
| Weather       | `weather-update`             | "what's the weather"               |
|               | `weather-precipitation`      | "is it raining / will it snow"     |
|               | `weather-week`               | "what's the forecast for the week" |
|               | `weather-wind`               | "how windy is it"                  |
|               | `weather-humidity`           | "is it muggy"                      |
|               | `weather-uv`                 | "do I need sunscreen"              |
|               | `weather-air-quality`        | "how's the air quality"            |
| Sun and moon  | `sun-times`                  | "when does it get dark"            |
|               | `moon-phase`                 | "is it a full moon"                |
| Words         | `define-word`                | "what does petrichor mean"         |
|               | `word-synonyms`              | "other words for tired"            |
| Encyclopedia  | `wiki-look-like`             | "what does an axolotl look like"   |
|               | `wiki-search`                | "tell me about the Roman Empire"   |
| Conversions   | `convert-units`              | "how many mm in an inch"           |
| Timers        | `set-timer`                  | "set a timer for 10 minutes"       |
|               | `cancel-timer`               | "cancel my timers"                 |
|               | `check-timers`               | "how long is left on my timer"     |
| Alarms        | `set-alarm`                  | "set an alarm at 4:40 PM"          |
|               | `cancel-alarm`               | "cancel my alarms"                 |
|               | `check-alarms`               | "what alarms do I have"            |
| Quiet         | `quiet-on / quiet-off`       | "do not disturb"                   |
|               | `mute-on / mute-off`         | "be quiet"                         |
|               | `mic-mute-on / mic-mute-off` | "mute the microphone"              |
| Navigation    | `go-to-page`                 | "show the calendar"                |
|               | `open-bookmark`              | "open scryfall"                    |
| System        | `nevermind`                  | "nevermind"                        |
|               | `quit-application`           | "quit the application"             |

The phrasings above are one example each; every skill accepts many, and the
matching is described in [Voice assistant](assistant.md) rather than here.

Weather is seven skills rather than one, because they are seven different
answers and a single skill covering everything outdoors answers "is the air
clean" with a wind speed:

| Skill                   | Asked as                                    | Answers with                                              |
|-------------------------|---------------------------------------------|-----------------------------------------------------------|
| `weather-update`        | "what's the weather", "how hot is it"       | Temperature, feels-like, sky, wind, humidity              |
| `weather-precipitation` | "is it raining", "will it snow tonight"     | What is falling now, when it is likely, how much          |
| `weather-week`          | "what's the forecast for the week"          | Seven days of high, low, sky and chance                   |
| `weather-wind`          | "how windy is it", "which way is the wind"  | Speed, what that is called, direction, gusts              |
| `weather-humidity`      | "how humid is it", "is it muggy"            | The percentage, and whether that is comfortable           |
| `weather-uv`            | "what's the UV index", "do I need sunscreen"| The index, its band, and what to do about it              |
| `weather-air-quality`   | "how's the air quality", "what's the AQI"   | US AQI with its EPA band, and the main pollutants         |

Rain and snow are one skill, not two. Splitting them would put "will it rain
or snow" in a competition between them, and the words that tell them apart are
the only difference between the two utterances - so the handler takes the
whole phrase and answers about whichever was asked. Asking about snow while
rain is falling says so rather than answering yes.

Air quality and UV come from a different open-meteo service to the forecast,
so they fail separately: a panel somewhere that model does not cover still
gets its weather, and asking about the air says so rather than showing a blank
card.

The wake word comes from Assistant settings unless a skill overrides it.
Skills that speak degrade to silence when TTS is unavailable, so the panel
still works with spoken replies turned off.

The **activity bar** shows what was heard and what the assistant is doing. It
lives on the passthrough overlay layer, so it can sit over the page without
taking a single touch from it.

### Looking a word up

`define-word` and `word-synonyms` answer "what does petrichor mean", "define
ephemeral", "what are other words for happy". Both go to
[dictionaryapi.dev](https://dictionaryapi.dev), which needs no key - a panel
that needs one for this is a panel where the skill starts failing the day
somebody's trial runs out. One request answers both, since definitions and
synonyms come back in the same document.

Two things about the matching are worth knowing before adding phrasings:

* **A payload anchor must not be the whole command.** The anchor is cut out
  before patterns are generated, so an anchor of `"whats"` leaves nothing to
  generate from and the resulting pattern matches every question on the panel
  - "whats the weather" and "whats the date" included. Every anchor here
  carries a word of its own: `definition of`, `the meaning of`, `look up`.
* **"what does X mean" has no leader**, so it is matched on its trailing verb
  and the word is read off the phrase. An argument pattern cannot do it:
  `extract_args` strips leading verbs and auxiliaries from the span it
  matched, so "what does **run** mean" comes back empty - and most short
  English words are also verbs.

### Converting units

`convert-units` answers "how many cups in a litre", "convert 5 miles to km",
"what's 350 Fahrenheit in Celsius". Length, mass, volume, time, speed, data
and temperature; US customary for volume, since a cup is 237ml there and 250
in a British recipe book.

Two things it does that are worth keeping if the table is extended:

* **The other side of the question settles an ambiguous unit.** "How many
  ounces in a gallon" is a volume and "how many ounces in a pound" is a mass -
  the word is identical and nothing but the other unit can decide it. An
  ounce is registered as both readings, and `resolve()` picks the pair whose
  dimensions agree.
* **Temperature is not in the same table.** It is affine rather than linear:
  0°C is not zero of anything, so a factor cannot express it and folding it in
  makes "20 C in F" come out as 36. It also gets no rate line, because one
  degree Fahrenheit is -17 Celsius - true, and not a conversion rate.

Like the dictionary, this reads the whole phrase rather than using argument
patterns. A conversion carries three values in an order the phrasing changes,
and `extract_args` returns the widest span *per argument* rather than one per
position - two units in one utterance is the case it cannot do.

**It is matched on shape, not on the units named.** A unit is an opaque value
the way a song title is: the examples can only list a handful, and scoring an
utterance against them punishes it for naming different ones. "How many inches
in a foot" against the example "how many feet in a mile" shares two lemmas of
three and scores 0.67, under the threshold - so the whole of length and data
reached no skill at all while converting perfectly the moment it was called by
hand. `units.PATTERN_TOKENS` exists for that: hand-written patterns fire on
"how many &lt;unit&gt; ... &lt;unit&gt;" whatever the units are. Anything added to the
table is reachable immediately, without an example naming it.

### The sun and the moon

`sun-times` and `moon-phase` answer "when is sunset", "when does it get dark",
"what phase is the moon". The arithmetic is **not** here - it belongs to the
[Astronomy](#astronomy) plugin, which registers no page, widget or skill on
purpose. `plugin.toml` declares the dependency so the load order is right;
every use is still guarded, so a panel with the library removed says so.

Sunrise and sunset are one skill. They are the same calculation and the same
card, and which end of the day was asked about is a word in the phrase rather
than a different question. Coordinates come from the weather plugin's
settings, where they are already configured - a second copy is a second thing
to edit and a second thing to be wrong. With no location set it says so
instead of answering: sunrise at 0,0 is a real time in the Gulf of Guinea,
which looks right and is hours out.

### Wikipedia

`wiki-look-like` answers "what does an axolotl look like" with a picture and
the article's own caption - the words somebody wrote under that photograph
explaining what is in it, which is exactly what was asked for and is nowhere
in the summary endpoint. `wiki-search` answers "tell me about the Roman
Empire" with the first few sentences, a picture, and a button that opens the
article on `#webpage`.

Four endpoints, because the answer is spread across all of them: search turns
a spoken phrase into an exact title (the summary endpoint 404s on anything
else), summary gives the thumbnail, the media list gives the caption, and
`prop=extracts&exintro` gives the **whole** introduction. That last one is
needed because the REST summary returns the lead paragraph only, and the
second paragraph of a Wikipedia introduction is usually where the useful part
is - the first says what category a thing is in, the second says what is
interesting about it. `first_paragraphs()` takes two, whole where they fit and
cut at a sentence where they do not. A descriptive `User-Agent` is not optional - Wikimedia blocks
anonymous clients that do not identify themselves, and the failure is a 403
rather than anything that reads as "say who you are".

`wiki-search` has a leader, so its subject is a payload. `wiki-look-like` does
not: the subject of "what does an axolotl look like" sits in the **middle**,
and a payload runs to the end of the utterance - which here is "look like". It
is matched on the trailing verb and the subject read off the phrase, the same
way `define-word` handles "what does X mean". `look up` is deliberately not an
anchor here; it belongs to the dictionary, where it was first.

**No subjects in `wiki-look-like`'s examples.** They named an axolotl, a
pangolin, Mount Fuji and Saturn, and every one of those words became the
skill's vocabulary - a `wants_phrase` skill has no payload, so its examples
are scored whole. "What is an axolotl" then matched on the word "axolotl"
alone and went hunting for a picture, while "what is a black hole" went to the
search skill. Which one answered depended on whether the noun happened to
appear in an example. The examples say "what does **it** look like" now, so
the only thing the skill knows is the shape.

"What is X" belongs to `wiki-search`, as a deliberately weak pattern: it
scores near zero, so any skill that actually knows the subject beats it, and
it only wins when nothing else does - which is exactly when looking it up is
the right answer.

A disambiguation page is caught and refused rather than read out. Its extract
looks like an answer and is not one - "Mercury may refer to:" followed by
nothing.

**A word the dictionary misses lands here.** `define-word` fires
`on_dictionary_lookup_failed` instead of apologising, and this plugin
subscribes to its own event to try the encyclopedia. The two cover different
ground - a dictionary has words and an encyclopedia has things, so
"petrichor" is in one and "Xochimilco" is in the other, and being told neither
exists is wrong about half of them. Through the event rather than one handler
calling the other, so anything else can answer a missed word too. The apology
belongs to whoever runs out of places to look, which is the encyclopedia.

**And it says which one came up empty, and why.** Both clients record whether
the last lookup failed because the thing is not there or because they could
not be reached, and the reply names both sources: *"xochimilco isn't in the
dictionary, and Wikipedia doesn't have an article on it either"* against
*"I couldn't reach the dictionary or Wikipedia"*. The two need different
things done about them - one means try a different word, the other means
check the network - and answering both with "I couldn't find it" sends
somebody hunting for a spelling mistake that is not there.

See [Voice assistant](assistant.md) for how skills are declared and matched.


## AI Fallback

`aifallback` - subscribes to `on_assistant_fallback` and answers phrases no
skill matched, using the OpenAI API.

Replies are rendered as markdown in a chat panel, with per-message and
per-session token counts so the cost of a conversation is visible while you
are having it. Remote images in a reply are fetched and displayed; the panel
closes on a tap outside it, which also cancels the assistant session.

### The pill in the panel

`StatusPill` says what the assistant is doing at the bottom of the
conversation. The voice bar lives at the bottom of the SCREEN, which a
full-screen card covers, so while a panel is open this is the only thing
saying whether it is listening - same place, same shape, same colours.

It reads `ASSIST_STATUS` rather than being driven by events, because the
states it shows are the ones that last: the round trip to the model is held
at THINKING by `client.thinking()` for its whole duration. Three things about
that reading are worth knowing:

- **It ticks at 33ms**, matching the voice bar. That interval is both the
  poll and the pulse: at 200ms the dot breathes at five frames a second and a
  state that comes and goes between two polls is never drawn. See
  [what the panel shows](assistant.md#what-the-panel-shows) for why a status
  cannot be polled that slowly.
- **The speaking probe has its own guard.** Sharing one `try` with the status
  read, and falling back to READY, means a backend that raises on
  `is_speaking()` does not merely lose the speaking state - it pins the pill
  to "say the wake word" while the assistant is listening and thinking behind
  it. One failure, and the pill silently stops reporting anything.
- **DORMANT is its own state.** An unknown status falling through to READY
  leaves a panel where the assistant is off - a declined model download, a
  missing package - inviting somebody to say a wake word nothing is listening
  for.

It repaints only while the dot is moving or the state has changed, and stops
its timer while hidden.

Needs `OPENAI_API_KEY` in `.env`, declared as a `secret` setting. Without it
the plugin loads and says so rather than failing.

The system prompt is a `body` setting, so the assistant's manner is editable
from the Settings page without touching code.


## Idle Random Triggers

`idletriggers` - runs registered callbacks at random while the panel is idle,
and rotates through everything registered to it.

Panels passed to it are handled automatically, including being closed again
when interaction resumes. This is what a screensaver-style rotation is built
on.

Uses `on_interaction` and `on_fresh_interaction` to know when idleness starts
and ends. See [Events](events.md).


## RSS Feeds

`rssfeeds` - depends on `idletriggers`, and is a good example of a plugin that
declares a dependency and builds on another plugin rather than the client.

Add a feed either by calling `add_rss_feed(plugin_key, url, transformer)`, or
by dropping a JSON file of `{"url": ..., "transformer": ...}` into an
`feeds/` folder in the working directory.

The `transformer` maps a feed's own shape onto
`{"title": ..., "items": [{"id", "title", "published", "summary", "author"}]}`.
It is optional: leave it out and one is inferred from the feed's data the
first time it is read, then cached.

Feeds are shown as idle panels through `idletriggers`.


### Managing feeds

`/public/rss_feeds` is a page — listed on the index as **Feeds** — for adding
and removing them from a phone. A name and an address; the name is only used
for the filename, and the feed's own title is what appears on the panel.

Feeds are stored one per file in the `feeds` folder as `{"url": "..."}`,
which is the whole format. A transformer is inferred on first use and does not
belong in a file somebody typed by hand. The folder is registered as an
uploadable asset, so the files are reachable the same way stickers are.

Adding one puts it into the live rotation immediately; removing one takes it
out rather than leaving it until the next restart.

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

## Music

Ask for a song and it plays.

|                |                                                             |
|----------------|-------------------------------------------------------------|
| Voice          | *"play Everlong"*, *"put on some jazz"*, *"stop the music"* |
| On screen      | A now-playing card with cover art, progress, and play/pause |
| Quick settings | A **Music** button opening what has been played recently    |
| Also shows     | Whatever else the machine is playing, through MPRIS         |

The card is not a music-plugin widget. Everything is published into the player
registry - see [What is playing](player.md) - so anything showing or
controlling playback works
the same whether the sound is coming from this plugin, from a browser tab, or
from something added later.

Titles arrive whole because `play-music` uses a
[payload argument](skills.md) — everything after "play" is taken verbatim
rather than scored word by word, so a long title is not truncated into a
search for its first two words.

**Finding a track.** YouTube Music first — the uploader is a field there rather
than a channel name to be guessed from — then the YouTube results page for
everything a catalogue does not carry. No key, and when an artist is named, a
source that has not got them is left for the next one. A result too unlike what
was asked for is rejected rather than played; when a retry succeeds, the
panel asks *"Did you mean X?"* and remembers the answer, so a name it
mishears once stops being a name it mishears.

**Playing it.** A hidden `QWebEnginePage` driving the documented IFrame Player
API — never the page's markup. A video whose owner forbids embedding falls back
to the watch page, adverts included and skipped.

**Getting out of the way.** Playback ducks to 5% while the assistant is
listening and restores when it settles. `stop` and `shut up` reach the music
through the [cancel registry](cancel.md); `nevermind` deliberately does not,
since that belongs to whatever is on screen.

Written up in full on the plugin's own page.

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

They can also be asked for by name - "when is Thanksgiving", "how long until
Christmas", "when is the fourth of July". `store.holiday_named()` matches an
alias against the **whole phrase** rather than against a name pulled out of
it, so nothing has to find where "the fourth of July" starts and stops.
Aliases are tried longest first, or "Christmas Eve" is answered with Christmas
Day, and matched on whole words, or a short alias matches inside an unrelated
one. `find_holiday()` rolls into next year rather than returning a date that
has been and gone: asked about Christmas on Boxing Day, the answer is next
December.

* **Calendar sub-page** - a month grid at `(0, 1)`, so it is one swipe down
  from the widgets. Tapping a day opens the day view; tapping an event opens
  it in full, with a map when it has somewhere to be.
* **Clear the home page** takes every sticker off the screen and leaves the
  library alone. Deleting a file is what Remove does, one at a time — a button
  that empties the page and a button that empties the library should not be the
  same button.
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


## Astronomy

`astronomy` - where the sun and moon are, worked out rather than asked for.

A **library plugin**: no page, no widget, no skill. It exists so more than one
plugin can share `astronomy.py`, and `load()` does nothing but expose it on
the public registry. See [Library plugins](architecture.md#library-plugins).

It is a plugin rather than part of `src/` because nothing in the client needs
to know where the moon is, and it is not owned by either of its callers
because Core Widgets loads before Nighttime Clock - a dependency in that
direction is a cycle. Having none of its own, this sits under both.

Exposed under `astronomy`: `sun_times`, `next_sun_event`, `describe_wait`,
`moon_phase`, `moon_name`, `moon_illumination`, `moon_waxing`, `moon_age`, and
`module` for a caller doing several sums in a row. Declare `astronomy` in your
`dependencies` so it has loaded before your `load()` runs.

No network - it is arithmetic on a date and a position. `sun_times` answers in
UTC with a timezone attached, so convert before comparing against
`datetime.now()`.


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

## Bookmark

A saved web page, as a floating widget or a 1×1 tile. Both show the site's icon
— fetched by the browser engine when the page was saved, not by a second
request.

On the tile the icon fills the cell, and **the name goes when there is one**. A
site's own picture says which site it is better than a few elided characters do,
and keeping both means the icon gets whatever the text left over — which is the
small centred thumbnail again by another route. Without an icon the tile falls
back to the site's initial and its name.

Added from the widgets or tiles panel, it asks which bookmark first; with none
saved it opens the browser's home page instead, since there is nothing to choose
from. Pressing one opens `#webpage` **locked to that site**: a bookmark is a
destination rather than a way into the internet.

Bookmarks themselves belong to the client (`client.BOOKMARKS`), so they outlive
this plugin.

Saving one from the browser toolbar puts a copy on the home page for a few
seconds. Queued rather than placed immediately — bookmarking happens *on the web
page*, so the home page is not on screen to receive it.

## Checklist

A title and a list of things to tick off. Tapping a row toggles it; tapping
anywhere else opens the menu — the rows are the point, and anything that makes a
tick harder is the widget getting in the way of the list.

Everything happens on the list. A row's box ticks it, the X beside it removes
it, and the **Add** row at the bottom is the only thing that opens a keyboard —
tapping a list should not put a wall of text in front of somebody who wanted to
cross off one thing.

The X is drawn in the paper's own colour darkened. A red one on a yellow note is
an alarm; this is a quiet way to take a line off a list.

The **chrome button** opens colour and size only: swatches and a stepper rather
than a menu of "Text 17pt" rows.

Twelve rows are drawn and the rest counted — a list longer than that is one
somebody scrolls, and a widget is not the place for that.

The sticky note carries the same colour and size controls.

### From a phone

`/public/note_add` and `/public/list_add` put either on the panel without
walking over to it. Both are in the dashboard drawer.

Colours are shown as colours rather than a dropdown of hex codes, and where it
lands is picked on a nine-cell grid shaped like the screen — the same one the
sticker page uses, and the same words for each cell.

Choosing a checklist that is already up **loads it**: its name and its lines fill
the form. What comes back replaces the list rather than being appended, so
removing a line works and nothing doubles; anything still there keeps its tick.

Placing goes through the framework's own copy path, which names the instance,
registers it, places it and writes the layout. It runs on the UI thread; these
requests arrive on a Flask one.
