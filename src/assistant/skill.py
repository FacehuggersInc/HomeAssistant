from __future__ import annotations

import re
import time
import math
from rapidfuzz.distance import JaroWinkler
import traceback
from threading import Thread
from typing import TYPE_CHECKING, Callable, Optional, List

from src.assistant import addressed, nlp

if TYPE_CHECKING:
	from src.main import Client

PRIMARY_THRESHOLD = 0.70
# Tuned by sweeping a 52-phrase corpus (see the eval harness in the repo
# history): 0.50 scores higher overall but lets "tell me a joke" answer with
# the weather. A miss costs the user a repeat; a misfire makes the assistant
# do the wrong thing, so the highest threshold with zero misfires wins.
FALLBACK_DEFAULT_RULE_SCORE = 0.55

#Words that can carry a request but can never BE one.
#
#"Tell me more" and "what does that mean" have exactly one content word each -
#`tell` and `mean` - and no subject at all. Every skill whose command reduces
#to that same word therefore scores 1.0 against them, so "tell me more" was
#answered with the time, and once a "tell me about X" skill existed it was
#answered with a Wikipedia search instead. Which one wins is an accident of
#what else is installed.
#
#A phrase made ONLY of these is a follow-up to something already said. It has
#no topic of its own, so no skill can be about it, and the only thing that can
#answer it is whatever holds the conversation - which is the fallback, and
#which is now handed the turn before it.
SUBJECTLESS_LEMMAS = frozenset({
    "tell", "say", "speak", "talk", "mean", "explain", "elaborate",
    "expand", "clarify", "describe", "detail", "continue", "repeat",
    "more", "further", "else", "again", "go", "know", "understand",
    "thing", "one", "ok", "okay", "yeah", "yes", "sure", "please",
    # "What" is never a subject. It survives the stopword filter in phrases
    # like "what else", where it is the only alphabetic lemma left, and
    # without it here that phrase is not recognised as the follow-up it is.
    "what",
})

#And the subset of those that are bare verbs of asking. A rule-phase match
#whose ENTIRE overlap is one of these has agreed with an example about the
#word "tell" and nothing else, which is not agreement about anything.
GENERIC_LEMMAS = frozenset({
    "tell", "say", "show", "give", "find", "get", "know", "ask", "want",
    "need", "look", "go", "do", "make", "take", "more", "thing", "one",
    "please", "help", "use", "put", "let", "speak", "talk",
})

# Above this, a Matcher hit is taken without consulting the rule phase. Below
# it, the rule phase gets to contest - a pattern can only ever match the words
# somebody wrote into an example, so a phrase carrying an arbitrary name or an
# unanticipated determiner reaches the Matcher as a weak partial hit at best.
MATCHER_CONFIDENT_SCORE = 0.75

#The most a skill's own vocabulary can lift its score.
#
#Small on purpose. It says "this phrase carries a word that is mine", which
#is evidence rather than an answer - large enough to lift a skill over the
#floor when nothing else speaks for it, small enough that a skill matching
#the SHAPE of the question still wins.
DISTINCTIVE_BONUS = 0.20

#How many skills get a turn at one phrase before it goes to the fallback.
#
#Every decline is another handler running, and a handler that declines has
#usually already done the work that told it to - a lookup, a title check. Four
#is more than any real phrase needs and short enough that a pathological one
#cannot spend a second walking the whole registry.
MAX_SKILL_ATTEMPTS = 4


class SkillDeclined(Exception):
    """
    Raised by a skill that matched but cannot answer after all.

    Matching happens on the words, and the words are sometimes not enough.
    A Wikipedia skill only knows the question was not for it once the article
    comes back and its title turns out to be about somebody else; a lookup
    skill only knows the subject is not a series once it has looked. Both are
    the highest-scoring skill for the phrase and both are wrong, and the
    engine cannot tell before the handler runs.

    Declining hands the phrase to the next-best skill, and to the fallback if
    nothing else takes it. It is NOT an error: the skill worked correctly and
    the answer is that this was not its question.

    Raise it before showing anything. A panel that opens and then reports a
    failure is worse than one that never opened - the phrase still has
    somewhere else to go, and the person watching cannot tell the difference
    between "not mine" and "broken".
    """

    def __init__(self, reason: str = ""):
        super().__init__(reason or "not this skill")
        self.reason = reason or "not this skill"


#Returned instead of raising, for a handler that would rather not.
DECLINED = SkillDeclined

# Words that carry no intent and vary freely between speakers. Made optional
# in generated patterns so their presence or absence never decides a match.
#How alike two lemmas must be to count as the same word misheard.
#
#Measured on JaroWinkler, which weights a shared prefix - which is how a
#mishearing behaves. Against real ones it has room: "axolata" for "axolotl"
#scores 0.886, "whether" for "weather" 0.914, "narudo" for "naruto" 0.933,
#"aplication" for "application" 0.976. The words it must NOT accept sit
#below: "alarm" against "alert" is 0.787, "moon" against "noon" 0.833.
FUZZY_MIN_RATIO = 0.86

#The bar when a fuzzy match is the ONLY thing holding a match together - no
#lemma matched outright and the whole score rests on one word being nearly
#another.
#
#A single substituted character in a short word is indistinguishable from a
#mishearing by any string metric: "timer" against "tiger" scores 0.893 and
#"whether" against "weather" 0.914. Nothing separates those, so a lone fuzzy
#match has to be surer of itself than one repairing a word inside a phrase
#that already agrees.
FUZZY_SOLE_RATIO = 0.90
FUZZY_MIN_LENGTH = 4

OPTIONAL_LEMMAS = {
	"please", "can", "could", "would", "will", "just", "kindly",
	"for", "to", "new", "some", "of", "up", "now", "then", "and",
}

def _is_content(token) -> bool:
	"""
	Whether a token counts towards the score.

	`is_alpha` alone drops every digit, which mattered more than it looks.
	"What is 5 times 3" reduced to the single lemma "time" - the numbers
	vanished - so precision came out 1/1 and it scored perfectly against any
	timer example containing the word. Counting the numbers gives three content
	words, two of which match nothing, and the phrase falls through to the AI
	where it belongs.

	A number is also a content word in its own right: "for 5 minutes" is not
	the same request as "for 50 minutes".
	"""
	return bool(token.is_alpha or token.like_num)


def fuzzy_equal(a: str, b: str) -> float:
	"""
	How alike two lemmas are, 0..1.

	Whisper substitutes acoustically similar words - "whether" for "weather",
	"notifcations", "aplication" - and no amount of extra example phrasings
	recovers those. Short tokens are compared exactly, since at three or four
	characters almost everything is close to everything.

	JaroWinkler, because it weights a shared PREFIX and a mishearing keeps
	one. A plain edit ratio does not, and inverts on the pairs that matter:
	it puts "axolata" against "axolotl" at 0.714 and "ocean" against "clean"
	at 0.800 - the wrong word closer than the right one, which no single
	threshold can separate.
	"""
	if a == b:
		return 1.0
	if len(a) < FUZZY_MIN_LENGTH or len(b) < FUZZY_MIN_LENGTH:
		return 0.0
	if abs(len(a) - len(b)) > 3:
		return 0.0

	# One being a prefix of the other is a different word, not a mishearing.
	#
	# A mishearing garbles the middle at roughly the same length - "whether"
	# for "weather", "notifcation" for "notification". Adding or dropping a
	# whole suffix changes the word: "time" is a prefix of "timer" and scores
	# 0.889, which is HIGHER than "whether" against "weather" at 0.857, so no
	# threshold separates them. "What is 5 times 3" therefore matched the timer
	# skill and never reached the AI.
	#
	# Inflections are safe to reject here because lemmatisation has already
	# dealt with them: "cancels" arrives as "cancel". A prefix relationship
	# that survives that is two different words.
	if a.startswith(b) or b.startswith(a):
		return 0.0

	ratio = JaroWinkler.normalized_similarity(a, b)
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


SKILL_KINDS = ("act", "ask")

#Words that carry no information about WHICH skill was meant.
#
#Wider than spaCy's stop list, which is about grammar. These are words that
#appear across so many skills that their presence says nothing: every skill
#has a "what", a "how" and a "my", and half of them have a "get" or a "set".
#
#Used to work out what a skill's own vocabulary actually is - see
#Skill.distinctive.
FILLER_LEMMAS = frozenset({
	"what", "which", "who", "when", "where", "why", "how", "whats",
	"is", "are", "be", "am", "was", "were", "do", "does", "did", "have",
	"has", "had", "get", "got", "make", "made", "give", "tell", "say",
	"can", "could", "would", "will", "shall", "should", "may", "might",
	"the", "a", "an", "this", "that", "these", "those", "it", "its",
	"i", "me", "my", "you", "your", "we", "us", "our", "he", "she", "they",
	"to", "of", "for", "in", "on", "at", "by", "with", "from", "about",
	"and", "or", "but", "if", "so", "then", "than", "as", "like",
	"please", "thanks", "now", "just", "some", "any", "all", "much", "many",
	"there", "here", "long", "until", "till", "go", "come", "want", "need",
	"s", "t", "re", "ve", "ll", "nt", "not",
})

#Stopwords that inverse a command rather than decorate it.
#
#Scoring drops stopwords because "the" and "your" distinguish nothing - but
#between two halves of a switch these are the only words that do. "Turn the
#sound back on" and "turn off your sounds" share every content word.
POLARITY_LEMMAS = frozenset({
	"on", "off", "back", "no", "not", "never", "again", "un",
	"up", "down", "out", "in", "stop", "start", "enable", "disable",
	"resume", "pause", "cancel", "clear",
})

#Words that hold a frame together rather than distinguish it. A frame may
#lose any of these to a mishearing and still be the frame it was; losing one
#of its own telling words means it is a different question.
FRAME_GRAMMAR = frozenset({
	"what", "whats", "which", "who", "whos", "when", "where", "why", "how",
	"is", "are", "was", "were", "be", "am", "s",
	"do", "does", "did", "done",
	"a", "an", "the", "of", "for", "to", "in", "on", "at", "by", "with",
	"i", "me", "my", "you", "your", "it", "its", "that", "this", "there",
	"can", "could", "would", "will", "should", "please", "some", "any",
	"and", "or", "else", "up", "about",
})

#Marks the hole in a frame. One per frame - a question has one subject, and a
#second hole makes the boundary between them unknowable.
HOLE = re.compile(r"\{(\w+)\}")


class Frame:
	"""
	A fixed phrase with a hole in it: `what does {subject} mean`.

	Matching is ALIGNMENT rather than scoring. The words before the hole must
	open the utterance, the words after it must close it, and something has
	to sit in between. That is the whole test.

	It exists because word overlap cannot separate two questions that differ
	only in where the subject sits. "What does X mean" and "what does X look
	like" share every leading word; the part that decides comes AFTER the
	hole, and a bag of lemmas has thrown the order away by then. Scored on
	overlap the two are indistinguishable, and which one wins is noise.

	The hole is also how the subject gets extracted, and extracted EXACTLY.
	A prefix anchor takes everything to the end of the utterance, so "how
	many episodes does frieren have" yields `frieren have` and needs a list
	of trailing words to strip afterwards - a list that has to know "have" is
	scaffolding and "Air" is a real title. A frame knows where the subject
	ends because it says so.
	"""

	#How far the subject boundaries may move from where the fixed wording says
	#they are. One word covers a dropped article or a contraction, which is
	#what a transcriber actually does; more turns the search into a scan.
	SLACK = 2

	#Charged per word sitting where fixed wording should be that the frame
	#did not account for.
	SPARE_PENALTY = 0.05

	#What a validated typed hole adds. The predicate has confirmed the subject
	#against real data, which is evidence of a different order from words
	#lining up - and the alternative is a coin toss on a hundredth of a point.
	TYPED_BONUS = 0.12

	#Below this the frame did not fit. It is not the routing floor - that is
	#MINIMUM_SCORE, applied to everything - only the point past which a
	#partial alignment stops being worth reporting.
	MINIMUM = 0.35

	def __init__(self, text: str, model, skill=None):
		self.text = " ".join(str(text or "").split())
		found = HOLE.search(self.text)
		if not found:
			raise BadSkill(
				f"{getattr(skill, 'key', '?')}: the frame {text!r} has no "
				f"hole in it. A frame needs one - \"what does {{subject}} "
				f"mean\" - or it is just a phrase.")
		if len(HOLE.findall(self.text)) > 1:
			raise BadSkill(
				f"{getattr(skill, 'key', '?')}: the frame {text!r} has more "
				f"than one hole. Where one ends and the next begins cannot "
				f"be worked out from the words between them.")

		self.name = found.group(1)
		before = self.text[:found.start()].strip()
		after = self.text[found.end():].strip()

		# Compared as lowercase text, not lemmas. A frame is a fixed phrase
		# and its whole value is being literal: lemmatising "does" to "do"
		# would let "what do X mean" align, which is fine, but lemmatising
		# the SUFFIX is what blurs "mean" and "meaning" into the same frame
		# as everything else.
		self.prefix = tuple(t.lower_ for t in model(before) if not t.is_space)
		self.suffix = tuple(t.lower_ for t in model(after) if not t.is_space)

		if not self.prefix and not self.suffix:
			raise BadSkill(
				f"{getattr(skill, 'key', '?')}: the frame {text!r} is "
				f"nothing but a hole, so it matches every utterance.")

		# Why a typed hole last refused, if it refused by raising. Read by
		# align_frames so a broken predicate is reported once per utterance
		# rather than silently losing its skill for good.
		self._validator_error = ""

		# Every word the frame supplies itself, for the no-subject check.
		self._fixed = set(self.prefix) | set(self.suffix)

		# The words that make this frame what it is, as opposed to the
		# grammar holding it together. "Definition", "mean", "look", "like" -
		# not "what", "is", "the", "of".
		#
		# They are not interchangeable evidence. "What is the definition of
		# {word}" matches "what is the capital of peru" on four fixed words
		# out of five, and the one it misses is the only one that mattered -
		# so a question about Peru arrived at the dictionary. A frame that
		# loses its own distinguishing wording has not been recognised, it
		# has been approximated.
		self._telling = {word for word in self._fixed
						 if word.isalpha() and word not in FRAME_GRAMMAR}

		# How specific this frame is. The frame with the most fixed words
		# wins, which is the entire ranking rule - no weighting, no IDF.
		# IDF exists to GUESS which words carry the meaning; a frame says so.
		self.weight = len(self.prefix) + len(self.suffix)

	@staticmethod
	def _same(said: str, wanted: str) -> float:
		"""
		1.0 for the same word, less for one nearly it, 0 for neither.

		The same repair the act track applies, for the same reason. A frame
		compares fixed WORDING, and nobody says a fixed phrase the same way
		twice: "mean" arrives as "means", "definition" as "definiton",
		"about" as "abut", "seasons" as "season". Compared literally every
		one of those loses the frame outright - and losing it on a telling
		word is worse than losing it on grammar, because the telling word is
		what the guard below insists on.
		"""
		if said == wanted:
			return 1.0

		# The same word, inflected. Frames compare SURFACE forms - the act
		# track lemmatises before it compares and frames deliberately do not,
		# because a frame's value is being literal - so "mean" against
		# "means" and "seasons" against "season" turn up constantly, and
		# `fuzzy_equal` refuses them on the prefix rule. That rule is right
		# where it is: after lemmatisation, one word being the front of
		# another means they are different words. Here it is the ordinary
		# case, and refusing it sent "how many season does naruto have" to
		# the episode skill.
		short, long = sorted((said, wanted), key=len)
		if long.startswith(short) and len(short) >= 3:
			if long[len(short):] in ("s", "es", "d", "ed", "ing", "n"):
				return 0.97
		if len(short) >= 4 and short.endswith("y") and \
				long == short[:-1] + "ies":
			return 0.97

		return fuzzy_equal(said, wanted)

	def align(self, words: tuple, validator=None):
		"""
		(score, subject) for how well this frame fits `words`, or None.

		Scored, not binary, and scored on the SAME formula the act track
		uses over lemma overlap - so a frame and an example produce numbers
		that mean the same thing and can share one ranked list. Recall is
		how much of the frame's fixed wording the utterance carried;
		precision is how much of the utterance outside the subject the frame
		accounted for; harmonic mean of the two. A frame that fits exactly
		scores 1.0 for the same reason an identical example does.

		Partial on purpose. Binary alignment is a cliff: the transcriber
		contracts "what does" to "whats", one fixed word is gone, and the
		frame that should have matched scores nothing at all. Allowing a
		fixed word to be missing or wrong turns that into 0.86 instead of 0,
		and the right frame still wins.

		The boundaries are searched rather than assumed, because a missing
		word moves them. `SLACK` bounds the search: without it a long
		utterance would be scanned end to end for a subject that could be
		anywhere.
		"""
		total_fixed = len(self.prefix) + len(self.suffix)
		if not words or not total_fixed:
			return None

		best = None
		lowest = max(0, len(self.prefix) - self.SLACK)
		highest = min(len(words), len(self.prefix) + self.SLACK + 1)
		for start in range(lowest, highest):
			last = max(start + 1, len(words) - len(self.suffix) - self.SLACK)
			for end in range(last, len(words) + 1):
				if end <= start:
					continue
				head, tail = words[:start], words[end:]

				# Counted from the OUTSIDE in: a prefix is anchored at the
				# start of the utterance and a suffix at the end, so that is
				# where each has to line up.
				hit = sum(self._same(a, b) for a, b in zip(head, self.prefix))
				hit += sum(self._same(a, b) for a, b in
						   zip(reversed(tail), reversed(self.suffix)))
				if not hit:
					continue

				# Precision is against the WHOLE utterance, subject included.
				#
				# The subject is a hole the frame did not explain, so it
				# counts against it - which is what makes a frame's score
				# reflect how much evidence it carries rather than only how
				# cleanly it fitted. Measured against the non-subject words
				# alone, "what is {subject}" scores a perfect 1.0 on any
				# "what is X" whatsoever, and beat every skill that actually
				# knew what X was: "what is the weather" went to the
				# encyclopedia.
				#
				# It also puts specificity into the score instead of leaving
				# it as a tie-break. Four fixed words explaining six beats
				# two explaining four, which is the right order.
				recall = hit / total_fixed
				precision = hit / len(words)
				if not (recall and precision):
					continue
				score = 2 * recall * precision / (recall + precision)

				# Words sitting where fixed wording should be, that the frame
				# did not account for. Without this a loose frame swallows a
				# specific one: "what is {subject}" would take "does an
				# axolotl look like" as its subject and score well on a
				# fluke.
				spare = max(0, len(head) - len(self.prefix)) \
						+ max(0, len(tail) - len(self.suffix))
				score -= self.SPARE_PENALTY * spare


				# A frame has to keep its own telling words. Losing grammar
				# to a mishearing is survivable; losing the word that names
				# the question is not being recognised, it is being
				# approximated.
				if self._telling:
					outer = tuple(head) + tuple(tail)
					kept = sum(1 for word in self._telling
							   if any(self._same(heard, word) for heard in outer))
					if kept * 2 < len(self._telling):
						continue

				# The subject cannot be made only of the frame's own wording.
				#
				# Searching the boundaries means they can slide onto a fixed
				# word: "what does mean" has nothing in the hole, but moving
				# the start by one offers "does" as the subject and scores it
				# 0.80. A question with no subject belongs to nobody, and
				# looking up the word "does" is worse than not answering.
				taken = words[start:end]
				if all(word in self._fixed for word in taken):
					continue

				# A typed hole. The shape fitting is not enough when the
				# shape is ambiguous: "when is the next holiday" and "when is
				# my dentist appointment" both fit `when is {holiday}`, and
				# only the data says neither is one.
				#
				# Checked inside the search, not after it, so a candidate
				# whose subject is the wrong kind of thing gives way to a
				# lower-scoring one whose subject is right.
				typed = False
				if validator is not None:
					try:
						if not validator(" ".join(taken)):
							continue
					except Exception as exc:
						# Recorded, not swallowed. A predicate that raises
						# rejects every alignment it is asked about, so the
						# skill silently never matches - which looks exactly
						# like a skill nobody said the right words to. The
						# reason has to reach the log or there is nothing to
						# find.
						self._validator_error = f"{type(exc).__name__}: {exc}"
						continue
					typed = True

				# A hole that was type-checked and passed is worth more than
				# one holding whatever happened to be there. The data has
				# confirmed the subject is the right KIND of thing, which no
				# amount of word overlap can establish - "what date is
				# easter" scored 0.857 as a shape against a date skill's
				# 0.867 as a bag of words, and the shape was right about a
				# real holiday while the bag of words had only "date".
				#
				# A margin, not a licence: it cannot lift a frame that did
				# not fit, only settle one that did against a rival scoring
				# on weaker evidence.
				if typed:
					score = min(1.0, score + self.TYPED_BONUS)

				if score > 0 and (best is None or score > best[0]):
					best = (score, " ".join(taken))

		if best is None or best[0] < self.MINIMUM:
			return None
		return best

	def __repr__(self):
		return f"Frame({self.text!r})"


class BadSkill(Exception):
	"""
	A skill declared in a way that cannot work.

	Raised at construction, which is registration time - so a plugin with a
	malformed skill fails to load and says why, rather than loading and
	quietly never matching. A skill that never matches looks exactly like a
	skill nobody has said the right words to, and that is a bug that hides
	for months.
	"""


class Skill:
	def __init__(
		self,
		wake_word: str,
		skill_key: str,
		plugin_key: str,
		kind: str,
		examples: List[str] = None,
		frames: List[str] = None,
		holes: dict = None,
		patterns : list[list[dict]] = None,
		opposite: str = None,
		owns: List[str] = None,
		arguments: dict[str, list[list[dict]]] = None,
		payload: dict[str, list[str]] = None,
		wants_phrase: bool = False,
		func: Optional[Callable] = None,
		words_leeway: int = 5
	):
		self.nlp = nlp.model()
		
		self.wake = wake_word
		self.key = skill_key
		self.plugin = plugin_key

		# What sort of thing this is, and therefore how it gets matched.
		#
		#   act   Something to DO. Matched on word overlap against examples,
		#         with fuzzy repair for a misheard word. "Set a timer for ten
		#         minutes", "cancel the alarm".
		#
		#   ask   Something to ANSWER. Matched on frames - a fixed phrase
		#         with a hole in it - because what separates two questions is
		#         usually where the subject sits rather than which words
		#         appear. "What does {subject} mean" and "what does {subject}
		#         look like" contain the same words in the same order and are
		#         different questions.
		#
		# Two tracks that cannot reach each other, so asking about a thing
		# can never run the command that changes it: "how many timers do i
		# have" cancelled a timer, because both skills own the word.
		self.kind = str(kind or "").strip().lower()
		if self.kind not in SKILL_KINDS:
			raise BadSkill(
				f"{plugin_key}:{skill_key} declares kind={kind!r}. "
				f"It has to be one of {', '.join(SKILL_KINDS)} - 'act' for "
				f"something to do, 'ask' for something to answer.")

		# The skill that undoes this one, by key.
		#
		# Two halves of a switch share every word except the one that
		# inverts it, and that word is worth exactly as much as any other to
		# a scorer over unordered lemmas. "Unmute the sounds" scored
		# identically against mute-off, which matched "unmute", and mute-on,
		# which matched only "sounds" - and the tie went to whichever was
		# reached first, so asking for the sound back turned it off.
		#
		# Declared rather than inferred. Nothing in the words says these two
		# are opposites; it is a fact about what the skills DO.
		self.opposite = str(opposite).strip() if opposite else None

		# Words that mean this skill, near-conclusively.
		#
		# Scoring weights a word by how FEW skills use it, which is a
		# statistic about the example lists rather than a fact about meaning.
		# It rates "calendar" at 2.75 and "today" at 2.28 - almost the same -
		# so "whats on my calendar today" went to the date skill, which owns
		# "today" thoroughly. No amount of weighting fixes that without
		# moving every other skill's numbers too.
		#
		# A narrowing filter rather than a selector. Where an utterance
		# carries an owned word, only the skills owning it compete, and
		# scoring still picks between them - so several skills may own one
		# word, and "calendar" belongs to the calendar's skills AND to
		# go-to-page, which navigates to it.
		#
		# Own only what is conclusive. If a phrasing exists that carries the
		# word and is not yours, it is not yours to own: "calendar" and
		# "appointment" qualify, "event", "today" and "week" do not.
		self.owns = {str(word).strip().lower()
					 for word in (owns or []) if str(word).strip()}

		examples = list(examples or [])
		frames = list(frames or [])

		if self.kind == "act" and not (examples or patterns):
			raise BadSkill(
				f"{plugin_key}:{skill_key} is an 'act' skill with no "
				f"examples and no patterns. There is nothing to match it "
				f"against, so it would register and never fire.")
		if self.kind == "ask" and not frames:
			raise BadSkill(
				f"{plugin_key}:{skill_key} is an 'ask' skill with no frames. "
				f"A frame is a phrase with a hole in it - "
				f"\"what does {{subject}} mean\" - and without one there is "
				f"nothing to align an utterance against.")
		if self.kind == "act" and frames:
			raise BadSkill(
				f"{plugin_key}:{skill_key} is an 'act' skill carrying "
				f"frames. Frames belong to 'ask' skills; an act skill "
				f"matches on examples and patterns.")
		if self.kind == "ask" and examples:
			raise BadSkill(
				f"{plugin_key}:{skill_key} is an 'ask' skill carrying "
				f"examples. An ask skill matches on frames - the examples "
				f"would be scored by nothing and silently ignored.")

		# {name: [anchor phrase, ...]}. Everything after an anchor is the
		# value, taken verbatim - see payload_span().
		#
		# Set up first: the patterns and the lemma sets below are all built
		# from the command part of each example, which needs the anchors.
		# Whether the whole utterance is passed in as `phrase`. For a skill
		# whose behaviour depends on which of its examples was said - "stop"
		# and "nevermind" mean different things - rather than on an argument
		# extracted from it.
		self.wants_phrase = bool(wants_phrase)

		self.payload = payload or {}
		self._anchors = []
		for name, phrases in self.payload.items():
			for phrase in phrases:
				tokens = tuple(t.lower_ for t in nlp.model()(phrase)
							   if not t.is_space)
				if tokens:
					self._anchors.append((name, tokens))
		# Longest first, so "put on" wins over "put".
		self._anchors.sort(key=lambda entry: len(entry[1]), reverse=True)

		# The words this skill is LIKELY to carry, filler removed.
		#
		# Built from everything it is matched against, so a skill listing
		# "when is christmas", "when is easter" and "when is thanksgiving"
		# ends up knowing those three words belong to it - without anyone
		# writing them down twice.
		#
		# Not ownership. An owned word takes the phrase outright, which is
		# too strong for a subject: "christmas" owned outright means asking
		# what Christmas IS returns a date. This only raises confidence that
		# the skill is the one being spoken to, and the rest of the pipeline
		# still decides.
		self.distinctive = set()
		_sources = list(examples) + [HOLE.sub(" ", t) for t in frames]
		for text in _sources:
			for token in self.nlp(str(text)):
				word = token.lemma_.lower()
				if _is_content(token) and word not in FILLER_LEMMAS \
						and len(word) > 2:
					self.distinctive.add(word)

		# What each hole is allowed to contain, by hole name.
		#
		# A predicate the skill supplies: `{"holiday": store.is_holiday}`.
		# Where a hole has one, an alignment whose subject fails it is not a
		# match at all - which is how "when is {holiday}" tells a holiday
		# from a dentist appointment, since the wording is identical and only
		# the data differs.
		#
		# It runs during MATCHING, so it has to be cheap and free of side
		# effects - a set lookup, not a request. An exception is read as a
		# refusal rather than propagated, so a broken predicate loses its own
		# skill rather than the whole utterance.
		self.holes = dict(holes or {})

		# Frames, for an 'ask' skill. See Frame.
		self.frames = [Frame(text, self.nlp, self) for text in frames]
		unknown = set(self.holes) - {f.name for f in self.frames}
		if unknown:
			raise BadSkill(
				f"{plugin_key}:{skill_key} types the hole(s) "
				f"{sorted(unknown)}, which none of its frames has. A typed "
				f"hole that matches no frame silently never runs.")

		# Phrase Pattern Matching
		self.matcher = nlp.shared_matcher()
		self.examples = examples
		self.patterns = self.generate_patterns(self.examples)
		if patterns: self.patterns += patterns
		self.docs = [self.nlp(phrase) for phrase in self.examples]
		self.intent_name = f"{self.plugin}:{self.key}"
		self.matcher.add(self.intent_name, self.patterns)
		self.id = self.nlp.vocab.strings[self.intent_name]
		# Command parts, so an example is compared the same way an utterance
		# is - see _command_part.
		self.command_docs = [self._command_part(doc) for doc in self.docs]
		self.lemmas = [{t.lemma_.lower() for t in doc if _is_content(t)}
					   for doc in self.command_docs]

		# Stopwords stripped: "the"/"a"/"for" appear in nearly every phrase and
		# would let unrelated skills score against each other.
		self.content_lemmas = [
			{t.lemma_.lower() for t in doc if _is_content(t) and not t.is_stop}
			for doc in self.command_docs
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
			# An example carries a payload of its own - "put on some music" is
			# the command "put on" and a stand-in value. Compiling the value
			# into the pattern means only that exact title ever matches, which
			# defeats the point of declaring a payload at all.
			doc = self.nlp(phrase)
			doc = self._command_part(doc)
			for token in doc:
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
		# The WIDEST match per argument, not the last one found.
		#
		# A pattern with optional tokens matches at several lengths, and the
		# Matcher reports all of them. Assigning as they arrive means whichever
		# came last wins: "1 hour and 48 minutes" offers "1 hour" and
		# "48 minutes" as separate hits, and the timer was set for 48 minutes.
		#
		# Ties keep the later one, which is the previous behaviour for
		# everything that does not overlap.
		widest = {}
		for match_id, start, end in self.arg_matcher(doc):
			arg_label = doc.vocab.strings[match_id]
			if arg_label in widest and (end - start) < widest[arg_label][1]:
				continue
			widest[arg_label] = ((start, end), end - start)

		for arg_label, ((start, end), _width) in widest.items():
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

	## -- payload

	def _command_part(self, doc):
		"""
		The doc with this skill's payload removed.

		Applied to examples as well as to what somebody said, so a command is
		compared against a command. Comparing "put on" to "put on some music"
		measures the stand-in title, which is exactly the noise a payload
		exists to keep out.
		"""
		span = self.payload_span(doc)
		return doc[:span[1]] if span else doc

	def payload_span(self, doc) -> tuple:
		"""
		(name, start, end) for the opaque part of an utterance, or None.

		`start` is the first token of the value; `end` is the end of the doc.
		Anchors are searched anywhere rather than only at the beginning, so
		"can you play X" works as well as "play X".
		"""
		if not self._anchors:
			return None

		lowered = [token.lower_ for token in doc]
		for name, anchor in self._anchors:
			width = len(anchor)
			for index in range(len(lowered) - width + 1):
				if tuple(lowered[index:index + width]) != anchor:
					continue
				start = index + width
				if start >= len(doc):
					# The anchor with nothing after it - "play" on its own is
					# a legitimate request, it simply carries no value.
					return None
				return name, start, len(doc)
		return None

	def client_log(self, level: str, message: str) -> None:
		"""Log through the client where there is one, print where there is not."""
		client = getattr(self, "client", None)
		if client is not None and hasattr(client, "log"):
			client.log(level, message)
		else:
			print(message)

	def align_frames(self, words: tuple):
		"""
		(weight, name, subject) for the most specific frame that fits, or None.

		Most specific, meaning the one with the most fixed words. "What does
		an axolotl look like" fits `what does {subject} look like` on four
		fixed words and fits nothing else - `what does {subject} mean` is
		refused outright because "mean" does not close the utterance, which
		is the difference between a frame and a bag of words.
		"""
		best = None
		for frame in self.frames:
			frame._validator_error = ""
			found = frame.align(words, self.holes.get(frame.name))
			if frame._validator_error:
				self.client_log("warning",
					f"[Skill] {self.key}: the type on {{{frame.name}}} raised "
					f"{frame._validator_error} - the frame {frame.text!r} "
					f"cannot match while it does.")
			if found is None:
				continue
			score, subject = found
			# Score first, specificity only to break a tie. Two frames can
			# both fit perfectly and both be true readings - "what is
			# {subject}" and "what is the capital of {country}" - and the one
			# with more fixed wording is the more particular reading.
			key = (score, frame.weight)
			if best is None or key > best[0]:
				best = (key, frame.name, subject, frame)
		if best is None:
			return None
		return (best[0][0], best[1], best[2])

	def payload_value(self, doc) -> dict:
		"""
		The payload as {name: text}, taken **verbatim**.

		No trimming, unlike extract_args. A title is not a phrase with filler
		on the front: "Let It Be" is three stopwords and trimming leaves "be".
		"""
		span = self.payload_span(doc)
		if span is None:
			return {}
		name, start, end = span
		text = doc[start:end].text.strip()
		return {name: text} if text else {}

	def command_lemmas(self, doc, content_only: bool = False) -> set:
		"""
		The lemmas of an utterance with this skill's payload removed.

		Scoring a song title against a skill's examples measures nothing: the
		title is words no example contains, so a longer one scores worse - the
		opposite of what it should do. Removing it means "play <anything>"
		scores exactly as well as "play".
		"""
		span = self.payload_span(doc)
		limit = span[1] if span else len(doc)
		return {token.lemma_.lower() for token in doc[:limit]
				if _is_content(token) and (not content_only or not token.is_stop)}

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
			# Nothing else to rebuild.
			#
			# There was a per-plugin PhraseMatcher rebuilt here. It called a
			# method that does not exist, so reaching this line raised; and it
			# stored the result in `_pm_by_plugin`, which nothing ever read. It
			# was dead and broken at once, which is why neither showed up.
			# Matching goes through `self.matcher`, which `rebuild_idf()` and
			# the skills' own `add()` calls keep current.

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
	
	def __skill_call_with_status_update(self, ranked, match, ask_args=None):
		"""
		Run the best skill, and the next one if it declines.

		`ranked` is every skill that scored, best first. Most phrases use the
		first and stop; a skill that raises SkillDeclined says the words
		matched but the question was not for it, and the phrase carries on
		down the list. When nothing takes it the fallback gets it, which is
		the same place a phrase that matched nothing would have gone.
		"""
		text = match.text if hasattr(match, "text") else str(match)

		for attempt, skill in enumerate(ranked[:MAX_SKILL_ATTEMPTS]):
			# A turn opens per attempt, before the skill runs, so any answer
			# it produces lands against the question that caused it. Opening
			# it inside the skill would mean every skill remembering to - and
			# the ones that forgot would be invisible, because a missing turn
			# looks exactly like a question nobody asked.
			#
			# Re-opened on each attempt rather than once: a declining skill
			# may have opened a turn of its own, and leaving it there
			# attributes the answer to whichever skill did not give one.
			try:
				self.client.CONTEXT.begin(skill.key, text)
			except Exception:
				pass

			if skill.kind == "ask":
				# The frame already said where the subject begins and ends,
				# so there is nothing to extract and nothing to trim. Its
				# arguments ARE the match.
				args = dict((ask_args or {}).get(skill.key) or {})
			else:
				args = skill.extract_args(match)
				# The payload wins where both name the same argument: it is
				# the verbatim value, and extract_args would have trimmed it.
				args.update(skill.payload_value(match))
			if skill.wants_phrase:
				args["phrase"] = text
			if attempt:
				self.client.log("info",
					f"[SkillIntentEngine] Trying '{skill.key}' instead.")
			self.client.log("info", f"Intent Args: {args}")

			try:
				skill.call(**args) if args else skill.call()
			except SkillDeclined as declined:
				# Not an error. The skill ran, worked, and established that
				# this was not its question - which is a thing only it could
				# have known and only after looking.
				self.client.log("info",
					f"[SkillIntentEngine] '{skill.key}' declined: "
					f"{declined.reason}")
				continue
			except Exception:
				self.client.log("error",
					f"Error calling skill '{skill.key}'"
					f"{' with args' if args else ''}:\n---start---\n"
					f"{traceback.format_exc().strip()}\n---end---")

			# Taken. Either it answered or it failed in a way of its own,
			# and neither is the next skill's business.
			self.client.ASSIST_STATUS = "LIVE"
			return

		# Nobody took it. The same destination as a phrase that matched
		# nothing at all, because from the outside that is what happened.
		#
		# A skill matching and then declining is weaker evidence than it
		# looks - it matched on words, which is the thing that goes wrong in
		# front of a television - so this is gated like the rest. A phrase
		# that a skill looked at properly is nearly always shaped like a
		# request anyway, and passes.
		self.client.log("info",
			"[SkillIntentEngine] Every skill declined - passing it on.")
		self.client.ASSIST_STATUS = "LIVE"
		self._fall_back(text)

	def _fall_back(self, phrase: str, doc = None) -> None:
		"""
		Nothing here answered it. Hand it on, or drop it.

		**One funnel for three call sites.** A phrase reaches this from three
		places - nothing matched at all, a follow-up with no subject of its
		own, and every candidate skill declining - and each used to fire
		`on_assistant_fallback` itself. A gate written at one of them would
		have been missing from the other two.

		**Which branch it arrived from does not decide whether it is gated.**
		That was the first shape of this and it was wrong. The follow-up
		branch looked safe to exempt - it exists to hand "tell me more" to
		whatever remembers the turn before - but it identifies a follow-up by
		its content lemmas, and "I told him she wouldn't be there" reduces to
		{"tell"}, which is one of them. Exempting the branch exempted the
		television along with it.

		`_should_gate()` asks the question that actually matters instead:
		was the panel in a conversation. A real follow-up has something to
		follow, and a fragment with no antecedent has nothing to lose by
		being tested.
		"""
		context = None
		try:
			context = self.client.CONTEXT.last
		except Exception:
			pass

		if self._should_gate(context):
			verdict = addressed.is_addressed(doc if doc is not None
											 else self.parse_doc(phrase))
			if not verdict.addressed:
				# Dropped, and said so. This is the only record that the
				# panel heard something and chose to do nothing, and from
				# the room the two look identical.
				self.client.log("info",
					f"[SkillIntentEngine] Dropped '{phrase}' - "
					f"{verdict.reason} ({verdict.rule}).")
				self.client.iterate_event_callables(
					"on_assistant_unaddressed", phrase)
				return
			self.client.log("debug",
				f"[SkillIntentEngine] '{phrase}' passed the gate - "
				f"{verdict.reason} ({verdict.rule}).")

		self.client.iterate_event_callables(
			"on_assistant_fallback", phrase, extra=context)

	#How recently the panel must have answered for the next phrase to be
	#taken on trust as a follow-up.
	#
	#Deliberately not `CONTEXT.RELEVANT_FOR`, which is five minutes. That is
	#the right window for what "that" refers to and the wrong one for this:
	#it would mean a single real interaction exempts everything said in the
	#room for the next five minutes, which in front of a television is the
	#whole problem rather than an edge of it. A follow-up arrives in seconds.
	FOLLOW_UP_WITHIN = 30.0

	def _should_gate(self, context) -> bool:
		"""
		Whether this turn has to prove itself. Three reasons it does not.

		Turned off in settings, because a panel that silently discards what
		somebody said needs an off switch that is easy to find.

		A session is open, which means a skill asked a question and is
		waiting for the answer - and an answer is not shaped like a request.
		"Tuesday" is a complete reply to "which day?" and passes no test for
		question shape.

		The panel ANSWERED something a moment ago, which makes this a
		follow-up for the same reason. Answered, not merely asked: a turn
		that produced nothing is not a conversation somebody is continuing,
		and treating it as one would let the first false wake hold the gate
		open for every one after it.
		"""
		try:
			if not self.client.setting(
					"assistant.wake.gate_unaddressed.value", True):
				return False
		except Exception:
			pass
		try:
			if self.client.SERVICES.STT.is_session():
				return False
		except Exception:
			pass
		try:
			if (context is not None and context.answer
					and context.age <= self.FOLLOW_UP_WITHIN):
				return False
		except Exception:
			pass
		return True

	def parse_doc(self, phrase: str):
		"""The utterance as a doc, for anything that needs one and has none."""
		return nlp.model()(str(phrase or ""))

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

	def boost_distinctive(self, scored: dict, content: set, doc=None):
		"""
		Raise the score of any skill whose own vocabulary the phrase carries.

		A skill's distinctive words are the non-filler words from everything
		it is matched against - so a holiday skill listing "when is
		christmas" and "when is easter" knows those words are its own,
		without anyone declaring them.

		A BOOST, not a claim. Owning a word takes the phrase outright, which
		is right for "calendar" and wrong for "christmas": a holiday name is
		a subject and appears in every kind of question about it, so owning
		it means asking what Christmas IS returns a date. Raising confidence
		leaves the rest of the pipeline to decide - the encyclopedia's frame
		for "what is X" outscores the lift and "when is X" does not.

		Weighted by how few skills share the word, so one only this skill
		uses is worth more than one it half-shares.
		"""
		if not content:
			return
		for skill in self.skills():
			shared = skill.distinctive & content
			if not shared:
				continue
			# Rarity across the whole registry, the same measure the scorer
			# uses - a word every skill lists is not distinctive whatever one
			# skill thinks of it.
			rarity = max(self.lemma_weight(word) for word in shared)
			lift = min(DISTINCTIVE_BONUS, DISTINCTIVE_BONUS * (rarity / 3.0))
			if skill.key in scored:
				scored[skill.key] = (min(1.0, scored[skill.key][0] + lift),
									 skill)
			# Deliberately NOT scored on demand.
			#
			# An owner is, because owning is a claim that the phrase is
			# yours. This is only a claim that a word is yours, and scoring
			# an unranked skill on it resurrects skills on the strength of a
			# subject: "what is christmas" reduces to {christmas}, which is
			# an exact content match for the holiday skill's "when is
			# christmas" - the question word is the whole difference and
			# content reduction has already thrown it away. Asking what
			# Christmas IS returned a date.
			#
			# A skill that scored nothing had nothing to say about the
			# phrase; carrying one of its words does not change that.

	def settle_owners(self, scored: dict, lemmas: set, content=None, doc=None):
		"""
		The best-scoring skill that owns a word the utterance carries, or None.

		Only when the top scorer does NOT own it. A skill claiming a word
		conclusively should not lose a phrase containing it to one that
		merely scored well on the words around it.

		Returns None where nothing is owned, where the winner already owns
		it, or where the owners all scored nothing - an owned word is a claim
		about vocabulary, not a licence to answer a phrase the skill matched
		no part of.
		"""
		if not scored:
			return None
		best = sorted(scored.values(), key=lambda pair: -pair[0])
		top = best[0][1]

		claimed = set()
		for skill in self.skills():
			claimed |= (skill.owns & lemmas)
		if not claimed or (top.owns & claimed):
			return None

		owners = [(score, skill) for score, skill in scored.values()
				  if skill.owns & claimed]
		if not owners:
			# Scored on demand. An owner usually has no score at all: the
			# Matcher hands something else a confident match, the rule phase
			# never runs, and the skill that claims the word is never
			# compared with anything. That is exactly the case owning exists
			# for, so it cannot be the case that disables it.
			claimants = [skill for skill in self.skills()
						 if skill.owns & claimed]
			found, score = self.rule_match(content, doc, only=claimants)
			if found is None:
				return None
			scored[found.key] = (score, found)
			owners = [(score, found)]
		owners.sort(key=lambda pair: -pair[0])
		winner = owners[0][1]
		self.client.log("info",
			f"[SkillIntentEngine] '{winner.key}' over '{top.key}' - the "
			f"phrase carries {sorted(claimed)}, which '{winner.key}' owns.")
		return winner

	def settle_opposites(self, scored: dict, content: set):
		"""
		Decide between two skills that undo each other, or leave them be.

		Returns the skill that should win, or None where the question does
		not arise. Only consulted when the top two are a declared pair -
		everything else is settled on score as usual.

		The rule is not "which scored higher" but "which one did the person
		actually say". Each half of a pair owns the words the other does not
		- `unmute`, `back` against `mute`, `silence`, `off` - and whichever
		set the utterance contains is the answer, however the two happened to
		score. Where both or neither appear, the score stands: the utterance
		did not distinguish them and guessing is not better than scoring.
		"""
		# No "at least two scored" guard. The opposite is looked up rather
		# than ranked, and the case that needs settling most is exactly the
		# one where only ONE of the pair scored at all - the Matcher handed
		# its twin a confident match and the other half was never compared
		# with anything.
		best = sorted(scored.values(), key=lambda pair: -pair[0])
		if not best:
			return None
		top = best[0][1]
		if not top.opposite:
			return None

		# Looked up in the registry, not in the ranking.
		#
		# The opposite often has no score at all: the Matcher can hand its
		# twin a confident match, the rule phase never runs, and the other
		# half is never scored against anything. "Turn the sound back on"
		# gave mute-on 1.0 and mute-off nothing.
		#
		# It does not need a score. The question here is not which of the two
		# scored higher - it is which of them the person said, and that is
		# answered by the words alone.
		second = next((skill for skill in self.skills()
					   if skill.key == top.opposite), None)
		if second is None or second.opposite != top.key:
			return None

		mine = self.exclusive_lemmas(top, second)
		theirs = self.exclusive_lemmas(second, top)
		said_mine = bool(mine & content)
		said_theirs = bool(theirs & content)
		if said_mine == said_theirs:
			return None

		winner = top if said_mine else second
		if winner is not top:
			self.client.log("info",
				f"[SkillIntentEngine] '{second.key}' over '{top.key}' - the "
				f"phrase carries {sorted(theirs & content)}, which only "
				f"'{second.key}' owns.")
		return winner

	@staticmethod
	def _pair_lemmas(skill) -> set:
		"""
		A skill's words for the purpose of telling it from its opposite.

		Content words, plus the small set that INVERTS a command. Scoring
		drops stopwords, and rightly - "the" and "your" distinguish nothing.
		But "on", "off" and "back" are stopwords too, and between two halves
		of a switch they are the only words that matter: "turn the sound back
		on" and "turn off your sounds" share every content word they have.
		"""
		# Built from ALL lemmas, not the content ones.
		#
		# Stopword flagging is context-dependent: spaCy calls "make" a
		# stopword in "you can make noise" and not in "stop making noise",
		# so the same word survived into one skill's content lemmas and not
		# the other's and looked like evidence for one of them. It is used
		# by both and distinguishes nothing - and on that evidence the pair
		# check flipped an exact 1.00 match to its opposite.
		#
		# Grammar is removed afterwards by the same rule either way, so
		# membership no longer depends on which sentence a word appeared in.
		found = set()
		for group in skill.lemmas:
			found |= group
		# Determiners and pronouns distinguish nothing, and one appearing in
		# only one skill's examples is an accident of how they were written:
		# "mute the panel" put "the" in mute-on's exclusive set, so "turn THE
		# sound back on" looked like evidence for both halves at once and the
		# tie-break abstained. Grammar out, polarity kept - "on" is both.
		return found - (FRAME_GRAMMAR - POLARITY_LEMMAS)

	@classmethod
	def exclusive_lemmas(cls, skill, other) -> set:
		"""The words `skill` uses that its opposite never does."""
		return cls._pair_lemmas(skill) - cls._pair_lemmas(other)

	def ask_match(self, match_doc) -> list:
		"""
		Every 'ask' skill, scored by how well one of its frames fits.

		[(score, skill, args)], best first. The score is on the same scale as
		the act track's - both are a harmonic mean of the same two coverages
		- so the two lists merge into one ranking without a conversion or a
		precedence rule.

		The args come out of the frame directly. A frame says where the
		subject ends, so there is nothing to trim afterwards: "how many
		episodes does frieren have" yields `frieren`, not `frieren have`.
		"""
		# Punctuation dropped, not only whitespace.
		#
		# Every transcript arrives with a full stop or a question mark on the
		# end - "what is the ocean?" - and a frame compares WORD FOR WORD, so
		# a stray "?" is a token that has to be accounted for. It lands where
		# the suffix should be and wrecks the alignment: "what does an
		# axolotl look like?" fell from 0.80 to 0.36, under the floor, and
		# every question on the panel went to the fallback.
		#
		# The act track never saw this because `_is_content` filters
		# punctuation on the way in. Frames tokenise the utterance directly
		# and had no such step, so the two tracks disagreed about what a word
		# was.
		words = tuple(t.lower_ for t in match_doc
					  if not (t.is_space or t.is_punct))
		if not words:
			return []

		found = []
		for skill in self.skills():
			if skill.kind != "ask":
				continue
			aligned = skill.align_frames(words)
			if aligned is None:
				continue
			score, name, subject = aligned
			# The same floor the act track applies, because the two produce
			# the same measurement. "Tell me a joke about penguins" fits
			# `tell me about {subject}` - the words really are in that order
			# - but only at 0.44, because most of the utterance ends up in
			# the hole. That is a reading, not an answer, and it belongs to
			# the fallback.
			if score < FALLBACK_DEFAULT_RULE_SCORE:
				continue
			found.append((score, skill, {name: subject}))

		found.sort(key=lambda entry: -entry[0])
		return found

	def rule_match(self, input_content: set, doc=None, only=None) -> tuple:
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

		for skill in (only if only is not None else self.skills()):
			if skill.kind != "act":
				continue
			# Same reason as the Matcher phase: a payload is not command
			# vocabulary and must not be scored as though it were.
			content = input_content
			if skill.payload and doc is not None:
				content = skill.command_lemmas(doc, content_only=True) or input_content

			for example in skill.content_lemmas:
				if not example:
					continue

				# The same words, said without the gap.
				#
				# Scoring is lemma against lemma, so one token can never match
				# two: "goodnight" is compared with "good" (0, prefix rule) and
				# with "night" (0, too different in length) and the phrase
				# scores nothing. Whisper writes compounds either way depending
				# on the sentence, and "good night" and "goodnight" are the
				# same thing said.
				#
				# Checked before the loop rather than as a token rule, because
				# it is a property of the whole phrase: the letters in order,
				# ignoring where the spaces fell.
				if content and "".join(example) == "".join(content):
					if 1.0 > best_score:
						best_score, best_skill = 1.0, skill
					continue

				matched = 0.0
				total = 0.0
				used = set()
				exact = False
				loosest = 1.0
				for lemma in example:
					weight = self.lemma_weight(lemma)
					total += weight
					best_token = 0.0
					for candidate in content:
						similarity = fuzzy_equal(lemma, candidate)
						if similarity > best_token:
							best_token, best_match = similarity, candidate
					if best_token:
						matched += weight * best_token
						used.add(best_match)
						if best_token >= 1.0:
							exact = True
						else:
							loosest = min(loosest, best_token)

				if not total:
					continue

				# Nothing matched outright, so everything here rests on words
				# that merely resemble each other. That is what fuzzy
				# matching is for, but it has to be surer of itself when it
				# is carrying the match alone than when it is repairing one
				# word inside a phrase that already agrees.
				if not exact and loosest < FUZZY_SOLE_RATIO:
					continue

				recall = matched / total
				# Precision keeps a long rambling phrase from matching a tiny
				# example on one shared word. Harmonic mean, so both have to
				# hold up.
				precision = len(used) / max(1, len(content))
				score = (2 * recall * precision / (recall + precision)) if (recall + precision) else 0.0

				# An overlap made only of bare asking-verbs is not an
				# overlap. "Tell me a joke" and the example "tell me about
				# mount fuji" agree about the word "tell", which is a
				# grammatical accident rather than a shared subject.
				if used and used <= GENERIC_LEMMAS:
					continue

				# A single bare noun is not a command.
				#
				# One content word gets precision for free - it covers all of
				# what was said, whatever it matched - so "timer" scored 0.74
				# against "cancel the timer" and cancelled one. The word
				# names a thing; it does not say what to do with it, and the
				# word that WOULD say is the one missing.
				#
				# Exact matches are unaffected: "unmute", "silence",
				# "nevermind" and "stop" are whole commands and are caught by
				# the identical-phrase check above, which never reaches here.
				if len(content) == 1 and len(example) > 1:
					continue

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
		#Every skill that scored, by key, with its best score. A skill can
		#decline once it has looked, and the phrase then needs somewhere to
		#go - which means knowing who came second.
		scored: dict = {}

		input_lemmas = {t.lemma_.lower() for t in match_doc if _is_content(t)}
		input_content = {t.lemma_.lower() for t in match_doc
						 if _is_content(t) and not t.is_stop}
		if not input_content:
			# A mishearing can land on a stopword - "weather" becomes
			# "whether", which spaCy treats as one - leaving nothing to score
			# against at all. Fall back to every alphabetic lemma so the fuzzy
			# comparison still has something to work with.
			input_content = {t.lemma_.lower() for t in match_doc if _is_content(t)}

		# A follow-up has no subject, so nothing here can be about it.
		#
		# "Tell me more", "what does that mean", "can you elaborate" carry one
		# content word between them and it is always a verb of asking. Scored
		# normally, whichever skill happens to reduce to that same verb wins
		# outright - the time skill did, until a Wikipedia skill was added and
		# took it instead. Neither was ever right, and which one answered was
		# decided by what else was installed.
		#
		# Refused before the Matcher rather than after it, because a
		# hand-written pattern can match one of these too: "what does that
		# mean" fired the dictionary's trailing-verb pattern and got back
		# "Which word?", which is the panel asking the person to repeat the
		# context it was holding all along.
		#
		# What CAN answer it is whatever remembers the turn before, and that
		# is the fallback - which is handed exactly that.
		if input_content and input_content <= SUBJECTLESS_LEMMAS:
			self.client.log("info",
				f"[SkillIntentEngine] '{phrase}' is a follow-up with no "
				f"subject of its own - passing it on.")
			self.client.ASSIST_STATUS = "LIVE"
			if use_skill:
				# Gated, and the gate lets a real follow-up through on the
				# conversation rather than on the words: `_should_gate()`
				# stands aside when the panel answered something moments
				# ago. "Tell me more" passes on its own shape as well.
				self._fall_back(phrase, doc=match_doc)
			return None, None

		# `.get`, not indexing. The Matcher is shared across the process,
		# so it can hold a pattern id this engine has no skill for - a
		# plugin unloaded mid-phrase, or a second engine in a test. Indexed
		# directly that is a KeyError out of parse(), which takes the whole
		# utterance down rather than the one pattern that went stale.
		candidates = [found for found in
					  (self.id2skill.get(m[0]) for m in results)
					  if found is not None]

		for skill in candidates:
			if skill.kind != "act":
				continue
			# A payload skill is scored on its command words alone. Its value
			# is words no example contains, so leaving it in makes a longer
			# request score worse - the opposite of what it should do.
			skill_lemmas = (skill.command_lemmas(match_doc) if skill.payload
							else input_lemmas)
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
				shared = skill_lemmas & example_lemmas
				if not shared:
					continue
				overlap   = len(shared)
				recall    = overlap / len(example_lemmas)
				precision = overlap / max(1, len(skill_lemmas))
				score = (2 * recall * precision / (recall + precision)
				         if (recall + precision) else 0.0)
				if score > scored.get(skill.key, (-1.0, None))[0]:
					scored[skill.key] = (score, skill)
				if score > best_score:
					best_score = score
					best_skill = skill

		# The ask track, merged in rather than run instead. Both produce a
		# harmonic mean of the same two coverages, so the numbers mean the
		# same thing and a frame competes on score - no precedence rule.
		ask_args: dict = {}
		for score, skill, args in self.ask_match(match_doc):
			ask_args[skill.key] = args
			if score > scored.get(skill.key, (-1.0, None))[0]:
				scored[skill.key] = (score, skill)
			if score > best_score:
				best_score, best_skill = score, skill

		if best_skill is None and candidates:
			# A pattern matched on something that is not a lemma - a number, a
			# part of speech - so there was nothing to score against, but the
			# match itself is real. Kept, because the old scoring accepted
			# these by accident (0 beat its -1 starting value) and dropping
			# them now would be a silent regression.
			best_skill, best_score = candidates[0], 0.0
			scored.setdefault(best_skill.key, (0.0, best_skill))

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
			rule_skill, rule_score = self.rule_match(input_content, match_doc)
			if rule_skill is not None:
				if rule_score > scored.get(rule_skill.key, (-1.0, None))[0]:
					scored[rule_skill.key] = (rule_score, rule_skill)
				if rule_score > best_score:
					best_skill, best_score = rule_skill, rule_score


		# Two skills that undo each other are settled on which one the phrase
		# The phrase's own vocabulary, before anything is settled - a skill
		# lifted over the floor here can then be compared like any other.
		self.boost_distinctive(scored, input_content, match_doc)
		if scored:
			lifted = max(scored.values(), key=lambda pair: pair[0])
			if lifted[0] > best_score:
				best_score, best_skill = lifted
		# actually names, not on which scored higher - see settle_opposites.
		if best_skill is not None:
			owned = self.settle_owners(scored, input_lemmas,
									   input_content, match_doc)
			if owned is not None and owned is not best_skill:
				best_score = scored[owned.key][0]
				best_skill = owned

		if best_skill is not None:
			settled = self.settle_opposites(scored, input_lemmas)
			if settled is not None and settled is not best_skill:
				# It keeps the score its twin won on. The pair was settled on
				# which one the phrase names, not on which scored higher -
				# and the winner often has no score of its own, because it
				# was never compared with anything.
				scored[settled.key] = (best_score, settled)
				best_skill = settled

		if not best_skill:
			self.client.log("info", f"[SkillIntentEngine] Matcher found Nothing : {round(time.time() - start, 3)}s")
			self.client.ASSIST_STATUS = "LIVE"
			if use_skill:
				# Nothing understood it. Anything subscribed gets a chance to
				# answer instead - see the AI fallback plugin. Only on the real
				# input path, so a use_skill=False probe stays side-effect free.
				# The turn before this one goes with it. A phrase nothing
				# understood is very often a follow-up - "where does that
				# come from" names nothing and matches nothing - and the
				# question it follows is the only thing that makes it
				# answerable. Handlers that take one argument still get one;
				# see `_accepts_two` in main.py.
				#
				# GATED. This is the branch a television arrives at: the wake
				# word fired on something, the words were real English, and
				# no skill wanted them. Without a test for whether anybody
				# was being addressed, "nothing matched" means "ask the AI",
				# and the panel answers the room out loud.
				self._fall_back(phrase, doc=match_doc)
			return None, None
		else:
			if use_skill:
				self.client.ASSIST_STATUS = "ACTING"
				# The counterpart of on_assistant_fallback: something took
				# this. Without it the only signal anything gets is the
				# failure, so a display cannot tell a phrase that was
				# understood from one that was not.
				self.client.iterate_event_callables("on_skill_called",
												   best_skill.key)
				# Best first, and everything else behind it in case the best
				# turns out not to want it after all.
				ranked = [skill for _score, skill in
						  sorted(scored.values(), key=lambda pair: -pair[0])]
				if best_skill in ranked:
					ranked.remove(best_skill)
				ranked.insert(0, best_skill)
				Thread(target = self.__skill_call_with_status_update,
					   args = [ranked, match_doc, ask_args]).start()
			else:
				self.client.ASSIST_STATUS = "LIVE"
			self.client.log("info", f"[SkillIntentEngine] Matcher found '{best_skill.key}' @ {best_score} : {time.time() - start}s")
			return best_skill, original_text
