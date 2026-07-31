"""
Starting a timer from a phone.

A page with a form rather than an index button labelled "Start a 5 minute
timer": that button fired the endpoint with no arguments at all, so the
duration never arrived and the label was a promise nothing kept.
"""

from __future__ import annotations

from src.webui import escape, page, position_grid, POSITION_SCRIPT
# Absolute, like src.webui above.
#
# `from ..timers import clock` needs this module to have a package, and it does
# not when it is loaded by path - which is how a plugin loads its own pages.
# The relative form worked while the import machinery happened to line up and
# failed from inside render_page() when it did not, which is a worse place to
# find out.
from src.assets.bundled.CoreWidgetsBundle.timers import clock

PRESETS = [1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90]


CSS = """
 .presets{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;
      margin-top:6px}
 .presets button{min-height:48px;padding:0;font-size:15px}
 .presets button.on{border-color:var(--accent);color:var(--accent);
      background:linear-gradient(150deg,rgba(47,240,142,.14),var(--card) 70%)}
 ul{list-style:none;padding:0;margin:0}
 li{display:flex;justify-content:space-between;gap:12px;padding:11px 0;
    border-bottom:1px solid var(--line);font-size:14.5px}
 li:last-child{border-bottom:0}
 li span{color:var(--muted)}
 .go{margin-top:18px}
 .go button{width:100%}
"""


SCRIPT = """
/* A preset fills the fields rather than submitting, so it can be adjusted
   before it starts - which is the whole reason this is a page and not a
   button that fired a fixed five minutes. */
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
""" + POSITION_SCRIPT


def render_page(token: str, running: list, message: str = "", bad: bool = False,
                form: dict = None) -> str:
    form = form or {}
    presets = "".join(
        f'<button type="button" data-min="{m}">{m}m</button>' for m in PRESETS)

    if running:
        listed = "".join(
            f"<li><b>{escape(t.name or 'Timer')}</b>"
            f"<span>{escape(clock(t.remaining()))} left</span></li>"
            for t in running)
    else:
        listed = '<li class="empty">Nothing running.</li>'

    # Blank is a real answer here and is not one of the nine: a timer with
    # nowhere named goes wherever there is room.
    where = str(form.get("quadrant") or "")

    body = f"""
<section>
  <form method="post" action="/public/timer_form?token={escape(token)}">
    <label>How long?</label>
    <div class="presets" id="presets">{presets}</div>

    <div class="row">
      <div>
        <label for="hours">Hours</label>
        <input id="hours" name="hours" type="number" min="0" max="23"
               value="{escape(form.get('hours') or '0')}">
      </div>
      <div>
        <label for="minutes">Minutes</label>
        <input id="minutes" name="minutes" type="number" min="0" max="59"
               value="{escape(form.get('minutes') or '5')}">
      </div>
      <div>
        <label for="seconds">Seconds</label>
        <input id="seconds" name="seconds" type="number" min="0" max="59"
               value="{escape(form.get('seconds') or '0')}">
      </div>
    </div>

    <label for="name">Call it something (optional)</label>
    <input id="name" name="name" placeholder="Eggs"
           value="{escape(form.get('name') or '')}">

    <label>Where should it sit?</label>
    {position_grid(where)}
    <p class="hint">Leave this alone and it goes wherever there is room.</p>

    <div class="go"><button type="submit">Start it</button></div>
  </form>
</section>

<section>
  <h2>Running now</h2>
  <ul>{listed}</ul>
</section>
"""

    return page(
        title="Start a timer",
        heading="Start a timer",
        blurb="It appears on the panel straight away.",
        token=token, message=message, bad=bad,
        css=CSS, body=body, script=SCRIPT,
    )
