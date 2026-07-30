# Writing skills

A skill is a phrase the assistant recognises and a function it calls. Declaring
one is a `Skill(...)` and a registration; everything about matching is handled
for you.

For how matching actually works — pattern generation, the rule phase, scoring —
see [Voice assistant](assistant.md). This page is about writing them.


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

| Key | Matches | Example |
|---|---|---|
| `LOWER` | The lowercased text. | `{"LOWER": "timer"}` |
| `ORTH` / `TEXT` | The exact text, case-sensitive. | `{"ORTH": "AM"}` |
| `LEMMA` | The dictionary form, so one spec covers inflections. | `{"LEMMA": "set"}` matches set/sets/setting |
| `POS` | Coarse part of speech. | `{"POS": "NOUN"}` |
| `TAG` | Fine-grained tag. | `{"TAG": "NNP"}` |
| `DEP` | Dependency label. | `{"DEP": "dobj"}` |
| `SHAPE` | Orthographic shape. | `{"SHAPE": "dddd"}` matches `2026` |
| `ENT_TYPE` | Named entity type. | `{"ENT_TYPE": "TIME"}` |
| `IS_ALPHA` | Letters only. | `{"IS_ALPHA": True}` |
| `IS_DIGIT` | Digits only. | `{"IS_DIGIT": True}` matches `10`, not `ten` |
| `IS_PUNCT` | Punctuation. | `{"IS_PUNCT": True}` |
| `IS_STOP` | A stop word. | `{"IS_STOP": False}` |
| `LIKE_NUM` | Anything numeric. | `{"LIKE_NUM": True}` matches `10` **and** `ten` |
| `LIKE_URL` / `LIKE_EMAIL` | Looks like one. | `{"LIKE_URL": True}` |

`LIKE_NUM` over `IS_DIGIT` almost always. Whisper writes numbers either way
depending on how they were said, and normalisation converts most but not all.

### Value operators

A value can be a dict instead of a literal:

| Operator | Means | Example |
|---|---|---|
| `IN` | One of a list. | `{"LOWER": {"IN": ["minute", "minutes", "min"]}}` |
| `NOT_IN` | None of a list. | `{"LOWER": {"NOT_IN": ["not", "cancel"]}}` |
| `REGEX` | Matches a pattern. | `{"LOWER": {"REGEX": "^colou?r$"}}` |
| `FUZZY` | Approximate, for mishearings. | `{"LOWER": {"FUZZY": "notifications"}}` |
| `>=`, `<=`, `>`, `<`, `==` | Numeric comparison on a numeric attribute. | `{"LENGTH": {">=": 4}}` |

`IN` is the workhorse. It is how you accept singular and plural, or a handful
of synonyms, without writing an alternative for each.

### Quantifiers — `OP`

`OP` says how many of the preceding token spec to match:

| `OP` | Means |
|---|---|
| `"?"` | Zero or one — an optional token. |
| `"*"` | Zero or more. |
| `"+"` | One or more. |
| `"!"` | Exactly zero — assert this token is *not* here. |
| `"{2,3}"` | Between two and three. |
| `"{2}"` | Exactly two. |
| `"{2,}"` | Two or more. |

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

| Said | Score against `play a song` |
|---|---|
| `play yesterday` | 0.57 |
| `play everlong` | 0.40 |
| `play never gonna give you up` | 0.20 |

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

```python
def porch_on(self):
    self.client.say("Porch light on.")
```

`say()` returns whether anything was actually spoken. TTS needs an the voice backend
key, and a panel without one is a perfectly valid install — so a skill should
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

Being unmatched is a valid outcome and the threshold protects it — scoring
every skill against every phrase would always produce a nearest match, and
"the assistant did the wrong thing" is worse than "the assistant did nothing".
