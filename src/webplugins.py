"""
The Plugins section of the dashboard: what is installed, how to start one, and
how to send a new version.

Rendering only. Every decision - may this device do this, is this zip safe,
what would it overwrite - belongs to `src.plugin.*` and to the routes; this
turns the answers into pages.

The three are siblings rather than a wizard, so they share a sticky sub-nav
and nothing else. Somebody arriving to update a plugin should not have to walk
past a form for creating one.
"""

from __future__ import annotations

from html import escape

from src.webicons import svg
from src.webui import core_assets, page, subnav

NAV = (
    ("/plugins", "Installed", "playlist-check"),
    ("/plugins/new", "Create", "folder-plus"),
    ("/plugins/upload", "Upload", "upload"),
)

def _sheet() -> tuple:
    """
    The plugin manager's styling, as `(inline, head_tag)`.

    A file in `src/web/` rather than a constant here - see docs/web-ui.md.
    Under the inline limit it is written into the page; over it, it is served
    from the asset route and cached, and the link goes in the head rather
    than the body so it is read before the page is laid out.
    """
    return core_assets().inline_or_link("plugins.css")



def _nav(current: str, token: str) -> str:
    return subnav(NAV, current=current, token=token)


## -- installed ----------------------------------------------------------------

def installed_page(entries: list, token: str, message: str = "",
                   bad: bool = False, may_control: bool = True) -> str:
    """
    Every plugin the panel knows about, and what can be done to it.

    Bundled plugins are shown and are not controllable from here: they are
    part of the app, they update with it, and offering an unload button for
    one is offering to break the panel from a phone.
    """
    if not entries:
        body = '<section class="empty">No plugins found.</section>'
    else:
        cards = []
        for entry in entries:
            loaded = entry.get("loaded")
            bundled = entry.get("bundled")
            blocked = entry.get("blocked")
            pills = []
            if blocked:
                pills.append('<span class="pill bad">Conflict</span>')
            else:
                pills.append('<span class="pill on">Running</span>' if loaded
                             else '<span class="pill off">Stopped</span>')
            if bundled:
                pills.append('<span class="pill bundled">Bundled</span>')
            if entry.get("version"):
                pills.append(f'<span class="pill">v'
                             f'{escape(str(entry["version"]))}</span>')

            acts = ""
            # No controls on a conflict. There is nothing to press: loading
            # will not work and installing packages will not help. The fix is
            # in the plugin's own toml, which is not on this page.
            if may_control and not bundled and not blocked:
                buttons = []
                if loaded:
                    buttons.append(("reload", "Reload", "refresh", False))
                    buttons.append(("unload", "Unload", "stop", True))
                else:
                    buttons.append(("load", "Load", "play", False))
                rows = "".join(
                    f'<button type="button" class="{"danger" if danger else ""}" '
                    f'data-act="{act}" data-key="{escape(entry["key"])}">'
                    f'{svg(icon, 16)}<span>{label}</span></button>'
                    for act, label, icon, danger in buttons)
                acts = f'<div class="acts">{rows}</div>'

            # Download sits outside that block, because a bundled plugin has
            # nothing to load, unload or remove and is still worth reading -
            # it is the best worked example of how one is written, and the
            # alternative to downloading it is a keyboard on the panel.
            if may_control:
                # A link, not a fetch. A download is the browser's job and
                # trying to do it through the action handler would give a
                # zip in a JSON parser.
                link = (f'<a class="btn" href="/plugins/'
                        f'{escape(entry["key"])}/download?token={escape(token)}">'
                        f'{svg("download", 16)}<span>Download</span></a>')
                if acts:
                    acts = acts[:-len("</div>")] + link + "</div>"
                else:
                    acts = f'<div class="acts">{link}</div>' 

            why = entry.get("description") or ""
            if entry.get("dependants"):
                why = (f'{why} Required by '
                       f'{escape(", ".join(entry["dependants"]))}.').strip()

            cards.append(
                f'<div class="plug{" blocked" if blocked else ""}">'
                f'<span class="glyph">'
                f'{svg(entry.get("icon") or "puzzle", 20)}</span>'
                f'<span class="meta">'
                f'<span class="name">{escape(entry.get("name") or entry["key"])}'
                f'{"".join(pills)}</span>'
                f'<span class="key">{escape(entry["key"])}</span>'
                f'{f'<div class="why">{escape(why)}</div>' if why else ""}'
                f'{acts}</span></div>')
        body = "".join(cards)

    script = """
document.addEventListener('click', function (e) {
  var b = e.target.closest && e.target.closest('button[data-act]');
  if (!b) { return; }
  b.disabled = true;
  fetch('/plugins/' + encodeURIComponent(b.dataset.key) + '/' + b.dataset.act
        + '?token=' + encodeURIComponent(TOKEN), {method: 'GET'})
    .then(function (r) { return r.json(); })
    .then(function (d) {
      // Reloaded rather than patched in place: load and unload change what
      // every other button on the page should say, and a page that half
      // updates is a page showing two moments at once.
      // Reloaded either way.
      //
      // A failure here almost always means the plugin was loaded or
      // unloaded AT THE PANEL since this page was drawn - so the button
      // that just failed is describing a state that no longer exists, and
      // re-enabling it puts the wrong button back. The reason is carried
      // through the reload rather than shown in an alert over a stale list.
      var to = '/plugins?token=' + encodeURIComponent(TOKEN);
      if (d.request !== 'Success') {
        to += '&note=' + encodeURIComponent(d.reason || 'That did not work.');
      }
      location.href = to;
    })
    .catch(function () { b.disabled = false; });
});
"""
    return page(
        "Plugins", body, token=token, nav=_nav("/plugins", token),
        heading="Plugins", blurb="What this panel is running.",
        message=message, bad=bad, css=_sheet()[0], head=_sheet()[1],
        script=f"var TOKEN={token!r};" + script)


## -- create -------------------------------------------------------------------

def create_page(token: str, message: str = "", bad: bool = False,
                values: dict = None) -> str:
    """The form that writes a starting plugin and hands it back as a zip."""
    values = values or {}

    def field(name, default=""):
        return escape(str(values.get(name, default)))

    body = f"""
<section>
  <form method="post" action="/plugins/new?token={escape(token)}">
    <label for="name">Folder name</label>
    <input id="name" name="name" value="{field('name')}"
           placeholder="AnimePlugin" autocomplete="off" required>
    <p class="hint">The folder this lives in, under <code>plugins/</code>.
       Letters, numbers, dashes and underscores, starting with a letter.</p>

    <label for="key">Key</label>
    <input id="key" name="key" value="{field('key')}"
           placeholder="anime" autocomplete="off">
    <p class="hint">What everything else refers to it by - settings paths,
       dependencies, the public registry. Lowercase. Left blank, it is worked
       out from the folder name.</p>

    <label for="version">Version</label>
    <input id="version" name="version" value="{field('version', '0.1.0')}"
           placeholder="0.1.0" autocomplete="off">
    <p class="hint">Shown wherever this plugin is listed, and the only way to
       tell at a glance which of two zips is the newer one. Bump it when you
       upload a change.</p>

    <label for="description">Description</label>
    <input id="description" name="description" value="{field('description')}"
           placeholder="Anime episode counts and release dates"
           autocomplete="off">

    <label for="settings">Settings file</label>
    <input id="settings" name="settings" value="{field('settings')}"
           placeholder="settings.json" autocomplete="off">
    <p class="hint">Leave this blank for a plugin with no settings. If you
       name one it is created <em>and</em> written into
       <code>plugin.toml</code> - a settings file nothing points at is never
       read, and every option then falls back to its default with nothing to
       say why.</p>

    <button type="submit">{svg('download', 16)}<span>Create and download</span></button>
  </form>
</section>

<section>
  <h2>What you get</h2>
  <ul class="files">
    <li>main.py <span class="tag">a plugin that loads</span></li>
    <li>plugin.toml <span class="tag">name, key, order, version</span></li>
    <li>settings.json <span class="tag">only if you named one</span></li>
  </ul>
  <p class="hint">The zip holds the <em>contents</em> of the folder, not the
     folder - unpack it inside <code>plugins/YourPlugin/</code>. That is the
     same shape the Upload page accepts, so you can edit it and send it
     straight back.</p>
</section>
"""
    return page("Create a plugin", body, token=token,
                nav=_nav("/plugins/new", token), heading="Create a plugin",
                blurb="A starting point, and nothing you did not ask for.",
                message=message, bad=bad, css=_sheet()[0], head=_sheet()[1])


## -- upload -------------------------------------------------------------------

def upload_page(token: str, message: str = "", bad: bool = False) -> str:
    body = f"""
<section>
  <form method="post" action="/plugins/upload?token={escape(token)}"
        enctype="multipart/form-data">
    <div class="drop" id="drop">
      <span class="big">{svg('upload', 30)}</span>
      A zip of a plugin folder, or of its contents.
      <div class="picked" id="picked"></div>
    </div>
    <label class="browse" for="pick">{svg('folder-plus', 17)}
      <span>Choose a zip</span></label>
    <input type="file" id="pick" name="file" accept=".zip" hidden required>

    <label for="version">Version</label>
    <input id="version" name="version" placeholder="leave blank to keep"
           autocomplete="off">
    <p class="hint">Sets the version in <code>plugin.toml</code>. Leave it
       blank to use whatever the zip carries. A zip of only the files you
       changed has no <code>plugin.toml</code> in it, and this is the way to
       move the version anyway - one that never moves tells nobody
       anything.</p>

    <label for="folder">Install as</label>
    <input id="folder" name="folder" placeholder="AnimePlugin"
           autocomplete="off">
    <p class="hint">The folder under <code>plugins/</code>. Leave blank to use
       the folder inside the zip, or the zip's own name.</p>

    <button type="submit">{svg('playlist-check', 16)}<span>See what this would do</span></button>
  </form>
</section>

<section>
  <h2>Before anything is written</h2>
  <p class="hint">Nothing is overwritten by uploading. The next page lists
     exactly which files would be replaced and which would be new, and waits
     for you.</p>
  <p class="hint">A plugin that is not installed here yet also needs somebody
     at the panel to agree. Being allowed to upload and agreeing to run new
     code are different questions.</p>
</section>
"""
    # Drag and drop as well as the picker, and the chosen name shown back.
    # A file input that says "no file selected" after a drop is a control
    # that looks like it did not work.
    script = """
var drop = document.getElementById('drop');
var pick = document.getElementById('pick');
var said = document.getElementById('picked');
function show() {
  said.textContent = pick.files && pick.files.length ? pick.files[0].name : '';
}
pick.addEventListener('change', show);
['dragenter','dragover'].forEach(function (e) {
  drop.addEventListener(e, function (ev) {
    ev.preventDefault(); drop.classList.add('over'); });
});
['dragleave','drop'].forEach(function (e) {
  drop.addEventListener(e, function (ev) {
    ev.preventDefault(); drop.classList.remove('over'); });
});
drop.addEventListener('drop', function (ev) {
  if (ev.dataTransfer && ev.dataTransfer.files.length) {
    pick.files = ev.dataTransfer.files; show();
  }
});
"""
    return page("Upload a plugin", body, token=token,
                nav=_nav("/plugins/upload", token), heading="Upload a plugin",
                blurb="Send a new version, or a new plugin.",
                message=message, bad=bad, css=_sheet()[0], head=_sheet()[1], script=script)


def _version_pill(report: dict) -> str:
    """`v0.1.0 → v0.2.0`, or just the one when there is nothing to compare."""
    was, now = report.get("was_version") or "", report.get("version") or ""
    if was and now and was != now:
        return (f'<span class="pill">v{escape(was)}</span>'
                f'<span class="pill on">&rarr; v{escape(now)}</span>')
    return f'<span class="pill">v{escape(now)}</span>' if now else ""


def _file_group(title: str, items: list, klass: str, tag: str) -> str:
    if not items:
        return ""
    rows = "".join(f'<li>{escape(p)}<span class="tag">{escape(tag)}</span></li>'
                   for p in items)
    return (f'<div class="group {klass}"><h3>{escape(title)}'
            f'<span class="n">{len(items)}</span></h3>'
            f'<ul class="files">{rows}</ul></div>')


def result_page(name: str, key: str, message: str, action: str,
                token: str, bad: bool = False) -> str:
    """
    What happened, and the one thing left to do about it.

    `action` is "load", "reload" or "" - the files are on disk either way,
    and the running plugin is not them until somebody says so. Offered rather
    than done: running new code is the decision the whole of this section
    exists to put in somebody's hands, and doing it automatically at the end
    would hand it back.
    """
    if action == "reload":
        note = ("The files are updated. The plugin is still running the code "
                "it was loaded with.")
        label, icon = "Reload it now", "refresh"
    elif action == "load":
        note = ("The files are installed. The plugin is not running yet.")
        label, icon = "Load it now", "play"
    else:
        note, label, icon = "", "", ""

    button = ""
    if action:
        # One block, not a note above a footer. The two used to be a warning
        # strip and a grey button row with a link beside it at a different
        # height - which reads as "here is the page, and here is some
        # furniture", when it is the only thing on the page worth pressing.
        button = (f'<div class="nextstep">'
                  f'<h3>{svg(icon, 19)}<span>One more step</span></h3>'
                  f'<p>{escape(note)}</p>'
                  f'<div class="acts">'
                  f'<button type="submit" id="go" data-act="{action}" '
                  f'data-key="{escape(key)}">{svg(icon, 16)}'
                  f'<span>{escape(label)}</span></button>'
                  f'<a class="skip" href="/plugins?token={escape(token)}">'
                  f'Not now</a>'
                  f'</div></div>')

    script = """
var go = document.getElementById('go');
if (go) {
  go.addEventListener('click', function () {
    go.disabled = true;
    fetch('/plugins/' + encodeURIComponent(go.dataset.key) + '/' + go.dataset.act
          + '?token=' + encodeURIComponent(TOKEN))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.request === 'Success') {
          location.href = '/plugins?token=' + encodeURIComponent(TOKEN);
        } else {
          go.disabled = false;
          alert(d.reason || 'That did not work.');
        }
      })
      .catch(function () { go.disabled = false; });
  });
}
"""
    return page(f"{name}", button, token=token,
                nav=_nav("/plugins/upload", token), heading=name, blurb=message,
                message=message, bad=bad, css=_sheet()[0], head=_sheet()[1],
                script=f"var TOKEN={token!r};" + script)


def waiting_page(name: str, token: str) -> str:
    """
    Sent while a new plugin waits for somebody at the panel.

    Polled rather than left as a dead end. The answer comes from a different
    room and can be minutes away, and a page that says "waiting" and never
    changes is one somebody reloads until they give up.
    """
    body = f"""
<div class="warnbox">{svg('shield-key', 20)}<div>
  <strong>Waiting for the panel.</strong> Somebody at the panel has to allow
  <code>{escape(name)}</code> before it is written. Nothing has been installed
  yet.</div></div>
<section id="state"><p class="hint">Still waiting&hellip;</p></section>
"""
    script = """
var NAME = %s;
function poll() {
  fetch('/plugins/upload/status?name=' + encodeURIComponent(NAME)
        + '&token=' + encodeURIComponent(TOKEN))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.state === 'approved') {
        location.href = '/plugins/installed?name=' + encodeURIComponent(NAME)
                      + '&token=' + encodeURIComponent(TOKEN);
      } else if (d.state === 'denied' || d.state === 'expired' ||
                 d.state === 'gone') {
        document.getElementById('state').innerHTML =
          '<p class="hint">' + (d.detail || 'That request is no longer waiting.')
          + '</p>';
      } else {
        setTimeout(poll, 2000);
      }
    })
    .catch(function () { setTimeout(poll, 4000); });
}
poll();
""" % (repr(name),)
    return page("Waiting for the panel", body, token=token,
                nav=_nav("/plugins/upload", token),
                heading="Waiting for the panel",
                blurb="Nothing has been written yet.", css=_sheet()[0], head=_sheet()[1],
                script=f"var TOKEN={token!r};" + script)


def preview_page(report: dict, staged_token: str, token: str) -> str:
    """
    What the upload would do, with the confirm button under it.

    Ordered by what somebody stands to lose. Overwrites first, because that is
    the only part of this that is not reversible by deleting something.
    """
    warn = ""
    if report["overwritten"]:
        warn = (f'<div class="warnbox">{svg("alert", 20)}'
                f'<div><strong>{len(report["overwritten"])} file'
                f'{"" if len(report["overwritten"]) == 1 else "s"} will be '
                f'replaced.</strong> The versions installed now are not kept.'
                f'</div></div>')
    elif report["new"]:
        warn = (f'<div class="warnbox">{svg("shield-key", 20)}'
                f'<div><strong>This plugin is not installed here yet.</strong> '
                f'Somebody at the panel has to agree before it is written. '
                f'Plugins run with the same reach as the panel itself.</div>'
                f'</div>')

    groups = (
        _file_group("Replaced", report["overwritten"], "lose", "overwritten")
        + _file_group("New", report["created"], "gain", "added")
        + _file_group("Merged", report["merged"], "", "values kept")
        + _file_group("Kept", [k["path"] for k in report["kept"]], "",
                      "install once")
        + _file_group("Unchanged", report["unchanged"], "", "identical")
    )

    notes = "".join(f'<p class="hint">{escape(n)}</p>'
                    for n in report["notes"])

    reload_box = (f'<div class="warnbox">{svg("refresh", 20)}<div>'
                  f'{escape(report["reload_note"])}</div></div>')

    if report["writes"]:
        confirm = f"""
<form method="post" action="/plugins/upload/apply?token={escape(token)}">
  <input type="hidden" name="staged" value="{escape(staged_token)}">
  <button type="submit">{svg('check-network', 16)}<span>Apply these
    {report['writes']} change{'' if report['writes'] == 1 else 's'}</span></button>
  <a class="back" style="margin-left:10px"
     href="/plugins/upload?token={escape(token)}">Cancel</a>
</form>"""
    else:
        confirm = (f'<a class="back" href="/plugins/upload?token='
                   f'{escape(token)}">Back</a>')

    body = f"""
{warn}
<section>
  <h2>{escape(report['name'])}
      <span class="pill">{escape(report['key'])}</span>
      {_version_pill(report)}</h2>
  {groups or '<p class="empty">This zip has nothing in it.</p>'}
  {notes}
</section>
{reload_box}
<section>{confirm}</section>
"""
    return page("Confirm the upload", body, token=token,
                nav=_nav("/plugins/upload", token),
                heading="Confirm the upload",
                blurb="Nothing has been written yet.", css=_sheet()[0], head=_sheet()[1])
