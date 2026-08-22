"""
Whether somebody was talking to the panel, or just talking.

The wake stack answers "was the word said". This answers the question after
it: given that something woke the panel and no skill claimed the phrase, was
this a request at all?

**The gap this fills.** Nothing matching used to mean one thing - hand it to
whatever answers general questions - so a television saying "congrats on the
match, not that it matters" arrived at the AI, got an answer, and the panel
said it out loud. There were two categories where there needed to be three:
understood, not understood, and not addressed to anybody here.

Rules, not a model. Everything below is a shape of speech rather than a
subject, so it costs a few microseconds on a doc the engine has already
parsed, and it can be read and argued with. The false-wake log is what a
model would eventually be trained on, and this is what fills it in the
meantime.

**Biased toward discarding.** An utterance with no signal either way is
dropped. A missed request costs one more sentence; a phantom interaction
costs a panel that talks over the television, which is the thing being fixed.
"""

from __future__ import annotations

## -- ADDRESSED

#Openers that make an utterance a question. A phrase starting with one of
#these is being asked of somebody, and the panel is the only one listening.
QUESTION_WORDS = frozenset({
    "what", "whats", "who", "whos", "when", "where", "why", "which",
    "how", "hows", "whose", "whom",
})

#Auxiliaries in front position. English puts them there to ask.
#"Is the door locked", "can you play something", "did i get any post".
QUESTION_AUXILIARIES = frozenset({
    "is", "are", "was", "were", "am", "do", "does", "did", "can", "could",
    "will", "would", "should", "shall", "may", "might", "have", "has", "had",
})

#Verbs a person points at a panel. Not a list of skills - those are matched
#long before this - but the shapes a request takes when no skill knew it.
COMMAND_VERBS = frozenset({
    "tell", "show", "give", "find", "play", "set", "turn", "remind", "open",
    "close", "start", "stop", "add", "make", "put", "send", "call", "read",
    "search", "look", "define", "spell", "convert", "calculate", "translate",
    "repeat", "list", "check", "cancel", "pause", "resume", "skip", "sing",
    "count", "explain", "describe", "name", "pick", "choose", "help",
    "remove", "delete", "create", "write", "draw", "wake", "sleep", "mute",
    "unmute", "increase", "decrease", "lower", "raise", "dim", "brighten",
})

#"I need", "I want", "I would like" - a request in the first person rather
#than the imperative.
FIRST_PERSON_ASKS = frozenset({
    "need", "want", "wonder", "wondering", "like", "would", "wanna", "gotta",
    "forgot", "forget", "forgotten", "remember", "cant", "can't",
})

#What spaCy leaves between "I" and the verb. It splits contractions, so
#"I'm wondering" is ["i", "'m", "wondering"] and "I can't remember" is
#["i", "ca", "n't", "remember"] - which is why looking for "im" or "ive" as
#a whole token finds nothing, ever, and every contracted request was dropped.
FIRST_PERSON_GAP = 3

#Politeness that can sit in front of any of the above.
LEAD_INS = frozenset({
    "please", "hey", "ok", "okay", "alright", "so", "um", "uh", "well",
    "quick", "quickly", "just", "also", "and", "then", "now",
})


## -- NOT ADDRESSED

#Third-person subjects. Somebody talking ABOUT a person is not talking TO
#the panel, and a panel has no idea who "he" is anyway.
NARRATIVE_SUBJECTS = frozenset({
    "he", "she", "they", "him", "her", "them", "we", "us", "his", "hers",
    "their", "theirs", "everybody", "everyone", "nobody", "somebody",
    "someone",
})

#The subset with no possible referent here, checked over the WHOLE utterance
#and ahead of everything else.
#
#"Did he leave", "what did she tell you", "tell him I called", "leave her
#alone" - these are a question and an instruction by every test of shape, and
#none of them is answerable by a panel that has never been told who "he" is.
#Shape says an utterance IS a request; it cannot say who it was aimed at, and
#this is the one case where the words themselves settle it.
#
#Only reached when there is no conversation open - see `_should_gate()`. A
#follow-up that says "what did they build" has an antecedent and never comes
#here. Without one there is nothing for the pronoun to point at.
#
#"We" and "us" are deliberately NOT in here: "what should we watch tonight"
#is somebody talking to the panel about themselves.
UNREFERENCED_PRONOUNS = frozenset({
    "he", "him", "his", "she", "her", "hers", "they", "them", "their",
    "theirs",
})

#What a reaction is made of. An utterance that is only these is somebody
#responding to something that is not the panel.
REACTION_LEMMAS = frozenset({
    "that", "this", "it", "be", "so", "such", "very", "really", "quite",
    "good", "bad", "great", "nice", "amazing", "terrible", "awful", "funny",
    "weird", "strange", "cool", "lovely", "horrible", "wonderful", "silly",
    "stupid", "crazy", "wow", "oh", "ah", "huh", "hm", "yeah", "yes", "no",
    "not", "just", "friendly", "matter", "like", "love", "hate", "think",
    "guess", "suppose", "mean", "know", "congrats", "congratulations",
    "thanks", "thank", "sorry", "please", "well", "right", "sure", "okay",
    "ok", "fine", "true", "false", "maybe", "probably",
})


class Verdict:
    """What the gate decided, and why - the reason is for the log."""

    __slots__ = ("addressed", "reason", "rule")

    def __init__(self, addressed: bool, reason: str, rule: str):
        self.addressed = bool(addressed)
        self.reason = str(reason)
        self.rule = str(rule)

    def __bool__(self) -> bool:
        return self.addressed

    def __repr__(self) -> str:
        state = "addressed" if self.addressed else "not addressed"
        return f"<Verdict {state}: {self.reason}>"


def _words(doc) -> list:
    """The utterance as lowercase word forms, punctuation dropped."""
    return [token.text.lower() for token in doc
            if not token.is_punct and not token.is_space]


def _strip_lead_ins(words: list) -> list:
    """
    Politeness in front of the request is not the request.

    Always leaves the last word, so an utterance that is nothing but filler
    still has something to be judged on rather than becoming empty and
    needing a branch of its own here.
    """
    index = 0
    while index < len(words) - 1 and words[index] in LEAD_INS:
        index += 1
    return words[index:]


def _sentence_count(doc) -> int:
    """
    How many sentences, counted on terminal punctuation.

    The model is loaded without a parser, so `doc.sents` is not available -
    see nlp.model(). Counting full stops is coarser and enough: what matters
    is whether this is one thought or several.
    """
    stops = sum(1 for token in doc if token.text in (".", "!", "?"))
    trailing = 1 if doc and doc[-1].text in (".", "!", "?") else 0
    return max(1, stops + 1 - trailing)


def is_addressed(doc, text: str = "") -> Verdict:
    """
    Whether this utterance was aimed at the panel. Takes a parsed doc.

    The doc rather than a string, because the caller already has one - the
    intent engine parses every phrase before scoring it, and parsing it twice
    to ask a cheaper question would cost more than the question.
    """
    words = _words(doc)
    if not words:
        return Verdict(False, "nothing was said", "empty")

    ## -- NOT ADDRESSED, BEFORE ANYTHING ELSE

    # Ahead of the positive rules on purpose. Television is full of questions
    # and instructions, and every one of them passes a test for question
    # shape: "what did she tell you" is accepted on "what" long before "she"
    # is looked at. A pronoun with nothing to point at is the one signal that
    # beats shape, so it is asked first.
    unreferenced = next((word for word in words
                         if word in UNREFERENCED_PRONOUNS), "")
    if unreferenced:
        return Verdict(False, f"it is about '{unreferenced}', who the panel "
                              f"has not been told about", "no referent")

    ## -- ADDRESSED: something in the shape of a request

    if any(token.text == "?" for token in doc):
        return Verdict(True, "it is punctuated as a question", "question mark")

    head = _strip_lead_ins(words)
    first = head[0]

    if first in QUESTION_WORDS:
        return Verdict(True, f"it opens with '{first}'", "question word")

    if first in QUESTION_AUXILIARIES:
        return Verdict(True, f"it opens with '{first}'", "auxiliary")

    if first in COMMAND_VERBS:
        return Verdict(True, f"it opens with '{first}'", "command verb")

    if first == "i":
        # A short window rather than the next token, because spaCy splits
        # contractions and leaves the clitic in between: "i" "'d" "like",
        # "i" "ca" "n't" "remember".
        for word in head[1:1 + FIRST_PERSON_GAP]:
            if word in FIRST_PERSON_ASKS:
                return Verdict(True, f"'i ... {word}' is asking for something",
                               "first person")

    if first in ("lets", "let"):
        return Verdict(True, "it opens with 'let'", "command verb")

    # A bare imperative the list above does not name. The tag is the test:
    # spaCy calls the base form VB, and a verb in front position with no
    # subject before it is somebody giving an instruction.
    for token in doc:
        if token.is_punct or token.is_space:
            continue
        if token.text.lower() in LEAD_INS:
            continue
        if token.tag_ == "VB" and token.pos_ in ("VERB", "AUX"):
            return Verdict(True, f"'{token.text}' is an instruction",
                           "imperative")
        break

    ## -- NOT ADDRESSED: shapes a request does not take

    if first in NARRATIVE_SUBJECTS:
        return Verdict(False, f"it is about '{first}', who the panel does "
                              f"not know", "narrative subject")

    if any(token.tag_ == "VBD" for token in doc) and \
            any(word in NARRATIVE_SUBJECTS for word in words):
        return Verdict(False, "it recounts something somebody else did",
                       "past tense narrative")

    lemmas = {token.lemma_.lower() for token in doc
              if not token.is_punct and not token.is_space}
    if lemmas and lemmas <= REACTION_LEMMAS:
        return Verdict(False, "it is a reaction rather than a request",
                       "reaction")

    if _sentence_count(doc) > 1:
        return Verdict(False, "it is several statements and none of them "
                              "asks anything", "conversation")

    ## -- NEITHER

    # Dropped. A request that gets dropped costs one more sentence; a
    # television that gets answered costs a panel talking over it, and that
    # is the failure being fixed. The reason is logged either way, so the
    # rules can be argued with from what the house actually produces.
    return Verdict(False, "nothing about it is addressed to the panel",
                   "no signal")
