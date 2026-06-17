from threading import Thread, Event as ThreadEvent
import re
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
        override_limits (bool): Override stability limits (e.g min, max's for certain vars).

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
		maximum_speech_windows:int= 267,
		wake_sample_windows:int=5,
		wake_timeout_seconds:float= 2.5,
		wake_speech_after_timeout_extension:float = 1.0,
		max_wake_speech_extensions:int = 2,
		use_noise_reduction:bool=True,
		max_queue_size:int=8,
		wake_words:list[str]=[],
		override_limits:bool = False,
		initial_mode : str = "wake" # "wake" or "passthrough"
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
		self.on_timeout = None
		self.on_voice_activity = None

		self.switching = False
		self.mode = initial_mode  # "wake" or "session"
		self.woke = False

		#Audio Recording
		self.__PCM_NORM_FACTOR = 32768.0
		self.audio_queue = queue.Queue(maxsize=max_queue_size)
		self.context_windows_start = max(10, context_audio_windows_start) if not override_limits else context_audio_windows_start
		self.context_windows_end = max(5, context_audio_windows_end) if not override_limits else context_audio_windows_end
		self.use_noise_reduction = use_noise_reduction
		self.sample_rate = sample_rate #16000
		self.window_duration_ms = window_duration_ms # 30 ms
		self.window_size_hz = int(sample_rate * (window_duration_ms / 1000)) #16000 * 0.3s
		self.channels = 1
		self.too_quiet_db = -35
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

	def set_callbacks(self, on_wake=None, on_final=None, on_timeout = None, on_voice_activity=None):
		self.on_wake = on_wake
		self.on_final = on_final
		self.on_timeout = on_timeout
		self.on_voice_activity = on_voice_activity

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

	def switch_mode(self, mode:str):
		if mode not in ["wake", "passthrough"]:
			mode = "wake"
		self.mode = mode
		self.switching = True

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
		if self.sample_check_thread and self.sample_check_thread.is_alive():
			#Already running a check
			return
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
			nonlocal self

			was_speech = False
			last_speech_time = time.time()
			end_context.clear()
			sample_window.clear()
			speech_window.clear()
			self.woke = False
			self.speech_timeout_start = None
			timeout_called = False
			ignore_timeout_call = False
			end_context_windows_accumulated = 0
			extensions_added = 0
			speech_cutoff = False

		## CORE LOOP
		while not self.stop_event.is_set():

			try:
				#Get Audio Window
				audio_window, _ = stream.read( self.window_size_hz )
				audio_window = audio_window[:, 0].tobytes()

				#If Switching Modes, Reset Everything | This allows clean transitions between modes
				if self.switching:
					reset_all()
					self.switching = False
					print('[Whisper]: Mode switched, resetting internal state.')

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
						print('[Whisper]: Mode switched, resetting internal state. (Fail Safe : Wake)')
						continue

					if is_speech_in_window and not speech_cutoff: #SPEECH BLOCK
						last_speech_time = time.time()

						#If Timeout reached, but Audio is still being detected as speech, force speech window to end
						if not self.speech_timeout_start is None \
						and time.time() - self.speech_timeout_start >= self.wake_timeout_seconds \
						and extensions_added >= self.max_wake_speech_extensions:
							speech_window.append( audio_window )
							speech_cutoff = True
							print("[Whisper]: Speech cutoff triggered due to max timeout extensions.")
							continue

						#Force Reset if too long, For Example, Music or TV may trigger VAD continuously. 
						if not self.woke and len(speech_window) * (self.window_duration_ms / 1000) >= speech_window_accumulation_limit:
							print("[Whisper]: Speech window accumulation limit reached, resetting.")
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
							if not self.sample_check_thread and len(sample_window) >= self.wake_sample_windows:
								self.__test_sample_for_wake(sample_window)

						#Build Speech
						speech_window.append( audio_window )
						end_context.clear() #for a clean context, always clear, there wont be any in-between silence

						#Reset Per Speech Blob End Context Accumulation Counter
						end_context_windows_accumulated = 0

						#If Woke and Speech Window is already long enough, start the timeout
						#This way, end context can start building immediately after speech
						if self.woke and len(speech_window) >= self.minimum_speech_windows:
							if self.speech_timeout_start is None:
								self.speech_timeout_start = time.time()
								print("[Whisper]: Minimum speech reached, starting timeout.")

							elif time.time() - self.speech_timeout_start >= self.wake_timeout_seconds \
							and extensions_added < self.max_wake_speech_extensions:
								self.speech_timeout_start += self.wake_speech_after_timeout_extension
								extensions_added += 1
								print(f"[Whisper]: Wake speech after timeout extension added ({self.wake_speech_after_timeout_extension}s)")
							ignore_timeout_call = True #if conditions are met early, ignore timeout call

					else: #SILENCE BLOCK
						finalize = speech_cutoff #finalize if speech cutoff triggered

						#If Speech Window Was Triggered
						if was_speech and not finalize:

							#Start Building End Context
							end_context.append( audio_window )

							#Final Test for Wake Word if not already woken
							#(this is in case, only the wake word was said, this gives it a bigger window to be detected)
							if not self.woke:
								sample_window.append( audio_window )
								if not self.sample_check_thread and len(sample_window) >= self.wake_sample_windows:
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
										print("[Whisper]: Speech timeout reached.")
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
								print("[Whisper]: End context full, finalizing speech window.")

						if finalize:
							print("[Whisper]: Finalizing speech window.")
							last_speech_time = time.time() # artificial reset the timer | used to prevent reset before speech_window can be processed

							#Build Final Byte Window
							speech_window.extend( end_context ) #add end context to speech window
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
							reset_all()

				## PASSTHROUGH MODE | Does not require to be Woken, Will Build Speech window as Normal
				elif self.mode == "passthrough":
					#Failsafe on first switch to this Mode, Reset Everything
					if self.switching:
						reset_all()
						self.switching = False
						print('[Whisper]: Mode switched, resetting internal state. (Fail Safe : Passthrough)')
						continue

					if is_speech_in_window: #SPEECH BLOCK
						last_speech_time = time.time()

						#Force Reset if too long, For Example, Music or TV may trigger VAD continuously. 
						if not self.woke and len(speech_window) * (self.window_duration_ms / 1000) >= speech_window_accumulation_limit:
							print("[Whisper]: Speech window accumulation limit reached, resetting.")
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
						speech_window.append( audio_window )
						end_context.clear() #for a clean context, always clear, there wont be any in-between silence

					else: #SILENCE BLOCK
						if was_speech:
							#Start Building End Context
							end_context.append( audio_window )

							#If End Context is Full, Meaning All Context is There, Finalize Speech
							if len(end_context) >= self.context_windows_end:
								last_speech_time = time.time() # artificial reset the timer | used to prevent reset before speech_window can be processed

								#Build Final Byte Window
								speech_window.extend( end_context ) #add end context to speech window
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
								reset_all()

				#Whether Speech is detected or Not, Reset if too long without speech
				if time.time() - last_speech_time >= reset_timeout_time:
					if was_speech: print("[Whisper]: Resetting due to extended silence.") #log only if was_speech
					reset_all()
					if callable(self.on_timeout):
						self.on_timeout("extended_timeout")

			except Exception as exc:
				print("[Whisper]: Error in stream loop:", f"{exc}\n---start---\n{traceback.format_exc().strip()}\n---end---")
				reset_all()
				continue

		## ON END
		stream.close()
		reset_all()
		print("[Whisper]: Microphone closed.")

	## PROCESSING
	def multi_phrase_check(self, text: str) -> bool:
		# Split on common sentence boundaries
		phrases = re.split(r'[.,;!?]\s*|\n', text)
		phrases = [self.clean_text(phrase.lower()) for phrase in phrases if phrase.strip()]
		counts = {}

		for phrase in phrases:
			if not phrase or phrase in self.wake_words or phrase in [w.lower() for w in self.wake_words]:
				continue
			if len(phrase.split()) < 2:  # Ignore very short phrases
				continue
			counts[phrase] = counts.get(phrase, 0) + 1

		# If any phrase appears more than once, flag as hallucination
		if any(count > 1 for count in counts.values()):
			return True

		# Check for long repeated single word (e.g., "hello hello hello hello")
		words = text.lower().split()
		word_counts = {}
		for word in words:
			word = self.clean_text(word)
			if not word or word in [w.lower() for w in self.wake_words]:
				continue
			word_counts[word] = word_counts.get(word, 0) + 1
		if any(count > 5 for count in word_counts.values()):  # Threshold can be tuned
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
			print("[Whisper]: Processing speech window...")

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
				
				if self.multi_phrase_check(final_text):
					return

				print(f"[Whisper]: Final Transcription: {final_text}")

				if callable(self.on_final):
					self.on_final(final_text, final_timestamps)

			final_text = ""



class STTServer:
	def __init__(self, host="127.0.0.1", command_port=65432, data_port=65433):
		self.host = host
		self.ports = {"command": command_port, "data": data_port}
		self.running = True
		self.connections: dict[str, socket] = {"command": None, "data": None}

		wake_words = []
		if len(sys.argv) > 1:
			wake_words = [w.strip() for w in sys.argv[1].split(",")]
		if not wake_words: wake_words = ["alexa"]

		self.whisper = WakeWhisper(
			vad_aggressiveness=3,
			window_duration_ms=30,
			context_audio_windows_start = 14,
			context_audio_windows_end = 5,
			minimum_speech_windows = 20,
			wake_timeout_seconds = 3.5,
			use_noise_reduction=True,
			wake_words = wake_words
		)
		self.whisper.set_callbacks(
			on_final=self.process_transcribed,
			on_wake = self.trigger_wake,
			on_timeout = self.trigger_wait,
			on_voice_activity = self.send_voice_activity
		)
	

	## EVENTS
	def send_voice_activity(self, level:float):
		if self.connections["data"]:
			try:
				self.connections["data"].sendall(
					f"host:voice_activity:{level:.3f}".encode("utf-8")
				)
			except Exception:
				print("[STTServer]: Lost transcript connection.")
				self.__close_connection("data")

	def trigger_wake(self, wake_word:str):
		if self.connections["data"]:
			try:
				self.connections["data"].sendall(
					f"host:woke:{wake_word}".encode("utf-8")
				)
			except Exception:
				print("[STTServer]: Lost transcript connection.")
				self.__close_connection("data")

	def trigger_wait(self, type:str):
		if self.connections["data"]:
			try:
				self.connections["data"].sendall(
					f"host:wait:{type}".encode("utf-8")
				)
			except Exception:
				print("[STTServer]: Lost transcript connection.")
				self.__close_connection("data")

	def process_transcribed(self, transcribed: str, timestamps: any):
		if self.connections["data"] and transcribed.strip():
			try:
				self.connections["data"].sendall(
					f"host:transcribe:{transcribed.lower()}".encode("utf-8")
				)
			except Exception:
				print("[STTServer]: Lost transcript connection.")
				self.__close_connection("data")


	## CORE
	def __close_connection(self, which: str):
		"""Close a specific socket connection safely."""
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
			print("[STTServer]: Listening for commands...")
			while self.running:
				conn, addr = s.accept()
				with conn:
					print("[STTServer]: Command connection from", addr)
					try:
						data = conn.recv(1024)
						if not data:
							break

						raw = data.decode("utf-8").strip()
						to, command = raw.split(":")
						if to != "server":
							continue

						if command == "STOP":
							print("[STTServer]: Received STOP command.")
							self.stop()
							break
						
						elif command == "START_WAKE":
							self.whisper.switch_mode("wake")
							print("[STTServer]: Switched Whisper to WAKE mode.")

						elif command == "START_PASSTHROUGH":
							self.whisper.switch_mode("passthrough")
							print("[STTServer]: Switched Whisper to PASSTHROUGH mode.")

					except Exception as e:
						print("[STTServer]: Command Error:", e)

	def __send_and_recv_data(self): 
		with socket(AF_INET, SOCK_STREAM) as s:
			s.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
			s.bind((self.host, self.ports["data"]))
			s.listen(1)
			print("[STTServer]: Waiting for transcript connection...")
			conn, addr = s.accept()
			if conn:
				print("[STTServer]: Data connection from", addr)
				self.connections["data"] = conn
				try:
					conn.sendall(b"host:notify:Ready!")
				except Exception:
					self.__close_connection("data")

	def run(self):
		Thread(target=self.__listen_for_commands, daemon=True).start()
		Thread(target=self.__send_and_recv_data, daemon=True).start()
		self.whisper.start()

		print("[STTServer]: Listening ...")
		while self.running:
			time.sleep(1)

		print("[STTServer]: Server shutting down complete.")

	def stop(self):
		"""Stop everything cleanly (called from STOP command)."""
		if not self.running:
			return
		print("[STTServer]: Stopping server...")
		self.running = False

		# Stop whisper threads
		self.whisper.stop()

		# Close sockets
		self.__close_connection("command")
		self.__close_connection("data")



if __name__ == "__main__":
	server = STTServer()
	server.run()