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

## Typing into a page

A field on the page is read-only and taps open the on-screen keyboard, the same
as everywhere else. Two things about that are worth knowing.

The page talks back through **`console.log('__ha_field:...')`**, caught by the
page object's `javaScriptConsoleMessage`.

**Not `document.title`.** That title is a shared, observable thing: Google
Analytics reads it and sends it as the `dt` parameter, so anything written there
travels to whichever tracker the page runs — for a field signal that means its
id, its label and **its current value**. It is visible in the panel's own title
bar as well. The console goes nowhere.

That also means the page object is installed whether or not there is a
`lock_base` — without it there is no channel, and no keyboard.

**The title comes from what a person would read**, in order: `aria-label`,
`placeholder`, `title`, a `<label>` tied to or wrapping the field, then
`aria-labelledby`. The `name` attribute is not among them — Google's search box
is `name="q"` with no placeholder, and a dialog titled "q" says nothing about
what is being typed.

When none of those exist, the fallback is the **site plus what the field
probably does**: `google.com search`, from the hostname and whether the type,
name or role looks like a search. That beats both "q" and "Text".

**Done submits.** Setting a value fires `input` and `change`, which tells a
framework the field changed and tells the page nothing. A search box updated
that way sat there with the query in it, which from the outside looks exactly
like the dialog having failed.

So Done also dispatches a real Enter — sites that listen for it do so *instead*
of using a form — and then calls `requestSubmit()` on the form if there is one.
`requestSubmit()` rather than `submit()`, because the latter skips the page's own
submit handlers and its validation. A page that refuses the submit is caught, and
Enter may already have done the job.

## What the panel cannot fix

A page's **Content Security Policy** applies to anything injected into it, so on
a site with a strict `style-src` the custom scrollbar styling is refused and the
native scrollbars are what you get. There is no way around that from this side,
and there should not be.

A site refusing its own resources — analytics on a domain missing from its own
`connect-src`, for instance — is between it and its analytics. Those console
lines are dropped rather than logged: one page load can produce a dozen, and a
log full of them looks like the panel failing when the site is merely strict.
Everything else a page prints still reaches the log.

## Bookmarks

Client-owned, in `src/bookmarks.py`. The web page belongs to the client and its
toolbar does too, so a list of addresses that disappears when somebody unloads a
plugin is not a bookmark list.

The star in the toolbar saves the page on screen, filled when it is already
saved. Icons come from **the view** rather than the network — the engine has
already downloaded the favicon to draw with, and asking again would need the
network up at the exact moment somebody pressed the button.

`/webhome` replaces `about:blank` as the home address: a grid of bookmarks, a
clock and a DuckDuckGo box. It is not authed, because it is served to the
panel's own web view, which has no token and no way to be given one.

## `on_web_event`

One event carrying a `kind`, rather than one event per thing that can happen:

`loaded` · `changed` · `home` · `refreshed` · `bookmarked` · `unbookmarked` ·
`error`

A subscriber wanting two of them would otherwise register twice, and a new kind
later would be a new event name that nothing is listening for. An undeclared
kind is refused rather than delivered.

## Dark pages

Chromium runs with `forceDarkModeEnabled`, set in `app.py` **before anything
imports WebEngine** — the flags are read once when the engine initialises, and
anything later is ignored.

Force-dark *inverts* a light page rather than asking it for a dark theme, so
`forceDarkModeImagePolicy=1` leaves images alone; without it photographs come out
as negatives.

`/webhome` declares `color-scheme: dark` and is therefore skipped, which is what
stops the one page already dark from being inverted into a white rectangle. Any
page you write for the panel should do the same.

## The search box

An address goes to the address; everything else is a search. Raw IPs, `host:port`
and `localhost` all navigate directly — typing `192.168.1.4` and being handed
search results about it is the most annoying thing a search box on a home network
can do, and that is most of what this box is for.

Guarded both ways: `999.1.2.3` is not a valid octet and `3.14` has no letter in
its last label, so both are treated as searches.

## Login fields

A field of type `password` or `email` is **filled but not submitted**. Sending
the form the moment a password is typed submits it with whatever the other field
happens to hold — usually nothing, since the email is filled second half the
time — and a failed sign-in attempt is not something to trigger on somebody's
behalf.
