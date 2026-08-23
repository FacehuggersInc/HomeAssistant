"""
What the model is asked, in one place.

Two things ask it - the judge inside the panel and the server another machine
runs - and they have to ask the same question. Two prompts is two behaviours,
and the one nobody is watching is the one that drifts.

Copied verbatim into the package that sets a remote machine up, alongside
`judge_protocol.py`, for the same reason.
"""

from __future__ import annotations

#How much of the room to hand over. A transcript is one sentence and a turn
#is two; anything longer than this is a transcriber that has run away, and
#truncating is better than a pass that takes a second.
MAX_CHARS = 600

#What the model is told. Deliberately short: a long instruction costs tokens
#on every utterance, and the decision is not a complicated one.
SYSTEM = (
    "You decide whether somebody was speaking to a voice assistant in their "
    "home, or whether the microphone picked up ordinary conversation, a "
    "television, or a radio.\n"
    "Answer with one word: ANSWER if they were speaking to the assistant, "
    "IGNORE if they were not."
)


def _clip(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text[:MAX_CHARS]


def build_prompt(payload: dict) -> str:
    """
    Everything known about one utterance, as the model sees it.

    Built here rather than at the caller so both backends - this one and the
    server another machine runs - ask the same question. Two prompts is two
    behaviours, and the one nobody is looking at is the one that drifts.
    """
    lines = []
    wake = _clip(payload.get("wake"))
    transcript = _clip(payload.get("transcript"))
    text = _clip(payload.get("text"))

    if payload.get("last_query"):
        lines.append(f"A moment ago they asked: {_clip(payload['last_query'])}")
    if payload.get("last_answer"):
        lines.append(f"The assistant replied: {_clip(payload['last_answer'])}")
    if payload.get("in_session"):
        lines.append("A conversation with the assistant is open.")
    if wake:
        lines.append(f"The assistant is woken by the word \"{wake}\".")
    if transcript and transcript != text:
        lines.append(f"The microphone heard: {transcript}")
    lines.append(f"The utterance to judge: {text or transcript}")
    return "\n".join(lines)




def chat_prompt(body: str, system: str = None) -> str:
    """
    The prompt in the shape Qwen was trained on.

    Written out rather than taken from a chat template, because the template
    lives in transformers and neither end of this has it. `/no_think` keeps
    Qwen3 out of its reasoning mode - which costs nothing when nothing is
    generated, but the model behaves differently when it believes it is about
    to reason.
    """
    return (f"<|im_start|>system\n{system or SYSTEM} /no_think<|im_end|>\n"
            f"<|im_start|>user\n{body}<|im_end|>\n"
            f"<|im_start|>assistant\n")
