from __future__ import annotations

import re
import time
import math
from difflib import SequenceMatcher
import traceback
from threading import Thread
from typing import TYPE_CHECKING, Callable, Optional, List

from src.assistant import nlp

if TYPE_CHECKING:
	from src.main import Client

PRIMARY_THRESHOLD = 0.70
# Tuned by sweeping a 52-phrase corpus (see the eval harness in the repo
# history): 0.50 scores higher overall but lets "tell me a joke" answer with
# the weather. A miss costs the user a repeat; a misfire makes the assistant
# do the wrong thing, so the highest threshold with zero misfires wins.
FALLBACK_DEFAULT_RULE_SCORE = 0.55

# Above this, a Matcher hit is taken without consulting the rule phase. Below
# it, the rule phase gets to contest - a pattern can only ever match the words
# somebody wrote into an example, so a phrase carrying an arbitrary name or an
# unanticipated determiner reaches the Matcher as a weak partial hit at best.
MATCHER_CONFIDENT_SCORE = 0.75

# Words that carry no intent and vary freely between speakers. Made optional
# in generated patterns so their presence or absence never decides a match.
# Rule-phase tuning. FUZZY_MIN_RATIO is deliberately generous: the phase only
# runs when the Matcher found nothing, and it is thresholded afterwards.
FUZZY_MIN_RATIO = 0.78
FUZZY_MIN_LENGTH = 4

OPTIONAL_LEMMAS = {
	"please", "can", "could", "would", "will", "just", "kindly",
	"for", "to", "new", "some", "of", "up", "now", "then", "and",
}

def fuzzy_equal(a: str, b: str) -> float:
	"""
	How alike two lemmas are, 0..1.

	Whisper substitutes acoustically similar words - "whether" for "weather",
	"notifcations", "aplication" - and no amount of extra example phrasings
	recovers those. Short tokens are compared exactly, since at three or four
	characters almost everything is close to everything.
	"""
	if a == b:
		return 1.0
	if len(a) < FUZZY_MIN_LENGTH or len(b) < FUZZY_MIN_LENGTH:
		return 0.0
	if abs(len(a) - len(b)) > 3:
		return 0.0
	ratio = SequenceMatcher(None, a, b).ratio()
	return ratio if ratio >= FUZZY_MIN_RATIO else 0.0


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
		if isinstance(other, list):
			return self.skills + other
		elif isinstance(other, SkillGroup):
			return self.skills + other.skills
		return NotImplemented

	def __radd__(self, other):
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
		self.nlp = nlp.model()
		
		self.wake = wake_word
		self.key = skill_key
		self.plugin = plugin_key

		# Phrase Pattern Matching
		self.matcher = nlp.shared_matcher()
		self.examples = examples
		self.patterns = self.generate_patterns(self.examples)
		if patterns: self.patterns += patterns
		self.docs = [self.nlp(phrase) for phrase in self.examples]
		self.intent_name = f"{self.plugin}:{self.key}"
		self.matcher.add(self.intent_name, self.patterns)
		self.id = self.nlp.vocab.strings[self.intent_name]
		self.lemmas = [{t.lemma_.lower() for t in doc if t.is_alpha} for doc in self.docs]

		# Stopwords stripped: "the"/"a"/"for" appear in nearly every phrase and
		# would let unrelated skills score against each other.
		self.content_lemmas = [
			{t.lemma_.lower() for t in doc if t.is_alpha and not t.is_stop}
			for doc in self.docs
		]

		#Argument Pattern Matching
		self.arg_matcher = nlp.new_matcher()
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
		"""
		Turn each example into a pattern that tolerates ordinary variation.

		This used to emit a literal lemma per token, so a pattern built from
		"set a timer for 10 minutes" only ever matched that exact sequence -
		"set the timer for 1 minute" failed on both the determiner and the
		number, and even "set a timer for 1 minute" failed, because the 10 was
		compiled in. In practice only the verbatim examples matched.

		Three relaxations, all of which preserve the content words that
		actually identify the intent:

		  numbers      -> any number, since the value is an argument
		  determiners  -> optional and interchangeable (a / the / my / this)
		  politeness   -> optional ("can you", "please", "just")
		"""
		patterns = []
		for phrase in phrases:
			pattern = []
			for token in self.nlp(phrase):
				lemma = token.lemma_.lower()
				# spaCy's LEMMA matching is case sensitive and it capitalises
				# some lemmas ("i" -> "I"), so a lowercased pattern could never
				# match them. Accept either form.
				lemma_match = ({"IN": sorted({lemma, token.lemma_})}
							   if lemma != token.lemma_ else lemma)
				if token.like_num:
					pattern.append({"LIKE_NUM": True})
				elif token.pos_ == "DET":
					# The one place a bare POS is right: a/the/my/this really
					# are interchangeable in front of a noun.
					pattern.append({"POS": "DET", "OP": "?"})
				elif lemma in OPTIONAL_LEMMAS or token.pos_ == "PRON":
					# Optional, but still itself. Generalising a pronoun to
					# {"POS": "PRON"} threw away its identity - "nothing"
					# became "any pronoun" and matched the "me" in "tell me a
					# joke", so its skill swallowed unrelated phrases.
					pattern.append({"LEMMA": lemma_match, "OP": "?"})
				else:
					pattern.append({"LEMMA": lemma_match})

			# Every pattern needs at least one token that both identifies a
			# word and is required. All-optional patterns match at every
			# position in every document.
			if pattern and not any("OP" not in token for token in pattern):
				pattern = [{k: v for k, v in token.items() if k != "OP"}
						   for token in pattern]

			if not any("LEMMA" in token or "LIKE_NUM" in token for token in pattern):
				continue

			if pattern:
				patterns.append(pattern)
		return patterns
	
	def extract_args(self, doc):
		"""
		Pull argument values out of a matched phrase.

		The matched span includes whatever tokens the pattern needed to anchor
		on, which are not part of the value: "call it Eggs" matched the name
		pattern and was handed to the skill verbatim, so the timer was named
		"call it Eggs". Leading anchor tokens are stripped, as long as
		something is left - a value that is entirely stopwords stays as it is
		rather than becoming empty.
		"""
		args = {}
		if not self.arguments:
			# Running an empty Matcher emits spaCy's W036 warning on every
			# parse of an argument-less skill, which is most of them.
			return args
		for match_id, start, end in self.arg_matcher(doc):
			arg_label = doc.vocab.strings[match_id]
			span = doc[start:end]

			trimmed = start
			while trimmed < end - 1:
				token = doc[trimmed]
				if token.pos_ in ("VERB", "AUX", "PRON", "DET", "ADP", "PART") or token.is_stop:
					trimmed += 1
				else:
					break

			value = doc[trimmed:end].text.strip()
			args[arg_label] = value or span.text
		return args

	def get_patterns(self):
		return self.normalized_patterns

	def call(self, *args, **kwargs):
		if self.func:
			self.func(*args ,**kwargs)


class SkillIntentEngine:

	def __init__(self, client):
		self.client = client

		self.phases = ["matcher", "rule"]

		self.registered: dict[str, list[Skill]] = {}
		self.skill_lib = {}
		self.id2skill = {}
		self.wake_args = []

	@property
	def nlp(self):
		return nlp.model()

	@property
	def matcher(self):
		return nlp.shared_matcher()

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
		self.rebuild_idf()
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
			pm = nlp.new_phrase_matcher(attr="LEMMA")
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

	def rebuild_idf(self) -> None:
		"""
		How discriminating each lemma is, from how many skills use it.

		Without this every shared word counts the same, so "clear all
		notifications" scored identically against notifications-open and
		notifications-empty - both contain "notification", and the word that
		actually decides it ("clear") was worth no more than the noise.
		"""
		skills = self.skills()
		total = max(1, len(skills))
		document_frequency = {}
		for skill in skills:
			seen = set()
			for example in skill.content_lemmas:
				seen |= example
			for lemma in seen:
				document_frequency[lemma] = document_frequency.get(lemma, 0) + 1

		self.idf = {
			lemma: math.log(1 + total / count)
			for lemma, count in document_frequency.items()
		}

	def lemma_weight(self, lemma: str) -> float:
		return getattr(self, "idf", {}).get(lemma, math.log(1 + len(self.skills()) or 1))

	def rule_match(self, input_content: set) -> tuple:
		"""
		Fallback for phrases the Matcher missed: how much of a skill's example
		does this utterance cover?

		Scored against the example rather than the input, so a long rambling
		request still matches a short skill. Thresholded, since scoring every
		skill against every phrase will always produce a nearest match -
		"nothing matched" has to remain a possible answer.
		"""
		best_skill, best_score = None, 0.0
		if not input_content:
			return None, 0.0

		if not hasattr(self, "idf"):
			self.rebuild_idf()

		for skill in self.skills():
			for example in skill.content_lemmas:
				if not example:
					continue

				matched = 0.0
				total = 0.0
				used = set()
				for lemma in example:
					weight = self.lemma_weight(lemma)
					total += weight
					best_token = 0.0
					for candidate in input_content:
						similarity = fuzzy_equal(lemma, candidate)
						if similarity > best_token:
							best_token, best_match = similarity, candidate
					if best_token:
						matched += weight * best_token
						used.add(best_match)

				if not total:
					continue

				recall = matched / total
				# Precision keeps a long rambling phrase from matching a tiny
				# example on one shared word. Harmonic mean, so both have to
				# hold up.
				precision = len(used) / max(1, len(input_content))
				score = (2 * recall * precision / (recall + precision)) if (recall + precision) else 0.0

				if score > best_score:
					best_score, best_skill = score, skill

		if best_score < FALLBACK_DEFAULT_RULE_SCORE:
			return None, 0.0

		self.client.log("info",
			f"[SkillIntentEngine] Rule phase matched '{best_skill.key}' @ {round(best_score, 2)}")
		return best_skill, best_score

	def parse(self, phrase: str, use_skill: bool = True) -> Intent | None:
		start = time.time()

		self.client.log("info", f"[SkillIntentEngine] Searching for Intent in '{phrase}' ...")

		original_text = phrase
		match_doc = self.nlp( phrase )
		results = self.matcher(match_doc)
		best_skill : Skill = None
		best_score = -1

		input_lemmas = {t.lemma_.lower() for t in match_doc if t.is_alpha}
		input_content = {t.lemma_.lower() for t in match_doc if t.is_alpha and not t.is_stop}
		if not input_content:
			# A mishearing can land on a stopword - "weather" becomes
			# "whether", which spaCy treats as one - leaving nothing to score
			# against at all. Fall back to every alphabetic lemma so the fuzzy
			# comparison still has something to work with.
			input_content = {t.lemma_.lower() for t in match_doc if t.is_alpha}
		candidates = [self.id2skill[m[0]] for m in results]

		for skill in candidates:
			for example_lemmas in skill.lemmas:
				# Both coverages, not just one. Dividing the overlap by the
				# example length alone gives a one-word example a perfect score
				# whenever that word appears anywhere in the utterance - so
				# "cancel the 5 minute timer" scored 1.0 against the nevermind
				# skill's "cancel", tied with the timer skill's own example,
				# and won the tie by sitting earlier in the sentence. Backing
				# out of the assistant ate every targeted cancel there was.
				#
				# The harmonic mean asks the second question too: how much of
				# what was said does this example actually account for? This is
				# the same formula the rule phase already used, for the same
				# reason.
				if not example_lemmas:
					continue
				overlap = len(input_lemmas & example_lemmas)
				if not overlap:
					continue
				recall    = overlap / len(example_lemmas)
				precision = overlap / max(1, len(input_lemmas))
				score = (2 * recall * precision / (recall + precision)
				         if (recall + precision) else 0.0)
				if score > best_score:
					best_score = score
					best_skill = skill

		if best_skill is None and candidates:
			# A pattern matched on something that is not a lemma - a number, a
			# part of speech - so there was nothing to score against, but the
			# match itself is real. Kept, because the old scoring accepted
			# these by accident (0 beat its -1 starting value) and dropping
			# them now would be a silent regression.
			best_skill, best_score = candidates[0], 0.0

		if not best_skill or best_score < MATCHER_CONFIDENT_SCORE:
			# The rule phase, which used to run only when the Matcher found
			# nothing at all. That was the wrong condition: a catch-all skill
			# with one-word examples is a candidate for anything containing
			# that word, so it "won" uncontested and the rule phase never ran.
			#
			# "stop the eggs timer" is the shape that exposed it. No pattern
			# can match an arbitrary name - the examples compile "pasta"
			# literally - so the only candidate was the nevermind skill's
			# "stop", at 0.40, and cancelling a named timer routed to backing
			# out of the assistant instead.
			#
			# A confident Matcher hit is still taken as-is; a weak one now has
			# to beat the rule phase, which scores against content words with
			# their discriminating weight.
			rule_skill, rule_score = self.rule_match(input_content)
			if rule_skill is not None and rule_score > best_score:
				best_skill, best_score = rule_skill, rule_score


		if not best_skill:
			self.client.log("info", f"[SkillIntentEngine] Matcher found Nothing : {round(time.time() - start, 3)}s")
			self.client.ASSIST_STATUS = "LIVE"
			if use_skill:
				# Nothing understood it. Anything subscribed gets a chance to
				# answer instead - see the AI fallback plugin. Only on the real
				# input path, so a use_skill=False probe stays side-effect free.
				self.client.iterate_event_callables("on_assistant_fallback", phrase)
			return None, None
		else:
			if use_skill:
				self.client.ASSIST_STATUS = "ACTING"
				Thread(target = self.__skill_call_with_status_update, args = [best_skill, match_doc]).start()
			else:
				self.client.ASSIST_STATUS = "LIVE"
			self.client.log("info", f"[SkillIntentEngine] Matcher found '{best_skill.key}' @ {best_score} : {time.time() - start}s")
			return best_skill, original_text