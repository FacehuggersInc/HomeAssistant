# Logging

## Levels

| Level      | For                                                          |
|------------|--------------------------------------------------------------|
| `debug`    | Numbers you would want when something looks wrong on screen. |
| `info`     | Normal lifecycle: loaded, registered, connected, navigated.  |
| `warning`  | Something failed and was handled. The app carries on.        |
| `error`    | Something failed and a feature is now broken.                |
| `critical` | The app cannot continue.                                     |

The distinction that matters is `warning` versus `error`: a warning means the
degradation is contained, an error means a user will notice something missing.
"Could not reach the weather API, using the last reading" is a warning.
"Could not start the STT process" is an error.

### Turning `debug` off

`debug` lines are dropped unless `debug.enabled` is on. Every other level always
goes out.

Without that filter every diagnostic in the tree printed on every launch, which
is what makes a startup log unreadable — the useful lines are in there, buried.
The calls are worth keeping: the widget sizes on disk, which route the backlight
rejected, which transcript was dropped as the panel's own voice. Each of those
located a real bug. Being able to turn them off is what was missing.

The setting is read **once** and cached, since `log()` is called thousands of
times. Before the settings exist it answers **true**: a failure during startup is
when the detail is most wanted, and there is nothing to ask yet.

## Everything routes here

Three sources need routing here, since none of them can reach `client.log()`
directly — and anything that does not is absent from the Logs page, which is
where somebody looks for it:

* **JavaScript on a web page.** Qt's default `javaScriptConsoleMessage` writes
  to stderr with a `js:` prefix. The page object routes it to `client.log()`
  instead, keeping the level the engine reported.
* **The speech process.** It is a separate process, so a print there goes to
  whatever stdout it inherited. It sends `host:log:<level>:<message>` over the
  socket it already uses for every other event, and the parent forwards it.
* **Stray prints** elsewhere in the tree.

## Not `print()`

A print goes to stdout only. It is not timestamped, carries no level, is not in
the log file, and on a panel started from a desktop launcher goes nowhere at all.

Three exceptions:

`log()` itself, which is what writes the log.

`send_log()` in the speech process, when the socket is not up yet — the startup
window is when a failure matters most, and that function **is** the logger, so
anything else there recurses until the stack gives out.

`styling.py` reports a failed stylesheet with a print. Everything
imports that module and it reaches nothing — a client import would be a cycle —
and saying so on stdout beats saying nothing.


One call, from anywhere, on any thread.

```python
self.client.log("info", "[MyPlugin] Loaded 4 feeds.")
```



## Format

```
[2026/7/27 14:32:05][INFO] [MyPlugin] Loaded 4 feeds.
```

Timestamp, four-letter level, message. Everything goes to stdout, and to the
log file when file logging is on.

**Prefix your messages with `[YourPlugin]`.** Everything in this codebase does,
and it is the only thing making a shared log readable — the level tells you how
bad it is, the prefix tells you who to blame.

### Extra arguments

```python
self.client.log("error", f"[MyPlugin] Fetch failed: {e}", include_traceback=True)
self.client.log("debug", "[MyPlugin] Layout pass", pointer=self)
```

`include_traceback` appends the current traceback, to stdout and the file both.
Use it in an `except` block where the exception type alone will not be enough.

`pointer` appends `FRM <object>`, for when several instances of the same class
are logging and you need to tell them apart.


## The hourly census

Once an hour, before the collection cycle releases anything, the client counts
what has piled up and logs it as a **warning**:

```
[Census] on_update 6, on_interaction 3, ... | widgets 412, timers 19,
         threads 11, timeouts 2, tracked objects 91043
[Census] Grown since the last hour: on_update 6->13, widgets 412->640
```

The first line is always written. The second appears only when something is
higher than it was an hour ago, so a steady panel stays quiet and a leaking one
names what is leaking.

Counted **before** the collection, deliberately: the question is what an hour of
running accumulated, not what survived the tidy-up. A count that climbs every
hour and resets on restart is the thing to chase.

Warning rather than info because these lines are wanted by somebody going
looking after the panel felt slow - `info` is where the ordinary lifecycle
noise lives, and this would be lost in it.

## Files

Logs go to `logs/` at the install root.

`logs/latest.log` is the current run. On startup the previous `latest.log` is
renamed to its own start timestamp — `2026-7-27-14-32.log` — so every run keeps
its own file and the newest is always at a fixed path you can `tail`.

The file is opened lazily on the first log call, and flushed after every write.
An app that dies mid-operation still has the line that describes what it was
doing, which is the entire reason for flushing every time rather than buffering.

`logs/` is in `UPDATE_PRESERVE`, so an update does not take the log of the run
that prompted it.

It is also registered as an asset under the key `logs`, which means it is
readable over the API:

```
GET /asset/logs?token=...
GET /asset/logs/latest.log?token=...
```

That is how you read a wall panel's log without a keyboard attached to it.


## What to log

**Log decisions, not progress.** "Chose backend wpctl", "Default page not
registered, showing RootPage", "Skipped 3 preserved files" — each explains
something a reader would otherwise have to guess.

**Log the numbers behind anything geometric.** Layout problems are close to
impossible to reason about from a screenshot and trivial from a line that says
page, window, viewport and device pixel ratio. The widget framework logs a
full set at `debug` on every pass for exactly this reason.

**Never log a secret.** `client.SECRETS.masked(key)` exists for when you need
to show that a key is present. The API's own request logger replaces `id` with
`***` before writing the line, and anything you write should do the same.

**Do not log in a tight loop.** Every call writes and flushes. A `debug` line
per frame will dominate the file and slow the loop it is describing. Log the
first occurrence, and then every hundredth:

```python
self._overflows += 1
if self._overflows in (1, 10, 100) or self._overflows % 500 == 0:
    self.client.log("warning", f"[Audio] Overflow x{self._overflows}")
```


## Threading

`log()` is safe from any thread — it prints and appends, and holds nothing that
a page or widget could be mid-rebuild on. Subsystems that run off the UI thread
log freely, including the STT process, which prints to its own stdout and is
captured separately.


## Reading it back

`client.show_runtime_state()` dumps live threads and their daemon status to
stdout. Useful when something has not shut down and you need to know what is
still holding on.

For the Qt side, the `debug` level is where geometry lives — turn it up when a
layout is wrong before reaching for a theory.
