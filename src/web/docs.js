document.querySelectorAll('.copy').forEach(function (button) {
  button.addEventListener('click', function () {
    var code = button.closest('.code').querySelector('code');
    // Fall back to a hidden textarea: navigator.clipboard is unavailable over
    // plain http on anything but localhost, and this is served over http.
    var done = function () {
      button.textContent = 'copied';
      button.classList.add('done');
      setTimeout(function () {
        button.textContent = 'copy';
        button.classList.remove('done');
      }, 1400);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(code.innerText).then(done);
      return;
    }
    var scratch = document.createElement('textarea');
    scratch.value = code.innerText;
    scratch.style.position = 'fixed';
    scratch.style.opacity = '0';
    document.body.appendChild(scratch);
    scratch.select();
    try { document.execCommand('copy'); done(); } catch (e) { /* nothing to do */ }
    document.body.removeChild(scratch);
  });
});

var filter = document.querySelector('.filter');
var results = document.querySelector('.results');
var nav = document.querySelector('.nav');
var indexNode = document.getElementById('search-index');
var index = indexNode ? JSON.parse(indexNode.textContent) : [];

if (filter) {
  filter.addEventListener('input', function () {
    var needle = filter.value.trim().toLowerCase();

    if (!needle) {
      nav.hidden = false;
      results.hidden = true;
      document.querySelectorAll('.nav a').forEach(function (link) {
        link.style.display = '';
      });
      return;
    }

    // Page titles first: an exact page is almost always what was wanted, and
    // burying it under twenty section matches would be worse than no search.
    var pageHits = 0;
    document.querySelectorAll('.nav a').forEach(function (link) {
      var hit = link.textContent.toLowerCase().indexOf(needle) !== -1;
      link.style.display = hit ? '' : 'none';
      if (hit) { pageHits++; }
    });
    nav.hidden = pageHits === 0;

    // A heading match outranks a body match. Somebody typing "threading"
    // wants the section called that, not the twelve paragraphs mentioning it.
    var headingRows = [], bodyRows = [];
    index.forEach(function (row) {
      if (row[2].toLowerCase().indexOf(needle) !== -1) {
        headingRows.push(row);
      } else if ((row[4] || '').toLowerCase().indexOf(needle) !== -1) {
        bodyRows.push(row);
      }
    });
    var rows = headingRows.concat(bodyRows).slice(0, 50);

    if (!rows.length) {
      results.hidden = pageHits > 0;
      results.innerHTML = pageHits > 0 ? '' : '<div class="empty">Nothing found.</div>';
      return;
    }

    function escapeHtml(text) {
      return text.replace(/[&<>"]/g, function (c) {
        return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c];
      });
    }

    // The words around the match, so a result can be judged without opening
    // it. A list of section names is not enough once the search reaches into
    // the body - the heading may say nothing about why it matched.
    function snippet(text, term) {
      var at = text.toLowerCase().indexOf(term);
      if (at === -1) { return ''; }
      var from = Math.max(0, at - 48);
      var piece = text.slice(from, at + term.length + 90);
      var out = escapeHtml(piece);
      var marked = escapeHtml(text.substr(at, term.length));
      out = out.replace(marked, '<mark>' + marked + '</mark>');
      return (from > 0 ? '&hellip;' : '') + out + '&hellip;';
    }

    var html = '<div class="results-title">' + rows.length +
               ' match' + (rows.length === 1 ? '' : 'es') + '</div>';
    rows.forEach(function (row) {
      var href = '/docs/' + row[0] + (row[3] ? '#' + row[3] : '');
      var body = snippet(row[4] || '', needle);
      html += '<a href="' + href + '"><span>' + escapeHtml(row[2]) +
              '</span><em>' + escapeHtml(row[1]) + '</em>' +
              (body ? '<small>' + body + '</small>' : '') + '</a>';
    });
    results.innerHTML = html;
    results.hidden = false;
  });

  // Escape clears, so the sidebar can be got back without reaching for the
  // mouse or selecting the text.
  filter.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      filter.value = '';
      filter.dispatchEvent(new Event('input'));
    }
  });
}

var menu = document.querySelector('.menu');
if (menu) {
  menu.addEventListener('click', function () {
    document.querySelector('.sidebar').classList.toggle('open');
  });
}
