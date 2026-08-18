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


## The skill

`nevermind` declares `wants_phrase=True`, so it receives the whole utterance —
which word was said is the decision, not an argument extracted from it.

```python
def nevermind(self, phrase: str = "", **_ignored):
    action = self.client.CANCEL.run(phrase)
    if action is None:
        self.client.cancel_assistant("nevermind")
        return
    if action.stops_listening:
        self.client.cancel_assistant(f"nevermind: {action.key}")
```

There is no built-in cancel path ahead of intent matching. The one remaining
`is_cancel` check is **inside a session**, where there is no intent matching at
all and so no skill to route to.


## Adding your own

Anything that puts something on screen or starts something running should
register. Ask two questions:

**Which words mean this specifically?** Not every cancel word — the ones that
make sense. A timer answers to "cancel the timer", not to "shut up".

**How far in front is it?** A modal dialog outranks a panel, which outranks
music. Pick a number with room either side.
