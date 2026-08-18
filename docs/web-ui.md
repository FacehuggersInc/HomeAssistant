# Web UI

Everything a phone sees comes from `src/webui.py`. A plugin that ships an
interface for a phone registers an endpoint that returns HTML, and the chrome
- the header, the back control, the shared styling - comes from here rather
than from the plugin.

There are two ways to build one, and the difference is where the markup
lives.

Not to be confused with [The web page](webpage.md), which is the panel's own
in-app browser. This is about pages the panel *serves*.


## Pages in files

**Use this for anything with a script in it.** The markup, styling and script
live in a `web/` folder beside `main.py`, and `WebAssets` reads them.

```
YourPlugin/
    main.py
    web/
        page.html
        page.css
        page.js
```

```python
from pathlib import Path
from src.webui import WebAssets

ASSETS = WebAssets(Path(__file__).with_name("web"),
                   required=("page.html", "page.css", "page.js"))


class YourPlugin(Plugin):

    KEY = "yourplugin"
    PATH = "/public/yourplugin_page"

    def load(self, carryover=None):
        self.client.API.register(self.KEY, "yourplugin_page", self.api_page,
                                 requires_auth=True, gui="Your Plugin",
                                 icon="mdi.puzzle")
        ASSETS.register(self.client, self.KEY)

        for name in ASSETS.missing():
            self.client.log("error", f"[Your Plugin] missing {name}")

    def api_page(self, **kwargs):
        html = ASSETS.page(
            title="Your Plugin",
            blurb="What this page is for.",
            token=token, endpoint=self.PATH,
            data={"things": self.store.all()},
        )
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}
```

`register()` wires the endpoint that serves the large files and remembers its
URL, so the plugin never mentions it again. Doing it by hand means the URL
appears in two places - the handler and the markup that links it - and two
places kept in step by hand is a page linking a script nobody serves, which
is silent until somebody opens it.

### A worked example

The three files, at their smallest useful size. A page that lists whatever
the panel sent and lets you add one.

`web/page.html` — static, no placeholders:

```html
<section class="card">
  <label>Things</label>
  <ul id="things"></ul>
  <div class="row">
    <input id="new" placeholder="Add one">
    <button type="button" onclick="add()">Add</button>
  </div>
</section>
<button type="button" id="save" onclick="save()">Save</button>
```

`web/page.js` — reads the one object, writes text:

```js
var PAGE = window.PAGE || {};
var things = PAGE.things || [];

function draw() {
  var host = document.getElementById('things');
  host.innerHTML = '';
  things.forEach(function (thing, index) {
    var row = document.createElement('li');
    /* textContent, not innerHTML. A name that came from outside is text. */
    row.textContent = thing;
    var drop = document.createElement('button');
    drop.type = 'button';
    drop.textContent = '\u00d7';
    /* The row's index, not its name. A name would have to be quoted inside
       an attribute, and that is the escaping layer this all exists to
       avoid. The rows are redrawn after every change, so an index is never
       stale by the time it is used. */
    drop.onclick = function () { things.splice(index, 1); draw(); };
    row.appendChild(drop);
    host.appendChild(row);
  });
}

function add() {
  var field = document.getElementById('new');
  if (field.value.trim()) { things.push(field.value.trim()); }
  field.value = '';
  draw();
}

function save() {
  var data = new FormData();
  data.set('things', JSON.stringify(things));
  data.set('token', PAGE.token || '');
  fetch(PAGE.endpoint, {method: 'POST', body: data});
}

draw();
```

`main.py` — sends data, never markup:

```python
def api_page(self, things: str = "", **kwargs):
    if things:
        self.store.replace(json.loads(things))

    html = ASSETS.page(
        title="Things", token=token, endpoint=self.PATH,
        data={"things": self.store.all()},
    )
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}
```

### Posting without leaving the page

A page whose state lives in a JavaScript array — a list being edited, a set
of rules — should post with `fetch` rather than submitting. A normal submit
navigates, the answer arrives as a fresh document, and everything picked is
gone. On a phone that means re-entering it to do the same thing twice.

```js
function post(fields) {
  var data = new FormData();
  Object.keys(fields).forEach(function (key) { data.set(key, fields[key]); });
  data.set('token', PAGE.token || '');
  data.set('fmt', 'json');
  return fetch(PAGE.endpoint, {method: 'POST', body: data})
    .then(function (r) { return r.json(); })
    .then(function (answer) { banner(answer.message, answer.bad); })
    .catch(function () { banner('Could not reach the panel.', true); });
}
```

The endpoint answers with the message alone when asked for `fmt=json`, and
with the page otherwise:

```python
if str(fmt).strip().lower() == "json":
    return {"message": message, "bad": bad}
return ASSETS.page(...), 200, {"Content-Type": "text/html; charset=utf-8"}
```

**Do not put the answer in the reply** if the panel is where it is shown.
Random Chance's page says "Flipping the coin", not which side won — reading
the result off a phone while the coin is still turning spoils the only
interesting second of it.

### Nothing is templated

The files are served exactly as written. Not formatted, not substituted into,
not escaped. Everything the panel has to say arrives as one JSON object:

```js
var data = window.PAGE || {};
```

`ASSETS.page(data=...)` writes that object, with `token` and `endpoint` added
to it. `window.PAGE` is the same name on every page, so a script never has to
be told what its own plugin called it.

**This is the point rather than a detail.** A quote inside an HTML attribute
inside a JavaScript string inside a Python string passes through two rounds of
escape processing, and one of them eats the backslash. A page whose script
fails to parse renders as a row of tabs and nothing else: every pane is
`display:none` until the first `show()` adds a class, so the script dies
before it can reveal one and nothing anywhere says why.

`str.format()` is worse again: CSS and JavaScript are full of braces and it
would try to substitute every one of them.

Being files also means an editor highlights them, `node --check` runs on the
script directly, and a formatter can be pointed at any of them.

### Small assets are inlined, large ones served

Anything over `INLINE_LIMIT` (4 KB) is fetched from the asset endpoint;
anything under it is written into the page. A couple of kilobytes of CSS costs
less inline than a second request does, and sixteen kilobytes of script is
worth fetching once and caching.

A served asset carries a hash of its own contents in its URL and is cached
hard. Without that, an update leaves every phone holding the previous script
against the new panel - a failure that looks like the panel rather than the
cache.

### What the folder may hold

`.html`, `.css` and `.js`, and nothing else. The listing is the allowlist:
`read()` compares a name against what is actually in the folder rather than
joining it onto a path, so `../main.py` is refused without any thinking about
separators or symlinks. Globbing every file would publish an editor backup or
a stray notes file the moment somebody saved one next to the page.

### Missing files are named at load

`ASSETS.missing()` is meant to be called when the plugin loads, so a file left
out of a build is a line in the log with the path in it rather than a 500 the
first time somebody opens the page. `verify_siblings()` does the same for
Python modules.

### Values from outside are data, never markup

A page is full of values it did not choose — a list's title, a timer's name, a
sticker's filename, and on the calendar page the error text a stranger's
server returns when a feed fails. Interpolating one into markup is safe only
if `escape()` is called at that site, and only if it is called at every other
site too.

Sending them as data removes the question. The script writes them with
`textContent`, so they are text because of the shape rather than because
somebody remembered:

```js
/* Yes */
row.textContent = thing.name;

/* No - and no amount of escaping on the Python side changes it */
host.innerHTML += '<li>' + thing.name + '</li>';
```

The one exception worth knowing: building an element and then serialising it
back through `outerHTML` into `innerHTML` relies on the browser escaping on
the way out. It does — but it is a reliance with no purpose. Append the
element instead.

### Long lists

A page showing many items - a sticker library, a folder of uploads - gets a
search box, a sort control and a cap on how many are drawn, from the shared
helper in `src/web/listing.js`:

```python
ASSETS.page(title="Stickers", token=token, endpoint=PATH,
            also=("listing.js",), data={"stickers": [...]})
```

```js
var result = Listing.view(items, {
  query: box.value, sort: pick.value,
  fields: ['label', 'name'], window: 60
});
result.shown.forEach(draw);
note.textContent = Listing.summary(result, 'sticker');
```

**The cap limits what is drawn, not what is searched.** A cap applied before
the filter is a search that misses whatever sits past it, which is worse than
having no search. `result.hidden` is how many matched and are not on screen,
so a page can offer them rather than pretending they are not there.

**Growing appends.** `from` is where the window starts, so a page that has
drawn sixty asks for the next sixty and appends them, leaving what is already
on screen alone. Asking for "the first 260" instead means building 260 tiles
and throwing away the 60 that were fine — work that grows the further down
somebody is.

```js
var result = Listing.view(items, {..., from: drawn, window: 60});
drawn += result.shown.length;
result.shown.forEach(draw);

if (result.hidden) {
  grid.appendChild(marker);              // a button, with an id
  stop = Listing.whenNear(marker, grow); // and drawn by scrolling to it
}
```

`whenNear` watches a marker after the last tile with an `IntersectionObserver`
and a 400px margin, so the next batch starts before the bottom is reached. It
hands back a function to stop watching, which a page redrawing from the top
must call. A browser without `IntersectionObserver` gets `null` and the marker
stays a button.

Keep the marker a real button either way: it is the fallback, and some people
would rather press something than scroll.

`summary()` answers the one question a filtered list raises: whether a thing
is absent or merely not shown. Sorting is by `name`, `newest`, `oldest` or
`largest`, and an item needs `modified` and `size_bytes` for the last three -
read them from disk in the endpoint, since a page cannot ask.

A page using it says so with `also=`, which links it from the head with its
own fingerprint. Both pages that need this share the one file, so neither can
drift from the other.

### Several pages, one folder

A plugin serving more than one page keeps them in the same folder and names
the files per page. Declare `WebAssets` once - in the plugin's `__init__.py`,
so the module that happens to be imported first does not own it - and pass
the file names at render time:

```python
ASSETS.page(title="Checklist", token=token, endpoint=PATH,
            body_file="list.html", css_file="list.css",
            script_file="list.js", data={...})
```

One `register()` call serves all of them.

### Loaded by sibling()

A page module reached through `sibling()` **cannot use a relative import**.
`sibling()` loads a file under a name whose package has never existed, so
`from .. import ASSETS` resolves perfectly as an ordinary import and fails on
the panel. Write it absolute:

```python
from src.assets.bundled.YourPlugin import ASSETS
```

### The methods

| Method                        | Does                                          |
|-------------------------------|-----------------------------------------------|
| `page(title, token, data...)` | The whole document, from the folder.          |
| `inline_or_link(name)`        | `(inline, tag)` - one filled, by size.        |
| `link(name)` / `tag(name)`    | The URL, and the element for it.              |
| `register(client, key)`       | Wires the asset endpoint, returns its path.   |
| `missing()`                   | Required files that are not there.            |
| `names()`                     | Every asset in the folder.                    |
| `read(name)`                  | One asset, or `""` if it is not on the list.  |
| `fingerprint(name)`           | Eight characters of the file's own hash.      |
| `serve(name)`                 | The endpoint handler.                         |


### The panel's own pages

The panel serves pages of its own - the documentation viewer, the plugin
manager - and they keep their files in `src/web/`. `core_assets()` in
`src/webui.py` is that folder, served from `/web`, and the route is
unauthenticated for the same reason `/docs` is: it is the stylesheet for a
page anybody can already open, and a token would mean the browser could not
fetch it after following the link.

The documentation viewer does not use `page()` at all - it has a sidebar, a
filter and a table of contents - so it uses the asset half on its own:

```python
assets = core_assets()
sheet, sheet_tag = assets.inline_or_link("docs.css")
script, script_tag = assets.inline_or_link("docs.js")
```

Exactly one of each pair is filled, following the same size rule. A page that
builds its own document puts the inline text in a `<style>` or `<script>` and
the tag beside it.

`page()` takes a `head=` argument for the same reason. A `<link>` placed in
the body works, but it is read after the page has been laid out once, which
shows as a flash of unstyled text.

### Not every page in a plugin is a served page

A module that imports Qt and holds markup is building a document for a **web
view** - a map, a player shell - loaded into the panel rather than sent to a
phone. It has no endpoint, no token and nothing to cache, so `WebAssets` has
nothing to offer it. A scan for markup left in Python should skip any module that imports Qt for
that reason.


## Pages built inline

**Use this for a form with a handful of fields and no script.** `page()`
builds a whole document and the caller supplies only its content.

```python
from src.webui import page

def render_page(token, message=""):
    body = """
<section class="card">
  <label for="name">Name</label>
  <input id="name" name="name">
</section>
"""
    return page(
        title="Something",
        heading="Something",
        blurb="What this page is for.",
        token=token, message=message,
        css=" .mine{color:var(--accent)}",
        body=body,
    )
```

| Argument                            | Meaning                                               |
|-------------------------------------|-------------------------------------------------------|
| `title`                             | The browser tab.                                      |
| `body`                              | The page's own markup.                                |
| `token`                             | The caller's token, for the back control.             |
| `heading` / `blurb`                 | The `h1` and the line under it.                       |
| `message` / `bad`                   | The status banner, and whether it reads as a failure. |
| `css`                               | Rules this page needs that the chrome does not carry. |
| `script`                            | JavaScript, placed at the end of the body.            |
| `back` / `back_label` / `back_href` | The back control, on by default.                      |

The moment a page grows a script worth naming, move it to `web/`. The
rationale for keeping pages inline was written when a page was six fields; it
does not survive fifteen kilobytes of JavaScript.


## Style plain elements, not classes

A page that uses `<button>`, `<h1>`, `<h2>`, `<section>`, `<label>` and
`.card` inherits the current look with nothing to edit. That is how every
served page stays consistent when the look changes.

`chrome_css()` styles `select` and `option` as well as `input`. Styling only
`input` is why one page's dropdown was the browser's own white control on an
otherwise dark page.


## The parts every page shares

| From `src.webui`     | Gives you                                             |
|----------------------|-------------------------------------------------------|
| `page()`             | A whole document, chrome included.                    |
| `WebAssets`          | A page kept as files.                                 |
| `chrome_css()`       | The palette, field styling and the back button's CSS. |
| `back_button(token)` | A styled back control, with the token on it.          |
| `subnav(items)`      | A row of links between related pages.                 |
| `banner(message)`    | The status line, on its own.                          |
| `position_grid()`    | The nine-cell picker, with `POSITION_SCRIPT`.         |
| `escape(text)`       | `html.escape` with `quote=True`, for attributes.      |


## Icons

Inline SVG, from `src/webicons.py`. A phone has no icon font and shipping one
for twenty glyphs is a megabyte for nothing. `mdi.rss` and `rss` are the same
request; an unknown name becomes a dot rather than a gap, so a missing icon is
a page that looks unfinished rather than one that fails. Add a path to `PATHS`
when one is missing.

An `icon=` passed to `API.register` is drawn from this set, not from the
panel's own qtawesome icons - a name that works on the settings page is not
necessarily in here.


## Templates

The panel's own Jinja templates live in **`src/web/templates/`** — beside its
stylesheets and scripts, so everything it serves a browser is in one place. A
subfolder on purpose: `core_assets()` globs the top of `src/web/` and nothing
below it, so a template is never servable as an asset. A `.html` served raw is
its Jinja source, which is the page's structure and whatever a `{% if %}` was
guarding.

A page rendered from there gets `chrome` and `back_button` from a context
processor, so a route passes neither.

```html
<style>
{{ chrome|safe }}
 .mine{color:var(--accent)}
</style>
...
<p>{{ back_button(token)|safe }}</p>
```

This is for the client's own pages. A plugin wanting a template engine should
use `WebAssets` instead: `{{ }}` inside a `<script>` block is another escaping
layer, and that is the thing being avoided.


## There is no static folder

Flask is given `static_folder=None`. It mounts `/static` whether anything is
in it or not, and the panel has nothing to put there — its CSS and JavaScript
go through `/web`, which fingerprints them and lets a browser cache them
properly. An empty mount is a URL that answers, an endpoint in `url_for`
that resolves, and a thing for the next person to wonder about.

Anything a browser needs goes in `src/web/` for the panel, or a plugin's own
`web/` folder.
