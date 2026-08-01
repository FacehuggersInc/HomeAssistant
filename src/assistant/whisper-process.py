from threading import Thread, Lock, Event as ThreadEvent
import re
import os
import queue
import signal
import collections
import time
import string
import json
import numpy as np
from socket import socket, AF_INET, SOCK_STREAM, SOL_SOCKET, SO_REUSEADDR
import sys, traceback

try:
    import psutil
except Exception:
    psutil = None


def _pid_alive(pid: int) -> bool:
    """Whether a process id is still running, as portably as possible."""
    if not pid:
        return True
    if psutil is not None:
        try:
            return psutil.pid_exists(pid)
        except Exception:
            pass
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, just not ours to signal.
        return True
    except OSError:
        return True

# Imported defensively so a missing audio stack produces a reportable message
# instead of a traceback into a log file. sounddevice raises OSError (not
# ImportError) when PortAudio is absent, so this catches Exception.
_IMPORT_ERROR = None
try:
    import sounddevice as sd
    import webrtcvad
    import torch
    from faster_whisper import WhisperModel
except Exception as _e:
    sd = webrtcvad = torch = WhisperModel = None
    _IMPORT_ERROR = f"{type(_e).__name__}: {_e}"

try:
    import noisereduce as nr
    _HAS_NOISEREDUCE = True
except Exception:
    _HAS_NOISEREDUCE = False


# Whisper emits these on silence or noise with high confidence. They are not
# transcription errors that better audio would fix - the model produces them
# from nothing, and they otherwise register as a command. Most are sign-offs
# and channel furniture, because the subtitle corpora it was trained on are
# full of them.
#
# Matching is on a flattened form (see _flatten), so punctuation, casing and
# curly apostrophes do not need a separate entry each.
HALLUCINATIONS = {
	"",
	# Thanks and sign-offs
	"you", "thank you", "thank you.", "thank you very much",
	"thank you very much.", "thank you so much", "thanks", "thanks.",
	"thanks a lot", "thank you all", "thank you all very much",
	"thank you for watching", "thank you for watching!",
	"thanks for watching", "thanks for watching!",
	"thank you very much for watching", "thanks for watching everyone",
	"thanks for listening", "thank you for listening",
	"thanks again", "thank you again",
	"bye", "bye.", "bye bye", "goodbye", "good bye",
	"see you next time", "see you in the next video", "see you next video",
	"i'll see you next time", "i'll see you in the next one",
	"have a good day", "have a nice day", "take care",
	# Channel furniture
	"subscribe", "please subscribe", "like and subscribe",
	"don't forget to subscribe", "please subscribe to my channel",
	"thanks for watching and don't forget to subscribe",
	"like comment and subscribe",
	# Tags the model emits instead of words
	".", "..", "...", "?", "!", "-", "\u2026",
	"[blank_audio]", "[silence]", "(silence)", "[music]", "(music)",
	"[music playing]", "(upbeat music)", "[applause]", "(applause)",
	"[laughter]", "(laughter)", "[inaudible]", "(inaudible)",
	"[no audio]", "[sound]", "(coughs)", "(sighs)",
	# Watermarks carried over from the training corpora
	"transcription by castingwords", "www.mooji.org",
	"subtitles by the amara.org community", "amara.org",
	"subs by www.zeoranger.co.uk", "transcribed by https://otter.ai",
	"the end", "all rights reserved",
}

# How many identical repeats of one phrase mean the model is looping rather
# than transcribing. Whisper falls into a repeat when it runs out of audio to
# describe, and the result is a valid-looking transcript that is entirely
# noise.
REPEAT_LIMIT = 5

# Curly quotes, ellipsis and dashes are not in string.punctuation, and Whisper
# emits all of them.
_STRIPPED = string.punctuation + "\u2026\u2018\u2019\u201c\u201d\u2013\u2014"
_PUNCTUATION_TABLE = str.maketrans("", "", _STRIPPED)


def _flatten(text: str) -> str:
	"""Lowercase, unpunctuated, single-spaced. The form everything compares in."""
	return " ".join(str(text or "").lower().translate(_PUNCTUATION_TABLE).split())


_FLAT_HALLUCINATIONS = {_flatten(entry) for entry in HALLUCINATIONS}


def _is_repeated_block(words: list, limit: int) -> bool:
	"""Whether the whole phrase is one block of words tiled `limit`+ times."""
	total = len(words)
	if total < limit:
		return False
	for size in range(1, total // limit + 1):
		if total % size or total // size < limit:
			continue
		block = words[:size]
		if all(words[i:i + size] == block for i in range(0, total, size)):
			return True
	return False


def repeated_phrase(text: str, limit: int = REPEAT_LIMIT) -> bool:
	"""
	True when one exact phrase accounts for `limit` or more of the transcript.

	Two shapes, because the model produces both: punctuated repeats
	("Okay. Okay. Okay. Okay. Okay.") and unpunctuated ones ("you you you you
	you"). The first is caught by splitting on sentence punctuation and
	counting; the second by checking whether the words tile evenly.
	"""
	counts = {}
	for part in re.split(r"[.,;:!?\n]+", str(text or "")):
		flat = _flatten(part)
		if not flat:
			continue
		counts[flat] = counts.get(flat, 0) + 1
		if counts[flat] >= limit:
			return True
	return _is_repeated_block(_flatten(text).split(), limit)


def is_hallucination(text: str) -> bool:
	flat = _flatten(text)
	if not flat:
		return True
	if flat in _FLAT_HALLUCINATIONS:
		return True
	return repeated_phrase(flat)


class WakeWhisper:

	def send_log(self, level:str, message:str, *extra):
		"""
		Say something through the server that owns this.

		`*extra` for the same reason as the server's: this is called from
		exception handlers, and a logger that raises hides what it was sent to
		report.

		Handed in rather than reached for: this class has no socket of its own.
		Calling a method it does not have is an AttributeError at the exact
		moment something is already going wrong, which is what a bulk rewrite
		of the old stdout calls left behind here.
		"""
		if extra:
			message = " ".join([str(message)] + [str(part) for part in extra])
		sink = getattr(self, "_log_sink", None)
		if sink is not None:
			try:
				sink(level, message)
				return
			except Exception:
				pass
		print(f"[{level.upper()[:4]}] {message}")

	def __init__(
		self,
		log=None,
		model_name:str="tiny.en",
		device:str="cpu",
		compute_type:str="int8",
		sample_rate:int=16000,
		vad_aggressiveness:int=3,
		window_duration_ms:int=30,
		context_audio_windows_start:int= 14,
		context_audio_windows_end:int= 10,
		minimum_speech_windows:int= 25,
		maximum_speech_windows:int= 267,
		wake_sample_windows:int=5,
		wake_timeout_seconds:float= 2.5,
		wake_speech_after_timeout_extension:float = 1.0,
		max_wake_speech_extensions:int = 2,
		wake_model:str = "",
		use_noise_reduction:bool=True,
		max_queue_size:int=8,
		session_silence_ms:int = 800,
		session_minimum_speech_ms:int = 150,
		wake_words:list[str]=[],
		input_device=None,
		override_limits:bool = False,
		initial_mode : str = "wake" # "wake" or "passthrough"
	):
		if torch is not None:
			torch.set_num_threads(5)

		#Threading
		self.running = False
		self._listen_thread = None
		self._process_thread = None
		self.stop_event = ThreadEvent()
		self.sample_check_thread = None
		#Bumped whenever the current utterance ends, so a wake check that
		#finishes after it can tell its answer is no longer wanted.
		self.__wake_check_id = 0
		self._overflows = 0

		#Callbacks
		self.on_wake = None
		self.on_final = None
		self.on_timeout = None
		self.on_voice_activity = None
		self.on_audio_error = None

		# None means "let PortAudio pick the default" - deliberately not the same
		# as pinning an index, since indices shift as devices come and go.
		self.input_device = input_device

		self.switching = False
		self.mode = initial_mode  # "wake" or "session"
		self.woke = False

		#Audio Recording
		self.__PCM_NORM_FACTOR = 32768.0
		self.audio_queue = queue.Queue(maxsize=max_queue_size)
		self.context_windows_start = max(10, context_audio_windows_start) if not override_limits else context_audio_windows_start
		self.context_windows_end = max(5, context_audio_windows_end) if not override_limits else context_audio_windows_end

		# Session (passthrough) mode ends an utterance on its own silence
		# threshold, which is much longer than wake mode's.
		#
		# Wake mode is answering "did they stop talking to the assistant", and
		# a short window is right there. Session mode is answering "did they
		# finish their sentence", and 5 windows - 150ms - is shorter than an
		# ordinary breath or the pause before a comma. The result was a
		# sentence chopped into fragments, each finalised, transcribed and sent
		# as its own API call.
		self.session_context_windows_end = max(
			self.context_windows_end,
			int(round(max(0, session_silence_ms) / window_duration_ms)),
		)
		# Detected speech, not buffered audio - the speech window already has
		# context_windows_start of lead-in prepended, so its length says
		# nothing about how much was actually spoken. A cough or a door click
		# is one or two windows; "yes" is comfortably more.
		self.session_minimum_speech_windows = max(
			1, int(round(max(0, session_minimum_speech_ms) / window_duration_ms))
		)
		self.use_noise_reduction = use_noise_reduction
		self.sample_rate = sample_rate #16000
		self.window_duration_ms = window_duration_ms # 30 ms
		self.window_size_hz = int(sample_rate * (window_duration_ms / 1000)) #16000 * 0.3s
		self.channels = 1
		self.too_quiet_db = -35
		#The same question, on the other side of the noise reduction - and on
		#a different scale. too_quiet_db is measured on int16 samples; by the
		#processing loop the audio is normalised to [-1, 1], which is about
		#90dB lower for the same sound. Reusing the int16 figure there would
		#throw away everything, including clear speech.
		#
		#Set below quiet speech and above what is left of a silent room after
		#de-noising, which is roughly -70dB.
		self.SILENT_DB = -60.0
		#Raised for a microphone with its own AGC. Sixty decibels of gain
		#lifts the room's noise floor along with the speech, so a threshold
		#set for a quiet raw signal stops separating them.
		self.HARDWARE_SILENT_DB = -48.0
		self.vad = webrtcvad.Vad(vad_aggressiveness)

		self.wake_words = wake_words
		self.wake_sample_windows = wake_sample_windows
		self.speech_timeout_start = None
		self.wake_timeout_seconds = wake_timeout_seconds
		self.wake_speech_after_timeout_extension = wake_speech_after_timeout_extension
		self.minimum_speech_windows = min(self.context_windows_start + self.context_windows_end, minimum_speech_windows) if not override_limits else minimum_speech_windows
		self.maximum_speech_windows = maximum_speech_windows
		self.max_wake_speech_extensions = max_wake_speech_extensions

		# Transcribing Model
		#Where send_log() sends. Set before anything else, so a failure
		#during setup still has somewhere to go.
		self._log_sink = log
		self.model_name = model_name
		self.device = device
		self.compute_type = compute_type
		self.model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)

		# A second, small model for the wake word only.
		#
		# The two jobs want opposite things. The wake check runs on every
		# 150ms of speech and has to answer before the person finishes their
		# sentence; it is looking for ONE known word, so a model that mishears
		# "weather" costs nothing. The phrase is transcribed once and is read
		# by a person, so accuracy is what matters and a second of latency is
		# amortised over the whole utterance.
		#
		# Sharing one model made the accurate choice pay its cost on every
		# wake check too - and they take the same lock, so a phrase being
		# transcribed blocks the next wake check behind it.
		self.wake_model = self.model
		self.wake_model_name = self.model_name
		if wake_model and wake_model != self.model_name:
			try:
				self.wake_model = WhisperModel(
					wake_model, device=self.device,
					compute_type=self.compute_type)
				self.wake_model_name = wake_model
				self.send_log("info",
					f"[Whisper]: Wake word on '{wake_model}', "
					f"phrases on '{self.model_name}'.")
			except Exception as exc:
				# The big one still works. A wake word checked slowly is
				# better than a process that will not start.
				self.send_log("warning",
					f"[Whisper]: Could not load wake model "
					f"'{wake_model}': {exc}. Using '{self.model_name}'.")
		self.transcribe_settings = {
			"language": "en",
			"temperature": 0,

			# best_of only applies when sampling (temperature > 0). At
			# temperature 0 faster-whisper uses beam search, so the old
			# "best_of": 5 was inert - beam_size is the knob that was wanted.
			"beam_size": 5,

			# Whisper carries previous text into the next window by default,
			# which makes it loop and hallucinate on short isolated commands.
			"condition_on_previous_text": False,

			# Drop windows the model itself thinks are silence, which is where
			# the phantom "Thank you." / "Thanks for watching!" outputs come
			# from.
			"no_speech_threshold": 0.6,
			"log_prob_threshold": -1.0,
		}

		# faster-whisper's WhisperModel is not documented as thread-safe, and
		# the wake-word check runs on its own thread alongside the processing
		# loop.
		self._model_lock = Lock()
		#Its own lock, so a phrase being transcribed does not hold up the next
		#wake check behind it. Only meaningful when they are separate models;
		#when they are the same object both paths take the same lock.
		self._wake_lock = (Lock() if self.wake_model is not self.model
						   else self._model_lock)


	## CORE
	def start(self):
		if self.stop_event.is_set():
			self.stop_event.clear()
		self._listen_thread = Thread(target=self.__listen_loop, daemon=True)
		self._process_thread = Thread(target=self.__processing_loop, daemon=True)
		self._listen_thread.start()
		self._process_thread.start()

	def stop(self):
		self.stop_event.set()
		try:
			self.audio_queue.put_nowait(None)
		except Exception:
			pass
		if self._listen_thread:
			self._listen_thread.join(timeout=2.0)
		if self._process_thread:
			self._process_thread.join(timeout=2.0)

	def set_callbacks(self, on_wake=None, on_final=None, on_timeout = None, on_voice_activity=None, on_audio_error=None):
		self.on_wake = on_wake
		self.on_final = on_final
		self.on_timeout = on_timeout
		self.on_voice_activity = on_voice_activity
		self.on_audio_error = on_audio_error

	## UTIL
	def clean_text(self, text: str) -> str:
		return ''.join(ch for ch in text if ch not in string.punctuation).strip()

	def contains_wake_word(self, text: str) -> str | None:
		t = text.lower()
		for w in self.wake_words:
			if w in t:
				return w
		return None

	def is_too_quiet(self, audio_bytes, threshold_db=-35, sample_rate=16000):
		audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
		rms = np.sqrt(np.mean(audio**2))
		db = 20 * np.log10(rms + 1e-6)
		return db < threshold_db

	def switch_mode(self, mode:str):
		if mode not in ["wake", "passthrough"]:
			mode = "wake"
		self.mode = mode
		self.switching = True

	def transcribe(self, audio, wake: bool = False):
		"""
		Locked transcribe, returning cleaned text or ''.

		`wake=True` uses the small model - see the constructor for why the two
		jobs do not want the same one.
		"""
		model = self.wake_model if wake else self.model
		lock = self._wake_lock if wake else self._model_lock
		with lock:
			segments, _ = model.transcribe(audio, **self.transcribe_settings)
			text = " ".join(seg.text.strip() for seg in segments).strip()
		return "" if is_hallucination(text) else text

	## RECORDING
	def __listen_loop(self):
		connection = True
		while not self.stop_event.is_set():
			try:
				with sd.InputStream(
					samplerate=self.sample_rate,
					channels=self.channels,
					dtype="int16",
					device=self.input_device
				) as stream:
					self.send_log("debug", "[Whisper]: Microphone opened.")
					if not connection and callable(self.on_audio_error):
						self.on_audio_error("")   # recovered
					connection = True
					self.__stream_loop(stream)
			except Exception as exc:
				if connection:
					connection = False
					self.send_log("warning", f"[Whisper]: Microphone Error: {exc}")
					# Reported to the client so it can tell the user, instead of
					# retrying silently every 5s into a log nobody reads.
					if callable(self.on_audio_error):
						self.on_audio_error(str(exc))
				# wait(), not sleep(): a stop arriving during the backoff used
				# to be ignored for the full five seconds, which is long enough
				# for the client to give up and terminate the process instead.
				self.stop_event.wait(5)

	def __wake_word_check(self, sample:bytes, started_as:int = 0):
		"""
		Look for a wake word in one sample, on its own thread.

		The gate is cleared in a `finally`. Cleared only on the success path,
		a transcribe that raised left a dead Thread in `sample_check_thread`
		forever - and the loop gates on that attribute being falsy, so wake
		detection stopped for good. `reset_all()` does not clear it either, so
		nothing recovered short of a mode switch.
		"""
		try:
			converted = np.frombuffer(sample, dtype=np.int16).astype(np.float32) / self.__PCM_NORM_FACTOR
			text = self.transcribe(converted, wake=True)
			if self.__wake_check_id != started_as:
				# The utterance this sample came from has already been
				# finalised and sent while this was transcribing. Waking now
				# announces a phrase that has already been answered, and
				# nothing further is coming to stand the panel back down - so
				# it sits reading "listening" with nobody talking to it.
				self.send_log("debug",
					"[Whisper]: Wake check finished too late - discarded.")
				return
			if text:
				lowered = text.lower()
				for word in self.wake_words:  # e.g., ["clyde", "jarvis"]
					# Word boundaries, not substring: "alexa" should not fire on
					# "alexander", and a short wake word matches inside all sorts
					# of ordinary speech.
					if re.search(rf"\b{re.escape(word)}\b", lowered):
						self.send_log("debug", f"[Whisper]: Wake word '{word}' detected.")
						self.woke = True
						if callable(self.on_wake):
							self.on_wake(word)
						break
		except Exception as exc:
			self.send_log("warning",
				f"[Whisper]: Wake word check failed: {exc}")
		finally:
			self.sample_check_thread = None

	def __test_sample_for_wake(self, sample_window:list[bytes]):
		check = self.sample_check_thread
		if check is not None and check.is_alive():
			#Already running a check
			return
		if check is not None:
			# Finished, but the gate was left set. Cleared here as well as in
			# the check's own finally, so a thread that died in a way that
			# skipped it cannot stop wake detection permanently.
			self.sample_check_thread = None
		sample = b"".join(sample_window)
		self.sample_check_thread = Thread(
			target=self.__wake_word_check,
			args=[sample, self.__wake_check_id],
			daemon=True
		)
		self.sample_check_thread.start()

	def __stream_loop(self, stream:sd.InputStream):

		speech_window = [] #Will Store all of the frames of audio during speech

		sample_window = []
		self.sample_check_thread = None

		#Always Stores a frame of audio each iteration, will be inserted at the beginning of the speech window when speech is first detected
		pre_context = collections.deque(maxlen=self.context_windows_start)

		# Sized for whichever mode waits longest; each mode compares against
		# its own threshold below.
		end_context = collections.deque(
			maxlen=max(self.context_windows_end, self.session_context_windows_end))

		was_speech = False #the trigger var for capturing an entire phrase

		#Windows of ACTUAL detected speech in the current utterance, used by
		#session mode to tell a real phrase from a click or a cough.
		speech_windows_seen = 0

		ignore_timeout_call = False
		timeout_called = False
		extensions_added = 0

		end_context_windows_accumulated = 0

		last_speech_time = time.time()
		reset_timeout_time = 15.0

		speech_window_accumulation_limit = (self.window_duration_ms * self.maximum_speech_windows) / 1000
		speech_cutoff = False

		def reset_all():
			nonlocal was_speech, last_speech_time, end_context, sample_window, speech_window
			nonlocal ignore_timeout_call, timeout_called, extensions_added, speech_cutoff
			nonlocal end_context_windows_accumulated
			nonlocal speech_windows_seen
			nonlocal self

			was_speech = False
			last_speech_time = time.time()
			end_context.clear()
			sample_window.clear()
			speech_window.clear()
			speech_windows_seen = 0
			self.woke = False
			self.speech_timeout_start = None
			timeout_called = False
			ignore_timeout_call = False
			end_context_windows_accumulated = 0
			extensions_added = 0
			speech_cutoff = False
			# A finished check must not survive a reset holding the gate.
			# The thread is left to end on its own; only the gate is dropped.
			check = self.sample_check_thread
			if check is None or not check.is_alive():
				self.sample_check_thread = None
			# Anything still transcribing belongs to the utterance being
			# thrown away, so its answer is no longer wanted.
			self.__wake_check_id += 1

		## CORE LOOP
		while not self.stop_event.is_set():

			try:
				#Get Audio Window
				audio_window, overflowed = stream.read( self.window_size_hz )
				if overflowed:
					# The loop fell behind realtime and the driver dropped
					# samples. Silently discarded before, which made the
					# resulting truncated phrases look like model errors.
					self._overflows += 1
					if self._overflows in (1, 10, 100) or self._overflows % 500 == 0:
						self.send_log("warning",
							f"[Whisper]: Audio overflow x{self._overflows} "
							f"- input dropped, processing is behind realtime.")
				audio_window = audio_window[:, 0].tobytes()

				#If Switching Modes, Reset Everything | This allows clean transitions between modes
				if self.switching:
					reset_all()
					self.switching = False
					self.send_log("debug", '[Whisper]: Mode switched, resetting internal state.')

				#Add Context
				pre_context.append(audio_window)

				# VAD runs on the RAW window.
				#
				# This used to spectral-gate the whole 420ms pre_context buffer
				# on every 30ms frame and then keep only the last 30ms of it -
				# roughly 23ms of work per 30ms of audio, about 76% of a core
				# continuously, 5000x more expensive than the VAD call it was
				# feeding. On slower hardware the loop cannot keep up at that
				# cost and starts dropping audio, which truncates speech
				# windows and produces exactly the unreliable transcripts it
				# was meant to improve.
				#
				# webrtcvad is built for noisy audio and is already running at
				# aggressiveness 3. Noise reduction still happens where it
				# actually helps: once, on the complete utterance, before
				# transcription.
				vad_window = audio_window

				#Detect Speech (per window)
				is_speech_in_window = False
				try:
					is_speech_in_window = self.vad.is_speech(
						vad_window, 
						sample_rate=self.sample_rate
					)
				except Exception:
					is_speech_in_window = False

				## WAKE MODE | Includes Speech Window Building Only when a Wake Word was Detected
				if self.mode == "wake":
					#Failsafe on first switch to this Mode, Reset Everything
					if self.switching:
						reset_all()
						self.switching = False

						#Voice Activity Callback Reset
						if callable(self.on_voice_activity):
							try:
								self.on_voice_activity(0.0)
							except Exception:
								pass
						self.send_log("warning", '[Whisper]: Mode switched, resetting internal state. (Fail Safe : Wake)')
						continue

					if is_speech_in_window and not speech_cutoff: #SPEECH BLOCK
						last_speech_time = time.time()

						#If Timeout reached, but Audio is still being detected as speech, force speech window to end
						if not self.speech_timeout_start is None \
						and time.time() - self.speech_timeout_start >= self.wake_timeout_seconds \
						and extensions_added >= self.max_wake_speech_extensions:
							speech_window.append( audio_window )
							speech_cutoff = True
							self.send_log("debug", "[Whisper]: Speech cutoff triggered due to max timeout extensions.")
							continue

						#Force Reset if too long, For Example, Music or TV may trigger VAD continuously. 
						if not self.woke and len(speech_window) * (self.window_duration_ms / 1000) >= speech_window_accumulation_limit:
							self.send_log("debug", "[Whisper]: Speech window accumulation limit reached, resetting.")
							reset_all()
							continue

						#Voice Activity Callback
						if self.woke and callable(self.on_voice_activity):
							try:
								# Convert audio_window to float32 in range [-1, 1]
								audio_np = np.frombuffer(audio_window, dtype=np.int16).astype(np.float32) / self.__PCM_NORM_FACTOR
								rms = np.sqrt(np.mean(audio_np ** 2))
								# RMS is already in [0, 1] for normalized audio
								loudness_normalized = min(max(rms, 0.0), 1.0)
								self.on_voice_activity(loudness_normalized)
							except Exception:
								pass

						#Trigger Was Speech (this toggles was_speech)
						if not was_speech:
							was_speech = True
							sample_window.extend( pre_context )
							speech_window.extend( pre_context ) #add start context to speech window

						# Check Sample Window Regularly for a wake Word
						if not self.woke:
							sample_window.append( audio_window )
							# Liveness, not the attribute. A finished thread
							# object is truthy, so gating on `not
							# self.sample_check_thread` stopped asking the
							# moment one was left behind.
							if len(sample_window) >= self.wake_sample_windows:
								self.__test_sample_for_wake(sample_window)

						#Build Speech
						speech_window.append( audio_window )
						end_context.clear() #for a clean context, always clear, there wont be any in-between silence

						#Reset Per Speech Blob End Context Accumulation Counter
						end_context_windows_accumulated = 0

						if self.woke and len(speech_window) >= self.minimum_speech_windows:
							if self.speech_timeout_start is None:
								self.speech_timeout_start = time.time()
								self.send_log("debug", "[Whisper]: Minimum speech reached, starting timeout.")

							elif time.time() - self.speech_timeout_start >= self.wake_timeout_seconds \
							and extensions_added < self.max_wake_speech_extensions:
								self.speech_timeout_start += self.wake_speech_after_timeout_extension
								extensions_added += 1
								self.send_log("debug", f"[Whisper]: Wake speech after timeout extension added ({self.wake_speech_after_timeout_extension}s)")
							ignore_timeout_call = True #if conditions are met early, ignore timeout call

					else: #SILENCE BLOCK
						finalize = speech_cutoff #finalize if speech cutoff triggered

						#If Speech Window Was Triggered
						if was_speech and not finalize:

							#Start Building End Context
							end_context.append( audio_window )

							if not self.woke:
								sample_window.append( audio_window )
								# Liveness, not the attribute - see above.
								if len(sample_window) >= self.wake_sample_windows:
									self.__test_sample_for_wake(sample_window)

							#Dont allow end_context to build yet, so that end context doesn't trigger finalization
							if self.woke and len(speech_window) < self.minimum_speech_windows:
								#Limit speech_window appending per speech blob to the same limit as end_context would get
								if not end_context_windows_accumulated >= self.context_windows_end:
									speech_window.append( audio_window ) #For the same reason as end_context is being built
									end_context_windows_accumulated += 1
								end_context.clear()
								
							elif self.woke and len(speech_window) >= self.minimum_speech_windows:
								#Start timeout if not already started
								if self.speech_timeout_start is None:
									self.speech_timeout_start = time.time()

								# Check if timeout has elapsed | end_context clearing release
								if time.time() - self.speech_timeout_start >= self.wake_timeout_seconds:
									# Allow end_context to build (do NOT clear it)

									#Timeout call if only timeout reached, but also speech did not meet minimum
									if not timeout_called and not ignore_timeout_call:
										timeout_called = True
										self.send_log("debug", "[Whisper]: Speech timeout reached.")
										if callable(self.on_timeout):
											self.on_timeout("wake_timeout")

								else:
									#Limit speech_window appending per speech blob to the same limit as end_context would get
									if not end_context_windows_accumulated >= self.context_windows_end:
										speech_window.append( audio_window ) #For the same reason as end_context is being built
										end_context_windows_accumulated += 1
									end_context.clear()  # still waiting for timeout

							#If End Context is Full, Meaning All Context is There, Finalize Speech
							if self.woke and len(end_context) >= self.context_windows_end:
								finalize = True 
								self.send_log("debug", "[Whisper]: End context full, finalizing speech window.")

						if finalize:
							# Timed from the first speech window, so the log
							# says WHERE the wait was rather than only that
							# there was one. The three numbers are different
							# problems: talking is the person, waiting is the
							# VAD refusing to call the room silent, and the
							# model is the model.
							spoke_ms = int(len(speech_window)
										   * self.window_duration_ms)
							waited_ms = int(end_context_windows_accumulated
											* self.window_duration_ms)
							self.send_log("debug",
								f"[Whisper]: Finalizing - {spoke_ms}ms spoken, "
								f"{waited_ms}ms waiting for silence.")
							self._finalised_at = time.time()
							last_speech_time = time.time() # artificial reset the timer | used to prevent reset before speech_window can be processed

							#Build Final Byte Window
							speech_window.extend( end_context ) #add end context to speech window
							speech = b"".join(speech_window)

							# De-noising happens in the processing loop, not here.
							# reduce_noise() on a whole utterance is real work, and
							# this is the audio thread - the microphone stream keeps
							# filling while it runs, so a long phrase was paid for by
							# dropping the start of whatever came next.
							
							#Finally if speech wasn't too quiet, queue for processing
							if not self.is_too_quiet(
								speech,
								threshold_db = self.too_quiet_db,
								sample_rate = self.sample_rate
							):
								try:
									self.audio_queue.put_nowait( speech )
								except queue.Full:
									# If queue is full, drop the oldest and enqueue
									try:
										_ = self.audio_queue.get_nowait()
										self.audio_queue.put_nowait(speech)
									except Exception:
										pass

							reset_all()

				## PASSTHROUGH MODE | Does not require to be Woken, Will Build Speech window as Normal
				elif self.mode == "passthrough":
					#Failsafe on first switch to this Mode, Reset Everything
					if self.switching:
						reset_all()
						self.switching = False
						self.send_log("warning", '[Whisper]: Mode switched, resetting internal state. (Fail Safe : Passthrough)')
						continue

					if is_speech_in_window: #SPEECH BLOCK
						last_speech_time = time.time()

						#Force Reset if too long, For Example, Music or TV may trigger VAD continuously. 
						if not self.woke and len(speech_window) * (self.window_duration_ms / 1000) >= speech_window_accumulation_limit:
							self.send_log("debug", "[Whisper]: Speech window accumulation limit reached, resetting.")
							reset_all()
							continue

						#Voice Activity Callback
						if callable(self.on_voice_activity):
							try:
								# Convert audio_window to float32 in range [-1, 1]
								audio_np = np.frombuffer(audio_window, dtype=np.int16).astype(np.float32) / self.__PCM_NORM_FACTOR
								rms = np.sqrt(np.mean(audio_np ** 2))
								# RMS is already in [0, 1] for normalized audio
								loudness_normalized = min(max(rms, 0.0), 1.0)
								self.on_voice_activity(loudness_normalized)
							except Exception:
								pass

						#Trigger Was Speech (this toggles was_speech)
						if not was_speech:
							was_speech = True
							speech_window.extend( pre_context ) #add start context to speech window

						#Build Speech
						speech_windows_seen += 1
						speech_window.append( audio_window )
						end_context.clear() #for a clean context, always clear, there wont be any in-between silence

					else: #SILENCE BLOCK
						if was_speech:
							#Start Building End Context
							end_context.append( audio_window )

							#If End Context is Full, Meaning All Context is There, Finalize Speech
							#session_context_windows_end, not context_windows_end: a
							#sentence needs longer to be judged finished than a
							#command does. See __init__.
							if len(end_context) >= self.session_context_windows_end:
								last_speech_time = time.time() # artificial reset the timer | used to prevent reset before speech_window can be processed

								#Too little actual speech to be a phrase - a cough, a
								#click, a chair. Dropping it here is what stops a
								#stray noise becoming a transcription and then an API
								#call with a session open.
								if speech_windows_seen < self.session_minimum_speech_windows:
									self.send_log("debug",
										f"[Whisper]: Discarding {speech_windows_seen} "
										f"speech window(s) - below session minimum.")
									reset_all()
									continue

								#Build Final Byte Window
								speech_window.extend( end_context ) #add end context to speech window
								speech = b"".join(speech_window)

								# De-noising happens in the processing loop, not here.
								# reduce_noise() on a whole utterance is real work, and
								# this is the audio thread - the microphone stream keeps
								# filling while it runs, so a long phrase was paid for by
								# dropping the start of whatever came next.
								
								#Finally if speech wasn't too quiet, queue for processing
								if not self.is_too_quiet(
									speech,
									threshold_db = self.too_quiet_db,
									sample_rate = self.sample_rate
								):
									try:
										self.audio_queue.put_nowait( speech )
									except queue.Full:
										# If queue is full, drop the oldest and enqueue
										try:
											_ = self.audio_queue.get_nowait()
											self.audio_queue.put_nowait(speech)
										except Exception:
											pass

								reset_all()

				#Whether Speech is detected or Not, Reset if too long without speech
				if time.time() - last_speech_time >= reset_timeout_time:
					# Reported before the reset, and only when there was
					# something to report.
					#
					# reset_all() sets last_speech_time, so this re-arms and
					# fires again every fifteen seconds forever - in a silent
					# room that was a socket write on the audio thread, on a
					# timer, saying nothing had happened. Announced only when
					# a phrase was actually abandoned.
					announce = was_speech
					if announce:
						self.send_log("debug",
							"[Whisper]: Resetting due to extended silence.")
					reset_all()
					if announce and callable(self.on_timeout):
						# Off this thread. sendall blocks, and the microphone
						# stream is filling while it does - a parent that is
						# slow to read stalls the audio, which drops windows
						# and truncates whatever is said next.
						Thread(target=self.__say_timeout,
						       args=["extended_timeout"],
						       daemon=True).start()

			except Exception as exc:
				self.send_log("warning",
					f"[Whisper]: Error in stream loop: {exc} | "
					f"{traceback.format_exc().strip()}")
				reset_all()
				continue

		## ON END
		stream.close()
		reset_all()
		self.send_log("debug", "[Whisper]: Microphone closed.")

	## PROCESSING
	def __say_timeout(self, kind: str) -> None:
		"""Report a timeout without the audio thread waiting on the socket."""
		try:
			if callable(self.on_timeout):
				self.on_timeout(kind)
		except Exception as exc:
			self.send_log("warning",
				f"[Whisper]: Could not report {kind}: {exc}")

	def multi_phrase_check(self, text: str) -> bool:
		"""
		Reject a transcript that is the model looping rather than speech.

		The phrase threshold used to be "appears more than once", which threw
		away ordinary answers - "yes, yes" and "no, no" are things people
		actually say. It is REPEAT_LIMIT now, in line with is_hallucination.
		"""
		if repeated_phrase(text, REPEAT_LIMIT):
			return True

		# A word that dominates the transcript without tiling it evenly -
		# "hello there hello hello hello hello" - which repeated_phrase()
		# cannot see, since the whole phrase is not one repeated block.
		wake = {w.lower() for w in self.wake_words}
		counts = {}
		for word in _flatten(text).split():
			if not word or word in wake:
				continue
			counts[word] = counts.get(word, 0) + 1
			if counts[word] >= REPEAT_LIMIT:
				return True

		return False
	
	def __processing_loop(self):
		while not self.stop_event.is_set():
			#Get Speech Audio Window
			speech = self.audio_queue.get()
			if speech is None:
				break
			
			#Convert
			speech = np.frombuffer(speech, dtype=np.int16).astype(np.float32) / self.__PCM_NORM_FACTOR
			queued_ms = 0
			started = getattr(self, "_finalised_at", 0.0)
			if started:
				queued_ms = int((time.time() - started) * 1000)
			self.send_log("debug", "[Whisper]: Processing speech window...")
			began = time.time()

			# De-noised here rather than on the audio thread, which cannot
			# afford to wait: this one is already about to spend far longer
			# inside the model, and nothing is being recorded against it.
			if self.use_noise_reduction:
				try:
					speech = nr.reduce_noise(y=speech, sr=self.sample_rate)
				except Exception as exc:
					self.send_log("debug",
						f"[Whisper]: Noise reduction skipped: {exc}")

			# Loudness is judged AFTER the noise comes out, which is the
			# question worth asking - "was anything said" rather than "was
			# the room quiet". The check on the audio thread now sees raw
			# audio and so keeps more than it did; this is what still drops a
			# window of nothing but hiss, and it costs a transcription rather
			# than a dropped phrase to be wrong here.
			level = float(np.sqrt(np.mean(speech ** 2)))
			level_db = 20 * np.log10(level + 1e-9)
			floor = (self.HARDWARE_SILENT_DB if not self.use_noise_reduction
					 else self.SILENT_DB)
			if level_db < floor:
				self.send_log("debug",
					f"[Whisper]: Nothing said ({level_db:.0f}dB).")
				continue

			#Transcribe Audio
			try:
				# Locked: the wake-word check transcribes on its own thread and
				# WhisperModel is not documented as thread-safe.
				with self._model_lock:
					segments, info = self.model.transcribe(speech, **self.transcribe_settings)
					segments = list(segments)
			except Exception as exc:
				self.send_log("warning", f"[Whisper]: Transcription error: {exc}")
				continue
			
			#Build
			final_text_pieces = []
			final_timestamps = []

			#Build Timestamps
			for seg in segments:
				final_text_pieces.append(seg.text)
				if hasattr(seg, "words") and seg.words:
					for w in seg.words:
						final_timestamps.append({"word": w.word, "start": float(w.start), "end": float(w.end)})
				else:
					final_timestamps.append({"segment_text": seg.text, "start": float(seg.start), "end": float(seg.end)})

			#Build Text and Send
			final_text = " ".join(p.strip() for p in final_text_pieces).strip()

			if is_hallucination(final_text):
				self.send_log("debug", f"[Whisper]: Discarded hallucination: {final_text!r}")
				final_text = ""

			if final_text and self.clean_text(final_text) and len(final_text.split()) <= 20:

				if self.multi_phrase_check(final_text):
					# `continue`, not `return`. This is inside the processing
					# loop, so returning ended the thread outright - one
					# repetitive transcript and nothing was ever transcribed
					# again, with every later phrase queueing behind a worker
					# that had gone. Nothing restarts it.
					self.send_log("debug",
						f"[Whisper]: Discarded repetition: {final_text!r}")
					continue

				# The whole journey, on one line. Anything that got slower
				# shows up as one of these growing rather than as "it feels
				# slow now".
				model_ms = int((time.time() - began) * 1000)
				self.send_log("debug",
					f"[Whisper]: Final Transcription ({queued_ms}ms queued, "
					f"{model_ms}ms in the model): {final_text}")

				if callable(self.on_final):
					self.on_final(final_text, final_timestamps)

			final_text = ""


class STTServer:
	def __init__(self, host="127.0.0.1", command_port=65432, data_port=65433):
		self.host = host
		self.ports = {"command": command_port, "data": data_port}
		self.running = True
		self.connections: dict[str, socket] = {"command": None, "data": None}

		# argv[1] is a JSON blob from STTProcessing. The old positional
		# comma-joined wake-word string is still accepted so an out-of-date
		# caller keeps working.
		config = {}
		if len(sys.argv) > 1:
			try:
				config = json.loads(sys.argv[1])
			except (ValueError, TypeError):
				config = {"wake_words": [w.strip() for w in sys.argv[1].split(",") if w.strip()]}

		wake_words = config.get("wake_words") or ["alexa"]
		self.input_device = config.get("input_device")
		self.model_name = config.get("model", "tiny.en")
		# Set before the early return below, so the watchdog still works on a
		# process that came up without an audio stack.
		self.parent_pid = int(config.get("parent_pid") or 0)
		self.import_error = _IMPORT_ERROR

		if self.import_error:
			self.send_log("warning", f"[STTServer]: Audio stack unavailable -> {self.import_error}")
			self.whisper = None
			return

		self.whisper = WakeWhisper(
			log = self.send_log,
			model_name = self.model_name,
			# Always small, whatever the phrase model is. It answers one
			# question - "was the wake word in that" - and has to answer it
			# before the person finishes speaking.
			wake_model = str(config.get("wake_model") or "tiny.en"),
			# Aggressive either way, and deliberately so.
			#
			# The obvious move is to soften this when the array has its own
			# VAD, on the grounds that two aggressive gates in series clip the
			# quiet start of a phrase. That is true and it is the wrong trade.
			#
			# A phrase is finalised when `context_windows_end` consecutive
			# windows are called SILENCE. A softer gate calls fewer things
			# silence, so that fills more slowly - and the counter resets on
			# any speech window, so a room with a fridge in it can hold a
			# phrase open. Softening it made every phrase END later, which
			# reads as the panel being slow to hear you.
			#
			# Worse with an array's AGC: sixty decibels of gain lifts the
			# room's noise floor, so ambient noise looks like speech to a gate
			# already reluctant to call anything silence.
			vad_aggressiveness=3,
			window_duration_ms=30,
			context_audio_windows_start = 14,
			context_audio_windows_end = 5,
			minimum_speech_windows = 20,
			wake_timeout_seconds = 3.5,
			# Off when the microphone has already done it. A second pass over
			# audio an XVF3800 has already cleaned is what makes speech sound
			# underwater - see mic_profile in the settings.
			use_noise_reduction=(str(config.get("mic_processing") or "software")
								 != "hardware"),
			session_silence_ms = int(config.get("session_silence_ms") or 800),
			session_minimum_speech_ms = int(config.get("session_minimum_speech_ms") or 150),
			wake_words = wake_words,
			input_device = self.input_device
		)
		self.whisper.set_callbacks(
			on_final=self.process_transcribed,
			on_wake = self.trigger_wake,
			on_timeout = self.trigger_wait,
			on_voice_activity = self.send_voice_activity,
			on_audio_error = self.send_audio_error
		)
	

	## EVENTS
	def send_log(self, level:str, message:str, *extra):
		"""
		Say something, in the parent's log rather than this process's stdout.

		`*extra` is joined on, the way print() would have. Not because callers
		should pass it, but because a logger that raises is the worst kind:
		this one is reached from inside exception handlers, and a TypeError
		there replaces the failure being reported with one about the report.

		This runs as a separate process, so a print here goes to whatever
		stdout it inherited - not timestamped, no level, not in the log file,
		and so never on the Logs page. The socket already carries every other
		event; this is one more command on it.

		Falls back to print when there is no connection yet, which is the
		startup window where something going wrong is most worth seeing.
		"""
		if extra:
			message = " ".join([str(message)] + [str(part) for part in extra])
		text = str(message).replace("\n", " ")[:600]
		if self.connections.get("data"):
			try:
				self.connections["data"].sendall(
					f"host:log:{level}:{text}".encode("utf-8"))
				return
			except Exception:
				self.__close_connection("data")
		# A print, and it must stay one: this IS the logger, so anything
		# else here recurses until the stack gives out.
		print(f"[{level.upper()[:4]}] {text}")

	def send_voice_activity(self, level:float):
		if self.connections["data"]:
			try:
				self.connections["data"].sendall(
					f"host:voice_activity:{level:.3f}".encode("utf-8")
				)
			except Exception:
				self.send_log("warning", "[STTServer]: Lost transcript connection.")
				self.__close_connection("data")

	def send_audio_error(self, message:str):
		"""Forward a microphone problem to the client so it can surface it."""
		if self.connections["data"]:
			try:
				self.connections["data"].sendall(
					f"host:audio_error:{message}".encode("utf-8")
				)
			except Exception:
				self.__close_connection("data")

	def trigger_wake(self, wake_word:str):
		if self.connections["data"]:
			try:
				self.connections["data"].sendall(
					f"host:woke:{wake_word}".encode("utf-8")
				)
			except Exception:
				self.send_log("warning", "[STTServer]: Lost transcript connection.")
				self.__close_connection("data")

	def trigger_wait(self, type:str):
		if self.connections["data"]:
			try:
				self.connections["data"].sendall(
					f"host:wait:{type}".encode("utf-8")
				)
			except Exception:
				self.send_log("warning", "[STTServer]: Lost transcript connection.")
				self.__close_connection("data")

	def process_transcribed(self, transcribed: str, timestamps: any):
		if self.connections["data"] and transcribed.strip():
			try:
				self.connections["data"].sendall(
					f"host:transcribe:{transcribed.lower()}".encode("utf-8")
				)
			except Exception:
				self.send_log("warning", "[STTServer]: Lost transcript connection.")
				self.__close_connection("data")


	## CORE
	def __close_connection(self, which: str):
		conn = self.connections.get(which)
		if conn:
			try:
				conn.shutdown(1)
			except Exception:
				pass
			try:
				conn.close()
			except Exception:
				pass
		self.connections[which] = None

	def __listen_for_commands(self):
		with socket(AF_INET, SOCK_STREAM) as s:
			s.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
			s.bind((self.host, self.ports["command"]))
			s.listen(1)
			# Timed accept, so `running` going False is noticed instead of the
			# thread sitting in accept() forever waiting for a command that is
			# never coming.
			s.settimeout(0.5)
			self.send_log("debug", "[STTServer]: Listening for commands...")
			while self.running:
				try:
					conn, addr = s.accept()
				except TimeoutError:
					continue
				except OSError:
					break
				with conn:
					self.send_log("debug", f"[STTServer]: Command connection from {addr}")
					try:
						data = conn.recv(1024)
						if not data:
							# continue, NOT break. An empty connection - a port
							# probe, a client that reconnected and dropped -
							# used to kill this listener outright, after which
							# STOP could never be delivered and the process
							# survived every shutdown.
							continue

						raw = data.decode("utf-8").strip()
						to, command = raw.split(":")
						if to != "server":
							continue

						if command == "STOP":
							self.send_log("debug", "[STTServer]: Received STOP command.")
							self.stop()
							break
						
						elif command == "START_WAKE":
							self.whisper.switch_mode("wake")
							self.send_log("debug", "[STTServer]: Switched Whisper to WAKE mode.")

						elif command == "START_PASSTHROUGH":
							self.whisper.switch_mode("passthrough")
							self.send_log("debug", "[STTServer]: Switched Whisper to PASSTHROUGH mode.")

					except Exception as e:
						self.send_log("warning", f"[STTServer]: Command Error: {e}")

	def __send_and_recv_data(self): 
		with socket(AF_INET, SOCK_STREAM) as s:
			s.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
			s.bind((self.host, self.ports["data"]))
			s.listen(1)
			self.send_log("debug", "[STTServer]: Waiting for transcript connection...")
			conn, addr = s.accept()
			if conn:
				self.send_log("debug", f"[STTServer]: Data connection from {addr}")
				self.connections["data"] = conn
				try:
					conn.sendall(b"host:notify:Ready!")
				except Exception:
					self.__close_connection("data")

	def __watch_parent(self):
		"""
		Exit if the client goes away without sending STOP.

		A crash, a kill, or a launcher restart leaves this process with nobody
		to talk to - still holding the microphone and both ports, so the next
		client cannot bind them and comes up with no audio at all. Nothing
		else notices: the command listener just waits for a command that is
		never coming.
		"""
		if not self.parent_pid:
			return
		while self.running:
			if not _pid_alive(self.parent_pid):
				self.send_log("debug", "[STTServer]: Parent process is gone - shutting down.")
				self.shutdown()
			time.sleep(2)

	def __install_signals(self):
		def handler(signum, _frame):
			self.send_log("debug", f"[STTServer]: Signal {signum} - shutting down.")
			self.shutdown()
		for sig in (signal.SIGTERM, signal.SIGINT):
			try:
				signal.signal(sig, handler)
			except (ValueError, OSError, AttributeError):
				# Not the main thread, or the platform has no such signal.
				pass

	def shutdown(self, code: int = 0):
		"""Release everything and leave, without waiting on native threads."""
		try:
			self.stop()
		except Exception as e:
			self.send_log("warning", f"[STTServer]: Error during stop: {e}")
		try:
			sys.stdout.flush()
		except Exception:
			pass
		# os._exit, not sys.exit: a normal interpreter shutdown joins at the C
		# level on threads parked inside PortAudio or CTranslate2, which is
		# exactly where they are when a transcription is in flight. That is
		# what left the process alive after it had already said it was
		# shutting down.
		os._exit(code)

	def run(self):
		self.__install_signals()
		Thread(target=self.__listen_for_commands, daemon=True).start()
		Thread(target=self.__send_and_recv_data, daemon=True).start()
		Thread(target=self.__watch_parent, daemon=True).start()

		if self.whisper is None:
			# Stay alive briefly so the client can connect and receive the
			# reason, rather than seeing the process vanish with no explanation.
			time.sleep(2)
			self.send_audio_error(self.import_error or "audio stack unavailable")
			time.sleep(2)
			self.running = False
			return

		try:
			self.whisper.start()
		except Exception as e:
			self.send_log("warning", f"[STTServer]: Failed to start Whisper: {e}")
			time.sleep(2)
			self.send_audio_error(f"Could not load the '{self.model_name}' model: {e}")
			self.running = False
			return

		self.send_log("debug", "[STTServer]: Listening ...")
		while self.running:
			time.sleep(1)

		self.send_log("debug", "[STTServer]: Server shutting down complete.")

	def stop(self):
		if not self.running:
			return
		self.send_log("debug", "[STTServer]: Stopping server...")
		self.running = False

		# Stop whisper threads
		if self.whisper is not None:
			# Guarded: on the import-error path there is no model to stop, and
			# the AttributeError used to escape into the command handler's
			# catch-all, which swallowed it and left the caller thinking the
			# shutdown had run.
			try:
				self.whisper.stop()
			except Exception as e:
				self.send_log("warning", f"[STTServer]: Error stopping Whisper: {e}")

		# Close sockets
		self.__close_connection("command")
		self.__close_connection("data")


if __name__ == "__main__":
	server = STTServer()
	try:
		server.run()
	finally:
		server.shutdown()