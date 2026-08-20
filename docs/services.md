# Services

Long-running work, whether it is a thread or a child process.

`client.SERVICES` holds both. A thread and a process want the same five verbs
from a caller — start, stop, is it alive, wait for it, who owns it — and differ
only in how they are asked to stop and in whether anything notices when they
die on their own. One registry answers the verbs and delegates the two
differences.

The panel's own speech recognition is registered here: the child process, and
the socket reader that only means anything while it is alive.

---

## A plugin with a worker: the short version

Read this before the rest. The order below is the order to do it in, and
everything after it explains one of these steps in more detail.

**1. Decide whether it is a thread or a process.** A thread for anything in
this interpreter - a poller, a watcher, a queue drainer. A process for
something that has to be separate: a model that would hold the GIL, a library
that crashes, anything that should be able to die without taking the panel with
it.

**2. Register it in `load()`.** Registering is cheap and does nothing;
`start()` is the commitment. Nothing here touches Qt, so `load()` is early
enough and `built()` is not needed.

```python
class MyPlugin(Plugin):

    KEY    = "myplugin"
    POLLER = "myplugin.poller"

    def load(self, carryover=None):
        self.client.SERVICES.create(self.KEY, self.POLLER, self.poll)
        self.client.SERVICES.start(self.POLLER)
```

**3. Take the stop event, and check it.** It arrives as your first argument. A
stop is a request - nothing can interrupt a thread parked in a native call - so
a target that never looks at it never stops.

```python
    def poll(self, stop_event):
        while not stop_event.is_set():
            self.fetch()
            # wait(), not sleep(): a stop arriving mid-sleep is ignored for
            # the rest of it, and a 60 second poll takes a minute to shut down.
            stop_event.wait(60)
```

**4. Name it after your plugin.** Names are global. Two plugins picking
`poller` share one entry, and the second one to register rebinds the first.

**5. For a process, write a command factory rather than a list.** It is called
on every start, so a restart brings back the settings as they are now rather
than as they were when the panel booted.

```python
    def argv(self):
        return [sys.executable, str(self.worker_path()),
                json.dumps({"interval": self.option("general.interval", 30)})]
```

**6. Give it a companion if something else only makes sense while it runs.** A
reader thread and the process it reads from are one lifecycle wearing two hats.
Register the companion **first** - the process names it, and a companion that is
not registered is a name the registry can do nothing with.

```python
    WORKER = "myplugin.worker"
    READER = "myplugin.worker.reader"

    def start_worker(self):
        services = self.client.SERVICES
        services.create(self.KEY, self.READER, self.read_from_worker)
        services.spawn(
            self.KEY, self.WORKER,
            command    = self.argv,
            on_stop    = self.ask_it_to_stop,
            on_exit    = self.it_went,
            companions = (self.READER,),
            restart    = Restart(backoff=(0.0, 5.0, 30.0), window=120.0),
        )
        services.start(self.WORKER)
```

**7. Say how it is asked to stop, in its own protocol.** `on_stop` answers
whether the request was delivered. Everything after that - terminate, wait,
kill, wait - is the same for every process and belongs to the registry.

```python
    def ask_it_to_stop(self) -> bool:
        return self.send("STOP")          # answers False if nothing took it
```

**8. Say what happens when it dies on its own.** `on_exit(code, restarting)`
fires only for an exit nobody asked for. The registry logs it either way;
whether anybody should be *told* is yours, because only you know whether this
one mattered to a person.

```python
    def it_went(self, code, restarting):
        if not restarting:
            self.client.simple_notify("error", "My Plugin",
                                      "The worker stopped and is not coming back.")
```

**9. Undo nothing in `unload()`.** Anything registered under your key is
stopped and dropped when your plugin goes. Turn that off deliberately, per
service, and only for a process:

```python
        services.create(self.KEY, self.PUMP, self.pump, survives_reload=True)
```

A thread that survives a reload keeps running the code it was loaded with,
which is what `unload()` exists to prevent. A process does not have that
problem.

**10. Adopt it back if you asked it to survive.** Call `create()` or `spawn()`
again with the same key and name in the new `load()`. The running thing is left
alone and the callables are pointed at the new instance. Anything you do not
claim back is stopped for you.

---

## One plugin, whole

The same ten steps as one file, so the shape is visible in one read. A plugin
with a poller in this interpreter and a worker in its own, each with the
smallest thing that makes it real.

```python
import json
import sys
from pathlib import Path

from src.plugin.template import Plugin
from src.registries.service_registry import Restart


class MyPlugin(Plugin):

    KEY    = "myplugin"

    # Named on the class, not written out at each call site. The worker names
    # the reader as its companion, so the same string has to agree in three
    # places.
    POLLER = "myplugin.poller"
    WORKER = "myplugin.worker"
    READER = "myplugin.worker.reader"

    ## -- lifecycle

    def load(self, carryover=None):
        services = self.client.SERVICES

        # A thread, for work that belongs in this interpreter.
        services.create(self.KEY, self.POLLER, self.poll)
        services.start(self.POLLER)

        # A process, and the thread that only means anything while it lives.
        # The reader is registered FIRST: the worker names it, and a companion
        # that is not registered is a name the registry can do nothing with.
        services.create(self.KEY, self.READER, self.read_from_worker)
        services.spawn(
            self.KEY, self.WORKER,
            command    = self.argv,
            on_stop    = self.ask_it_to_stop,
            on_exit    = self.it_went,
            companions = (self.READER,),
            restart    = Restart(backoff=(0.0, 5.0, 30.0), window=120.0),
        )
        services.start(self.WORKER)

    def unload(self, carryover=None):
        # Nothing. Everything above is filed under this plugin's key and is
        # stopped and dropped when it goes.
        pass

    ## -- the thread

    def poll(self, stop_event):
        while not stop_event.is_set():
            self.fetch()
            # wait(), not sleep(): a stop arriving mid-sleep is ignored for
            # the rest of it, and a 60 second poll takes a minute to shut down.
            stop_event.wait(60)

    ## -- the process

    def argv(self) -> list:
        """
        A factory, not a stored list.

        Called on every start, so a restart brings back the settings as they
        are now rather than as they were when the panel booted.
        """
        return [sys.executable, str(Path(__file__).with_name("worker.py")),
                json.dumps({"interval": self.option("general.interval", 30)})]

    def ask_it_to_stop(self) -> bool:
        """
        The polite half, in whatever protocol this worker speaks.

        Answers whether the request was delivered. Everything after it -
        terminate, wait, kill, wait - is the same for every process and belongs
        to the registry.
        """
        return self.send_to_worker("STOP")

    def it_went(self, code, restarting: bool) -> None:
        """
        It exited without being asked to.

        The registry logs either way. Whether anybody should be *told* is this
        plugin's call, because only it knows whether this one mattered.
        """
        if not restarting:
            self.client.simple_notify(
                "error", "My Plugin",
                "The worker stopped and is not coming back.")

    def read_from_worker(self, stop_event):
        while not stop_event.is_set():
            ...
```

### Surviving a reload

Add `survives_reload=True` to the worker's `spawn()` and it is left running
while the plugin reloads, instead of being stopped and started again for
nothing:

```python
        services.spawn(self.KEY, self.WORKER, ..., survives_reload=True)
```

The new instance's `load()` then runs exactly the code above. `spawn()` finds
the worker still running, leaves it alone, and points `command`, `on_stop` and
`on_exit` at the new instance — so adopting it back is not a different call.
Anything not claimed back is stopped for you.

Not on the poller. A thread that survives a reload keeps executing the module
that was replaced.

---

## A thread

```python
client.SERVICES.create("myplugin", "myplugin.poller", self.poll)
client.SERVICES.start("myplugin.poller")
```

The target is handed a `threading.Event` as its first argument and is expected
to check it:

```python
def poll(self, stop_event):
    while not stop_event.is_set():
        self.fetch()
        # wait(), not sleep(): a stop arriving mid-sleep is ignored for the
        # rest of it, and a 60 second poll takes a minute to shut down.
        stop_event.wait(60)
```

A stop is a request. Nothing here can interrupt a thread parked in a native
call, which is why `stop()` and `wait_for_stop()` are separate — a shutdown
asks everything and then collects it.

## A process

```python
client.SERVICES.spawn(
    "myplugin", "myplugin.worker",
    command    = self.argv,
    on_stop    = self.ask_it_to_stop,
    on_exit    = self.it_went,
    companions = ("myplugin.worker.reader",),
    restart    = Restart(backoff=(0.0, 5.0, 30.0), window=120.0),
)
```

| Argument     |                                                                    |
|--------------|--------------------------------------------------------------------|
| `command`    | A **callable** answering with the argv.                            |
| `on_stop`    | Ask it to stop, its own way. Answers whether that was delivered.   |
| `on_exit`    | `(code, restarting)`, when it goes without being asked.            |
| `companions` | Thread services that live and die with it.                         |
| `restart`    | A `Restart`, or omitted for never.                                 |

**`command` is a factory rather than a list**, because anything worth
restarting is worth restarting as it is now. The speech process is handed its
wake words, its sensitivity and its precision on the command line, and a stored
list would bring back the settings as they were when the panel booted.

### Stopping one

`on_stop` is the polite request, in whatever protocol the process speaks —
`STTProcessing` sends `STOP` on its command port. The escalation after it is
the same for every process and lives in the registry: wait, `terminate`, wait,
`kill`, wait, and an `error` naming what is still being held if it survives all
of that.

An `on_stop` that answers False skips the wait. There is nothing to wait for
when the request was never delivered, and five seconds of a shutdown somebody
is watching is a long time to spend finding that out.

### Companions

A reader thread and the process it reads from are one lifecycle wearing two
hats. Declared as companions they are started after it, stopped before it, and
stopped again when it dies on its own — so a child that goes takes its reader
with it rather than leaving one reconnecting to a socket nothing is behind.

## Which thread your callbacks run on

The rule from [Threading](threading.md) applies here and is easy to miss,
because none of these are called from where you registered them:

| Callback  | Runs on                                                              |
|-----------|----------------------------------------------------------------------|
| `command` | Whoever called `start()`, or the **supervisor** during a restart.    |
| `on_stop` | Whoever called `stop()` - during shutdown that is the **UI thread**. |
| `on_exit` | The **supervisor thread**, always.                                   |
| A target  | Its own thread, obviously.                                           |

So an `on_exit` that opens a panel or touches a widget is the crash
`Threading` opens with: `Timers cannot be started from another thread`, then
`SIGTRAP`, with no Python exception anywhere. Hand it over:

```python
    def it_went(self, code, restarting):
        self.client.call_on_ui(self.show_the_bad_news)
```

`client.simple_notify()`, `client.log()`, `client.alert()` and
`client.dialog()` marshal themselves and are safe from any of these - which is
why the assistant's own `process_exited` can notify directly.

**`on_stop` is on the UI thread during shutdown, so keep it short.** `stop()`
can block for the whole escalation - `STOP_TIMEOUT` plus two `KILL_TIMEOUT`s,
about nine seconds - and `Client.cleanup()` runs it while somebody is watching
the window fail to close. Answering False when the request could not be
delivered is what keeps the first five of those out of it.

## What is not supervised

**A thread that stops is not noticed.** The supervisor polls processes.
A target that returns early, or raises, just ends: `is_active()` goes False,
nothing is restarted, and no `on_exit` fires - `Restart` is a process idea.
The traceback does reach `logs/crash.log`, because `threading.excepthook` is
installed - see [When it will not start](when-it-will-not-start.md) - but
nothing tells the plugin that registered it.

If a thread's work matters, have it report for itself rather than relying on
the registry to notice.

**A process that is alive but wedged looks healthy.** Liveness is `poll()`,
which answers whether it exited. A child stuck inside a native call - opening
an ALSA device that never answers is the one this panel has actually hit - is
running as far as this is concerned, forever.

**`wait_for_stop` waits one second by default**, and `stop_all()` uses that.
A thread sitting in `stop_event.wait(60)` has not exited when shutdown moves
past it. That is deliberate for daemon threads, and it means "Stopping" in the
shutdown log means *asked*, not *gone*.

## Restarting

Opt-in per service, and the policy carries its own numbers:

```python
Restart(backoff=(0.0, 5.0, 30.0), window=120.0)
```

`backoff` is a delay per attempt and **running out of them is giving up**, so
the policy states its own limit rather than carrying a separate count. That one
reads as: straight away, then in five seconds, then in thirty, then leave it
down. A delay of zero means now rather than on the next pass.

`window` is how long a service has to run before it counts as having recovered.
A process that held up for longer than that and then died is not in a loop, so
its attempt count starts again.

Its own numbers rather than a pair of settings, because what a speech model
that cannot find its weights should do and what a feed poller that lost the
network should do are different answers, and one setting shared between them is
a thing to be wrong about twice.

**A deliberate stop is not a death.** `stop()` clears the restart intent, so
only an exit nobody asked for counts.

### Reporting

The registry logs either way — `warning` on a restart with the attempt number,
`error` when the backoff runs out and the service is left down.

It does not notify. Whether a dead service is worth putting on screen depends
on what it was for, which the registry does not know. That is `on_exit`, and
the assistant uses it: a panel whose speech process has gone is deaf, the pill
reads whatever it read last, and from the room that is indistinguishable from a
broken microphone.

## Owning one

The owner is your plugin key from `plugin.toml`, the same as every other
registry.

**Names are global, and one another owner holds is refused.** Two plugins
picking `poller` is a collision rather than a handover: rebinding would give
the second one a thread it did not start, and stop it on an unload the first
knows nothing about. The refusal is a warning naming both owners.

There is no `claim()` here, unlike a provider. A service is a plugin's own
internal machinery; a thing two plugins want between them is a provider. A
plugin re-registering **its own** name is the same owner and goes straight
through, which is how a reload adopts one back.

Prefix with your plugin key - `myplugin.poller`, not `poller`. The refusal is
at registration, so `start()` and `stop()` still take a bare name and will act
on whatever holds it.

**Cleanup is opt-in, and only across a reload.** A service is stopped and
dropped when its owner unloads unless it asked to survive:

```python
client.SERVICES.create("myplugin", "myplugin.pump", self.pump,
                       survives_reload=True)
```

The loader passes a carryover for a reload and not for anything else, and that
is what decides. On a genuine unload or at shutdown everything stops, with no
opt-out — a thread still running against a module nobody can reach is not a
thing to leave behind on a panel that goes weeks without a restart.

**A surviving thread keeps running the code it was loaded with; a
surviving process does not have that problem.** A
thread's target is a bound method on the instance that has gone, so it keeps
executing the module that was replaced. A subprocess is code on disk in its own
interpreter and the reload never touched it. Threads should rarely ask for
this; processes often should.

### Adopting one back

Registering over a live service **rebinds** it: the running thing is left
alone, and the callables describing it — the command factory, the stop request,
the exit handler — are pointed at the new instance. So the new `load()` calls
`create()` or `spawn()` with the same owner and name, and gets back what never
stopped.

Anything held across the gap that the new instance did not register again is
**reaped** — stopped and dropped, with a warning naming it. Without that,
opting out of cleanup would be leaking by default: a plugin whose `load()`
raised, or that changed the name it registers under, would leave a process
holding whatever it holds until the panel is restarted.

Since a service can outlive its owner, each plugin's own page in Settings lists
what it holds, whether each is running, and marks anything orphaned. A thing
that survives its plugin and is only visible if you go looking is a thing
nobody audits.

## Listening and speaking

`SERVICES.STT` and `SERVICES.TTS` are the two the panel itself runs. They are
**facades**: each holds the state and delegates the work to whatever is
currently doing it.

| On `SERVICES.STT`   |                                                     |
|---------------------|-----------------------------------------------------|
| `status`            | `DORMANT`, `LIVE`, `LISTENING`, `THINKING`, `ACTING`|
| `level`             | Input level while capturing, 0 to 1.                |
| `thinking(why)`     | Hold the pill while something slow runs.            |
| `wake_word`         | The configured word.                                |
| `status_snapshot()` | Everything the recogniser knows.                    |
| `config()`          | The listening settings a restart depends on.        |
| `cancel(reason)`    | Back to the wake word.                              |
| `source`            | Whatever is listening, or None.                     |

| On `SERVICES.TTS`  |                                             |
|--------------------|---------------------------------------------|
| `say(text)`        | Speak. Answers whether anybody heard it.    |
| `recent_spoken()`  | The last few replies, newest first.         |
| `owner()`          | The token for the most recent thing said.   |
| `is_speaking()`    | Including while a sentence is being made.   |
| `start()`          | Pick a backend, or report why there is none.|
| `config()`         | The voice settings, its own.                |
| `backend`          | Whatever is speaking, or None.              |

**The state is on the facade because it has to outlive the implementation.**
Whatever is transcribing can be stopped, restarted or replaced; what the
assistant is *doing* is the panel's own idea and should survive all three.

On the speaking side that is not a nicety. `recent_spoken()` is what
[`echoed()`](assistant.md#hearing-itself) compares a transcript against for
twenty seconds after a reply, so a backend swap that took the ring with it
would leave the panel deaf to its own voice for exactly the window it is most
likely to hear it.

`attach()` and `detach()` are how the implementation is set, which is why
`client.STT` is read-only.

**They have separate `config()` tuples, and that separation is the point.**
Each holds the settings its own implementation was built against, and a save
compares them one at a time - so picking a different voice rebuilds the voice
and leaves the microphone, the speech process and the wake word alone. One
tuple for both meant the smallest setting on the page cost several seconds of
a deaf panel.

### Who supplies them

A **provider** says who builds a capability. It is not a service - it has no
lifecycle of its own, it is a factory for the thing that does. `assistant.stt`
is a provider; the process it ends up spawning is a service.

```python
client.SERVICES.provide("client", "assistant.stt", factory, "Parakeet")
```

The panel registers its own for `assistant.stt` and `assistant.tts` when it is
constructed, which is what puts a stock install at the bottom of the stack.

Registering a name another owner holds is **refused** and logged, the way the
API registry refuses a key. Two owners quietly fighting over one name is much
harder to find than a warning at startup.

### Taking one over

```python
class MyPlugin(Plugin):

    def load(self, carryover=None):
        self.client.SERVICES.claim(
            self.plugin_key(), "assistant.stt", self.build_recogniser,
            "Cloud speech recognition")
```

`claim()` is the deliberate way past the refusal. What was there is
**remembered rather than dropped**, and comes back when this plugin
unregisters - a plugin that claimed the recogniser and then unloaded would
otherwise leave the panel with none until it was restarted, and restoring it by
hand would mean knowing how the client builds its own.

Several claims stack. Releasing one uncovers the one below it, in the order
they arrived.

**A claim goes when its owner does**, reload or not. A factory is bound to the
instance that made it, so one held across a reload would build from the module
that was replaced. The new instance claims again in its own `load()`.

**Nothing has to be restarted by hand.** The panel watches both names and
restarts the assistant when one changes hands. Without that a claim would leave
the panel running the implementation it already had, which looks from outside
like the claim not having worked and leaves nothing in the log.

### What a recogniser has to answer

Everything on `SERVICES.STT` that is not state. The facade is written out
rather than forwarding with `__getattr__`, because **this list is the
contract** - a facade that forwarded whatever it was asked for would let a
replacement look complete right up until something reached for the one method
it left out.

```python
start()  stop()  status()  submit(phrase)  new_session()
open_session()  close_session()  is_session()  processing
start_monitor()  stop_monitor()  add_listener(cb)  remove_listener(cb)
note_speech_ended()  note_interrupted()  check_wake_timeout()
send_command(command, retries=10)  hold_capture(held)  cancel(reason)
```

The factory is called as `factory(client, **kwargs)`, with `input_device`,
`model`, `wake_words` and `session_silence_ms`.

**The guards are not yours to reimplement.** Self-hearing, the echo comparison,
hallucination trimming, wake matching, sessions and the interrupt settle all
live above this and apply to whatever is underneath. A recogniser that skipped
them would answer its own voice. See
[Voice assistant](assistant.md#hearing-itself).

The lighter option is to leave `STTProcessing` in place and supply a different
**process** that speaks its socket protocol - `whisper-process.py` is in the
tree doing exactly that. Five messages rather than nineteen methods, and every
guard still applies.

### What a voice has to answer

`assistant.tts`'s provider answers with a **list** of `(label, class)`, tried
in order, so the per-backend failure reporting survives - which is what tells a
missing package apart from a missing key.

```python
available   error   claim()   is_speaking()   play(text, thread=True)
stop(owner=None)
```

### The names on the Client

`client.ASSIST_STATUS`, `client.ASSIST_VOICE_ACTIVITY_LEVEL`,
`client.wake_word`, `client.say()`, `client.thinking()`,
`client.note_spoken()`, `client.recent_spoken()`, `client.speech_owner()`,
`client.cancel_assistant()` and `client.assistant_config()` all delegate here.

`say()` and `answer()` stay on the Client because they read correctly at that
level - "the panel says something" is a client-level verb in a way that a
status field never was.

**`SERVICES.STT` and `SERVICES.TTS` are the only way to whatever is listening
and speaking.** There is no `client.STT`. One name for a thing means nothing
can hold a stale reference to an implementation that has since been replaced,
and `SERVICES.STT.running` is how to ask whether the assistant is up.

`client.restart_assistant()` stops it and starts it again, which is what a
provider changing hands does by itself.


## Shutting down

`stop_all()` works backwards through the registration order. A service
registered late is usually one that depends on the ones before it, and stopping
the timeout scheduler while the assistant is still using it is the wrong end.
Nothing has to declare a priority for that to be right.

## The methods

| Method                                    | Does                                            |
|-------------------------------------------|-------------------------------------------------|
| `provide(owner, name, factory, desc)`     | Say who supplies a capability.                  |
| `claim(owner, name, factory, desc)`       | Take one, remembering who had it.               |
| `release(owner, name="")`                 | Give it back, uncovering the one below.         |
| `provider(name)` / `build(name, ...)`     | Who supplies it, and make one.                  |
| `watch_provider(name, callback)`          | Be told when it changes hands.                  |
| `create(owner, name, target, *args)`      | Register a thread. None if the name is taken.   |
| `spawn(owner, name, command, ...)`        | Register a process. None if the name is taken.  |
| `start(name)`                             | Run it. A no-op while it is already alive.      |
| `stop(name)`                              | Ask it to stop, escalating for a process.       |
| `kill(name)`                              | Force a process down without asking first.      |
| `wait_for_stop(name, timeout=1)`          | Collect it.                                     |
| `is_active(name)`                         | Whether it is alive.                            |
| `process(name)` / `pid(name)`             | The `Popen`, and its pid.                       |
| `unregister(owner, name="", reloading=)`  | Give back what an owner registered.             |
| `reap(owner)`                             | Stop what a reload held and nothing claimed.    |
| `entries_for(owner)` / `names_for(owner)` | What an owner holds.                            |
| `stop_all()`                              | Everything, newest registration first.          |
| `snapshot()`                              | Every service, as dicts, for a display.         |

## The assistant, as an example

The same steps, with the code that is actually in the tree. It is worth
reading whole because it uses every part of this page at once: a provider, a
process, a companion, a restart policy, and a facade holding the state.

### 1. The provider is registered while the Client is being constructed

`Client.__init__` builds the registry, then about ninety lines later calls
`register_speech_providers()`:

```python
self.SERVICES.provide("client", "assistant.stt", parakeet,
                      "Parakeet, in a child process")
self.SERVICES.provide("client", "assistant.tts", pocket,
                      "Pocket TTS, locally")
self.SERVICES.watch_provider("assistant.stt", self._speech_provider_changed)
self.SERVICES.watch_provider("assistant.tts", self._speech_provider_changed)
```

Nothing is running yet. At this point the registry holds **two providers and
no services** — the factories, and nothing built from them. The watcher guards
on `self.BUILT`, so these registrations do not trigger the restart they are
there to cause.

### 2. It starts well after the window does

`build()` finishes and `QTimer.singleShot(1600, self.start_assistant)` fires.
Deferred with the plugin dependency prompt so a slow subsystem does not hold up
first paint — see [Application lifecycle](lifecycle.md).

`start_assistant()` snapshots the settings it depends on
(`SERVICES.STT.remember()`), checks the speech stack, finds a microphone that
opens, and calls `_launch_assistant(device, model)`.

### 3. The implementation comes from the provider, not from a name

```python
self.SERVICES.TTS.start()

source = self.SERVICES.STT.build(
    input_device = device,
    model        = model,
    wake_words   = [self.wake_word],
    session_silence_ms = int(self.setting("assistant.wake.session_silence.value", 800)),
)
if source is None:
    ...                          # nothing provides it - reported, not raised
self.SERVICES.STT.start()
```

`build()` calls whoever holds `assistant.stt` and attaches what comes back.
That is the line a plugin's claim changes, and the only one.

### 4. The services are registered by the thing that owns them

`STTProcessing.start()`, reached by the `SERVICES.STT.start()` above:

```python
services.create("client", self.RECEIVER, self.__listen_for_stt_data)
services.spawn(
    "client", self.SERVICE,
    command    = self.argv,
    on_stop    = self.ask_to_stop,
    on_exit    = self.process_exited,
    companions = (self.RECEIVER,),
    restart    = self.RESTART_POLICY,
)
self.listening = True
services.start(self.SERVICE)
```

`SERVICE` and `RECEIVER` are class constants — `assistant.stt` and
`assistant.stt.receiver` — because the same two strings have to agree in the
`create`, the `spawn` and the `companions`.

The reader is registered first, for the reason above. `listening` is set
**before** the process starts: it is what the reader's loop runs on, the reader
connects in a retry loop so it is allowed to be early, and setting it after
races a child that answers quickly.

### 5. The command line is built fresh

`argv()` reads the wake words, the sensitivity, the precision and the phrase
cap out of Settings at the moment it is asked, and returns

```
[sys.executable, parakeet-process.py, <config as JSON>]
```

A restart minutes later is a restart with the settings as they are now. That is
the whole reason `command` is a callable.

### 6. Stopping is a `STOP` on port 65432, and then not this side's problem

```python
def ask_to_stop(self) -> bool:
    self.listening = False
    sent = bool(self.send_command("STOP", retries=2))
    ...
    return sent
```

Answering False skips the registry's five-second wait, because there is nothing
to wait for when the request never arrived. `terminate`, `kill` and the timeouts
around them are the registry's, identically for every process it holds.

### 7. Dying on its own is reported by both halves

`RESTART_POLICY` is `Restart(backoff=(0.0, 5.0, 30.0), window=120.0)` — now,
then in five seconds, then in thirty, then leave it down. A model that cannot
find its weights fails identically every time; a child killed by something
passing usually comes back on the first attempt.

The registry logs each attempt and errors when the backoff runs out.
`process_exited(code, restarting)` decides what the panel says:

```python
if restarting:
    self.client.ASSIST_STATUS = "LIVE"
    ...
    return

self.listening = False
self.client.ASSIST_STATUS = "DORMANT"
self.client.simple_notify("error", "Assistant", "Speech recognition stopped. ...")
```

**`listening` stays true on the restarting path.** The reader comes back as a
companion, and a fresh reader thread would otherwise read the flag once and
return. Nothing else is misled: every other guard checks `process`, which is
None through the gap.

Giving up is worth a notification because at that point the panel is deaf, the
pill reads whatever it read last, and from the room that is indistinguishable
from a broken microphone.

### 8. What is running once it is up

| Holds     |                                                              |
|-----------|--------------------------------------------------------------|
| Providers | `assistant.stt`, `assistant.tts`, both owned by `client`     |
| Services  | `assistant.stt` (process), `assistant.stt.receiver` (thread) |
| Threads   | one supervisor, not registered as a service                  |

The supervisor exists only because a process was spawned; a panel with the
assistant off never has one. Each pass is a `poll()` per process.

**The registry is not in the speech path.** Audio, the socket protocol,
transcripts and routing all go between the child, the reader and
`STTProcessing`. The registry knows whether the process is alive and nothing
else.

### 9. Restarting reuses the registration

`stop_assistant()` stops the service and detaches; the **registrations stay**.
`start_assistant()` then builds a new `STTProcessing` from the provider, whose
`start()` calls `create`/`spawn` under the same two names — which rebinds
`command`, `on_stop` and `on_exit` to the new instance and leaves everything
else alone.

So the adoption path built for plugin reloads is also what makes an ordinary
settings-triggered restart work. Three things reach it: a save where `config()`
differs from `remembered()`, a provider changing hands, or
`client.restart_assistant()`.

### 10. Shutting down

`cleanup()` runs `SERVICES.STT.stop()` — the path that sends `STOP` and
notifies — and then `SERVICES.stop_all()`, which stops the supervisor first so
nothing is restarted on the way down, then works backwards through the
registration order.

See [Voice assistant](assistant.md) for what the process itself does once it is
running.

## When it does not do what you expected

**It never started.** `start()` answers False for a name nothing registered,
and logs it. For a process it also answers False when the command factory
raised or the argv came back empty, each with its own line. Check the log for
`[Services]` before assuming the child is at fault.

**It was refused at registration.** `create()` and `spawn()` answer `None`
when another owner holds the name. Ignoring that return leaves a plugin that
looks registered and is not - and a later `start()` on the same name will run
the *other* plugin's service.

**It stops the moment it starts.** A target that reads a flag once and returns
looks identical to one that never ran. The assistant hit this: `listening` is
set before the process starts precisely because the reader would otherwise
read it once, find False, and end.

**It restarted a few times and gave up.** The backoff ran out. That is an
`error` naming the service and the exit code, and `on_exit` was called a last
time with `restarting` False. Nothing will start it again by itself.

**It restarts forever.** It cannot - `backoff` is the limit. If it looks like
it, something is calling `start()` in a loop, or the process is exiting
faster than `window` and the count is resetting.

**Its companion is not running.** Companions start with the process, so a
companion registered *after* the `spawn()` that names it is a name the
registry could do nothing with at the time. Register it first.

**It survived an unload it should not have.** Only `survives_reload=True`
services do, and only across a reload. If one is still there, `reap()` has
not run - it fires after the new instance's `load()`, so a `load()` that
raised leaves the reap to the next one.

**Nothing lists what is running.** `client.show_runtime_state()` prints every
service, its owner, whether it is alive, its pid, and every provider. A
plugin's own page in Settings shows what it registered and what it provides.

## `client.THREADS`

The older `ThreadManager` is still there and still works. It has no owner, so
nothing is cleaned up for you — stop yours in `unload()`.
