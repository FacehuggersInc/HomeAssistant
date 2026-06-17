from src import *
from typing import Callable, Optional


class Timeout:
	def __init__(self, duration: float, callback: Callable, autostart: bool = False):
		"""
		duration: how many seconds until timeout
		callback: function to call when timeout ends
		autostart: if True, starts automatically
		"""
		self.duration = duration
		self.callback = callback
		self.start_time: Optional[float] = None
		self._thread: Optional[threading.Thread] = None
		self._finished = threading.Event()
		self.__lock = threading.Lock()

		if autostart:
			self.start()

	def start(self):
		"""Start or restart the timeout."""
		print(f"Timeout of {self.duration} -> Started")
		with self.__lock:
			self.cancel()  # stop existing one if running
			self._finished.clear()
			self.start_time = time.time()
			self._thread = threading.Thread(target=self.__timeout_thread, daemon=True)
			self._thread.start()

	def __timeout_thread(self):
		deadline = self.start_time + self.duration
		while not self._finished.is_set():
			remaining = deadline - time.time()
			if remaining <= 0:
				self.callback()
				self._finished.set()
				break
			time.sleep(0.05)

	def is_alive(self) -> bool:
		"""Check if timeout is still running."""
		return not self._finished.is_set()

	def remaining(self) -> float:
		"""How much time is left in seconds."""
		if self.start_time is None:
			return self.duration
		elapsed = time.time() - self.start_time
		return max(0, self.duration - elapsed)

	def cancel(self):
		"""Cancel the timeout."""
		self._finished.set()

	def done(self) -> bool:
		"""True if timeout has finished."""
		return self._finished.is_set()

class TimeoutScheduler:
	def __init__(self, client):
		self.client = client
		self.timeouts = {}
		self.active = {}
		client.THREADS.create(
			"__timeout_scheduler",
			self.__scheduler_thread
		)
		client.THREADS.start("__timeout_scheduler")

	def add(self, sec:int, callback:Callable, id:str, autostart:bool = False):
		t = [sec, callback, id]
		if autostart:
			start = time.time()
			t.append(start)
			self.active[ start + sec ] = t
		
		self.timeouts[id] = t

		return id
	
	def remaining(self, id):
		if self.timeouts.get(id):
			return time.time() - self.timeouts[id][-1]

	def start(self, id:str):
		t = self.timeouts[id]
		start = time.time()
		t.append(start)
		self.active[ start + t[0] ] = t

	def cancel(self, id:str):
		t = self.timeouts[id]
		timeout = t[-1] + t[0]
		if self.active.get(timeout):
			del self.active[timeout]

	def __scheduler_thread(self, stop_event):
		while not stop_event.is_set():
			time.sleep(0.1)
			times = list( self.active.keys() )
			for timeout in times:
				if time.time() >= timeout:
					if self.active.get(timeout):
						Thread(target = self.active[timeout][1]).start()
						
					try: del self.active[timeout]
					except: pass

	def stop(self):
		"""Stop the scheduler loop."""
		self.client.THREADS.stop("__timeout_scheduler")

