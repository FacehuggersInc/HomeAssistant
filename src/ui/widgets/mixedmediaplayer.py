from src import *

from src.ui.controls.buttons import IconButton, IconAndTextButton, DropdownButton

from src.ui.widget import Widget

class MediaWidget(Widget):
	def __init__(self, client):
		
		## INIT
		self.client = client
		self.KEYBOARD = Controller()

		self.update_thread = None
		self.can_update = True

		self.position_name = self.client.SETTINGS.home.media_player_position.value
		self.mode = "normal"
		self.transitioning = False
		self.normal_width = 550
		self.max_width = 750
		self.transition_delay = 0.6
		self.transition_time = time.time() + self.transition_delay

		self.hide_media_after_stop_delay = 5
		self.hide_media_after_stop_time = None

		self.getting_media_info = False
		self.current_media_info = None

		#API
		self.playback = None
		self.album_id = None
		self.playing_id = None
		self.context_uri = None
		self.playing_uri = None
		self.seeking = False
		self.playlists = {}
		"""if self.client.SETTINGS.PLUGINS.MEDIA.USERNAME:
			self.playlists = self.client.API['spotify'].get_user_playlists(
				self.client.SETTINGS.PLUGINS.MEDIA.USERNAME
			)"""

		## BUILD - Default
		self.toggle_btn = IconButton(
			ft.Icons.MUSIC_NOTE,
			self.toggle_state,
			size = 40
		)

		self.prev_btn = IconButton(
			ft.Icons.SKIP_PREVIOUS_ROUNDED,
			self.previous,
			size = 40
		)

		self.play_pause_btn = IconButton(
			ft.Icons.PLAY_ARROW_ROUNDED,
			self.play_or_pause,
			size = 40
		)

		self.next_btn = IconButton(
			ft.Icons.SKIP_NEXT_ROUNDED,
			self.next,
			size = 40
		)

		self.whats_playing_btn = IconAndTextButton(
			"",
			ft.TextStyle(
				size = 28,
				color = "white",
				weight = ft.FontWeight.W_500,
				shadow = ft.BoxShadow(
					3, 3, "black", ft.Offset(3, 3), ft.ShadowBlurStyle.OUTER
				)
			),
			"right",
			self.normal_width,
			ft.Icons.MUSIC_NOTE,
			self.toggle_state,
			ft.Colors.with_opacity(0.7, 'black'),
			radius = 100
		)

		self.controls_row =  ft.Row(
			expand = True,
			spacing = 0.5,
			controls = [
				ft.Row(
					expand = True,
					alignment = ft.MainAxisAlignment.CENTER,
					controls = [self.prev_btn, self.play_pause_btn, self.next_btn]
				)
			]
		)

		self.controls_container = ft.Container(
			expand = True,
			bgcolor = COLORS['950'],
			border_radius = 100,
			content = self.controls_row
		)

		self.normal_container = ft.Container(
			expand = True,
			visible=True,
			bgcolor = ft.Colors.TRANSPARENT,
			content = ft.Column(
				expand = True,
				spacing = 5,
				controls = []
			)
		)

		if 'top' in self.position_name:
			if self.client.SETTINGS.home.show_normal_media_player:
				self.normal_container.content.controls.append(self.controls_container)
			if self.client.SETTINGS.home.show_whats_playing:
				self.normal_container.content.controls.append(self.whats_playing_btn)
		elif 'bottom' in self.position_name:
			if self.client.SETTINGS.home.show_whats_playing:
				self.normal_container.content.controls.append(self.whats_playing_btn)
			if self.client.SETTINGS.home.show_normal_media_player:
				self.normal_container.content.controls.append(self.controls_container)

		## BUILD - API
		self.api_play_pause_btn = IconButton(
			ft.Icons.PLAY_ARROW_ROUNDED,
			self.play_or_pause,
			size = 40
		)

		self.device_label = ft.Text(
		 	style = ft.TextStyle(
				size = 18,
				color = '#FAF9F6',
				weight = ft.FontWeight.W_100,
				shadow = ft.BoxShadow(
					5, 5, "black", ft.Offset(3, 3), ft.ShadowBlurStyle.OUTER
				)
			)
		)

		self.name_label = ft.Text(
		 	style = ft.TextStyle(
				size = 30,
				color = 'white',
				weight = ft.FontWeight.W_500,
				shadow = ft.BoxShadow(
					5, 5, "black", ft.Offset(3, 3), ft.ShadowBlurStyle.OUTER
				)
			)
		)

		self.artist_label = ft.Text(
		 	style = ft.TextStyle(
				size = 22,
				color = '#FAF9F6',
				weight = ft.FontWeight.W_300,
				shadow = ft.BoxShadow(
					5, 5, "black", ft.Offset(3, 3), ft.ShadowBlurStyle.OUTER
				)
			)
		)

		self.progress_time_label = ft.Text(
			expand = True,
			value = '',
			style = ft.TextStyle(
				size = 18,
				shadow = ft.BoxShadow(
					5, 5, "black", ft.Offset(3, 3), ft.ShadowBlurStyle.OUTER
				)
			)
		)

		self.progress_max_time_label = ft.Text(
			expand = True,
			value = '',
			text_align = ft.TextAlign.END,
			style = ft.TextStyle(
				size = 18,
				shadow = ft.BoxShadow(
					5, 5, "black", ft.Offset(3, 3), ft.ShadowBlurStyle.OUTER
				)
			)
		)

		self.progress = ft.Slider(
			value = 0,
			min = 0,
			max = 100000,
			expand = True,
			active_color = 'white',
			thumb_color = 'white',
			inactive_color = ft.Colors.with_opacity(0.7, 'black'),
			on_change_start = self.seek_start,
			on_change_end = self.seek_end
		)

		self.progress_column = ft.Column(
			expand=True,
			spacing = 1,
			controls = [
				self.progress,
				ft.Row(
					expand=True,
					controls = [
						ft.Row(
							alignment = ft.MainAxisAlignment.START,
							controls = [self.progress_time_label]
						),
						ft.Row(
							expand = True,
							alignment = ft.MainAxisAlignment.END,
							controls = [self.progress_max_time_label]
						)
					]
				)
			]
		)

		self.close_btn = IconButton(
			ft.Icons.MUSIC_OFF_ROUNDED,
			self.transition,
			size = 30,
			right = 5,
			top = 5,
			visible = False
		)

		self.shuffle_btn = ft.FloatingActionButton(
			data = False,
			content = ft.Icon(
				name = ft.Icons.SHUFFLE,
				color = "white",
				size = 45,
				shadows = [
					ft.BoxShadow(
						3, 3, "black", ft.Offset(3, 3), ft.ShadowBlurStyle.OUTER
					)
				]
			),
			bgcolor = ft.Colors.TRANSPARENT,
			elevation = 0,
			hover_elevation = 0,
			highlight_elevation = 1,
			shape = ft.RoundedRectangleBorder(radius=2),
			width = 80,
			height = 80,
			on_click = self.toggle_shuffle,
		)

		self.repeat_btn = IconButton(
			ft.Icons.REPEAT,
			self.change_repeat,
			size = 40,
			data = 0
		)

		self.api_container = ft.Container(
			expand = True,
			visible=False,
			padding = 25,
			bgcolor = COLORS['950'],
			gradient=ft.LinearGradient(
				begin=ft.alignment.top_left,
				end=ft.alignment.bottom_right,
				colors=[COLORS['950'], COLORS['900']],
			),
			content = ft.Column(
				expand =  True,
				spacing = 0,
				controls= [
					self.device_label,
					self.name_label,
					self.artist_label,
					self.progress_column,
					ft.Row(
						expand = True,
						controls = [
							ft.Row(
								expand = True,
								alignment = ft.MainAxisAlignment.CENTER,
								controls = [self.shuffle_btn, self.prev_btn, self.api_play_pause_btn, self.next_btn, self.repeat_btn]
							)
						]
					)
				]
			)
		)

		self.background = None

		## BUILD
		self.views = ft.Stack(
			expand = True,
			controls = [
				self.normal_container,
				self.api_container,
				self.close_btn
			]
		)

		self.add_to_playlist_btn = None
		if self.playlists:
			items = []
			for item in self.playlists['playlists']:
				option = ft.PopupMenuItem(
					data = item,
					on_click = self.add_to_playlist,
					content = ft.Text(
						value = item['name'],
						style = ft.TextStyle(
							size = 25,
							color = 'white'
						)
					)
				)
				items.append(option)

			self.add_to_playlist_btn = DropdownButton(
				ft.Icons.ADD,
				items,
				size = 30,
				right = 75,
				top = 5,
				visible = False,
			)

			self.views.controls.append( self.add_to_playlist_btn )

		super().__init__(
			client = self.client,
			key = "mixedmediawidget",
			widget_content = self.views,
			width = self.normal_width,
			height = 125,
			anchor = self.client.SETTINGS.home.media_player_position,
			animate_size = ft.Animation(450, ft.AnimationCurve.EASE_IN_OUT),
			border_radius = 6
		)



	## MEDIA
	def get_playback_status(self, status):
		"""Convert PlaybackStatus enum to string"""
		status_dict = {
			PlaybackStatus.CLOSED: "closed",
			PlaybackStatus.OPENED: "opened",
			PlaybackStatus.CHANGING: "changing",
			PlaybackStatus.STOPPED: "stopped",
			PlaybackStatus.PLAYING: "playing",
			PlaybackStatus.PAUSED: "paused"
		}
		return status_dict.get(status, "unknown")

	async def __update_media_info_thread(self, stop_event, loop):
		"""
		Get current media information
		"""
		sessions = await MediaManager.request_async()
		current_session = sessions.get_current_session()
		
		if current_session:
			info = await current_session.try_get_media_properties_async()
			if info:
				media_info = {
					'title': info.title,
					'playback_status': self.get_playback_status(current_session.get_playback_info().playback_status),
				}
				self.current_media_info = media_info

		if stop_event.is_set():
			return
		else:
			self.getting_media_info = False
			return

	def update_media_info(self):
		if self.getting_media_info: return
		self.getting_media_info = True
		def thread_target(stop_event):
			if stop_event.is_set(): return
			loop = asyncio.new_event_loop()  # Create a new event loop for the thread
			asyncio.set_event_loop(loop)# Set it as the current event loop
			loop.run_until_complete(self.__update_media_info_thread(stop_event, loop))
			loop.close()
		
		if not self.client.THREADS.get("__async_threaded_media_info"):
			self.client.THREADS.create(
				name = "__async_threaded_media_info",
				target = thread_target
			)
			self.client.THREADS.start("__async_threaded_media_info")
		else:
			if not self.client.THREADS["__async_threaded_media_info"]["stop_event"].is_set():
				self.client.THREADS.start("__async_threaded_media_info")

	def ms_to_time(self, milliseconds):
		# Convert milliseconds to seconds
		seconds = milliseconds // 1000
		# Calculate hours, minutes, and seconds
		hours = seconds // 3600
		minutes = (seconds % 3600) // 60
		seconds = seconds % 60
		# Format time as hh:mm:ss or mm:ss
		if hours > 0:
			return f"{hours:02}:{minutes:02}:{seconds:02}"
		else:
			return f"{minutes:02}:{seconds:02}"

	def get_image(self, url:str, blur:bool = True) -> str | None:
		response = requests.get(url)
		if response.status_code == 200:
			image = Image.open(BytesIO(response.content))

			if blur:
				image = image.filter(ImageFilter.GaussianBlur(2.5))

				enhancer = ImageEnhance.Brightness(image)
				image = enhancer.enhance(0.5)  # factor < 1.0 to darken
			
			buffered = BytesIO()
			image.save(buffered, format="PNG")
			return base64.b64encode(buffered.getvalue()).decode("utf-8")
		else:
			return None

	def update_playback_state(self, fallback:bool = False) -> dict:
		api = self.client.API.get("spotify")
		if api:
			data = api.get_playback_state()
			if data:
				self.playback = data
			else:
				if fallback:
					self.transition()
		else:
			if fallback:
				self.transition()

	def update_playing(self):
		data = self.client.API['spotify'].get_playing_track()
		if data:
			self.playing = data

	def update_progress(self):
		if self.playback and self.playback.get('item'):
			if type(self.playing_id) == str and not self.playing_id == self.playback['item']['id']:
				self.update_playback_state(fallback=True)
				self.update_music_controls()
			elif not self.playback.get('item'):
				self.transition()

			if not self.seeking:
				self.progress.max = self.playback['item']['duration_ms']
			self.progress_max_time_label.value = self.ms_to_time(self.progress.max)
			
			if not self.seeking:
				self.progress.value = self.playback['progress_ms']
			self.progress_time_label.value = self.ms_to_time(int(str(self.progress.value).split('.')[0]))

			try:
				self.background.update()
			except: pass

	def __update_background_thread(self):
		image_url = self.playback['item']['album']['images'][0]['url']
		if image_url and self.background and not self.background.data == image_url:
			image_str = self.get_image( image_url )
			if image_str:
				self.background.src_base64 = image_str
		elif image_url and not self.background:
			image_str = self.get_image( image_url )
			if image_str and len(self.views.controls) >= 2 and len(self.views.controls) < 5:
				self.background = ft.Image(
					expand = True,
					data = image_url,
					scale = 4,
					fit=ft.ImageFit.FILL,
					src_base64=image_str,
				)
				self.views.controls.insert(1, self.background)
				self.views.update()

		self.api_container.bgcolor = ft.Colors.TRANSPARENT
		self.api_container.gradient = None
		try: self.api_container.update()
		except: pass
		
		try: self.background.update()
		except: pass

	def update_background(self):
		Thread(target = self.__update_background_thread).start()

	def update_music_controls(self):
		playback_item = self.playback.get('item')
		if playback_item:
			self.playing_id = playback_item['id']
			self.context_uri = self.playback['context']['uri']
			self.playing_uri = self.playback['item']['uri']
			self.album_id = self.playback['item']['album']['id']

			if self.playback.get('item'):
				self.update_background()
			else:
				return False

			self.device_label.value = f'DEVICE: {self.playback['device']['name']}'
			try: self.device_label.update()
			except: pass

			self.name_label.value = self.playback['item']['name']
			try: self.name_label.update()
			except: pass
			
			artists = ''
			for artist in self.playback['item']['artists']:
				artists += f', {artist['name']}'
			self.artist_label.value = artists.strip()[1:].strip()
			try: self.artist_label.update()
			except: pass

			return True
		else:
			return False



	## CONTROL
	def add_to_playlist(self, event):
		is_added = self.client.API['spotify'].add_to_playlist(
			event.control.data['id'],
			self.playing_uri
		)

		if not is_added:
			self.client.notify(
				ft.Icons.MUSIC_NOTE,
				"Spotify",
				f"Couldn't add {self.name_label.value}"
			)
		else:
			self.client.notify(
				ft.Icons.MUSIC_NOTE,
				"Spotify",
				f"Added {self.name_label.value} to {event.control.data['name']}"
			)

	def change_repeat(self, event=None):
		if self.playback:
			options = [
				[ft.Icons.REPEAT, "off"],
				[ft.Icons.REPEAT, "context"],
				[ft.Icons.REPEAT_ONE, "track"]
			]

			self.repeat_btn.data += 1
			self.repeat_btn.content.color = SPOTIFY
			self.repeat_btn.color = SPOTIFY
			if self.repeat_btn.data > len(options) - 1:
				self.repeat_btn.data = 0
				self.repeat_btn.content.color = 'white'
				self.repeat_btn.content.color = 'white'

			option = options[self.repeat_btn.data]

			self.repeat_btn.content.name = option[0]
			self.repeat_btn.content.update()

			self.client.API['spotify'].set_repeat_player(option[1])

	def toggle_shuffle(self, event = None):
		if self.playback:
			if self.shuffle_btn.data:
				self.shuffle_btn.data = False
				self.shuffle_btn.content.color = 'white'
			else:
				self.shuffle_btn.data = True
				self.shuffle_btn.content.color = SPOTIFY
			
			self.shuffle_btn.update()
			self.client.API['spotify'].toggle_shuffle_player(self.shuffle_btn.data)

	def seek_start(self, event= None):
		self.seeking = True

	def seek_end(self, event= None):
		response = self.client.API['spotify'].seek_player(
			int(self.progress.value)
		)
		self.seeking = False

	def previous(self, event = None):
		if self.mode == "normal":
			self.KEYBOARD.press(Key.media_previous)
		elif self.mode == "api-media":
			self.client.API['spotify'].prev_player()
			self.update_music_controls()

	def next(self, event = None):
		if self.mode == "normal":
			self.KEYBOARD.press(Key.media_next)
		elif self.mode == "api-media":
			self.client.API['spotify'].next_player()
			self.update_music_controls()

	def play_or_pause(self, event = None):
		if self.mode == "normal":
			self.KEYBOARD.press(Key.media_play_pause)
		elif self.mode == "api-media":
			#PLAY
			if not self.api_play_pause_btn.content.name == ft.Icons.PAUSE:
				self.api_play_pause_btn.content.name = ft.Icons.PAUSE
				response = self.client.API['spotify'].play_player(self.playing_uri, self.progress.value)
				
			#PAUSE
			elif not self.api_play_pause_btn.content.name == ft.Icons.PLAY_ARROW_ROUNDED:
				self.api_play_pause_btn.content.name = ft.Icons.PLAY_ARROW_ROUNDED
				response = self.client.API['spotify'].pause_player()



	## CORE

	def transition(self, event = None):
		self.transitioning = True
		self.transition_time = time.time() + self.transition_delay
		if self.mode == "normal":
			#Get Playback and Current
			self.update_playback_state()
			if not self.playback:
				if self.current_media_info and self.current_media_info['title']:
					self.client.notify(
						ft.Icons.MUSIC_NOTE,
						"Spotify",
						"What is currently playing likely is not a Spotify Song."
					)
				else:
					self.client.notify(
						ft.Icons.MUSIC_NOTE,
						"Spotify",
						"Nothing is playing, go play a Song from you library first!"
					)
				return

			got_id = self.update_music_controls()
			if not got_id:
				return
			
			self.update_progress()

			#Switch Mode
			self.mode = "api-media"
			if self.background: 
				self.background.visible = True
			self.visible = True
			self.api_container.visible = True
			self.close_btn.visible = True
			if self.client.SETTINGS.plugins.media.username:
				self.add_to_playlist_btn.visible = True
			self.normal_container.visible = False
			self.width = self.max_width
			self.height = 300

			self.position = self.get_position()
			self.left = self.position[0]
			self.right = self.position[1]
			self.top = self.position[2]
			self.bottom = self.position[3]

		else:
			self.playback = None
			self.playing_id = None
			self.playing_uri = None

			if not self.getting_media_info:
				self.update_media_info()
			if self.client.SETTINGS.home.show_whats_playing:
				playing = self.current_media_info.get("title", None)
				if playing:
					self.whats_playing_btn.set_text(playing)
				else:
					pass #! NOTE: SHOULD DEAL WITH WHATEVER THIS CAUSES

			self.mode = "normal"
			self.width = self.normal_width
			self.height = None
			if self.background: 
				self.background.visible = False
			self.api_container.visible = False
			self.close_btn.visible = False
			if self.client.SETTINGS.plugins.media.username:
				self.add_to_playlist_btn.visible = False
			self.normal_container.visible = True

			if not self.client.SETTINGS.home.show_whats_playing:
				self.whats_playing_btn.visible = False
			else:
				self.whats_playing_btn.visible = True
			if not self.client.SETTINGS.home.show_normal_media_player:
				self.controls_container.visible = False
			else:
				self.controls_container.visible = True

			self.position = self.get_position()
			self.left = self.position[0]
			self.right = self.position[1]
			self.top = self.position[2]
			self.bottom = self.position[3]


		self.update()

	def toggle_state(self, event = None):
		self.transition()

	def start_update(self):
		self.client.THREADS.create(
			name = "__multimedia_update_thread",
			target = self.__update_thread
		)
		self.client.THREADS.start("__multimedia_update_thread")

	def stop_update(self):
		self.client.THREADS.stop("__multimedia_update_thread")

	def is_update_running(self) -> bool:
		if self.update_thread:
			return self.update_thread.is_alive()
		else:
			return False
		
	def __update_thread(self, stop_event):
		"""Updates This Controls Behavior"""
		while not stop_event.is_set():
			if self.client.BUILT:
				time.sleep(0.2)

				if self.mode == "api-media":
					if not self.seeking:
						self.update_playback_state(True)
					self.update_progress()

					if self.client.SETTINGS.plugins.media.username:
						if self.album_id and self.playlists:
							found_item = False
							for playlist in self.playlists['playlists']:
								if playlist['id'] == self.album_id:
									found_item = True
									self.add_to_playlist_btn.icon_color = SPOTIFY
									break
							
							if not found_item:
								self.add_to_playlist_btn.icon_color = 'white'

					if self.playback and self.playback.get('is_playing'):
						if not self.api_play_pause_btn.content.name == ft.Icons.PAUSE:
							self.api_play_pause_btn.content.name = ft.Icons.PAUSE
							try: self.api_play_pause_btn.content.update()
							except: pass
					elif not self.playback or self.playback['is_playing'] == False:
						if not self.api_play_pause_btn.content.name == ft.Icons.PLAY_ARROW_ROUNDED:
							self.api_play_pause_btn.content.name = ft.Icons.PLAY_ARROW_ROUNDED
							try: self.api_play_pause_btn.content.update()
							except: pass

				elif self.mode == "normal":
					if not self.getting_media_info:
						self.update_media_info()
					if self.current_media_info and self.current_media_info['title']:
						self.visible = True

						if self.client.SETTINGS.home.show_whats_playing:
							self.whats_playing_btn.set_text(self.current_media_info['title'])

						if self.client.SETTINGS.home.show_normal_media_player:
							if 'playing' in self.current_media_info['playback_status'].strip():
								self.play_pause_btn.content.name = ft.Icons.PAUSE
								try:self.play_pause_btn.update()
								except: pass
							else:
								self.play_pause_btn.content.name = ft.Icons.PLAY_ARROW_ROUNDED
								try:self.play_pause_btn.update()
								except: pass

					else:
						if not self.visible == False:
							if self.hide_media_after_stop_time == None:
								self.hide_media_after_stop_time = time.time() + self.hide_media_after_stop_delay

							if time.time() >= self.hide_media_after_stop_time:
								self.visible = False
								self.hide_media_after_stop_time = None
				
				if self.transitioning and time.time() >= self.transition_time:
					self.transitioning = False

				if not self.transitioning:
					try: self.update()
					except: pass
