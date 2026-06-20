from src import *

REPEAT_STATES = Literal["track", "context", "off"]

class SpotifyAPI():
	def __init__(self, app):

		self.app : Client = app

		self.load_data()

		self.__NAME = "SPOT"

		self.URLS = {
			"auth" : "https://accounts.spotify.com/authorize",
			"api"  : "https://api.spotify.com/v1",
			"token": "https://accounts.spotify.com/api/token"
		}

		self.SCOPES = [
			'playlist-modify-private',
			'playlist-modify-public',
			'user-read-playback-state',
			'user-modify-playback-state',
			'user-read-currently-playing'
		]

		self.attempted_requests = 0
		self.failed_requests = 0
		self.total_successful_requests = 0
		self.last_received_data = None

		self.__auth = None
		self.__last_request_time = time.time()

		is_token = self.update_access_token()



	## HELPERS
	def url(self, name:str, endpoint:str = None) -> str | None:
		url = self.URLS.get(name)
		if url and endpoint:
			url += endpoint
		return url
	
	def dump(self, obj, file) -> None:
		with open(file, "w") as f:
			json.dump(obj, f, indent=4)

	def get_auth_header(self):
		auth_header = base64.urlsafe_b64encode( (self.__LOADED['id'] + ":" + self.__LOADED['secret']).encode() )
		return {
			"Content_type" : 'application/x-www-form-urlencoded',
			"Authorization" : f"Basic {auth_header.decode("ascii")}"
		}
	
	def get_access_header(self):
		return {
			"Content_type" : 'application/x-www-form-urlencoded',
			"Authorization" : f"Bearer   {self.__LOADED['access']}"
		}
	
	def load_data(self) -> None:
		file = find_dotenv()
		load_dotenv(file)

		self.__LOADED = {
			"file" : file,
			"id" : os.getenv("CLIENT_ID"),
			"secret" : os.getenv("CLIENT_SECRET"),
			"access" : os.getenv("ACCESS_TOKEN"),
			"user_auth" : os.getenv("USER_AUTH")
		}

		expires = os.getenv("EXPIRES")
		if expires:
			self.__LOADED['expires'] = float(expires)

		self.app.log_event("info", self.__LOADED)

	def get(self, url:str, params:dict = None, headers:dict = None, data:dict = None, wait_time:int = 1) -> None | requests.Response:
		current = time.time()
		while current - self.__last_request_time < wait_time:
			current = time.time()

		if self.failed_requests >= 5: 
			self.app.log_event("info", f'[{self.__NAME}][GET Request:Failed]: More Than 5 Failed Requests in a Row. Please wait and then send a successful request')
			self.failed_requests = 0
			return None

		try:
			self.attempted_requests += 1
			response : requests.Response = requests.get(url, headers=headers, params=params, data=data)
			if response:
				self.last_status = response.status_code
				if int(self.last_status) == 200:
					self.total_successful_requests += 1
					#self.app.log_event("info", f'[{self.__NAME}][GET Request:Succeeded]: Status {self.last_status}\t* Given URL" {url}\t* Given PARAMS: {params}\t* Given DATA: {data}')
					self.failed_requests = 0
					self.__last_request_time = current
					return response
				else:
					self.app.log_event("info", f'[{self.__NAME}][GET Request:Failed]: Status {self.last_status} {response.reason}\t* Given URL" {url}\t* Given HEADERS: {headers}\t* Given PARAMS: {params}\t* Given DATA: {data}')
					self.failed_requests += 1
					self.__last_request_time = current
					return response
			else:
				self.app.log_event("info", f'[{self.__NAME}][GET Request:Failed] Reason: No Response\t* Given URL: {url}\t* Given HEADERS: {headers}\t* Given PARAMS: {params}\t* Given DATA: {data}')
				self.failed_requests += 1
				self.__last_request_time = current
				return None
		except ConnectionError:
			self.app.log_event("info", f'[{self.__NAME}][GET Request:Failed] Reason: Connection Error\t* Given URL: {url}\t* Given HEADERS: {headers}\t* Given PARAMS: {params}\t* Given DATA: {data}')
			self.failed_requests += 1
			self.__last_request_time = current
			return None
		
	def post(self, url:str, params:dict = None, headers:dict = None, data:dict = None, json:dict = None, wait_time:int = 1) -> None | requests.Response:
		current = time.time()
		while current - self.__last_request_time < wait_time:
			current = time.time()

		if self.failed_requests >= 5: 
			self.app.log_event("info", f'[{self.__NAME}][POST Request:Failed]: More Than 5 Failed Requests in a Row. Please wait and then send a successful request')
			self.failed_requests = 0
			return None

		try:
			self.attempted_requests += 1
			response : requests.Response = requests.post(url, headers=headers, params=params, data=data, json=json)
			if response:
				self.last_status = response.status_code
				if int(self.last_status) == 200:
					self.total_successful_requests += 1
					#self.app.log_event("info", f'[{self.__NAME}][GET Request:Succeeded]: Status {self.last_status}\t* Given URL" {url}\t* Given PARAMS: {params}\t* Given DATA: {data}')
					self.failed_requests = 0
					self.__last_request_time = current
					return response
				else:
					self.app.log_event("info", f'[{self.__NAME}][POST Request:Failed]: Status {self.last_status} {response.reason}\t* Given URL" {url}\t* Given PARAMS: {params}\t* Given DATA: {data}')
					self.failed_requests += 1
					self.__last_request_time = current
					return response
			else:
				self.app.log_event("info", f'[{self.__NAME}][POST Request:Failed] Reason: No Response\t* Given URL: {url}\t* Given HEADERS: {headers}\t* Given PARAMS: {params}\t* Given DATA: {data}')
				self.failed_requests += 1
				self.__last_request_time = current
				return None
		except ConnectionError:
			self.app.log_event("info", f'[{self.__NAME}][POST Request:Failed] Reason: Connection Error\t* Given URL: {url}\t* Given HEADERS: {headers}\t* Given PARAMS: {params}\t* Given DATA: {data}')
			self.failed_requests += 1
			self.__last_request_time = current
			return None
	
	def put(self, url:str, params:dict = None, headers:dict = None, data:dict = None, wait_time:int = 1) -> None | requests.Response:
		current = time.time()
		while current - self.__last_request_time < wait_time:
			current = time.time()

		if self.failed_requests >= 5: 
			self.app.log_event("info", f'[{self.__NAME}][PUT Request:Failed]: More Than 5 Failed Requests in a Row. Please wait and then send a successful request')
			self.failed_requests = 0
			return None

		try:
			self.attempted_requests += 1
			response : requests.Response = requests.put(url, headers=headers, params=params, data=data)
			if response:
				self.last_status = response.status_code
				if int(self.last_status) == 200:
					self.total_successful_requests += 1
					#self.app.log_event("info", f'[{self.__NAME}][GET Request:Succeeded]: Status {self.last_status}\t* Given URL" {url}\t* Given PARAMS: {params}\t* Given DATA: {data}')
					self.failed_requests = 0
					self.__last_request_time = current
					return response
				else:
					self.app.log_event("info", f'[{self.__NAME}][PUT Request:Failed]: Status {self.last_status} {response.reason}\t* Given URL" {url}\t* Given PARAMS: {params}\t* Given DATA: {data}')
					self.failed_requests += 1
					self.__last_request_time = current
					return response
			else:
				self.app.log_event("info", f'[{self.__NAME}][PUT Request:Failed] Reason: No Response\t* Given URL: {url}\t* Given HEADERS: {headers}\t* Given PARAMS: {params}\t* Given DATA: {data}')
				self.failed_requests += 1
				self.__last_request_time = current
				return None
		except ConnectionError:
			self.app.log_event("info", f'[{self.__NAME}][PUT Request:Failed] Reason: Connection Error\t* Given URL: {url}\t* Given HEADERS: {headers}\t* Given PARAMS: {params}\t* Given DATA: {data}')
			self.failed_requests += 1
			self.__last_request_time = current
			return None

	## SOCKET
	def __kill_server_thread(self, pid:int):
		timeout = time.time() + 5
		while True:
			if time.time() > timeout:
				#Kill Server
				try:
					os.kill(int(pid), signal.SIGTERM)
				except Exception as e:
					print(f"Failed to kill: {e}")

				break

	def __socket_auth_receive_server(self):
		# Create a socket object
		server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

		# Bind to an address and port
		server_socket.bind(('localhost', 3333))

		# Start listening for connections
		server_socket.listen(1)

		# Accept a connection
		client_socket, address = server_socket.accept()
		self.app.log_event("info", f"[SPOT][SOCKET]: Connection established with {address}")

		data_buffer = []
		while True:
			data = client_socket.recv(1024)  # Buffer size is 1024 bytes
			if not data:
				# If no more data, exit the loop
				break
			data_buffer.append(data)

		# Combine all parts of the data
		full_data = b''.join(data_buffer)

		parts = full_data.decode().split('|')

		pid = parts[0].strip()
		Thread(target=self.__kill_server_thread, args=[pid,]).start()

		self.last_received_data = parts[1].strip()
		self.app.log_event("info", f'[SPOT][SOCKET]: Received: {self.last_received_data}')

		# Close the sockets
		client_socket.close()
		server_socket.close()

	def start_socket(self):
		Thread(target = self.__socket_auth_receive_server).start()



	## AUTH
	def update_access_token(self) -> bool:

		## NEW / RENEW
		if not self.__LOADED.get("expires") or self.__LOADED['expires'] <= time.time():
			#self.app.log_event("info", "[SPOT] Getting New Access Token ...")

			#Start Flask Webserver (to receive code from callback)
			self.app.log_event("info", "[SPOT] Starting Auth Server ...")
			os.system("start cmd /c python server\\main.py")

			#Start Socket Server (to Receive via Socket)
			self.start_socket()

			#Open Website
			self.app.log_event("info", "[SPOT] Auth Code <False>, getting code ...")

			#Create Scope String
			scope = ''
			for scope_string in self.SCOPES:
				scope += f' {scope_string}'

			params = {
				'client_id': self.__LOADED['id'],
				'response_type': 'code',
				'redirect_uri': 'http://localhost:3000',
				'scope': scope.strip()
			}
			webbrowser.open(f'{self.url('auth')}?{urlencode( params )}')

			#Hold until data received
			while self.last_received_data == None:
				time.sleep(0.1)

			if self.last_received_data:
				self.app.log_event("info", "[SPOT] Auth Code Gained.")
				self.__auth = self.last_received_data

			#Make / Load Request
			response = self.post(
				self.url("token"),
				headers = self.get_auth_header(),
				data = {
					'grant_type': 'authorization_code',
					'code': self.__auth,
					'redirect_uri': 'http://localhost:3000',
				}
			)

			if response:
				response = json.loads( response.text )
				if response.get("access_token"):

					#Save Token / Time
					expires = time.time() + response['expires_in']
					token = response["access_token"]

					self.__LOADED['user_auth'] = self.__auth
					set_env_key(
						self.__LOADED['file'],
						"USER_AUTH",
						self.__LOADED['user_auth']
					)

					self.__LOADED['access'] = token
					set_env_key(
						self.__LOADED['file'],
						"ACCESS_TOKEN",
						self.__LOADED["access"]
					)
					
					self.__LOADED['expires'] = expires
					set_env_key(
						self.__LOADED['file'],
						"EXPIRES",
						str(self.__LOADED["expires"])
					)

					return True
				else:
					#self.app.log_event("info", f"[SPOT] No Access Token... \t{response}")
					return False
			else:
				return False
		
		## FRESH
		elif time.time() < self.__LOADED['expires']:
			#self.app.log_event("info", "[SPOT] Already Have Fresh Token.")
			return True
		
		## FAILED
		else:
			#self.app.log_event("info", "[SPOT] Failed to get Token ...")
			return False



	## LIVE PLAYER
	def set_repeat_player(self, state:REPEAT_STATES):
		self.update_access_token()

		response = self.put(
			self.url('api', "/me/player/repeat"),
			headers = self.get_access_header(),
			params = {
				"state" : state
			}
		)

		if response and response.status_code == 204:
			return True
		else:
			return False
		
	def toggle_shuffle_player(self, state:bool):
		self.update_access_token()

		response = self.put(
			self.url('api', "/me/player/shuffle"),
			headers = self.get_access_header(),
			params = {
				"state" : state
			}
		)

		if response and response.status_code == 204:
			return True
		else:
			return False

	def play_player(self, context_uri:str, position_ms:int = 0) -> bool:
		self.update_access_token()

		response = self.put(
			self.url('api', "/me/player/play"),
			headers = self.get_access_header(),
			params = {
				"context_uri" : context_uri,
				"position_ms" : position_ms
			}
		)

		if response and response.status_code == 204:
			return True
		else:
			return False
		
	def pause_player(self) -> bool:
		self.update_access_token()

		response = self.put(
			self.url('api', "/me/player/pause"),
			headers = self.get_access_header()
		)

		if response and response.status_code == 204:
			return True
		else:
			return False
		
	def seek_player(self, position_ms:int) -> bool:
		self.update_access_token()

		response = self.put(
			self.url('api', "/me/player/seek"),
			headers = self.get_access_header(),
			params = {
				"position_ms" : position_ms
			}
		)

		if response and response.status_code == 204:
			return True
		else:
			return False
		
	def next_player(self) -> bool:
		self.update_access_token()

		response = self.post(
			self.url('api', "/me/player/next"),
			headers = self.get_access_header()
		)

		if response and response.status_code == 204:
			return True
		else:
			return False
		
	def prev_player(self) -> bool:
		self.update_access_token()

		response = self.post(
			self.url('api', "/me/player/previous"),
			headers = self.get_access_header()
		)

		if response and response.status_code == 204:
			return True
		else:
			return False

	def get_playing_track(self)-> dict:
		self.update_access_token()
		self.app.log_event('info', '[SPOT] Getting Playing Track')

		response = self.get(
			self.url('api', "/me/player/currently-playing"),
			headers = self.get_access_header(),
			params = {
				"market" : "US"
			}
		)

		if response and response.status_code == 200:
			return json.loads(response.text)
		else:
			return None

	def get_playback_state(self) -> dict:
		self.update_access_token()
		#self.app.log_event('info', '[SPOT] Getting Player Playback State')

		response = self.get(
			self.url('api', f"/me/player"),
			headers = self.get_access_header(),
			params = {
				"market" : "US"
			}
		)

		if response and response.status_code == 200:
			return json.loads(response.text)
		else:
			return None



	## PLAYLIST
	def add_to_playlist(self, playlist_id, uris:str) -> bool:
		"""Adds to a playlist"""
		self.update_access_token()
		self.app.log_event("info", f"[SPOT] Adding {uris} to Playlist {playlist_id} ...")

		#Init Request
		response = requests.post(
			url = f'https://api.spotify.com/v1/playlists/{playlist_id}/tracks',
			headers = self.get_access_header(),
			params = {
				'uris' : uris
			}
		)

		if response and response.status_code == 201:
			return True
		else:
			self.app.log_event("info", f"[SPOT] Failed to add {uris} ...")
			return False

	def get_genres_from_artists(self, artists:list[dict]) -> dict:
		"""Does not make API request for artists, use get_artists_data func first"""
		self.app.log_event("info", "[SPOT] Grabbing Genres from Artists ...")

		#Assign Genres to Artist ID
		genres_dict = {}
		for artist in artists:
			genres = []
			for genre in artist['genres']:
				if not genre in genres:
					genres.append( genre )

			genres_dict[artist['id']] = genres

		#Get All Genres
		genres_dict["all_genres"] = []
		for key in genres_dict:
			for genre in genres_dict[key]:
				if not genre in genres_dict["all_genres"]:
					genres_dict["all_genres"].append( genre )

		return genres_dict

	def get_artists_data(self, artist_ids:list[str]) -> list:
		"""Returns the data for each artist in the list. artist_ids can be a list of "name,id" or just "id" """
		self.update_access_token()

		self.app.log_event("info", "[SPOT] Getting Artists Data ...")

		artists = []

		#Clean Ids
		ids = []
		for item in artist_ids:
			id = None
			if len(item.split(',')) > 1:
				id = item.split(',')[-1]
			else:
				id = item
			
			id = id.strip()
			ids.append(id)

		completed = 0
		to_complete = len(ids)
		offset = 0

		while completed != to_complete:
			offset_ids = ids[offset:offset + 50]
			if len(offset_ids) <= 0: break

			self.app.log_event("info", f'[SPOT] Completed ({completed} / {to_complete})')

			id_string = ''
			for id in offset_ids:
				if not id == offset_ids[-1]:
					id_string += f'{id},'
				else:
					id_string += f'{id}'

			#Request
			response = self.get(
				self.url('api', '/artists'),
				headers = self.get_access_header(),
				params = {
					"ids" : id_string
				}
			)

			if response:
				to_add = []
				items = json.loads( response.text )
				for item in items['artists']:
					to_add.append( item )

			offset += 50
			completed += len( to_add )
			artists += to_add

		return artists

	def get_playlist_artists(self, playlist_id:str, include_colab_artists:bool = False) -> list:
		"""Gets Artists Name and Id in <name,id> format, returned in a list"""
		self.update_access_token()

		self.app.log_event("info", "[SPOT] Getting Artists ...")

		looped = False
		artists = []
		offset = 0

		#Init Request
		response = self.get(
			self.url('api', f"/playlists/{playlist_id}/tracks"),
			headers = self.get_access_header(),
			params = {
				"fields" : "next,items(track(album, artists, id))",
				"market" : "US",
				"limit" : 50,
				"offset" : offset
			}
		)

		if response:
			#Save Init Data, Progress
			data = json.loads( response.text )
			
			for item in data['items']:
				artists_to_add = []
				if include_colab_artists:
					artists_to_add += item['track']['artists']
				else:
					artists_to_add += item['track']['album']['artists']

				for artist in artists_to_add:
					id = f"{artist['name']} , {artist['id']}"
					if not id in artists:
						artists.append( id )
			
			offset += 50

			self.app.log_event("info", f"[SPOT] Offset: {offset}")
			
			#Add Next Items
			while True:
				if not looped and not data.get('next'): break
				
				#Request Offset Playlist
				response = self.get(
					data.get('next'),
					headers = self.get_access_header(),
					params = {
						"fields" : "next,items(track(album, artists, id))",
						"market" : "US",
						"limit" : 50,
						"offset" : offset
					}
				)

				#Add Items
				if response:
					data = json.loads( response.text )

					for item in data['items']:
						artists_to_add = []
						if include_colab_artists:
							artists_to_add += item['track']['artists']
						else:
							artists_to_add += item['track']['album']['artists']

						for artist in artists_to_add:
							id = f"{artist['name']} , {artist['id']}"
							if not id in artists:
								artists.append( id )

					offset += 50
					looped = True

					self.app.log_event("info", f"[SPOT] Offset: {offset}")

				if not data.get('next'): break
		
		return artists

	def get_user_playlists(self, user:str) -> dict:
		self.update_access_token()

		response = self.get(
			self.url('api', f"/users/{user}/playlists"),
			headers = self.get_access_header(),
			params = {
				"limit" : 50,
				"offset" : 0
			}
		)

		if response:
			converted = {}
			data = json.loads( response.text )

			converted['total'] = data['total']
			converted['playlists'] = []

			for item in data['items']:
				if not item: continue

				playlist = {
					"id" : item['id'],
					'public' : item['public'],
					"name" : item['name'],
					"description" : item['description'],
					"owner" : f"{item['owner']['display_name']} : {item['owner']['id']}",
					"total" : item['tracks']['total'],
					"images" : item['images'],
					"link" : item['external_urls']['spotify'],
					"uri"  : item['uri']
				}

				converted['playlists'].append(playlist)

			return converted

	def get_playlist_items(self, playlist_id:str) -> list:
		self.update_access_token()

		self.app.log_event("info", "[SPOT] Getting Playlist Items ...")

		looped = False
		playlist = []
		offset = 0

		#Init Request
		response = self.get(
			self.url('api', f"/playlists/{playlist_id}/tracks"),
			headers = self.get_access_header(),
			params = {
				"market" : "US",
				"limit" : 50,
				"offset" : offset
			}
		)

		if response:
			#Save Init Data, Progress
			data = json.loads( response.text )
			playlist += data['items']
			offset += 50

			self.app.log_event("info", f"[SPOT] Songs ({len(playlist)} / {data["total"]})")
			
			#Add Next Items
			while True:
				if not looped and not data.get('next'): break
				
				#Request Offset Playlist
				response = self.get(
					data.get('next'),
					headers = self.get_access_header(),
					params = {
						"market" : "US",
						"limit" : 50,
						"offset" : offset
					}
				)

				#Add Items
				if response:
					data = json.loads( response.text )
					playlist += data['items']
					offset += 50
					looped = True

					self.app.log_event("info", f"[SPOT] Songs ({len(playlist)} / {data["total"]})")

				if not data.get('next'): break
		
		return playlist