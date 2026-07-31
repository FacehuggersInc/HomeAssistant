# RSS Feeds API and Idle Panels

- REQUIRES: "IdleRandomTriggers" Plugin (bundled)
- Add a feed two ways: call `add_rss_feed(plugin_key, url, transformer)`, or drop a `{"url":..., "transformer":...}` JSON file into `RSSFeeds/` in the app's working directory (auto-loaded under the `rssfeeds` key).
- `transformer` maps feedparser's data into `{"title": ..., "items": [{"id", "title", "published", "summary", "author"}]}`. Use `"COMPACT"` paths to map a sub-transformer over a list (e.g. `entries` → `items`).
- `transformer` is optional — if omitted, one is auto-inferred from the feed's own data the first time it's used (checking which of the expected keys are actually present) and cached for reuse.
- `id`, `title`, `summary` are expected per item; `published`/`author`/feed `title` are optional and just get omitted from the panel if missing.

## What a panel shows

A picture, a headline, a row of tags, and the article.

**The picture strip** runs edge to edge across the top — no margin, no
rounding, cropped to fill rather than fitted inside. A feed image letterboxed
in a padded box looks like a placeholder; one that runs to both edges looks
like the article. The panel itself has no margins for that reason, and the
text below sits in its own padded column.

If an entry has **more than one** image they cycle every few seconds. The timer
is parented to the label so it dies with the panel — a transient panel that
left one running would keep swapping pictures nobody can see.

Images are found for you. Feeds hide images in four different places -
`media_thumbnail`, `media_content`, an `<enclosure>`, or simply an `<img>` in
the description - and `extract_image()` tries all of them, widest first,
because a feed that bothers to declare a thumbnail has chosen a better one than
whatever happens to be first in its body. Enclosures are filtered by MIME type:
a podcast's `audio/mpeg` is not a picture.

It is downloaded on a worker thread and the label stays hidden until the bytes
arrive, so a feed without an image - or a slow one - leaves no gap.

**The article** is rendered as HTML when it looks like HTML, which most feeds
send. Reading HTML as markdown prints the tags out as text, so the two are
told apart rather than assumed.

Feeds are written for a white page in a browser, so their own `style`,
`bgcolor`, `width` and `color` attributes are stripped along with any `script`
or `style` blocks, and the `<img>` is removed because the panel is already
showing it above the headline.

**A countdown bar** across the very top shows how long until the next
article, driven by the rotation time the builder is handed. It is timed on the
monotonic clock rather than the wall clock, so a panel whose time changes
underneath it does not jump.

**Nothing stretches except the article.** A `QLabel` defaults to a Preferred
size policy on both axes, so in a column with spare height a layout will
stretch tag pills into tall coloured slabs and sit the headline in the middle
of a large empty box. The tags are fixed height, the headline is as tall as its
wrapped text, and the body takes everything left over.

**Dates are readable.** Feeds send RFC-822 - "Tue, 18 Feb 2020 10:29:00 -0800"
as a pill on a wall panel is a wall of punctuation nobody reads, so it becomes
"18 Feb 2020". Anything unparseable is shown as it came rather than dropped.

**Links** are drawn in the theme accent rather than Qt's default, which is the
same saturated blue on every theme and near invisible on a dark panel.

**Sizing.** The panel is 40% of the screen, floored at 460px and capped at
780px - a column of text 1500px wide is no more readable than one 200px wide.
The strip is a fixed 260px so the layout below does not jump as pictures of
different shapes arrive, and the body takes the spare height so the text fills
the panel instead of huddling under the title.

## The order things appear in

**One item from each feed in turn**, not everything from one feed and then
everything from the next - with a Steam deals feed and a news feed, the latter
would mean twenty game deals before a single headline.

Nothing repeats until **every item from every feed** has been shown once, at
which point it starts round again. A feed that runs out is skipped rather than
stalling the rotation, and item keys are namespaced per feed - two feeds can
both number their entries from one, and an id alone would let one suppress the
other.

Parsed items are cached for fifteen minutes. This is consulted once per panel
now rather than once per feed exhaustion, and refetching every rotation would
hit the network every minute for as long as the panel sat idle. A feed that
fails is cached as empty for the same period rather than retried constantly.

## Pacing

Two settings on **IdleRandomTriggers**, since they govern every idle builder
and not only this one:

| Setting | Default | Meaning |
|---|---|---|
| `sprint_items` | 4 | How many panels in a row before pausing. `0` never pauses. |
| `sprint_break` | 300000ms | How long the screen is left alone between runs. |

Rotating forever meant the panel never settled, which is the one thing an idle
screen is supposed to do. A break dismisses whatever is up and stops rotating;
when it ends, the rotation only picks up **if the panel is still idle** -
somebody walking past during a break stops it, and it should stay stopped. An
interaction cancels the break outright.

## When a feed shows nothing

`feedparser` reports a failed fetch in its return value rather than by raising,
so a blocked or missing feed looks exactly like an empty one. Every fetch is
checked and the reason is logged:

```
[RSSFeedsPlugin] 'https://www.reddit.com/r/anime/top/.rss?t=day' is rate limiting us (429).
```

**A full browser header set** is sent on every request, not just a
User-Agent. Hosts behind a bot filter look at the whole request: a browser
sends an Accept list, a language, an encoding and the `Sec-Fetch-*` set, and a
request carrying only a User-Agent is an obvious tool however that agent is
spelled.

`A-IM: feed` is removed as well. `feedparser` adds it for RFC 3229 delta
encoding and no browser has ever sent it, so it identifies the request however
carefully the rest is set. It is added after the caller's own headers, so a
request handler removes it rather than a header overriding it.

If a host still wants something specific, a feed can name it:

```json
{
  "url": "https://example.com/feed.xml",
  "headers": { "Cookie": "session=..." }
}
```

Those are merged over the defaults, so a feed only has to name what it needs.

A feed is fetched **once** per load. Inferring a transformer and then applying
it with a second `parse()` would be two requests back to back for every feed,
which is enough on its own to trip a rate limiter - so `transform_data()`
applies one to data already in hand.

A feed that answers badly is left alone: fifteen minutes for an ordinary
failure, **an hour** after a 429, since a rate limiter needs considerably
longer than a missing file does.

If Reddit keeps answering 429, it is throttling the panel's address rather than
rejecting the request. Waiting is the only fix; the hour-long back-off is
there so the panel is not making it worse.

## Reddit feeds

Any subreddit works by appending `/.rss`:

```json
{ "url": "https://www.reddit.com/r/anime/top/.rss?t=day" }
```

They parse cleanly - every field is inferred - but what comes out needs
tidying, and that happens automatically for anything on `reddit.com` or
`redd.it`:

* **Authors lose the `/u/`.** "/u/Turbostrider27" is noise in a tag.
* **The feed is renamed.** "top scoring links : anime" reads like a database
  column; it becomes **r/anime**.
* **Reddit's furniture is stripped** - "submitted by", `[link]`, `[comments]`,
  the `SC_OFF`/`SC_ON` markers, and the layout table that exists only to put
  the thumbnail beside the text. The table is *unwrapped* rather than deleted,
  or a self post's body would go with it.
* **Thumbnails are asked for at a usable size.** Reddit serves `?width=140`
  by default, which stretched across a 780px panel is a blurry mess. The width
  is a query parameter it honours and the signature covers the path rather
  than the size, so it is raised to 960 - never lowered, so a feed already
  offering more keeps it. The paired `height=` is dropped, since a stale one
  would letterbox.

A **link post has no body of its own** once that is done: its entire content
was the thumbnail and the furniture. Rather than an empty panel it says where
the post points - "Links to youtube.com" - which is the useful part.

None of this touches a feed from anywhere else.

## Adding a feed

**From a phone:** `/public/rss_feeds`, listed on the index as **Feeds**. A name
and an address. The name is only used for the filename; the feed's own title is
what appears on the panel.

**By hand:** a JSON file in the `RSSFeeds` folder next to the application.

```json
{ "url": "http://store.steampowered.com/feeds/daily_deals.xml" }
```

The `url` key is the whole format. `transformer` is accepted but optional -
without one the shape is inferred from the first few entries and remembered,
and an inferred one does not belong in a file somebody typed by hand.

The folder is registered as an uploadable asset, so the files are reachable the
same way stickers are. Adding a feed puts it into the rotation immediately;
removing one takes it out rather than leaving it until the next restart.

Names typed into the form are sanitised before they become filenames - this
arrives over the network, and `../../etc/cron.d/x` is a perfectly valid thing
for somebody to type into a text box.
