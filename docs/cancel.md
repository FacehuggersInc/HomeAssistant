# What "stop" means right now

"Nevermind" and "stop" are not one instruction.

Said with an answer panel open they mean close it. Said over music they mean
stop the music. Said with neither they mean stop listening. And the words are
not interchangeable: **"stop" fits music where "nevermind" does not**, while
"nevermind" is exactly right for a question somebody has thought better of
asking.

So whatever can be cancelled says so itself — which words apply to it, when it
is applicable, and what to do. The `nevermind` skill asks `client.CANCEL`
rather than holding a list of special cases that grows every time something new
appears.


## Registering

```python
self.client.CANCEL.register(
    "aifallback", "answer_panel",
    keywords=["nevermind", "never mind", "forget it", "cancel that", "stop"],
    handler=self.close_panel,
    is_active=self._panel_is_open,
    priority=50,
    description="close the answer panel and its session",
)
```

|                   |                                                                                                                   |
|-------------------|-------------------------------------------------------------------------------------------------------------------|
| `keywords`        | The phrases **this** thing answers to. Normalised on the way in, so case and spacing do not have to be got right. |
| `is_active`       | Whether there is anything to back out of right now. Omitted means always.                                         |
| `priority`        | Highest first.                                                                                                    |
| `stops_listening` | Whether backing out should also stand the assistant down. Default `True`.                                         |

Registering with no keywords or no handler is refused with a warning, since
neither can ever fire.


## How a phrase matches

A keyword matches when its words appear **in** the phrase, as whole words and
in order. So *stop* fires on "stop", on "why are you still hearing me, stop"
and on "please stop it now" — and not on "stopwatch", because a substring is
not a word. Punctuation is dropped first: a transcriber writes "me, stop" and
the comma is not something anybody said.

Containment rather than equality, because nobody says a keyword on its own.
An apology, a complaint or a second thought around the word — "sorry, stop",
"why are you still hearing me, stop" — is the same instruction, and matching
the whole phrase puts all of them out of reach of the thing they are aimed at.

Register the plain word rather than every sentence it might appear in. A long
keyword is still matched in order and together, so `"stop the music"` does not
fire on "music stop the" or on "stop the loud music".

## How it resolves

`CANCEL.run(phrase)` walks the active actions that match, highest priority
first, and runs the first that succeeds. A handler that raises is logged and the
next is tried; an `is_active` that raises counts as inactive.

**Priority is what makes two things at once work.** With an answer panel open
over playing music:

| Said        | What happens                                              |
|-------------|-----------------------------------------------------------|
| *stop*      | The panel closes — it is in front, at priority 50.        |
| *shut up*   | The music stops — the panel does not answer to that word. |
| *nevermind* | The panel closes. Music never claims that word.           |

With only music playing, *nevermind* matches nothing and the assistant simply
stands down.

### `stops_listening`

Stopping the music does **not** stop listening: somebody who says "stop" over
music may be about to ask for something else. Closing an answer panel does,
because the conversation it belonged to is over.


## Backing out, ahead of the skills

**A cancellation on the end of a request is checked before intent matching,
not by a skill.** A skill has to win a scoring contest to run, and this one
loses it: "set a timer for ten minutes, no forget it" is mostly a timer
request by every measure the engine has, so the timer skill takes it and sets
one. The words that undo it arrive too late to be voted on.

`normalize.is_cancellation(phrase)` answers this, and returns the phrase that
matched so the log can say which word it acted on. It is asked in
`STTProcessing.process_phrase()` before `SKILLS.parse()`, and inside a session
by `Session.wait_for_phrase()`.

### Position, not containment

A cancel word in the middle of a sentence is usually a word. One on the end is
somebody who changed their mind mid-sentence.

| Said                                        | Result                      |
|---------------------------------------------|-----------------------------|
| "what's the weather, actually never mind"   | Cancelled.                  |
| "set a timer for ten minutes, no forget it" | Cancelled. No timer is set. |
| "how do I stop a nosebleed"                 | A question. Answered.       |
| "what time does the bus stop"               | A question. Answered.       |
| "never mind the weather, tell me the news"  | A news query.               |

Two tiers, because the words are not equally safe at the end of a sentence.

**`TERMINAL_CANCELS`** mean it wherever they land — *never mind*, *forget it*,
*forget about it*, *cancel that*, *scratch that*, *disregard that*, *don't
worry about it*, *stop listening*, *quit listening*, *as you were*. No request
ends with one of these.

**`GUARDED_CANCELS`** are ordinary words in ordinary requests — *stop*,
*cancel*, *nothing*, *quit*, *abort*, *enough*, *disregard*, *leave it*. One
of these needs a `CANCEL_MARKER` directly in front of it — *no*, *actually*,
*wait*, *on second thought*, *you know what*, *hold on* — or has to be the
whole utterance.

Trailing politeness comes off first, so "set a timer, never mind then thanks"
is the same instruction as "never mind".

A missed cancellation costs one more word. A wrong one eats the question, which
is why the guarded tier exists.

`is_cancel()` is the older whole-utterance test and still answers that question.
It is a different question from `is_cancellation()`, and the two are not
interchangeable.


## Buttons do not ask

Everything above is about a phrase, where "stop" has to mean whatever is in
front. A button does not have that problem: somebody pressing one has already
said which thing they meant by choosing it.

So the dashboard's controls go straight to what they name rather than through
`CANCEL.run()`:

| Button                    | Does                                               |
|---------------------------|----------------------------------------------------|
| Stop talking              | Cuts off the reply. Keeps listening.               |
| Stand down                | Closes any conversation, back to the wake word.    |
| Close the AI conversation | Tears down the conversation panel and its session. |

Routing these through the registry would make a button's effect depend on what
happens to be registered and in front, which is right for a word and wrong for
a control with a label on it. See [Backend API](api.md#assistanthush-and-assistantstand-down).

The last one is registered by AI Fallback, not by the panel, and is what
reaches a conversation left open in an empty room — the case the spoken
"nevermind" cannot help with, because the session swallows every phrase as a
follow-up until it times out.


### Inside a session, a cancel word counts anywhere

**Looser in here, and deliberately.** On the wake path a cancel word has to be
in the right place, because "how do I stop a nosebleed" is a question and
treating it as a cancellation eats it. Inside a session somebody is already
mid-conversation with the panel, is looking at it, and is trying to make it
stop — so a cancel word counts wherever it lands, the way the wake word does.

"How do I, how do I, how do I stop" is somebody stuttering at a panel that will
not shut up, and it has to work.

The vocabulary comes from `client.CANCEL` rather than a second list, so the
answer panel says what closes it and nothing drifts. Only **active** actions
are consulted — with nothing in front there is nothing to find, and the
positional test is the only one that speaks.

**Looser is not flat.** The same two tiers apply to whatever the thing in
front registered: a keyword in `TERMINAL_CANCELS` counts wherever it lands,
and anything else has to be the last thing said. Flat containment ended the
conversation on "you have to stop blaming yourself", "I can't cancel the
meeting now" and "there's nothing I can do", which is television. Of ten such
lines, one still closes a conversation; without the tiers it was seven.

`CancelAction.matched_all()` reports every keyword that matched, longest
first, and all of them are tried. "Please stop it" matches both `stop it` and
`stop`, and only the longer one is at the end — rejecting on the first tried
would drop a real cancellation.

| In a session      | Said                              | Result                                       |
|-------------------|-----------------------------------|----------------------------------------------|
| Answer panel open | "how do I how do I how do I stop" | The panel closes.                            |
| Answer panel open | "will you just stop"              | The panel closes.                            |
| Answer panel open | "what about Tuesday"              | Passed to the conversation.                  |
| Music only        | "stop"                            | Music stops, **the conversation continues**. |
| Panel and music   | "stop"                            | The panel closes; the music plays on.        |
| Nothing in front  | "how do I stop a nosebleed"       | Passed to the conversation.                  |

`stops_listening` is what makes row four work: stopping the music is not
somebody finishing talking, so `wait_for_phrase()` keeps waiting rather than
returning `None`.

`CancelAction.matched(phrase)` answers *which* keyword was found, so the log
can say what it acted on. `matches()` is the same question asked yes-or-no.


## The skill

`nevermind` declares `wants_phrase=True`, so it receives the whole utterance —
which word was said is the decision, not an argument extracted from it.

```python
def nevermind(self, phrase: str = "", **_ignored):
    if phrase and not normalize.is_cancellation(phrase):
        raise SkillDeclined(...)
    self.client.CANCEL.handle(phrase)
```

**It declines a phrase that is not a cancellation.** Its examples are short and
every one of them is an ordinary word, so it wins phrases that merely contain
one: "how do I stop a nosebleed" scored as a cancellation and stood the panel
down instead of answering. Declining hands the phrase to the next-ranked skill.
The test is the same function the check ahead of the skills uses, so a phrase
cannot be a cancellation on one path and a question on the other.

### `CANCEL.handle(phrase)`

Runs what applies and stands the panel down when nothing does, respecting
`stops_listening`. `run()` answers *what applied*; `handle()` answers *what to
do about it*, which all three callers were otherwise writing out for
themselves.

```python
action = CANCEL.run(phrase)          # what applies, or None
CANCEL.handle(phrase)                # ...and do it
```


## Adding your own

Anything that puts something on screen or starts something running should
register. Ask two questions:

**Which words mean this specifically?** Not every cancel word — the ones that
make sense. A timer answers to "cancel the timer", not to "shut up".

**How far in front is it?** A modal dialog outranks a panel, which outranks
music. Pick a number with room either side.
