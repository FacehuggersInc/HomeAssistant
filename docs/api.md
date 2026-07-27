# Backend API

A Flask server on port **5000**, separate from the Qt application. It exists so
the panel can be driven from elsewhere - a phone, a script, another machine on
the network - without touching the UI.

Everything except `/notify` and `/docs` requires the client ID as `?id=`. You
will find it under **Settings → Info**.

```
http://<panel-ip>:5000/restart?id=YOUR_CLIENT_ID
```

The ID is compared with `hmac.compare_digest`, so a wrong one cannot be guessed
a character at a time by measuring how long the reply takes.

---

## Client control

| Endpoint | Does |
|---|---|
| `GET /terminate` | Shut the panel down. |
| `GET /restart` | Relaunch as-is. |
| `GET /update` | Download, stage and restart into the newest commit. |
| `GET /update/check` | Report whether an update exists. Downloads nothing. |
| `GET /notify?icon=&title=&body=` | Show a notification. No auth. |
| `GET /process?...` | Run an assistant intent. |

`/update` returns as soon as staging starts - the download and restart happen
in the background. Poll `/plugins` to tell when the panel is back.

---

## Settings

| Endpoint | Does |
|---|---|
| `GET /settings/<path>` | Read a setting. |
| `GET /settings/<path>?v=VALUE` | Write it. |

Paths are dotted, matching the settings tree:

```
/settings/assistant.model.value?id=...&v=small.en
/settings/application.updates.check_interval.value?id=...&v=12
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
GET /public/forecast?id=...&city=Omaha
```

Query parameters become keyword arguments. `id` is stripped first - it is the
API's, not the endpoint's. `POST` bodies (JSON or form) are merged in on top of
the query string.

A callback may return a bare value or a `(body, status)` tuple. Bare values are
sent as **200**.

Bad arguments return **400** with the `TypeError` text, which is far easier to
diagnose than a generic failure.

`cached=True` caches the first body and serves it thereafter, regardless of
return shape. Call `endpoint.clear_cache()` to invalidate.

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
./hactl.py hosts add panel --host 192.168.1.50   # prompts for the ID
./hactl.py update --check
./hactl.py update --wait
./hactl.py plugins list
./hactl.py settings assistant.model.value small.en
```

The host and ID are asked for once and cached in `~/.config/hactl/hosts.json`,
written `0600` because that ID is the only thing between the network and full
control of the panel. It is masked everywhere it is displayed, and prompts use
`getpass` so it stays out of shell history.

Multiple panels are supported with `-t NAME`; `--host` and `--id` override
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
| `plugins ...` | Everything under `/plugins`. |
| `public ENDPOINT [k=v ...]` | Call a registered endpoint. |
| `raw PATH [k=v ...]` | Anything else. |

`--json` prints the raw reply. Exit codes are 0 success, 1 refused, 2 usage,
3 unreachable, so it is usable from a shell script.

`--wait` polls `/plugins`, which is read-only and safe to hit repeatedly. It
requires the panel to actually drop before counting it as back - otherwise a
poll landing between the reply and the restart reports success against the
process on its way out.
