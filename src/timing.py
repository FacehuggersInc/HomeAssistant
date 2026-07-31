import time
from threading import Event, Lock
from typing import Callable, Optional


class TimeoutScheduler:
	"""
	Named deferred callbacks, fired on the UI thread.

	A registration is created with add() and armed with start(). Re-arming a
	timeout that is already counting REPLACES its deadline rather than adding
	a second one. That is load-bearing: every auto-close in the app calls
	start() on each interaction, so an implementation that stacked deadlines
	fired the callback once per interaction, fired it at the earliest deadline
	rather than the latest, and grew its registration without bound.
	"""

	def __init__(self, client):
		self.client = client
		# id -> {"sec", "callback", "deadline": float | None, "transient": bool}
		self.timeouts: dict[str, dict] = {}
		self._lock = Lock()
		client.THREADS.create("__timeout_scheduler", self.__scheduler_thread)
		client.THREADS.start("__timeout_scheduler")

	def add(self, sec: float, callback: Callable, id: str,
	        autostart: bool = False, transient: bool = False,
	        idle: bool = False) -> str:
		"""
		Register a timeout.

		`idle` marks one that measures NOTHING HAPPENING rather than a
		duration. Those are held while a dialog is open: somebody reading a
		map or answering "who is this" is doing something, and a timer that
		takes the page away underneath them is measuring the wrong thing. A
		display duration - a transient widget's few seconds - is not idle and
		keeps counting.

		`transient` marks a registration whose id is generated per instance -
		a uuid, or anything derived from an object - so prune() may drop it
		once it is no longer counting. Leave it False for a registration that
		is created once and re-armed for the life of the app, or prune() will
		remove it between arms and start() will have nothing to find.
		"""
		with self._lock:
			self.timeouts[id] = {
				"sec":       float(sec),
				"callback":  callback,
				"deadline":  (time.time() + float(sec)) if autostart else None,
				"transient": bool(transient),
				"idle":      bool(idle),
			}
		return id

	def start(self, id: str) -> None:
		"""Arm or re-arm. Restarts from zero; never stacks a second deadline."""
		with self._lock:
			entry = self.timeouts.get(id)
			if entry is None:
				missing = True
			else:
				missing = False
				entry["deadline"] = time.time() + entry["sec"]
		if missing:
			# Silent here used to mean an auto-close that simply stopped
			# working, with nothing anywhere to say why.
			self.client.log("warning",
			                f"[Timeouts] start('{id}') on an unregistered timeout - "
			                f"call add() first.")

	def cancel(self, id: str) -> None:
		"""Stop counting. The registration stays, so start() works again."""
		with self._lock:
			entry = self.timeouts.get(id)
			if entry is not None:
				entry["deadline"] = None

	def discard(self, id: str) -> None:
		"""Cancel and forget entirely. For ids belonging to one instance."""
		with self._lock:
			self.timeouts.pop(id, None)

	def remaining(self, id: str) -> float:
		with self._lock:
			entry = self.timeouts.get(id)
			if not entry or entry["deadline"] is None:
				return 0.0
			return max(0.0, entry["deadline"] - time.time())

	def is_counting(self, id: str) -> bool:
		with self._lock:
			entry = self.timeouts.get(id)
			return bool(entry and entry["deadline"] is not None)

	def prune(self) -> int:
		"""Drop transient registrations that are no longer counting."""
		with self._lock:
			stale = [tid for tid, e in self.timeouts.items()
			         if e["transient"] and e["deadline"] is None]
			for tid in stale:
				del self.timeouts[tid]
			return len(stale)

	def __scheduler_thread(self, stop_event: Event) -> None:
		while not stop_event.is_set():
			# wait(), not sleep(): a stop arriving mid-sleep is otherwise
			# ignored for the rest of it.
			stop_event.wait(0.1)
			if stop_event.is_set():
				break

			now = time.time()
			# Asked once per pass, not once per entry.
			blocked = self._dialog_open()
			due: list[Callable] = []
			# Collected under the lock, dispatched outside it - call_on_ui
			# emits a queued signal, and holding the lock across that invites
			# a stall if anything on the UI thread reaches back in here.
			with self._lock:
				for entry in self.timeouts.values():
					deadline = entry["deadline"]
					if deadline is None:
						continue
					if entry.get("idle") and blocked:
						# Pushed out, not cancelled. The countdown restarts
						# from when the dialog closes, which is what "nothing
						# has happened for twenty seconds" should mean.
						entry["deadline"] = now + entry["sec"]
						continue
					if now >= deadline:
						entry["deadline"] = None
						due.append(entry["callback"])

			for callback in due:
				self.client.call_on_ui(callback)

	def _dialog_open(self) -> bool:
		"""
		Whether a dialog is up.

		No try/except around the lookup: DIALOG and dialog_stack both exist on
		a built client, and swallowing an AttributeError here would turn a
		rename into an idle timer that quietly stopped being held.
		"""
		manager = getattr(self.client, "DIALOG", None)
		if manager is None:
			return False
		return bool(manager.dialog_stack)

	def stop(self) -> None:
		self.client.THREADS.stop("__timeout_scheduler")

	# --- Dict-like behaviour, kept from the original ---

	def get(self, id: str) -> Optional[dict]:
		return self.timeouts.get(id)

	def __contains__(self, id: str) -> bool:
		return id in self.timeouts

	def __len__(self) -> int:
		return len(self.timeouts)
