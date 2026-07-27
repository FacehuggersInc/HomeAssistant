# AI Fallback

Answers anything the skill engine did not understand by asking OpenAI, and
shows the reply in a chat panel.

Listens for `on_assistant_fallback`, which the client fires when
`SkillIntentEngine` finds no skill for a phrase. Skills always win; this only
ever sees what nothing else claimed.

## Setup

Needs an OpenAI API key, entered under this plugin's settings and stored in
`.env`. Without one the plugin stays quiet and unmatched phrases behave as
they did before.

**OpenAI has no free tier.** The account needs credit on it. An
`insufficient_quota` error is a billing limit, not a rate limit, so waiting
will not help.

## Model

`gpt-5.4-mini`. Add more to the `model` options in `settings.json` if you want
the choice back.
