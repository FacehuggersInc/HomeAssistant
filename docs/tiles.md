# Tiles

A tile is a fixed-size card on a snapping grid. Tiles are the other half of the
component story alongside [Widgets](widgets.md), and the difference is worth
being clear about:

> **Provided by a plugin.** Everything on this page comes from
> `corewidgetsbundle`. Disable or remove it and these features go with it — the client
> itself has no tile grid. That is deliberate: see
> [Bundled plugins](bundled-plugins.md).
| | Widgets | Tiles |
|---|---|---|
| Live on | `sub.home` | `sub.tiles` |
| Positioned by | Anchors and rows | Grid cells |
| Size | Free, optionally resizable | Whole cells (`grid_w` x `grid_h`) |
| Off-screen storage | Widgets panel | Tile panel |
| Saved as | `widget_layout.json` | Tile positions per grid |
| Resize | Corner handle, free pixels | Corner handle, whole cells |
| Remove | Delete handle, back to the panel | Delete handle, back to the panel |

Widgets are for a home screen that looks arranged. Tiles are for a dashboard
that looks tidy. Both are registered rather than constructed-and-placed, and
both remember where you put them.


## Dragging one

A drag positions the tile from where the pointer IS, not by adding up how far
it has moved since the last event. Deltas accumulate whatever they miss - a
finger faster than the events are delivered, a tile nudged by anything else -
and the tile then stays permanently behind the finger, because every later
delta is measured from where it already is.

Every way a drag can end puts the tile back on the grid, including the ways
that are not a release. A move arriving with no button held ends the gesture -
a touchscreen produces those on a fast flick - and that path used to just clear
the flags, leaving the tile between two cells, belonging to neither, and
refusing to move again because the gesture state was gone.

## The framework

Three classes, all owned by the page rather than by you:

**`TileGrid`** — the grid itself. Divides the page into `cols` x `rows` cells,
computes cell size and gaps from the page size, snaps dragged tiles to the
nearest cell, and saves positions per tile key.

**`Tile`** — the base class you subclass. Handles its own drag, hover and
painting; you fill in the content and `tick()`.

**`TilePanel`** — the drawer of tiles not currently on the grid. Dragging one
out of the panel and onto the grid places it; the delete handle returns it.

`sub.tiles` builds all three and exposes them as features. You never construct
them.


## Writing a tile

```python
from datetime import datetime

from PyQt6.QtWidgets import QLabel, QVBoxLayout
from PyQt6.QtCore import Qt

from src.ui.widgets.tile import Tile
from src.styling import make_font, set_style, add_text_shadow


class WeatherTile(Tile):

    KEY  = "weather_tile"          # unique, and how the position is saved
    NAME = "Weather"               # shown in the tile panel
    ICON = "mdi.weather-partly-cloudy"

    def __init__(self, client):
        super().__init__(client, grid_w=2, grid_h=2, bg_color="#16222e")

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.temp = QLabel("--")
        self.temp.setFont(make_font(42))
        self.temp.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.temp, "common", "text-strong")
        add_text_shadow(self.temp, blur=10)

        self.label = QLabel("Weather")
        self.label.setFont(make_font(13))
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.label, "common", "text-muted")

        layout.addWidget(self.temp)
        layout.addWidget(self.label)

        # Add to content_layout, not to self. The base class owns the layout
        # on the widget and paints the rounded background behind it.
        self.content_layout.addLayout(layout)

    def tick(self) -> None:
        """Called on the client tick while this tile is on the grid."""
        api = self.client.API.get("weather")
        if api is None:
            return
        try:
            self.temp.setText(f"{int(api.get_current_weather()['temperature_2m'])}")
        except Exception:
            self.temp.setText("--")
```

The bundled `WeatherTile` is the same idea with size variants added — worth
reading in full at
`src/assets/bundled/CoreWidgetsBundle/widgets/tiles/weather_tile.py`.

### Class attributes

| Attribute | Meaning |
|---|---|
| `KEY` | Unique. Required — `add_tile()` raises without it, and it is the key the position is saved under. |
| `NAME` | Shown in the tile panel. Required by `register_tile()`. |
| `ICON` | An `mdi.` name for the panel entry. |
| `MULTIPLE` | Whether it can be placed more than once. A template stays in the panel and every one placed is a copy with its own key — see the bookmark and action tiles. |
| `EDITABLE` | Whether it carries a setup worth going back to. Turns on a pencil handle; the tile overrides `edit()` to say what that opens. |

### Constructor arguments

| Argument | Default | Meaning |
|---|---|---|
| `grid_w`, `grid_h` | `2`, `2` | Starting size in cells. |
| `bg_color` | `"#2a2a2a"` | The card fill. |
| `on_click` | `None` | Called on a tap that was not a drag. |

And on the class, alongside `KEY`/`NAME`/`ICON`:

| Attribute | Default | Meaning |
|---|---|---|
| `RESIZABLE` | `True` | Offers a resize handle. |
| `REMOVABLE` | `True` | Offers a delete handle. |
| `MIN_GRID_W`, `MIN_GRID_H` | `1`, `1` | Smallest span it can be dragged to. |
| `MAX_GRID_W`, `MAX_GRID_H` | `8`, `8` | Largest. |
| `MULTIPLE` | `False` | `True` makes it a template: it stays in the panel and each drag-out places another copy. |

A `MULTIPLE` tile behaves like a `MULTIPLE` widget. Copies get their own
generated keys, so each one saves and restores its position independently, and
`template_key` on a copy points back at the tile it came from.

Tiles resize in whole cells — the grid decides how many pixels a cell is.
Design for the ratio, not a pixel count.


## Selecting, resizing and removing

**Hold** a tile to select it: it gets a dashed border and its handles — a red
delete in the top-left, a resize in the bottom-right, and, on a tile that sets
`EDITABLE`, an amber pencil in the top-right. The border appears under your
finger and stays there when you let go.

The pencil is dropped on a tile too narrow for two handles across the top: at
one cell they land on each other, and a tap in the overlap does whichever is
checked first, which was delete. Resize is never dropped — it is the only way
to make a small tile bigger, and a tile too small for two handles is exactly
the one somebody wants to resize.

A press taken by a handle is not also a press on the tile. Delete takes the
tile away so nothing lands afterwards, but the pencil leaves it there, and the
release used to find a deselected tile with an `on_click` and run it.

To clear it, tap the tile again or tap anywhere on the grid background.
Selecting one tile deselects any other, so only one is ever live.

Dragging the resize handle changes the span in whole cells, clamped to the
tile's `MIN`/`MAX` and to the space left on the grid. Growing into another tile
is refused outright rather than clamped to the largest free size — a resize
that stops dead at the neighbour is easier to read than one that quietly picks
its own limit.

**Tiles cannot overlap.** A drop onto occupied cells sends the tile back to
where the drag started, rather than sliding it to the nearest free block —
on a grid this size that would usually be somewhere across the screen from
where you pointed. A tile arriving from the panel has no previous position, so
it takes the first free block instead. **Variants swap live
during the drag**, so the size being chosen is the size being previewed, and
the drag continues across as many thresholds as you pass.

That last part is why `tick()` must never block. It is called on every variant
swap, so a synchronous network request in one freezes the resize until it
returns. Fetch on a thread and paint from cache — the bundled `WeatherTile`
does exactly that.

Delete is not a delete. The instance goes back to the tile panel with its
state and saved size intact, so putting it back restores it — which is why it
is one tap with nothing in the way.


## Size variants

A tile can show a different layout depending on how big it is. Register the
variants in `build_variants()`:

```python
def build_variants(self) -> None:
    self.add_variant(1, 1, self._build_glance)   # at least 1x1
    self.add_variant(2, 3, self._build_hourly)   # at least 2 wide, 3 tall
    self.add_variant(3, 3, self._build_full)     # at least 3x3
```

Each builder returns a `QWidget`, which the tile puts in its `content_layout`,
replacing whatever was there.

### Thresholds, not exact sizes

`add_variant(min_w, min_h, builder)` is a **floor**, not a match. A tile does
not need an entry for every span it could be dragged to — with the three above,
1x1, 2x1, 3x1 and 2x2 all get the glance layout, and 3x3, 4x3 and 6x6 all get
the full one.

The most demanding entry the tile satisfies wins, scored on area first and
then the larger dimension. At 2x6 that gives the hourly layout (3x3 does not
fit, 2x3 does); at 3x2 it gives the glance one, because a tile that is wide
but short has nowhere to put an hourly strip.

A tile with no variants keeps whatever its constructor put in
`content_layout` and never swaps — that is the simple case, and most tiles
want it.

### When builders run

`apply_span()` is called on construction and again whenever the span changes
during a resize drag. It rebuilds **only when the variant changes**, not on
every frame, so crossing a threshold costs one rebuild and staying inside one
costs nothing.

`tick_once()` is called immediately after a swap. A freshly built variant has
nothing in it, and `tick_once()` is what fills it — do not populate content in
the builder itself beyond structure.

`variant_key()` returns the active threshold, if you need to branch in `tick()`.


## Sizes in the tile panel

A tile with variants is offered at each of them, as separate entries — so
dragging out the 3×3 entry places it at 3×3 rather than at a default size you
then have to resize.

```python
PANEL_SIZES = [(1, 1), (3, 3)]     # advertise only these two
```

Empty (the default) derives the list from the registered variants. Set it when
a tile has many variants and only a couple are worth offering up front.

Each entry shows a real render of the tile at that size. A tile is a singleton
by `KEY`, so the panel borrows the instance, renders it at each advertised
span, grabs the result and puts it back exactly as it was — the entries are the
same tile at different starting sizes, not separate tiles. Placing any one of
them removes all of them.

Renders are at **full size** — the pixel size the tile will actually occupy on
the grid, taken from the grid's own cell metrics. The panel is a third wider
than the other panels to make room for that; a span too wide even for it is
scaled down, which after the width increase is rare. The list scrolls
vertically.

Renders are re-grabbed whenever the panel ticks, so a clock preview is not
frozen at whatever time the panel was first opened, and the first render after
the grid has a real cell size replaces the placeholder-sized one from load.

---

### `tick()` versus `tick_once()`

`tick()` runs on the client tick, on the UI thread. Keep it cheap; the weather
example above is borderline and would be better caching its reading.

`tick_once()` is called when the tile is first placed, for anything that should
happen on arrival rather than every frame.

Anything slow belongs on a thread with the result marshalled back through
`call_on_ui`. See [Threading](threading.md).

**Tiles only tick while `sub.tiles` is the sub-page on screen.** The grid keeps
every sub-page built, so without that gate every tile ticked once a second from
launch whether or not anyone had ever swiped to the dashboard. One tick fires
immediately on arrival, so a clock face is right the moment the swipe lands.

**`tick()` runs during construction.** `Tile.__init__` calls `apply_span()`,
which calls `tick_once()` — so it happens before your own `__init__` body has
run. Anything `tick()` reads must be a class attribute or have a `getattr`
default; an instance attribute assigned after `super().__init__()` does not
exist yet on that first call.

### Cleaning up

A tile may define `teardown()`, called when it is removed from the grid and
when the sub-page itself is destroyed — including tiles sitting in the tile
panel, which `remove_tile()` never reaches. Unsubscribe from anything you
subscribed to there:

```python
def teardown(self) -> None:
    self.client.unsubscribe_from_event("on_settings_saved", self._on_saved)
```

Both paths can reach the same tile, so make it safe to run twice.


## Registering a tile

From `built()`, once the page exists:

```python
class MyPlugin(Plugin):

    def built(self):
        tiles = self.client.get_page("#cwb_home_page")
        sub = self.client.public.cwb_sub_pages  # or reach the page directly

        page = self.client.PAGES.get_entry("#cwb_home_page")
        if page and page.instance:
            sub_tiles = page.instance.sub_page_dict.get("tiles")
            if sub_tiles and sub_tiles.has_feature("register_tile"):
                sub_tiles.features().register_tile(WeatherTile)
```

The bundled plugin does it more directly, through a mixin on the sub-page's
own `__init__`:

```python
from src.mixins import mixin

class MyPlugin(Plugin):

    @mixin("sub.tiles.__init__", "myplugin", "after")
    def _add_tiles(self, sub_tiles, *args):
        sub_tiles.features().register_tile(WeatherTile, in_grid=False)
```

That is the pattern to copy — it runs exactly when the page is built, whether
that is at startup or after a reload. See [Mixins](mixins.md).

### `register_tile(tile_class, *args, in_grid=False, col=0, row=0, **kwargs)`

Constructs the tile, adds it to the page's registry, and then:

* if a **saved position** exists for that `KEY`, places it on the grid there —
  the saved layout always wins
* else if `in_grid=True`, places it at `col`, `row`
* else puts it in the tile panel

`in_grid=False` is the right default for most tiles. It means the tile is
*available* rather than *imposed*, and the person using the panel decides
whether it appears.

Extra `*args` and `**kwargs` are passed to your tile's constructor.


## Other tile features

Exposed by `sub.tiles`:

| Feature | Does |
|---|---|
| `register_tile(cls, ...)` | As above. |
| `add_tile(tile, col, row)` | Put an already-constructed tile on the grid. |
| `remove_tile(key)` | Take it off. |
| `return_tile_to_panel(tile)` | Move it back to the panel rather than deleting it, keeping whatever state it had built up. |
| `get_tile(key)` | The live instance, or `None`. |
| `tile_grid` | The `TileGrid` itself. |

```python
tile = sub_tiles.features().get_tile("weather_tile")
if tile:
    tile.set_bg_color("#2a1616")
```

Reach for `tile_grid` only when you need something the named features do not
cover — an object reference is harder to keep stable across refactors than a
method.


## Cleaning up

```python
def unload(self, carryover=None):
    page = self.client.PAGES.get_entry("#cwb_home_page")
    if page and page.instance:
        sub_tiles = page.instance.sub_page_dict.get("tiles")
        if sub_tiles and sub_tiles.has_feature("remove_tile"):
            sub_tiles.features().remove_tile("weather_tile")
```

The page outlives your plugin. A tile left on the grid after its plugin is gone
is a card that paints from a module that no longer exists — and it will keep
`tick()`ing.


## Positions

`TileGrid` saves positions per tile `KEY` and restores them on the next launch.
A tile that has never been placed has no saved entry, which is what makes
`in_grid` a first-run default rather than a permanent one.

Positions are stored against the grid's owning plugin, so a tile registered by
your plugin still has its position remembered when the grid is rebuilt.

## State beyond position

`TileGrid` saves each tile's column, row and span, keyed by `KEY`. A tile that
needs more than that implements two methods:

```python
def tile_state(self) -> dict:
    return {"url": self.url}

def apply_tile_state(self, state: dict) -> None:
    if state.get("url"):
        self.url = str(state["url"])
        self.refresh()
```

Merged into the same entry rather than a second file — it is the same tile's
state. **Position wins**: a tile cannot move itself by returning a key called
`col`.

Without this a bookmark tile came back on the right cell asking to be chosen
again, because its address had nowhere to live.
