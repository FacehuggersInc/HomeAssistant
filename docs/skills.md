# Writing skills

A skill is a phrase the assistant recognises and a function it calls. Declaring
one is a `Skill(...)` and a registration; everything about matching is handled
for you.

For how matching actually works — pattern generation, the rule phase, scoring —
see [Voice assistant](assistant.md). This page is about writing them.

---

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

---

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

---

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

Each argument maps to a **list of pattern alternatives**, and each alternative
is a list of token specs. Anything the Matcher supports works — `LOWER`,
`LEMMA`, `IS_ALPHA`, `LIKE_NUM`, `IN`, `OP` for quantifiers.

Give every argument a default in your function signature. An argument that
does not match is simply not passed, and a skill that raises `TypeError`
because of it looks to the user like the assistant ignoring them.

Arguments arrive as **strings**, exactly as spoken. Parse and validate them
yourself — `"ten"` and `"10"` can both reach you, and
[transcript normalisation](assistant.md) converts most spoken numbers but is
not a guarantee.

---

## Speaking back

```python
def porch_on(self):
    self.client.say("Porch light on.")
```

`say()` returns whether anything was actually spoken. TTS needs an ElevenLabs
key, and a panel without one is a perfectly valid install — so a skill should
never *depend* on being heard. Show a notification or update the UI as well.

---

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

---

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

---

## When nothing matches

An utterance no skill claims fires `on_assistant_fallback`. The bundled
[AI Fallback](bundled-plugins.md) plugin subscribes to it and answers with an
LLM; you can subscribe too.

Being unmatched is a valid outcome and the threshold protects it — scoring
every skill against every phrase would always produce a nearest match, and
"the assistant did the wrong thing" is worse than "the assistant did nothing".
