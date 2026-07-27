# Features

Features are one of the primary extension systems of the application.

Pages expose functionality through Features rather than allowing direct access to their internals. A plugin should never need to reach into `sub_home.widget_manager` directly — it should call whatever Feature that page chose to expose for that purpose.

Think of Features as an API that a Page exposes. The Page decides what's exposed and under what name; the plugin only ever sees the names the Page chose to give it.

## How a Page exposes Features

Every page gets `add_features(dict)`, `has_feature(key)`, and `features(key=None, *args, **kwargs)` for free from `PageFramework` / `SubPageFramework`. A page calls `add_features` once, near the end of its own `__init__`, after everything it wants to expose already exists:

```python
self.add_features({
    "add_widgets":   self.widget_manager.add,
    "remove_widget": self.widget_manager.remove,
})
```

The dict values can be **bound methods** (the common case — calling the feature calls straight through to the real method) or a **raw object reference**, exposing an entire sub-system directly rather than one method at a time:

```python
self.add_features({
    "tile_grid": self.tile_grid,   # the whole TileGrid instance, not a method
})
```

## How a plugin calls a Feature

```python
page.features().add_widgets([MyWidget(client)])
```

`page.features()` with no arguments returns the whole feature container; calling `.add_widgets(...)` on it resolves to whatever was registered under that name and calls it normally. You can also call `page.features("add_widgets", [MyWidget(client)])` — passing the key and args directly — though the attribute-style call above is more common and more readable.

Always check `has_feature` first if a Feature might not exist (e.g. a page from a plugin that may not be loaded):

```python
if page.has_feature("add_widgets"):
    page.features().add_widgets([...])
```

## Example: `WidgetFramework`

Widgets are reusable UI components — not intended to be directly inserted into layouts, but managed by a Page system like `WidgetFramework`. `WidgetFramework` is the system behind anchored widgets like `DateTimeWidget` or `WeatherWidget`. A page that wants widgets constructs one, parents it, and exposes a couple of its methods as Features — see `SubHomePage`:

```python
self.widget_manager = WidgetFramework(
    client   = client,
    page_key = "sub.home",
    padding  = client.SETTINGS.home.widget_margin.value,
)
self.widget_manager.setParent(self)
self.widget_manager.setGeometry(0, 0, w, h)
self.widget_manager.show()

# ... later, once everything else on the page exists ...

self.add_features({
    "add_widgets":   self.widget_manager.add,
    "remove_widget": self.widget_manager.remove,
})
```

A plugin then adds widgets to that page without ever touching `WidgetFramework` itself:

```python
@mixin("sub.home.__init__", "myplugin", "after")
def _inject_widgets(self, sub_home, *args):
    sub_home.features().add_widgets([
        DateTimeWidget(self.client, show_date=True, show_time=True),
    ])
```

Flow, end to end:

```text
Plugin → Page Feature → WidgetFramework → Widget
```

Examples from `CoreWidgetsBundle`: `WeatherWidget`, `DateTimeWidget`, `NotificationCenterWidget`, `CyclingBackground`.

## Example: `TileGrid`

Tiles are lightweight interactive UI components, managed the same way — a Page system (`TileGrid`) owns them, plugins never manipulate layouts directly. `SubTilesPage` constructs `TileGrid` the same way `SubHomePage` constructs `WidgetFramework`, but exposes a richer set of Features — several individual methods, **and** the raw `TileGrid` instance itself:

```python
self.tile_grid = TileGrid(client, cols=16, rows=10)
self.tile_grid.setParent(self)
self.tile_grid.setGeometry(0, 0, w, h)
self.tile_grid.show()

# ... later ...

self.add_features({
    "register_tile": self.register_tile,     # SubTilesPage's own method
    "add_tile":       self.tile_grid.add_tile,
    "remove_tile":    self.tile_grid.remove_tile,
    "get_tile":       self.tile_grid.get_tile,
    "tile_grid":      self.tile_grid,          # raw instance, for anything not covered above
})
```

A plugin registers a tile **class** (not an instance — the page constructs it):

```python
@mixin("sub.tiles.__init__", "myplugin", "after")
def _inject_tile(self, sub_tiles, *args):
    sub_tiles.features().register_tile(MyTile, in_grid=False)
```

Notice `register_tile` here is `SubTilesPage`'s **own** method, not `TileGrid`'s — the page wraps `TileGrid.add_tile` with extra logic (checking for a saved position, deciding panel vs. grid) before deciding what to call. This is the pattern to follow when a Feature needs to do more than just forward straight through to the underlying system: write the logic as a method on the Page itself, and expose *that* instead of the raw sub-system method.

Flow, end to end:

```text
Plugin → Page Feature → TileGrid → Tile
```

## General guidance

* Expose the smallest, most specific set of methods a typical plugin actually needs.
* Only expose a raw object reference (like `"tile_grid": self.tile_grid`) when plugins genuinely need capabilities you haven't wrapped yet — prefer specific named methods otherwise, since they're easier to keep stable across refactors.
* Call `add_features` once your page's sub-systems already exist — Features exposing something that doesn't exist yet will simply error when called.
* Always prefer using Features when extending existing pages, rather than reaching into a page's internals directly.

---

# Adding your own cards to a settings page

A plugin can contribute widgets to its own page in Settings, between the
registry summary and its settings, by defining `settings_blocks()`:

```python
def settings_blocks(self) -> list[QWidget]:
    return [my_card]
```

Return any widgets you like; they render in order and are not affected by the
sort toolbar, since they are static content rather than sortable settings. A
plugin raising here is logged and skipped rather than blanking its own page.

`CoreSkillsBundle` uses this to list its voice skills - each with its trigger
phrases, argument names and word-count range - which is a better fit there
than in a generic registry card.

# What a plugin has registered

Every plugin's own page in Settings shows what it currently owns, between its
description and its settings. One card per registry, each with a count and
the entries themselves:

```text
Registered  ·  9 items across 6 registries

  Pages            2      API Endpoints    2
    #weather  Weather Page   /public/forecast
    #radar                   /public/alerts

  Public Registry  1      Skills           2
    weather_state           weather-forecast
                            weather-update

  Mixins           1      Pip Packages     1
    sub.home.__init__ (after)   requests>=2.28
```

Empty registries are omitted rather than shown as zero. A plugin that has
registered nothing says so.

The data comes from `PluginManager.registrations(plugin_key)`, which returns
`[(registry_name, [entries])]` already formatted for display. Each registry is
read independently and a failing one is skipped, so a registry raising cannot
take the whole page down.

Adding a new registry to this view means adding one `add(...)` call there. If
your registry does not already expose a per-owner listing, give it one -
`APIRegistry.endpoints_for()`, `PublicRegistry.names_for()` and
`MixinManager.mixins_for()` exist for exactly this.
