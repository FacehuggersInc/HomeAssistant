# Writing these pages

**This page is not part of the documentation.** It is the rules the other
pages are written to, kept here because this is where somebody editing one
will look. It is absent from `NAV_GROUPS` and named in `docs.UNLISTED`, so it
has no sidebar entry, no prev/next, no search hit and no URL — see
`resolve()`. Nothing links here on purpose.

---

## The one rule

**The docs describe the current state of the codebase and nothing else.**

A page that narrates its own history ages twice as fast as one that does not,
and a reader arriving today has no use for what the code did last month. The
reason a thing is the way it is belongs here; the fact that it was once
another way does not.

So: "a keyword matches when its words appear in the phrase, because nobody
says one on its own" — rather than an account of what it matched before.
State the rule and the reason for it, and leave the change out.

The rewrite is always the same move: **keep the reasoning, drop the narrative
of arriving at it.** Past-tense bug narration becomes a present-tense
conditional. "Clearing the home page worked until the page was reopened"
becomes "clearing the home page holds only until the page is reopened."


## What the docs are for

- What is there, how to use it, and short examples.
- Brief explanations of *why*, where the why is not obvious.
- What errors arrive if it is done wrong.
- Accurate references: function names, their arguments, event names, endpoint
  paths, settings paths, skill keys.


## What they must never contain

Every one of these has been found in these pages and removed. They are subtle
and they propagate, because a feature added usually gets explained twice.

| Forbidden                         | Example found                                                                     |
|-----------------------------------|-----------------------------------------------------------------------------------|
| Narrating a former state          | "speaking used to be false for all of them"                                       |
| Explaining how a thing came about | "It came about because Core Widgets loads before Nighttime Clock"                 |
| Then-and-now contrasts            | "They used to end through the same function"                                      |
| Naming a past bug                 | "so the card vanished mid-sentence"                                               |
| Dating a feature                  | "devices approved long before it existed"                                         |
| Justifying by absence             | "until there was a way to send one over the network, the only way was a keyboard" |
| Pointing at removed files         | "check_text_fits.py reads every setFixedHeight"                                   |

The last two slip through most. **Justifying by absence** hides in a
subordinate clause about the user's situation rather than the code's, and
reads as background. **Dating a feature** appears wherever a default is
explained.

**Code comments follow the opposite rule where they must.** A comment may
explain why an obvious-looking approach was not taken, because the next
person will otherwise try it. That belongs beside the code, not on a page
somebody reads to find out how the panel works.


## Checking them

`sweep_docs.py` lives in scratch, not in the tree. It implements 1, 2, 5, 6,
8, 9 and 10 below, and it is mutation-tested — see the handoff.

1. **Historical framing**: `used to`, `it came about`, `previously`, `had
   been`, `ever since`, `until (this|now|then)`, `worked until`, `predates
   it`, `long before it existed`, `there was no way`, `the only way to … was`,
   `originally`, `at first`, `since then`, `the old (code|way|version)`.
2. Every `name()` in the docs resolves to a real `def` or a real
   `public.expose` key.
3. Every event in code is documented and every documented event exists.
4. Every `skill_key` named in docs exists in code.
5. Every settings path named exists in the JSON.
6. Every endpoint named matches an `@app.route`.
7. Every `/docs/plugin/<key>/<page>` route resolves to a real plugin key and
   a real doc file.
8. Markdown tables are aligned — every row in a block the same length.
9. **Comments that state numbers must state the right ones.** Where a comment
   gives a figure, compute it.
10. **A doc that describes a key the template reads must name a key that
    exists.**

### Writing the checks

Three lessons, each of which cost a run:

**Narrow the pattern or the check is noise.** The first version of the sweep
reported 68 findings, of which about ten were real. `assistant.stt` is a
provider name and not a settings path; `/sys/class/backlight` is a filesystem
path and not an endpoint; "two plugins declaring the same key" is prose and
not a count. A check that reports fifty false positives around the one line
that matters is the same as no check.

**Read the whole file, not the line.** The registry-count claim wraps across
a line break — `objects. There are\n  thirteen.` — so a line-by-line scan
never saw the phrase and the number together and passed on any number at all.
Collapse whitespace first.

**A mention is not a section.** The bundled-plugins check looked for the
plugin key anywhere on the page, which the table at the top satisfies even
with the section deleted. Look for a heading.

### Known false positives on (1)

Read the line before changing it. "As if it had been tapped", "a title that
has since loaded" and "a newly paired device" are all present tense about
runtime. The standing ones are recorded in the sweep's `ALLOWED` set:

| Page               | Phrase              | Why                                     |
|--------------------|---------------------|-----------------------------------------|
| `index.md`         | the whole rule list | It quotes the forbidden phrases.        |
| `assistant.md`     | `predates it`       | Present tense about an installed build. |
| `notifications.md` | `had been`          | "As if it had been tapped."             |
| this page          | all of them         | It IS the rule list. Skipped entirely.  |


## Anchors

**A slug drops non-word characters and turns each space into its own
hyphen.** `## Payloads — an open-ended value` slugs to
`payloads--an-open-ended-value`, with two hyphens. A checker that collapses
whitespace reports that as a dead anchor. It is not one.


## Tables

There is **no way to put a `|` inside a table cell.** The renderer splits the
row on every pipe before it looks at inline code, so backticks do not protect
one — and `\|` is not an escape here either: it splits anyway and leaves a
stray backslash behind. Reword instead: "`GET` or `POST`", not `GET|POST`.

Every row in a block must be the same length, including the separator. The
sweep checks this and it is the single most common thing to get wrong when
adding a row by hand.


## When a change lands

**Fix the pages the change makes false, in the same pass.** A number in a
doc, a default in a setting description, a claim about what happens when
something is missing — these go stale silently, and the next person reads
them as true.

Two that were shipped wrong and are worth learning from:

- The clip cap was raised from ten to fifty and the figure was stated in four
  places. Three were updated; the fourth was in the page's own empty-state
  string, which now carries no number at all so it cannot drift again.
- The speech gate default was turned on with a claim that a panel unable to
  use it "builds the spotter without the gate and says so in the log". That
  was true on one of two code paths. **The claim was verified by reading.**
  A panel went deaf for it.

If a doc asserts that something degrades gracefully, run the degraded case.
