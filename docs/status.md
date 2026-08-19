# Status

A long-running job has nowhere to say so. A log line is read afterwards by
somebody already suspicious, and a dedicated panel is a screen nobody has open
at the moment it matters. What is missing is the middle: something running
right now, visible without being asked for, and gone when it stops.

`client.STATUS` is that middle. One icon per job, in a row beside the Quick
Settings title.

---

## Starting one

```python
status = client.STATUS.start("mdi.microphone", "stt.listening", "myplugin")
```

| Argument |                                                               |
|----------|---------------------------------------------------------------|
| `icon`   | Any name `webicons` or qtawesome knows. `mdi.` prefixes work. |
| `key`    | Names the job. Starting the same key twice returns the first. |
| `owner`  | A plugin key, or `"client"` for the panel itself.             |
| `colour` | Optional. Empty is the ordinary muted one.                    |

**`start()` is the only way in.** A caller holds the handle it gets back and
never reaches into the registry by key, which is what stops two plugins
fighting over one entry and what makes `stop()` mean something.

Starting a key that is already there returns the existing entry rather than
adding a second. A job that was already running and started again is one job,
and two icons for it would say the work had doubled.

## The handle

```python
status.set_icon("mdi.text-recognition")     # the same job, doing something else
status.set_colour("#ffb454")
status.set(icon="mdi.alert", colour="#ff7a7a")   # both, one redraw

status.hide()      # still registered, not drawn
status.show()
status.stop()      # gone from the row and from the registry
```

`hide()` is for a job that comes and goes on its own - the microphone between
phrases - where starting and stopping an entry every few seconds costs more
than it says. `stop()` is for a job that is over.

**Every method is safe after `stop()`.** The thing that starts a job is rarely
the thing that notices it ended: a process that dies takes its own cleanup
with it, and a handle that raises afterwards turns one failure into two.

`status.running_for` is seconds since it started, for anything that wants to
say how long.

## Unloading

```python
def unload(self, carryover=None):
    self.client.STATUS.stop_all(self.KEY)
```

A plugin that goes away while one of its jobs is showing leaves an icon
nothing can ever remove.

## What the row is for

Icons and nothing else - no label, no count, no progress. The question it
answers is *is something happening*, and anything wordier is a thing to read
rather than a thing to notice. Whatever is running says so; the panel only
draws what is there.

The row is rebuilt when something starts, stops, hides or changes, not on a
timer.

Glyphs are drawn larger than the title beside them - `STATUS_ICON` in
`quick_settings.py`. An icon the size of the text reads as punctuation, and
this is meant to be caught at a glance rather than looked for.

## Speech

The panel puts its own two on it. Speech recognition and speech itself are the
things it does that take time and have nothing else to show for it.

| State        |                                               |
|--------------|-----------------------------------------------|
| `processing` | Transcribing what was said.                   |
| `awake`      | Woken, waiting for the rest of the sentence.  |
| `monitoring` | Watching every word, with no wake word.       |
| `held`       | Not listening, because the panel is speaking. |
| `error`      | Speech recognition failed.                    |
| `stopped`    | Speech recognition is not running.            |

**`idle` and `listening` show nothing.** They are what a working panel looks
like, and a row that is never empty is a row nobody reads.

`SpeechStatus` polls rather than being told. Neither side emits anything when
it changes state, and adding signals to both would mean touching the audio
path in two processes to fix a display - which is the one thing that can
afford to be a fraction of a second late.
