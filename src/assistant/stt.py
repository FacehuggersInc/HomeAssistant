from __future__ import annotations

import re
import os
import sys
import json
import time
import queue
import string
import socket
import subprocess
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING


from src.assistant import nlp, normalize

if TYPE_CHECKING:
	from src.main import Client

SENTENCE_END_TOKENS = {'.', '!', '?', ';'}

_HERE = Path(__file__).resolve().parent
PROCESS_REALTIMESTT = str(_HERE / "realtimestt-process.py")
PROCESS_VOSK        = str(_HERE / "vosk-process.py")
PROCESS_WHISPER     = str(_HERE / "whisper-process.py")

_CANCELLED = object()


class Session():
	def __init__(self, client):
		self.__client = client
		self.__queued = queue.Queue()
		self.matcher = nlp.new_matcher()
		self.is_open = False
		self.cancelled = False
		self.__id = f"session:{self.__client.uuid()}"

	def __enter__(self):
		self.is_open = True
		self.cancelled = False
		self.__client.STT.open_session()
		self.__client.TIMEOUTS.add(60 * 5, self.timed_out, self.__id,
		                           transient=True)
		self.__client.TIMEOUTS.start(self.__id)
		return self

	def __exit__(self, exc_type, exc_val, exc_tb):
		self.is_open = False
		self.__client.STT.close_session()
		# discard(), not cancel(): the id is a uuid belonging to this session
		# alone, so a cancelled registration would sit in the table until the
		# hourly prune and one entry would accumulate per conversation.
		self.__client.TIMEOUTS.discard(self.__id)
		# Release anyone still blocked in wait_for_phrase().
		self.__queued.put(_CANCELLED)

	def id(self) -> str:
		return self.__id

	def timed_out(self):
		# Used to call close_session() directly, which reset the STT but left
		# wait_for_phrase() blocked on an empty queue forever - the skill
		# thread never returned.
		self.__client.log("info", "[Session] Timed out.")
		self.cancel()

	def cancel(self):
		"""End the session and release the waiter. Safe from any thread."""
		if self.cancelled:
			return
		self.cancelled = True
		self.__queued.put(_CANCELLED)
		if self.is_open:
			self.close()

	def close(self):
		self.__exit__(None, None, None)

	def put(self, next_transcribed:str):
		self.__queued.put(next_transcribed)

	def wait_for_phrase(self, timeout: float = None) -> str | None:
		"""
		Next phrase, or None when the user backed out.

		None means cancelled, timed out or closed - callers should break out
		of their prompt loop rather than asking again.
		"""
		try:
			phrase = self.__queued.get(timeout=timeout) if timeout else self.__queued.get()
		except queue.Empty:
			return None

		if phrase is _CANCELLED or not self.is_open:
			return None
		# A follow-up question inside a session. Backing out here is still
		# handled directly: there is no intent matching in a session, so there
		# is no skill to route it to.
		if normalize.is_cancel(phrase):
			self.__client.log("info", f"[Session] Cancelled by '{phrase}'.")
			self.cancel()
			return None
		return phrase
	
	def push(self):
		self.__client.STT.processing = False


class STTProcessing():
	def __init__(self, client, process:str = "whisper",
				 input_device=None, model:str = "tiny.en", wake_words=None,
				 session_silence_ms:int = 800):
		self.client = client
		self.input_device = input_device
		self.model = model
		self.wake_words = list(wake_words or [])
		# How long a silence ends a phrase once a session is open. Wake mode
		# keeps its own much shorter threshold.
		self.session_silence_ms = int(session_silence_ms)
		self.last_error : str = ""
		self.process_type = process
		self.__process_path = None
		match self.process_type:
			case "whisper": self.__process_path = PROCESS_WHISPER
			case _: self.__process_path = PROCESS_WHISPER

		#Process & Socket
		self.process = None
		self.listening = False
		self.host = "127.0.0.1"
		self.ports = {
			"command" : 65432,
			"data" : 65433
		}

		self.processing : bool = False

		self.woke_with : str = None
		# When the wake word was heard, so a wake nobody followed up on can be
		# stood down. See check_wake_timeout().
		self.woke_at : float = 0.0

		self.session :Session = None
		self.route = "wake"


	## WAKE WORD

	@staticmethod
	def find_wake(text: str, wake: str):
		"""
		Last occurrence of a wake word, case-insensitively and on word
		boundaries. Returns the match or None.

		Whisper capitalises the first word of every transcript, so the old
		`wake in processed` test was False for essentially every real
		utterance - "alexa" is not in "Alexa, set a timer for 1 minute." Word
		boundaries matter too: a short wake word otherwise fires inside
		ordinary words.
		"""
		if not text or not wake:
			return None
		found = None
		for match in re.finditer(rf"\b{re.escape(wake)}\b", text, re.IGNORECASE):
			found = match
		return found

	@classmethod
	def strip_wake(cls, text: str, wake: str) -> str:
		"""Everything after the wake word, or the whole phrase if absent."""
		match = cls.find_wake(text, wake)
		return text[match.end():].strip() if match else text.strip()

	## PROCESSING
	def limit_words( self, limit:int, phrase:str ):
		" ".join( phrase.split(" ")[:limit] )

	def clean_text(self, text:str) -> str:
		text = ''.join(ch for ch in text if ch not in string.punctuation).strip()
		return text

	def process_phrase(self, phrase:str):
		skill, _ = self.client.SKILLS.parse( phrase )
		if skill:
			self.client.iterate_event_callables("on_woke_assistant", (skill, phrase))
		if self.woke_with: self.woke_with = None
		self.processing = False

		# Stood back down, here, rather than left to the wake timeout.
		#
		# A skill that has run is finished, and nothing else was resetting the
		# status on this path - only the no-skill branch of
		# detect_wake_words_full() did. So a question that worked left the pill
		# reading "listening" over its own answer, until the timeout noticed
		# some seconds later. That is the wrong way round: the timeout is a
		# safety net for a process that never reports back, not the mechanism.
		#
		# Unless a session was opened - a skill expecting a follow-up - in which
		# case listening is the truth and saying otherwise would be worse.
		if not self.is_session():
			self.woke_at = 0.0
			self.client.ASSIST_STATUS = "LIVE"
			self.client.ASSIST_VOICE_ACTIVITY_LEVEL = 0.0

	def detect_wake_words_full(self, processed:str):
		found_skill = False
		for wake, max_words, min_words in self.client.SKILLS.wake_args:
			if not found_skill and self.find_wake(processed, wake):
				phrase = self.strip_wake(processed, wake)
				words = phrase.split(" ")
				if phrase and len(words) >= min_words:
					found_skill = True
					self.woke_with = wake
					self.start_skill_parse(wake, processed)
					break
		
		if not found_skill:
			self.processing = False
			self.client.ASSIST_STATUS = "LIVE"

	def start_skill_parse(self, wake:str, processed:str):
		phrase = self.strip_wake(processed, wake)

		if wake and phrase:
			self.client.log("info", f"[STTProcessing] Routing -> '{processed}' to {self.route}")
			Thread(target = self.process_phrase, args = [self.clean_text( phrase.strip() ), ] ).start()
		else:
			# The wake word on its own, with nothing after it. Stood back down
			# rather than left armed: woke_with lingering here is how the
			# panel ended up believing it was mid-conversation with nobody.
			self.processing = False
			self.woke_with = None
			self.woke_at = 0.0
			self.client.ASSIST_STATUS = "LIVE"

	def routing(self, processed:str):
		match self.route:
			case "wake":
				if not self.woke_with :
					print("Detecting Wake Words")
					self.detect_wake_words_full(processed)
				else:
					print("Sending to Skill Parse")
					self.start_skill_parse(self.woke_with, processed)

			case "session":
				if self.is_session():
					self.client.log("info", f"[STTProcessing] Routing -> '{processed}' to {self.route}")
					self.session.put(processed)
					self.client.ASSIST_STATUS = "LISTENING"
				else:
					self.close_session()
				self.processing = False
		
		self.client.iterate_event_callables("on_assistant_transcribed", processed, True)

	def words_to_numbers(self, text):
		# Kept as a method because plugins and mixins may target it. The old
		# implementation had \s inside its alternation, so " one " matched whole
		# and collapsed to "1" - "for one minute" became "for1minute", a single
		# token no skill pattern could match.
		return normalize.words_to_numbers(text)

	def pre_processing(self, transcribed:str):
		if not self.client.TTS.is_speaking():
			# Dropped before anything else looks at it.
			#
			# The transcriber invents end-screen boilerplate and subtitle
			# credits when it is given silence or music rather than speech,
			# and a panel with speakers hears its own music through its
			# microphone. Acting on that means the panel doing something
			# nobody asked for, mid-song.
			if normalize.is_hallucination(transcribed):
				self.client.log("debug",
					f"[STTProcessing] Ignored '{transcribed}' - nothing was said.")
				return

			if not self.processing:
				self.processing = True
				self.client.ASSIST_STATUS = "THINKING"
				processed = normalize.normalize(transcribed)
				if processed != transcribed:
					self.client.log("debug",
						f"[STTProcessing] Normalised '{transcribed}' -> '{processed}'")
				self.routing( processed )


	def wake_timeout_seconds(self) -> float:
		"""How long to stay listening after a wake with nothing said."""
		try:
			return max(3.0, float(self.client.setting(
				"assistant.wake_listen_timeout.value", 12)))
		except Exception:
			return 12.0

	def check_wake_timeout(self) -> None:
		"""
		Stand down a wake nobody followed up on.

		Called from the client tick. The STT process has its own reset, but
		the panel used to sit in LISTENING until that arrived, refusing new
		wake words the whole time - so waking it and then pausing to think
		left it unusable rather than simply idle.

		Self-healing on the panel's own clock, so a process that never sends
		its reset cannot strand the assistant.
		"""
		if not self.woke_at or self.is_session():
			return
		if self.client.ASSIST_STATUS not in ("LISTENING",):
			return
		if time.time() - self.woke_at < self.wake_timeout_seconds():
			return

		self.client.log("info",
			"[STTProcessing] Wake timed out with nothing said - standing down.")
		self.woke_at = 0.0
		self.woke_with = None
		self.processing = False
		self.client.ASSIST_STATUS = "LIVE"
		self.client.ASSIST_VOICE_ACTIVITY_LEVEL = 0.0

	def cancel(self, reason: str = "") -> None:
		"""
		Abandon whatever the assistant is doing and go back to waiting for the
		wake word. Safe to call from any state, including when nothing is
		happening.
		"""
		self.client.log("info", f"[STTProcessing] Cancelled{f' ({reason})' if reason else ''}.")

		if self.is_session():
			self.session.cancel()
		else:
			self.close_session()

		self.woke_with = None
		self.woke_at = 0.0
		self.processing = False
		self.route = "wake"
		self.client.ASSIST_STATUS = "LIVE"
		self.client.ASSIST_VOICE_ACTIVITY_LEVEL = 0.0
		self.client.iterate_event_callables("on_assistant_cancelled", reason)

	## SESSIONS
	def is_session(self) -> bool:
		return True if isinstance(self.session, Session) and self.session.is_open else False

	def new_session(self) -> Session:
		if not self.is_session():
			self.session = Session(self.client)
			return self.session
		
	def open_session(self):
		if self.is_session():
			self.client.ASSIST_STATUS = "LISTENING"
			self.send_command("START_PASSTHROUGH")
			self.route = "session"

	def close_session(self):
		self.client.ASSIST_STATUS = "LIVE"
		self.send_command("START_WAKE")
		self.route = "wake"
		self.woke_with = None
		self.session = None


	## SOCKET
	def send_command(self, command:str, retries:int = 10):
		for attempt in range(max(1, retries)):
			try:
				with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
					s.settimeout(1.0)
					s.connect( (self.host, self.ports["command"]) )
					s.sendall( f"server:{command}".encode("utf-8") )
					return True
			except (ConnectionRefusedError, OSError):
				if attempt < retries - 1:
					time.sleep(0.5)
		self.client.log("error", "[STTProcessing] Could not connect to STT process to send command")
		return False

	def __listen_for_stt_data(self, stop_event):
		while self.listening and not stop_event.is_set():
			try:
				#While Connected to Self
				with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:

					#Try Connection
					while True:
						try:
							sock.connect( (self.host, self.ports["data"]) )
							break
						except ConnectionRefusedError:
							time.sleep(0.5)

					#If Connections, Data Receive loop
					while self.listening:
						raw = sock.recv(1024 * 5).decode("utf-8")
						if not raw:
							break
						
						try:
							to, command, data = raw.split(":", 2)
							if not to == "host": continue
							match command:
								case "notify":
									if self.client.ASSIST_STATUS == "DORMANT":
										self.client.ASSIST_STATUS = "LIVE"
										self.client.simple_notify(
											"assistant",
											"Assistant: STT",
											"STT is Listening!",
											False
										)
								case "transcribe":
									print(f"Received to Route: {data}")
									self.pre_processing(data)

								case "voice_activity": #Will Get Used A Lot
									if not self.woke_with and not self.client.ASSIST_STATUS == "LISTENING": continue
									try:
										level = float(data)
										level = min(level * 3, 1.0)
										level = round(level, 2)
										self.client.ASSIST_VOICE_ACTIVITY_LEVEL = level
									except:
										self.client.ASSIST_VOICE_ACTIVITY_LEVEL = 0.2

								case "woke":
									# Refreshed rather than ignored while already
									# listening. Ignoring it meant that once the
									# panel was stuck in LISTENING, saying the wake
									# word again did nothing at all - which is
									# exactly what a person does when it looks like
									# it did not hear them.
									self.woke_with = data.strip()
									self.woke_at = time.time()
									self.client.ASSIST_STATUS = "LISTENING"

								case "wait":
									self.client.ASSIST_STATUS = "LIVE"
									self.processing = False
									self.woke_at = 0.0
									if self.woke_with: self.woke_with = None

								case "audio_error":
									self.handle_audio_error(data)

									
						except: pass
			
			except Exception as ex:
				self.client.simple_notify(
					"assistant",
					"Assistant: LISTENING ERROR",
					str(ex)
				)
				time.sleep(1)  # avoid busy loop


	def handle_audio_error(self, message:str):
		"""
		Microphone trouble reported by the STT process.

		An empty message means it recovered. Repeats are swallowed: the process
		retries every 5s, and notifying on each retry would bury the screen.
		"""
		message = (message or "").strip()
		if not message:
			if self.last_error:
				self.last_error = ""
				self.client.ASSIST_STATUS = "LIVE"
				self.client.simple_notify("assistant", "Assistant", "Microphone reconnected.")
			return

		if message == self.last_error:
			return
		self.last_error = message

		self.client.ASSIST_STATUS = "DORMANT"
		self.client.log("error", f"[STTProcessing] Audio error: {message}")
		self.client.simple_notify("error", "Assistant", "Microphone unavailable. Tap for details.")
		self.client.alert(
			"Microphone unavailable",
			"The voice assistant cannot record audio. It will keep retrying in "
			"the background.",
			detail=message,
		)

	## PROCESS
	def start(self):
		if self.process is None or self.process.poll() is not None:
			
			# Every registered skill carries the same wake word, so this is one
			# entry per skill before de-duplication. Deduped once here so the
			# log shows what is actually sent rather than the raw list.
			words = [w[0] for w in self.client.SKILLS.wake_args] or list(self.wake_words)
			words = sorted({w.strip().lower() for w in words if w and w.strip()})
			if not words:
				words = [self.client.wake_word]

			config = json.dumps({
				"wake_words":   words,
				"input_device": self.input_device,
				"model":        self.model,
				"session_silence_ms": self.session_silence_ms,
				# So the process can notice the client dying without a STOP and
				# leave on its own, instead of surviving as an orphan holding
				# the microphone and both ports.
				"parent_pid":   os.getpid(),
			})
			self.client.log("info", f"[STTProcessing] Starting STT: model={self.model} "
									f"device={self.input_device} wake={words}")
			self.process = subprocess.Popen([sys.executable, self.__process_path, config])

			self.listening = True

			self.client.THREADS.create("__stt_receiver_thread", self.__listen_for_stt_data)
			self.client.THREADS.start("__stt_receiver_thread")

	# How long to give the process at each stage of shutting down. Short on
	# purpose: this runs on the UI thread from Client.cleanup(), so the whole
	# escalation has to fit inside a shutdown the user is watching.
	STOP_TIMEOUT = 5.0
	KILL_TIMEOUT = 2.0

	def kill(self):
		"""
		Force the process down, escalating until it is actually gone.

		terminate() alone is a request, not a guarantee - a process parked in a
		native call may never act on it.
		"""
		self.listening = False
		process, self.process = self.process, None
		if process is None or process.poll() is not None:
			return

		for step, label in ((process.terminate, "terminate"), (process.kill, "kill")):
			try:
				step()
				process.wait(timeout=self.KILL_TIMEOUT)
				self.client.log("info", f"[STTProcessing] STT process ended by {label}.")
				return
			except subprocess.TimeoutExpired:
				continue
			except Exception as ex:
				self.client.log("warning", f"[STTProcessing] {label} failed: {ex}")

		self.client.log("error", "[STTProcessing] STT process would not die - it will "
								 "keep holding the microphone and ports 65432/65433.")

	def stop(self):
		"""
		Ask the STT process to exit, then confirm it did.

		Sending STOP and walking away is what left a process behind: if the
		command listener had already gone the message went nowhere, and nothing
		ever checked. The survivor keeps the microphone and both ports, so the
		next launch cannot bind them and comes up silent.
		"""
		self.listening = False

		process = self.process
		if process is None or process.poll() is not None:
			self.process = None
			return

		try:
			self.send_command("STOP", retries=2)
			self.client.simple_notify(
				"assistant",
				"Assistant: STT",
				"Stopping Process"
			)
		except Exception as ex:
			self.client.log("warning", f"[STTProcessing] Error sending STOP: {ex}")

		try:
			process.wait(timeout=self.STOP_TIMEOUT)
			self.process = None
			self.client.log("info", "[STTProcessing] STT process exited cleanly.")
			return
		except subprocess.TimeoutExpired:
			self.client.log("warning", "[STTProcessing] STT process ignored STOP - "
									   "terminating.")
		except Exception as ex:
			self.client.log("warning", f"[STTProcessing] Error waiting on STT: {ex}")

		self.kill()