# Tiles

A tile is a fixed-size card on a snapping grid. Tiles are the other half of the
component story alongside [Widgets](widgets.md), and the difference is worth
being clear about:

| | Widgets | Tiles |
|---|---|---|
| Live on | `sub.home` | `sub.tiles` |
| Positioned by | Anchors and rows | Grid cells |
| Size | Free, optionally resizable | Whole cells (`grid_w` x `grid_h`) |
| Off-screen storage | Widgets panel | Tile panel |
| Saved as | `widget_layout.json` | Tile positions per grid |

Widgets are for a home screen that looks arranged. Tiles are for a dashboard
that looks tidy. Both are registered rather than constructed-and-placed, and
both remember where you put them.

---

## The framework

Three classes, all owned by the page rather than by you:

**`TileGrid`** — the grid itself. Divides the page into `cols` x `rows` cells,
computes cell size and gaps from the page size, snaps dragged tiles to the
nearest cell, and saves positions per tile key.

**`Tile`** — the base class you subclass. Handles its own drag, hover and
painting; you fill in the content and `tick()`.

**`TilePanel`** — the drawer of tiles not currently on the grid. Dragging one
out of the panel and onto the grid places it; dragging one to the trash returns
it to the panel.

`sub.tiles` builds all three and exposes them as features. You never construct
them.

---

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

### Class attributes

| Attribute | Meaning |
|---|---|
| `KEY` | Unique. Required — `add_tile()` raises without it, and it is the key the position is saved under. |
| `NAME` | Shown in the tile panel. Required by `register_tile()`. |
| `ICON` | An `mdi.` name for the panel entry. |

### Constructor arguments

| Argument | Default | Meaning |
|---|---|---|
| `grid_w`, `grid_h` | `2`, `2` | Size in cells. |
| `bg_color` | `"#2a2a2a"` | The card fill. |
| `on_click` | `None` | Called on a tap that was not a drag. |

Tiles do not resize freely — `grid_w` and `grid_h` are the size, and the grid
decides how many pixels that is. Design for the ratio, not for a pixel count.

### `tick()` versus `tick_once()`

`tick()` runs on the client tick, on the UI thread. Keep it cheap; the weather
example above is borderline and would be better caching its reading.

`tick_once()` is called when the tile is first placed, for anything that should
happen on arrival rather than every frame.

Anything slow belongs on a thread with the result marshalled back through
`call_on_ui`. See [Threading](threading.md).

---

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

---

## Other tile features

Exposed by `sub.tiles`:

| Feature | Does |
|---|---|
| `register_tile(cls, ...)` | As above. |
| `add_tile(tile, col, row)` | Put an already-constructed tile on the grid. |
| `remove_tile(key)` | Take it off. |
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

---

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

---

## Positions

`TileGrid` saves positions per tile `KEY` and restores them on the next launch.
A tile that has never been placed has no saved entry, which is what makes
`in_grid` a first-run default rather than a permanent one.

Positions are stored against the grid's owning plugin, so a tile registered by
your plugin still has its position remembered when the grid is rebuilt.
