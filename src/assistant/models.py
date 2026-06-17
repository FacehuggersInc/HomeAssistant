"""A File holding a Reference Classes for STT Processing with Whisper Models | These may be outdated or unused."""

from threading import Thread, Event as ThreadEvent
import queue
import collections
import time
import string
import numpy as np
import sounddevice as sd
import webrtcvad
import torch
from faster_whisper import WhisperModel
from socket import socket, AF_INET, SOCK_STREAM, SOL_SOCKET, SO_REUSEADDR
import sys, traceback

try:
    import noisereduce as nr
    _HAS_NOISEREDUCE = True
except Exception:
    _HAS_NOISEREDUCE = False


class WakeWhisper:
	"""
	May be Outdated to the newer implementation in assistant/whisper-process.py.

	Unused / Works as Standalone Currently.

	WakeWhisper: Real-time speech-to-text with wake word support. Using Whisper.

	Features:
		- Always-on listening with VAD (Voice Activity Detection)
		- Partial transcription for wake word detection
		- Full phrase capture after wake word
		- Optional noise reduction
		- Threaded audio processing

	Args:
		model_name (str): Whisper model name (e.g., "tiny.en").
		device (str): Device for inference ("cpu" or "cuda").
		compute_type (str): Model compute type ("int8", "float16", etc.).
		sample_rate (int): Audio sample rate (default 16000).
		vad_aggressiveness (int): VAD sensitivity (0-3).
		window_duration_ms (int): Audio window size in ms.
		context_audio_windows_start (int): Pre-context window size.
		context_audio_windows_end (int): End-context window size.
		minimum_speech_windows (int): Minimum windows for valid speech.
		wake_timeout_seconds (float): Timeout after wake word.
		wake_speech_after_timeout_extension (float): Extension after timeout.
		use_noise_reduction (bool): Enable noise reduction.
		max_queue_size (int): Max audio queue size.
		wake_words (list[str]): List of wake words.
		override_limits (bool): Override safety limits.
	"""

	def __init__(
		self,
		model_name:str="tiny.en",
		device:str="cpu",
		compute_type:str="int8",
		sample_rate:int=16000,
		vad_aggressiveness:int=3,
		window_duration_ms:int=30,
		context_audio_windows_start:int= 14,
		context_audio_windows_end:int= 10,
		minimum_speech_windows:int= 25,
		wake_timeout_seconds:float= 2.5,
		wake_speech_after_timeout_extension:float = 1.0,
		use_noise_reduction:bool=True,
		max_queue_size:int=8,
		wake_words:list[str]=[],
		override_limits:bool = False
	):
		torch.set_num_threads(5)

		#Threading
		self.running = False
		self._listen_thread = None
		self._process_thread = None
		self.stop_event = ThreadEvent()
		self.sample_check_thread = None

		#Callbacks
		self.on_wake = None
		self.on_final = None
		self.on_wake_timeout = None

		self.woke = False

		#Audio Recording
		self.__PCM_NORM_FACTOR = 32768.0
		self.audio_queue = queue.Queue(maxsize=max_queue_size)
		self.context_windows_start = max(10, context_audio_windows_start) if not override_limits else context_audio_windows_start
		self.context_windows_end = max(5, context_audio_windows_end) if not override_limits else context_audio_windows_end
		self.use_noise_reduction = (use_noise_reduction and _HAS_NOISEREDUCE) if not override_limits else use_noise_reduction
		self.sample_rate = sample_rate #16000
		self.window_duration_ms = window_duration_ms # 30 ms
		self.window_size_hz = int(sample_rate * (window_duration_ms / 1000)) #16000 * 0.3s
		self.channels = 1
		self.too_quiet_db = -35
		self.vad = webrtcvad.Vad(vad_aggressiveness)

		self.wake_words = wake_words
		self.wake_sample_amount = 4
		self.speech_timeout_start = None
		self.wake_timeout_seconds = wake_timeout_seconds
		self.wake_speech_after_timeout_extension = wake_speech_after_timeout_extension
		self.minimum_speech_windows = min(self.context_windows_start + self.context_windows_end, minimum_speech_windows) if not override_limits else minimum_speech_windows

		# Transcribing Model
		self.model_name = model_name
		self.device = device
		self.compute_type = compute_type
		self.model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)
		self.transcribe_settings = {"language": "en", "temperature": 0, "best_of": 5}


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

	def set_callbacks(self, on_wake=None, on_final=None, on_wake_timeout = None):
		self.on_wake = on_wake
		self.on_final = on_final
		self.on_wake_timeout = on_wake_timeout

	## UTIL
	def clean_text(self, text: str) -> str:
		return ''.join(ch for ch in text if ch not in string.punctuation).strip()

	def contains_wake_word(self, text: str) -> str | None:
		"""
		Check if text contains a wake word.

		Returns
		-------
		str | None
			The matched wake word (lowercase) if found, else None.
		"""
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

	## RECORDING
	def __listen_loop(self):
		connection = True
		while not self.stop_event.is_set():
			try:
				with sd.InputStream(
					samplerate=self.sample_rate,
					channels=self.channels,
					dtype="int16"
				) as stream:
					print("[Whisper]: Microphone opened.")
					connection = True
					self.__stream_loop(stream)
			except Exception as exc:
				if connection:
					connection = False
					print(f"[Whisper]: Microphone Error (likely no mic connected): \n---start---\n{traceback.format_exc()}\n---end--- ")
				time.sleep(5)

	def __wake_word_check(self, sample:bytes):
		converted = np.frombuffer(sample, dtype=np.int16).astype(np.float32) / self.__PCM_NORM_FACTOR
		segments, info = self.model.transcribe(converted, **self.transcribe_settings)
		text = " ".join(seg.text.strip() for seg in segments).strip()
		if text:
			for word in self.wake_words:  # e.g., ["clyde", "jarvis"]
				if word in text.lower():
					print(f"[Whisper]: Wake word '{word}' detected.")
					self.woke = True
					if callable(self.on_wake):
						self.on_wake(word)
					break
		self.sample_check_thread = None

	def __test_sample_for_wake(self, sample_window:list[bytes]):
		sample = b"".join(sample_window)
		self.sample_check_thread = Thread(
			target=self.__wake_word_check,
			args=[sample],
			daemon=True
		)
		self.sample_check_thread.start()

	def __stream_loop(self, stream:sd.InputStream):

		speech_window = [] #Will Store all of the frames of audio during speech

		sample_window = []
		self.sample_check_thread = None

		#Always Stores a frame of audio each iteration, will be inserted at the beginning of the speech window when speech is first detected
		pre_context = collections.deque(maxlen=self.context_windows_start)

		#Similar to pre_context, but stores the semi-silence frames after, -
		#used for extra context at the end of speech and for knowing when to actually queue up the entire speech
		end_context = collections.deque(maxlen=self.context_windows_end)

		was_speech = False #the trigger var for capturing an entire phrase

		ignore_timeout_call = False
		timeout_called = False

		end_context_windows_accumulated = 0

		last_speech_time = time.time()
		reset_timeout_time = 15.0

		## CORE LOOP
		while not self.stop_event.is_set():

			#Get Audio Window
			audio_window, _ = stream.read( self.window_size_hz )
			audio_window = audio_window[:, 0].tobytes()

			#Add Context
			pre_context.append(audio_window)

			#De-Noise The Current Frame for VAD
			if self.use_noise_reduction:
				# Convert prebuffer to float32 array
				pb_bytes = b"".join(pre_context)
				pb_np = np.frombuffer(pb_bytes, dtype=np.int16).astype(np.float32) / self.__PCM_NORM_FACTOR
				try:
					denoised = nr.reduce_noise(y=pb_np, sr=self.sample_rate)
				except Exception:
					denoised = pb_np
				denoised_last = denoised[-self.window_size_hz:]
				vad_window = (denoised_last * self.__PCM_NORM_FACTOR).astype(np.int16).tobytes()

			else:
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

			#Start Building the Speech Window
			if is_speech_in_window: #SPEECH BLOCK
				last_speech_time = time.time()

				#Trigger Was Speech (this toggles was_speech)
				if not was_speech:
					was_speech = True
					sample_window.extend( pre_context )
					speech_window.extend( pre_context ) #add start context to speech window

				# Check Sample Window Regularly for a wake Word
				if not self.woke:
					sample_window.append( audio_window )
					if not self.sample_check_thread and len(sample_window) >= self.wake_sample_amount:
						self.__test_sample_for_wake(sample_window)

				#Build Speech
				speech_window.append( audio_window )
				end_context.clear() #for a clean context, always clear, there wont be any in-between silence

				end_context_windows_accumulated = 0

				#If Woke and Speech Window is already long enough, start the timeout
				#This way, end context can start building immediately after speech
				if self.woke and len(speech_window) >= self.minimum_speech_windows:
					if self.speech_timeout_start is None:
						self.speech_timeout_start = time.time()
						print("[Whisper]: Minimum speech reached, starting timeout.")
					elif time.time() - self.speech_timeout_start >= self.wake_timeout_seconds:
						self.speech_timeout_start += self.wake_speech_after_timeout_extension
						print(f"[Whisper]: Wake speech after timeout extension added ({self.wake_speech_after_timeout_extension}s)")
					ignore_timeout_call = True #if conditions are met early, ignore timeout call

			else: #SILENCE BLOCK

				#Reset only Wake Variables
				if not was_speech and (self.woke or timeout_called):
					self.woke = False
					timeout_called = False
					ignore_timeout_call = False
					sample_window.clear()

				#If Speech Window Was Triggered
				if was_speech:

					#Start Building End Context
					end_context.append( audio_window )

					#Final Test for Wake Word if not already woken
					#(this is in case, only the wake word was said, this gives it a bigger window to be detected)
					if not self.woke:
						sample_window.append( audio_window )
						if not self.sample_check_thread and len(sample_window) >= self.wake_sample_amount:
							self.__test_sample_for_wake(sample_window)

					#Dont allow end_context to build yet, so that end context doesn't trigger finalization
					if self.woke and len(speech_window) < self.minimum_speech_windows:
						#Limit speech_window appending per speech blob to the same limit as end_context would get
						if not end_context_windows_accumulated >= self.context_windows_end:
							speech_window.append( audio_window ) #For the same reason as end_context is being built
							end_context_windows_accumulated += 1
						end_context.clear()
						
					elif self.woke and len(speech_window) >= self.minimum_speech_windows:
						#Start a timeout
						# Start timeout if not already started
						if self.speech_timeout_start is None:
							self.speech_timeout_start = time.time()

						# Check if timeout has elapsed
						if time.time() - self.speech_timeout_start >= self.wake_timeout_seconds:
							# Allow end_context to build (do NOT clear it)
							if not timeout_called and not ignore_timeout_call:
								timeout_called = True
								print("[Whisper]: Speech timeout reached.")
								if callable(self.on_wake_timeout):
									self.on_wake_timeout()

						else:
							#Limit speech_window appending per speech blob to the same limit as end_context would get
							if not end_context_windows_accumulated >= self.context_windows_end:
								speech_window.append( audio_window ) #For the same reason as end_context is being built
								end_context_windows_accumulated += 1
							end_context.clear()  # still waiting for timeout

					#If End Context is Full, Meaning All Context is There, Finalize Speech
					if self.woke and len(end_context) == self.context_windows_end:
						last_speech_time = time.time() # artificial reset the timer | used to prevent reset before speech_window can be processed

						#Build Final Byte Window
						speech = b"".join(speech_window)

						# Optionally : de-noise the entire speech
						if self.use_noise_reduction:
							try:
								utt_np = np.frombuffer(speech, dtype=np.int16).astype(np.float32) / self.__PCM_NORM_FACTOR
								utt_clean = nr.reduce_noise(y=utt_np, sr=self.sample_rate)
								speech = (utt_clean * self.__PCM_NORM_FACTOR).astype(np.int16).tobytes()
							except Exception:
								pass
						
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

						#With was_speech triggered and end_context triggered
						#were at the reset point of the full speech
						was_speech = False
						end_context.clear()
						sample_window.clear()
						speech_window.clear()
						self.woke = False
						self.speech_timeout_start = None
						timeout_called = False
						ignore_timeout_call = False


			#Whether Silence is detected or Not, Reset if too long without speech
			if not self.woke and time.time() - last_speech_time >= reset_timeout_time:
				if was_speech: print("[Whisper]: Resetting due to extended silence.") #log only if was_speech
				last_speech_time = time.time() #artificially reset the timer | no actual speech here
				was_speech = False
				end_context.clear()
				sample_window.clear()
				speech_window.clear()
				self.woke = False
				self.speech_timeout_start = None
				timeout_called = False
				ignore_timeout_call = False


	## PROCESSING
	def multi_phrase_check(self, text:str):
		phrases = text.split(".") + text.split(",")
		phrases = [phrase.strip() for phrase in phrases]
		counts = {}
		for phrase in phrases:
			if counts.get(phrase): return True
			else:
				counts[phrase] = True

		return False
	
	def __processing_loop(self):
		while not self.stop_event.is_set():
			#Get Speech Audio Window
			speech = self.audio_queue.get()
			if speech is None:
				break
			
			#Convert
			speech = np.frombuffer(speech, dtype=np.int16).astype(np.float32) / self.__PCM_NORM_FACTOR

			#Transcribe Audio
			try:
				segments, info = self.model.transcribe(speech, **self.transcribe_settings)
			except Exception as exc:
				print("[Whisper]: Transcription error:", exc)
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
			if final_text and self.clean_text(final_text) and len(final_text.split()) <= 20:
				
				if self.multi_phrase_check(final_text): return

				if callable(self.on_final):
					self.on_final(final_text, final_timestamps)

			final_text = ""

class FullUtteranceWhisper:
	"""
	Unused / Works as Standalone Currently.

	A Whisper Object Class

	Phases:
		1) Listen + VAD + optional short noise reduction -> "general recording"
		2) On VAD end, push utterance to a processing queue (processing runs in separate thread)
		3) Processing thread runs transcription.

	Callbacks:
		- on_final(text, timestamps): called for final utterance result
	"""

	def __init__(
		self,
		model_name="tiny.en",
		device="cpu",
		compute_type="int8",
		sample_rate=16000,
		window_duration_ms=30,
		vad_aggressiveness=3,
		prebuffer_ms=400,
		context_windows_end=10,
		use_noise_reduction=True,
		max_queue_size=8,
		maximum_words = 30
	):
		"""
		Parameters
		----------
		model_name : str
			faster-whisper model id or path (use tiny.en for English-only).
		device : str
			"cpu" for CPU. faster-whisper supports "cpu" for CTranslate2 backend.
		compute_type : str
			quantization type for faster-whisper (e.g., "int8", "int8_float16", "float32").
		sample_rate : int
			microphone sampling rate in Hz.
		window_duration_ms : int
			VAD frame duration in ms (10, 20, or 30 recommended).
		vad_aggressiveness : int
			webrtcvad aggressiveness (0..3). Higher is stricter on noise.
		prebuffer_ms : int
			how many ms of audio to keep before VAD start for context (e.g., 400ms).
		context_windows_end : int
			number of consecutive non-speech frames to consider end of utterance.
		use_noise_reduction : bool
			if True and noisereduce is available, apply a short denoise to buffered audio
			before VAD decision and before queuing for transcription.
		max_queue_size : int
			maximum queued utterances before dropping older ones.
		maximum_words : int
			maximum words for an understood utterance, if the utterance if over this number, it will be ignore and not returned.
		"""
		torch.set_num_threads(5)

		self.model_name = model_name
		self.device = device
		self.compute_type = compute_type

		self.__PCM_NORM_FACTOR = 32768.0
		self.sample_rate = sample_rate
		self.window_duration_ms = window_duration_ms
		self.window_size_hz = int(sample_rate * window_duration_ms / 1000)
		self.channels = 1

		self.vad = webrtcvad.Vad(vad_aggressiveness)

		self.context_windows_start = max(1, int((prebuffer_ms / window_duration_ms)))
		self.context_windows_end = context_windows_end

		self.use_noise_reduction = use_noise_reduction and _HAS_NOISEREDUCE

		self.running = False
		self._listen_thread = None
		self._process_thread = None

		# queue for utterances (raw int16 bytes)
		self.audio_queue = queue.Queue(maxsize=max_queue_size)

		# callbacks
		self.on_partial = None
		self.on_final = None

		self.maximum_words = maximum_words

		# Load faster-whisper model (CPU, quantized)
		self.model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)

		self.transcribe_settings = {
			"language" : "en",
			"temperature" : 0,
			"best_of" : 10
		}

		self.status = {
			"listening" : "stopped",
			"processing" : "stopped"
		}

		self.stop_event = ThreadEvent()

	def start(self):
		if self.stop_event.is_set():  # reset if restarting
			self.stop_event.clear()
		self._listen_thread = Thread(target=self.__listen_loop, daemon=True)
		self._process_thread = Thread(target=self.__processing_loop, daemon=True)
		self._listen_thread.start()
		self._process_thread.start()

	def stop(self):
		"""Signal all threads to stop and wait for them."""
		self.stop_event.set()
		try:
			self.audio_queue.put_nowait(None)
		except Exception:
			pass
		if self._listen_thread:
			self._listen_thread.join(timeout=2.0)
		if self._process_thread:
			self._process_thread.join(timeout=2.0)

	# ----------------------
	# Listening / VAD thread
	# ----------------------
	
	def is_too_quiet(self, audio_bytes, threshold_db=-35, sample_rate=16000):
		audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
		rms = np.sqrt(np.mean(audio**2))
		db = 20 * np.log10(rms + 1e-6)
		return db < threshold_db
	
	def __listen_loop(self):
		"""Retry opening mic every 5s until success."""
		while not self.stop_event.is_set():
			try:
				with sd.InputStream(samplerate=self.sample_rate, 
									channels=self.channels, 
									dtype="int16") as stream:
					print("[Whisper]: Microphone opened successfully.")
					self.__stream_loop(stream)
			except Exception as exc:
				print("[Whisper]: No microphone found, retrying in 5s...", exc)
				time.sleep(5)

	def __stream_loop(self, stream):
		"""
		Continuously read frames from microphone, run VAD on buffered windows,
		collect voiced frames, and enqueue utterances for processing.
		"""

		talking_frames = []
		context_buffer = collections.deque(maxlen=self.context_windows_start)
		silence_buffer = collections.deque(maxlen=self.context_windows_end)
		was_speech = False

		while not self.stop_event.is_set():

			#Get Next Slice / Frame of Audio from Microphone
			frame, _ = stream.read(self.window_size_hz)
			frame_bytes = frame[:, 0].tobytes()

			# Keep a short prebuffer of frames for context
			context_buffer.append(frame_bytes)

			if self.use_noise_reduction:

				# Convert prebuffer to float32 array
				pb_bytes = b"".join(context_buffer)
				pb_np = np.frombuffer(pb_bytes, dtype=np.int16).astype(np.float32) / self.__PCM_NORM_FACTOR
				try:
					denoised = nr.reduce_noise(y=pb_np, sr=self.sample_rate)
				except Exception:
					denoised = pb_np
				last_frame_len = self.window_size_hz
				denoised_last = denoised[-last_frame_len:]
				vad_frame_bytes = (denoised_last * self.__PCM_NORM_FACTOR).astype(np.int16).tobytes()

			else:
				vad_frame_bytes = frame_bytes

			# Run VAD on the selected frame bytes
			is_speech = False
			try:
				is_speech = self.vad.is_speech(vad_frame_bytes, sample_rate=self.sample_rate)
			except Exception:
				is_speech = False

			if is_speech:
				if not was_speech:
					was_speech = True
					# add prebuffer as initial context
					talking_frames.extend(list(context_buffer))

				# Append current frame
				talking_frames.append(frame_bytes)
				silence_buffer.clear()

			else:
				if was_speech:
					# Keep a few non-speech frames to detect end of utterance
					silence_buffer.append(frame_bytes)
					if len(silence_buffer) >= self.context_windows_end:
						# Finalize utterance: join talking_frames and enqueue
						utterance_bytes = b"".join(talking_frames)

						# Optionally run a final denoise pass on the whole utterance
						if self.use_noise_reduction:
							try:
								utt_np = np.frombuffer(utterance_bytes, dtype=np.int16).astype(np.float32) / self.__PCM_NORM_FACTOR
								utt_clean = nr.reduce_noise(y=utt_np, sr=self.sample_rate)
								utterance_bytes = (utt_clean * self.__PCM_NORM_FACTOR).astype(np.int16).tobytes()
							except Exception:
								pass

						# 🔹 New check: discard if too quiet
						if not self.is_too_quiet(utterance_bytes, threshold_db=-35, sample_rate=self.sample_rate):
							# Enqueue for processing (drop if queue full)
							try:
								self.audio_queue.put_nowait(utterance_bytes)
							except queue.Full:
								# If queue is full, drop the oldest and enqueue
								try:
									_ = self.audio_queue.get_nowait()
									self.audio_queue.put_nowait(utterance_bytes)
								except Exception:
									pass

						# reset buffers
						talking_frames = []
						silence_buffer.clear()
						was_speech = False

				else:
					# not in speech, continue listening
					pass


	# -------------------------
	# Processing / transcription
	# -------------------------
	def clean_text(self, text:str) -> str:
		return ''.join(ch for ch in text if ch not in string.punctuation).strip()
	
	def multi_phrase_check(self, text:str):
		phrases = text.split(".") if "." in text else text.split(",")
		phrases = [phrase.strip() for phrase in phrases]
		counts = {}
		for phrase in phrases:
			if counts.get(phrase): return True
			else:
				counts[phrase] = True

		return False

	def __processing_loop(self):
		"""
		Pull utterances from the queue and run transcription.
		This thread is allowed to be slower; listening thread stays responsive.
		"""

		while True:
			self.status["processing"] = "waiting"

			item = self.audio_queue.get()
			if item is None:
				# sentinel for shutdown
				break

			utterance_bytes = item

			# Convert to float32 numpy array in range [-1, 1]
			audio_np = np.frombuffer(utterance_bytes, dtype=np.int16).astype(np.float32) / self.__PCM_NORM_FACTOR

			# For shorter utterances, whisper/faster-whisper works fine directly.
			# For long utterances, consider chunking. Here we pass the full chunk.
			try:
				# Run faster-whisper transcription.
				# segments is an iterator/list of Segment objects; info contains metadata.
				self.status["processing"] = "transcribing"
				segments, info = self.model.transcribe(
					audio_np,
					**self.transcribe_settings
				)
			except Exception as exc:
				# If transcription errors, continue to next item
				print("[Whisper]: Transcription error:", exc)
				continue

			# Build final text and timestamps
			final_text_pieces = []
			final_timestamps = []

			for seg in segments:
				# Faster-whisper 'seg' typically has attributes: start, end, text, words (list)
				final_text_pieces.append(seg.text)
				# If seg.words is available, append per-word timestamps; otherwise use segment
				if hasattr(seg, "words") and seg.words:
					for w in seg.words:
						final_timestamps.append({
							"word": w.word,
							"start": float(w.start),
							"end": float(w.end)
						})
				else:
					final_timestamps.append({
						"segment_text": seg.text,
						"start": float(seg.start),
						"end": float(seg.end)
					})

			final_text = " ".join([p.strip() for p in final_text_pieces]).strip()
			if final_text and self.clean_text(final_text) and len(final_text.split(" ")) <= self.maximum_words:

				#Multi Phrase Check
				if self.multi_phrase_check(final_text): continue

				self.status["processing"] = "transcribed"
				# Emit final callback
				if callable(self.on_final):
					try:
						self.on_final(final_text, final_timestamps)
					except Exception as e:
						print(f"Final Callable Error: {e}")

	# -------------------------
	# Convenience setters
	# -------------------------
	def set_callbacks(self, on_partial=None, on_final=None):
		"""
		Set callback functions. Each receives (text, timestamps).
		"""
		self.on_partial = on_partial
		self.on_final = on_final