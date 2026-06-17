from src import *

LAYOUTS = {}
with open("src/assets/data/keyboard_layouts.json", "r") as file:
	LAYOUTS = json.load(file)

class ActionKey(ft.FloatingActionButton):
	def __init__(self, key:str, call:Callable, width:int = None):
		self.label = ft.Text(
			value = key,
			style = STYLES.I1
		)

		super().__init__(
			elevation = 2,
			width = width,
			hover_elevation = 2,
			focus_elevation = 2,
			bgcolor = ft.Colors.with_opacity(0.9, ft.Colors.GREY_900),
			shape = ft.RoundedRectangleBorder(radius = 4),
			content = ft.SafeArea(
				minimum_padding = 5,
				content = self.label
			),
			on_click = call
		)

class Key(ft.FloatingActionButton):
	def __init__(self, key:dict, call:Callable):
		self.lower = key["lower"]
		self.alt = key["upper"]
		self.current = self.lower
		self.mode = "lower"

		self.label = ft.Text(
			value = self.lower,
			style = STYLES.I1
		)

		super().__init__(
			elevation = 2,
			hover_elevation = 2,
			focus_elevation = 2,
			bgcolor = ft.Colors.with_opacity(0.9, ft.Colors.GREY_900),
			shape = ft.RoundedRectangleBorder(radius = 4),
			content = ft.SafeArea(
				minimum_padding = 5,
				content = self.label
			),
			on_click = call
		)

	def shift(self, event=None):
		if self.mode == "lower":
			self.label.value = self.alt
			self.current = self.alt
			self.mode = "alt"
		else:
			self.label.value = self.lower
			self.current = self.lower
			self.mode = "lower"

class KeyboardOverlay(ft.Container):
	def __init__(self, layout, client, field_control:ft.TextField):
		self.client = client
		self.layout : list[list[dict[str, str]]] = LAYOUTS[layout]
		self.field_control : ft.TextField = field_control
		self.field_on_change = self.field_control.on_change
		self.field_control.on_change = self.__on_change_wrapper

		self.input_used = False

		self.keys = []
		self.rows = []
		self.inserts = [
			(0, -1, "Backspace", self.backspace, 165),
			(2, -1, "Enter", self.confirm, 110),
			(2, 0, 75),
			(3, (0, -1), "Shift", self.shift, 110),
			(4, 0, "Space", self.space, 600)
		]

		self.display_field = ft.TextField(
			value = self.field_control.value,
			height=65,
			width = float("inf"),
			border_radius=8,
			border_color=COLORS.DARK.BORDER.NORMAL,
			bgcolor=COLORS.DARK.BGDARK,
			text_style=STYLES.I2,
		)

		#Characters
		for i, row in enumerate(self.layout):
			key_row = ft.Row(width = float("inf"), spacing = 8, alignment=ft.MainAxisAlignment.CENTER)
			for key in row:
				key_ctrl = Key(key, self.on_type)
				self.keys.append(key_ctrl)
				key_row.controls.append( key_ctrl )
			self.rows.append(key_row)

		#Action Keys
		for data in self.inserts:
			if not data[0] < len(self.rows):
				key_row = ft.Row(width = float("inf"), spacing = 4, alignment=ft.MainAxisAlignment.CENTER)
				self.rows.append(key_row)

			if len(data) > 3:
				row = self.rows[data[0]]
				data = data[1:]

				if isinstance(data[0], int):
					if not data[0] == -1:
						row.controls.insert(
							data[0],
							ActionKey(data[1], data[2], data[3])
						)
					else:
						row.controls.append(ActionKey(data[1], data[2], data[3]))
				elif isinstance(data[0], tuple):
					for index in data[0]:
						if not index == -1:
							row.controls.insert(
								index,
								ActionKey(data[1], data[2], data[3])
							)
						else:
							row.controls.append(ActionKey(data[1], data[2], data[3]))
			else:
				data = data[1:]
				if not data[0] == -1:
					row.controls.insert(
						data[0],
						ft.Row(width = data[1])
					)
				else:
					row.controls.append(ft.Row(width = data[1]))

		width = 1015
		height = 450
		super().__init__(
			width = width,
			height = height,
			padding = 10,
			border_radius = 8,
			border = ft.border.all(2, ft.Colors.with_opacity(0.8, COLORS.DARK.BGDARK)),
			bgcolor = ft.Colors.GREY_900,
			gradient = ft.LinearGradient(
				begin=ft.alignment.top_left,
				end=ft.alignment.bottom_right,
				colors=[
					ft.Colors.with_opacity(0.98, COLORS.DARK.BGDARK),
					ft.Colors.with_opacity(0.98, ft.Colors.GREY_900)
				]
			),
			content = ft.Column(
				expand = True,
				spacing = 0,
				controls = [
					self.display_field,
					ft.Column(
						expand = True,
						spacing = 4,
						alignment=ft.MainAxisAlignment.CENTER,
						controls = self.rows
					)
				]
			),
			left = (self.client.SETTINGS.application.window.size.value[0] // 2) - (width // 2),
			bottom = self.client.SETTINGS.home.widget_margin.value
		)

	def input_check(self, event = None):
		pass

	def confirm(self, event = None):
		self.field_control.on_change = self.field_on_change
		self.client.close_keyboard()

	def backspace(self, event = None):
		val = self.field_control.value
		if str(val):  # avoid errors on empty
			self.field_control.value = val[:-1]
			self.update_field()
			self.trigger_on_change(event)
		else:
			self.update_display()

	def shift(self, event = None):
		"""Shifts all Keys to their Alterative Character"""
		for key in self.keys:
			key.shift()
		self.update()

	def space(self, event = None):
		"""Adds a space to the Text Field value"""
		self.type(key = " ")

	def type(self, event=None, key: str = ""):
		"""Appends key to the Text Field value"""
		self.field_control.value += key
		self.update_field()

	def on_type(self, event):
		"""When a Character Key is clicked, type its key into the Text Field"""
		self.type(key = event.control.current)
		self.trigger_on_change(event)

	def update_field(self):
		"""Updates the target tText Field"""
		try: self.field_control.update()
		except: pass

	def update_display(self):
		"""Update display to mimic Text Field"""
		self.display_field.value = self.field_control.value
		self.display_field.update()

	def trigger_on_change(self, event):
		"""When keyboard triggers a change, it calls on change on the Text Field"""
		self.__on_change_wrapper(event)

	def __on_change_wrapper(self, event):
		"""Allow for Update of Display when on change occurs outside of keyboard functionality"""
		if self.field_on_change: self.field_on_change(event)
		self.update_display()
