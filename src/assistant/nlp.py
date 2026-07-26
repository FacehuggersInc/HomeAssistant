from threading import Lock

_MODEL = None
_MATCHER = None
_LOCK = Lock()


def model():
    global _MODEL
    if _MODEL is None:
        with _LOCK:
            if _MODEL is None:
                import spacy
                _MODEL = spacy.load("en_core_web_sm", disable=["parser", "ner"])
    return _MODEL


def shared_matcher():
    global _MATCHER
    if _MATCHER is None:
        with _LOCK:
            if _MATCHER is None:
                from spacy.matcher import Matcher
                _MATCHER = Matcher(model().vocab)
    return _MATCHER


def vocab():
    return model().vocab


def new_matcher():
    from spacy.matcher import Matcher
    return Matcher(vocab())


def new_phrase_matcher(attr: str = None):
    from spacy.matcher import PhraseMatcher
    return PhraseMatcher(vocab(), attr=attr) if attr else PhraseMatcher(vocab())


def preload() -> None:
    model()
    shared_matcher()


def is_loaded() -> bool:
    return _MODEL is not None
