# When it will not start

A startup that freezes leaves a log that stops and nothing else. That tells you
roughly where it got to; it does not tell you whether that stage is the cause or
merely the last thing that got a chance to run.

## When it starts and then dies

Look in **`logs/crash.log`** first. It is appended to, so the interesting crash
is often not the last one in the file. It catches three different endings,
which look identical from outside:

* an unhandled exception on the main thread
* one on a worker thread, which Python routes separately and which otherwise
  goes to a stderr nobody is reading
* a **fault** - a segfault, or an abort from Qt touching a C++ object whose
  Python half has already gone. That raises no Python exception at all, so no
  `try/except` anywhere in the app can see it. `faulthandler` writes the stack
  instead.

The file matters because the launcher restarts the panel: the process comes
back, whatever was on stderr goes with the old one, and the only thing left to
work from is which page was on screen. The handlers are installed before
anything else in `app.py` runs, so a crash during startup is caught too.

## Narrow it down in one run

```
HA_SAFE_MODE=1 python app.py          # Linux, macOS
```

```
set HA_SAFE_MODE=1 && python app.py   :: Windows, cmd
$env:HA_SAFE_MODE=1; python app.py    # Windows, PowerShell
```

The `VAR=value command` form is a shell feature, not a Python one, and neither
cmd nor PowerShell has it. Set the variable first on Windows, and remember it
stays set for the rest of that terminal session.

Bluetooth, the voice assistant and the embedded browser are all skipped. If it
starts, one of the three is responsible — turn them back on one at a time:

|                     |                                            |
|---------------------|--------------------------------------------|
| `HA_NO_BLUETOOTH=1` | No D-Bus, no adapter lookup                |
| `HA_NO_ASSISTANT=1` | No microphone, no speech model             |
| `HA_NO_WEBENGINE=1` | No hidden player page, no embedded browser |

Whatever is switched off is logged as a **warning** at startup. A panel started
with something disabled and then forgotten about is a panel with a mysteriously
missing feature.

## The stages that hang rather than fail

Most startup work either succeeds or raises. Four do neither:

**Opening the microphone.** This is the one that has actually happened.

`sd.InputStream(...)` calls PortAudio, which on Linux calls ALSA. On a machine
whose `default` points at something wedged or exclusively held, that open
**blocks with no error and no end** — there is nothing to catch and nothing to
time out against, so the wait has to be imposed from outside.

Two things follow from that:

* Every open is **bounded** (`PROBE_TIMEOUT`, six seconds). It runs on its own
  thread and is abandoned if it does not return. That thread may stay stuck
  inside PortAudio for the life of the process; it is a daemon, so the cost is a
  thread rather than the application.
* `working_input()` tries candidates **in turn** until one answers: the
  configured device, then the system default, then `pipewire`, `pulse`,
  `sysdefault`, then real hardware. A listed device is not an openable one, and
  the one the system calls `default` is no more trustworthy than the rest.

A panel listening through `pipewire` because `default` would not answer is worth
much more than one that does not come up. The substitution is never silent — the
log and a notification say what was skipped and why.

If nothing opens at all, that is a message and the panel starts anyway.

**Loading the speech model.** The first load on a cold cache reaches the network.

A Parakeet's weights are fetched by the panel rather than the speech process,
and on a worker thread — `download_speech_model` starts it and `call_on_ui`
finishes it, so neither the UI thread nor a child process without a socket is
holding a download. `python3 hactl.py speech-model` answers locally while it
is happening, and names the files still missing. See
[the assistant](assistant.md#asked-once).

**Loading the language model.** The first start after a rebuilt virtual
environment reaches the network.

`pip install -r requirements.txt` brings spaCy and no model — models are
separate downloads and are not on PyPI. `nlp.preload()` runs before the
plugins, so a missing `en_core_web_sm` is fetched at a point with a log line
either side rather than from inside whichever plugin declared the first
skill. Bounded by `DOWNLOAD_TIMEOUT`.

If it cannot be had, the panel stops with `ModelMissing` and the command to
run by hand. That is one of the few things worth refusing to start over: the
model is what turns a phrase into something a skill can match, so an assistant
without one understands nothing said to it.

**The embedded browser.** QtWebEngine is the largest thing in the process and
starts a second one. A line like

```
GBM is not supported with the current configuration.
Fallback to Vulkan rendering in Chromium.
```

means it could not use the normal GPU path. That fallback is where hangs and
`SIGTRAP` aborts tend to come from, and it is worth ruling out early:

```
QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python app.py   # Linux, macOS
```

```
set QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu && python app.py   :: Windows, cmd
```

If that starts cleanly, the problem is graphics, not the panel.

## When a missing package is not a missing package

`No module named 'pkg_resources'` is the one to know, and the obvious move is
the wrong one. It is not in requirements.txt, `pip install setuptools` answers
that it is already installed, and searching the name leads to advice written
before setuptools v82 removed the module. `webrtcvad` imports it on its first
line, to read its own version number and for nothing else, so a current
environment cannot import webrtcvad at all:

```
[WARN][Parakeet]: Not starting - the audio stack is unavailable
                  (ModuleNotFoundError: No module named 'pkg_resources').
```

`src/system/pkg_resources_shim.py` stands in for the one function webrtcvad
calls, backed by `importlib.metadata`. **Only around that import, and only
when the real module is absent.** A fake `pkg_resources` left in
`sys.modules` is worse than none: a package that asks for a part the shim does
not have gets an `AttributeError` where it would have got a clean
`ImportError` and taken its own fallback. Libraries read their version at
import time, so there is nothing left for it to do once the import returns.

Pinning `setuptools<82` is the other answer and is worse. It holds the whole
environment on a superseded build tool for one line in one dependency, and
`install()` is a single all-or-nothing pip call - a pin that conflicts with
anything else fails every package in requirements.txt at once.

`explain_import()` in `src/system/requirements.py` appends the thing to do
where the missing module is not the package to install, and `IMPORT_HINTS` is
where another one goes. Both speech processes report through it, and both fall
back to the bare message if the helper itself will not load - a hint is worth
less than a process that starts.

The speech process is restarted a bounded number of times, from
`STTProcessing.RESTART_POLICY`. A cause that cannot change exhausts them and
the panel says so; everything else carries on.

## A note on platforms

The safe-mode switches work anywhere. The specific failures above are mostly
Linux ones: ALSA and `pipewire` are what PortAudio talks to there, `ddcutil`
and `brightnessctl` are Linux-only, and the GBM/Vulkan line comes from
Chromium on a Linux GPU stack. On Windows and macOS the same stages exist and
can still hang; the names in the logs differ.

## Why environment variables

A setting lives in a file the application has to start in order to edit. On a
wall panel every attempt is a walk across a room, so the switch has to work from
the command line and has to be worth the trip.
