from __future__ import annotations

import re
import random
import os
import sys
import json
import time
import queue
import string
import socket
import subprocess
from pathlib import Path
from threading import Thread, RLock
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


def _has_words(text: str) -> bool:
	"""Whether a phrase contains anything somebody actually said."""
	return bool(re.search(r"[A-Za-z0-9]", str(text or "")))


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

	def keep_waiting(self) -> None:
		"""Restart the idle clock without taking a phrase."""
		try:
			self.__client.TIMEOUTS.start(self.__id)
		except Exception:
			pass

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
		#When the panel last finished speaking. See heard_itself().
		self.spoke_until : float = 0.0
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
		#Anything watching transcripts without taking them - the microphone
		#test page. See add_listener().
		self._listeners : list = []
		self._listener_lock = RLock()
		self.woke_at : float = 0.0
		#When LISTENING was entered by something other than a wake word.
		#See check_wake_timeout().
		self.listening_since : float = 0.0

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
			self.listening_since = 0.0
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
			self.listening_since = 0.0
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
			self.listening_since = 0.0
			self.client.ASSIST_STATUS = "LIVE"

	def routing(self, processed:str):
		match self.route:
			case "wake":
				if not self.woke_with :
					self.detect_wake_words_full(processed)
				else:
					self.start_skill_parse(self.woke_with, processed)

			case "session":
				if self.is_session():
					spoken = self.for_session(processed)
					if spoken is None:
						# Heard, but not a question yet. The wake word on its
						# own is how somebody cuts a reply short before they
						# have decided what to ask - passing it on sends the
						# word "alexa" to be answered.
						self.session.keep_waiting()
						self.client.ASSIST_STATUS = "LISTENING"
					else:
						self.client.log("info", f"[STTProcessing] Routing -> '{spoken}' to {self.route}")
						self.session.put(spoken)
						self.client.ASSIST_STATUS = "LISTENING"
				else:
					self.close_session()
				self.processing = False
		
		self.client.iterate_event_callables("on_assistant_transcribed", processed, True)

	#How long after cutting a reply short the tail of it is still expected.
	#
	#The microphone was recording while the panel spoke, and Whisper only
	#transcribes once it hears silence - so the last thing it was saying
	#arrives just AFTER it was stopped, looking like a question.
	#
	#Short, and spent once. There is exactly one tail to catch, and a window
	#that keeps swallowing means somebody who interrupts and then asks has to
	#ask again - which is the panel ignoring them at the moment they took the
	#trouble to interrupt it.
	INTERRUPT_SETTLE = 0.5

	def for_session(self, processed: str) -> str | None:
		"""
		What a session should be given, or None to keep listening.

		| Heard | Given to the session |
		|---|---|
		| "alexa what about tuesday" | "what about tuesday" |
		| "alexa" | nothing - keep listening |
		| something else, just after an interruption | nothing - it is the tail |
		| something else | as it was said |

		The wake word is stripped rather than passed on: inside a session it is
		not addressing the panel, it is interrupting it, and what follows is
		the question. On its own it is somebody stopping a reply before they
		have decided what to ask.
		"""
		phrase = str(processed or "").strip()
		if not _has_words(phrase):
			return None

		try:
			words = [w[0] for w in self.client.SKILLS.wake_args] or list(self.wake_words)
		except Exception:
			words = list(self.wake_words)

		woke = False
		for wake in words:
			if wake and self.find_wake(phrase, wake):
				phrase = self.strip_wake(phrase, wake).strip()
				woke = True
				break

		if woke:
			# This is the phrase that did the interrupting. Whatever follows
			# the wake word in it is the question, and holding that back for
			# the settle would drop the thing somebody just said.
			#
			# Punctuation is not a question: "Alexa!" leaves an exclamation
			# mark behind, which is not empty and would be asked of the model.
			return phrase if _has_words(phrase) else None

		since = time.time() - getattr(self, "interrupted_at", 0.0)
		if since < self.interrupt_settle():
			# Spent, so only the first phrase after an interruption is
			# treated as the tail. Everything after it is somebody talking.
			self.interrupted_at = 0.0
			self.client.log("debug",
				f"[STTProcessing] Holding '{phrase}' - the panel had just been "
				f"stopped and this is its tail.")
			return None
		return phrase if _has_words(phrase) else None

	def words_to_numbers(self, text):
		# Kept as a method because plugins and mixins may target it. The old
		# implementation had \s inside its alternation, so " one " matched whole
		# and collapsed to "1" - "for one minute" became "for1minute", a single
		# token no skill pattern could match.
		return normalize.words_to_numbers(text)

	#How long after the panel stops talking a transcript is still treated as
	#its own voice.
	#
	#Checking is_speaking() alone is not enough: the microphone captures while
	#the panel talks, but Whisper only transcribes once it hears silence - so
	#the text arrives AFTER the speech has finished, by which point
	#is_speaking() is false and the panel answers itself. The AI answer panel
	#is the worst case, since it holds a session open and reads a long reply
	#aloud into an open microphone.
	SELF_HEARING_GRACE = 2.5

	def heard_itself(self) -> bool:
		"""Whether this transcript was captured while the panel was talking."""
		tts = getattr(self.client, "TTS", None)
		if tts is None:
			# No voice backend at all - there is nothing to have overheard.
			return False
		try:
			if tts.is_speaking():
				return True
		except Exception:
			return False
		return (time.time() - getattr(self, "spoke_until", 0.0)
				) < self.self_hearing_grace()

	def interrupt_for_wake(self, transcribed: str) -> bool:
		"""
		Whether the wake word was heard, and anything playing was stopped.

		Answers on the WAKE WORD, not on whether there was something to stop.
		Returning "stopped" instead meant the two-and-a-half second grace after
		a reply ate the wake word entirely: nothing was playing by then, so
		there was nothing to stop, so it was dropped as the panel hearing
		itself - which is the exact window somebody talks in, because they are
		answering what it just said.

		The panel never says its own wake word, so hearing one is never it
		hearing itself.

		Only the wake word counts. Anything else during a reply is the panel,
		and treating a stray phrase as an interruption would let one talk
		itself out of its own sentence.
		"""
		try:
			words = [w[0] for w in self.client.SKILLS.wake_args] or list(self.wake_words)
		except Exception:
			words = list(self.wake_words)

		spoken = normalize.normalize(transcribed) if transcribed else ""
		if not spoken or not any(self.find_wake(spoken, w) for w in words if w):
			return False

		tts = getattr(self.client, "TTS", None)
		stopped = False
		try:
			if tts is not None and hasattr(tts, "stop"):
				stopped = bool(tts.stop())
		except Exception as e:
			self.client.log("warning", f"[STTProcessing] Could not stop speech: {e}")

		self.client.log("info",
			f"[STTProcessing] Heard '{transcribed}' over the panel"
			+ (" and stopped it." if stopped else "."))
		# When the words are allowed to start piling up again.
		self.interrupted_at = time.time()
		# Whatever was still coming is not coming now, so the grace would only
		# suppress the next real thing said.
		self.spoke_until = 0.0
		# Said immediately rather than waiting for the wake pipeline: this is
		# the moment somebody is looking for a sign they were heard.
		self.client.ASSIST_STATUS = "LISTENING"
		return True

	def note_speech_ended(self) -> None:
		"""Called when the panel finishes a spoken reply."""
		self.spoke_until = time.time()

	def submit(self, phrase: str) -> bool:
		"""
		Handle a phrase that was typed or sent, not heard.

		Separate from `pre_processing`, which is the microphone's path and
		expects a wake word: a request arriving over the API has already said
		who it is talking to by arriving at all. Sent through wake detection
		it matched no wake word, so `found_skill` stayed False and it was
		dropped in silence - the pill showed the query and then nothing
		happened, which is what "/process did nothing" looks like.

		A session still takes it, because a conversation waiting on an answer
		should get one however it was sent.

		Returns whether anything was going to act on it.
		"""
		phrase = str(phrase or "").strip()
		if not phrase:
			return False

		if self.is_session():
			self.pre_processing(phrase)
			return True

		if self.processing:
			self.client.log("debug",
				f"[STTProcessing] Busy - ignored '{phrase}'.")
			return False

		self.processing = True
		self.client.ASSIST_STATUS = "THINKING"
		try:
			processed = normalize.normalize(phrase)

			# A wake word is allowed but not required. Somebody sending
			# "alexa play something" means the same as "play something", and
			# leaving it in hands the skill parser a word that is not part of
			# the request.
			try:
				words = [w[0] for w in self.client.SKILLS.wake_args] or list(self.wake_words)
			except Exception:
				words = list(self.wake_words)
			for wake in words:
				if wake and self.find_wake(processed, wake):
					stripped = self.strip_wake(processed, wake).strip()
					if stripped:
						processed = stripped
					break

			self.client.log("info",
				f"[STTProcessing] Submitted -> '{processed}'")
			Thread(target=self.process_phrase,
			       args=[self.clean_text(processed)],
			       daemon=True).start()
			return True
		except Exception as exc:
			self.processing = False
			self.listening_since = 0.0
			self.client.ASSIST_STATUS = "LIVE"
			self.client.log("warning",
				f"[STTProcessing] Could not handle '{phrase}': {exc}")
			return False

	def pre_processing(self, transcribed:str):
		if self.heard_itself():
			# The wake word gets through anyway, and stops the talking.
			#
			# Refusing everything while the panel speaks means waiting out a
			# whole reply before the next question can be asked, and no way to
			# cut one short by voice at all. A wake word is not something the
			# microphone picks up by accident from a room, and hearing it
			# clearly means somebody wants to say something now.
			if self.interrupt_for_wake(transcribed):
				pass
			else:
				self.client.log("debug",
					f"[STTProcessing] Ignored '{transcribed}' - the panel was "
					f"talking.")
				return

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

		# What the transcriber added to the end of a real phrase comes off
		# before anything reads it. It appends its habits to a question asked
		# with a pause after it, and the invented half was being asked of the
		# model along with the real one.
		trimmed = normalize.strip_hallucination(transcribed)
		if trimmed != transcribed:
			self.client.log("debug",
				f"[STTProcessing] Trimmed '{transcribed}' -> '{trimmed}'")
		if not trimmed:
			return
		transcribed = trimmed

		if self.processing:
			# Something is already being dealt with. Said rather than dropped
			# in silence: /process spawns a thread per call, so two requests
			# close together land here and the second simply vanished.
			self.client.log("debug",
				f"[STTProcessing] Busy - ignored '{transcribed}'.")
			return

		self.processing = True
		self.client.ASSIST_STATUS = "THINKING"
		try:
			processed = normalize.normalize(transcribed)
			if processed != transcribed:
				self.client.log("debug",
					f"[STTProcessing] Normalised '{transcribed}' -> '{processed}'")
			self.routing( processed )
		except Exception as exc:
			# `processing` is the gate on everything else being heard, and it
			# was cleared only by the paths that succeeded. A raise anywhere
			# under routing() left it set for good, and the panel went deaf
			# with the pill still up.
			self.processing = False
			self.listening_since = 0.0
			self.client.ASSIST_STATUS = "LIVE"
			self.client.log("warning",
				f"[STTProcessing] Could not handle '{transcribed}': {exc}")


	#What it says when it starts listening. Picked at random so a panel that
	#restarts twice does not say the same thing twice.
	#
	#Full sentences rather than two words. Speech needs a moment to be
	#recognised as speech - a room reacts to "I'm awake" after it has already
	#finished - and a phrase this short gives somebody nothing to catch. These
	#run two or three seconds, which is long enough to be heard from the next
	#room and short enough not to be in the way.
	GREETINGS = (
		"Hello. I'm up and listening whenever you need me.",
		"Good to be back. I'm listening now.",
		"I'm awake and ready when you are.",
		"Hello there. The microphone is on and I'm listening.",
		"I'm here and listening, whenever you want something.",
		"Back up and running. Just say the word.",
	)

	def greet(self) -> None:
		"""
		Say hello now that the microphone is up.

		A notification either way - somebody who missed it wants to know the
		assistant came back - and spoken only if asked for. A panel that
		restarts itself at four in the morning should not announce it to the
		room, so the speech is off by default while the notification is not.
		"""
		wake = ""
		try:
			wake = str(self.client.SKILLS.wake_args[0][0] or "").strip().title()
		except Exception:
			wake = ""

		greeting = random.choice(self.GREETINGS)
		spoken = f"{greeting} Say {wake} when you need me." if wake else greeting

		self.client.simple_notify(
			"assistant",
			"Assistant",
			spoken,
			False,
		)

		try:
			if bool(self.client.setting("assistant.greet_on_start.value", False)):
				self.client.say(greeting)
		except Exception as e:
			self.client.log("debug", f"[STTProcessing] Could not greet: {e}")

	def add_listener(self, callback) -> None:
		"""
		Watch every transcript, without taking it.

		For the microphone test page, and anything else that wants to see
		what was heard rather than act on it. A listener does not consume the
		phrase - routing happens exactly as it would have.
		"""
		with self._listener_lock:
			if callback not in self._listeners:
				self._listeners.append(callback)

	def remove_listener(self, callback) -> None:
		with self._listener_lock:
			if callback in self._listeners:
				self._listeners.remove(callback)

	def _tell_listeners(self, phrase: str) -> None:
		"""
		Hand a transcript to anything watching.

		Each in its own try. A listener that raises is a bug in the listener,
		and it must not take the transcript down with it - the phrase still
		has a panel to reach.
		"""
		with self._listener_lock:
			watching = list(self._listeners)
		for callback in watching:
			try:
				callback(phrase)
			except Exception as e:
				self.client.log("warning",
					f"[STTProcessing] A transcript listener failed: {e}")

	def mic_processing(self) -> str:
		"""Whether the microphone cleans its own audio."""
		try:
			mode = str(self.client.setting(
				"assistant.mic_processing.value", "software")).strip().lower()
		except Exception:
			mode = "software"
		return "hardware" if mode == "hardware" else "software"

	#What a microphone array that does its own work changes on this side.
	#
	#An XVF3800 and its like have already run AEC, noise suppression, AGC and
	#VAD before the audio arrives. Running the same things again is not
	#neutral: a second noise pass on already-clean speech is what makes it
	#sound underwater, and the self-hearing guards exist to work around an
	#echo the hardware has already cancelled.
	#
	#Shorter rather than zero. The hardware cancels what it can hear through
	#its own reference; a speaker not wired through the array is still an
	#echo it knows nothing about, so a little caution is kept.
	HARDWARE_GRACE = 0.6
	HARDWARE_SETTLE = 0.2

	def self_hearing_grace(self) -> float:
		"""How long after speaking the panel still expects to hear itself."""
		if self.mic_processing() == "hardware":
			return self.HARDWARE_GRACE
		return self.SELF_HEARING_GRACE

	def interrupt_settle(self) -> float:
		"""How long after an interruption the tail is still expected."""
		if self.mic_processing() == "hardware":
			return self.HARDWARE_SETTLE
		return self.INTERRUPT_SETTLE

	def wake_timeout_seconds(self) -> float:
		"""How long to stay listening after a wake with nothing said."""
		try:
			return max(3.0, float(self.client.setting(
				"assistant.wake_listen_timeout.value", 12)))
		except Exception:
			return 12.0

	def note_not_listening(self) -> None:
		"""Drop the listening anchor. Called whenever the status leaves it."""
		self.listening_since = 0.0

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
		if self.client.ASSIST_STATUS not in ("LISTENING",):
			return

		# Anchored on entering LISTENING, not on waking.
		#
		# `woke_at` is set by the wake word and by nothing else, so every
		# other way of reaching LISTENING - a session taking a phrase, a
		# /process call while one is open - produced a pill this could not
		# see, because it returned early on `not self.woke_at`. It stayed up
		# until something happened to say otherwise, which in a session is
		# minutes.
		# Stamped when this pass first SEES it listening, and dropped the
		# moment it is not. Keeping an anchor across a stand-down would time
		# out the next listening state the instant it began.
		if not self.woke_at and not self.listening_since:
			self.listening_since = time.time()
			return
		since = self.woke_at or self.listening_since
		if time.time() - since < self.wake_timeout_seconds():
			return

		# A session used to disable this entirely, so a wake that arrived with
		# nothing behind it - a check that finished after its own phrase had
		# already been answered - left the pill reading "listening" for as
		# long as the conversation stayed open, which is minutes.
		#
		# The session is left alone; only the wake state is stood down. It is
		# still listening for the conversation, and saying so was the lie.
		if self.is_session():
			self.client.log("info",
				"[STTProcessing] Stale wake during a session - standing down "
				"the wake state, session left open.")
			self.woke_at = 0.0
			self.listening_since = 0.0
			self.woke_with = None
			self.client.ASSIST_VOICE_ACTIVITY_LEVEL = 0.0
			return

		self.client.log("info",
			"[STTProcessing] Listening timed out with nothing said - standing down.")
		self.woke_at = 0.0
		self.listening_since = 0.0
		self.woke_with = None
		self.processing = False
		self.listening_since = 0.0
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
		self.listening_since = 0.0
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
		self.listening_since = 0.0
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
										self.listening_since = 0.0
										self.client.ASSIST_STATUS = "LIVE"
										self.greet()
								case "log":
									# The child process talking. It has no
									# logger of its own - it is a different
									# process - so its messages arrive here
									# and go out through the panel's.
									level, _, body = data.partition(":")
									if body:
										self.client.log(
											level.strip() or "debug",
											f"[Whisper] {body}")

								case "transcribe":
									# Anything watching gets it raw, before
									# normalising, wake matching or routing.
									# That is the point of watching: to tell
									# "the microphone heard nothing" apart
									# from "the wake word did not match".
									self._tell_listeners(data)
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
									self.listening_since = 0.0
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
				self.listening_since = 0.0
				self.client.ASSIST_STATUS = "LIVE"
				self.client.simple_notify("assistant", "Assistant", "Microphone reconnected.")
			return

		if message == self.last_error:
			return
		self.last_error = message

		self.listening_since = 0.0
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
				# What the microphone does for itself - see mic_profile().
				"mic_processing": self.mic_processing(),
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