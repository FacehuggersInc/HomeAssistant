# What is playing

One contract for every source. A plugin that can play something registers as a
**backend**; anything that shows or controls playback talks to `client.PLAYER`
and never to a backend directly.

A now-playing widget, a quick settings control and a voice skill should not
each need to know whether the sound is coming from a web player, a local
library or a network device. Adding a source is a registration and no change
anywhere else.

---

## Registering a backend

```python
self.client.PLAYER.register(
    "musicplugin", "youtube", "YouTube",
    {
        "play":     self.play,          # play(query_or_id=None)
        "pause":    self.pause,
        "toggle":   self.toggle,
        "next":     self.next,
        "previous": self.previous,
        "seek":     self.seek,          # seek(seconds)
        "volume":   self.volume,        # volume() -> int, volume(n) -> None
        "search":   self.search,        # search(text) -> list
    },
)
```

Every command is **optional**. A radio stream has no `seek` and should not have
to provide one that raises — `PLAYER.can("seek")` is False and the widget hides
the control. A name that is not in `PlayerRegistry.COMMANDS` is refused at
registration with a warning, so a typo fails where you can see it rather than
on the first button press.

`unregister(owner)` drops every backend a plugin registered, and clears the
state if one of them was active.

---

## Publishing state

```python
from src.registries.player_registry import NowPlaying, PLAYING

self.client.PLAYER.publish("youtube", NowPlaying(
    title    = "Everlong",
    artist   = "Foo Fighters",
    art_url  = "https://.../cover.jpg",
    state    = PLAYING,
    position = 61,
    duration = 250,
    track_id = "eBG7P-K-r1Y",
))
```

| Field | Meaning |
|---|---|
| `title`, `artist`, `album` | What it is. Blank is fine. |
| `art_url` | Fetched once per URL by anything showing it. |
| `state` | `playing`, `paused`, `loading` or `stopped`. |
| `position`, `duration` | Seconds. A duration of 0 means unknown, and progress reads 0. |
| `track_id`, `source` | Identity, so a new track can be told from a position tick. |
| `can_seek` | Whether a scrub would work. |

`NowPlaying` is a value object rather than a dict, so a backend that forgets a
field gets a sensible default instead of a `KeyError` inside a paint method. An
unknown `state` falls back to `stopped` and a negative position to zero.

**Publishing is identified by backend key**, not by owner. One plugin can
register several — a web player and a reader of whatever else the machine is
doing — and checking the owner would let both through: each publishes on its
own timer, the state alternates between them every second, and anything
watching rebuilds continuously.

**Only the active backend's state is kept.** A publish from any other is
dropped; the panel plays one thing at a time.

---

## Subscribing

```python
def on_player(kind: str):
    if kind == "ticked":
        bar.update()          # only the position moved
    else:
        rebuild()             # a new track, a pause, a backend change
```

The distinction matters. A backend publishing once a second would otherwise
rebuild artwork once a second. `publish()` compares the track, the state and
the duration; if only the position moved it fires `ticked`.

A listener that raises is **removed**, so one broken widget cannot break every
future update.

Publishes may come from a network thread. Marshal with `client.call_on_ui`
before touching a widget.

---

## Ducking

```python
client.PLAYER.duck(25)     # drop to 25% for something more important
client.PLAYER.unduck()     # put back exactly what was there
```

The level before the duck is remembered, so `unduck()` restores the actual
volume rather than a guess. **Ducking twice does not stack** — a second call
would record the ducked level as the one to restore, and the volume would never
come back up. `unduck()` with nothing ducked does nothing.

A backend that offers `duck`/`unduck` has those called instead, so one that can
fade smoothly does the fading rather than being stepped by the registry.

---

## Commands

```python
client.PLAYER.play("everlong")
client.PLAYER.toggle()
client.PLAYER.volume(60)
client.PLAYER.seek(30)
```

Every one returns `None` when the active backend cannot do it, and a backend
that raises is logged rather than allowed to propagate — a broken source should
not take the panel down.

`toggle()` falls back to `pause()`/`play()` when a backend does not offer it,
chosen from the published state.

---

## The widget

`NowPlayingWidget` (`src/ui/widgets/now_playing.py`) is the reference consumer:
a cover, a title, an artist, a progress line, and **play/pause and restart**.

No skip buttons. What a music source queues is usually *alternatives* for the
song that was asked for rather than a playlist, so "next" would play something
nobody chose. Restart is a `seek(0)`, and hides itself where there is nothing to
go back to.

Both the title and the artist **scroll** when they do not fit, rather than being
elided — on a music card the end of a title is often the part saying which
version it is. Widening the card may stop them scrolling entirely, since they
re-measure on a resize.

**Resizable in width, fixed in height.** There is nothing useful to do with more
height: a taller card is a cover with empty space beside it. 320px to 900px.

The **progress line runs the full width of the bottom edge** — a row of the
outer layout rather than part of the text column, so it reaches both edges. It
is clipped to the card's own corner radius, or a full-width line would square
off the bottom corners and the card would look like it had a foot.

Under the artist it shows **`1:23 / 3:41`** — the progress line says roughly
how far through, and the clock says how far exactly, which is what somebody
deciding whether to wait for the end needs. A source with no known length shows
only the position, since `1:23 / 0:00` is worse than saying nothing.

It reads `client.PLAYER` and nothing else — there is no source named anywhere
in it. It hides itself when nothing is playing rather than leaving a blank card
on the wallpaper, and hides any control the active backend cannot do.

**The cover, blurred, is the card's background** when there is art, and a
gradient when there is not. Blurred by scaling down to a thumbnail and back up
rather than with a `QGraphicsBlurEffect`, which needs a scene and a render
pass for the same result. A wash goes over it, since a bright cover would
otherwise leave white text on white.

**It draws its own opaque gradient** when there is no art.

**The title scrolls** when it does not fit, rather than being elided — on a
music card the end of a title is often the part saying which version this is.
It only moves when it has to, and stops when the card is hidden. The wallpaper behind it is a photograph,
and a translucent card is legible over some images and unreadable over others
with no way to know which from here. Painted rather than set as a stylesheet
background, since a stylesheet gradient does not follow a resize.

**Rebuilding is idempotent.** A source polled every couple of seconds
republishes the same track; rebuilding then re-lays out the row for no visible
change, which reads as the card flashing. The widget compares everything it
actually draws — and nothing that ticks — and returns early when none of it
moved. When it does rebuild it logs *what* changed, so a source republishing
something subtly different is findable rather than only visible.

Cover art is compared by **path, without the query string** — a query is where
a temporary token or a regenerated size lives. Not by filename: every YouTube
thumbnail is called `maxresdefault.jpg`, so keying on that makes every video's
art look identical and the cover never changes. And the cover is never blanked
mid-swap: the old one stays until the new one has arrived, so a track change
is a swap rather than a blank frame followed by a picture.
