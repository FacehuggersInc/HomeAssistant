from src import time, Thread, Lock, ThreadEvent as Event
from typing import Callable, Optional


class TimeoutScheduler:
	def __init__(self, client):
		self.client   = client
		self.timeouts = {}
		self.active   = {}
		client.THREADS.create("__timeout_scheduler", self.__scheduler_thread)
		client.THREADS.start("__timeout_scheduler")

	def add(self, sec: int, callback: Callable, id: str, autostart: bool = False) -> str:
		t = [sec, callback, id]
		if autostart:
			start = time.time()
			t.append(start)
			self.active[start + sec] = t
		self.timeouts[id] = t
		return id

	def remaining(self, id: str) -> float:
		t = self.timeouts.get(id)
		if t and len(t) > 3:
			return max(0.0, (t[-1] + t[0]) - time.time())
		return 0.0

	def start(self, id: str) -> None:
		t = self.timeouts[id]
		start = time.time()
		t.append(start)
		self.active[start + t[0]] = t

	def cancel(self, id: str) -> None:
		t = self.timeouts.get(id)
		if not t:
			return
		if len(t) > 3:
			deadline = t[-1] + t[0]
			self.active.pop(deadline, None)

	def prune(self) -> int:
		pending = {id(t) for t in self.active.values()}
		stale = [tid for tid, t in self.timeouts.items() if id(t) not in pending]
		for tid in stale:
			del self.timeouts[tid]
		return len(stale)

	def __scheduler_thread(self, stop_event: Event) -> None:
		while not stop_event.is_set():
			time.sleep(0.1)
			for deadline in list(self.active.keys()):
				if time.time() >= deadline:
					entry = self.active.pop(deadline, None)
					if entry:
						self.client.call_on_ui(entry[1])

	def stop(self) -> None:
		self.client.THREADS.stop("__timeout_scheduler")