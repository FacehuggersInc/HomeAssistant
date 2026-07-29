"""
Starting a timer from a phone.

Was an index button labelled "Start a 5 minute timer" that fired the endpoint
with no arguments at all - so the duration was never sent, the endpoint
refused, and the label was a promise nothing kept. A page with a form is what
that button should have been.
"""

from __future__ import annotations

from src.webui import escape, back_button, chrome_css

QUADRANTS = [
    ("", "Wherever there is room"),
    ("top-left", "Top left"), ("top", "Top"), ("top-right", "Top right"),
    ("left", "Left"), ("center", "Middle"), ("right", "Right"),
    ("bottom-left", "Bottom left"), ("bottom", "Bottom"),
    ("bottom-right", "Bottom right"),
]

PRESETS = [1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90]


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Start a timer</title>
<style>
__CHROME__
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--text);
      font:16px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;padding:18px}
 h1{font-size:22px;margin:0 0 4px}
 p.sub{color:var(--muted);margin:0 0 18px;font-size:14px}
 section{background:var(--card);border:1px solid var(--line);
      border-radius:14px;padding:16px;margin-bottom:16px}
 label{display:block;font-size:13px;color:var(--muted);margin:12px 0 4px}
 button{width:100%;margin-top:18px;padding:15px;border:0;border-radius:10px;
      background:var(--accent);color:#10281c;font-size:17px;font-weight:600}
 .presets{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:6px}
 .presets button{margin:0;padding:14px 4px;font-size:15px;background:#26262b;
      color:var(--text);border:1px solid var(--line)}
 .presets button.on{background:var(--accent);color:#10281c;
      border-color:var(--accent)}
 .row{display:flex;gap:10px}.row>div{flex:1}
 .note{background:rgba(47,240,142,.14);border:1px solid rgba(47,240,142,.5);
     border-radius:10px;padding:12px;margin-bottom:14px}
 .warn{background:rgba(224,138,138,.14);border:1px solid rgba(224,138,138,.5);
     border-radius:10px;padding:12px;margin-bottom:14px;color:var(--bad)}
 ul{list-style:none;padding:0;margin:0}
 li{display:flex;justify-content:space-between;gap:12px;padding:10px 0;
    border-bottom:1px solid var(--line);font-size:14px}
 li span{color:var(--muted)}
 .empty{color:var(--muted);font-size:14px}
</style></head><body>
__BACK__
<h1>Start a timer</h1>
<p class="sub">It appears on the panel straight away.</p>
__MESSAGE__

<section>
  <form method="post" action="/public/timer_form?token=__TOKEN__">
    <label>How long?</label>
    <div class="presets" id="presets">__PRESETS__</div>

    <div class="row">
      <div>
        <label for="hours">Hours</label>
        <input id="hours" name="hours" type="number" min="0" max="23"
               value="__HOURS__">
      </div>
      <div>
        <label for="minutes">Minutes</label>
        <input id="minutes" name="minutes" type="number" min="0" max="59"
               value="__MINUTES__">
      </div>
      <div>
        <label for="seconds">Seconds</label>
        <input id="seconds" name="seconds" type="number" min="0" max="59"
               value="__SECONDS__">
      </div>
    </div>

    <label for="name">Call it something (optional)</label>
    <input id="name" name="name" placeholder="Eggs" value="__NAME__">

    <label for="quadrant">Where should it sit?</label>
    <select id="quadrant" name="quadrant">__QUADS__</select>

    <button type="submit">Start it</button>
  </form>
</section>

<section>
  <h1 style="font-size:16px;margin:0 0 8px">Running now</h1>
  <ul>__RUNNING__</ul>
</section>

<script>
 // A preset fills the fields rather than submitting, so it can be adjusted
 // before it starts - which is the whole reason this is a page and not a
 // button that fired a fixed five minutes.
 document.querySelectorAll('#presets button').forEach(function (b) {
   b.addEventListener('click', function (e) {
     e.preventDefault();
     document.querySelectorAll('#presets button').forEach(function (o) {
       o.classList.remove('on');
     });
     b.classList.add('on');
     var m = parseInt(b.dataset.min, 10);
     document.getElementById('hours').value = Math.floor(m / 60);
     document.getElementById('minutes').value = m % 60;
     document.getElementById('seconds').value = 0;
   });
 });
</script>
</body></html>"""


def render_page(token: str, running: list, message: str = "", bad: bool = False,
                form: dict = None) -> str:
    # Imported here rather than at module scope: the page is also rendered by
    # tests that load it on its own, where a package-relative import has no
    # package to be relative to.
    try:
        from ..timers import clock
    except ImportError:
        from timers import clock

    form = form or {}
    presets = "".join(
        f'<button data-min="{m}">{m}m</button>' for m in PRESETS)

    quads = "".join(
        f'<option value="{escape(key)}"'
        f'{" selected" if key == str(form.get("quadrant") or "") else ""}>'
        f'{escape(label)}</option>'
        for key, label in QUADRANTS)

    if running:
        listed = "".join(
            f"<li><b>{escape(t.name or 'Timer')}</b>"
            f"<span>{escape(clock(t.remaining()))} left</span></li>" for t in running)
    else:
        listed = '<li class="empty">Nothing running.</li>'

    if message:
        block = (f'<div class="{"warn" if bad else "note"}">'
                 f'{escape(message)}</div>')
    else:
        block = ""

    return (PAGE
            .replace("__CHROME__", chrome_css())
            .replace("__BACK__", back_button(token))
            .replace("__TOKEN__", escape(token))
            .replace("__MESSAGE__", block)
            .replace("__PRESETS__", presets)
            .replace("__QUADS__", quads)
            .replace("__RUNNING__", listed)
            .replace("__HOURS__", escape(form.get("hours") or "0"))
            .replace("__MINUTES__", escape(form.get("minutes") or "5"))
            .replace("__SECONDS__", escape(form.get("seconds") or "0"))
            .replace("__NAME__", escape(form.get("name") or "")))
