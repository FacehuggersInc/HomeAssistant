"""
Long-running work, whether it is a thread or a process.

A thread and a child process want the same five verbs from a caller - start,
stop, is it alive, wait for it, who owns it - and differ only in how they are
asked to stop and in whether anything notices when they die on their own. One
registry answers the verbs and delegates the two differences.
"""

import os
import subprocess
import threading
import time


class Restart:
    """
    What to do when a process dies without being asked to.

    `backoff` is a delay per attempt and running out of them is giving up, so
    the policy states its own limit rather than carrying a separate count.
    `(0, 5, 30)` reads as: straight away, then in five seconds, then in
    thirty, then leave it down.

    A value object rather than a dict, and rather than a pair of settings.
    What a speech model that cannot find its weights should do and what a feed
    poller that lost the network should do are different answers, and one
    setting shared between them is a thing to be wrong about twice.
    """

    def __init__(self, backoff=(0.0, 5.0, 30.0), window: float = 120.0):
        delays = tuple(max(0.0, float(delay)) for delay in (backoff or ()))
        self.backoff = delays or (0.0,)
        # A service that ran for longer than this before dying is not in a
        # loop, so its attempt count starts again. The same idea the launcher
        # applies to the app, with its own numbers rather than that one's.
        self.window = max(0.0, float(window))

    @property
    def attempts(self) -> int:
        return len(self.backoff)

    def delay_for(self, attempt: int) -> float | None:
        """The wait before attempt `attempt`, or None once they run out."""
        if attempt < 0 or attempt >= len(self.backoff):
            return None
        return self.backoff[attempt]


class Provider:
    """
    Who supplies a named capability.

    Not a `Service`: a provider has no lifecycle of its own, it is a factory
    for the thing that does. `assistant.stt` is a provider; the process it
    ends up spawning is a service.

    Stacked rather than replaced. A plugin that claims a name and later
    unloads has to leave the panel with a working one, and the alternative is
    every claimant knowing how to rebuild whatever it displaced.
    """

    def __init__(self, owner: str, name: str, factory, description: str = ""):
        self.owner = owner
        self.name = name
        self.factory = factory
        self.description = description or ""

    def build(self, *args, **kwargs):
        return self.factory(*args, **kwargs)

    def describe(self) -> dict:
        return {"name": self.name, "owner": self.owner,
                "description": self.description}


class Service:
    """
    One registration. A thread or a process, never both.

    `order` is the registration sequence, and shutdown runs backwards through
    it: the timeout scheduler and the backend come up first and go down last,
    the assistant comes up late and goes down first. Nothing has to declare a
    priority for that to be right.
    """

    THREAD = "thread"
    PROCESS = "process"

    def __init__(self, owner: str, name: str, kind: str, order: int,
                 survives_reload: bool = False):
        self.owner = owner
        self.name = name
        self.kind = kind
        self.order = order
        self.survives_reload = bool(survives_reload)

        # Set while an owner has unloaded and nothing has adopted this back.
        # Only ever true for `survives_reload`, and only until reap() runs.
        self.orphaned = False

        # Set by stop(), read by the supervisor. A service somebody asked to
        # stop is not a service that died, so it is never restarted - which is
        # the difference between a supervisor and a loop.
        self.stopping = False

        # -- thread
        self.target = None
        self.args = ()
        self.kwargs = {}
        self.stop_event = threading.Event()
        self.thread = None

        # -- process
        self.command = None          # a callable answering with the argv
        self.on_stop = None          # ask it to stop, its own way
        self.on_exit = None          # told when it went, and whether we retry
        self.companions = ()         # threads that live and die with it
        self.restart = None          # a Restart, or None for never
        self.popen = None
        self.started_at = 0.0
        self.attempt = 0             # restarts so far inside the window
        self.retry_at = 0.0          # when the next one is due

    def is_active(self) -> bool:
        if self.kind == self.THREAD:
            return bool(self.thread and self.thread.is_alive())
        return bool(self.popen and self.popen.poll() is None)

    def describe(self) -> dict:
        return {
            "name": self.name,
            "owner": self.owner,
            "kind": self.kind,
            "running": self.is_active(),
            "orphaned": self.orphaned,
            "pid": getattr(self.popen, "pid", None) if self.popen else None,
        }


class ServiceRegistry:

    # How long a process gets at each rung of the escalation. Short on
    # purpose: this runs on the UI thread from Client.cleanup(), so the whole
    # ladder has to fit inside a shutdown somebody is watching.
    STOP_TIMEOUT = 5.0
    KILL_TIMEOUT = 2.0

    # How often the supervisor looks. Fast enough that a dead child is noticed
    # before anybody has finished wondering, slow enough to cost nothing.
    POLL_SECONDS = 0.5

    def __init__(self, client):
        self.client = client
        self.services: dict[str, Service] = {}
        self._lock = threading.RLock()
        self._order = 0

        # The two capabilities the panel itself runs, as facades rather than
        # as whatever is currently implementing them. They hold the state -
        # what the assistant is doing, what it last said - which has to
        # outlive any one implementation of it. See src/registries/speech.py.
        from src.registries.speech import (JudgeFacade, SpeechFacade,
                                           VoiceFacade)
        self.STT = SpeechFacade(client)
        self.TTS = VoiceFacade(client)
        # The judge holds no state of its own - one question, one key. It is
        # a facade so that it sits on the provider stack like the other two,
        # and so a plugin can replace it without anything else knowing.
        self.JUDGE = JudgeFacade(client)

        # Who supplies each named capability. `providers[name]` is whoever
        # holds it now; `_displaced[name]` is everything under them, newest
        # first, so releasing a claim uncovers the one below it.
        self.providers: dict[str, Provider] = {}
        self._displaced: dict[str, list] = {}
        self._watchers: dict[str, list] = {}

        # Not a service itself. Registering it would mean stop_all() stopping
        # the thing doing the stopping, part way down its own list.
        self._supervisor = None
        self._supervisor_stop = threading.Event()

    ## -- logging

    def _log(self, level: str, message: str) -> None:
        try:
            self.client.log(level, f"[Services] {message}")
        except Exception:
            pass

    ## -- registering

    def create(self, owner: str, name: str, target, *args,
               survives_reload: bool = False, **kwargs) -> Service | None:
        """
        A named background thread.

        The target is handed a `threading.Event` as its first argument and is
        expected to check it - a stop is a request rather than a signal, and
        nothing here can interrupt a thread parked in a native call.

        Registering over one **this owner** already has rebinds it rather than
        replacing it: the running thread is left alone and the next start()
        uses the new target. That is what lets a reloaded plugin adopt a
        service it asked to survive, without a second one appearing.

        Another owner's name is refused. Names are global, so two plugins
        picking `poller` is a collision rather than a handover - and rebinding
        would hand the second one a thread it did not start and stop it on an
        unload the first knows nothing about.
        """
        with self._lock:
            existing = self.services.get(name)
            if existing is not None:
                if not self._may_rebind(existing, owner):
                    return None
                self._rebind(existing, owner, target=target, args=args,
                             kwargs=kwargs, survives_reload=survives_reload)
                return existing

            self._order += 1
            entry = Service(owner, name, Service.THREAD, self._order,
                            survives_reload)
            entry.target = target
            entry.args = args
            entry.kwargs = kwargs
            self.services[name] = entry
            return entry

    def spawn(self, owner: str, name: str, command, on_stop=None,
              on_exit=None, companions=(), restart: Restart = None,
              survives_reload: bool = False) -> Service | None:
        """
        A child process, supervised.

        `command` is a **callable answering with the argv**, not the argv.
        Anything worth restarting is worth restarting as it is now rather than
        as it was the first time - the speech process is handed its wake
        words, its sensitivity and its precision on the command line, and a
        stored list would bring back the settings that have since changed.

        `on_stop` is the polite request, in whatever protocol this process
        speaks, answering whether it was delivered. The escalation after it -
        terminate, wait, kill, wait - is the same for every process and lives
        here.

        `on_exit(code, restarting)` is called when it goes without being
        asked. The registry logs; whether anybody should be **told** depends
        on what the service was for, which the registry does not know.

        `companions` are thread services that only make sense while this is
        alive. Started after it, stopped before it, and stopped again when it
        dies on its own.
        """
        with self._lock:
            existing = self.services.get(name)
            if existing is not None:
                if not self._may_rebind(existing, owner):
                    return None
                self._rebind(existing, owner, command=command,
                             on_stop=on_stop, on_exit=on_exit,
                             companions=companions, restart=restart,
                             survives_reload=survives_reload)
                return existing

            self._order += 1
            entry = Service(owner, name, Service.PROCESS, self._order,
                            survives_reload)
            entry.command = command
            entry.on_stop = on_stop
            entry.on_exit = on_exit
            entry.companions = tuple(companions or ())
            entry.restart = restart
            self.services[name] = entry
            return entry

    def _may_rebind(self, entry: Service, owner: str) -> bool:
        """
        Whether this owner is allowed to register over what is already there.

        Only its own. A service is a plugin's own internal machinery, unlike a
        provider, which exists to be taken over - so there is no `claim()` here
        and no escape hatch. Two plugins wanting one thing between them want a
        provider, and a plugin re-taking its own after a reload is the same
        owner and goes straight through.
        """
        if entry.owner == owner:
            return True
        self._log("warning",
                  f"'{owner}' cannot register '{entry.name}' - '{entry.owner}' "
                  f"already has a {entry.kind} under that name. Prefix service "
                  f"names with your plugin key.")
        return False

    def _rebind(self, entry: Service, owner: str, **fields) -> None:
        """
        Point an existing registration at a new set of callables.

        Always the same owner - see `_may_rebind`. A service that survived a
        reload is still running, but every callable describing it closes over
        the instance that has gone - the command factory, the stop request, the
        exit handler. Rebinding is how the new instance takes it back.

        The running thing is deliberately left alone. For a process that is
        the whole point: it is code on disk in its own interpreter and the
        reload never touched it. For a thread it is the caveat - the thread
        goes on executing the module that was replaced, which is why a thread
        should rarely ask to survive one.
        """
        entry.owner = owner
        entry.orphaned = False
        for key, value in fields.items():
            if value is None and key in ("on_stop", "on_exit", "restart"):
                continue
            if key == "companions":
                entry.companions = tuple(value or ())
            elif key == "survives_reload":
                entry.survives_reload = bool(value)
            else:
                setattr(entry, key, value)

    ## -- running

    def start(self, name: str) -> bool:
        entry = self.services.get(name)
        if entry is None:
            self._log("warning", f"start('{name}') - nothing registered.")
            return False
        if entry.is_active():
            return True

        entry.stopping = False
        if entry.kind == Service.THREAD:
            return self._start_thread(entry)
        return self._start_process(entry)

    def _start_thread(self, entry: Service) -> bool:
        entry.stop_event = threading.Event()
        thread = threading.Thread(
            target=entry.target,
            args=(entry.stop_event, *entry.args),
            kwargs=entry.kwargs,
            name=f"__service_{entry.name}",
            daemon=True,
        )
        entry.thread = thread
        thread.start()
        return True

    def _start_process(self, entry: Service) -> bool:
        try:
            argv = entry.command() if callable(entry.command) else entry.command
        except Exception as exc:
            self._log("error", f"'{entry.name}' could not build its command: {exc}")
            return False
        if not argv:
            self._log("error", f"'{entry.name}' has no command to run.")
            return False

        try:
            entry.popen = subprocess.Popen(argv)
        except Exception as exc:
            self._log("error", f"'{entry.name}' would not start: {exc}")
            entry.popen = None
            return False

        entry.started_at = time.time()
        entry.retry_at = 0.0
        self._log("info", f"'{entry.name}' started as pid {entry.popen.pid}.")

        for companion in entry.companions:
            self.start(companion)

        self._ensure_supervisor()
        return True

    ## -- stopping

    def stop(self, name: str, wait: bool = True) -> None:
        """
        Ask a service to stop.

        A thread is asked and not waited for - `wait_for_stop` is the second
        half, and separating them is what lets a shutdown ask everything and
        then collect it.

        A process is escalated until it is actually gone, because terminate()
        is a request and a process parked in a native call may never act on
        one.
        """
        entry = self.services.get(name)
        if entry is None:
            return

        entry.stopping = True

        if entry.kind == Service.THREAD:
            entry.stop_event.set()
            return

        for companion in entry.companions:
            self.stop(companion)

        process = entry.popen
        if process is None or process.poll() is not None:
            entry.popen = None
            return

        asked = False
        if entry.on_stop is not None:
            try:
                asked = bool(entry.on_stop())
            except Exception as exc:
                self._log("warning", f"'{name}' refused its stop request: {exc}")

        if asked and wait:
            try:
                process.wait(timeout=self.STOP_TIMEOUT)
                entry.popen = None
                self._log("info", f"'{name}' exited cleanly.")
                return
            except subprocess.TimeoutExpired:
                self._log("warning", f"'{name}' ignored the stop request - "
                                     f"terminating.")
            except Exception as exc:
                self._log("warning", f"'{name}' could not be waited on: {exc}")

        self.kill(name)

    def kill(self, name: str) -> None:
        """
        Force a process down, escalating until it is actually gone.

        The two rungs are the same for every process, which is why they are
        here rather than in each service that spawns one.
        """
        entry = self.services.get(name)
        if entry is None or entry.kind != Service.PROCESS:
            return

        entry.stopping = True
        process, entry.popen = entry.popen, None
        if process is None or process.poll() is not None:
            return

        for step, label in ((process.terminate, "terminate"),
                            (process.kill, "kill")):
            try:
                step()
                process.wait(timeout=self.KILL_TIMEOUT)
                self._log("info", f"'{name}' ended by {label}.")
                return
            except subprocess.TimeoutExpired:
                continue
            except Exception as exc:
                self._log("warning", f"'{name}' {label} failed: {exc}")

        self._log("error", f"'{name}' would not die. Whatever it was holding - "
                           f"a device, a port - is still held.")

    def wait_for_stop(self, name: str, timeout: float = 1.0) -> None:
        entry = self.services.get(name)
        if entry is None:
            return
        if entry.kind == Service.THREAD and entry.thread:
            try:
                entry.thread.join(timeout)
            except RuntimeError:
                # Joining the thread this is running on. Nothing to wait for.
                pass
            return
        if entry.popen is not None:
            try:
                entry.popen.wait(timeout=timeout)
            except Exception:
                pass

    def stop_all(self) -> None:
        """
        Everything, newest registration first.

        Backwards through the registration order rather than through the dict:
        a service registered late is usually one that depends on the ones
        before it, and stopping the timeout scheduler while the assistant is
        still using it is the wrong end.
        """
        self._supervisor_stop.set()

        with self._lock:
            entries = sorted(self.services.values(), key=lambda e: -e.order)

        for entry in entries:
            if not entry.is_active():
                continue
            self._log("info", f"Stopping {entry.kind} '{entry.name}'.")
            self.stop(entry.name)
        for entry in entries:
            self.wait_for_stop(entry.name)

    ## -- providers

    def provide(self, owner: str, name: str, factory,
                description: str = "") -> bool:
        """
        Say who supplies a capability.

        The panel registers its own for `assistant.stt` and `assistant.tts` at
        startup, which is what puts a stock install at the bottom of the stack
        and means a plugin releasing a claim always uncovers something that
        works.

        Registering a name another owner holds is **refused**, the way the API
        registry refuses a key - two owners quietly fighting over one name is
        much harder to find than a warning. `claim()` is the deliberate way
        past it.
        """
        with self._lock:
            held = self.providers.get(name)
            if held is not None and held.owner != owner:
                self._log("warning",
                          f"'{owner}' cannot provide '{name}' - '{held.owner}' "
                          f"already does. Use claim() to take it.")
                return False
            self.providers[name] = Provider(owner, name, factory, description)
        self._tell_watchers(name)
        return True

    def claim(self, owner: str, name: str, factory,
              description: str = "") -> bool:
        """
        Take a capability from whoever currently supplies it.

        What was there is remembered rather than dropped, and comes back when
        this owner unregisters. A plugin that claimed the speech recogniser
        and then unloaded would otherwise leave the panel with none until it
        was restarted - and restoring it by hand would mean knowing how the
        client builds its own.
        """
        with self._lock:
            held = self.providers.get(name)
            if held is not None and held.owner != owner:
                self._displaced.setdefault(name, []).insert(0, held)
                self._log("info", f"'{owner}' has taken '{name}' from "
                                  f"'{held.owner}'.")
            self.providers[name] = Provider(owner, name, factory, description)
        self._tell_watchers(name)
        return True

    def release(self, owner: str, name: str = "") -> int:
        """
        Give a claimed capability back, uncovering whoever had it before.
        """
        with self._lock:
            names = [n for n, p in self.providers.items()
                     if p.owner == owner and (not name or n == name)]
            changed = []
            for held in names:
                under = self._displaced.get(held) or []
                if under:
                    restored = under.pop(0)
                    self.providers[held] = restored
                    self._log("info", f"'{held}' is back with "
                                      f"'{restored.owner}'.")
                else:
                    self.providers.pop(held, None)
                    self._log("warning", f"'{held}' has no provider - "
                                         f"'{owner}' held the only one.")
                if not self._displaced.get(held):
                    self._displaced.pop(held, None)
                changed.append(held)
        for held in changed:
            self._tell_watchers(held)
        return len(changed)

    def provider(self, name: str):
        """Whoever supplies it now, or None."""
        return self.providers.get(name)

    def build(self, name: str, *args, **kwargs):
        """
        Make the thing a capability's provider supplies.

        None when nothing provides it, rather than a raise: the caller is
        already reporting that a subsystem could not start, and a missing
        provider is one more reason on that list rather than a different kind
        of failure.
        """
        held = self.providers.get(name)
        if held is None:
            self._log("error", f"Nothing provides '{name}'.")
            return None
        return held.build(*args, **kwargs)

    def providers_for(self, owner: str) -> list:
        return [p for p in self.providers.values() if p.owner == owner]

    def watch_provider(self, name: str, callback) -> None:
        """
        Be told when a capability changes hands.

        Without it a plugin claims one, nothing rebuilds, and the panel goes on
        running the implementation it already had - which looks from outside
        like the claim not having worked and leaves nothing in the log.
        """
        self._watchers.setdefault(name, []).append(callback)

    def _tell_watchers(self, name: str) -> None:
        for callback in list(self._watchers.get(name, [])):
            try:
                callback(name)
            except Exception as exc:
                self._log("warning",
                          f"A watcher of '{name}' raised: {exc}")

    ## -- ownership

    def unregister(self, owner: str, name: str = "", reloading: bool = False):
        """
        Give back what an owner registered.

        `reloading` is the difference between a plugin going away and a plugin
        coming straight back, and the caller already knows which: the loader
        passes a carryover for one and not the other. A service that asked to
        survive a reload is marked and left running for one; on a genuine
        unload everything stops, with no opt-out. A thread still running
        against a module nobody can reach is not a thing to leave behind for
        weeks on a panel that does not get restarted.
        """
        with self._lock:
            targets = [entry for entry in self.services.values()
                       if entry.owner == owner and (not name or entry.name == name)]

        # Its claims go with it, whether or not this is a reload. A provider
        # is a factory bound to an instance that has gone, so a claim held
        # across a reload would build from the module that was replaced. The
        # new instance claims again in its own load(), which is one line and
        # is where somebody looking for it would expect to find it.
        self.release(owner)

        for entry in targets:
            if reloading and entry.survives_reload:
                entry.orphaned = True
                self._log("debug", f"'{entry.name}' held across the reload of "
                                   f"'{owner}'.")
                continue
            self.stop(entry.name)
            self.wait_for_stop(entry.name)
            with self._lock:
                self.services.pop(entry.name, None)

    def reap(self, owner: str) -> int:
        """
        Stop anything held across a reload that nothing adopted back.

        Called by the loader once the new instance's load() has run. Without
        it, opting out of cleanup would mean leaking by default: a plugin
        whose load() raised, or that changed the name it registers under,
        would leave a process holding whatever it holds until the panel is
        restarted - and this one is not.
        """
        with self._lock:
            stale = [entry for entry in self.services.values()
                     if entry.owner == owner and entry.orphaned]

        for entry in stale:
            self._log("warning", f"'{entry.name}' survived the reload of "
                                 f"'{owner}' and nothing claimed it back - "
                                 f"stopping it.")
            self.stop(entry.name)
            self.wait_for_stop(entry.name)
            with self._lock:
                self.services.pop(entry.name, None)
        return len(stale)

    def entries_for(self, owner: str) -> list:
        with self._lock:
            return [entry for entry in self.services.values()
                    if entry.owner == owner]

    def names_for(self, owner: str) -> list:
        return [entry.name for entry in self.entries_for(owner)]

    ## -- asking

    def is_active(self, name: str) -> bool:
        entry = self.services.get(name)
        return bool(entry and entry.is_active())

    def process(self, name: str):
        """The Popen, for a caller that needs to talk to it directly."""
        entry = self.services.get(name)
        if entry is None or entry.kind != Service.PROCESS:
            return None
        return entry.popen

    def pid(self, name: str):
        process = self.process(name)
        return getattr(process, "pid", None) if process else None

    def snapshot(self) -> list:
        with self._lock:
            entries = sorted(self.services.values(), key=lambda e: e.order)
        return [entry.describe() for entry in entries]

    ## -- the supervisor

    def _ensure_supervisor(self) -> None:
        if self._supervisor is not None and self._supervisor.is_alive():
            return
        self._supervisor_stop = threading.Event()
        self._supervisor = threading.Thread(
            target=self._supervise, name="__service_supervisor", daemon=True)
        self._supervisor.start()

    def _supervise(self) -> None:
        while not self._supervisor_stop.is_set():
            try:
                self._sweep()
            except Exception as exc:
                self._log("warning", f"Supervisor pass failed: {exc}")
            self._supervisor_stop.wait(self.POLL_SECONDS)

    def _sweep(self) -> None:
        with self._lock:
            entries = [entry for entry in self.services.values()
                       if entry.kind == Service.PROCESS]

        now = time.time()
        for entry in entries:
            if entry.retry_at and now >= entry.retry_at and entry.popen is None:
                entry.retry_at = 0.0
                self._log("warning", f"Restarting '{entry.name}' "
                                     f"(attempt {entry.attempt}).")
                self._start_process(entry)
                continue

            process = entry.popen
            if process is None or process.poll() is None:
                continue
            self._died(entry, process.poll())

    def _died(self, entry: Service, code) -> None:
        """
        A process went without being asked to.

        Reported either way, and reported here rather than left to whoever
        registered it - a service dying quietly is the failure that looks like
        the feature never existing. Whether anybody should be *told* is the
        owner's call through on_exit, because the registry does not know
        whether this one mattered to a person.
        """
        entry.popen = None

        for companion in entry.companions:
            self.stop(companion)

        if entry.stopping:
            return

        ran_for = time.time() - entry.started_at
        policy = entry.restart

        if policy is not None and ran_for > policy.window:
            # It held up for longer than the window, so whatever went wrong
            # now is not what went wrong before. Start counting again.
            entry.attempt = 0

        delay = policy.delay_for(entry.attempt) if policy is not None else None

        if delay is None:
            level = "error" if policy is not None else "warning"
            gave_up = (" and will not be restarted" if policy is not None
                       else "")
            self._log(level, f"'{entry.name}' exited with {code} after "
                             f"{ran_for:.0f}s{gave_up}.")
        else:
            entry.attempt += 1
            self._log("warning", f"'{entry.name}' exited with {code} after "
                                 f"{ran_for:.0f}s - restarting"
                                 f"{f' in {delay:.0f}s' if delay else ''} "
                                 f"({entry.attempt} of {policy.attempts}).")
            # A delay of zero means now rather than on the next pass. Waiting
            # half a second to honour "straight away" is half a second the
            # panel spends deaf for no reason anybody asked for.
            entry.retry_at = time.time() + delay if delay else 0.0

        if entry.on_exit is not None:
            try:
                entry.on_exit(code, delay is not None)
            except Exception as exc:
                self._log("warning", f"'{entry.name}' exit handler raised: {exc}")

        if delay is not None and not entry.retry_at and not entry.stopping:
            self._start_process(entry)

    ## -- dict-like

    def get(self, name: str):
        return self.services.get(name)

    def __contains__(self, name) -> bool:
        return name in self.services

    def __iter__(self):
        return iter(self.services)

    def __getitem__(self, name):
        return self.services[name]

    def __len__(self) -> int:
        return len(self.services)
