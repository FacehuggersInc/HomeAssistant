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
from src.constants import LOG_DIR
from src.registries.service_registry import Restart

if TYPE_CHECKING:
	from src.main import Client

SENTENCE_END_TOKENS = {'.', '!', '?', ';'}

_HERE = Path(__file__).resolve().parent
PROCESS_WHISPER     = str(_HERE / "whisper-process.py")
PROCESS_PARAKEET    = str(_HERE / "parakeet-process.py")

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
		self.__client.SERVICES.STT.open_session()
		self.__client.TIMEOUTS.add(60 * 5, self.timed_out, self.__id,
		                           transient=True)
		self.__client.TIMEOUTS.start(self.__id)
		return self

	def __exit__(self, exc_type, exc_val, exc_tb):
		self.is_open = False
		self.__client.SERVICES.STT.close_session()
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
		self.__client.SERVICES.STT.processing = False


class STTProcessing():

	#What `status()` can say, in the order it prefers. A panel showing one
	#line wants the most specific true thing, not the first one checked.
	STATUS_ORDER = ("error", "processing", "awake", "monitoring", "listening",
					"held", "idle", "stopped")

	#What the two halves are registered as. Named on the class rather than
	#written out at each call site: the receiver is declared as the process's
	#companion, so the two strings have to agree in three places.
	SERVICE = "assistant.stt"
	RECEIVER = "assistant.stt.receiver"

	@property
	def process(self):
		"""
		The child, or None.

		Read-only, and read through the registry rather than held here: the
		registry is what restarts it, so a handle kept alongside would be the
		previous one from the moment it did.
		"""
		try:
			return self.client.SERVICES.process(self.SERVICE)
		except Exception:
			return None

	def __init__(self, client, process:str = "parakeet",
				 input_device=None, input_device_name: str = "",
				 model:str = "tiny.en", wake_words=None,
				 session_silence_ms:int = 800):
		self.client = client
		self.input_device = input_device
		# What the client called it. The child enumerates separately and the
		# two lists have been seen to disagree, so the index is a hint and
		# this is the thing that identifies the microphone.
		self.input_device_name = str(input_device_name or "")
		self.model = model
		self.wake_words = list(wake_words or [])
		# How long a silence ends a phrase once a session is open. Wake mode
		# keeps its own much shorter threshold.
		self.session_silence_ms = int(session_silence_ms)
		self.last_error : str = ""
		#When the panel last finished speaking. See heard_itself().
		self.spoke_until : float = 0.0
		#Whether capture is muted, and since when. Only the panel speaking
		#should hold it, and only for as long as that lasts.
		self.capture_held : bool = False
		self.held_since : float = 0.0
		self.process_type = process
		self.__process_path = None
		match self.process_type:
			case "whisper": self.__process_path = PROCESS_WHISPER
			case "parakeet": self.__process_path = PROCESS_PARAKEET
			case _: self.__process_path = PROCESS_PARAKEET

		#Process & Socket
		#
		# The child and the socket reader are one lifecycle wearing two hats,
		# so they are registered as a service and its companion rather than as
		# a process here and a thread somewhere else. `process` is a read-only
		# view of what the registry holds.
		self.listening = False
		self.host = "127.0.0.1"
		self.ports = {
			"command" : 65432,
			"data" : 65433
		}

		self.processing : bool = False

		#Longest a single message off the socket may be before it is treated
		#as a protocol failure rather than a slow arrival. Transcripts are the
		#big ones and they are a sentence.
		self.MAX_MESSAGE = 64 * 1024

		self.woke_with : str = None
		# When the wake word was heard, so a wake nobody followed up on can be
		# stood down. See check_wake_timeout().
		#Anything watching transcripts without taking them - the microphone
		#test page. See add_listener().
		self._listeners : list = []
		self._listener_lock = RLock()
		#Whether something is watching everything said, with no wake word.
		#See start_monitor().
		self._monitoring : bool = False
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
		if found is not None:
			return found

		# Nothing matched exactly. The wake check reads 150ms of audio with a
		# small model, so an exact spelling is a lot to ask - see
		# find_wake_fuzzy for what that actually returns.
		return STTProcessing.find_wake_fuzzy(text, wake)
	#How wrong a heard word may be and still count as the wake word.
	#
	#0.8 is roughly one wrong letter in five. The wake check transcribes 150ms
	#of audio with a small model, which is barely a syllable - it comes back
	#with "alexis", "elexa", "a lexa", "lexa" for somebody saying the word
	#perfectly clearly. Demanding an exact spelling threw all of those away
	#and the panel looked deaf.
	#
	#Only the wake word is fuzzy. Everything after it is passed on as heard,
	#because a skill's arguments are not a known short list to match against.
	WAKE_RATIO = 0.8

	@classmethod
	def find_wake_fuzzy(cls, text: str, wake: str):
		"""
		The wake word, allowing for a small model mishearing it.

		Tried only after an exact match fails. A word is compared against the
		wake word on its own and joined with its neighbour, because the other
		common failure is one word arriving as two.
		"""
		if not text or not wake:
			return None
		import difflib

		target = wake.lower()
		words = list(re.finditer(r"[A-Za-z']+", text))
		best = None
		best_score = 0.0

		for index, match in enumerate(words):
			candidates = [(match.group(0), match)]
			if index + 1 < len(words):
				# "a lexa" and "alex a" - one word heard as two.
				joined = match.group(0) + words[index + 1].group(0)
				candidates.append((joined, words[index + 1]))

			for word, ending in candidates:
				score = difflib.SequenceMatcher(
					None, word.lower(), target).ratio()
				if score >= cls.WAKE_RATIO and score > best_score:
					best_score = score
					best = ending
		return best

	@classmethod
	def strip_wake(cls, text: str, wake: str) -> str:
		"""Everything after the wake word, or the whole phrase if absent."""
		match = cls.find_wake(text, wake)
		return text[match.end():].strip() if match else text.strip()

	## PROCESSING
	def clean_text(self, text:str) -> str:
		"""
		Punctuation off, except where it is part of a word or a number.

		Stripping every punctuation character turns "11:46" into "1146" and
		"o'clock" into "oclock" - and the Matcher runs on this, so a clock
		time stops being one on its way to the skill that wanted it. A
		trailing full stop is noise; the one in "3.5" is not.
		"""
		said = str(text or "")
		kept = []
		for index, char in enumerate(said):
			if char not in string.punctuation:
				kept.append(char)
				continue
			before = said[index - 1] if index else ""
			after = said[index + 1] if index + 1 < len(said) else ""
			# Inside a number: a clock time, or a decimal.
			if char in ":." and before.isdigit() and after.isdigit():
				kept.append(char)
				continue
			# Inside a word: "o'clock", "don't".
			if char == "'" and before.isalpha() and after.isalpha():
				kept.append(char)
				continue
		return "".join(kept).strip()

	def process_phrase(self, phrase:str):
		# Said before the search, not after it.
		#
		# Parsing is fast when it matches and no slower when it does not, so
		# without this the only feedback is the answer - and a phrase that
		# matches nothing produces no feedback at all. Somebody who was
		# misheard then cannot tell whether the microphone failed or the
		# skill did.
		self.client.iterate_event_callables("on_heard_assistant", phrase)

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
		tts = self.client.SERVICES.TTS
		if not tts.available:
			# No voice backend at all - there is nothing to have overheard.
			return False
		if tts.is_speaking():
			return True
		return (time.time() - getattr(self, "spoke_until", 0.0)
				) < self.self_hearing_grace()

	#How long after the panel finishes speaking a transcript can still be the
	#tail of what it said. Longer than the self-hearing grace: that one covers
	#audio captured DURING speech, and this covers a phrase finalised after
	#it, which on a long reply read into a live microphone is most of them.
	#
	#Measured from when speech ENDED. Measured from when it started, an AI
	#reply that takes forty seconds to read has used the whole window before
	#the last phrase is even finalised - and the last phrase is the one that
	#gets through, because everything before it was still being spoken and
	#`heard_itself()` had already caught it.
	ECHO_WINDOW = 20.0

	#How close a transcript has to be to what was said. Not 1.0 - it has been
	#through a microphone, a room and a transcriber, so "the kitchen lights
	#are off" comes back as "kitchen lights are off" or worse.
	#
	#Measured on WORDS, not characters. On characters "turn the bedroom
	#lights off" scores 0.87 against "turned the kitchen lights off" - almost
	#every letter matches - and a request to change a different room reads as
	#the panel repeating itself.
	ECHO_RATIO = 0.8

	#Below this, a phrase is too short to attribute. "yes", "stop" and "no"
	#appear in almost any reply, and they are also the things somebody says.
	ECHO_MIN_WORDS = 3

	def echoed(self, transcribed: str) -> bool:
		"""
		Whether this transcript is the panel hearing its own last reply.

		`heard_itself()` answers "was the panel talking", which covers the
		common case and not the expensive one: a session holds the microphone
		open while a long reply is read into it, and each fragment is
		finalised on a pause and transcribed AFTER speech ends. Every one of
		those is a phrase the session asks the model about, and the answer is
		spoken, and it goes round again.

		So this asks a different question - "is this what I just said" - by
		matching the transcript against the text handed to the TTS.
		"""
		recent = self.client.recent_spoken()
		if not recent:
			return False

		# The panel never says its own wake word, so a transcript carrying one
		# is never the panel. It is also the way out of the one case this
		# guard gets wrong: the reply suggests something, the person asks for
		# exactly that, and the words match because they were the panel's
		# words first. Saying the wake word first always gets through.
		try:
			words_for = [w[0] for w in self.client.SKILLS.wake_args] or list(self.wake_words)
		except Exception:
			words_for = list(self.wake_words)
		for wake in words_for:
			if wake and self.find_wake(transcribed, wake):
				return False

		speaking = False
		try:
			speaking = self.client.SERVICES.TTS.is_speaking()
		except Exception:
			speaking = False

		heard = normalize.flatten(transcribed)
		words = heard.split()
		if len(words) < self.ECHO_MIN_WORDS:
			return False

		import difflib
		ended = getattr(self, "spoke_until", 0.0)
		now = time.time()
		best = 0.0

		for said, when in recent:
			# From whichever is later: the moment it started saying this, or
			# the moment it last stopped saying anything.
			since = max(when, ended)
			if not speaking and (now - since) > self.ECHO_WINDOW:
				continue

			spoken = normalize.flatten(said)
			if not spoken:
				continue
			if heard in spoken:
				return True

			# A fragment, against the same number of words anywhere in the
			# reply. Comparing against the WHOLE reply would score almost
			# nothing: six words out of two hundred is a low ratio however
			# exact they are.
			reply = spoken.split()
			width = len(words)
			for start in range(0, max(1, len(reply) - width + 1)):
				window = reply[start:start + width]
				best = max(best, difflib.SequenceMatcher(
					None, words, window).ratio())
				if best >= self.ECHO_RATIO:
					return True

		# Said out loud when it was close. A loop that gets through is a panel
		# talking to itself, and the number that decides it should not have to
		# be guessed at from the outside.
		if best >= 0.5:
			self.client.log("debug",
				f"[STTProcessing] '{transcribed}' scored {best:.2f} against "
				f"what the panel said (needs {self.ECHO_RATIO}).")
		return False

	def is_cancel_word(self, transcribed: str) -> bool:
		"""
		Whether this transcript is nothing but a registered cancel phrase.

		The WHOLE phrase, matched against what `client.CANCEL` answers to -
		not a word found somewhere inside one. A reply mentioning "stop" in
		passing is the panel; a transcript that is only "stop" is somebody
		saying it, because the panel does not speak in single words.

		Content, where `heard_itself()` is a clock. That is the point: the
		clock cannot tell a person from an echo, and this can - a cancel word
		on its own is never a fragment of prose the panel just read.
		"""
		phrase = " ".join(str(transcribed or "").lower().split())
		phrase = phrase.strip(" .,!?;:")
		if not phrase:
			return False
		try:
			return phrase in {" ".join(str(word).lower().split())
							  for word in self.client.CANCEL.keywords()}
		except Exception:
			return False

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

		stopped = self.client.SERVICES.TTS.stop_speaking()

		self.client.log("info",
			f"[STTProcessing] Heard '{transcribed}' over the panel"
			+ (" and stopped it." if stopped else "."))
		# When the words are allowed to start piling up again.
		self.interrupted_at = time.time()
		# Whatever was still coming is not coming now, so the grace would only
		# suppress the next real thing said.
		self.spoke_until = 0.0
		# Only when something was actually stopped. With nothing playing
		# there is no unwinding callback to defend against, and the flag
		# would sit there and swallow the grace after the NEXT reply.
		if stopped:
			self.note_interrupted()
		# Said immediately rather than waiting for the wake pipeline: this is
		# the moment somebody is looking for a sign they were heard.
		self.client.ASSIST_STATUS = "LISTENING"
		return True

	def hold_capture(self, held: bool) -> None:
		"""
		Stop the child capturing phrases while the panel is speaking.

		The reliable half of not answering itself. `echoed()` compares text
		and can be argued with - a reply the TTS cut short, a transcriber
		that heard it differently - but audio that is never captured cannot
		come back as a question at all.

		The spotter keeps running in the child, so the wake word still
		interrupts. Best effort: a command that does not arrive leaves the
		text guards, which is where this started.
		"""
		if self.process is None or not self.listening:
			return
		self.capture_held = bool(held)
		self.held_since = time.time() if held else 0.0
		try:
			self.send_command("MUTE" if held else "UNMUTE", retries=2)
		except Exception as e:
			self.client.log("debug",
				f"[STTProcessing] Could not {'hold' if held else 'resume'} "
				f"capture: {e}")

	def wake_interrupts_speech(self) -> None:
		"""
		The spotter fired while the panel was talking. Stop it and listen.

		The missing half of "the spotter keeps running, so the wake word still
		interrupts". It kept running and it did fire - the message arrived and
		set the panel to LISTENING - but nothing acted on it: the reply carried
		on, and capture stayed muted, so the question that followed the wake
		word was never recorded. From the outside that is the panel ignoring
		somebody, and no amount of sensitivity fixes it, because the detector
		was never the part that was failing.

		The other interrupt path works off a TRANSCRIPT, which cannot happen
		here: capture is muted while speaking, so there is nothing to
		transcribe. The spotter is the only thing that can hear anything in
		this window, so it has to be the thing that acts.

		Only once the reply is audible - see below.
		"""
		# AUDIBLE, not merely speaking.
		#
		# A reply spends a second or two being synthesised before any sound
		# exists, and `is_speaking()` counts that - correctly, for everything
		# else that asks. Here it is wrong: a wake word arriving during
		# generation arrived into silence, so it is the room rather than
		# somebody talking over an answer. Acting on it dropped the answer
		# before it was ever heard, and a fan produces enough of them to
		# swallow every reply in a row - the question routed, the skill ran,
		# and nothing came out.
		audible = False
		try:
			audible = self.client.SERVICES.TTS.is_audible()
		except Exception:
			audible = False
		if not audible:
			return

		try:
			self.client.SERVICES.TTS.stop_speaking()
		except Exception as e:
			self.client.log("warning",
				f"[STTProcessing] Could not stop speech on wake: {e}")

		# Marked as an interruption, so the settle window treats whatever
		# arrives next as the question rather than as the tail of the reply
		# that was just cut off.
		self.interrupted_at = time.time()
		# Cleared, NOT stamped. `heard_itself()` treats anything within the
		# grace of this as the panel overhearing its own voice, and the
		# sentence somebody is saying right now arrives inside that window -
		# so stamping it here threw away the question that the wake word was
		# said in order to ask. The reply was stopped mid-word: whatever else
		# was coming is not coming, and there is nothing left to overhear.
		# `interrupt_for_wake` clears it for the same reason.
		self.spoke_until = 0.0
		# And the clear is defended. `tts.stop()` above returns before the
		# playback thread has noticed, so that thread calls
		# note_speech_ended() a moment from now and would stamp the grace
		# right back over this line.
		self.note_interrupted()

		# And listening again. Muted was correct while it was talking; it is
		# not talking any more, and the sentence after the wake word is
		# already being said.
		self.hold_capture(False)
		self.client.log("info",
			"[STTProcessing] Wake word heard over a reply - stopped it and "
			"reopened the microphone.")

	#How long a stop takes to unwind. `tts.stop()` only ASKS the playback
	#thread to break: it finishes the buffer it is holding, waits out the
	#tail and then calls note_speech_ended(). Bounded so a flag can never
	#outlive the callback it was set for and suppress a later, real one.
	INTERRUPT_UNWIND = 4.0

	def note_interrupted(self) -> None:
		"""The speech about to end was cut off rather than finished."""
		self._interrupted_speech = time.time()

	def note_speech_ended(self) -> None:
		"""
		Called when the panel finishes a spoken reply.

		**Not after an interruption.** The grace exists to cover the panel
		overhearing the tail of its own voice, and a reply cut off mid-word
		has no tail - what is in the room instead is the person who
		interrupted it, mid-sentence, saying the thing the wake word was said
		in order to ask.

		Both interrupt paths clear `spoke_until` themselves, and it did not
		help: `tts.stop()` returns before the playback thread has noticed, so
		that thread arrives HERE a moment later and stamps the grace straight
		back over the clear. From the log that is a wake word interrupting a
		reply correctly, the microphone reopening correctly, and the next
		question ignored as an echo two seconds afterwards.
		"""
		cut = getattr(self, "_interrupted_speech", 0.0)
		self._interrupted_speech = 0.0
		if cut and time.time() - cut < self.INTERRUPT_UNWIND:
			self.spoke_until = 0.0
			return
		self.spoke_until = time.time()
		self.hold_capture(False)

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
			elif self.is_cancel_word(transcribed):
				# A bare cancel word gets through too, for the same reason
				# the wake word does: this is precisely the window somebody
				# says it in.
				#
				# The guard is a clock, not a comparison - anything within a
				# couple of seconds of the panel finishing is treated as its
				# tail. "Stop", said the moment a long reply ends, lands
				# squarely inside it and was dropped, so the panel had to be
				# woken again before it would hear the word that means stop.
				self.client.log("debug",
					f"[STTProcessing] '{transcribed}' is a cancel word - "
					f"letting it through the self-hearing guard.")
			else:
				# Info, not debug. From outside this looks like the panel
				# hearing somebody and then doing nothing, which is the
				# hardest kind of failure to report - the log is the only
				# place it is visible at all.
				self.client.log("info",
					f"[STTProcessing] Ignored '{transcribed}' - the panel was "
					f"talking.")
				return

		# The panel hearing its own reply come back.
		#
		# Checked AFTER the wake word, so somebody interrupting mid-reply
		# still gets through, and before anything acts on the phrase. Without
		# it a session reads a long answer into a live microphone, transcribes
		# the fragments, asks the model about them, and speaks the answer -
		# which is the loop.
		if self.echoed(transcribed):
			self.client.log("debug",
				f"[STTProcessing] Ignored '{transcribed}' - the panel said it.")
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

	#For a panel whose microphone is muted at the mixer.
	#
	#Every greeting above promises to be listening, which is exactly wrong
	#here: the panel starts, says it is listening, and then ignores the wake
	#word - and nothing about that says the microphone is the reason. Somebody
	#stands there repeating the word at a device that told them it was ready.
	MUTED_GREETINGS = (
		"Hello. I'm up, but the microphone is muted so I can't hear you.",
		"I'm back. The microphone is muted at the moment, so I'm not listening.",
		"Hello there. I'm running, but my microphone is muted.",
		"I'm here, though the microphone is muted so nothing reaches me.",
	)

	def greet(self) -> None:
		"""
		Say hello now that the microphone is up.

		A notification either way - somebody who missed it wants to know the
		assistant came back - and spoken only if asked for. A panel that
		restarts itself at four in the morning should not announce it to the
		room, so the speech is off by default while the notification is not.
		"""
		# The configured wake word, not the first skill's.
		#
		# `SKILLS.wake_args` is every skill that declares one, in load order,
		# so `[0][0]` was whichever plugin happened to register first - the
		# calendar, the timer, whatever. The panel then greeted somebody by
		# telling them to say a word that is not the one it is listening for.
		#
		# `client.wake_word` is the accessor, and its own docstring says to
		# use it rather than reaching for something else.
		try:
			wake = str(self.client.wake_word or "").strip().title()
		except Exception:
			wake = ""

		# Asked at the mixer rather than assumed. A greeting that promises to
		# be listening while the microphone is muted is worse than no greeting
		# at all: the wake word does nothing afterwards, and nothing on the
		# panel connects the two.
		muted = False
		try:
			muted = bool(self.client.mic_muted())
		except Exception as e:
			self.client.log("debug",
				f"[STTProcessing] Could not read the microphone: {e}")

		if muted:
			greeting = random.choice(self.MUTED_GREETINGS)
			# No "say the wake word" here. Telling somebody to say it while
			# nothing can hear them is the whole problem being described.
			spoken = f"{greeting} Unmute it and I'll be listening."
		else:
			greeting = random.choice(self.GREETINGS)
			spoken = (f"{greeting} Say {wake} when you need me."
					  if wake else greeting)

		self.client.simple_notify(
			"assistant",
			"Assistant",
			spoken,
			False,
		)

		try:
			if bool(self.client.setting("assistant.feedback.greet_on_start.value", False)):
				# The full sentence when muted, not just the opening. What
				# matters is the part explaining why the wake word will not
				# work, and that is in the second half.
				self.client.say(spoken if muted else greeting)
		except Exception as e:
			self.client.log("debug", f"[STTProcessing] Could not greet: {e}")

	def start_monitor(self) -> bool:
		"""
		Transcribe everything, without a wake word and without routing.

		For the microphone test page. Passthrough is the same mode a
		conversation uses - the child stops waiting to be woken and finalises
		on silence - but nothing is put into a session, so the phrases reach
		the listeners and go no further.

		Returns whether the microphone was actually opened.
		"""
		if self._monitoring:
			return True
		if not self.listening or self.process is None:
			return False
		try:
			self.send_command("START_PASSTHROUGH")
		except Exception as e:
			self.client.log("warning",
				f"[STTProcessing] Could not start monitoring: {e}")
			return False
		self._monitoring = True
		self.client.log("info", "[STTProcessing] Monitoring - no wake word.")
		return True

	def stop_monitor(self) -> None:
		"""Back to waiting for the wake word."""
		if not self._monitoring:
			return
		self._monitoring = False
		try:
			self.send_command("START_WAKE")
		except Exception as e:
			self.client.log("warning",
				f"[STTProcessing] Could not stop monitoring: {e}")
		self.client.log("info", "[STTProcessing] Monitoring ended.")

	def status(self) -> dict:
		"""
		What this is doing right now, for something that wants to show it.

		One dict rather than a handful of attributes, because a panel reading
		`listening`, `processing`, `woke_at` and `process.poll()` separately
		gets a different answer for each depending on when it asked.

		`state` is one word from STATUS_ORDER. `since` is when that state
		started, or 0 - a listener stuck open for four minutes is the thing
		worth seeing, and the state alone does not say it.
		"""
		import time as _time

		running = self.client.SERVICES.is_active(self.SERVICE)
		if not running:
			state, since = "stopped", 0.0
		elif self.last_error:
			state, since = "error", 0.0
		elif self.processing:
			state, since = "processing", 0.0
		elif self.woke_at:
			# Woken and waiting for the rest of the sentence.
			state, since = "awake", self.woke_at
		elif self._monitoring:
			state, since = "monitoring", self.listening_since
		elif self.capture_held:
			# The panel is speaking and is not listening to itself.
			state, since = "held", self.held_since
		elif self.listening:
			state, since = "listening", self.listening_since
		else:
			state, since = "idle", 0.0

		return {
			"state": state,
			"since": float(since or 0.0),
			"for": max(0.0, _time.time() - since) if since else 0.0,
			"running": running,
			"pid": self.client.SERVICES.pid(self.SERVICE) if running else None,
			"engine": self.process_type,
			"model": self.model,
			"error": self.last_error or "",
			# What is watching without taking. A page left open holds one of
			# these, and a count that does not go back down is the tell.
			"listeners": len(self._listeners),
			"woke_with": self.woke_with or "",
		}

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

	#What the wake word is checked with. Small on purpose: it answers one
	#question, on every 150ms of speech, and a model that mishears an ordinary
	#word costs nothing because only the wake word is being looked for.
	WAKE_MODEL = "tiny.en"

	def mic_processing(self) -> str:
		"""Whether the microphone cleans its own audio."""
		try:
			mode = str(self.client.setting(
				"audio.devices.mic_processing.value", "software")).strip().lower()
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
				"assistant.wake.wake_listen_timeout.value", 12)))
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

		# Nothing said, or nothing heard?
		#
		# Capture muted and never released looks exactly like silence: the
		# spotter still runs, so the wake word gets through and the panel
		# opens its window, and then every phrase in that window transcribes
		# to nothing. From the outside somebody is talking to a panel that
		# lights up and does nothing, over and over.
		#
		# Released here rather than waited out. The child lifts its own mute
		# after two minutes as a failsafe, which is far too long to be sat in
		# front of, and the hold means "while the panel is speaking" - the
		# panel is not speaking, or this window would not have opened.
		if getattr(self, "capture_held", False):
			held_for = time.time() - getattr(self, "held_since", 0.0)
			self.client.log("warning",
				f"[STTProcessing] Capture was still muted after "
				f"{held_for:.0f}s - releasing it. Nothing said in that window "
				f"could have been heard.")
			self.hold_capture(False)

		self.client.log("info",
			"[STTProcessing] Listening timed out with nothing said - standing down.")
		self.woke_at = 0.0
		self.listening_since = 0.0
		self.woke_with = None
		self.processing = False
		self.listening_since = 0.0
		self.client.ASSIST_STATUS = "LIVE"
		self.client.ASSIST_VOICE_ACTIVITY_LEVEL = 0.0

		# And the child is TOLD, rather than assumed to have agreed.
		#
		# This used to clear the panel's own state and nothing else, so a
		# child still armed carried on capturing - and every capture it
		# could not transcribe announced `transcribing`, drove the pill to
		# THINKING and let it fade again. With the panel stood down there is
		# no wake word in any of it, so from the front it is a light
		# flashing on its own, indefinitely, and saying the wake word does
		# not help because nothing is waiting for one.
		#
		# A session never reaches here - that case returned above - so this
		# cannot close a conversation. START_WAKE is idempotent: it resets
		# the child to waiting for the word and bumps the generation, so
		# anything already captured is dropped rather than announced.
		#
		# Few retries. This runs on the update thread, and ten of them at
		# half a second each is five seconds of the tick loop spent on a
		# child that is not answering anyway.
		self.send_command("START_WAKE", retries=2)

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
					#
					# Buffered, and split on newlines.
					#
					# `recv` hands back whatever bytes have arrived, not one
					# message: two sendall calls a moment apart come back in
					# ONE read, and the old `raw.split(":", 2)` then read the
					# first message and swallowed the second into its payload.
					#
					# `host:transcribe:...` immediately followed by
					# `host:transcribed:1` arrives as one string, so the
					# transcript was acted on and the panel was never told the
					# model had finished - it sat at THINKING forever. The
					# same read also ate `transcribing` off the end of the
					# voice_activity stream, which is every 30ms while
					# somebody is speaking.
					#
					# It was survivable with whisper, where seconds in the
					# model spaced the messages out by accident. It is not
					# with a transcriber that answers in 300ms.
					buffer = ""
					while self.listening:
						chunk = sock.recv(1024 * 5).decode("utf-8")
						if not chunk:
							break
						buffer += chunk

						# A message that never terminates would otherwise grow
						# this without limit. Dropped loudly rather than
						# silently: it means the two sides disagree about the
						# protocol, which is worth knowing.
						if len(buffer) > self.MAX_MESSAGE and "\n" not in buffer:
							self.client.log("warning",
								"[STTProcessing] Dropped an oversized message "
								"from the speech process.")
							buffer = ""
							continue

						while "\n" in buffer:
							raw, _, buffer = buffer.partition("\n")
							raw = raw.strip()
							if not raw:
								continue
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
											# No prefix added. The child
											# already names itself, and
											# "[Whisper] [Parakeet]: Ready."
											# is what hard-coding one gets you
											# once there is more than one
											# process.
											self.client.log(
												level.strip() or "debug",
												body)

									case "transcribe":
										# Anything watching gets it raw, before
										# normalising, wake matching or routing.
										# That is the point of watching: to tell
										# "the microphone heard nothing" apart
										# from "the wake word did not match".
										self._tell_listeners(data)
										if self._monitoring:
											# Monitoring is listening without
											# acting. Routing here would run a
											# skill for every sentence said in
											# the room while the test page is
											# open, which is worse than useless.
											continue
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
										self.wake_interrupts_speech()
										# Somebody in the room said something
										# TO the panel, which is an
										# interaction by any definition -
										# they just did not touch it. Without
										# this the idle clock keeps running
										# through the wake, the question and
										# the reply, so waking a panel four
										# seconds into its window and asking
										# it something got an answer onto a
										# screen that was already going dark.
										try:
											self.client.reset_interaction_timeout()
										except Exception:
											pass
										# Before the answer rather than after it.
										# A device that arrived since the last
										# wake brings its own volume with it, and
										# the reply is the thing that needs to be
										# heard. Threaded inside; cheap when the
										# setting is off.
										try:
											self.client.apply_minimum_volume()
										except Exception:
											pass

									case "transcribing":
										# Audio captured, the model is running.
										# On a big model that is seconds, and
										# without this the panel stands down after
										# the wake and says nothing until the text
										# arrives - a pill that fades, then an
										# answer out of nowhere.
										self.client.ASSIST_STATUS = "THINKING"
										self.client.iterate_event_callables(
											"on_transcribing_assistant", None)

									case "transcribed":
										# The model finished, whatever it decided.
										# Five paths in the child end without a
										# transcript - too quiet, an error, a
										# hallucination, a repetition, nothing
										# usable - and without this the panel sat
										# at "thinking" forever on every one of
										# them. Silence is the common case.
										if self.client.ASSIST_STATUS == "THINKING":
											if not self.processing:
												self.listening_since = 0.0
												self.client.ASSIST_STATUS = (
													"LISTENING" if self.woke_with
													else "LIVE")
										self.client.iterate_event_callables(
											"on_transcribed_assistant", None)

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
	def argv(self) -> list:
		"""
		The command line the child is started with.

		A factory rather than a stored list, because everything in it is read
		from settings at the moment of asking - the wake words, the
		sensitivity, the precision. A restart minutes later has to bring back
		the settings as they are now, not as they were when the panel booted.
		"""
		# Every registered skill carries the same wake word, so this is one
		# entry per skill before de-duplication. Deduped once here so the
		# log shows what is actually sent rather than the raw list.
		words = [w[0] for w in self.client.SKILLS.wake_args] or list(self.wake_words)
		words = sorted({w.strip().lower() for w in words if w and w.strip()})
		if not words:
			words = [self.client.wake_word]

		settings = {
			"wake_words":   words,
			"input_device": self.input_device,
			"input_device_name": self.input_device_name,
			# What the PANEL can see. The speech process enumerates for
			# itself, and on at least one machine the two lists differ - a USB
			# array present here and absent there, with every index past it
			# shifted. Sending this over turns that into one line in the
			# report instead of two log files read side by side.
			"panel_inputs": self.panel_inputs(),
			"model":        self.model,
			"session_silence_ms": self.session_silence_ms,
			# What the microphone does for itself - see mic_profile().
			"mic_processing": self.mic_processing(),
			# Which Parakeet weights. The child has to be told: it checks
			# the cache for itself before loading, and int8 and full
			# precision are different files.
			"parakeet_precision": str(self.client.setting(
				"assistant.speech.parakeet_precision.value", "int8") or "int8"),
			# How long the child keeps waiting for a phrase after a wake
			# before standing down on its own.
			"wake_listen_timeout": self.wake_timeout_seconds(),
			# The longest a single phrase may run before it is thrown away.
			# What passes this is a television rather than a question.
			"max_phrase_ms": int(max(2.0, float(self.client.setting(
				"assistant.wake.max_phrase_seconds.value", 8) or 8)) * 1000),
			# Whether every wake is explained in the log - the score, and a
			# transcript of what was being said around it.
			"wake_diagnostics": bool(self.client.setting(
				"assistant.wake.wake_diagnostics.value", False)),
			# The standing record: what the microphone is, how loud the room
			# is, every wake, and every near miss. On by default, because the
			# question it answers is always asked after the fact - and a
			# report somebody has to turn on before the problem happens is a
			# report that is off when it matters.
			"wake_report": bool(self.client.setting(
				"assistant.wake.wake_report.value", True)),
			"wake_report_path": str(LOG_DIR / "wake.log"),
			# How sure the spotter has to be. Read here rather than held,
			# because the child is respawned when it changes.
			"wake_sensitivity": float(self.client.setting(
				"assistant.wake.wake_sensitivity.value", 0.5) or 0.5),
			# And the one used while the panel is talking. 0 means the same.
			"wake_sensitivity_speaking": float(self.client.setting(
				"assistant.wake.wake_sensitivity_speaking.value", 0.0) or 0.0),
			# So the process can notice the client dying without a STOP and
			# leave on its own, instead of surviving as an orphan holding
			# the microphone and both ports.
			"parent_pid":   os.getpid(),
		}

		# The whisper process's own settings, sent only to it.
		#
		# They were sent to whatever was spawned, which was fine while
		# there was one process and is not now: `wake_model` and
		# `beam_size` describe a second small model and a beam width,
		# and the parakeet process has neither. Reading them here would
		# also mean reading settings that no longer exist.
		# Fixed values, not settings. The whisper process is kept for
		# reference and nothing starts it, so its knobs are not offered
		# in Settings - and reading paths that are not in the template
		# would answer with these defaults anyway, silently.
		if self.process_type == "whisper":
			settings["wake_model"] = self.WAKE_MODEL
			settings["wake_detector"] = "auto"
			settings["beam_size"] = 5

		config = json.dumps(settings)
		self.client.log("info", f"[STTProcessing] Starting {self.process_type}: "
								f"model={self.model} "
								f"device={self.input_device} "
								f"({self.input_device_name or 'system default'}) "
								f"wake={words}")
		config = json.dumps(settings)
		self.client.log("info", f"[STTProcessing] Starting {self.process_type}: "
								f"model={self.model} "
								f"device={self.input_device} "
								f"({self.input_device_name or 'system default'}) "
								f"wake={words}")
		return [sys.executable, self.__process_path, config]

	def panel_inputs(self) -> list:
		"""
		Every input this process can see, as `[index] name - Nch, API`.

		Without the ALSA helper plugins. The speech process compares this
		against its own list and names the difference, and the two disagree
		about `lavrate` and `upmix` all the time - which is of no interest to
		anybody and buries the one line that is: a real microphone offered in
		Settings that the thing which has to open it cannot see.
		"""
		try:
			from src.assistant import audio
			return [f"[{d['index']}] {d['name']} - {d['channels']}ch, "
					f"{d.get('hostapi', '?')}"
					for d in audio.input_devices(include_helpers=False)]
		except Exception:
			return []

	def ask_to_stop(self) -> bool:
		"""
		The polite half of stopping, in the protocol this child speaks.

		Handed to the registry rather than called by it directly: the
		escalation afterwards - terminate, wait, kill, wait - is the same for
		every process and belongs there, and "send STOP on port 65432" is the
		only part of it that is ours.
		"""
		self.listening = False
		try:
			sent = bool(self.send_command("STOP", retries=2))
		except Exception as ex:
			self.client.log("warning", f"[STTProcessing] Error sending STOP: {ex}")
			return False
		if sent:
			self.client.simple_notify(
				"assistant",
				"Assistant: STT",
				"Stopping Process"
			)
		return sent

	def process_exited(self, code, restarting: bool) -> None:
		"""
		The child went without being asked to.

		The panel is deaf from this moment and nothing else says so: the pill
		reads whatever it read last, the wake word does nothing, and from the
		room that is indistinguishable from a broken microphone. The registry
		logs it; saying it out loud is this side's call, because only this
		side knows the panel just stopped listening.
		"""
		self.woke_with = None
		self.woke_at = 0.0
		self.listening_since = 0.0
		self.processing = False
		self.client.ASSIST_VOICE_ACTIVITY_LEVEL = 0.0
		if restarting:
			# `listening` stays true across the gap. It is what the reader
			# loop runs on, and the reader is restarted with the process as
			# its companion - so clearing it here would start a fresh thread
			# that reads the flag once and returns. Nothing else is misled:
			# every other guard checks `process`, which is None until it is
			# back, and `status()` reads `running` before it reads this.
			self.client.ASSIST_STATUS = "LIVE"
			self.client.log("warning",
				f"[STTProcessing] The speech process exited ({code}). "
				f"It is being restarted.")
			return

		self.listening = False
		self.client.ASSIST_STATUS = "DORMANT"
		self.client.log("error",
			f"[STTProcessing] The speech process exited ({code}) and is not "
			f"coming back. Nothing is listening.")
		self.client.simple_notify(
			"error", "Assistant",
			"Speech recognition stopped. The wake word will not work until "
			"the assistant is restarted.")

	# Straight away, then after five seconds, then after thirty, then leave it
	# down. A model that cannot find its weights fails identically every time,
	# so retrying faster than this only fills the log; a child killed by
	# something passing usually comes back on the first attempt.
	RESTART_POLICY = Restart(backoff=(0.0, 5.0, 30.0), window=120.0)

	def start(self):
		if self.process is not None:
			return

		services = self.client.SERVICES

		# The reader is registered first because the process names it as a
		# companion, and a companion that is not registered is a name the
		# registry can do nothing with.
		services.create("client", self.RECEIVER, self.__listen_for_stt_data)
		services.spawn(
			"client", self.SERVICE,
			command    = self.argv,
			on_stop    = self.ask_to_stop,
			on_exit    = self.process_exited,
			companions = (self.RECEIVER,),
			restart    = self.RESTART_POLICY,
		)

		# Before the process, not after. The reader connects in a retry loop,
		# so it is allowed to be early - and `listening` is what its loop runs
		# on, so setting it afterwards races a child that answers quickly.
		self.listening = True
		services.start(self.SERVICE)

	def kill(self):
		"""
		Force the process down without asking first.

		For a caller that already knows the polite route is gone - a child
		that never answered, a shutdown with no time left. Ordinary stops go
		through stop().
		"""
		self.listening = False
		self.client.SERVICES.kill(self.SERVICE)

	def stop(self):
		"""
		Ask the STT process to exit, then confirm it did.

		Sending STOP and walking away is what leaves a process behind: if the
		command listener has already gone the message arrives nowhere. The
		survivor keeps the microphone and both ports, so the next launch
		cannot bind them and comes up silent.

		The asking is `ask_to_stop`, which is ours; the confirming and the
		escalation after it belong to the registry, which does the same thing
		for every process it holds.
		"""
		self.listening = False
		self.client.SERVICES.stop(self.SERVICE)