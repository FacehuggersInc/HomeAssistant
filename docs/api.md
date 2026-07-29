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

---

## Client control

| Endpoint | Does |
|---|---|
| `GET /terminate` | Shut the panel down. |
| `GET /restart` | Relaunch as-is. |
| `GET /update` | Download, stage and restart into the newest commit. |
| `GET /update/check` | Report whether an update exists. Downloads nothing. |
| `GET /notify?icon=&title=&body=` | Show a notification. No auth. |
| `POST /access/request?name=` | Ask to be allowed. No auth, by definition. |
| `GET /access/state?token=` | Whether that request has been answered. |
| `GET /process?...` | Run an assistant intent. |
| `GET /pages` | Every registered page key, and which is on screen. |
| `GET|POST /goto/<page>` | Switch pages. Query parameters become the page's data. |
| `GET|POST /clipboard` | Read the clipboard, or set it with `?text=`. |
| `GET /clipboard/clear` | Empty it. |

`/update` returns as soon as staging starts - the download and restart happen
in the background. Poll `/plugins` to tell when the panel is back.

---

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

---

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

---

## Settings

| Endpoint | Does |
|---|---|
| `GET /settings/<path>` | Read a setting. |
| `GET /settings/<path>?v=VALUE` | Write it. |

Paths are dotted, matching the settings tree:

```
/settings/assistant.model.value?token=...&v=small.en
/settings/application.updates.check_interval.value?token=...&v=12
```

Presence of `v` decides read from write, not its value - `?v=` with nothing
after it clears a setting rather than reading it.

---

## Plugins

| Endpoint | Does |
|---|---|
| `GET /plugins` | Everything loaded and pending. |
| `GET /plugins/<key>/info` | One plugin in detail. |
| `GET /plugins/<key>/reload` | Reload it. |
| `GET /plugins/<key>/unload` | Unload it. |
| `GET /plugins/<key>/load` | Load a pending plugin. |
| `GET /plugins/<key>/install` | Install its pip requirements, then load it. |
| `GET /plugins/<key>/uninstall` | Remove its pip requirements. |

`unload` returns **409** when another plugin depends on this one. Pass
`?force=1` to do it anyway - unloading underneath a dependant leaves it calling
into a module that is gone, so it is opt-in rather than silent.

`install` and `uninstall` run pip, so they return **202** immediately and
report the result as a notification on the panel.

---

## Plugin-registered endpoints

Anything a plugin registers with `APIRegistry` is served under `/public/`:

```python
class MyPlugin(Plugin):
    def load(self, carryover=None):
        self.client.API_REGISTRY.register(
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
self.client.API_REGISTRY.register(
    "myplugin", "my_upload", self.handler,
    requires_auth=True, accepts_files=True)

def handler(self, files=None, **params):
    upload = files.get("file") if files else None
    data = upload.read() if upload else b""
```

`files` is Flask's `request.files`. Opt-in rather than always: forwarding it to
every endpoint would hand each one an unexpected keyword and raise `TypeError`,
which is the same trap `id` and `token` already sprang.

---

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
self.client.API_REGISTRY.register(
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
self.client.API_REGISTRY.register(
    "calendar", "calendar_sync", self.api_sync, requires_auth=True,
    action="Sync calendars")
```

Same endpoint either way — the difference is whether opening it is the point,
or whether the point is that it ran. Add `danger=True` and the index styles it
as destructive and asks before running it.

Plugin actions are listed above the client's own, which are always there and
always the same.

---

## Endpoints the bundled plugins add

Registered like any other plugin endpoint, and served under `/public/`.

| Endpoint | From | Does |
|---|---|---|
| `timer_start` | Core Widgets | Start a timer. `seconds=`, `minutes=`, `hours=` add up. |
| `timer_list` | Core Widgets | Every timer the panel is counting. |
| `timer_cancel` | Core Widgets | Cancel one by `key=`, or `all=1`. |
| `widget_show` | Core Widgets | Place a transient widget on the home screen. |
| `widget_dismiss` | Core Widgets | Take one away. |
| `sticker_add` | Core Widgets | A page to upload and place stickers from a phone. |
| `sticker_list` | Core Widgets | The sticker library. |
| `sticker_place` | Core Widgets | Place one, permanently or for a while. |
| `sticker_remove` | Core Widgets | Delete one from the library. |
| `calendar_add` | Calendar | Add an event. |
| `calendar_upcoming` | Calendar | The next few events. |
| `calendar_sync` | Calendar | Refresh every subscribed feed. |

See each plugin's own documentation for the full argument list.

## Plugin-served pages

An endpoint may return HTML rather than JSON, which is how a plugin ships a
small interface for a phone without needing a page in the app.

The Calendar plugin does this with `calendar_form` — a mobile-sized page for
adding an event, served at
`/public/calendar_form?token=...`. See the Calendar plugin's own docs.

---

## Files

| Endpoint | Does |
|---|---|
| `GET /upload` | Index of upload keys. |
| `GET /upload/<key>` | Upload form for one key. |
| `POST /upload/<key>` | Receive a file. |
| `GET /asset/<key>` | List assets under a key. |
| `GET /asset/<key>/<filename>` | Download one. |

---

## Documentation

| Endpoint | Does |
|---|---|
| `GET /docs` | This documentation, rendered. No auth. |
| `GET /docs/<page>` | One page. |
| `GET /docs/<page>.md` | The raw markdown. |

---

## hactl.py

A single-file CLI at the project root, stdlib only and importing nothing from
`src/`. Copy it to any machine that can reach the panel.

```bash
./hactl.py hosts add panel --host 192.168.1.50   # pairs with the panel
./hactl.py update --check
./hactl.py update --wait
./hactl.py plugins list
./hactl.py settings assistant.model.value small.en
```

Pairing happens once per machine and the token is cached in
`~/.config/hactl/hosts.json`, written `0600`. It is masked everywhere it is displayed, and prompts use
`getpass` so it stays out of shell history.

Multiple panels are supported with `-t NAME`; `--host` and `--token` override
without saving.

| Command | Does |
|---|---|
| `hosts list/add/remove/use` | Manage saved panels. |
| `ping` | `ready`, `starting`, `unauthorized` or `unreachable`. |
| `update [--check] [--wait]` | Check, or stage and restart. |
| `restart [--wait]` | Restart. |
| `terminate [-y]` | Shut down. |
| `notify ICON TITLE BODY` | Show a notification. |
| `settings PATH [VALUE]` | Read or write. |
| `pages` | List pages; a `*` marks the one on screen. |
| `goto PAGE [k=v ...]` | Switch pages, passing data. |
| `clipboard [get\|set TEXT\|clear]` | Read, set or empty the clipboard. |
| `plugins ...` | Everything under `/plugins`. |
| `public ENDPOINT [k=v ...]` | Call a registered endpoint. |
| `raw PATH [k=v ...]` | Anything else. |

`--json` prints the raw reply. Exit codes are 0 success, 1 refused, 2 usage,
3 unreachable, so it is usable from a shell script.

`--wait` polls `/plugins`, which is read-only and safe to hit repeatedly. It
requires the panel to actually drop before counting it as back - otherwise a
poll landing between the reply and the restart reports success against the
process on its way out.
