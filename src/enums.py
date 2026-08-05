from datetime import datetime
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

	def mark_uploadable(self) -> "Asset":
		object.__setattr__(self, "uploadable_flag", True)
		return self

	@property
	def is_uploadable(self) -> bool:
		return object.__getattribute__(self, "uploadable_flag") if "uploadable_flag" in self.__dict__ else False

	def mark_deletable(self) -> "Asset":
		"""
		Files in here may be listed and removed over the API.

		Separate from uploadable, and deliberately not implied by it. Adding
		to a folder is recoverable by deleting what was added; emptying one is
		not, and a folder somebody may put things into is not automatically a
		folder they should be able to empty from a phone.
		"""
		object.__setattr__(self, "deletable_flag", True)
		return self

	@property
	def is_deletable(self) -> bool:
		return object.__getattribute__(self, "deletable_flag") if "deletable_flag" in self.__dict__ else False