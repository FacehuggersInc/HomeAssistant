# Backend API

A Flask server on port **5000**, separate from the Qt application. It exists so
the panel can be driven from elsewhere - a phone, a script, another machine on
the network - without touching the UI.

Everything except `/notify`, `/docs` and the two access endpoints needs a
**device token**.

There is no shared password. A device asks for access, somebody standing at the
panel allows or denies it, and the token that comes back belongs to that device
alone — revoking one does not affect the others, and an endpoint can tell who
is calling.

```
POST /access/request?name=My%20phone     ->  {"token": "...", "state": "pending"}
GET  /access/state?token=...             ->  {"state": "pending|approved|denied"}
```

Then send it on every request, as `?token=` or an `X-Client-Token` header:

```
http://<panel-ip>:5000/restart?token=YOUR_DEVICE_TOKEN
```

An unapproved token comes back **403** with its state, so a device polling for
approval can tell "not yet" from "no".

Approved devices are listed under **Settings → Users**, where each can be
revoked on its own.


## Client control

| Endpoint                         | Does                                                   |
|----------------------------------|--------------------------------------------------------|
| `GET /terminate`                 | Shut the panel down.                                   |
| `GET /restart`                   | Relaunch as-is.                                        |
| `GET /update`                    | Download, stage and restart into the newest commit.    |
| `GET /update/check`              | Report whether an update exists. Downloads nothing.    |
| `GET /notify?icon=&title=&body=` | Show a notification. No auth.                          |
| `POST /access/request?name=`     | Ask to be allowed. No auth, by definition.             |
| `GET /access/state?token=`       | Whether that request has been answered.                |
| `GET /process?...`               | Run an assistant intent.                               |
| `GET /pages`                     | Every registered page key, and which is on screen.     |
| `GET` or `POST` `/goto/<page>`   | Switch pages. Query parameters become the page's data. |
| `GET` or `POST` `/clipboard`     | Read the clipboard, or set it with `?text=`.           |
| `GET /clipboard/clear`           | Empty it.                                              |

`/update` returns as soon as staging starts - the download and restart happen
in the background. Poll `/plugins` to tell when the panel is back.


## Navigation

```
GET /pages?token=...
GET /goto/<page>?token=...&<anything else>
```

Everything in the query string except `token`, `id` and `override` is handed to
the page as its `data` - the same dict a plugin passes to `client.goto()`. So
the built-in [web page](webpage.md) is driveable from anywhere:

```bash
curl "http://panel:5000/goto/%23webpage?token=...\
&url=https://example.com/docs\
&home=https://example.com/docs\
&lock_base=https://example.com\
&lock_address=true"
```

Or through the CLI, which encodes the `#` for you:

```bash
./hactl.py goto webpage url=https://example.com/docs lock_address=true
./hactl.py pages
```

**The `#` must be percent-encoded** (`%23`) in a raw URL, or everything after it
is a fragment the server never sees. A key with no `#` at all is accepted and
the `#` put back, so `/goto/webpage` and `/goto/%23webpage` are the same request.

### Values are converted

A query string is all strings, and page data is not. `true`, `yes` and `on`
become `True`; `false`, `no` and `off` become `False`; digits become numbers;
everything else stays a string.

That conversion is load-bearing rather than tidy. `#webpage` reads its locks
with `bool(data.get("lock_address"))`, and `bool("false")` is `True` — so
without it, asking for an unlocked address bar would lock it, silently and
exactly backwards.

A bare `1` or `0` stays a number, since `zoom=1` is a number and
`lock_address=1` is truthy either way.

`POST` bodies (JSON or form) are merged on top of the query string, which is
where anything long or awkward to escape belongs.

`override=true` rebuilds the page even if it is already the current one -
`goto()` returns immediately otherwise.

Unregistered keys return **404** with the list of pages that do exist, rather
than failing silently the way `goto()` does on its own.


## Clipboard

```
GET  /clipboard?token=...              read it
GET  /clipboard?token=...&text=Hello   set it
POST /clipboard   {"text": "..."}      set it, for anything long
GET  /clipboard/clear?token=...        empty it
```

```bash
./hactl.py clipboard set "https://example.com/very/long/thing"
./hactl.py clipboard          # prints what is on it
./hactl.py clipboard clear
```

This is the app's clipboard, which is the system clipboard - the on-screen
keyboard's paste key reads the same thing, so this is how a URL gets from a
phone into a text field on the panel without typing it on a touch keyboard.

The clipboard belongs to the UI thread and these arrive on a Flask worker, so
the read is marshalled and waits for an answer. If the UI thread is wedged the
endpoint returns **500** with the reason rather than hanging the request.


## Settings

| Endpoint                       | Does            |
|--------------------------------|-----------------|
| `GET /settings/<path>`         | Read a setting. |
| `GET /settings/<path>?v=VALUE` | Write it.       |

Paths are dotted, matching the settings tree:

```
/settings/assistant.speech.model.value?token=...&v=parakeet-v2
/settings/application.updates.check_interval.value?token=...&v=12
```

Presence of `v` decides read from write, not its value - `?v=` with nothing
after it clears a setting rather than reading it.


## Plugins

| Endpoint                       | Does                                        |
|--------------------------------|---------------------------------------------|
| `GET /plugins`                 | Everything loaded and pending.              |
| `GET /plugins/<key>/info`      | One plugin in detail.                       |
| `GET /plugins/<key>/reload`    | Reload it.                                  |
| `GET /plugins/<key>/unload`    | Unload it.                                  |
| `GET /plugins/<key>/load`      | Load a pending plugin.                      |
| `GET /plugins/<key>/install`   | Install its pip requirements, then load it. |
| `GET /plugins/<key>/uninstall` | Remove its pip requirements.                |

`unload` returns **409** when another plugin depends on this one. Pass
`?force=1` to do it anyway - unloading underneath a dependant leaves it calling
into a module that is gone, so it is opt-in rather than silent.

`install` and `uninstall` run pip, so they return **202** immediately and
report the result as a notification on the panel.


## Plugin-registered endpoints

Anything a plugin registers with `APIRegistry` is served under `/public/`:

```python
class MyPlugin(Plugin):
    def load(self, carryover=None):
        self.client.API.register(
            "myplugin", "forecast", self.forecast,
            requires_auth=True, cached=False,
        )

    def forecast(self, city="Omaha"):
        return {"city": city, "temp": 21}      # or ({...}, 200)
```

```
GET /public/forecast?token=...&city=Omaha
```

Query parameters become keyword arguments. `id` is stripped first - it is the
API's, not the endpoint's. `POST` bodies (JSON or form) are merged in on top of
the query string.

A callback may return a bare value, `(body, status)`, or
`(body, status, headers)`. Bare values are sent as **200**.

The third form is how an endpoint serves a page rather than data:

```python
return html, 200, {"Content-Type": "text/html; charset=utf-8"}
```

Bad arguments return **400** with the `TypeError` text, which is far easier to
diagnose than a generic failure.

`cached=True` caches the first body and serves it thereafter, regardless of
return shape. Call `endpoint.clear_cache()` to invalidate.

### Receiving an upload

`POST` bodies reach an endpoint as keyword arguments, but **files do not unless
the endpoint asks for them**:

```python
self.client.API.register(
    "myplugin", "my_upload", self.handler,
    requires_auth=True, accepts_files=True)

def handler(self, files=None, **params):
    upload = files.get("file") if files else None
    data = upload.read() if upload else b""
```

`files` is Flask's `request.files`. Opt-in rather than always: forwarding it to
every endpoint would hand each one an unexpected keyword and raise `TypeError`,
which is the same trap `id` and `token` already sprang.


## The index

`http://<panel-ip>:5000/` is somewhere to start. It lists the pages the panel
serves and offers buttons for the actions worth having on a phone — ping, check
for an update, restart, shut down.

It is authed on purpose. A browser arriving with no token is walked through
approval and lands back on the index holding one, so **opening the panel's
address is the whole of setting a phone up**. Bookmark what you end up on.

Destructive actions ask first. "Shut down" on a phone in a pocket is a panel
somebody has to go and switch on again.

### Listing a plugin's page there

An endpoint that returns a page rather than data can say so, and the index
picks it up:

```python
self.client.API.register(
    "myplugin", "myplugin_form", self.api_form, requires_auth=True,
    gui="Add a thing",
    description="A page sized for a phone.")
```

`gui` is the label; leaving it empty keeps the endpoint off the index, which is
right for the great majority that return JSON.

### Listing an action there

An endpoint that *does* something rather than showing something becomes a
button instead:

```python
self.client.API.register(
    "calendar", "calendar_sync", self.api_sync, requires_auth=True,
    action="Sync calendars")
```

Same endpoint either way — the difference is whether opening it is the point,
or whether the point is that it ran. Add `danger=True` and the index styles it
as destructive and asks before running it.

Plugin actions are listed above the client's own, which are always there and
always the same.


## API classes, not only endpoints

`client.API` holds both. Alongside `register()` for an HTTP endpoint there is
`register_api()` for a class other plugins can call into:

```python
self.client.API.register_api("corewidgetsbundle", "weather",
                             OpenMeteoAPI(self, self.client))
```

```python
api = client.API.get("weather")
if api:
    reading = api.get_current_weather()
```

The Nighttime Clock uses the weather client this way rather than fetching its
own, and the RSS plugin registers its parser as `RSS`. See
[Registries](registries.md) for ownership and cleanup.

## Health

`GET /ping` answers whether the panel is up, which page is on screen, and how
long it has been running. Authenticated like everything else, and listed on the
index as a button.

```json
{"request": "Success", "alive": true, "app": "Desktop Home Assistant",
 "page": "#cwb_home_page", "uptime": "3h 41m", "uptime_seconds": 13260,
 "device": "Chris"}
```

`GET /backlight` reports what is driving the screen brightness. Add
`survey=1` to probe every route rather than only the one in use - slower,
because it includes a `ddcutil detect`, but it is the difference between "it
is using the overlay" and knowing why.

`GET /users` returns the approved devices, for anything building its own owner
picker rather than a text field. See [Users](users.md).

## Writing a page endpoint

An endpoint may return HTML rather than JSON, which is how a plugin ships an
interface for a phone without needing a page in the app.

How to build one - `page()` for a form, `WebAssets` for anything with a script
in it, the shared helpers and the icon set - is in
**[Web UI](web-ui.md)**.

## Endpoints the bundled plugins add

Registered like any other plugin endpoint, and served under `/public/`.

| Endpoint            | From         | Does                                                    |
|---------------------|--------------|---------------------------------------------------------|
| `timer_start`       | Core Widgets | Start a timer. `seconds=`, `minutes=`, `hours=` add up. |
| `timer_list`        | Core Widgets | Every timer the panel is counting.                      |
| `timer_cancel`      | Core Widgets | Cancel one by `key=`, or `all=1`.                       |
| `widget_show`       | Core Widgets | Place a transient widget on the home screen.            |
| `widget_dismiss`    | Core Widgets | Take one away.                                          |
| `sticker_add`       | Core Widgets | A page to upload and place stickers from a phone.       |
| `sticker_list`      | Core Widgets | The sticker library.                                    |
| `sticker_place`     | Core Widgets | Place one, permanently or for a while.                  |
| `sticker_remove`    | Core Widgets | Delete one from the library.                            |
| `calendar_add`      | Calendar     | Add an event.                                           |
| `calendar_upcoming` | Calendar     | The next few events.                                    |
| `calendar_sync`     | Calendar     | Refresh every subscribed feed.                          |

See each plugin's own documentation for the full argument list.

## Plugin-served pages

Registered like any other plugin endpoint and served under `/public/`.
Calendar has `calendar_form` for adding an event; Random Chance has
`randomchance_page` and the asset endpoint beside it. See
[Web UI](web-ui.md).

## Files

| Endpoint                      | Does                           |
|-------------------------------|--------------------------------|
| `GET /upload`                 | Index of upload keys.          |
| `GET /upload/<key>`           | Upload form for one key.       |
| `POST /upload/<key>`          | Receive a file.                |
| `GET /upload/<key>/files`     | What is in the folder.         |
| `GET /upload/<key>/file/<n>`  | One file, for the thumbnails.  |
| `POST /upload/<key>/delete`   | Remove the named files.        |
| `GET /asset/<key>`            | List assets under a key.       |
The last three need the folder marked **deletable** as well as uploadable, and
the two are separate on purpose: adding to a folder is undone by deleting what
was added, emptying one is not, and a folder somebody may put things into is
not automatically one they should be able to empty from a phone. Without the
mark they answer 403 and the page draws no listing.

`background_images` and `stickers` carry it. The listing shows a thumbnail for
anything that is an image and a filename for everything else; tapping marks,
and nothing is removed until the button at the bottom, which asks once by
count. `POST .../delete` answers for each name separately - `deleted` and
`failed` - because a batch that stops at the first refusal leaves the caller
unable to say which of the ten it asked about are still there.

| `GET /asset/<key>/<filename>` | Download one.                  |
| `GET /font/<name>`            | One of the panel's font files. |

`/font` is **not authed**. A typeface is not information, and the pages that
need it include the approval screen - a browser refused the font renders the
one page somebody has to read in order to get a token in a fallback face.


## Asking the assistant

`GET /process?q=...` is the machine-readable route: a bare JSON contract, and
what a script should call.

`GET /ask?q=...` is the same thing for a person. With no `q` it serves a form
with real examples on it; with one it answers `{"request", "what"}`, where
`what` is a sentence rather than a status.

Two endpoints on purpose. A page and a script sharing a URL means one of them
shapes the other - the page cannot grow a friendlier failure without changing
what scripts parse, and the script cannot stay terse once the page needs
prose. Both go through `STT.submit()`, so neither needs a wake word.

**The answer happens on the panel**, not in the browser. What comes back here
is whether anything took it, which is the only thing this end can know.


## Pages rather than data

Some endpoints answer with HTML because the thing asking is a browser rather
than a script.

| Endpoint                               | Does                                                      |
|----------------------------------------|-----------------------------------------------------------|
| `GET /`                                | The index. See [The index](#the-index).                   |
| `GET /ask`                             | Ask the assistant something. Serves the form with no `q`. |
| `GET /goto/page`                       | A page switcher for a device with a browser.              |
| `GET /clipboard/page`                  | The clipboard, as a page a phone can open.                |
| `GET /upload`, `GET /upload/<key>`     | Upload forms, above.                                      |
| `GET /access/wait`, `GET /access/name` | The approval flow. See [Users](users.md).                 |

`/goto/page` also matches `/goto/<path:page>`. Werkzeug sorts rules by
specificity rather than by declaration order, so the static one wins and there
is no page called `page` to collide with.


## Served to the panel's own browser

Three endpoints exist for the built-in web view and are **not authed**,
because that view has no token and no way to be given one.

| Endpoint                    | Does                               |
|-----------------------------|------------------------------------|
| `GET /webhome`              | What the panel's browser opens on. |
| `GET /bookmark/forget`      | Remove a bookmark, from that page. |
| `GET /bookmark-icon/<name>` | One saved favicon, served by name. |

None exposes anything a person standing at the panel cannot already see or do.
The icon is served by NAME with the path stripped to its last component,
because the folder it comes from is inside the user data directory. See
[The web page](webpage.md).


## Documentation

| Endpoint              | Does                                   |
|-----------------------|----------------------------------------|
| `GET /docs`           | This documentation, rendered. No auth. |
| `GET /docs/<page>`    | One page.                              |
| `GET /docs/<page>.md` | The raw markdown.                      |


## hactl.py

A single-file CLI at the project root, stdlib only and importing nothing from
`src/`. Copy it to any machine that can reach the panel.

```bash
./hactl.py hosts add panel --host 192.168.1.50   # pairs with the panel
./hactl.py update --check
./hactl.py update --wait
./hactl.py plugins list
./hactl.py settings assistant.speech.model.value parakeet-v2
```

Pairing happens once per machine and the token is cached in
`~/.config/hactl/hosts.json`, written `0600`. It is masked everywhere it is displayed, and prompts use
`getpass` so it stays out of shell history.

Multiple panels are supported with `-t NAME`; `--host` and `--token` override
without saving.

| Command                                       | Does                                                  |
|-----------------------------------------------|-------------------------------------------------------|
| `hosts list/add/remove/use`                   | Manage saved panels.                                  |
| `ping`                                        | `ready`, `starting`, `unauthorized` or `unreachable`. |
| `update [--check] [--wait]`                   | Check, or stage and restart.                          |
| `restart [--wait]`                            | Restart.                                              |
| `terminate [-y]`                              | Shut down.                                            |
| `notify ICON TITLE BODY`                      | Show a notification.                                  |
| `settings PATH [VALUE]`                       | Read or write.                                        |
| `pages`                                       | List pages; a `*` marks the one on screen.            |
| `goto PAGE [k=v ...]`                         | Switch pages, passing data.                           |
| `clipboard` with `get`, `set TEXT` or `clear` | Read, set or empty the clipboard.                     |
| `plugins ...`                                 | Everything under `/plugins`.                          |
| `public ENDPOINT [k=v ...]`                   | Call a registered endpoint.                           |
| `raw PATH [k=v ...]`                          | Anything else.                                        |

`--json` prints the raw reply. Exit codes are 0 success, 1 refused, 2 usage,
3 unreachable, so it is usable from a shell script.

`--wait` polls `/plugins`, which is read-only and safe to hit repeatedly. It
requires the panel to actually drop before counting it as back - otherwise a
poll landing between the reply and the restart reports success against the
process on its way out.

## The dashboard

`/` is the panel's dashboard: the pages a plugin has registered, the actions it
offers, who can reach it, and a live clock. One column on a phone, as many as
fit on a desktop.

### Icons

```python
client.API.register("myplugin", "my_page", self.my_page,
                    requires_auth=True, gui="My page", icon="rss")
```

Icons are drawn as **inline SVG**, not a font — a phone opening this has no icon
font, and shipping one to get twenty glyphs is a megabyte for nothing.
`src/webicons.py` holds the set; `mdi.rss` and `rss` are the same request, and a
name that is not there becomes a dot rather than a gap.

Add a path to `PATHS` if you need one that is missing.

### `/dashboard/state`

Everything the dashboard shows, in one request: the panel's name, uptime, what
page it is on, Wi-Fi, every connected Bluetooth device, the quiet modes,
brightness, bookmarks and recent notifications.

One round trip rather than six — this is polled from a phone that may be on the
far side of a house, and six requests is six chances for one to be the slow one.
Each part is guarded on its own, so a machine with no Bluetooth shows the rest.

### The update card

There is no "Update and restart" action. When `/dashboard/state` reports one
available, a card appears below the header and **is** the button — nobody should
have to know which of ten actions was the one to press. It says what is waiting
rather than that something is.

### `/say`

`GET /say?from=Kitchen&message=Dinner is ready` — the panel reads it out as
"Kitchen said dinner is ready". With no message it serves a form.

`say()` reports whether anything came out, so quiet mode and a missing voice are
the same answer, and neither loses the message: it goes on screen at panel size
instead.

`&voice=anna` uses that voice for this message and puts the setting back
afterwards — trying one from a phone is not deciding to change the panel's.

### `/quiet/<what>/<state>`

`dnd` or `mute`, `on` or `off`. A state rather than a toggle: the dashboard
already knows which way it is, so two phones pressing at once agree.

### `/quick`

Opens the panel's quick settings from wherever you are. The panel's own gesture
is a swipe from the top edge, which is no use from across the room — and
brightness, volume, Wi-Fi and do-not-disturb are exactly what somebody wants to
change without walking over.
