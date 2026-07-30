# Pages

A page owns a UI system. It builds its own interface, decides how that
interface is laid out, and exposes the parts plugins are allowed to touch as
[Features](features.md).

Plugins do not build pages' interiors. They ask a page to do something.


## Registering a page

```python
from src.ui.page import PageFramework

class MyPage(PageFramework):
    def __init__(self, client, page):
        super().__init__(client=client, key="mypage")
        ...

class MyPlugin(Plugin):
    def load(self, carryover=None):
        self.client.add_page("#mypage", "My Page", MyPage, owner="myplugin")
```

The key is what `client.goto()` takes, and the display name is what appears
anywhere pages are listed. Pages are dropped from the registry when their
owning plugin unloads.

| Call | Does |
|---|---|
| `client.goto(key)` | Navigate. Tears the old page down, builds the new one. |
| `client.has_page(key)` | Whether anything has registered it. |
| `client.get_page(key)` | The registry entry, not the live widget. |
| `client.get_pages()` | Every registered key. |
| `client.PAGE` | The page currently on screen. |

Only one page is instantiated at a time. `client.PAGE` is the live widget;
everything else in the registry is a class waiting to be built.


## A complete page

Everything a page needs, and nothing it does not.

```python
from PyQt6.QtWidgets import QVBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt

from src.ui.page import PageFramework
from src.styling import make_font, SIZES, set_style


class WeatherPage(PageFramework):

    def __init__(self, client, data=None):
        super().__init__(client=client, key="#weather", data=data)

        set_style(self, "common", "page-background")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title = QLabel("Weather")
        self.title.setFont(make_font(SIZES.L1, bold=True))
        set_style(self.title, "common", "text-strong")
        layout.addWidget(self.title)

        self.reading = QLabel("...")
        self.reading.setFont(make_font(SIZES.M2))
        set_style(self.reading, "common", "text-muted")
        layout.addWidget(self.reading)

        back = QPushButton("Back")
        back.setFixedHeight(44)
        back.clicked.connect(lambda: client.goto(client.DEFAULT_PAGE or "#root"))
        layout.addWidget(back)

        # What plugins are allowed to do to this page.
        self.add_features({
            "set_reading": self.set_reading,
            "title_label": self.title,
        })

    ## -- features

    def set_reading(self, text: str) -> None:
        self.reading.setText(str(text))

    ## -- lifecycle

    def start(self) -> None:
        """Called by goto() once the page is on screen."""
        self.refresh()

    def stop(self) -> None:
        """Called by goto() before the page is torn down."""
        self.client.TIMEOUTS.cancel("weather_page_refresh")

    def tick(self) -> None:
        """Called on the client tick. Keep it cheap - this is the UI thread."""
        pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Nothing lays a page out for you. Anything positioned by hand rather
        # than by a layout has to be re-applied here.

    ## -- work

    def refresh(self) -> None:
        api = self.client.API.get("weather")
        if api is None:
            self.set_reading("Weather plugin not loaded.")
            return

        def work(stop_event):
            try:
                data = api.get_current_weather()
                text = f"{int(data['temperature_2m'])} degrees"
            except Exception as e:
                self.client.log("warning", f"[WeatherPage] Fetch failed: {e}")
                text = "unavailable"
            # Back to the UI thread before touching a widget.
            self.client.call_on_ui(lambda: self.set_reading(text))

        self.client.THREADS.create("weather_page_fetch", work)
        self.client.THREADS.start("weather_page_fetch")
```

Registered from a plugin:

```python
class WeatherPlugin(Plugin):
    def load(self, carryover=None):
        self.client.add_page("#weather", "Weather", WeatherPage, owner="weather")
```

Four things in there are the whole pattern:

* **`add_features()` in `__init__`.** A plugin cannot reach into the page's
  widgets, only through what the page chose to expose.
* **`start()` / `stop()`.** Optional. `goto()` calls them if they exist, which
  is where subscriptions and timers belong.
* **`resizeEvent()`.** The window can change size at runtime.
* **`call_on_ui`.** The fetch is on a worker thread; the `setText` is not.


## Sub-pages

> **`HomePage` and its sub-pages come from `corewidgetsbundle`.** A plugin
> adding a sub-page depends on that plugin being loaded, and should degrade
> rather than raise when it is not. `RootPage` and the settings page are the
> only two the client registers itself — see
> [Application lifecycle](lifecycle.md).


A page can own a grid of sub-pages navigated by swiping. `HomePage` does this:
each sub-page has a coordinate, and a swipe moves to whatever sits in that
direction.

```python
class MySubPage(SubPageFramework):
    def __init__(self, client, page):
        super().__init__(client=client, key="mysub", coord=(1, 0))
```

`coord` is `(x, y)`. `(0, 0)` is the origin, and a swipe left moves to
`(1, 0)`. A direction with nothing in it is a no-op, so gaps are fine.

### Adding one

Sub-pages are added through the parent page's features, not the page registry.
The parent has to exist first, so this belongs in `built()` or in a mixin on
the parent's `__init__`:

```python
class MyPlugin(Plugin):

    def built(self):
        home = self.client.get_page("#cwb_home_page")
        if home and home.instance and home.instance.has_feature("add_sub_page"):
            home.instance.features().add_sub_page("mysub", MySubPage)

    def unload(self, carryover=None):
        home = self.client.get_page("#cwb_home_page")
        if home and home.instance and home.instance.has_feature("remove_sub_page"):
            home.instance.features().remove_sub_page("mysub")
```

`add_sub_page(key, page_class)` applies mixins to the class, constructs it,
sizes it to the parent and moves it to `coord * size`. The key is what
`remove_sub_page()` takes.

Cleaning up in `unload()` matters here. The parent page outlives your plugin,
so a sub-page left behind is a page the user can still swipe to, built from a
module that no longer exists.

### Adding one to your own page

Any page can host sub-pages — `HomePage` is not special. Copy its pattern:
keep a dict of sub-pages by key, give each a `coord`, position them at
`coord * page size`, and move the container on swipe.

The features to expose are `add_sub_page` and `remove_sub_page`, named exactly
that, so a plugin written against `HomePage` works against yours unchanged.

### The page map

Holding on empty space on `HomePage` opens a map of its sub-pages. Tapping one
jumps straight to it rather than swiping there a page at a time.

Dragging a page onto another swaps them; dragging onto an empty slot moves it.
Empty slots are only offered next to a page that exists, so the map cannot grow
into coordinates nothing could reach by swiping.

The arrangement is saved to `sub_page_layout.json` in the user data directory
and applied in `start()` — after every plugin has registered its sub-pages,
since a saved coordinate for a page that has not been added yet would be lost.

**(0,0) must always hold a page.** It is where the app starts, so a layout with
an empty origin cannot be navigated to. The dialog will not close while the
origin is empty, and a saved layout that has one is discarded rather than
half-applied.

### Cleaning up a sub-page

`remove_sub_page()` calls `teardown()` on the page if it defines one, before
unparenting it. The parent's own `stop()` calls it too, so it runs whether the
page is removed by a plugin unloading or destroyed by navigating away — `goto()`
destroys a page rather than hiding it, and destruction alone runs nothing.

That is where a sub-page unsubscribes from anything it subscribed to:

```python
def teardown(self) -> None:
    self.client.unsubscribe_from_event("on_calendar_changed", self._on_changed)
```

Without it a removed page keeps its handlers on the event bus, and the first
fire afterwards calls into a deleted widget.

### Off-screen sub-pages

A sub-page grid keeps every page constructed and slides between them, so an
inactive page is fully built and completely off screen. **Anything it does on a
timer is work for something nobody can see**, and with a full set of widgets and
tiles placed that is the largest steady cost in the app while it sits idle.

Sub-pages are told when they come and go:

```python
class MySubPage(SubPageFramework):

    def on_activated(self) -> None:
        self._timer.start(1000)
        self.refresh()          # do not show a stale face until the next tick

    def on_deactivated(self) -> None:
        self._timer.stop()
```

Start timers in `on_activated()`, not in `__init__` — a page built at startup
that starts its own timer runs from launch whether or not anyone ever swipes to
it. Refresh once on activation as well, so the page is current the moment it
lands rather than a tick later.

Exactly one sub-page is active at a time: the one at the parent's current
coordinate. The parent resolves that after construction, after a saved layout
is applied, on every swipe, on a jump from the page map, and whenever a
sub-page is added. `is_active` reflects it and can still be assigned directly —
the hooks run either way.

Both bundled sub-pages do this: `sub.tiles` gates its tile tick, and `sub.home`
gates the widget framework's re-fit pass **and** every registered widget's own
tick — including the ones sitting in the widgets panel, which are off screen
twice over.

### Features from a sub-page

A sub-page's own features are re-exposed on the parent under the sub-page's
name, so a plugin reaches them through the parent it already has:

```python
self.client.action("sub.home.register_widget", MyWidget)
```

That is why the key is `sub.home.register_widget` and not just
`register_widget` — the prefix is the sub-page.


## What a page owns

Pages in `CoreWidgetsBundle` own things like:

* a `WidgetFramework` - see [Widgets](widgets.md)
* a `TileGrid` and its `TilePanel`
* sub-page navigation
* their own background

A page is free to own anything. What it must not do is assume a particular
plugin exists - if a plugin is unloaded, the page has to keep working without
whatever that plugin had added.


## Lifecycle

A page is constructed on navigation and destroyed on leaving. That means:

* Build interface in `__init__`, and expose features there with
  `add_features()`.
* `resizeEvent()` is where geometry is re-applied. The window can change size
  at runtime, and nothing lays a page out for you.
* `tick()` is called on the client's tick, if you define it. Keep it cheap -
  it runs on the UI thread.

Anything a plugin added to a page is gone when the page is rebuilt, which is
why widgets are **registered** rather than constructed and handed over. The
page rebuilds them from the saved layout on the way up.


## Global controls

Do not put app-level controls on a page. Settings access, fullscreen, quit and
anything else that should exist everywhere belong in
[quick settings](quick-settings.md), which is registered once against the
client and reachable from every page.

## The web page resumes where it was

`goto()` destroys the outgoing page, so a `#webpage` returned to is a **new
instance** — and one built from `data["url"]`, which is whatever the original
caller passed. Coming back from the night clock therefore reopened the address it
started on, at the top, whatever had been read in between.

The URL and scroll position are kept on the **client**, keyed on the browsing
context (`lock_base` plus the home URL) rather than on the page, since anything
stored on the page goes with it.

* An explicit `data["url"]` still wins. A caller asking for a page means go
  there; a caller asking for nothing means resume.
* A remembered address outside the context's `lock_base` is refused. It is not a
  way around the lock.
* Scroll is **polled** every two seconds. `QWebEngineView` emits no scroll
  signal, and injecting a listener that posts back would be a message per frame
  while a finger is dragging.
