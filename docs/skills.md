# Writing skills

A skill is a phrase the assistant recognises and a function it calls. Declaring
one is a `Skill(...)` and a registration; everything about matching is handled
for you.

For how matching actually works — pattern generation, the rule phase, scoring —
see [Voice assistant](assistant.md). This page is about writing them.


## Writing a skill: the short version

Read this before the rest. The order below is the order to think in.

**1. Is it an act or an ask?** Something to *do* is `kind="act"` and matches
on `examples`. Something to *answer* is `kind="ask"` and matches on `frames`.
The two cannot reach each other, which is what stops a question about timers
cancelling one.

**2. For an act, write the examples people actually say.** Not one canonical
phrasing - all of them, including the ones you would not write down. Most
routing failures on this panel have been a missing phrase rather than a
scoring fault: `check-alarms` listed "list my alarms" and `check-timers` did
not, and that asymmetry alone was the bug. If a sibling skill has a phrasing,
yours probably needs it too.

**3. For an ask, put the hole where the subject is.** `what does {subject}
mean`, not an anchor on the front. A frame says where the subject *ends*, so
it arrives exactly - no trailing words to strip afterwards.

**4. Write a frame for each shape, not each wording.** Inflections and near
misses are handled: "mean"/"means", "definiton"/"definition". What is not
handled is a different *shape* - `what is the meaning of {word}` is a
separate frame from `what does {word} mean`.

**5. Type the hole if the data can confirm it.** `holes={"holiday": ...}`
turns an ambiguous shape into an unambiguous one, and a validated hole
carries a bonus. The predicate runs during matching: a set lookup, never a
request or a disk read.

**6. Declare an opposite if the skill has one.** Any on/off pair. The word
that inverts a command is worth no more than any other to a scorer, so this
has to be stated.

**7. Own a noun only if it is conclusive.** If any phrasing exists that
carries the word and is not yours, do not own it. `calendar` qualifies;
`christmas` does not, because it is a *subject* and appears in every kind of
question about it.

**8. Decline rather than apologise.** If the skill cannot answer after
looking - no such series, no subject in the phrase - raise `SkillDeclined`
and the phrase carries on to the next skill and then the fallback. Do not
show anything first, and do not ask a clarifying question: that ends the turn
on behalf of every skill that was never tried.

**9. Then measure.** `test_corpus.py` routes every skill's phrasings and the
collisions between them. Run it before and after. A change that looks right
and moves the number the wrong way is a change to revert, not to patch - that
has happened twice.

## The smallest skill

```python
from src.assistant.skill import Skill

class MyPlugin(Plugin):

    def load(self, carryover=None):
        key = self.config["plugin"]["key"]
        wake = self.client.wake_word

        self.skills = [
            Skill(
                wake_word  = wake,
                skill_key  = "porch-light-on",
                plugin_key = key,
                examples   = [
                    "turn on the porch light",
                    "porch light on",
                    "switch the porch light on",
                    "light up the porch",
                ],
                func = self.porch_on,
            ),
        ]

        self.client.SKILLS.register(key, self.skills)

    def unload(self, carryover=None):
        self.client.SKILLS.un_register(self.config["plugin"]["key"])

    def porch_on(self):
        self.client.simple_notify("check", "Porch", "Light on.")
```

`self.client.wake_word` reads the wake word from Assistant settings. Use it
rather than hardcoding one, so changing it in Settings changes your skills too.


## Examples are the whole interface

`examples` is not a list of exact phrases. Each one is compiled into a pattern
with determiners, numbers, politeness and pronouns relaxed, so `"set a timer
for 10 minutes"` also matches `"set the timer for 1 minute"`.

They also feed the rule phase, which scores an unmatched utterance against
every skill's vocabulary. That gives you two rules of thumb:

**Write several, and vary the shape.** Four to twelve is normal. Cover the
different ways someone would actually say it — "clear my notifications",
"empty notifications", "dismiss all notifications" — not the same sentence
with the determiner swapped, which the generaliser already handles.

**Use the words that distinguish your skill.** The rule phase weights a lemma
that only one skill uses far above one every skill shares. If two of your
skills both say "notifications", the word deciding between them needs to be in
the examples of both.

Do not add a wake word to your examples. It is stripped before matching.


## Arguments

To capture part of the phrase, declare an argument with spaCy Matcher
patterns. The captured text is passed to your function as a keyword argument
of the same name.

```python
Skill(
    wake_word=wake, skill_key="set-timer", plugin_key=key,
    examples=[
        "set a timer for 10 minutes",
        "start a timer for 5 minutes",
        "make a timer for 11 minutes",
    ],
    arguments={
        "duration": [
            [{"LIKE_NUM": True}, {"LOWER": {"IN": ["minute", "minutes"]}}],
        ],
    },
    func=self.set_timer,
)

def set_timer(self, duration: str = ""):
    ...
```

For a value with no fixed shape — a song title, a search phrase — see
[Payloads](#payloads--an-open-ended-value) instead. Matching those as arguments
trims them and lowers the skill's score.

### The shape of `arguments`

```
arguments = {
    "arg_name": [           # a LIST of alternative patterns
        [ {...}, {...} ],   # pattern 1: a list of token specs
        [ {...}, {...} ],   # pattern 2: a different way of saying it
    ],
}
```

Each key maps to a **list of alternatives**, and each alternative is a list of
token specs. Any one of them matching captures the argument, so this is how you
cover phrasings that do not share a shape. From the bundled skills:

```python
arguments={
    "given_date": [
        # "the day before today"  - two or three bare words
        [{"IS_ALPHA": True, "OP": "{2,3}"}],
        # "what is tomorrow"      - a copula, then up to four words
        [{"LOWER": {"IN": ["is", "was"]}}, {"IS_ALPHA": True, "OP": "{1,4}"}],
    ],
}
```

Alternatives are cheap. Adding one is almost always better than making a
single pattern clever enough to cover both, because a loose pattern starts
capturing things you did not mean.

### Token attributes

Full reference: <https://spacy.io/usage/rule-based-matching>, and the API at
<https://spacy.io/api/matcher>.

The ones worth knowing:

| Key                       | Matches                                              | Example                                         |
|---------------------------|------------------------------------------------------|-------------------------------------------------|
| `LOWER`                   | The lowercased text.                                 | `{"LOWER": "timer"}`                            |
| `ORTH` / `TEXT`           | The exact text, case-sensitive.                      | `{"ORTH": "AM"}`                                |
| `LEMMA`                   | The dictionary form, so one spec covers inflections. | `{"LEMMA": "set"}` matches set/sets/setting     |
| `POS`                     | Coarse part of speech.                               | `{"POS": "NOUN"}`                               |
| `TAG`                     | Fine-grained tag.                                    | `{"TAG": "NNP"}`                                |
| `DEP`                     | Dependency label.                                    | `{"DEP": "dobj"}`                               |
| `SHAPE`                   | Orthographic shape.                                  | `{"SHAPE": "dddd"}` matches `2026`              |
| `ENT_TYPE`                | Named entity type.                                   | `{"ENT_TYPE": "TIME"}`                          |
| `IS_ALPHA`                | Letters only.                                        | `{"IS_ALPHA": True}`                            |
| `IS_DIGIT`                | Digits only.                                         | `{"IS_DIGIT": True}` matches `10`, not `ten`    |
| `IS_PUNCT`                | Punctuation.                                         | `{"IS_PUNCT": True}`                            |
| `IS_STOP`                 | A stop word.                                         | `{"IS_STOP": False}`                            |
| `LIKE_NUM`                | Anything numeric.                                    | `{"LIKE_NUM": True}` matches `10` **and** `ten` |
| `LIKE_URL` / `LIKE_EMAIL` | Looks like one.                                      | `{"LIKE_URL": True}`                            |

`LIKE_NUM` over `IS_DIGIT` almost always. The transcriber writes numbers either way
depending on how they were said, and normalisation converts most but not all.

### A value spanning several tokens

A pattern of `[{"LIKE_NUM": True}, {"LEMMA": "minute"}]` matches "1 hour" and
"48 minutes" **separately** in "1 hour and 48 minutes". The Matcher reports
both, and only one becomes the argument.

Write the whole thing as one pattern with optional tokens:

```python
"time": [
    [{"LIKE_NUM": True},
     {"LEMMA": {"IN": ["second", "minute", "hour", "day"]}},
     {"LOWER": {"IN": ["and", "plus"]}, "OP": "?"},
     {"LIKE_NUM": True, "OP": "?"},
     {"LEMMA": {"IN": ["second", "minute", "hour", "day"]}, "OP": "?"}],
]
```

The **widest** match for an argument is the one taken, so the compound span
beats the two short ones inside it. Ties keep the later match.

Both timer skills share `DURATION_UNITS` and `DURATION_JOINERS` for this, and
the joiners are in `TIMER_NAME_STOPWORDS` too - otherwise "the 1 hour and 10
minute timer" comes back as a timer named "and".

Against the real pipeline:

| Said                                   | `time`                           |
|----------------------------------------|----------------------------------|
| set a timer for 1 hour and 48 minutes  | `1 hour and 48 minutes` -> 6480s |
| cancel the 1 hour and 10 minutes timer | `1 hour and 10 minutes` -> 4200s |
| stop the 2 hour 30 minute timer        | `2 hour 30 minute` -> 9000s      |
| cancel the eggs timer                  | none; `name` is `eggs timer`     |

### A clock time is not `LIKE_NUM`

`4:40` is a single token, tagged `NUM`, whose `like_num` is **False** - so
`{"LIKE_NUM": True}` never matches it while `8` matches fine. Ask for the
shape instead:

```python
{"TEXT": {"REGEX": r"^\d{1,2}([:.]\d{2})?$"}}
```

The alarm skills use this (`ALARM_TIME_PATTERNS` in Core Skills), alongside a
separate `after` argument for the relative form. Both match on "set an alarm
10 minutes from now" - "10" reads as a clock time - so the handler prefers
the relative one: the one somebody said is the one with a unit on it.

### Value operators

A value can be a dict instead of a literal:

| Operator                   | Means                                      | Example                                           |
|----------------------------|--------------------------------------------|---------------------------------------------------|
| `IN`                       | One of a list.                             | `{"LOWER": {"IN": ["minute", "minutes", "min"]}}` |
| `NOT_IN`                   | None of a list.                            | `{"LOWER": {"NOT_IN": ["not", "cancel"]}}`        |
| `REGEX`                    | Matches a pattern.                         | `{"LOWER": {"REGEX": "^colou?r$"}}`               |
| `FUZZY`                    | Approximate, for mishearings.              | `{"LOWER": {"FUZZY": "notifications"}}`           |
| `>=`, `<=`, `>`, `<`, `==` | Numeric comparison on a numeric attribute. | `{"LENGTH": {">=": 4}}`                           |

`IN` is the workhorse. It is how you accept singular and plural, or a handful
of synonyms, without writing an alternative for each.

### Quantifiers — `OP`

`OP` says how many of the preceding token spec to match:

| `OP`      | Means                                           |
|-----------|-------------------------------------------------|
| `"?"`     | Zero or one — an optional token.                |
| `"*"`     | Zero or more.                                   |
| `"+"`     | One or more.                                    |
| `"!"`     | Exactly zero — assert this token is *not* here. |
| `"{2,3}"` | Between two and three.                          |
| `"{2}"`   | Exactly two.                                    |
| `"{2,}"`  | Two or more.                                    |

```python
# "for 10 minutes" / "10 minutes" - the preposition is optional
[{"LOWER": "for", "OP": "?"}, {"LIKE_NUM": True}, {"LOWER": {"IN": ["minute", "minutes"]}}]

# a name of one to three words
[{"IS_ALPHA": True, "OP": "{1,3}"}]

# anything up to the word "off", not including a negation
[{"LOWER": {"NOT_IN": ["not"]}, "OP": "*"}, {"LOWER": "off"}]
```

Be careful with bare `*` and `+` at the start of a pattern — they will happily
swallow the whole utterance. Bound them with `{1,4}` unless you mean it.

### Writing them

Keep patterns short. A pattern of two or three tokens anchored on a distinctive
word is more reliable than a long one describing a whole sentence, because the
sentence around your argument varies and the argument itself usually does not.

Give every argument a default in your function signature. An argument that
does not match is simply not passed, and a skill that raises `TypeError`
because of it looks to the user like the assistant ignoring them.

Arguments arrive as **strings**, exactly as spoken. Parse and validate them
yourself — `"ten"` and `"10"` can both reach you, and
[transcript normalisation](assistant.md) converts most spoken numbers but is
not a guarantee.


## Payloads — an open-ended value

`arguments` matches a *shape*. Some requests do not have one:

```
play never gonna give you up
search for how tall the eiffel tower is
remind me to take the bins out
```

The value is arbitrary text the engine cannot know anything about. Two things
go wrong if you try to match it as an argument.

**It drags the score down.** A skill is scored on how much of what you said its
examples explain, so words no example contains lower it — and a title is
nothing but such words. Scored that way, the more distinctive the title the
*less* likely it routes:

| Said                           | Score against `play a song` |
|--------------------------------|-----------------------------|
| `play yesterday`               | 0.57                        |
| `play everlong`                | 0.40                        |
| `play never gonna give you up` | 0.20                        |

**And it gets trimmed.** `arguments` strips leading determiners, verbs and
prepositions, which is right for `"call it Eggs"` and ruinous for a title:
`Let It Be` is three stopwords and comes out as `be`.

### Declaring one

`payload` maps an argument name to the words that introduce it:

```python
Skill(
    wake_word   = "computer",
    skill_key   = "play-music",
    plugin_key  = "musicplugin",
    examples    = ["play something", "put on some music", "play a song"],
    payload     = {"track": ["play", "put on"]},
    func        = self.play,
)
```

`self.play(track="never gonna give you up")`.

Three things follow from the declaration:

* **Everything after the anchor is the value**, taken verbatim. Nothing is
  trimmed, so a title made entirely of stopwords survives intact.
* **The value is removed before scoring.** `play <anything>` scores exactly as
  well as `play`, whatever the title's length.
* **Patterns are built from the command part**, so `put on some music` compiles
  to `put on` rather than requiring the word *music*.

Examples have their payloads stripped too — a command is compared against a
command, never against the stand-in title in your example.

### Choosing anchors

The anchor is what somebody says *before* the value, and it is matched anywhere
in the phrase, so `can you play X` works as well as `play X`.

**The longest match wins**, so listing both `put` and `put on` gives the value
`some jazz` rather than `on some jazz`. List the longer forms; there is no cost
to having several.

```python
payload = {"query": ["search for", "look up", "google"]}
payload = {"reminder": ["remind me to", "remember to"]}
payload = {"item": ["add", "put"]}
```

**Anchor on words that mean the command and nothing else.** `play` is safe;
`for` would fire on half the timer requests. A payload skill only competes when
its anchor is actually said, so a specific anchor keeps it out of everything
else.

An anchor with nothing after it carries no value — `"play"` alone still routes,
and `func()` is called without the argument. Handle that case.

### Both together

A skill can declare `arguments` and `payload`. Where both name the same key the
payload wins, since it is the verbatim value.

## Speaking back

A spoken answer shorter than four words is given a lead-in before it is
said - `speakable.flavour()`, applied by `client.answer()`. "72 degrees." is
two words and is finished before a room has noticed anybody is talking, and
what gets missed is the front of it.

That is a safety net, not a substitute for writing the answer. A lead-in adds
no information; if a skill has something worth saying, say it:

```python
speak="It's 72 degrees and overcast, though it feels more like 78."
```

Anything already four words or longer is left exactly as it is.


```python
def porch_on(self):
    self.client.say("Porch light on.")
```

`say()` returns whether anything was actually spoken. Replies can be turned
off in Settings and a backend can fail to load, and a panel that never speaks
is a perfectly valid install — so a skill should
never *depend* on being heard. Show a notification or update the UI as well.


## Asking a follow-up question

A session keeps the microphone open for an answer instead of going back to
waiting for the wake word.

```python
def delete_everything(self):
    self.client.say("Are you sure?")

    with self.client.STT.new_session() as session:
        answer = session.wait_for_phrase()
        if answer is None:
            return                      # cancelled, timed out, or closed
        if "yes" in answer.lower():
            self.do_it()
```

`wait_for_phrase()` always returns — it takes a timeout, and a cancel or close
pushes a sentinel — so it cannot leave your thread blocked forever. `None`
means no answer is coming; treat it as "no".

Always use the `with` form. Leaving a session open means every phrase in the
house goes into your queue instead of being treated as a fresh command.

Skills run on a worker thread, so touching any widget from one needs
`client.call_on_ui()`. See [Threading](threading.md).


## Managing skills at runtime

```python
self.client.SKILLS.register(plugin_key, skills)       # list of Skill or SkillGroup
self.client.SKILLS.remove_skill(plugin_key, "porch-light-on")
self.client.SKILLS.un_register(plugin_key)            # everything you own
self.client.SKILLS.registered_count(plugin_key)
```

Always `un_register()` in `unload()`. A skill left registered after its plugin
is gone matches a phrase and calls into a module that no longer exists.

### Grouping

`SkillGroup(domain, skills)` bundles related skills under a domain name. It
adds and iterates like a list, so it can be passed to `register()` alongside
plain `Skill` objects.


## When nothing matches

An utterance no skill claims fires `on_assistant_fallback`. The bundled
[AI Fallback](bundled-plugins.md) plugin subscribes to it and answers with an
LLM; you can subscribe too.

A skill that matched but [declined](#declining-after-you-have-looked) ends up
here too, which is deliberate: from outside the panel, a phrase nobody would
answer and a phrase everybody handed on are the same event.

Being unmatched is a valid outcome and the threshold protects it — scoring
every skill against every phrase would always produce a nearest match, and
"the assistant did the wrong thing" is worse than "the assistant did nothing".

## Declining after you have looked

Matching happens on words, and words are sometimes not enough to know whether
a question was yours. Raise `SkillDeclined` and the phrase carries on to the
next-best skill, and to the fallback if nothing takes it:

```python
from src.assistant.skill import SkillDeclined

def wiki_search(self, subject: str = "", phrase: str = ""):
    found = api.look_up(subject)
    if not found:
        raise SkillDeclined(f"nothing for {subject!r}")
    if not title_matches(subject, found["title"]):
        raise SkillDeclined(f"asked {subject!r}, got {found['title']!r}")
    ...
```

This is not an error. The skill ran, worked, and established something only
it could know and only after looking: Wikipedia answers every query with its
nearest article, so "what is the ocean like" comes back as *Frank Ocean*. No
score computed before the handler ran could have seen that.

**No subject is a decline, not a question.** A skill that matched but cannot
work out what it was asked about should hand the phrase on rather than ask
for it. "When is the next season *or* the ramparts of ice" - one mis-heard
word - broke the anime skill's payload anchor, so the Wikipedia skill won at
0.18, found no subject, and replied "what should I look up?" It was holding a
phrase that named the thing perfectly well, and it ended the turn on behalf
of every skill that was never tried.

**Decline before you show anything.** A panel that opens and then reports a
failure is worse than one that never opened — the phrase still has somewhere
to go, and nobody watching can tell "not mine" from "broken".

**An outage is not a decline either.** "No such thing" is a judgement about
your own subject matter and belongs to the next skill. "The service is down"
is not - nothing about it established that the question was not yours, and
handing it on has the next skill answering for somebody else's network. Say
so instead.

**An ordinary exception is not a decline.** A skill that raises `ValueError`
is broken, and the phrase is *not* handed on: a crash is not a judgement
about whose question it was, and passing it along would have the next skill
answering for a bug.

At most `MAX_SKILL_ATTEMPTS` skills get a turn. Every decline is another
handler running, and a handler that declines has usually already done the
work that told it to.

Only skills that **scored** are in the queue. A phrase matched by exactly one
skill has nowhere to fall but the fallback, which is the right destination
anyway.

## Matching something with an unpredictable name

Most skills match a fixed phrase. A bookmark cannot: its title comes from the
page rather than from whoever saved it, so it might be `Scryfall` or
`Advanced Search - Scryfall`.

`open-bookmark` therefore **anchors on the verb** and takes everything after it:

```python
"wanted": [
    [{"LOWER": {"IN": ["open", "goto", "launch"]}},
     {"IS_ALPHA": True, "OP": "+"}],
]
```

The matching happens in the handler, against the list — word overlap rather than
character similarity, and only whether each word is *present* rather than where.
"scryfall" scores 1.0 against "Advanced Search - Scryfall", because the part
somebody says is the part they remember and it is rarely the whole title.

Both this and `go-to-page` refuse a weak match rather than guessing. They
navigate, so a wrong answer takes the screen away from whatever was on it — and
"I have no bookmark like that" is a better outcome than the wrong site.

## Two kinds of skill

Every skill declares a `kind`, and it decides how the skill is matched:

| `kind` | Is                  | Matched on                                       | Needs                    |
|--------|---------------------|--------------------------------------------------|--------------------------|
| `act`  | Something to do     | Word overlap against examples, with fuzzy repair | `examples` or `patterns` |
| `ask`  | Something to answer | Frames — a fixed phrase with a hole in it        | `frames`                 |

Both may carry `arguments` and `payload`.

A malformed skill raises `BadSkill` at construction, which is registration
time, so a plugin fails to load and says why. A skill that registers and then
never matches looks exactly like a skill nobody has said the right words to,
and that hides for months. Refused: no kind, an unknown kind, an `act` with
neither examples nor patterns, an `ask` with no frames, an `act` carrying
frames, an `ask` carrying examples.

## A plugin owning its nouns

A skill may declare words that mean it, near-conclusively:

```python
Skill(skill_key="calendar-today", kind="act",
      owns=["calendar", "appointment", "agenda"], ...)
```

Scoring weights a word by how **few** skills use it — a statistic about the
example lists rather than a fact about meaning. It rates `calendar` at 2.75
and `today` at 2.28, near enough the same, so "whats on my calendar today"
went to the date skill, which owns "today" thoroughly. Raising `calendar`'s
weight would fix that phrase and move every other skill's numbers with it.

Where an utterance carries an owned word and the top scorer does not own it,
the best-scoring owner wins instead. A **narrowing filter, not a selector**:
several skills may own one word, and scoring still picks between them —
`calendar` belongs to the calendar's skills and to `go-to-page`, which
navigates to it.

**Own only what is conclusive.** If a phrasing exists that carries the word
and is not yours, it is not yours to own. `calendar`, `appointment` and
`agenda` qualify; `event`, `today` and `week` do not.

**Owning is a claim about vocabulary, not a licence to answer.** The owner is
still scored — on demand where nothing scored it, since the Matcher often
hands another skill a confident match and the owner is never compared with
anything. A skill that owns a word but has no example containing it will
still lose: `calendar-tomorrow` claimed "calendar" while every one of its
examples said "what is on tomorrow", and it kept losing until the word
appeared in something it was scored against.

## Skills that undo each other

A skill may name its opposite:

```python
Skill(skill_key="mute-off", kind="act", opposite="mute-on", ...)
```

Two halves of a switch share every word except the one that inverts it, and
to a scorer over unordered lemmas that word is worth no more than any other.
"Unmute the sounds" scored identically against `mute-off`, which matched
"unmute", and `mute-on`, which matched only "sounds" — and the tie went to
whichever was reached first, so asking for the sound back turned it off.

Where the winner declares an opposite, the pair is settled on **which half
the phrase names**, not on which scored higher. Each half owns the words the
other never uses — `unmute`, `back`, `on` against `mute`, `silence`, `off` —
and whichever set the utterance carries is the answer. Where both or neither
appear the score stands: the phrase did not distinguish them, and guessing is
not better than scoring.

The comparison is built from a skill's **whole** lemma set, not its content
lemmas. Stopword flagging is context-dependent — spaCy calls "make" a
stopword in "you can make noise" and not in "stop making noise" — so building
it from content words made a word both halves use look exclusive to one of
them, and the check flipped an exact 1.00 match to its opposite.

Two more details make it work. The deciding words are usually **stopwords**, which
scoring drops — "the" and "your" distinguish nothing, but between two halves
of a switch "on", "off" and "back" are the only words that do, so
`POLARITY_LEMMAS` is kept and the rest of the grammar discarded. And the
opposite is looked up in the registry rather than in the ranking: it often
has no score at all, because the Matcher handed its twin a confident match
and the other half was never compared with anything.

This is declared, never inferred. Nothing in the words says two skills are
opposites — it is a fact about what they do.

## Frames

```python
frames = [
    "what does {subject} mean",
    "what is the definition of {subject}",
]
```

A frame is a fixed phrase with one hole. The words before it should open the
utterance, the words after it should close it, and the subject is whatever
sits between.

Frames exist because word overlap cannot separate two questions that differ
only in **where the subject sits**. `what does {subject} mean` and `what does
{subject} look like` share every leading word, and the part that decides comes
*after* the hole — which a bag of lemmas has thrown away by then.

Scoring is a harmonic mean of two coverages, the same shape the `act` track
uses — so both produce numbers meaning the same thing, share one ranked list
and one floor, and neither needs a precedence rule to beat the other.

Recall is how much of the frame's fixed wording the utterance carried.
**Precision is against the whole utterance, subject included** — the subject
is a hole the frame did not explain, so it counts against it. That puts
specificity into the score rather than leaving it beside as a tie-break: four
fixed words explaining six beats two explaining four.

Measured the other way, `what is {subject}` scores a flawless 1.0 on any
"what is X" whatsoever and outranks every skill that actually knows what X is
— `what is the weather` went to the encyclopedia.

| Said                           | Frame                     | Score |
|--------------------------------|---------------------------|-------|
| what does an axolotl look like | `what does {s} look like` | 0.91  |
| what is hyvee                  | `what is {s}`             | 0.80  |
| what is a volcano              | `what is {s}`             | 0.67  |
| tell me a joke about penguins  | `tell me about {s}`       | 0.44  |

The last one is why the floor matters. Those words really are in that order,
so it is a true reading — but most of the utterance ends in the hole, and a
reading is not an answer. Below `FALLBACK_DEFAULT_RULE_SCORE` it goes to the
fallback, the same as any weak act match.

**Partial, on purpose.** Binary alignment is a cliff: the transcriber
contracts "what does" to "whats", one fixed word is gone, and the frame that
should have matched scores nothing. Allowing a fixed word to be missing or
wrong keeps the right frame winning.

**Fixed wording is compared loosely too.** A frame's value is being literal,
but nobody says a fixed phrase the same way twice and the transcriber
mishears the rest — "mean" arrives as "means", "definition" as "definiton",
"about" as "abut", "of" as "or". Each fixed word is matched exactly, then as
an inflection of itself, then on JaroWinkler.

The inflection step matters because frames compare **surface forms** where
the act track lemmatises first. After lemmatisation, one word being the front
of another means they are genuinely different words, which is why
`fuzzy_equal` refuses it — `time` and `timer`. Here it is the ordinary case,
and refusing it sent "how many season does naruto have" to the episode skill.

**But a frame keeps its own telling words.** The grammar holding a frame
together — "what", "is", "the", "of" — is interchangeable, and losing any of
it to a mishearing is survivable. The words that make the frame what it is
are not. `what is the definition of {word}` matches "what is the capital of
peru" on four fixed words out of five, and the one it misses is the only one
that mattered, so a question about Peru arrived at the dictionary. At least
half of a frame's non-grammar words must be present; the list of what counts
as grammar is `FRAME_GRAMMAR`.

Two rules keep a loose frame from swallowing a specific one. Words sitting
where fixed wording should be that the frame did not account for are charged
`SPARE_PENALTY` each — without it `what is {subject}` takes "does an axolotl
look like" as its subject and scores well on a fluke. And a subject made only
of the frame's own words is refused, since sliding a boundary onto a fixed
word turns "what does mean" into a lookup of "does".

**Punctuation is dropped before alignment.** Every transcript arrives with a
full stop or a question mark on the end, and a frame compares word for word —
so a stray `?` is a token that has to be accounted for. It lands where the
suffix belongs and wrecks the match: "what does an axolotl look like?" scores
0.36 rather than 0.80, under the floor. The `act` track never meets this
because `_is_content` filters punctuation on the way in; frames tokenise the
utterance directly, so both must drop it or the two tracks disagree about
what a word is.

`SLACK` (2) bounds how far the boundaries may move; more turns the search into
a scan. Where two frames tie, the one with more fixed wording wins — it is the
more particular reading of the same phrase.

**The hole also extracts the subject exactly.** `how many episodes does
{subject} have` yields `frieren`, not `frieren have`, because the frame says
where the subject ends — which is what a prefix-only anchor cannot do.

**And it arrives as a keyword argument named after the hole.** An `ask`
skill's holes *are* its arguments — there is nothing to extract afterwards
and nothing to trim — so a frame of `when is {holiday}` calls
`func(holiday="christmas")`, and `wants_phrase=True` adds `phrase` alongside
it. A handler that does not accept every hole its frames declare raises
`TypeError` when it is finally reached, which reads from outside as the
skill matching and then doing nothing.

Give them defaults, the same as any other argument:

```python
def named_holiday(self, holiday: str = "", phrase: str = ""):
    ...
```

## Typed holes

A hole may be given a predicate, and an alignment whose subject fails it is
not a match at all:

```python
frames=["when is {holiday}", "what date is {holiday}"],
holes={"holiday": lambda text: bool(store.holiday_named(text))},
```

Shape alone is not always enough, because the same shape carries different
questions. "When is the next holiday" and "when is my dentist appointment"
fit `when is {holiday}` exactly as well as "when is christmas" does — only
the data separates them.

The predicate is checked **inside** the boundary search rather than after it,
so a candidate whose subject is the wrong kind of thing gives way to a
lower-scoring one whose subject is right.

**A validated hole carries a bonus.** The predicate has confirmed the subject
against real data, which is evidence of a different order from words lining
up. Without it, "what date is easter" fitted `what date is {holiday}` at
0.857 while a date skill scored 0.867 as a bag of words — a hundredth of a
point apart, decided by whichever example list had been edited last, and the
wrong one won. The shape was right about a real holiday; the bag of words had
only "date".

`TYPED_BONUS` is a margin, not a licence. It cannot lift a frame that did not
fit — only settle one that did against a rival scoring on weaker evidence, so
`what is christmas` still reaches the encyclopedia.

**It runs during matching**, so it must be cheap and free of side effects — a
set lookup, not a request. An exception is read as a refusal rather than
propagated, so a broken predicate loses its own skill instead of the whole
utterance. A hole named in `holes` that no frame has raises `BadSkill`, since
a typed hole matching no frame silently never runs.

This is what a grammar's non-terminal does — `when is <holiday>` where
`<holiday>` is a terminal set — arrived at one skill at a time rather than by
rewriting the engine into a parser.

## A skill's own vocabulary

Each skill derives the non-filler words from everything it is matched
against, and carrying one of them lifts its score by up to
`DISTINCTIVE_BONUS`. A holiday skill listing "when is christmas" and "when is
easter" therefore knows those words are its own, with nothing declared.

**A boost, not a claim.** Owning a word takes the phrase outright, which is
right for `calendar` and wrong for `christmas`: a holiday name is a *subject*
and appears in every kind of question about it, so owning it means asking
what Christmas **is** returns a date.

It lifts only skills that already scored. Scoring an unranked skill on the
strength of one word resurrects it on a subject alone: "what is christmas"
reduces to `{christmas}`, an exact content match for the holiday skill's
"when is christmas" — the question word is the entire difference and content
reduction has already discarded it. That case is what frames are for, not
what a boost can fix.

`FILLER_LEMMAS` is wider than spaCy's stop list, which is about grammar.
These are words appearing across so many skills that their presence says
nothing about which was meant.

## Words the transcriber got slightly wrong

A speech model substitutes acoustically similar words, and no amount of extra
example phrasings recovers those. So a lemma that is *nearly* a lemma in an
example still counts, scored on **JaroWinkler** similarity from `rapidfuzz`.

JaroWinkler weights a shared prefix, which is how a mishearing behaves — the
front of the word survives and the middle garbles:

| Compared                     | Score |
|------------------------------|-------|
| `aplication` / `application` | 0.976 |
| `narudo` / `naruto`          | 0.933 |
| `whether` / `weather`        | 0.914 |
| `axolata` / `axolotl`        | 0.886 |
| `moon` / `noon`              | 0.833 |
| `alarm` / `alert`            | 0.787 |

`FUZZY_MIN_RATIO` (0.86) is the floor, with three guards in front of it:
tokens under `FUZZY_MIN_LENGTH` (4) are compared exactly, since at three
characters almost everything is close to everything; a length difference over
three is refused; and one word being a **prefix** of the other is refused,
because that is a different word rather than a mishearing — `time` is a prefix
of `timer`, and lemmatisation has already dealt with real inflections.

`FUZZY_SOLE_RATIO` (0.90) is the higher bar a fuzzy match must clear when it
is the **only** thing holding a match together — nothing matched outright and
the whole score rests on one word being nearly another. A single substituted
character in a short word is indistinguishable from a mishearing by any string
metric (`timer` / `tiger` scores 0.893, `whether` / `weather` 0.914), so a lone
fuzzy match has to be surer of itself than one repairing a word inside a phrase
that already agrees. Without it, `what is the ocean` matched `clean` in the
air-quality skill and asked the weather.

## Compounds

Scoring compares one lemma against one lemma, so a single token can never match
two. `goodnight` scores 0 against `good` (the prefix rule) and 0 against `night`
(too different in length), and the phrase matches nothing — even with
`"good night"` listed as an example.

The transcriber writes compounds either way depending on the sentence, so before the
per-token loop the whole phrase is compared with the spaces removed:

```python
if "".join(example) == "".join(content):
    return 1.0          # the same thing said
```

The letters in order, ignoring where the gaps fell. `goodnight` / `good night`,
`goodmorning` / `good morning`, `wifi` / `wi fi`. Exact, so `goodnight` still
does not match `good morning`.

Listing a phrase both ways in `examples` is still worth doing — this is the
safety net, not the plan.

## A skill runs on a worker

Handlers are called from the assistant's thread, not the UI one. Anything
reaching Qt has to be handed over:

```python
def go_to_page(self, page_name: str = ""):
    ...
    self.client.call_on_ui(
        lambda target=best: self.client.goto(target, override=True))
```

`goto()` rebuilds a page and a plugin's own navigation fades the backlight —
both Qt work. Calling them inline produces `Timers cannot be stopped from
another thread`, `Cannot set parent, new parent is in a different thread`, and
a page torn down underneath its own widgets.

This is checked: a method registered as a skill's `func` may not call
`client.goto`, `dialog`, `apply_settings` or the quiet-mode setters directly. A
nested function passed to `call_on_ui` by name counts as marshalled.
