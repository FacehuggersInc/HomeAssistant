# Transient widgets and timers

Widgets that appear because something happened, and the timer service that is
the first thing to use them.

> **Provided by a plugin.** Everything here comes from `corewidgetsbundle`.
> Disable it and the transient API, the timer service and the timer widget go
> with it. Anything reaching for them should check first — `client.public.has("timers")`.

---

## What a transient widget is

An ordinary widget is placed by the person arranging their home screen and
stays until they move it. A **transient** widget is placed by something
happening — a timer started, an API call asking for a note — and goes away when
that reason does.

The difference is not cosmetic:

| | Ordinary | Transient |
|---|---|---|
| Placed by | the person, from the widgets panel | code, in response to something |
| Position | wherever they dropped it | an exact point, or a free spot in a named region |
| Saved to `widget_layout.json` | yes | **never** |
| Survives a restart | yes | no |
| Delete handle | returns it to the panel | dismisses it, and tells whatever placed it |

**They are deliberately not persisted.** A widget that exists only while its
reason exists must not come back on the next launch as a ghost with nothing
behind it. `save_layout()` skips them *and* clears any stale entry under their
key, so one written by an older build is cleaned up rather than restored.

---

## Placing one

Through `sub.home`'s features:

```python
sub_home.features().show_transient(widget, at="top-right", timeout=120)
```

| Argument | Meaning |
|---|---|
| `widget` | A `Widget` instance. Build one with `create(..., transient=True)`. |
| `center` | `(x, y)` in page pixels. The widget is centred there. |
| `at` | One of the nine positions to land in instead. |
| `timeout` | Seconds until it dismisses itself. `0` means stay. |
| `bundle` | Group with transient widgets already up. Default `True`. |

The positions are the nine in `POSITIONS` — the corners, the edge centres and
the middle. `quadrant=` is accepted as the same argument under its older name,
and the short spellings `top`, `bottom`, `left`, `right` and `middle` fold onto
the nine. See [Widgets](/docs/widgets).

### Where it actually lands

**Nothing ever overlaps.** Placement is tried in order, and each step rejects
any position that would collide with a widget already on the page:

1. **The exact centre**, if one was given. If that spot is taken, the search
   spirals outward in widening rings to the nearest free position — moving a
   widget a few pixels is better than dropping it on top of the clock.
2. **Beside the last transient widget** — below, right, above, then left — so
   several read as a group rather than scattered across the screen.
3. **A random point inside the region**, retried up to forty times.
4. **A scan** for the first free slot anywhere, before it gives up.

`timeout` only removes the widget. **It says nothing and shows nothing.**
Whatever asked for the widget is what knows why it was there, and a placement
API that also notified would notify for stickers and notes too.

### Building one

A registered widget is a singleton under its `KEY`, so a transient copy needs
its own — otherwise dismissing the copy would take the real one with it:

```python
framework = sub_home.features().widget_framework
widget = framework.create("sticky-note", transient=True, text="Back at 6")
sub_home.features().show_transient(widget, at="top-left", timeout=600)
```

`create` works for `MULTIPLE` templates and for anything else whose
constructor takes no required arguments. It returns `None` and logs if the
widget cannot be built. `make_transient(key, **kwargs)` names the same act and
takes the same arguments.

### Being dismissed

A transient widget gets the same hold-then-delete handle as any other, but the
handle does not file it in the widgets panel — there is no entry there for it
to go back to. Instead the framework calls `on_dismissed()` on the widget:

```python
def on_dismissed(self) -> bool:
    """Return True if you removed the widget yourself."""
    self.service.cancel(self.timer.key)
    return True
```

Return `True` to say you have handled removal, `False` (or define nothing) to
let the framework simply take the widget away.

That hook is what makes deleting a timer stop the countdown. Without it the
square would vanish while the timer kept running, and it would announce itself
minutes later from nowhere.

---

## Timers

`TimerService` owns the countdowns. Widgets only draw them.

**Not state on the widget.** A widget lives on `sub.home`, which is destroyed
and rebuilt whenever the page changes — so a countdown stored there would be
cancelled by a trip to Settings and back.

**Timers do not survive a restart.** One is a thing happening in the room over
the next few minutes, and a panel that has rebooted has already failed to be
the thing counting it.

Published on the public registry as `timers`:

```python
if client.public.has("timers"):
    timer = client.public.timers["start"](300, name="Eggs")
```

| Call | Does |
|---|---|
| `start(seconds, name="", quadrant="", center=None)` | Start one. Returns the `Timer`. |
| `cancel(key)` | Stop one, and take its widget away. |
| `cancel_all()` | Stop all of them. Returns how many. |
| `find(name="", seconds=0)` | Timers matching a name, a duration, or both. |
| `cancel_matching(name="", seconds=0)` | Cancel those, and return them. |
| `get(key)` | One `Timer`, or `None`. |
| `running()` | Those still counting. |
| `all()` | Every timer, finished ones included. |

A `Timer` carries `key`, `name`, `duration`, `colour`, `remaining()`,
`fraction()` and `done`.

### When one finishes

The **service** announces it, not the widget and not the placement API:

* **the idle clock is reset**, through `client.reset_interaction_timeout()`.
  A timer going off is the panel asking for attention, so measuring how long
  since anyone touched the screen is measuring the wrong thing — and if the
  panel had already gone idle, the reset wakes it, so a screensaver is
  dismissed rather than left sitting over the answer.
* an answer panel with the name and how long it ran, spoken if TTS is available
* the `on_timer_finished` event, with the `Timer` as its payload
* a 60 second countdown to taking the widget away — so a finished timer stays
  on screen long enough to be noticed, and then leaves on its own

```python
client.subscribe_to_event("on_timer_finished", lambda timer: ...)
```

### The widget

A square that drains. The remaining time is drawn as a fill from the bottom up,
so the level falls as the timer runs out, with a brighter line on the surface
so a nearly-full square still reads as having a level.

Self-painted rather than composed from labels — a `QLabel` cannot be half a
colour — which also means it can rotate with the rest of the home screen.

**It repaints only when something moved.** `tick()` runs twice a second, but
compares what would be drawn - the face, and the fill level in whole pixels -
against what was drawn last, and returns without a repaint when they match.
That matters more than it sounds: the overlay layer is translucent, so a
repaint here forces everything composited above it to redraw too, including an
open quick settings panel and its full-width backdrop. A ten minute timer now
repaints about once a second instead of twice, and an hour-long one barely
more. The colours are derived once at construction and the fitted font is
cached by face, so the repaints that do happen are cheaper as well.

Each timer gets its own colour from an eight-entry palette, handed out in turn
and skipping any already on screen. Two live timers only share a colour once
all eight are taken. A hash of the key would have been simpler and can collide,
which is the one thing this is meant to prevent.

**No title unless one was given.** A timer started as "set a timer for ten
minutes" draws only its countdown; one started as "call it Eggs" draws the
name above it. There is no close button — deletion goes through the hold-then-
delete handle like any other widget, and that stops the real timer.

### Voice

Three skills, from `coreskillsbundle`:

| Say | Does |
|---|---|
| "set a timer for ten minutes" | Starts one. |
| "create an eggs timer for five minutes" | Starts a named one. |
| "set a spaghetti timer for one hour" | The same, however you phrase it. |
| "make a timer called Eggs for five minutes" | Also works. |
| "how long is left on my timer" | Reads out everything running. |
| "cancel my timers" | Stops all of them. |
| "cancel the eggs timer" | Stops one by name. |
| "cancel the five minute timer" | Stops one by how long it was set for. |

A name is picked up two ways: after "called" or "named", and **immediately
before the word "timer"** - which is how people actually say it. Units and
quantifiers are excluded from the second, or "a five minute timer" would come
back as a timer named "minute".

Durations are parsed from the spoken text rather than trusted: "half an hour",
"quarter of an hour", "twenty five minutes" and "an hour" all work, and a bare
number with no unit is read as minutes.

**Cancelling matches loosely on purpose.** A name comes from a transcript, so
it is tried exact, then starts-with, then contains, then close-enough - "Eggs"
still cancels when it arrives as "egg". Below five characters the fuzzy step is
skipped, because at that length nearly everything is close to everything.

A duration matches what the timer was **set for**, not what is left: a five
minute timer is still "the five minute timer" four minutes in.

**A request that matches nothing cancels nothing**, and says what is actually
running instead. Falling back to cancelling everything is the failure that
would cost somebody their dinner.

#### The built-in cancel phrase

`STTProcessing` checks for backing-out phrases *before* intent matching, so
"cancel", "stop" and "nevermind" never reach a skill at all. That is the right
precedence - alone, they mean "forget what I was saying".

It matches the **whole** utterance against a fixed list, so anything longer
passes through untouched: "cancel my timers", "stop the pasta timer" and
"cancel the 5 minute timer" all reach the skill. No workaround is needed, and
none should be added - a skill that tried to claim a bare "stop" would break
backing out of every other conversation.

### By hand

`/public/timer_form` is the page for it, listed on the index as **Start a
timer**. Presets fill the fields rather than submitting, so a duration can be
adjusted before it starts - which is why this is a page rather than an index
button, since an index button fires its endpoint with no arguments at all.

On the panel, the **timer button** on the configuration bar opens a duration picker — hours,
minutes and seconds as steppers. A preset list can only offer what somebody
guessed in advance, and seven minutes is not an unreasonable thing to want.

---

## API

| Endpoint | Does |
|---|---|
| `GET` or `POST` `/public/timer_form` | **A page** to start one from a phone: presets, hours/minutes/seconds, a name and a position. |
| `GET /public/timer_start` | Start a timer, for a script. |
| `GET /public/timer_list` | Every timer the panel is counting. |
| `GET /public/timer_cancel` | Cancel one, or all of them. |
| `GET /public/widget_show` | Place a transient widget. |
| `GET /public/widget_dismiss` | Take one away. |

```bash
# seconds, minutes and hours add up
curl "http://panel:5000/public/timer_start?token=...&minutes=5&name=Eggs"
curl "http://panel:5000/public/timer_start?token=...&hours=1&minutes=30"

# an exact spot rather than a quadrant
curl "http://panel:5000/public/timer_start?token=...&minutes=2&x=960&y=540"

curl "http://panel:5000/public/timer_cancel?token=...&all=1"

# any registered widget key; anything else is passed to its constructor
curl "http://panel:5000/public/widget_show?token=...&widget=sticky-note\
&quadrant=top-left&timeout=300"
```

`widget_show` answers as soon as the request is **accepted**, not once the
widget is on screen — it arrives on a Flask worker and the UI thread may be
mid-frame. Failures are logged rather than returned.
