from src import datetime
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Thread

## EVENT CLASSES
@dataclass
class Event():
	command   : int
	control: any
	timeof : str

class TriggerAppEvent(Event):
	def __init__(self, command, control, timeof):
		super().__init__(command, control, timeof)

## APP EVENTS
GLOBAL_EVENTS = []

def new_event(event_name:str, command:str, control:any) -> None:
	global GLOBAL_EVENTS

	match event_name:
		case 'TRIGGER':
			GLOBAL_EVENTS.append(
				TriggerAppEvent(command, control, datetime.now())
			)

		case _:
			raise Exception(f'[__events__]: Error: Cannot create Event of type "{event_name}"')
	
def get_global_events() -> list[Event] | list:
	global GLOBAL_EVENTS
	return GLOBAL_EVENTS
	
def get_latest_event() -> Event | None:
	global GLOBAL_EVENTS

	if len(GLOBAL_EVENTS) > 0:
		return GLOBAL_EVENTS[-1]
	else:
		return None

def clear_events():
	global GLOBAL_EVENTS
	GLOBAL_EVENTS = []

## ETC CLASSES
class Asset(Path):
	def __init__(self, *args):
		super().__init__(*args)




