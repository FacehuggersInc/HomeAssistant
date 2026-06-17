from src import *

import queue
from spacy.matcher import Matcher
from word2number import w2n

SENTENCE_END_TOKENS = {'.', '!', '?', ';'}

PROCESS_REALTIMESTT = "src\\assistant\\realtimestt-process.py"
PROCESS_VOSK = "src\\assistant\\vosk-process.py"
PROCESS_WHISPER = "src\\assistant\\whisper-process.py"

class Session():
	def __init__(self, client):
		self.__client = client
		self.__queued = queue.Queue()
		self.matcher = Matcher(NLP_MODEL.vocab)
		self.is_open = False
		self.__id = f"session:{self.__client.uuid()}"

	def __enter__(self):
		self.is_open = True
		self.__client.STT.open_session()
		self.__client.TIMEOUTS.add(60 * 5, self.__client.STT.close_session, self.__id)
		self.__client.TIMEOUTS.start(self.__id)

	def __exit__(self, exc_type, exc_val, exc_tb):
		self.is_open = False
		self.__client.STT.close_session()  
		self.__client.TIMEOUTS.cancel(self.__id)

	def id(self) -> str:
		return self.__timeout_id

	def close(self):
		self.__exit__(None, None, None)

	def put(self, next_transcribed:str):
		self.__queued.put(next_transcribed)

	def wait_for_phrase(self) -> str | None:
		return self.__queued.get()
	
	def push(self):
		self.__client.STT.processing = False


class STTProcessing():
	def __init__(self, client, process:str = "whisper"):
		self.client = client
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

		self.session :Session = None
		self.route = "wake"



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

	def detect_wake_words_full(self, processed:str):
		found_skill = False
		for wake, max_words, min_words in self.client.SKILLS.wake_args:
			if wake in processed and not found_skill:
				phrase = processed.rsplit(wake, 1)[-1]
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
		phrase = processed.rsplit(wake, 1)[-1]
		if wake and phrase:
			self.client.log("info", f"[STTProcessing] Routing -> '{processed}' to {self.route}")
			Thread(target = self.process_phrase, args = [self.clean_text( phrase.strip() ), ] ).start()
		else:
			self.processing = False
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
		# matches sequences of alphabetic words (e.g., "twenty one")
		pattern = re.compile(
			r'\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|'
			r'eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|'
			r'eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|'
			r'eighty|ninety|hundred|thousand|million|and|\s)+\b', re.I
		)
		def replacer(match):
			try:
				return str(w2n.word_to_num(match.group()))
			except ValueError:
				return match.group()
		return pattern.sub(replacer, text)

	def pre_processing(self, transcribed:str):
		"""Pre process transcribed text before letting Routing take it somewhere | Punctuation will not be Processed"""
		if not self.client.TTS.is_speaking():
			if not self.processing:
				self.processing = True
				self.client.ASSIST_STATUS = "THINKING"
				processed = self.words_to_numbers(transcribed)
				self.routing( processed )



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
	def send_command(self, command:str):
		for _ in range(10):
			try:
				with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
					s.connect( (self.host, self.ports["command"]) )
					s.sendall( f"server:{command}".encode("utf-8") )
					return
			except ConnectionRefusedError:
				time.sleep(0.5)
		self.client.log("error", "[STTProcessing] Could not connect to STT process to send command")

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
									if not self.client.ASSIST_STATUS == "LISTENING":
										self.woke_with = data.strip()
										self.client.ASSIST_STATUS = "LISTENING"

								case "wait":
									self.client.ASSIST_STATUS = "LIVE"
									self.processing = False
									if self.woke_with: self.woke_with = None

									
						except: pass
			
			except Exception as ex:
				self.client.simple_notify(
					"assistant",
					"Assistant: LISTENING ERROR",
					str(ex)
				)
				time.sleep(1)  # avoid busy loop



	## PROCESS
	def start(self):
		if self.process is None or self.process.poll() is not None:
			
			wake_word_str = ", ".join(w[0] for w in self.client.SKILLS.wake_args)
			self.process = subprocess.Popen(["python", self.__process_path, wake_word_str])

			self.listening = True

			self.client.THREADS.create("__stt_receiver_thread", self.__listen_for_stt_data)
			self.client.THREADS.start("__stt_receiver_thread")

	def kill(self):
		"""Force kill subprocess"""
		if self.process and self.process.poll() is None:
			self.process.terminate()
			self.listening = False

	def stop(self):
		"""Graceful stop via socket command"""
		try:
			self.send_command("STOP")

			self.client.simple_notify(
				"assistant",
				"Assistant: STT",
				"Stopping Process"
			)
			
			self.listening = False
		except Exception as ex:
			self.client.simple_notify(
				"assistant",
				"Assistant: STOP ERROR",
				str(ex)
			)