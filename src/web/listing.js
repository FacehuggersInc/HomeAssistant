/*
  Finding one thing in a long list, on a phone.

  A folder of stickers or uploads grows until the page is a scroll with no
  end and no order to it. Three things fix that and they are the same three
  wherever a page shows many items, so they live here rather than in each
  page: filter, sort, and drawing only as much as is worth drawing.

  Used by the sticker page and the upload page. Neither owns it, so neither
  can drift from the other.

  Nothing here touches the DOM. A page hands over its items and gets back
  the ones to draw, because what a tile looks like is the page's business
  and what order it is in is not.
*/

window.Listing = (function () {

  /* Everything the filter should look at, lowercased once. A page says how
     to read a name out of its own item; the shape of an item is its own. */
  function haystack(item, fields) {
    return (fields || ['name', 'label'])
      .map(function (key) { return String(item[key] === undefined ? ''
                                          : item[key]); })
      .join(' ')
      .toLowerCase();
  }

  /* Every word has to appear somewhere, in any order. Typing "cat png" then
     finds the same thing as "png cat", which is how somebody types when
     they half-remember a filename. */
  function matches(item, terms, fields) {
    if (!terms.length) { return true; }
    var text = haystack(item, fields);
    return terms.every(function (term) { return text.indexOf(term) !== -1; });
  }

  function terms(query) {
    return String(query || '').toLowerCase().split(/\s+/)
      .filter(function (t) { return t.length; });
  }

  var SORTS = {
    name: function (a, b) {
      return String(a.label || a.name || '')
        .localeCompare(String(b.label || b.name || ''),
                       undefined, {numeric: true, sensitivity: 'base'});
    },
    newest: function (a, b) { return (b.modified || 0) - (a.modified || 0); },
    oldest: function (a, b) { return (a.modified || 0) - (b.modified || 0); },
    largest: function (a, b) { return (b.size_bytes || 0) - (a.size_bytes || 0); }
  };

  /*
    What to draw, and what to say about the rest.

    `window` is a cap on how many are DRAWN, not on how many are searched.
    A thousand tiles is a page a phone struggles to scroll and a filter that
    finds one of them instantly - so the filter runs over everything and the
    cap only decides how much of the answer is on screen.

    `from` is where that window starts, and it is what makes growing cheap:
    a page that has already drawn sixty asks for the next sixty and appends
    them. Without it the only way to show more is to rebuild every tile that
    is already on screen, which is the work that makes growing feel slow -
    and it gets slower the further down you are.

    `shown` is the slice to draw. `drawn` is how many come before it, so a
    page can tell whether it is starting or continuing.
  */
  function view(items, options) {
    var opts = options || {};
    var wanted = terms(opts.query);
    var fields = opts.fields;
    var from = Math.max(0, opts.from || 0);
    var cap = opts.window || 0;

    var found = (items || []).filter(function (item) {
      return matches(item, wanted, fields);
    });

    var order = SORTS[opts.sort] || SORTS.name;
    found = found.slice().sort(order);

    var until = cap > 0 ? from + cap : found.length;
    var shown = found.slice(from, until);
    return {
      shown: shown,
      drawn: from,
      found: found.length,
      total: (items || []).length,
      hidden: Math.max(0, found.length - (from + shown.length)),
      filtered: wanted.length > 0
    };
  }

  /*
    Draw more when the bottom comes into view.

    An observer rather than a scroll handler: a scroll handler runs on every
    pixel and has to work out where it is, and this runs once when a marker
    somebody is scrolling towards appears. The marker sits after the last
    tile, so it is reached before the end rather than at it.

    Returns a function that stops watching, for a page redrawing from the
    top. A browser without IntersectionObserver gets nothing back and the
    page falls back to its button.
  */
  function whenNear(marker, grow) {
    if (!marker || typeof IntersectionObserver === 'undefined') { return null; }
    var watcher = new IntersectionObserver(function (entries) {
      if (entries.some(function (e) { return e.isIntersecting; })) { grow(); }
    }, {rootMargin: '400px'});
    watcher.observe(marker);
    return function () { watcher.disconnect(); };
  }

  /* The line under the controls. It has to answer "is it not here, or am I
     just not seeing it" without being read twice. */
  function summary(result, noun) {
    var word = noun || 'item';
    var plural = word + 's';
    if (!result.total) { return ''; }
    if (result.filtered && !result.found) {
      return 'Nothing matches. ' + result.total + ' ' + plural + ' in all.';
    }
    if (result.hidden) {
      return 'Showing ' + (result.drawn + result.shown.length) + ' of ' +
             result.found +
             (result.filtered ? ' matching' : '') + ' \u00b7 ' +
             result.hidden + ' more';
    }
    if (result.filtered) {
      return result.found + ' of ' + result.total + ' ' + plural;
    }
    return result.total + ' ' + (result.total === 1 ? word : plural);
  }

  return {view: view, summary: summary, whenNear: whenNear,
          sorts: Object.keys(SORTS)};
})();
