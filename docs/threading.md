# Threading

The single most important rule in this codebase, and the easiest one to break:

> **Touch Qt objects only from the UI thread.**

A widget created, moved, shown or restyled from a worker thread does not raise.
It corrupts state and the app dies later somewhere unrelated, which makes the
crash almost impossible to trace back to the call that caused it.

Everything below exists so you never have to break that rule.

---

## `client.call_on_ui(fn)`

The way back to the UI thread. Takes a callable with no arguments and runs it
on the Qt event loop.

```python
def work(stop_event):
    data = requests.get(url).json()          # slow, off the UI thread
    self.client.call_on_ui(lambda: self.panel.set_text(data["title"]))
```

Use a `lambda` or `functools.partial` to bind arguments. The call returns
immediately - it queues, it does not wait.

Anything that ends up touching a widget belongs inside it: `setText`,
`setGeometry`, `show`, `hide`, `set_style`, opening a dialog, adding a widget
to a page.

`client.dialog()`, `client.alert()` and `client.confirm()` already marshal
themselves, so those are safe to call from anywhere.

### Widgets can be deleted while your callback is queued

A queued callable runs later, and by then the panel it refers to may be gone.
Touching a deleted Qt object raises `RuntimeError`, which on a worker thread
takes the thread down silently.

```python
def apply():
    try:
        self.label.setText(text)
    except RuntimeError:
        pass          # the panel closed between the fetch and this running
self.client.call_on_ui(apply)
```

---

## `client.THREADS` - named background threads

A `ThreadManager`, keyed by name, so a thread can be created once and started,
stopped and restarted without leaking a new one each time.

```python
self.client.THREADS.create("myplugin_poller", self.poll)
self.client.THREADS.start("myplugin_poller")
```

Your target receives a `threading.Event` as its **first argument**, and is
expected to check it:

```python
def poll(self, stop_event):
    while not stop_event.is_set():
        self.fetch()
        # wait(), not sleep(): a stop arriving mid-sleep is ignored for the
        # rest of it, and a 60 second poll takes a minute to shut down.
        stop_event.wait(60)
```

| Method | Does |
|---|---|
| `create(name, target, *args, **kwargs)` | Register. No-op if one is already running under that name. |
| `start(name)` | Clear the stop flag and run. Builds a fresh `Thread` each time. |
| `stop(name)` | Set the stop flag. Does not block. |
| `is_active(name)` | Whether the thread is alive. |
| `wait_for_stop(name, timeout=1)` | Join, with a timeout. |
| `get(name)` | The entry dict, or `None`. |

Threads are daemons, so they do not hold the process open - but a daemon
parked in a native call is not unwound cleanly at exit either. Stop yours in
`unload()`:

```python
def unload(self, carryover=None):
    self.client.THREADS.stop("myplugin_poller")
    self.client.THREADS.wait_for_stop("myplugin_poller", timeout=2)
```

Name threads with your plugin key as a prefix. The registry is global, and two
plugins picking `"poller"` will silently share one entry.

---

## `client.TIMEOUTS` - deferred and repeating callbacks

A `TimeoutScheduler`. One thread ticks at 100ms and fires due callbacks
**on the UI thread**, so a timeout callback can touch widgets directly.

```python
self.timeout_id = self.client.TIMEOUTS.add(
    30, self.hide_panel, "myplugin_autohide"
)
self.client.TIMEOUTS.start(self.timeout_id)      # begin counting
```

| Method | Does |
|---|---|
| `add(seconds, callback, id, autostart=False)` | Register, returns the id. |
| `start(id)` | Start or restart the countdown. |
| `cancel(id)` | Stop it. The registration stays, so `start()` works again. |
| `remaining(id)` | Seconds left, or `0.0`. |
| `prune()` | Drop registrations with no pending deadline. |

Calling `start()` again restarts from zero, which is how every auto-close in
the app works: each interaction calls `start()`, and the callback only fires
once nothing has for the full period.

Ids are global. Prefix with your plugin key, or use
`f"myplugin_thing:{self.client.uuid()}"` when you need one per instance.

Cancel your timeouts in `unload()` - a fired callback pointing into an
unloaded module is an exception on the UI thread.

---

## `Thread` directly

Fine for one-shot work that does not need stopping:

```python
from threading import Thread

Thread(target=self.fetch_once, name="__myplugin_fetch", daemon=True).start()
```

Always `daemon=True`, always a name - `client.show_runtime_state()` prints
every live thread, and unnamed ones are impossible to attribute.

Use `THREADS` instead whenever the work loops or needs to stop on unload.

---

## Reading settings from a thread

`client.SETTINGS` is not thread-safe to write. Reads are fine; writes should
go through the UI thread or be guarded with `client.SETTINGS_LOCK`.

Reading a setting on every iteration of a tight loop is also wasteful - read
it once before the loop, and subscribe to `on_settings_saved` if you need to
notice changes.
