"""
Sole owner of the spaCy pipeline.

This used to live at module scope in `src/__init__.py`, which meant every
importer of `src` — the Flask backend, app.py, every plugin — paid the
model load (~1-2s, tens of MB) whether or not the voice assistant was ever
going to be used.

The model now loads on first use. Only `skill.py` and `stt.py` consume it.

If you would rather absorb the cost during startup than take a stutter on
the first wake word, call `preload()` from a background thread during
Client.build() — it is a plain function and safe to call more than once.
Nothing calls it right now, so the default behaviour is lazy.
"""

from threading import Lock

_MODEL = None
_MATCHER = None
_LOCK = Lock()


def model():
    """The shared spaCy Language object. Loads on first call."""
    global _MODEL
    if _MODEL is None:
        with _LOCK:
            if _MODEL is None:
                import spacy
                _MODEL = spacy.load("en_core_web_sm", disable=["parser", "ner"])
    return _MODEL


def shared_matcher():
    """
    The process-wide Matcher, previously the module-level MATCHER global in
    skill.py. Shared deliberately — Skill and SkillIntentEngine both add
    patterns to the same instance.
    """
    global _MATCHER
    if _MATCHER is None:
        with _LOCK:
            if _MATCHER is None:
                from spacy.matcher import Matcher
                _MATCHER = Matcher(model().vocab)
    return _MATCHER


def vocab():
    """Convenience for the common `Matcher(model().vocab)` construction."""
    return model().vocab


def new_matcher():
    """A fresh Matcher bound to the shared vocab."""
    from spacy.matcher import Matcher
    return Matcher(vocab())


def new_phrase_matcher(attr: str = None):
    """A fresh PhraseMatcher bound to the shared vocab."""
    from spacy.matcher import PhraseMatcher
    return PhraseMatcher(vocab(), attr=attr) if attr else PhraseMatcher(vocab())


def preload() -> None:
    """Force the model in now rather than on first use. Safe to call twice."""
    model()
    shared_matcher()


def is_loaded() -> bool:
    """True if the model has actually been loaded. Does not trigger a load."""
    return _MODEL is not None
