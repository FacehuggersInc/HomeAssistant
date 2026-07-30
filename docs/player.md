# What is playing

> **Reading what the machine is playing is Linux only** - it goes
> through MPRIS. The panel's own playback, and everything else on this
> page, works on any platform.

One contract for every source. A plugin that can play something registers as a
**backend**; anything that shows or controls playback talks to `client.PLAYER`
and never to a backend directly.

A now-playing widget, a quick settings control and a voice skill should not
each need to know whether the sound is coming from a web player, a local
library or a network device. Adding a source is a registration and no change
anywhere else.


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


## The widget

`NowPlayingWidget` (`src/ui/widgets/now_playing.py`) is the reference consumer,
and the reason this registry exists in the shape it does.

**It reads `client.PLAYER` and nothing else.** No source is named anywhere in
it - a test asserts that - so the same card serves this panel's own player, a
browser tab read over MPRIS, and anything added later.

What that gets you as a backend author:

* Publish, and the card appears. Nothing has to be told about you.
* Register only the commands you can honour. A control the active backend
  cannot do is hidden rather than shown dead - so a backend with no `seek`
  loses the restart button and keeps the rest.
* Publish `STOPPED` when you finish. The card hides itself rather than sitting
  on the wallpaper showing a track that stopped existing; **staying silent is
  not the same as saying stopped**, and the registry cannot tell the difference
  for you.
* Position and duration drive the progress line and the clock. A source with no
  known length shows only the position, since `1:23 / 0:00` is worse than
  saying nothing.

Its layout and painting are a widget concern rather than a player one, and are
covered on [Widgets](widgets.md).

