# The web page

A browser page the client registers itself, for anything that needs to show a web page without carrying a browser of its own.

---

`#webpage` is a page the client registers itself, alongside `#root` and
`#settings`. Back, forward, reload and home, an address bar that opens the
on-screen keyboard, and the page itself with a small margin.

It exists so that several things wanting to show a web page — the docs, a
plugin's own interface, a login that has to happen in a real browser engine —
do not each have to carry a browser.

```python
self.client.goto("#webpage", data={
    "url":  "http://192.168.1.50:5000/docs",
    "home": "http://192.168.1.50:5000/docs",
})
```

| Key in `data` | Meaning |
|---|---|
| `url` | What to load. |
| `home` | Where the home button goes. Defaults to `url`. |
| `lock_address` | `True` stops the address bar being edited. |
| `lock_base` | A prefix every navigation must start with. |

The two locks answer different questions and are usually set together.
`lock_address` is about who may *type* an address; `lock_base` is about where
any navigation may go, typed or clicked. Setting only the first still leaves
every link on the page live.

`lock_base` is enforced at the engine, in `acceptNavigationRequest`, not by
watching for a URL change and going back — by the time a URL has changed the
page has already been fetched. A locked page shows a padlock beside its
address bar, and a refused navigation says so rather than doing nothing.

```python
self.client.goto("#webpage", data={
    "url":          docs_url,
    "home":         docs_url,
    "lock_base":    docs_url,
    "lock_address": True,
})
```

That is how the quick settings docs button opens: the panel is a shared screen,
and "open the documentation" should not also be a way to browse anywhere from
it.

And as features, for code that already has the page:

| Feature | Does |
|---|---|
| `navigate(url)` | Load something. A missing scheme becomes `https://`. |
| `set_home(url)` | Change where home goes. |
| `current()` | The address currently loaded. |

## Built for a panel

A browser engine assumes a mouse, a keyboard and someone sitting close to the
screen. None of those hold on a wall panel, so the page adjusts for each.

**Zoom**, because the commonest problem with a web page on a panel is that it
was laid out for somebody 60cm away. Nine steps from 75% to 250%, starting at
115% — a shade larger than a desktop, read standing up. Pass `zoom` in `data`
to start somewhere else.

**Drag to scroll.** A panel sends mouse events rather than touch ones, so the
engine's own touch scrolling never engages and the only way down a page is a
14px scrollbar. Dragging anywhere scrolls, and a flick carries on afterwards.
Links still work: the drag only begins past an 8px threshold, and the release
that ended one is swallowed so it does not also follow whatever was under your
finger.

**Jump to top**, since a long page is a long way back by drag alone.

**No context menu.** A long press otherwise offers "open in new window" and
"view source", which are both ways out of a locked page.

**A hairline progress bar** under the toolbar. Two pixels, no text — enough to
say something is happening without taking room from the page.

**Styled scrollbars.** The engine's default is a white desktop scrollbar,
which on this palette is the brightest thing on screen. The styling is injected
as a script at `DocumentReady` in `ApplicationWorld`, so it applies to whatever
is navigated to next and a page's own scripts cannot remove it.

---

### Idle

The page sets `blocks_idle = True`, and the client's idle check honours that on
any page that declares it. A web page is read at a person's own pace and
produces no interaction while it is, so the idle clock is measuring the wrong
thing — a screensaver taking over mid-paragraph is worse than one that waits.

Any page can do the same:

```python
class MyPage(PageFramework):
    blocks_idle = True
```

### It needs a browser engine

`PyQt6-WebEngine` is a dependency, so a failure here means an incomplete
install rather than a missing extra. The page still opens and says so instead
of failing to build — a page that cannot be navigated to is worse than one that
explains itself.
