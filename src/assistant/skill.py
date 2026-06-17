from src import *

import re
from typing import Callable, Optional, List
from spacy.matcher import Matcher
from spacy.matcher import PhraseMatcher

MATCHER = Matcher(NLP_MODEL.vocab)
PRIMARY_THRESHOLD = 0.70
FALLBACK_DEFAULT_RULE_SCORE = 0.66

class Intent:
	def __init__(self, phrase: str, accuracy: float, arguments: dict, source: str):
		self.phrase = phrase
		self.accuracy = accuracy
		self.arguments = arguments
		self.source = source

	def __repr__(self):
		return f"Intent({self.phrase}:{self.accuracy}:{self.source}:{self.arguments})"
	


class SkillGroup:
	def __init__(self, domain: str, skills: list[dict]):
		self.domain = domain
		self.skills = [
			Skill(**{**skill, "domain": domain})
			for skill in skills
		]

	def __iter__(self):
		return iter(self.skills)

	def __len__(self):
		return len(self.skills)

	def __getitem__(self, idx):
		return self.skills[idx]

	def __add__(self, other):
		"""Allows list + SkillGroup or SkillGroup + list"""
		if isinstance(other, list):
			return self.skills + other
		elif isinstance(other, SkillGroup):
			return self.skills + other.skills
		return NotImplemented

	def __radd__(self, other):
		"""Allows list + SkillGroup in reverse order"""
		if isinstance(other, list):
			return other + self.skills
		return NotImplemented



class Skill:
	def __init__(
		self,
		wake_word: str,
		skill_key: str,
		plugin_key: str,
		examples: List[str],
		patterns : list[list[dict]] = None,
		arguments: dict[str, list[list[dict]]] = None,
		func: Optional[Callable] = None,
		words_leeway: int = 5
	):
		"""
		Initialize a Skill instance for the assistant.

		This class represents a skill with a wake word, intent key, plugin association,
		example phrases, argument patterns, and an optional callable function.

		Parameters
		----------
		wake_word : str
			The keyword that activates the skill (e.g., "Clyde, assistant", "Clyde" being the keyword).
		skill_key : str
			A unique identifier for the skill.
		plugin_key : str
			The Plugin identifier, should be the same as the plugin key you created and registered for this Skill.
		examples : list[str]
			Example phrases demonstrating how the skill may be invoked. These will be converted automatically into the best
		patterns : list[list[dict]], optional
			Additional spaCy Matcher patterns for the skill beyond auto-generated patterns.
		arguments : dict[str, list[list[dict]]], optional
			Mapping of argument names to spaCy Matcher patterns for extracting arguments from text.
		func : Callable, optional
			Function to execute when this skill is triggered.
		words_leeway : int, default=5
			Extra allowance added to the maximum word count of example phrases for flexible pattern matching.
		"""
		self.nlp = NLP_MODEL
		
		self.wake = wake_word
		self.key = skill_key
		self.plugin = plugin_key

		# Phrase Pattern Matching
		self.matcher = MATCHER
		self.examples = examples
		self.patterns = self.generate_patterns(self.examples)
		if patterns: self.patterns += patterns
		self.docs = [self.nlp(phrase) for phrase in self.examples]
		self.intent_name = f"{self.plugin}:{self.key}"
		self.matcher.add(self.intent_name, self.patterns)
		self.id = self.nlp.vocab.strings[self.intent_name]
		self.lemmas = [{t.lemma_.lower() for t in doc if t.is_alpha} for doc in self.docs]

		#Argument Pattern Matching
		self.arg_matcher = Matcher(NLP_MODEL.vocab)
		self.arguments = arguments
		if arguments:
			for arg_pattern_key, patterns in arguments.items():
				self.arg_matcher.add( arg_pattern_key, patterns)

		self.word_max: int = 0
		self.word_min: int = 100
		self.func = func

		# Compute word min/max
		for example in self.examples:
			words = len(example.split())
			if words > self.word_max: self.word_max = words
			if words < self.word_min: self.word_min = words
		self.word_max += words_leeway
		self.word_min = max(2, self.word_min - 2)

	def generate_patterns(self, phrases:list[str]):
		patterns = []
		for phrase in phrases:
			doc = self.nlp(phrase)
			pattern = [{"LEMMA": token.lemma_.lower()} for token in doc]
			patterns.append(pattern)
		return patterns
	
	def extract_args(self, doc):
		"""Run arg_matcher on the doc and return dict of arguments"""
		args = {}
		matches = self.arg_matcher(doc)
		for match_id, start, end in matches:
			arg_label = doc.vocab.strings[match_id]
			span = doc[start:end]
			args[arg_label] = span.text
		return args

	def get_patterns(self):
		return self.normalized_patterns

	def call(self, *args, **kwargs):
		"""Execute the skill's function if defined"""
		if self.func:
			self.func(*args ,**kwargs)


class SkillIntentEngine:
	def __init__(self, client):
		self.client = client
		self.nlp = NLP_MODEL
		self.matcher = MATCHER

		self.phases = ["matcher", "rule"]

		self.registered: dict[str, list[Skill]] = {}
		self.skill_lib = {}
		self.id2skill = {}
		self.wake_args = []

	def registered_count(self, plugin_key:str):
		return 0 if not self.registered.get(plugin_key) else len( self.registered[plugin_key] )

	def skills(self, filter:str = None) -> list[Skill]:
		all = []
		for plugin, skills in self.registered.items():
			if filter and plugin != filter: 
				continue
			all += skills
		return all

	def get_allowed_skills(self, plugin_key:str, wake_word:str) -> list[Skill]:
		return [
			s for s in self.registered.get(plugin_key, [])
			if (s.wake or "").lower() == (wake_word or "").lower()
		]
	
	def get_skill(self, skill_key):
		for key, skill in self.skill_lib.items():
			if skill_key in key: return skill

		return None

	def register(self, plugin_key:str, skills:list[Skill|SkillGroup]):
		# Flatten skills / groups
		all_skills: list[Skill] = []
		for skill in skills:
			if isinstance(skill, Skill):
				all_skills.append(skill)
			elif isinstance(skill, SkillGroup):
				all_skills += list(skill)

		# Merge with any existing
		existing = self.registered.get(plugin_key, [])
		skills = existing + all_skills
		self.registered[plugin_key] = skills
		self.client.log("info", f"[SkillIntentEngine] {plugin_key} added {len(all_skills)} Skills")

		for skill in skills:
			self.skill_lib[f"{skill.plugin}:{skill.key}"] = skill
			self.id2skill[skill.id] = skill

		# Update wake info (unchanged)
		self.wake_args = [(s.wake.lower(), s.word_max, s.word_min) for s in self.skills() if s.wake]

	def remove_skill(self, plugin_key:str, skill_key:str):
		if plugin_key in self.registered:
			self.registered[plugin_key] = [s for s in self.registered[plugin_key] if s.key != skill_key]
			# Rebuild PhraseMatcher for that plugin to keep it consistent
			pm = PhraseMatcher(NLP_MODEL.vocab, attr="LEMMA")
			for s in self.registered[plugin_key]:
				docs = s.get_patterns()
				if docs:
					self._pm_add_patterns(pm, s.key, docs)
			self._pm_by_plugin[plugin_key] = pm

	def un_register(self, plugin_key:str):
		if plugin_key in self.registered:
			del self.registered[plugin_key]

		#Skill Lib
		ids = []
		to_remove = []
		for key, skill in self.skill_lib.items():
			if plugin_key in key:
				ids.append(skill.id)
				to_remove.append(key)
		for key in to_remove:
			del self.skill_lib[key]
		
		for id in ids:
			del self.id2skill[id]

	def get_plugin_from_wake_word(self, wake_word:str) -> str|None:
		for skill in self.skills():
			if (skill.wake or "").lower() == (wake_word or "").lower():
				return skill.plugin
		return None

	def normalize_text(self, text: str) -> str:
		s = text.strip().lower()
		s = re.sub(r"\s+", " ", s)
		return s
	
	def multi_phase(self, phase):
		"""deprecated"""
		start = time.time()
		phase_results = {}
		phase_threads = {}

		original_text = phase

		for name, t in phase_threads.items():
			if name in self.phases:
				t.start()

		#Process Results as they Come In
		best_skill = None
		best_score = 0.0
		processed = []
		best_early = False
		while True:
			capture = phase_results.items()
			for source, (skill, score) in capture:
				if source in processed: continue
				if score > 0: self.client.log("info", f"[SkillIntentEngine] Phase '{source}' found '{skill.key}' @ {score} : {round(time.time() - start, 3)}s")
				if skill and score > best_score:
					best_skill = skill
					best_score = score
					if not best_early and best_skill and best_score >= (best_skill.accuracy or 0.6):
						Thread(target = best_skill.call, args=[best_skill, original_text]).start()
						best_early = True
						break

				processed.append(source)

			if best_early or len(processed) == len(self.phases) or best_score >= 1.0: break

	def __skill_call_with_status_update(self, best_skill:Skill, match):
		args = best_skill.extract_args(match)
		self.client.log("info", f"Intent Args: {args}")
		if args:
			try:
				best_skill.call(**args)
			except Exception as e:
				self.client.log("error", f"Error calling skill '{best_skill.key}' with args:\n---start---\n{traceback.format_exc().strip()}\n---end---")
		else:
			try:
				best_skill.call()
			except Exception as e:
				self.client.log("error", f"Error calling skill '{best_skill.key}':\n---start---\n{traceback.format_exc().strip()}\n---end---")
			
		self.client.ASSIST_STATUS = "LIVE"

	def parse(self, phrase: str, use_skill: bool = True) -> Intent | None:
		start = time.time()

		self.client.log("info", f"[SkillIntentEngine] Searching for Intent in '{phrase}' ...")

		original_text = phrase
		match_doc = self.nlp( phrase )
		results = self.matcher(match_doc)
		best_skill : Skill = None
		best_score = -1

		input_lemmas = {t.lemma_.lower() for t in match_doc if t.is_alpha}
		candidates = [self.id2skill[m[0]] for m in results]

		for skill in candidates:
			for example_lemmas in skill.lemmas:
				score = len(input_lemmas & example_lemmas)
				if score > best_score:
					best_score = score
					best_skill = skill

		if not best_skill:
			self.client.log("info", f"[SkillIntentEngine] Matcher found Nothing : {round(time.time() - start, 3)}s")
			self.client.ASSIST_STATUS = "LIVE"
			return None, None
		else:
			if use_skill:
				self.client.ASSIST_STATUS = "ACTING"
				Thread(target = self.__skill_call_with_status_update, args = [best_skill, match_doc]).start()
			else:
				self.client.ASSIST_STATUS = "LIVE"
			self.client.log("info", f"[SkillIntentEngine] Matcher found '{best_skill.key}' @ {best_score} : {time.time() - start}s")
			return best_skill, original_text