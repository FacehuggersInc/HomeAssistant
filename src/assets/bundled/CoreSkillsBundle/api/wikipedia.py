"""
Wikipedia: a summary, a picture, and the caption under it.

Free, unauthenticated, and three endpoints rather than one, because the thing
somebody asks for is spread across all three:

* `search`  - turns "the eiffel tower" into the title `Eiffel Tower`. Titles
  are exact, and the summary endpoint 404s on anything that is not one.
* `summary` - the first paragraph, a thumbnail, and the page URL.
* `media-list` - the **caption**. The summary gives a picture and no words
  about it; the caption under a photograph is written by somebody explaining
  what is in it, which is exactly what "what does an axolotl look like"
  wants and is nowhere else in the API.

A descriptive User-Agent is not optional here. Wikimedia rate-limits and
blocks anonymous clients that do not identify themselves, and the failure is a
403 rather than anything that reads as "you should have said who you were".
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request


class WikipediaAPI:

    HOST = "https://en.wikipedia.org"
    SEARCH = HOST + "/w/api.php"
    SUMMARY = HOST + "/api/rest_v1/page/summary/"
    MEDIA = HOST + "/api/rest_v1/page/media-list/"

    AGENT = ("HomeAssistantPanel/1.0 "
             "(https://github.com/; wall panel voice assistant)")
    TIMEOUT = 8.0
    IMAGE_TIMEOUT = 10.0

    #A picture bigger than this is a panel waiting on a download it will scale
    #away anyway. Wikipedia serves thumbnails at a requested width, so this is
    #what gets asked for rather than what gets thrown away.
    THUMB_WIDTH = 640
    #And a hard cap on what will be read off the wire, in case the URL points
    #at something enormous.
    MAX_BYTES = 6 * 1024 * 1024

    CACHE_SECONDS = 6 * 60 * 60
    CACHE_MAX = 60

    def __init__(self, plugin, client):
        self.plugin = plugin
        self.client = client
        self._cache: dict = {}
        # "missing" when the encyclopedia has no such article, "offline" when
        # it could not be asked. Both come back as None from `look_up`, and
        # telling somebody the wrong one of the two sends them looking for a
        # spelling mistake that is not there.
        self.last_failure = ""

    ## -- plumbing

    def _get(self, url: str, binary: bool = False):
        request = urllib.request.Request(url, headers={"User-Agent": self.AGENT})
        timeout = self.IMAGE_TIMEOUT if binary else self.TIMEOUT
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if binary:
                return response.read(self.MAX_BYTES)
            return json.loads(response.read().decode("utf-8"))

    def _remember(self, key, value):
        if len(self._cache) >= self.CACHE_MAX:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            self._cache.pop(oldest, None)
        self._cache[key] = (time.time(), value)

    def _cached(self, key):
        found = self._cache.get(key)
        if found and time.time() - found[0] < self.CACHE_SECONDS:
            return found[1]
        return None

    ## -- the parts

    def find_title(self, query: str) -> str:
        """
        The article title best matching a spoken phrase, or "".

        Searched rather than guessed. The summary endpoint takes an exact
        title and 404s on everything else, so "the eiffel tower" and "eiffel
        tower" and "effiel tower" are three different failures without this
        and one article with it.
        """
        query = (query or "").strip()
        if not query:
            return ""

        params = urllib.parse.urlencode({
            "action": "query", "list": "search", "srsearch": query,
            "srlimit": 1, "format": "json", "srnamespace": 0,
        })
        try:
            body = self._get(f"{self.SEARCH}?{params}")
            hits = ((body or {}).get("query") or {}).get("search") or []
            return str(hits[0].get("title") or "") if hits else ""
        except Exception as e:
            self.client.log("warning", f"[Wikipedia] Search for {query!r} failed: {e}")
            self.last_failure = "offline"
            return ""

    def summary(self, title: str) -> dict | None:
        """
        {title, extract, description, url, image, type} for an exact title.

        `type` carries `disambiguation` through untouched. A disambiguation
        page has an extract that reads like an answer and is not one - "Mercury
        may refer to:" - so the caller has to be able to tell.
        """
        title = (title or "").strip()
        if not title:
            return None

        cached = self._cached(("summary", title))
        if cached is not None:
            return cached

        try:
            body = self._get(self.SUMMARY + urllib.parse.quote(title.replace(" ", "_")))
        except Exception as e:
            self.client.log("warning", f"[Wikipedia] Summary for {title!r} failed: {e}")
            self.last_failure = "offline"
            return None
        if not isinstance(body, dict):
            return None

        thumb = str((body.get("thumbnail") or {}).get("source") or "")
        original = body.get("originalimage") or {}
        source = str(original.get("source") or "")

        # How wide the file actually IS, so the thumbnail is not asked for at
        # a size that does not exist. Wikimedia will render a thumbnail at any
        # width up to the original and **404s above it** - so rewriting every
        # URL to 640px silently lost the picture for every article whose lead
        # image is smaller than that, which is a great many of them.
        try:
            widest = int(original.get("width") or 0)
        except (TypeError, ValueError):
            widest = 0

        found = {
            "title":       str(body.get("title") or title),
            "extract":     str(body.get("extract") or "").strip(),
            "description": str(body.get("description") or "").strip(),
            "url":         str(((body.get("content_urls") or {})
                                .get("desktop") or {}).get("page") or ""),
            "image":       self._sized(thumb or source, widest),
            # Every other URL the response offered, in order, so a picture is
            # only given up on once all of them have failed. One 404 on a
            # rewritten thumbnail used to be the end of it.
            "image_fallbacks": [u for u in (thumb, source) if u],
            "type":        str(body.get("type") or ""),
        }
        self._remember(("summary", title), found)
        return found

    def intro(self, title: str) -> str:
        """
        The article's whole introduction, paragraphs and all, or "".

        The summary endpoint returns the LEAD only - usually one paragraph -
        and the second paragraph of a Wikipedia introduction is very often
        where the useful part is: the first says what category a thing is in,
        the second says what is actually interesting about it.

        `exintro` with `explaintext` gives the intro as plain text with blank
        lines between paragraphs, which is the one thing the REST summary
        cannot provide.
        """
        title = (title or "").strip()
        if not title:
            return ""

        cached = self._cached(("intro", title))
        if cached is not None:
            return cached

        params = urllib.parse.urlencode({
            "action": "query", "prop": "extracts", "exintro": 1,
            "explaintext": 1, "redirects": 1, "format": "json",
            "titles": title,
        })
        try:
            body = self._get(f"{self.SEARCH}?{params}")
            pages = ((body or {}).get("query") or {}).get("pages") or {}
            # Keyed by page id, and the id is not known in advance. A
            # negative one means "no such page", which is not an error worth
            # raising - it is an empty answer.
            text = ""
            for page in pages.values():
                text = str((page or {}).get("extract") or "").strip()
                if text:
                    break
        except Exception as e:
            self.client.log("debug", f"[Wikipedia] No intro for {title!r}: {e}")
            return ""

        self._remember(("intro", title), text)
        return text

    def caption(self, title: str) -> str:
        """
        The words under the article's lead picture, or "".

        The media list gives every image with whatever caption the article
        wrote for it, and the lead one is the first with a caption at all -
        many have none, and an empty caption is not a reason to stop looking
        at the next.
        """
        title = (title or "").strip()
        if not title:
            return ""

        cached = self._cached(("caption", title))
        if cached is not None:
            return cached

        try:
            body = self._get(self.MEDIA + urllib.parse.quote(title.replace(" ", "_")))
        except Exception as e:
            self.client.log("debug", f"[Wikipedia] No media list for {title!r}: {e}")
            return ""

        text = ""
        for item in (body or {}).get("items") or []:
            if not isinstance(item, dict) or item.get("type") != "image":
                continue
            found = (item.get("caption") or {}).get("text") or ""
            found = self.strip_markup(str(found))
            if found:
                text = found
                break

        self._remember(("caption", title), text)
        return text

    def image_bytes(self, url: str, fallbacks: list = None) -> bytes | None:
        """
        The picture itself, or None, trying each URL in turn.

        More than one because the first is a guess. The thumbnail URL is
        rewritten to ask for a useful size, and a rewrite that asks for
        something the file cannot supply is a 404 - which is a worse outcome
        than a small picture, and used to be the whole answer.
        """
        candidates = [u for u in [url] + list(fallbacks or []) if u]
        seen = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)

            cached = self._cached(("image", candidate))
            if cached is not None:
                return cached
            try:
                data = self._get(candidate, binary=True)
            except Exception as e:
                self.client.log("debug",
                                f"[Wikipedia] {candidate} failed: {e}")
                continue
            if not data:
                continue
            self._remember(("image", candidate), data)
            return data

        if candidates:
            self.client.log("warning",
                            f"[Wikipedia] No image could be fetched from "
                            f"{len(candidates)} URL(s); first was {candidates[0]}")
        return None

    ## -- shaping

    def _sized(self, url: str, widest: int = 0) -> str:
        """
        A thumbnail URL asked for at the width the panel will use.

        Wikipedia encodes the width in the path - `.../120px-Foo.jpg` - so a
        thumbnail arrives at whatever size the summary felt like, often 120
        wide, which on a 600px card is a stamp. Rewriting the number asks for
        the size actually wanted.

        Capped at the original's width when that is known. Wikimedia renders
        any width UP TO the file's own and 404s above it, so asking a 500px
        photograph for 640 does not give a slightly soft picture - it gives
        no picture.
        """
        if not url:
            return ""
        want = self.THUMB_WIDTH
        if widest:
            want = min(want, widest)
        current = re.search(r"/(\d+)px-", url)
        if not current:
            return url
        # Never smaller than what was offered. If the summary already handed
        # back something wider than the cap, leaving it alone is free.
        if int(current.group(1)) >= want:
            return url
        return re.sub(r"/(\d+)px-", f"/{want}px-", url, count=1)

    @staticmethod
    def strip_markup(text: str) -> str:
        """Tags and reference brackets out; whitespace collapsed."""
        text = re.sub(r"<[^>]+>", " ", text or "")
        text = re.sub(r"\[\d+\]", "", text)
        text = text.replace("&nbsp;", " ").replace("&amp;", "&")
        text = text.replace("&quot;", '"').replace("&#39;", "'")
        return " ".join(text.split())

    #Full stops that end a word rather than a sentence. A split on ". " puts
    #"St. Louis" and "J. R. R. Tolkien" in different sentences, and an answer
    #that stops at "He lived in St." reads as a broken panel.
    ABBREVIATIONS = {
        "st", "mr", "mrs", "ms", "dr", "prof", "jr", "sr", "vs", "etc",
        "approx", "inc", "ltd", "co", "mt", "ft", "no", "fig", "al", "ca",
        "cf", "op", "ed", "pp", "vol",
    }

    @classmethod
    def _sentences(cls, text: str) -> list:
        """`text` split into sentences, abbreviations kept whole."""
        # Split first, then put back what should not have been split. A
        # single regex cannot do it: `re` has no variable-length lookbehind,
        # which is what excluding a list of words before the stop would need.
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", text)
        merged = []
        for part in parts:
            if merged:
                tail = merged[-1].rstrip(".").rsplit(" ", 1)[-1].lower()
                # An initial too: "J. R. R. Tolkien" is four splits.
                if tail in cls.ABBREVIATIONS or (len(tail) == 1 and tail.isalpha()):
                    merged[-1] = f"{merged[-1]} {part}"
                    continue
            merged.append(part)
        return merged

    @classmethod
    def first_paragraphs(cls, text: str, count: int = 2,
                         limit: int = 900) -> str:
        """
        The opening paragraphs of an article, whole.

        Two by default. The first paragraph of a Wikipedia introduction says
        what kind of thing something is; the second says what is worth
        knowing about it, and stopping at the first is the half that reads
        like a dictionary entry.

        Paragraphs are kept whole where they fit and cut at a sentence where
        they do not - a paragraph ending mid-word reads as a broken panel
        rather than an abbreviated answer.
        """
        text = (text or "").strip()
        if not text:
            return ""

        parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if not parts:
            return ""

        blob = ""
        for part in parts[:max(1, count)]:
            candidate = f"{blob}\n\n{part}" if blob else part
            if len(candidate) <= limit:
                blob = candidate
                continue
            # This paragraph does not fit whole. Take what fits of it, to a
            # sentence, rather than dropping it - the second paragraph is the
            # one worth having and half of it beats none.
            room = limit - len(blob) - 2
            if room > 120:
                sentences = cls._sentences(part)
                taken = ""
                for sentence in sentences:
                    if len(taken) + len(sentence) + 1 > room:
                        break
                    taken = f"{taken} {sentence}".strip()
                if taken:
                    blob = f"{blob}\n\n{taken}" if blob else taken
            break

        return blob or cls.first_blob(text, sentences=3, limit=limit)

    @classmethod
    def first_blob(cls, extract: str, sentences: int = 3, limit: int = 420) -> str:
        """
        The opening of an article, cut at a sentence rather than a character.

        A summary chopped mid-word reads as a broken panel rather than an
        abbreviated answer, and the first sentence of a Wikipedia article is
        almost always the definition somebody wanted anyway.
        """
        extract = (extract or "").strip()
        if not extract:
            return ""

        parts = cls._sentences(extract)
        blob = " ".join(parts[:max(1, sentences)]).strip()
        if len(blob) <= limit:
            return blob

        trimmed = blob[:limit]
        cut = max(trimmed.rfind(". "), trimmed.rfind("! "), trimmed.rfind("? "))
        return (trimmed[:cut + 1] if cut > 60 else
                trimmed.rsplit(" ", 1)[0] + "\u2026")

    def look_up(self, query: str) -> dict | None:
        """
        Search, summarise and caption in one go, or None.

        The three calls belong together because no caller wants one of them:
        a title with no summary is a string, and a summary with no caption is
        a picture nobody has explained.
        """
        self.last_failure = ""
        title = self.find_title(query)
        if not title:
            # `find_title` sets "offline" if it could not ask at all; an
            # empty result with no failure recorded means it asked and there
            # was nothing.
            self.last_failure = self.last_failure or "missing"
            return None
        found = self.summary(title)
        if not found or not (found["extract"] or found["image"]):
            self.last_failure = self.last_failure or "missing"
            return None
        found["caption"] = self.caption(title)
        # The fuller intro when there is one, the summary's lead when there
        # is not. Kept apart from `extract` so a caller wanting the short
        # form still has it.
        found["intro"] = self.intro(title) or found["extract"]
        return found

    def picture(self, found: dict) -> bytes | None:
        """The picture for a `look_up` result, trying every URL it offered."""
        if not found:
            return None
        return self.image_bytes(found.get("image") or "",
                                found.get("image_fallbacks"))
