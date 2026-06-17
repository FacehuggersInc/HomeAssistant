from src import *

class WordnikAPI():
	def __init__(self, app):
		self.app : Client = app

		self.load_data()
		self.BASE = 'http://api.wordnik.com/v4'
		self.URLS = {
			'wotd' : f"{self.BASE}/words.json/wordOfTheDay"
		}

	def url(self, name:str) -> str | None:
		url = self.URLS.get(name)
		return url

	def load_data(self) -> None:
		file = find_dotenv()
		load_dotenv(file)
		self.__LOADED = {
			"key" : os.getenv("WORDNIK")
		}

	def get_wotd(self) -> None:
		response = requests.get(
			self.url("wotd"),
			headers = {
				'Accept' : 'application/json'
			},
			params = {
				"api_key" : self.__LOADED['key']
			}
		)

		if response and response.status_code == 200:
			return json.loads(response.text)
		else:
			return None