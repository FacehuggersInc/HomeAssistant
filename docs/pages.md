# Pages

A page owns a UI system. It builds its own interface, decides how that
interface is laid out, and exposes the parts plugins are allowed to touch as
[Features](features.md).

Plugins do not build pages' interiors. They ask a page to do something.

---

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

---

## Sub-pages

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

Sub-pages are added through the parent's features rather than the page
registry:

```python
home.features().add_sub_page(MySubPage)
```

A sub-page's own features are exposed under its name, so
`sub.home.register_widget` reaches `SubHomePage`.

---

## What a page owns

Pages in `CoreWidgetsBundle` own things like:

* a `WidgetFramework` - see [Widgets](widgets.md)
* a `TileGrid` and its `TilePanel`
* sub-page navigation
* their own background

A page is free to own anything. What it must not do is assume a particular
plugin exists - if a plugin is unloaded, the page has to keep working without
whatever that plugin had added.

---

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

---

## Global controls

Do not put app-level controls on a page. Settings access, fullscreen, quit and
anything else that should exist everywhere belong in
[quick settings](quick-settings.md), which is registered once against the
client and reachable from every page.
