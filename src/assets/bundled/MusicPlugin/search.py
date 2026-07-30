"""
Turning "play everlong" into something with an ID.

The IFrame Player API plays a video; it cannot find one. Two ways to get from
a phrase to an ID, and the plugin will use whichever it can:

* the **Data API**, with a key. Ordered, documented, and it returns the
  channel and the artwork alongside the ID.
* **scraping the results page**, with no key. It works, and it breaks whenever
  YouTube changes its markup, so it is the fallback rather than the default.

Both run on a worker: this is called from a spoken request and a network round
trip on the UI thread would freeze the panel mid-sentence.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request


API = "https://www.googleapis.com/youtube/v3/search"
RESULTS = "https://www.youtube.com/results"
#YouTube Music. Worth having as its own source rather than as a nicer YouTube:
#a track is often filed under a translated or romanised title there and only
#its original title on YouTube - "Kaiju Girl" and "\u4e59\u5973\u602a\u7363"
#are the same song, and searching the one will not find the other.
MUSIC = "https://music.youtube.com/search"

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

#a search costs 100 of the 10,000 daily quota units, so roughly a hundred
#searches a day on a free key
QUOTA_PER_SEARCH = 100


class Result:
    """One thing that could be played."""

    __slots__ = ("video_id", "title", "artist", "art_url", "duration")

    def __init__(self, video_id: str, title: str = "", artist: str = "",
                 art_url: str = "", duration: float = 0.0):
        self.video_id = str(video_id or "")
        self.title = str(title or "")
        self.artist = str(artist or "")
        self.art_url = art_url or (
            f"https://i.ytimg.com/vi/{self.video_id}/hqdefault.jpg"
            if self.video_id else "")
        self.duration = float(duration or 0)

    def __repr__(self):
        return f"Result({self.video_id}: {self.title!r} by {self.artist!r})"

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__slots__}


def _fetch(url: str, timeout: float = 8.0, headers: dict = None) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(4 * 1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        # The body says which parameter it objected to, and throwing it away
        # leaves "400 Bad Request" and nothing to act on.
        detail = ""
        try:
            payload = json.loads(error.read().decode("utf-8", "replace"))
            problem = (payload.get("error") or {})
            reasons = [d.get("reason", "") for d in problem.get("errors", [])]
            detail = f" - {problem.get('message', '')}"
            if reasons:
                detail += f" [{', '.join(r for r in reasons if r)}]"
        except Exception:
            pass
        raise OSError(f"HTTP {error.code}{detail}") from None


def _unescape(text: str) -> str:
    from html import unescape
    return unescape(str(text or "")).strip()


def search_api(query: str, key: str, limit: int = 10,
               restrict: bool = True) -> list:
    """
    The Data API. Raises on failure so the caller can fall back.

    `restrict` asks only for embeddable music. It is worth having - a result
    that cannot be embedded loads, errors, and gets skipped - but the extra
    parameters are also what a fussy key or a region rejects, so a refusal
    is retried without them rather than treated as the API being unavailable.
    """
    fields = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max(1, min(25, int(limit))),
        "key": key,
    }
    if restrict:
        fields["videoEmbeddable"] = "true"
        # Deliberately NOT videoCategoryId: a great deal of music is not
        # filed under Music, and the restriction loses more than it saves.
        fields["videoSyndicated"] = "true"

    payload = json.loads(_fetch(f"{API}?{urllib.parse.urlencode(fields)}"))

    results = []
    for item in payload.get("items", []):
        video_id = (item.get("id") or {}).get("videoId")
        snippet = item.get("snippet") or {}
        if not video_id:
            continue
        thumbs = snippet.get("thumbnails") or {}
        art = ((thumbs.get("high") or thumbs.get("medium")
                or thumbs.get("default") or {}).get("url") or "")
        results.append(Result(video_id,
                              title=_unescape(snippet.get("title")),
                              artist=_unescape(snippet.get("channelTitle")),
                              art_url=art))
    return results


#the results page embeds its data as one JSON blob
_INITIAL = re.compile(r"var ytInitialData\s*=\s*(\{.*?\});</script>", re.S)


def search_scrape(query: str, limit: int = 10) -> list:
    """
    The results page, with no key.

    Reads the JSON the page embeds rather than its markup - the markup is
    generated class names, the blob at least has field names.
    """
    params = urllib.parse.urlencode({"search_query": query, "sp": "EgIQAQ%3D%3D"})
    html = _fetch(f"{RESULTS}?{params}")

    match = _INITIAL.search(html)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return []

    results = []

    def walk(node):
        if len(results) >= limit:
            return
        if isinstance(node, dict):
            renderer = node.get("videoRenderer")
            if isinstance(renderer, dict):
                video_id = renderer.get("videoId")
                if video_id:
                    results.append(Result(
                        video_id,
                        title=_text(renderer.get("title")),
                        artist=_text(renderer.get("ownerText"))))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return results[:limit]


#words that say nothing about which recording this is
_NOISE = {"official", "video", "audio", "lyrics", "lyric", "hd", "hq", "full",
          "music", "the", "a", "an", "by", "ft", "feat", "featuring", "with",
          "version", "remastered", "original", "soundtrack", "ost"}


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", str(text or "").lower())
            if w not in _NOISE and len(w) > 1}


#Below this a result is not the thing that was asked for. A title with no
#word in common is 0; a title matching every word is 1.
MIN_TITLE_MATCH = 0.34


#Roughly: does this text use letters a Latin query could ever match?
_LATIN = re.compile(r"[a-z0-9]")
#CJK, kana, hangul, cyrillic, arabic, hebrew, thai, devanagari
_NON_LATIN = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
    r"\u0400-\u04ff\u0590-\u05ff\u0600-\u06ff\u0900-\u097f"
    r"\u0e00-\u0e7f]")


def script_of(text: str) -> str:
    """'latin', 'other', or 'mixed'."""
    text = str(text or "").lower()
    latin = bool(_LATIN.search(text))
    other = bool(_NON_LATIN.search(text))
    if latin and other:
        return "mixed"
    if other:
        return "other"
    return "latin"


def comparable(a: str, b: str) -> bool:
    """
    Whether comparing two titles means anything.

    A song has one title on YouTube Music and another on YouTube - "Kaiju
    Girl" is the same track as "\u4e59\u5973\u602a\u7363", and comparing them
    letter by letter says only that they are written differently. Treating
    that as "wrong" throws away the right answer.
    """
    first, second = script_of(a), script_of(b)
    if "mixed" in (first, second):
        return True
    return first == second


def title_match(wanted: set, found: set, raw_wanted: str, raw_found: str) -> float:
    """
    How much of the asked-for title a result actually contains, 0 to 1.

    Word overlap first, then a character-level comparison as a floor - so a
    title that was misheard by a letter or two still counts. "Kaiju" against
    "Kaijuu" shares no word and almost every character.
    """
    if not wanted:
        return 1.0
    overlap = len(wanted & found) / len(wanted)
    if overlap >= 1.0:
        return 1.0

    from difflib import SequenceMatcher
    close = SequenceMatcher(None, raw_wanted, raw_found).ratio()
    # The best of the two, since either being high is a good sign on its own.
    return max(overlap, close if close > 0.6 else 0.0)


def score_result(result, query: str) -> tuple:
    """
    (score, comparable) for one result.

    `comparable` is False when the two titles are written in different
    scripts, which is not a low score - it is no score at all. The caller
    decides what to do with an unknown, and treating it as zero would reject
    every Japanese title somebody asked for in English.

    Separate from rank() because ordering and *accepting* are different
    questions: sorting always produces a first result, however wrong, and
    something has to decide that the first result is not an answer.
    """
    title_part, artist_part = split_request(query)
    wanted_title = _words(title_part)
    wanted_artist = _words(artist_part)

    found_title = _words(result.title)
    found_artist = _words(result.artist)

    artist = 0.0
    if wanted_artist:
        artist = max(
            len(wanted_artist & found_artist) / len(wanted_artist),
            len(wanted_artist & found_title) / len(wanted_artist),
        )

    if not comparable(title_part, result.title):
        # Nothing can be said about the title. The artist is all there is.
        return artist, False

    title = title_match(wanted_title, found_title | found_artist,
                        title_part.lower(), str(result.title or "").lower())
    if not wanted_artist:
        return title, True

    # The title carries it. An artist heard wrongly is common - a title heard
    # wrongly enough to share nothing is not the same song.
    return title * 0.75 + artist * 0.25, True


def rank(results: list, query: str) -> list:
    """
    Put the closest match first.

    Search engines answer the words, not the request: "the bear by casey lee
    williams" returns anything with a bear in it. The phrase already says what
    matters - a title, and after "by", who by - so the results are re-ordered
    against both rather than the first one being taken on trust.
    """
    if not results:
        return results

    title_part, artist_part = split_request(query)
    wanted_title = _words(title_part)
    wanted_artist = _words(artist_part)

    def score(index_and_result) -> tuple:
        index, result = index_and_result
        title = _words(result.title)
        artist = _words(result.artist)

        points = 0.0
        if wanted_title:
            # How much of the asked-for title this result actually contains.
            points += 2.0 * len(wanted_title & title) / len(wanted_title)
        if wanted_artist:
            # The channel first. Somebody naming an artist wants that artist's
            # upload, not a cover with their name in the title.
            on_channel = len(wanted_artist & artist) / len(wanted_artist)
            in_title = len(wanted_artist & title) / len(wanted_artist)
            points += 4.0 * on_channel + 1.5 * in_title
            # An exact channel match settles it. "Ok Goodnight" the channel
            # beats "Ok Goodnight cover by someone else" every time.
            if on_channel >= 1.0:
                points += 1.5
        # Ties keep the order the search returned, which is its own ranking
        # and better than nothing.
        return (-points, index)

    return [result for _, result in
            sorted(enumerate(results), key=score)]


def _text(node) -> str:
    """YouTube writes text as {runs:[{text}]} or {simpleText}."""
    if not isinstance(node, dict):
        return ""
    if node.get("simpleText"):
        return _unescape(node["simpleText"])
    runs = node.get("runs")
    if isinstance(runs, list):
        return _unescape("".join(run.get("text", "") for run in runs
                                 if isinstance(run, dict)))
    return ""


def split_request(phrase: str) -> tuple:
    """
    ("the bear", "okay goodnight") out of "the bear by okay goodnight".

    The last "by" wins, since a title may contain one - "Death by Glamour by
    Toby Fox" is a title and an artist, not two artists.
    """
    text = " ".join(str(phrase or "").split())
    lowered = text.lower()
    at = lowered.rfind(" by ")
    if at == -1:
        return text, ""
    return text[:at].strip(), text[at + 4:].strip()


def build_query(phrase: str) -> str:
    """
    What to actually ask for.

    The word "by" is dropped: a search engine treats it as a term to match,
    and it appears in a great many unrelated titles. Both halves are kept,
    because the artist is the strongest signal there is - the title alone
    returns covers, live versions and anything sharing a common word.
    """
    title, artist = split_request(phrase)
    if not artist:
        return title
    return f"{title} {artist}".strip()


def usable(results: list, query: str, floor: float = MIN_TITLE_MATCH,
           log=None) -> list:
    """
    Only the results that are plausibly what was asked for.

    Ranking sorts; this rejects. Without it the best of a bad set is still
    played - "kaiju girl by metta nick" returning a short film about a
    corporate monster is a score of zero, and it played because zero was the
    highest score there was.
    """
    _title, wanted_artist = split_request(query)

    kept, unknown = [], []
    for index, result in enumerate(results or []):
        score, could_compare = score_result(result, query)

        if not could_compare:
            # A title in another script. Kept if the artist agrees, and
            # otherwise held back - the search engine put it here for a
            # reason, so the first one is worth trusting when there is
            # nothing else to go on.
            if wanted_artist and score >= 0.5:
                kept.append((score, result))
            else:
                unknown.append((index, result))
            continue

        if score >= floor:
            kept.append((score, result))
        elif log:
            log("debug", f"[Music] Rejected {result.title!r} "
                         f"(match {score:.2f} < {floor:.2f})")

    kept.sort(key=lambda pair: -pair[0])
    ordered = [result for _score, result in kept]

    if unknown and not ordered:
        # Nothing comparable matched, so fall back to what the search itself
        # ranked first. Only the top one: trusting the whole list would queue
        # up nine things nobody can vouch for.
        unknown.sort(key=lambda pair: pair[0])
        best = unknown[0][1]
        if log:
            log("info", f"[Music] {best.title!r} is written in another script "
                        f"- trusting the search's own ranking.")
        return [best]

    return ordered


def _unescape_js(blob: str) -> str:
    """
    Undo the backslash escaping in a JavaScript string literal.

    `unicode_escape` alone is wrong here: it decodes bytes as Latin-1, so a
    UTF-8 title comes back as one mangled character per byte - "\u4e59\u5973\u602a\u7363"
    arrives as gibberish, and the bullet separating a row's fields stops being
    a bullet, which breaks the parsing that depends on it.

    Unescaped, then put back through Latin-1 and read as the UTF-8 it always
    was.
    """
    text = blob.encode("utf-8", "surrogatepass").decode("unicode_escape")
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        # Already clean, or not UTF-8 underneath. Better than nothing.
        return text


def _walk_for(node, key: str, found: list, limit: int) -> None:
    """Every dict under `node` holding `key`, depth first."""
    if len(found) >= limit:
        return
    if isinstance(node, dict):
        if key in node and isinstance(node[key], dict):
            found.append(node[key])
        for value in node.values():
            _walk_for(value, key, found, limit)
    elif isinstance(node, list):
        for value in node:
            _walk_for(value, key, found, limit)


def search_music(query: str, limit: int = 10) -> list:
    """
    YouTube Music, which is where the translated titles are.

    Read out of the page's embedded JSON like the YouTube results page, but
    a different shape: a row is a `musicResponsiveListItemRenderer` whose
    columns hold the title, then the kind, artist, album and length.
    """
    params = urllib.parse.urlencode({"q": query})
    html = _fetch(f"{MUSIC}?{params}", headers={
        # Without this the page is a consent wall in much of the world, and
        # the wall has no results in it.
        "Cookie": "CONSENT=YES+cb; SOCS=CAI",
    })

    blobs = re.findall(r"initialData\.push\(\{path:.*?data:\s*'(.*?)'\}\)", html, re.S)
    payloads = []
    for blob in blobs:
        try:
            payloads.append(json.loads(_unescape_js(blob)))
        except (ValueError, UnicodeDecodeError):
            continue
    if not payloads:
        match = _INITIAL.search(html)
        if match:
            try:
                payloads.append(json.loads(match.group(1)))
            except ValueError:
                pass
    if not payloads:
        return []

    rows = []
    for payload in payloads:
        _walk_for(payload, "musicResponsiveListItemRenderer", rows, limit * 3)

    results, seen = [], set()
    for row in rows:
        video_id = (((row.get("playlistItemData") or {}).get("videoId"))
                    or _music_video_id(row))
        if not video_id or video_id in seen:
            continue

        columns = []
        for column in row.get("flexColumns") or []:
            renderer = column.get(
                "musicResponsiveListItemFlexColumnRenderer") or {}
            columns.append(_text(renderer.get("text")))
        if not columns or not columns[0]:
            continue

        seen.add(video_id)
        results.append(Result(video_id,
                              title=columns[0],
                              artist=_music_artist(columns)))
        if len(results) >= limit:
            break
    return results


def _music_video_id(row: dict) -> str:
    """The id off whichever nested endpoint carries it."""
    found = []
    _walk_for(row, "watchEndpoint", found, 3)
    for endpoint in found:
        if endpoint.get("videoId"):
            return str(endpoint["videoId"])
    return ""


def _music_artist(columns: list) -> str:
    """
    The artist out of "Song \u2022 Artist \u2022 Album \u2022 3:41".

    The second column is a list of facts separated by bullets, and which
    position the artist sits in depends on the row - so the kind and anything
    that looks like a duration are dropped and the first of what is left is
    taken.
    """
    if len(columns) < 2:
        return ""
    # Split on a bullet however it arrives - and on a vertical bar, which some
    # rows use instead.
    parts = [p.strip() for p in
             re.split(r"\s*(?:[\u2022\u00b7\u30fb|]|\u00e2\u0080\u00a2)\s*", columns[1])
             if p.strip()]

    kinds = {"song", "video", "album", "single", "ep", "playlist", "artist",
             "podcast", "episode"}
    cleaned = []
    for part in parts:
        # A leading kind word with no separator after it: "Song METANICK".
        # Stripped as well as filtered, so a missing bullet does not leave the
        # word glued to the artist's name.
        for kind in kinds:
            if part.lower().startswith(kind + " "):
                part = part[len(kind) + 1:].strip()
                break
        if not part or part.lower() in kinds:
            continue
        if re.fullmatch(r"[\d:]+", part):
            continue
        # "1.2M views", "3.4K plays" - a count, not a name.
        if re.fullmatch(r"[\d.,]+[KMB]?\s+\w+", part):
            continue
        cleaned.append(part)
    return cleaned[0] if cleaned else ""


def search(query: str, key: str = "", limit: int = 10, log=None) -> list:
    """
    Whatever works. Returns [] rather than raising.

    The key is tried first when there is one, and a failure falls through to
    scraping rather than leaving somebody with silence - an exhausted quota
    should not mean the music stops working for the rest of the day.
    """
    query = str(query or "").strip()
    if not query:
        return []

    # Asked for without the "by", ranked against both halves of what was said.
    asked = build_query(query)
    if log and asked != query:
        log("debug", f"[Music] Searching for {asked!r}")

    if key:
        # Restricted first, then plain. A refusal is usually one of the
        # optional filters rather than the key, and giving up on the API
        # entirely would drop to scraping for the rest of the session.
        for restrict in (True, False):
            try:
                results = search_api(asked, key, limit, restrict=restrict)
                if results:
                    good = usable(results, query, log=log)
                    if good:
                        return good
                    if log:
                        log("debug", "[Music] Nothing the API returned matched "
                                     "closely enough.")
                if log:
                    log("debug", f"[Music] The Data API returned nothing"
                                 f"{' with filters' if restrict else ''}.")
            except Exception as e:
                if log:
                    log("warning", f"[Music] Data API search failed ({e})"
                                   f"{' - retrying unfiltered' if restrict else ''}.")

    # YouTube first, then YouTube Music.
    #
    # Both, rather than one: a track is often filed under a translated or
    # romanised title on Music and only its original title on YouTube, so a
    # search for the English name finds nothing on one and everything on the
    # other. Whichever produces something plausible wins.
    for name, finder in (("YouTube", search_scrape),
                         ("YouTube Music", search_music)):
        try:
            results = finder(asked, limit)
        except Exception as e:
            if log:
                log("warning", f"[Music] {name} search failed: {e}")
            continue

        good = usable(results, query, log=log)
        if good:
            if log and name != "YouTube":
                log("info", f"[Music] Found on {name}: {good[0].title!r}")
            return good
        if log and results:
            log("debug", f"[Music] {name} returned {len(results)} results, "
                         f"none matching.")

    return []
