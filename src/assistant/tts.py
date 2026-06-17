from src import *

import io
from typing import Iterator

from elevenlabs import stream as elevenstream
from elevenlabs import play as elevenplay
from elevenlabs.client import ElevenLabs
from elevenlabs.types import Voice

class TTSProcessing():
	def __init__(self, client):
		load_dotenv()
		self.client = client

		self.KEY = os.getenv("ELEVENLABS_KEY")
		self.elevenlabs = ElevenLabs(api_key = self.KEY)
		self.voices, self.voice_ids = self.get_voices()
		self.names = list(self.voices.keys())
		self.default_voice = self.voices[ "Mark - Natural Conversations" ]

		self.speaking = False



	## AUDIO
	def __convert_audio_to_buffer(self, audio:Iterator[bytes]) -> BytesIO:
		buffer = io.BytesIO()
		for chunk in audio:
			if chunk:
				buffer.write(chunk)
		return buffer

	def __play_audio(self, audio:Iterator[bytes]):
		self.speaking = True
		elevenplay( audio )
		time.sleep(1)
		self.speaking = False

	def __play_tts(self, text:str):
		audio = self.get_audio(text)
		self.__play_audio(audio)

	def stream_audio(self, text:str, voice_id:str = None, model_id:str = 'eleven_flash_v2_5'):
		voice_id = voice_id if voice_id else self.default_voice.voice_id
		stream = self.elevenlabs.text_to_speech.stream(
			text = text,
			voice_id = voice_id,
			model_id = model_id
		)
		self.speaking = True
		elevenstream( stream )
		self.speaking = False



	## HELPERS
	def is_speaking(self) -> bool:
		return self.speaking



	## API
	def get_voices(self) -> tuple[dict[str, Voice], dict[str, Voice]]:
		voices = {}
		voices_id = {}
		response = self.elevenlabs.voices.search()
		for voice in response.voices:
			voices[voice.name] = voice
			voices_id[voice.voice_id] = voice

		return voices, voices_id
	
	def get_audio(self, text:str, auto_play:bool = False, voice_id:str = None, model_id:str = 'eleven_flash_v2_5', format:str = "mp3_44100_128") -> Iterator[bytes]:
		voice_id = voice_id if voice_id else self.default_voice.voice_id
		audio = self.elevenlabs.text_to_speech.convert(
			text = text,
			enable_logging = True,
			voice_id = voice_id,
			model_id = model_id,
			output_format = format
		)

		if auto_play: 
			self.__play_audio(audio)

		return audio
	


	## INTERFACE
	def play(self, text:str = None, audio:list[bytes] = None, thread:bool = True):
		"""Ask ElevenLabs to generate the audio OR use already generated audio(ElevenLabs Audio Only), plays locally."""
		if text:
			if thread:
				Thread(target = self.__play_tts, name=f"__tts_thread({text[:10]})" , args = [text, ]).start()
			else:
				self.__play_tts(text)
		elif not text and audio:
			if thread:
				Thread(target = self.__play_audio, args = [audio, ]).start()
			else:
				self.__play_audio(audio)

	def stream(self, text:str, thread:bool = True):
		"""Ask ElevenLabs to generate the audio from the text, then stream the audio back, playing audio in chunks."""
		if thread: 
			Thread(target = self.stream_audio, args = [text, ]).start()
		else: 
			self.stream_audio(text)