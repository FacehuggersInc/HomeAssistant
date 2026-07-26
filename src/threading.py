import threading
import time

class ThreadManager:
	def __init__(self):
		self.threads = {}  # name -> {thread, stop_event, target, args, kwargs}

	def create(self, name, target, *args, **kwargs):
		if name in self.threads and self.is_active(name):
			return

		stop_event = threading.Event()
		self.threads[name] = {
			"target": target,
			"args": args,
			"kwargs": kwargs,
			"stop_event": stop_event,
			"thread": None
		}

	def start(self, name):
		if name not in self.threads:
			return

		entry = self.threads[name]
		if entry["thread"] and entry["thread"].is_alive():
			return

		# Reset stop flag
		entry["stop_event"].clear()

		# Create a fresh thread
		thread = threading.Thread(
			target=entry["target"],
			args=(entry["stop_event"], *entry["args"]),
			kwargs=entry["kwargs"],
			daemon=True
		)
		entry["thread"] = thread
		thread.start()

	def stop(self, name):
		if name in self.threads:
			self.threads[name]["stop_event"].set()

	def is_active(self, name):
		if name in self.threads and self.threads[name]["thread"]:
			return self.threads[name]["thread"].is_alive()
		return False

	def wait_for_stop(self, name, timeout=1):
		if name in self.threads and self.threads[name]["thread"]:
			try: self.threads[name]["thread"].join(timeout)
			except: print("Cannot Join with Current Thread.")


	# --- Dict-like / iterable behavior ---
	def get(self, name:str) -> bool:
		return self.threads.get(name)

	def __iter__(self):
		return iter(self.threads)

	def __getitem__(self, name):
		return self.threads[name]

	def __len__(self):
		return len(self.threads)