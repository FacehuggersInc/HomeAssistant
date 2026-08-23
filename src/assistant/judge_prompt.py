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

#What the model is asked to SAY, in preference order. These are not the wire
#keys - `judge_protocol` still speaks ANSWER and IGNORE, and the backend maps
#one to the other.
#
#Two properties matter and neither is obvious.
#
#**One token each.** The backend never generates: it reads the logits at a
#single position and compares two candidates there. A label that spells as
#more than one token is represented by its FIRST token, and a short leading
#fragment sits high for reasons that have nothing to do with the question -
#it is the entry point to every continuation the vocabulary can spell from
#it. Compared against a whole word, the fragment wins whatever was said.
#
#**Neither may be a word the instruction uses.** A label that is also the
#verb in "answer with one word" is primed by the sentence asking for it.
#
#A list rather than one pair, because whether a given string is one token is
#a fact about the tokenizer, not about English, and this has to survive a
#different model.
#`A`/`B` looks like the obvious third pair and is not: "a" is an article,
#so the instruction itself is full of it and the token carries the weight
#of every ordinary sentence that starts with one.
LABELS = (("YES", "NO"), ("Y", "N"), ("1", "2"))


def system_for(yes: str, no: str) -> str:
    """
    What the model is told, naming the two words it may say.

    Deliberately short: a long instruction costs tokens on every utterance,
    and the decision is not a complicated one. It is built rather than
    written out so the instruction cannot name one pair while the backend
    compares another.
    """
    return (
        "You decide whether somebody was speaking to a voice assistant in "
        "their home, or whether the microphone picked up ordinary "
        "conversation, a television, or a radio.\n"
        f"Reply with one word: {yes} if they were speaking to the assistant, "
        f"{no} if they were not."
    )


#The default pair's instruction, for anything that wants the text alone.
SYSTEM = system_for(*LABELS[0])


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
    The prompt in the shape Qwen was trained on, ending where the answer goes.

    Written out rather than taken from a chat template, because the template
    lives in transformers and neither end of this has it.

    **The empty reasoning block is not decoration.** Qwen3 opens an assistant
    turn by reasoning, so the token it wants immediately after
    `<|im_start|>assistant\\n` is `<think>` - and this backend never generates,
    it reads the logits at exactly that position and compares two candidates.
    Read there, `ANSWER` and `IGNORE` are both far down a distribution whose
    mass is on `<think>`, and which of the two is larger says close to nothing
    about the utterance.

    `/no_think` alone does not close that. It is a switch the CHAT TEMPLATE
    honours by writing the empty block below; the model does not skip the
    block on its own for having been asked nicely. Writing the prompt by hand
    means writing the block by hand.

    So the turn is opened, the reasoning is closed empty, and the position
    read is the one where the reply itself starts.
    """
    return (f"<|im_start|>system\n{system or SYSTEM} /no_think<|im_end|>\n"
            f"<|im_start|>user\n{body}<|im_end|>\n"
            f"<|im_start|>assistant\n<think>\n\n</think>\n\n")
