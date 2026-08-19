import logging
import os
import time
import shutil
from threading import Thread

from src.constants import APP_NAME


def _pretty_size(total: int) -> str:
	"""Bytes as something readable. Two places at GB, one below."""
	if total < 1024:
		return f"{total} B"
	if total < 1024 * 1024:
		return f"{total / 1024:.1f} KB"
	if total < 1024 ** 3:
		return f"{total / (1024 * 1024):.1f} MB"
	return f"{total / (1024 ** 3):.2f} GB"

from urllib.parse import urlencode

from flask import Flask, jsonify, redirect, send_from_directory, request, render_template, make_response, send_file, Response

ADDRESS = "0.0.0.0"
PORT = 5000

#Where an approved device's token is remembered.
#
#A browser sends no X-Client-Token header and a bare address carries no query
#string, so without this an approved device is asked to request access again
#every time somebody types the address in.
TOKEN_COOKIE = "ha_device_token"
#A year. The token is revocable from the panel, so expiry adds nothing except
#somebody being asked to pair again for no reason.
TOKEN_COOKIE_AGE = 365 * 24 * 60 * 60

#Requests that a page polls. Werkzeug logs one line per request of its own, on
#top of anything this file logs - so the dashboard asking for its state every
#five seconds produced two identical lines a second time over.
POLLED_PATHS = ("/dashboard/state", "/ping", "/access/state")


class _QuietRequests(logging.Filter):
	"""Drops werkzeug's own line for a polled route."""

	def filter(self, record) -> bool:
		try:
			message = record.getMessage()
		except Exception:
			return True
		return not any(path in message for path in POLLED_PATHS)


def FlaskService(stop_event, client, flask):
	from werkzeug.serving import make_server

	# Werkzeug logs through the logging module AND prints to stderr through its
	# own handler. Silencing one leaves the other, which is why these lines
	# arrived in pairs.
	access = logging.getLogger("werkzeug")
	access.addFilter(_QuietRequests())
	for handler in list(access.handlers):
		handler.addFilter(_QuietRequests())
	# Anything attached later - werkzeug installs its handler lazily - is
	# covered by the logger-level filter above.

	# threaded: this loop serves one request at a time, so a single slow
	# endpoint - a pip install, an update download, /terminate's own wait -
	# blocked every other caller until it finished.
	server = make_server(ADDRESS, PORT, flask, threaded=True)
	server.timeout = 1

	while not stop_event.is_set():
		server.handle_request()

def FlaskApp(client):
	here = os.path.dirname(os.path.abspath(__file__))
	app = Flask(
		APP_NAME.replace(" ", "") + "_backend",
		# Beside the panel's own stylesheets and scripts, in src/web/, rather
		# than in a folder of its own - everything the panel serves to a
		# browser is in one place now.
		#
		# A subfolder on purpose: core_assets() globs the top of src/web/ and
		# nothing below it, so a template is never servable as an asset. A
		# .html served raw is its Jinja source, which is the page's structure
		# and whatever a {% if %} was guarding.
		template_folder=os.path.join(here, "web", "templates"),
		# No static folder. Flask mounts one at /static whether anything is
		# in it or not, and this one held nothing and never had: the panel's
		# own CSS and JS are served by the /web route, which fingerprints
		# them, and a plugin's are served by its own. An empty mount is a URL
		# that answers, an endpoint in url_for, and a thing to wonder about.
		static_folder=None,
	)

	@app.context_processor
	def _shared_chrome():
		"""
		The panel's name, the chrome and the back control, in every template.

		A context processor rather than a keyword on each render_template():
		there are several pages and adding one is how a heading ends up saying
		"Home Assistant" on a panel somebody named something else, or how a
		page ends up with a hand-written back link that drifts from the shared
		one. This way a new page gets all three without having to ask.
		"""
		from src.webui import back_button, chrome_css
		try:
			name = client.panel_name()
		except Exception:
			name = APP_NAME
		return {"panel": name,
				"chrome": chrome_css(),
				"back_button": back_button}

	# AUTH & HELPERS
	def _token() -> str:
		"""
		The calling device's token, from wherever it arrived.

		The same three places `auth()` looks. A route that reads only
		`request.args` works from a link and silently does not from a form
		post or a fetch with a header, which is the kind of difference that
		shows up as one page in a section behaving unlike the others.
		"""
		return (request.args.get("token")
				or request.form.get("token")
				or request.headers.get("X-Client-Token")
				or request.cookies.get(TOKEN_COOKIE)
				or "").strip()

	def _wants_html() -> bool:
		"""
		Whether this looks like a browser rather than a script.

		A script wants a 401 it can read; a person looking at a blank page
		with some JSON on it wants to be told what to do about it. The two
		cases are told apart by what the caller says it accepts.
		"""
		accepts = request.headers.get("Accept", "")
		return "text/html" in accepts and "application/json" not in accepts.split(",")[0]

	def _strip_token(target: str) -> str:
		"""Remove any token/id from a URL a browser is about to be sent to."""
		from urllib.parse import urlsplit, urlunsplit, parse_qsl
		parts = urlsplit(target or "/")
		kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
				if k not in ("token", "id")]
		return urlunsplit(("", "", parts.path or "/", urlencode(kept), ""))

	def _next_target() -> str:
		"""
		Where to send a browser back to, with any credential stripped out.

		request.full_path carries the query string the browser arrived with -
		including the token that has just been rejected. Appending a fresh one
		produces `?token=OLD&token=NEW`, and the FIRST value wins in every
		parser there is, so the browser was sent straight back holding the bad
		token and bounced between here and /access/wait forever. The same
		applies to /access/name, which is the naming half of the same loop.
		"""
		from urllib.parse import urlsplit, urlunsplit, parse_qsl
		parts = urlsplit(request.full_path or "/")
		kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
				if k not in ("token", "id")]
		path = parts.path or "/"
		return urlunsplit(("", "", path, urlencode(kept), ""))

	def auth():
		"""
		A per-device token, checked against the approved list.

		There is no shared secret any more. A token identifies one device, is
		revocable on its own, and tells an endpoint who is calling - none of
		which a single id copied between machines could do.
		"""
		token = (request.args.get("token")
				 or request.headers.get("X-Client-Token")
				 or request.cookies.get(TOKEN_COOKIE)
				 or "").strip()
		if not token:
			if _wants_html():
				# Sent to wait rather than refused. A browser arriving at a
				# page it is not allowed to see should be walked through
				# getting access and put back where it was going, not handed
				# an error and left to find /access/request on its own.
				return redirect("/access/wait?" + urlencode({"next": _next_target()}))
			return {"request": "Failed",
					"reason": "No device token. Request access at /access/request.",
					"state": "unknown"}, 401

		user = client.USERS.touch(token)
		if user is not None:
			# Still unnamed and looking at a page: sent to name itself first.
			# The poll is not the only way in - a device with a bookmark can
			# arrive anywhere - so the check belongs on every request rather
			# than on the one page that happens to poll.
			# awaiting_decision comes first. Sending a device to name itself
			# while somebody at the panel is still being asked whether to name
			# it produces two people naming one device at once.
			if getattr(user, "awaiting_decision", False) and _wants_html() and \
					not request.path.startswith("/access/"):
				return redirect("/access/wait?" + urlencode(
					{"next": _next_target(), "token": token}))

			if user.awaiting_name and _wants_html() and \
					not request.path.startswith("/access/"):
				return redirect("/access/name?" + urlencode(
					{"token": token, "next": _next_target()}))

			# Recorded on the request, so an endpoint can tell who called it -
			# the calendar tags what it stores with this.
			request.environ["ha.user"] = user
			# Remembered, so the next visit does not have to carry the token.
			# A browser sends no X-Client-Token header, and a bare address has
			# no query string - without this an approved device is asked to
			# request access again every time somebody types the address.
			request.environ["ha.set_token"] = token
			return None

		state = client.USERS.state_of(token)
		if _wants_html():
			# The token is NOT forwarded. It is the one that was just refused,
			# and handing it back to the wait page means it asks about a token
			# nobody will ever approve rather than requesting a new one.
			return redirect("/access/wait?" + urlencode(
				{"next": _next_target()}))
		return {"request": "Failed",
				"reason": f"This device is {state}.", "state": state}, 403

	#Routes that a page polls. Logged at debug, so they are there when
	#debugging and invisible otherwise - the dashboard asks for its state every
	#five seconds, which is 720 identical lines an hour.
	POLLED = ("/dashboard/state", "/ping", "/access/state")

	def answered(payload, status: int = 200):
		"""
		A JSON answer, as JSON or as a page.

		Wrapped once here rather than at each route: a browser following a link
		gets something readable, a script gets the data, and neither has to be
		told which it is.
		"""
		from src.webresult import wants_page, render
		if not wants_page(request):
			return payload, status
		# `_token()`, which also reads the cookie.
		#
		# This used to look in the query string and the header only. An
		# approved phone browses on its cookie and puts no ?token= on the
		# address bar, so `token` was EMPTY on exactly the devices this is
		# rendered for - which passes `auth()` (it checks the cookie) and
		# then fails every permission check and writes an empty token into
		# every link on the page.
		token = _token()
		return render(payload, token=token, status=status), status, \
			{"Content-Type": "text/html; charset=utf-8"}

	def log(level:str = "info", extra:str = ""):
		if level == "info" and request.path in POLLED:
			level = "debug"
		# Both, and by the same rule as the other masking site - a token in a
		# shared log or a screenshot is a device somebody else can be.
		args = {k: ("***" if k in ("id", "token") else v)
				for k, v in request.args.items()}
		arg_str = "  " + "  ".join(f"{k}={v}" for k, v in args.items()) if args else ""

		if not extra:
			client.log(level, f"[API] {request.method} {request.path}{arg_str}")
		else:
			client.log(level, f"[API][{extra}] {request.method} {request.path}{arg_str}")


	## CLIENT CONTROL ENDPOINTS
	@app.route("/terminate")
	def terminate_client():
		err = auth()
		if err: return err
		client.simple_notify("kill", "Termination", "Was asked to Terminate via API")

		def shutdown():
			# The delay is so the notification is on screen before the window
			# goes. It belongs off the request thread - sleeping here held the
			# reply open and, before the server was threaded, the whole API.
			time.sleep(1)
			client.call_on_ui(client.stop)

		Thread(target=shutdown, name="__api_terminate", daemon=True).start()
		return {"request": "Success"}

	@app.route("/restart")
	def restart_client():
		err = auth()
		if err: return err
		if not client.BUILT:
			return {"request": "Failed", "reason": "Wait until the Program has started fully."}
		client.RESTART = True
		return {"request": "Success"}

	@app.route("/update")
	def update_client():
		err = auth()
		if err: return err
		if not client.BUILT:
			return {"request": "Failed", "reason": "Wait until the Program has started fully."}

		# Same path the quick settings button takes. Two copies of the
		# stage-then-restart dance is two places to get it wrong.
		client.begin_update()
		return {"request": "Success", "message": "Update staging started."}

	@app.route("/update/check")
	def update_check_route():
		"""Whether one is waiting, without downloading anything."""
		err = auth()
		if err: return err

		from src import update_check
		try:
			available, commit, reason = update_check.check()
		except Exception as e:
			return {"request": "Failed", "reason": str(e)}, 502

		return {
			"request":   "Success",
			"available": available,
			"reason":    reason,
			"installed": update_check.installed_sha(),
			"latest":    commit.as_dict() if commit else None,
		}, 200

	@app.route("/notify/", methods=["GET"])
	def redirects_bad_endpoint():
		return redirect(f"{request.base_url.rstrip('/')}?{request.query_string.decode()}")

	@app.route("/notify", methods=["GET"])
	def backend_notify():
		try:
			# Read all three before touching any of them. Calling .split() on a
			# missing icon raised AttributeError straight past the check below,
			# so a request with no icon returned a 500 about NoneType instead
			# of the "missing ->" reply that was written for it.
			raw_icon = request.args.get("icon") or ""
			icon  = raw_icon.split(".")[-1]
			title = request.args.get("title")
			body  = request.args.get("body")
			if icon and title and body:
				client.simple_notify(icon, title, body)
				return {"request": "Success"}, 200
			else:
				missing = []
				if not icon:  missing.append("icon")
				if not title: missing.append("title")
				if not body:  missing.append("body")
				return {"request": "Failed", "reason": f"missing -> {missing}"}, 404
		except Exception as e:
			client.log("error", f"[backend.backend_notify] Notify Failed: {e}")
			return {"request": "Failed", "reason": str(e)}, 500
			
	
	@app.route("/process", methods=["GET"])
	def start_intent():
		query = request.args.get("q")
		if not (query and query.strip()):
			return {"request": "Failed", "reason": "No Query(q) Given!"}, 404

		# submit(), not pre_processing(). The second is the microphone's path
		# and looks for a wake word first; a typed query has none, so it was
		# matched against every wake arg, matched none, and was dropped in
		# silence - a 200 and nothing happening.
		#
		# Answered rather than assumed: the caller is told whether anything
		# took it.
		try:
			taken = bool(client.STT.submit(query))
		except Exception as e:
			return {"request": "Failed", "reason": str(e)}, 500
		if not taken:
			return {"request": "Failed",
			        "reason": "The assistant is busy."}, 409
		return {"request": "Success"}, 200

	@app.route("/ask", methods=["GET", "POST"])
	def ask_page():
		"""
		Ask the assistant something, from a browser.

		GET /ask?token=...&q=what is the weather

		With no query it serves the form. A separate endpoint from
		`/process` on purpose: that one is the machine-readable route with a
		bare JSON contract, and a page and a script that share a URL end up
		with one of them shaping the other. This can grow a form, examples
		and a friendlier failure without touching what scripts depend on.

		The answer happens on the PANEL, not here. What comes back is
		whether anything took it, which is the only thing this end can know.
		"""
		log()
		err = auth()
		if err: return err

		# `_token()`, which also reads the cookie.
		#
		# This used to look in the query string and the header only. An
		# approved phone browses on its cookie and puts no ?token= on the
		# address bar, so `token` was EMPTY on exactly the devices this is
		# rendered for - which passes `auth()` (it checks the cookie) and
		# then fails every permission check and writes an empty token into
		# every link on the page.
		token = _token()
		query = str(request.values.get("q") or "").strip()

		if not query:
			# Real skills rather than invented ones. An example that matches
			# nothing teaches somebody that the panel does not work.
			examples = []
			try:
				for skill in list(client.SKILLS.skills())[:60]:
					for phrase in (getattr(skill, "examples", None) or [])[:1]:
						if phrase and len(phrase) <= 34:
							examples.append(phrase)
							break
					if len(examples) >= 6:
						break
			except Exception:
				examples = []
			return render_template("ask.html", token=token,
								   examples=examples,
								   panel=client.panel_name()), 200

		if not client.BUILT:
			return {"request": "Failed",
					"reason": "Wait until the Program has started fully."}, 409

		# submit(), like /process - the microphone's path looks for a wake
		# word first and a typed query has none.
		try:
			taken = bool(client.STT.submit(query))
		except Exception as e:
			return {"request": "Failed", "reason": str(e)}, 500
		if not taken:
			return {"request": "Failed",
					"what": "The assistant is busy - try again in a moment.",
					"reason": "The assistant is busy."}, 409
		return {"request": "Success", "what": f"Asked: {query}"}, 200

	## NAVIGATION

	def _coerce(value: str):
		"""
		A query string is all strings; page data is not.

		`bool("false")` is True, so `?lock_address=false` would lock the
		address bar rather than leave it alone - the exact opposite of what
		was asked for, silently. Only the words that unambiguously mean a
		boolean become one; a bare 1 or 0 stays a number, because `zoom=1`
		is a number and `lock_address=1` is still truthy either way.
		"""
		text = (value or "").strip()
		low = text.lower()
		if low in ("true", "yes", "on"):
			return True
		if low in ("false", "no", "off"):
			return False
		try:
			return int(text)
		except ValueError:
			pass
		try:
			return float(text)
		except ValueError:
			pass
		return text

	def _page_data() -> dict:
		"""Every query parameter except the ones that belong to the API."""
		reserved = ("token", "id", "override")
		data = {k: _coerce(v) for k, v in request.args.items() if k not in reserved}
		if request.method == "POST":
			body = request.get_json(silent=True) or request.form.to_dict()
			for key, value in (body or {}).items():
				if key in reserved:
					continue
				data[key] = _coerce(value) if isinstance(value, str) else value
		return data

	@app.route("/goto/<path:page>", methods=["GET", "POST"])
	def goto_page(page):
		"""
		Switch pages, with the query string as the page's `data`.

		GET /goto/%23webpage?token=...&url=https://example.com&lock_address=true

		The leading '#' has to be percent-encoded in a URL or everything after
		it is a fragment the server never sees, so a bare key is accepted and
		the '#' put back.
		"""
		err = auth()
		if err: return err
		if not client.BUILT:
			return {"request": "Failed",
					"reason": "Wait until the Program has started fully."}, 409

		key = page.strip()
		if not key.startswith("#"):
			key = f"#{key}"

		if not client.has_page(key):
			return {"request": "Failed",
					"reason": f"No page registered as '{key}'.",
					"pages": sorted(client.get_pages())}, 404

		data = _page_data()

		# Override by default.
		#
		# goto() returns early when the requested page is already on screen, so
		# without this an endpoint asking for the page the panel happens to be
		# on did nothing at all - and "nothing at all" includes ignoring the
		# data sent with it. Asking a panel to go somewhere it already is, with
		# a different url or a different lock, is a normal request and the
		# obvious reading is that it should be honoured.
		#
		# Pass override=false to keep the old behaviour, where re-asking for
		# the current page is a no-op.
		# `not in (False, 0)` rather than `is not False`: _coerce turns "0"
		# into the integer zero, and `0 is not False` is True - so override=0
		# would have switched it on, which is the opposite of what was typed.
		override = _coerce(request.args.get("override", "true")) not in (False, 0)

		# goto() builds and destroys widgets, so it belongs on the UI thread -
		# this is a Flask worker.
		client.call_on_ui(lambda: client.goto(key, data=data, override=override))
		return {"request": "Success", "page": key, "data": data}, 200

	@app.route("/webhome", methods=["GET"])
	def web_home():
		"""
		What the panel's browser opens on.

		Not authed. This is served to the panel's OWN web view, which has no
		token and no way to be given one - and it exposes nothing a person
		standing at the panel could not already see by opening the page.
		"""
		marks = []
		try:
			marks = client.BOOKMARKS.all()
		except Exception as e:
			client.log("warning", f"[Bookmarks] Could not list: {e}")
		return render_template("webhome.html", bookmarks=marks), 200

	@app.route("/bookmark/forget", methods=["GET", "POST"])
	def forget_bookmark():
		"""
		Remove one, from the browser's own home page.

		Not authed, for the same reason /webhome is not: it is served to the
		panel's own web view, which has no token. Somebody who can reach that
		view is standing at the panel.
		"""
		url = str(request.values.get("url") or "").strip()
		if not url:
			return {"request": "Failed", "reason": "No address."}, 400
		gone = False
		try:
			gone = bool(client.BOOKMARKS.remove(url))
		except Exception as e:
			return {"request": "Failed", "reason": str(e)}, 500
		return {"request": "Forgotten" if gone else "Not found",
				"url": url}, 200 if gone else 404

	@app.route("/bookmark-icon/<path:name>", methods=["GET"])
	def bookmark_icon(name):
		"""
		One saved favicon.

		Served by NAME rather than by path, and the name is stripped to its
		last component - the icon folder is inside the user's data directory,
		and a route that joins whatever arrives would read anything on the
		disk.
		"""
		from flask import send_from_directory
		from pathlib import Path as _Path
		safe = _Path(str(name)).name
		if not safe.endswith(".png"):
			return {"request": "Failed", "reason": "Not an icon."}, 404
		folder = client.BOOKMARKS.icon_dir()
		if not (folder / safe).is_file():
			return {"request": "Failed", "reason": "No such icon."}, 404
		return send_from_directory(str(folder), safe)

	@app.route("/goto/page", methods=["GET"])
	def goto_ui():
		"""
		The page switcher, for a device with a browser.

		`/goto/page` also matches `/goto/<path:page>`, but Werkzeug sorts rules
		by specificity rather than by declaration order, so the static one wins
		wherever it is written. Verified rather than assumed - a page key of
		"page" would otherwise be unreachable and this route would be shadowed.
		"""
		err = auth()
		if err: return err
		token = request.args.get("token", "")
		return render_template("goto.html",
							   token=token), 200

	@app.route("/pages", methods=["GET"])
	def list_pages():
		"""What /goto will accept, and what is on screen now."""
		err = auth()
		if err: return err
		current = getattr(client.PAGE, "name", None) if client.PAGE else None
		return {"request": "Success",
				"current": current,
				"pages": sorted(client.get_pages())}, 200

	## CLIPBOARD

	def _on_ui_result(fn, timeout: float = 2.0):
		"""
		Run something on the UI thread and wait for what it returns.

		call_on_ui is fire and forget, which is fine for setting a value and
		useless for reading one. The clipboard belongs to the GUI thread, so
		reading it from a Flask worker is not an option.
		"""
		from threading import Event
		box = {}
		done = Event()

		def run():
			try:
				box["value"] = fn()
			except Exception as e:
				box["error"] = e
			finally:
				done.set()

		client.call_on_ui(run)
		if not done.wait(timeout):
			raise TimeoutError("the UI thread did not answer in time")
		if "error" in box:
			raise box["error"]
		return box.get("value")

	@app.route("/clipboard", methods=["GET", "POST"])
	def clipboard():
		"""
		GET  /clipboard?token=...            read what is on it
		GET  /clipboard?token=...&text=...   put something on it
		POST /clipboard  {"text": "..."}     the same, for anything long
		"""
		err = auth()
		if err: return err
		if not client.BUILT:
			return {"request": "Failed",
					"reason": "Wait until the Program has started fully."}, 409

		text = request.args.get("text")
		if text is None and request.method == "POST":
			body = request.get_json(silent=True) or request.form.to_dict()
			text = (body or {}).get("text")

		try:
			if text is None:
				current = _on_ui_result(lambda: client.app.clipboard().text())
				return {"request": "Success", "text": current or "",
						"length": len(current or "")}, 200

			_on_ui_result(lambda: client.app.clipboard().setText(str(text)))
			return {"request": "Success", "text": str(text),
					"length": len(str(text))}, 200
		except Exception as e:
			return {"request": "Failed", "reason": str(e)}, 500

	@app.route("/clipboard/page", methods=["GET"])
	def clipboard_page():
		"""
		The clipboard, as a page a phone can open.

		The JSON endpoint above needs a client that can make requests; this
		needs a browser. Copying from the panel to a phone is the common
		direction - a URL the panel scanned, an address a skill produced - and
		asking somebody to write a fetch() for that is not an answer.
		"""
		err = auth()
		if err: return err
		# The same way every other page here gets it: whatever the browser
		# arrived with. The cookie set at approval means this is usually
		# already in the URL after one visit.
		token = request.args.get("token", "")
		return render_template("clipboard.html",
							   token=token), 200

	@app.route("/clipboard/clear", methods=["GET", "POST"])
	def clipboard_clear():
		err = auth()
		if err: return err
		if not client.BUILT:
			return {"request": "Failed",
					"reason": "Wait until the Program has started fully."}, 409
		try:
			_on_ui_result(lambda: client.app.clipboard().clear())
			return {"request": "Success", "text": ""}, 200
		except Exception as e:
			return {"request": "Failed", "reason": str(e)}, 500


	## ASSET MANAGEMENT ENDPOINTS
	@app.route("/upload", methods=["GET"])
	def upload_index():
		log()
		err = auth()
		if err: return err

		#collect all uploadable FOLDER assets with stats
		uploadable = []
		for key, asset in client.ASSETS.get("FOLDER", {}).items():
			# Guarded assets are not listed as ordinary uploads. They have
			# their own page, with their own confirmation, and offering a
			# drag-and-drop box for one here would be a second way in that
			# skipped it.
			if getattr(asset, "is_guarded", False):
				continue
			if not getattr(asset, "is_uploadable", False):
				continue
			info = {"key": key, "path": str(asset), "exists": False,
					"file_count": 0, "size": "0 B", "size_bytes": 0,
					"deletable": bool(getattr(asset, "is_deletable", False))}
			try:
				import os
				if asset.exists():
					info["exists"] = True
					files = [f for f in asset.iterdir() if f.is_file() and not f.name.startswith(".")]
					total = sum(f.stat().st_size for f in files)
					info["file_count"] = len(files)
					info["size_bytes"] = total
					info["size"] = _pretty_size(total)
			except Exception as e:
				client.log("error", f"[backend.upload_index] Upload Failed: {e}")
			uploadable.append(info)

		# Passed through so the page's own links and its POST carry the token
		# that fetched it - the browser has no other way to authenticate.
		token = request.args.get("token", "")
		return render_template("upload_index.html", assets=uploadable,
							   token=token)

	@app.route("/upload/<key>", methods=["GET"])
	def upload_page(key):
		log()
		err = auth()
		if err: return err

		path = client.asset("FOLDER", key)
		if not path:
			return {"request": "Failed", "reason": f"No FOLDER asset '{key}'"}, 404

		if not getattr(path, "is_uploadable", False):
			return {"request": "Failed", "reason": f"Asset '{key}' is not marked as uploadable"}, 403

		if getattr(path, "is_guarded", False):
			return {"request": "Failed",
					"reason": f"Asset '{key}' holds code and is uploaded "
							  f"through its own page, not here."}, 403

		token = request.args.get("token", "")
		# The shared chrome rather than a copy in the template. Two templates
		# each carrying their own copy of the same CSS is how one of them
		# ended up with no size on its back-button icon, and an SVG with no
		# size fills whatever contains it.
		return render_template("upload.html", key=key, path=str(path),
							   token=token,
							   deletable=bool(getattr(path, "is_deletable", False)))

	def _readable_folder(key):
		"""
		The folder behind `key` for LOOKING at, or the refusal.

		The same rule the page itself uses - uploadable, and not guarded -
		rather than the deletable one. Listing and downloading are not
		deleting: a folder somebody may put files into and may not empty is
		still a folder they can be shown, and answering 403 to the listing
		meant an un-managed page arrived with nothing on it and no way to get
		anything off it.
		"""
		path = client.asset("FOLDER", key)
		if not path:
			return None, ({"request": "Failed",
						   "reason": f"No FOLDER asset '{key}'"}, 404)
		if not getattr(path, "is_uploadable", False):
			return None, ({"request": "Failed",
						   "reason": f"Asset '{key}' is not marked as "
									 f"uploadable"}, 403)
		if getattr(path, "is_guarded", False):
			return None, ({"request": "Failed",
						   "reason": f"Asset '{key}' holds code and is "
									 f"handled through its own page."}, 403)
		return path, None

	def _deletable_folder(key):
		"""The folder behind `key`, or the refusal to hand it over."""
		path = client.asset("FOLDER", key)
		if not path:
			return None, ({"request": "Failed",
						   "reason": f"No FOLDER asset '{key}'"}, 404)
		if not getattr(path, "is_deletable", False):
			return None, ({"request": "Failed",
						   "reason": f"Asset '{key}' is not marked as deletable"}, 403)
		return path, None

	def _safe_name(name):
		"""
		A plain filename, or nothing.

		The names come back from a page this served, but a request is a
		request whatever served the page before it - and `folder / "../x"`
		reaches outside the folder just as happily as any other path.
		"""
		import os
		name = os.path.basename(str(name or "")).strip()
		if not name or name.startswith(".") or "/" in name or "\\" in name:
			return ""
		return name

	@app.route("/upload/<key>/files", methods=["GET"])
	def upload_files(key):
		log()
		err = auth()
		if err: return err

		path, refusal = _readable_folder(key)
		if refusal: return refusal

		images = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
		files = []
		try:
			for entry in sorted(path.iterdir(), key=lambda f: f.name.lower()):
				if entry.name.startswith("."):
					continue

				if entry.is_dir():
					# Folders are listed too, and download as a zip. A folder
					# that is not shown is a folder nobody knows is there,
					# and the only way at what is inside it is a keyboard on
					# the panel.
					inside = [f for f in entry.rglob("*") if f.is_file()]
					total = sum(f.stat().st_size for f in inside)
					files.append({
						"name": entry.name,
						"is_dir": True,
						"count": len(inside),
						"size_bytes": total,
						"size": f"{len(inside)} file"
								f"{'' if len(inside) == 1 else 's'} \u00b7 "
								f"{_pretty_size(total)}",
						"is_image": False,
						"modified": int(entry.stat().st_mtime),
					})
					continue

				if not entry.is_file():
					continue
				size = entry.stat().st_size
				files.append({
					"name": entry.name,
					"is_dir": False,
					"size_bytes": size,
					"size": _pretty_size(size),
					"is_image": entry.suffix.lower() in images,
					"modified": int(entry.stat().st_mtime),
				})
		except Exception as e:
			client.log("error", f"[backend.upload_files] {key}: {e}")
			return {"request": "Failed", "reason": str(e)}, 500
		return {"request": "OK", "key": key, "files": files}

	@app.route("/upload/<key>/file/<path:name>", methods=["GET"])
	def upload_file_raw(key, name):
		"""
		One file: the thumbnail in the listing, and the download.

		Readable rather than deletable - the same file backs both, and a
		folder somebody may not empty is still one they can take a copy from.
		"""
		log()
		err = auth()
		if err: return err

		path, refusal = _readable_folder(key)
		if refusal: return refusal

		safe = _safe_name(name)
		target = path / safe if safe else None
		if not safe or not target.is_file():
			return {"request": "Failed", "reason": "No such file"}, 404
		# as_attachment only when asked, so the same URL is both the preview
		# the grid draws and the download the link offers.
		wants_copy = str(request.args.get("download", "")).lower() in (
			"1", "true", "yes", "on")
		return send_file(str(target), as_attachment=wants_copy,
						 download_name=safe)

	@app.route("/upload/<key>/folder/<path:name>", methods=["GET"])
	def upload_folder_zip(key, name):
		"""
		One folder, zipped.

		Built in memory rather than on disk: this runs on a Flask worker and
		a temporary file left behind by a failed request is a temporary file
		forever. A folder big enough for that to matter is one nobody should
		be pulling through a browser anyway.
		"""
		log()
		err = auth()
		if err: return err

		path, refusal = _readable_folder(key)
		if refusal: return refusal

		safe = _safe_name(name)
		target = path / safe if safe else None
		if not safe or not target or not target.is_dir():
			return {"request": "Failed", "reason": "No such folder"}, 404

		import io
		import zipfile
		from pathlib import Path as _Path

		buffer = io.BytesIO()
		try:
			with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
				for entry in sorted(target.rglob("*")):
					if not entry.is_file() or entry.name.startswith("."):
						continue
					# Named from the folder, so unpacking it gives the folder
					# back rather than scattering its contents.
					bundle.write(entry, str(_Path(safe) /
											entry.relative_to(target)))
		except OSError as e:
			return {"request": "Failed",
					"reason": f"Could not read {safe}: {e}"}, 500

		body = buffer.getvalue()
		client.log("info", f"[Upload] {key}/{safe} zipped ({len(body)} bytes).")
		return Response(body, mimetype="application/zip", headers={
			"Content-Disposition": f'attachment; filename="{safe}.zip"',
			"Content-Length": str(len(body)),
		})

	@app.route("/upload/<key>/delete", methods=["POST"])
	def upload_delete(key):
		"""
		Remove the named files.

		Every name is answered for separately. A batch that stops at the first
		refusal leaves the caller unable to say which of the ten it asked
		about are still there.
		"""
		log()
		err = auth()
		if err: return err

		path, refusal = _deletable_folder(key)
		if refusal: return refusal

		payload = request.get_json(silent=True) or {}
		names = payload.get("files")
		if not isinstance(names, list) or not names:
			return {"request": "Failed", "reason": "No files given"}, 400

		deleted, failed = [], {}
		for raw in names:
			safe = _safe_name(raw)
			if not safe:
				failed[str(raw)] = "Bad filename"
				continue
			target = path / safe
			try:
				if not target.is_file():
					failed[safe] = "No such file"
					continue
				target.unlink()
				deleted.append(safe)
			except Exception as e:
				failed[safe] = str(e)

		if deleted:
			client.log("info", f"[backend.upload_delete] Removed "
							   f"{len(deleted)} from '{key}': "
							   f"{', '.join(deleted)}")
		for name, why in failed.items():
			client.log("warning", f"[backend.upload_delete] {key}/{name}: {why}")
		return {"request": "OK", "deleted": deleted, "failed": failed}

	@app.route("/upload/<key>", methods=["POST"])
	def upload_file(key):
		log()
		err = auth()
		if err: return err

		path = client.asset("FOLDER", key)
		if not path:
			return {"request": "Failed", "reason": f"No FOLDER asset '{key}'"}, 404

		if not getattr(path, "is_uploadable", False):
			return {"request": "Failed", "reason": f"Asset '{key}' is not marked as uploadable"}, 403

		# A guarded asset holds code, and this route cannot handle it in
		# either sense. It flattens a zip to its basenames - right for a
		# folder of sounds, and for a plugin it means every file landing in
		# `plugins/` with its folders gone - and it writes on the strength of
		# a login alone, where a guarded asset needs somebody at the panel.
		if getattr(path, "is_guarded", False):
			return {"request": "Failed",
					"reason": f"Asset '{key}' holds code and is uploaded "
							  f"through its own page, not here."}, 403

		if "file" not in request.files:
			return {"request": "Failed", "reason": "No file in request"}, 400

		import zipfile, os, re

		file = request.files["file"]
		if not file.filename:
			return {"request": "Failed", "reason": "Empty filename"}, 400

		#sanitize filename - strip path components, replace unsafe chars
		filename = os.path.basename(file.filename)
		filename = re.sub(r"[^\w\s.-]", "", filename).strip()
		if not filename:
			return {"request": "Failed", "reason": "Invalid filename"}, 400

		#ensure destination exists
		path.mkdir(parents=True, exist_ok=True)

		dest = path / filename

		#if zip - extract contents into the folder
		if filename.lower().endswith(".zip"):
			import tempfile
			with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
				file.save(tmp.name)
				tmp_path = tmp.name
			try:
				with zipfile.ZipFile(tmp_path, "r") as z:
					#only extract safe files - no path traversal
					extracted = []
					for member in z.infolist():
						member_name = os.path.basename(member.filename)
						if not member_name or member_name.startswith("."):
							continue
						out_path = path / member_name
						with z.open(member) as src, open(out_path, "wb") as dst:
							shutil.copyfileobj(src, dst)
						extracted.append(member_name)
				return {"request": "Success", "message": f"Extracted {len(extracted)} files from {filename}", "files": extracted}
			finally:
				os.unlink(tmp_path)
		else:
			file.save(str(dest))
			return {"request": "Success", "message": f"{filename} uploaded to {key}"}

	@app.route("/asset/<key>", methods=["GET"])
	@app.route("/asset/<key>/<filename>", methods=["GET"])
	def asset_download(key, filename=None):
		err = auth()
		if err: return err

		type_ = request.args.get("type", "FOLDER").upper()
		path  = client.asset(type_, key)

		if not path:
			return {"request": "Failed", "reason": f"Asset '{key}' not found"}, 404

		def _safe(f):
			name = f.name
			rel  = f.as_posix()
			return (
				f.is_file()
				and not name.startswith(".")
				and "src/" not in rel
			)

		if filename is None:
			files = [f.name for f in path.iterdir() if _safe(f)]
			return {"request": "Success", "key": key, "files": files}

		if filename.startswith(".") or "src/" in filename.replace("\\", "/"):
			return {"request": "Failed", "reason": "Access denied"}, 403

		if "." not in filename:
			match = next((f for f in path.iterdir() if f.stem == filename and _safe(f)), None)
			if match:
				return send_from_directory(path.as_posix(), match.name)
			return {"request": "Failed", "reason": f"No file with stem '{filename}' in '{key}'"}, 404

		actual = path / filename
		if actual.exists() and _safe(actual):
			return send_from_directory(path.as_posix(), actual.name, as_attachment=True)
		if actual.exists():
			return {"request": "Failed", "reason": "Access denied"}, 403
		return {"request": "Failed", "reason": f"File '{filename}' not found in '{key}'"}, 404

	@app.route("/settings/<path:path>", methods=["GET", "POST"])
	def setting_set(path):
		log()
		# This route reads AND writes every setting in the app, including the
		# window geometry and the assistant configuration. It was the only
		# endpoint of its kind with no auth check on it at all.
		err = auth()
		if err: return err

		if not path:
			return {"request": "Failed", "reason": "No given Path"}, 404

		setting = client.SETTINGS.get_path(path)
		if setting is None:
			return {"request": "Failed", "reason": f"No Setting at {path}"}, 404

		# Presence, not truthiness: `?v=` with an empty value is a legitimate
		# write, and testing the value meant a setting could never be cleared.
		if "v" not in request.args:
			return {"request": "Success", "setting": setting}, 200

		client.SETTINGS.set_path(path, request.args.get("v"))
		return {"request": "Success", "setting": client.SETTINGS.get_path(path)}, 200


	## INDEX
	@app.route("/", methods=["GET"])
	def index():
		"""
		Somewhere to start.

		Authed on purpose: a browser with no token is redirected into the
		approval flow and arrives back here holding one, so opening the panel's
		address is the whole of setting a phone up.
		"""
		log()
		err = auth()
		if err: return err

		# `_token()`, which also reads the cookie.
		#
		# This used to look in the query string and the header only. An
		# approved phone browses on its cookie and puts no ?token= on the
		# address bar, so `token` was EMPTY on exactly the devices this is
		# rendered for - which passes `auth()` (it checks the cookie) and
		# then fails every permission check and writes an empty token into
		# every link on the page.
		token = _token()
		user = request.environ.get("ha.user")

		from src.webicons import svg

		# The panel's own pages. A plugin's go above these - see below.
		pages = [
			{"url": "/docs", "label": "Documentation",
			 "description": "Everything about this panel and how to extend it.",
			 "auth": False, "icon": "book-open-variant"},
			{"url": "/upload", "label": "Files",
			 "description": "Send files to anything the panel has opened up.",
			 "auth": True, "icon": "upload"},
			{"url": "/goto/page", "label": "Go to",
			 "description": "Change what the panel is showing, or send it to a "
							"web page.",
			 "auth": True, "icon": "arrow-right-bold"},
			{"url": "/say", "label": "Say something",
			 "description": "Read a message out on the panel, from anywhere.",
			 "auth": True, "icon": "message-text"},
			{"url": "/ask", "label": "Ask",
			 "description": "Ask the assistant something and let the panel "
							"answer.",
			 # An icon the set actually has - svg() falls back to a dot for
			 # a name it does not know, which is a gap rather than an icon.
			 "auth": True, "icon": "message-text"},
			{"url": "/clipboard/page", "label": "Clipboard",
			 "description": "Read what is on the panel's clipboard, or put "
							"something on it.",
			 "auth": True, "icon": "clipboard-text"},
		]

		# Only for a device allowed to use it. A drawer entry leading to a
		# refusal is a door with a lock and no handle - it tells somebody the
		# room exists and then stops them, every time they look.
		try:
			if client.USERS.may(token, "plugins"):
				pages.append(
					{"url": "/plugins", "label": "Plugins",
					 "description": "What this panel is running, and how to "
									"add or update one.",
					 "auth": True, "icon": "puzzle"})
		except Exception as e:
			client.log("debug", f"[Index] Could not check plugin access: {e}")
		# Everything above is the panel's own and is on every install. What a
		# plugin adds is what somebody chose to put there, and it goes first -
		# the same reasoning the actions below already follow.
		#
		# Two lists rather than one sorted list, because they are not the same
		# kind of thing: the panel's pages are fixed and a plugin's come and go
		# with what is installed, and a heading over each says which is which
		# without anybody having to work it out from the names.
		system_pages = pages
		plugin_pages = []
		for endpoint in client.API.gui_endpoints():
			plugin_pages.append({
				"url": f"/public/{endpoint.key}", "label": endpoint.gui,
				"description": endpoint.description,
				"auth": endpoint.authed,
				# A plugin that named no icon gets the fallback dot rather
				# than a gap - see webicons.svg().
				"icon": endpoint.icon})

		# Named, and in the order they are shown.
		groups = [
			{"title": "From plugins", "pages": plugin_pages},
			{"title": "This panel", "pages": system_pages},
		]
		pages = plugin_pages + system_pages
		for page in pages:
			page["icon"] = svg(page.get("icon", ""))

		# Things worth a button rather than a page. Anything destructive is
		# marked so the template can make it look like what it is.
		actions = []
		# A plugin's own actions first. They are the ones somebody added on
		# purpose; the client's are always there and always the same.
		for endpoint in client.API.action_endpoints():
			actions.append({"url": f"/public/{endpoint.key}",
							"label": endpoint.action, "danger": endpoint.danger})

		# The logs, as downloads rather than as actions.
		#
		# An action posts and shows what came back; a log is a file the browser
		# has to save, so these are links. `previous` is the one before
		# latest.log, which is the one worth having after a restart has already
		# rotated the interesting one away.
		actions += [
			{"url": "/logs/latest", "label": "Latest log", "danger": False,
			 "icon": "file-document", "download": True},
			{"url": "/logs/previous", "label": "Previous log", "danger": False,
			 "icon": "file-document", "download": True},
			{"url": "/logs/crash", "label": "Crash log", "danger": False,
			 "icon": "alert", "download": True},
			{"url": "/logs/startup", "label": "Startup log", "danger": False,
			 "icon": "power", "download": True},
		]

		actions += [
			{"url": "/ping", "label": "Ping", "danger": False,
			 "icon": "check-network"},
			{"url": "/update/check", "label": "Check for an update",
			 "danger": False, "icon": "download"},
			{"url": "/restart", "label": "Restart", "danger": True,
			 "icon": "restart"},
			{"url": "/terminate", "label": "Shut down", "danger": True,
			 "icon": "power"},
		]
		for entry in actions:
			entry["icon"] = svg(entry.get("icon", ""), 18)

		# Who else can reach this panel.
		#
		# Named rather than counted: "3 approved devices" tells somebody
		# nothing, and the useful question standing in a kitchen is whether the
		# phone in your hand is one of them.
		users = []
		try:
			for entry in client.USERS.all_users():
				users.append({"name": entry.name,
							  "current": bool(user and entry.name == user.name)})
		except Exception as e:
			client.log("debug", f"[Index] Could not list users: {e}")

		return render_template("index.html", pages=pages, groups=groups,
							   actions=actions,
							   users=users, token=token,
							   panel=client.panel_name(),
							   device=user.name if user else "this device"), 200

	@app.route("/font/<path:name>", methods=["GET"])
	def font_file(name):
		"""
		One of the panel's font files.

		Not authed: a typeface is not information, and the pages that need it
		include the login screen - a browser refused the font would render the
		approval flow in something else entirely.

		By basename only, and only .ttf, since this reads from a folder inside
		the install.
		"""
		from pathlib import Path as _Path
		safe = _Path(str(name)).name
		if not safe.endswith(".ttf"):
			return {"request": "Failed", "reason": "Not a font."}, 404
		folder = _Path(__file__).resolve().parent / "assets" / "fonts"
		if not (folder / safe).is_file():
			return {"request": "Failed", "reason": "No such font."}, 404
		return send_from_directory(str(folder), safe,
								   mimetype="font/ttf",
								   max_age=60 * 60 * 24 * 30)

	@app.route("/quiet/<what>/<state>", methods=["GET"])
	def set_quiet(what, state):
		"""
		Do not disturb and mute, from anywhere.

		A route rather than a toggle: the dashboard already knows which way it
		is, so it asks for the state it wants. Two phones pressing at once
		then agree, where a toggle would leave them arguing.
		"""
		log()
		err = auth()
		if err: return err

		wanted = str(state).lower() in ("on", "1", "true", "yes")
		try:
			if what == "dnd":
				client.call_on_ui(lambda: client.set_do_not_disturb(wanted))
			elif what == "mute":
				client.call_on_ui(lambda: client.set_sounds_muted(wanted))
			else:
				return {"request": "Failed",
						"reason": f"No quiet mode called '{what}'."}, 404
		except Exception as e:
			return {"request": "Failed", "reason": str(e)}, 500

		label = "Do not disturb" if what == "dnd" else "Sounds"
		return answered({"request": "Set",
						 what: "on" if wanted else "off",
						 "what": f"{label} is now {'on' if wanted else 'off'}"})

	@app.route("/say", methods=["GET", "POST"])
	def say_something():
		"""
		Read a message out on the panel, or show it if it cannot speak.

		GET /say?token=...&from=Kitchen&message=Dinner is ready

		With no message it serves the form. The panel answers rather than the
		phone, which is the point - somebody in another room hears it.
		"""
		log()
		err = auth()
		if err: return err

		# `_token()`, which also reads the cookie.
		#
		# This used to look in the query string and the header only. An
		# approved phone browses on its cookie and puts no ?token= on the
		# address bar, so `token` was EMPTY on exactly the devices this is
		# rendered for - which passes `auth()` (it checks the cookie) and
		# then fails every permission check and writes an empty token into
		# every link on the page.
		token = _token()
		user = request.environ.get("ha.user")
		message = str(request.values.get("message") or "").strip()
		sender = str(request.values.get("from") or "").strip()

		if not message:
			try:
				names = [u.name for u in client.USERS.all_users()]
			except Exception:
				names = []
			voices, current = [], ""
			try:
				entry = client.SETTINGS.audio.speech.tts_voice
				voices = list(entry.options)
				current = str(entry.value)
			except Exception:
				pass
			return render_template(
				"say.html", users=names, token=token, voices=voices,
				voice=current,
				panel=client.panel_name(),
				device=user.name if user else ""), 200

		if not sender:
			sender = user.name if user else "Somebody"
		spoken = f"{sender} said {message}"

		# Said if it can, shown if it cannot.
		#
		# say() returns whether anything came out, so quiet mode and a missing
		# voice are the same answer - and neither should mean the message is
		# lost. answer() puts it on screen at panel size.
		# A voice, for this message only.
		#
		# Put back afterwards rather than left: somebody trying a voice from a
		# phone has not decided to change the panel's, and finding it different
		# tomorrow would be a setting changed by accident.
		voice = str(request.values.get("voice") or "").strip()
		previous = ""
		if voice:
			try:
				previous = str(client.SETTINGS.audio.speech.tts_voice.value)
				if voice != previous:
					client.SETTINGS.audio.speech.tts_voice.value = voice
				else:
					previous = ""
			except Exception as e:
				client.log("debug", f"[Say] Could not set the voice: {e}")
				previous = ""

		heard = False
		try:
			heard = bool(client.say(spoken, thread=False))
		except Exception as e:
			client.log("warning", f"[Say] Could not speak: {e}")

		if previous:
			try:
				client.SETTINGS.audio.speech.tts_voice.value = previous
			except Exception:
				pass

		if not heard:
			try:
				# speak="" on purpose: this branch is reached BECAUSE speech
				# did not happen, and answer() speaks its lines by default.
				client.call_on_ui(lambda: client.answer(
					"message-text", sender, [message], tint="#5ac8fa",
					speak=""))
			except Exception as e:
				return answered({"request": "Failed", "reason": str(e)}, 500)

		return answered({"request": "Said",
						 "from": sender,
						 "message": message,
						 "how": "spoken" if heard else "shown on the panel",
						 "what": f"The panel {'said' if heard else 'showed'} "
								 f"your message."})

	@app.route("/dashboard/state", methods=["GET"])
	def dashboard_state():
		"""
		Everything the dashboard shows, in one request.

		One round trip rather than six: this is polled every few seconds from a
		phone that may be on the far side of a house, and six requests is six
		chances for one of them to be the slow one.

		Every part is guarded on its own. A machine with no Bluetooth should
		show the rest of the dashboard, not an error.
		"""
		log()
		err = auth()
		if err: return err

		from urllib.parse import urlparse

		state = {"panel": client.panel_name()}

		try:
			state["uptime"] = int(time.time() - client.STARTED_AT)
		except Exception:
			state["uptime"] = 0

		try:
			page = client.PAGE
			state["page"] = {"key": page.name if page else "",
							 "label": (page.name or "").lstrip("#")
										.replace("_", " ") if page else ""}
		except Exception:
			state["page"] = {"key": "", "label": ""}

		# Wi-Fi
		try:
			from src.system import wifi
			connection = wifi.current()
			state["wifi"] = {
				"available": wifi.available(),
				"connected": connection is not None,
				"ssid": connection.ssid if connection else "",
				"signal": connection.signal if connection else 0,
			}
		except Exception:
			state["wifi"] = {"available": False, "connected": False,
							 "ssid": "", "signal": 0}

		# Bluetooth: everything connected, not just the one the panel shows.
		try:
			from src.system import bluetooth
			snapshot = bluetooth.snapshot()
			state["bluetooth"] = {
				"available": not bluetooth.missing(),
				"powered": bool(snapshot.powered),
				"devices": [
					{"name": d.label,
					 "battery": d.battery if d.has_battery else None}
					for d in snapshot.devices if d.connected
				],
			}
		except Exception:
			state["bluetooth"] = {"available": False, "powered": False,
								  "devices": []}

		try:
			# `muted` is the panel's own sounds; `mic` is the microphone
			# itself, muted at the mixer. Two different things, and a
			# dashboard that showed only the first would say a room was
			# private when it was not.
			state["quiet"] = {"dnd": client.do_not_disturb(),
							  "muted": client.sounds_muted(),
							  "mic_available": client.mic_mute_available(),
							  "mic_muted": client.mic_muted()}
		except Exception:
			state["quiet"] = {"dnd": False, "muted": False,
							  "mic_available": False, "mic_muted": False}

		try:
			# `latest` as well as the wording, so the dashboard can tell
			# whether a re-check found a different commit from the one its
			# button was offering.
			latest = getattr(client, "UPDATE_LATEST", None)
			state["update"] = {
				"available": bool(client.UPDATE_AVAILABLE),
				"detail": str(getattr(client, "UPDATE_DETAIL", "") or ""),
				"latest": latest if isinstance(latest, dict) else None,
			}
		except Exception:
			state["update"] = {"available": False, "detail": "", "latest": None}

		try:
			state["brightness"] = int(client.DIMMER.brightness())
		except Exception:
			state["brightness"] = -1

		try:
			state["bookmarks"] = [
				# `base` so the dashboard can open it locked, the same way the
				# widget and the tile do.
				{"url": b.url, "label": b.label, "host": b.host,
				 "icon": b.icon,
				 "base": f"{urlparse(b.url).scheme}://{urlparse(b.url).netloc}"
						 if urlparse(b.url).netloc else ""}
				for b in client.BOOKMARKS.all()[:24]
			]
		except Exception:
			state["bookmarks"] = []

		# Notifications, newest first.
		state["notifications"] = []
		try:
			if client.public.has("notification_history"):
				# (widget, icon, title, body, timestamp), inserted at 0 - so
				# the list is already newest first. See
				# NotificationHistory.add().
				history = client.public.notification_history.items
				for _widget, icon, title, body, when in list(history)[:12]:
					state["notifications"].append({
						"title": str(title or ""),
						"body": str(body or ""),
						"when": when.strftime("%H:%M") if when else "",
					})
		except Exception:
			pass

		return state, 200

	@app.route("/quick", methods=["GET"])
	def open_quick_settings():
		"""
		Open the panel's quick settings, from a phone.

		The panel's own gesture is a swipe from the top edge, which is no use
		from across the room - and everything in that panel (brightness,
		volume, Wi-Fi, do not disturb) is exactly what somebody wants to change
		without walking over.
		"""
		log()
		err = auth()
		if err: return err
		try:
			client.call_on_ui(client.QUICK_SETTINGS.open_panel)
		except Exception as e:
			return {"request": "Failed", "reason": str(e)}, 500
		return answered({"request": "Opened", "what": "quick settings"})

	@app.route("/ping", methods=["GET"])
	def ping():
		"""
		Is the panel there, and what is it.

		The index has offered a Ping button since it was written, but the route
		was never added - so it answered with Flask's own 404 page, which the
		index showed as a screenful of raw HTML.
		"""
		log()
		err = auth()
		if err: return err
		import time as _time
		started = getattr(client, "STARTED_AT", None)
		uptime = int(_time.time() - started) if started else 0
		hours, rest = divmod(uptime, 3600)
		minutes = rest // 60
		user = request.environ.get("ha.user")
		return {"request": "Success",
				"alive": True,
				"app": APP_NAME,
				"page": getattr(client.PAGE, "name", None) if client.PAGE else None,
				"uptime_seconds": uptime,
				"uptime": f"{hours}h {minutes}m" if hours else f"{minutes}m",
				"device": user.name if user else "this device"}, 200

	@app.route("/backlight", methods=["GET"])
	def backlight():
		"""
		What is driving the screen brightness, and what else could.

		`survey=1` probes every backend rather than reporting the chosen one -
		slower, because it includes a ddcutil detect, but it is the difference
		between "using the overlay" and knowing why.
		"""
		log()
		err = auth()
		if err: return err
		dimmer = getattr(client, "DIMMER", None)
		if dimmer is None:
			return {"request": "Failed", "reason": "No dimmer yet."}, 409

		body = {"request": "Success"}
		body.update(dimmer.describe())
		if _coerce(request.args.get("survey", "false")) is True:
			from src.ui.backlight import BacklightController
			body["survey"] = BacklightController.survey()
		return body, 200

	@app.route("/users", methods=["GET"])
	def users_list():
		"""Who can own something, for a picker rather than a text field."""
		log()
		err = auth()
		if err: return err
		return {"request": "Success", "users": client.USERS.names()}, 200

	## DEVICE APPROVAL
	@app.route("/access/wait", methods=["GET"])
	def access_wait():
		"""
		The page a browser lands on when it has no approved token.

		Asks for access on the visitor's behalf, waits, and sends them on to
		wherever they were going. No auth, by definition - this is what a
		device does before it has any.
		"""
		log()
		target = request.args.get("next") or "/docs"
		token = (request.args.get("token") or "").strip()

		if not token:
			name = request.user_agent.browser or "Browser"
			platform = request.user_agent.platform or ""
			label = f"{name} on {platform}".strip() if platform else name
			token = client.USERS.request_access(label, request.remote_addr or "").token

		return render_template("access_wait.html", token=token, next=target), 200

	@app.route("/access/request", methods=["GET", "POST"])
	def request_access():
		"""
		A new device asking to be let in. No auth, by definition.

		Returns a token immediately; the device polls /access/state with it
		until somebody on the panel answers.
		"""
		log()
		name = (request.args.get("name") or "").strip() or "Unnamed device"
		pending = client.USERS.request_access(name, request.remote_addr or "")
		return {"request": "Success", "token": pending.token, "state": "pending"}, 202

	@app.route("/access/name", methods=["GET", "POST"])
	def access_name():
		"""
		The page a device is sent to when the panel let it name itself.

		Unauthenticated by the usual rule, because the token is approved but
		the device has nothing else yet - and it can only name the token it
		already holds.
		"""
		log()
		token = (request.args.get("token") or "").strip()
		user = client.USERS.get(token)
		if user is None:
			return redirect("/access/wait")
		if getattr(user, "awaiting_decision", False):
			# Arrived early, or by a stale link. Sent back to wait rather than
			# allowed to name itself out from under the panel.
			return redirect("/access/wait?" + urlencode(
				{"token": token,
				 "next": _strip_token(request.args.get("next") or "/")}))

		name = (request.args.get("name") or "").strip()
		if name:
			client.USERS.rename(token, name)
			client.simple_notify("account-check", "Users",
								 f"'{name}' named themselves.")
			# Stripped, not appended to. A `next` still carrying the old
			# token would win over the one added here and send the browser
			# back into naming forever.
			target = _strip_token(request.args.get("next") or "/")
			joiner = "&" if "?" in target else "?"
			return redirect(f"{target}{joiner}token={token}")

		return render_template("access_name.html", token=token,
							   suggested=user.name,
							   next=request.args.get("next") or "/"), 200

	@app.route("/access/state", methods=["GET"])
	def access_state():
		token = (request.args.get("token") or "").strip()
		if not token:
			return {"request": "Failed", "reason": "No token given"}, 400
		state = client.USERS.state_of(token)
		user = client.USERS.get(token)
		# Still approved, but not yet cleared to go anywhere: the panel is
		# mid-question about who names this device.
		if state == "approved" and client.USERS.awaiting_decision(token):
			state = "deciding"
		return {"request": "Success", "state": state,
				"name": user.name if user else "",
				# The waiting page sends them on to name themselves rather
				# than straight to where they were going.
				"needs_name": client.USERS.needs_name(token)}, 200

	@app.after_request
	def remember_token(response):
		"""
		Put the accepted token in a cookie.

		Only after it has been checked, so an unknown one is never stored -
		and only when it is not already the cookie's value, so an ordinary
		page load does not carry a Set-Cookie it does not need.
		"""
		token = request.environ.get("ha.set_token")
		if token and request.cookies.get(TOKEN_COOKIE) != token:
			response.set_cookie(
				TOKEN_COOKIE, token,
				max_age=TOKEN_COOKIE_AGE,
				path="/",
				httponly=True,
				# Lax rather than Strict: a device following a link from a
				# message app is still the same device, and Strict would drop
				# the cookie and start the whole request-access dance again.
				samesite="Lax",
			)
		return response

	@app.route("/logs/<which>", methods=["GET"])
	def download_log(which):
		"""
		One log file, as a download.

		Four names rather than a path: `latest`, `previous`, `crash` and
		`startup`. A route that took a filename would be a way to read anything
		in the folder, and there is nothing else in there worth naming.

		`previous` is the newest rotated log - the one before latest.log -
		found by modified time rather than by its name, because the name comes
		from the first line of the log before it.
		"""
		log()
		err = auth()
		if err: return err

		import re as _re
		from datetime import datetime as _datetime
		from src.constants import LOG_DIR, LAUNCHER_LOG

		wanted = str(which or "").lower()

		if wanted == "startup":
			path = LAUNCHER_LOG
		elif wanted == "crash":
			path = LOG_DIR / "crash.log"
		elif wanted == "latest":
			path = LOG_DIR / "latest.log"
		elif wanted == "previous":
			try:
				rotated = sorted(
					(entry for entry in LOG_DIR.glob("*.log")
					 if entry.is_file() and entry.name not in (
						 "latest.log", "crash.log", "startup.log")),
					key=lambda entry: entry.stat().st_mtime, reverse=True)
			except OSError:
				rotated = []
			if not rotated:
				return {"request": "Failed",
						"reason": "No log before this one yet."}, 404
			path = rotated[0]
		else:
			return {"request": "Failed",
					"reason": f"No log called '{which}'."}, 404

		if not path.is_file():
			return {"request": "Failed",
					"reason": f"{path.name} is not here yet."}, 404

		try:
			body = path.read_bytes()
		except OSError as e:
			return {"request": "Failed",
					"reason": f"Could not read {path.name}: {e}"}, 500

		# The panel's name and the date on it, so four of these in a downloads
		# folder are still tellable apart a week later.
		stamp = _datetime.fromtimestamp(
			path.stat().st_mtime).strftime("%Y%m%d-%H%M")
		safe = _re.sub(r"[^A-Za-z0-9_-]+", "-", client.panel_name() or "panel")
		filename = f"{safe}-{wanted}-{stamp}.log"

		client.log("info", f"[Logs] {path.name} downloaded as {filename}.")
		return Response(body, mimetype="text/plain; charset=utf-8", headers={
			"Content-Disposition": f'attachment; filename="{filename}"',
			"Content-Length": str(len(body)),
			"Cache-Control": "no-store",
		})

	## THE PANEL'S OWN WEB ASSETS
	@app.route("/web", methods=["GET"])
	def core_web_asset():
		"""
		The styling and script behind the panel's own pages.

		Unauthenticated, like /docs and for the same reason: this is the
		stylesheet for a page anybody can already open, and requiring a token
		would mean the browser could not fetch it after following the link.

		The name is checked against the folder listing rather than joined
		onto it, so there is no path to traverse out of - see WebAssets.read.
		"""
		from src.webui import core_assets

		return core_assets().serve(name=request.args.get("name", ""))

	## DOCUMENTATION
	@app.route("/docs", methods=["GET"])
	@app.route("/docs/", methods=["GET"])
	@app.route("/docs/<path:page>", methods=["GET"])
	def docs_route(page: str = "index"):
		"""
		The docs/ folder, rendered.

		Deliberately unauthenticated. This is the documentation shipped with
		the app, it exposes nothing the install does not already publish, and
		requiring ?id= would mean the link in Settings could not simply be
		opened in a browser.
		"""
		from src import docs

		if not docs.available():
			return {"request": "Failed",
					"reason": "No docs/ folder in this install."}, 404

		# Raw markdown, for reading it in an editor or piping it somewhere.
		if page.endswith(".md"):
			path = docs.resolve(page)
			if path is None:
				return {"request": "Failed", "reason": f"No page '{page}'."}, 404
			return path.read_text(encoding="utf-8"), 200, {
				"Content-Type": "text/plain; charset=utf-8"}

		rendered = docs.page(page)
		if rendered is None:
			return {"request": "Failed", "reason": f"No page '{page}'."}, 404
		return rendered, 200, {"Content-Type": "text/html; charset=utf-8"}

	## PLUGIN ENDPOINTS
	@app.route("/plugins", methods=["GET"])
	def list_plugins():
		"""
		What is loaded, what is waiting, and what can safely be removed.

		There was no way to ask this at all, so every other plugin call was a
		guess at a key.
		"""
		log()
		if not client.BUILT:
			return {"request": "Failed", "reason": "The Application is still building..."}, 503
		err = auth()
		if err: return err

		loaded = []
		for plugin, key in client.PLUGIN.get_plugins():
			loaded.append({
				"key":        key,
				"name":       client.PLUGIN.plugin_name(key),
				"loaded":     True,
				"dependants": client.PLUGIN.get_dependants(key),
				"can_unload": client.PLUGIN.can_unload(key),
				"endpoints":  client.API.endpoints_for(key),
			})

		pending = []
		for item in client.PLUGIN.pending_plugins():
			key = getattr(item, "key", None) or str(item)
			pending.append({
				"key":          key,
				"loaded":       False,
				"requirements": client.PLUGIN.plugin_requirements(key),
			})

		if _wants_html():
			# A browser gets the section; a script gets the same JSON it
			# always got. One route rather than two paths to the same list,
			# so they cannot come to disagree about what is installed.
			token = _token()
			refusal = _may_plugins()
			if refusal:
				return refusal
			from src import webplugins
			# A note carried back from a failed action, so the reason survives
			# the reload that re-reads the real state.
			note = (request.args.get("note") or "").strip()
			return webplugins.installed_page(_plugin_entries(), token,
											 message=note, bad=bool(note))

		return {"request": "Success", "loaded": loaded, "pending": pending}, 200

	## -- the Plugins section -------------------------------------------------

	def _may_plugins():
		"""The refusal, or None. Every route in this section starts here."""
		token = _token()
		try:
			if client.USERS.may(token, "plugins"):
				return None
		except Exception as e:
			client.log("error", f"[Plugins] Permission check failed: {e}")
		if _wants_html():
			return webplugins_denied(token), 403
		return {"request": "Failed",
				"reason": "This device does not have the 'plugins' "
						  "permission."}, 403

	def webplugins_denied(token):
		from src.webui import page
		return page(
			"Plugins", '<section class="empty">This device is approved, but '
			'does not have permission to manage plugins. A panel user with '
			'access can grant it in Settings &rarr; Users.</section>',
			token=token, heading="Plugins",
			blurb="Not permitted on this device.")

	def _pending_version(item):
		try:
			return str((item.config.get("plugin") or {}).get("version") or "")
		except Exception:
			return ""

	def _taken_keys():
		"""
		{key: folder} for every plugin the panel knows about.

		Loaded, stopped and already-conflicting alike. All three hold the key
		as far as a scan is concerned, so an upload claiming one of them can
		never load whichever list it is currently on.
		"""
		from pathlib import Path as _Path
		taken = {}
		try:
			for plugin, key in client.PLUGIN.get_plugins():
				path = getattr(plugin, "path", None) or client.PLUGIN.registered.get(key)
				taken[key] = _Path(str(path)).name if path else key
			for item in client.PLUGIN.pending_plugins():
				key = getattr(item, "key", None)
				if key and key not in taken:
					taken[key] = _Path(str(getattr(item, "path", ""))).name or key
			for item in client.PLUGIN.conflicting_plugins():
				taken.setdefault(item.key, _Path(str(item.blocked_by)).name)
		except Exception as e:
			client.log("debug", f"[Plugins] Could not list keys in use: {e}")
		return taken

	def _plugin_entries():
		"""Every plugin the panel knows about, loaded or not."""
		from pathlib import Path
		from src.constants import INSTALL_ROOT

		# INSTALL_ROOT, not the working directory.
		#
		# `os.getcwd()` is wherever the process happened to be started from -
		# a launcher, a service unit, a shell somewhere else - and when it is
		# not the install root this test silently answers False for every
		# plugin. Nothing looks wrong: bundled plugins simply stop being
		# bundled, which showed up as a download refusing them for not being
		# in the plugins folder, where of course they never were.
		#
		# INSTALL_ROOT is derived from src/constants.py's own location, so it
		# is right wherever the panel was started.
		bundled_root = (INSTALL_ROOT / "src" / "assets" / "bundled").resolve()
		entries = []
		seen = set()
		for plugin, key in client.PLUGIN.get_plugins():
			# The loader's own record of where the folder is.
			#
			# `get_plugins()` hands back the plugin INSTANCES, and the loader
			# never puts a path on one - it sets `settings`, `config` and
			# `client` and nothing else. So `getattr(plugin, "path", ...)`
			# was None for every loaded plugin, which made all of them look
			# unbundled AND gave them an empty folder name. A download then
			# refused every one of them for not being where it had not looked.
			#
			# `registered` is written beside `plugins` at load time and is the
			# same key, so there is nothing to line up.
			path = client.PLUGIN.registered.get(key)
			if path is None:
				path = (getattr(plugin, "path", None)
						or getattr(plugin, "directory", None))
			bundled = False
			try:
				bundled = bundled_root in Path(str(path)).resolve().parents
			except Exception:
				pass
			seen.add(key)
			entries.append({
				"key": key,
				"name": client.PLUGIN.plugin_name(key) or key,
				"loaded": True,
				"bundled": bundled,
				# The FOLDER, which is not the key. `plugins/AnimePlugin`
				# holds the plugin `anime`, and a download has to find the
				# first from the second.
				"folder": Path(str(path)).name if path else "",
				"version": plugin.config.get_path("plugin.version", "") if
						   hasattr(plugin, "config") else "",
				"icon": plugin.config.get_path("plugin.icon", "puzzle") if
						hasattr(plugin, "config") else "puzzle",
				"description": plugin.config.get_path("plugin.description", "") if
							   hasattr(plugin, "config") else "",
				"dependants": client.PLUGIN.get_dependants(key),
			})
		for item in client.PLUGIN.pending_plugins():
			key = getattr(item, "key", None) or str(item)
			if key in seen:
				continue
			entries.append({"key": key, "name": getattr(item, "name", key),
							"loaded": False, "bundled": False,
							"version": _pending_version(item),
							"folder": Path(str(getattr(item, "path", ""))).name,
							"icon": getattr(item, "icon", None) or "puzzle",
							"description": "Installed and not running."
										   if not getattr(item, "missing", None)
										   else "Waiting on packages: "
												+ ", ".join(item.missing),
							"dependants": []})
		# Folders that will never load because their key is taken. Shown, and
		# shown as unloadable - the folder is on disk and looks installed, so
		# leaving it out answers "where is my plugin" with silence.
		# Guarded like the rest of this function. A whole page failing because
		# one of its three lists could not be built is a worse answer than the
		# page without that list.
		try:
			clashes = client.PLUGIN.conflicting_plugins()
		except Exception as e:
			client.log("debug", f"[Plugins] Could not list conflicts: {e}")
			clashes = []
		for item in clashes:
			owner = ("a plugin that ships with the app"
					 if item.bundled_winner
					 else f"'{Path(str(item.blocked_by)).name}'")
			entries.append({
				"key": item.key,
				"name": item.name,
				"loaded": False,
				"blocked": True,
				"bundled": False,
				"version": _pending_version(item),
				"folder": item.folder,
				"icon": item.icon or "alert",
				"description": (f"Cannot load. The key '{item.key}' already "
								f"belongs to {owner}. Change the key in this "
								f"plugin's plugin.toml, or remove the folder."),
				"dependants": [],
			})

		entries.sort(key=lambda e: (e["bundled"], e["name"].lower()))
		return entries

	@app.route("/plugins/new", methods=["GET", "POST"])
	def plugins_new():
		log()
		err = auth()
		if err: return err
		err = _may_plugins()
		if err: return err

		from src.plugin.skeleton import build, as_zip, BadSkeleton
		from src import webplugins
		token = _token()

		if request.method == "GET":
			return webplugins.create_page(token)

		values = {k: (request.form.get(k) or "").strip()
				  for k in ("name", "key", "description", "settings", "version")}
		try:
			folder, files = build(values["name"], values["key"],
								  values["description"], values["settings"],
								  values["version"])
		except BadSkeleton as e:
			return webplugins.create_page(token, message=str(e), bad=True,
										  values=values), 400

		# Refused here rather than at upload. A skeleton with a key that is
		# already taken is a folder somebody writes code in and then cannot
		# install, and the cost of saying so now is one field.
		import tomllib as _tomllib
		chosen = str((_tomllib.loads(files["plugin.toml"].decode())
					  .get("plugin") or {}).get("key") or "")
		owner = _taken_keys().get(chosen)
		if owner and owner != folder:
			return webplugins.create_page(
				token, values=values, bad=True,
				message=(f"The key '{chosen}' already belongs to the plugin in "
						 f"'{owner}'. Pick another - the first folder scanned "
						 f"wins, so a second plugin with this key would never "
						 f"load.")), 409

		client.log("info", f"[Plugins] Skeleton for '{folder}' downloaded.")
		return Response(
			as_zip(files), mimetype="application/zip",
			headers={"Content-Disposition":
					 f'attachment; filename="{folder}.zip"'})

	@app.route("/plugins/upload", methods=["GET", "POST"])
	def plugins_upload():
		log()
		err = auth()
		if err: return err
		err = _may_plugins()
		if err: return err

		from src.plugin import install as installer
		from src.plugin.install import Refusal
		from src.plugin.staging import report
		from src import webplugins
		token = _token()
		user = client.USERS.get(token)
		who = user.name if user else "a device"

		if request.method == "GET":
			return webplugins.upload_page(token)

		upload = request.files.get("file")
		if upload is None or not upload.filename:
			return webplugins.upload_page(
				token, message="Choose a zip file first.", bad=True), 400

		try:
			data = upload.read()
			files, found = installer.read_zip(
				data, name=(request.form.get("folder") or "").strip())
			plan = installer.plan(files, found or upload.filename,
								  client.asset("FOLDER", "plugins"),
								  version=(request.form.get("version") or "").strip(),
								  taken=_taken_keys())
		except Refusal as e:
			return webplugins.upload_page(token, message=str(e), bad=True), 400
		except Exception as e:
			client.log("error", f"[Plugins] Upload failed: {e}")
			return webplugins.upload_page(
				token, message="That upload could not be read.", bad=True), 400

		loaded = client.PLUGIN.has_plugin(plan.key)
		staged = client.PLUGIN_STAGING.stage(plan, who)
		return webplugins.preview_page(
			report(plan, loaded=loaded), staged.token, token)

	@app.route("/plugins/upload/apply", methods=["POST"])
	def plugins_upload_apply():
		log()
		err = auth()
		if err: return err
		err = _may_plugins()
		if err: return err

		from src.plugin.install import Refusal, apply as apply_plan
		from src.plugin.staging import report
		from src import webplugins
		token = _token()
		user = client.USERS.get(token)
		who = user.name if user else "a device"

		staged, refusal = client.PLUGIN_STAGING.claim(request.form.get("staged"))
		if staged is None:
			return webplugins.upload_page(token, message=refusal, bad=True), 409

		plan = staged.plan
		# A plugin that is not here yet needs somebody in the room. An update
		# to one already installed does not - that decision was made when it
		# was first accepted, and asking again for every bug fix trains people
		# to press yes without reading.
		if plan.is_new:
			client.PLUGIN_GATE.ask(plan, token, who)
			return webplugins.waiting_page(plan.name, token)

		try:
			summary = apply_plan(plan)
		except Refusal as e:
			return webplugins.upload_page(token, message=str(e), bad=True), 500

		# The files are on disk; the running plugin is not them. Offered
		# rather than done - see webplugins.result_page.
		loaded = client.PLUGIN.has_plugin(plan.key)
		if not loaded:
			client.PLUGIN.discover(plan.target)
		note = f"{plan.name} updated - {summary['written']} files written."
		client.log("info", f"[Plugins] {note}")
		return webplugins.result_page(
			plan.name, plan.key, note,
			"reload" if loaded else ("load" if summary["written"] else ""),
			token)

	@app.route("/plugins/<plugin_key>/download", methods=["GET"])
	def plugins_download(plugin_key):
		"""
		The plugin as it is on disk right now, zipped.

		The point of it is a round trip: take what is installed, change
		something, send it back. Without this the only copy of a plugin that
		has been updated three times over the network is the one on the panel,
		and the only way to get at it is a keyboard - which is the problem
		this whole section exists to remove.
		"""
		log()
		err = auth()
		if err: return err
		err = _may_plugins()
		if err: return err

		from src.plugin.install import pack, Refusal, safe_folder_name

		def refuse(reason, code=404):
			# Logged, not only returned.
			#
			# The success path logged and the failure path did not, so a
			# download that failed left a request line in the log and nothing
			# after it - the log could say somebody had tried and not what
			# happened, which is the half that matters.
			client.log("warning", f"[Plugins] '{plugin_key}' not downloaded: "
								  f"{reason}")
			return {"request": "Failed", "reason": reason}, code

		entry = next((e for e in _plugin_entries()
					  if e["key"] == plugin_key), None)
		if entry is None:
			return refuse(f"No plugin '{plugin_key}'.")
		# Bundled plugins download too.
		#
		# They were refused on the grounds that a copy of something the app
		# ships would be silently replaced by the next update - true, and not a
		# reason to withhold it. A bundled plugin is the best worked example
		# there is of how to write one, and reading it meant a keyboard on the
		# panel, which is the exact problem this section exists to remove.
		#
		# The zip says so in its name instead, so what somebody is holding is
		# obvious a week later.
		from pathlib import Path

		from src.constants import INSTALL_ROOT

		if entry.get("bundled"):
			# Same root the entry was classified against - see
			# _plugin_entries(). Deriving it a second way is how the two
			# disagree.
			root = (INSTALL_ROOT / "src" / "assets" / "bundled").resolve()
		else:
			root = client.asset("FOLDER", "plugins")

		folder = entry.get("folder") or ""
		name = safe_folder_name(folder)
		if not name:
			return refuse(f"'{plugin_key}' has no folder name on record "
						  f"(got {folder!r}).")
		if not (root / name).is_dir():
			where = "bundled with the app" if entry.get("bundled") \
				else "in the plugins folder"
			return refuse(f"'{plugin_key}' is not {where}: "
						  f"{root / name} is not a folder.")

		try:
			data = pack(root / name)
		except Refusal as e:
			return refuse(str(e), 500)

		version = entry.get("version") or ""
		filename = f"{name}-{version}.zip" if version else f"{name}.zip"
		if entry.get("bundled"):
			# Named for what it is. A bundled plugin unpacked back into the
			# plugins folder would be loaded twice under one key, and a file
			# called `CoreWidgetsBundle-1.2.zip` gives nobody a reason to
			# think twice about it.
			filename = f"{name}-bundled-copy.zip"
		client.log("info", f"[Plugins] '{plugin_key}' downloaded as {filename}.")
		return Response(data, mimetype="application/zip",
						headers={"Content-Disposition":
								 f'attachment; filename="{filename}"'})

	@app.route("/plugins/upload/status", methods=["GET"])
	def plugins_upload_status():
		"""Whether the panel has answered yet. Polled by the waiting page."""
		err = auth()
		if err: return err
		err = _may_plugins()
		if err: return err
		found = client.PLUGIN_GATE.status(request.args.get("name") or "")
		if found is None:
			return {"state": "gone",
					"detail": "That request is no longer waiting."}, 200
		return found, 200

	@app.route("/plugins/installed", methods=["GET"])
	def plugins_installed():
		"""
		Where the waiting page lands once the panel has agreed.

		The plugin is on disk and not running. It is made discoverable here
		rather than at install time, because the install happens on the UI
		thread inside a dialog callback and the loader's scan is not
		something to do from there.
		"""
		err = auth()
		if err: return err
		err = _may_plugins()
		if err: return err

		from src import webplugins
		token = _token()
		name = (request.args.get("name") or "").strip()
		found = client.PLUGIN_GATE.status(name)
		if not found or found.get("state") != "approved":
			return webplugins.upload_page(
				token, message="That plugin was not installed.", bad=True), 409

		folder = client.asset("FOLDER", "plugins") / name
		key = client.PLUGIN.discover(folder) or found.get("key") or ""
		client.PLUGIN_GATE.forget(name)
		return webplugins.result_page(
			name, key, f"{name} installed.",
			"reload" if client.PLUGIN.has_plugin(key) else "load", token)

	@app.route("/plugins/<plugin_key>/<endpoint>", methods=["GET"])
	def plugin_endpoint(plugin_key, endpoint):
		log()

		if not client.BUILT:
			return {"request": "Failed", "reason": "The Application is still building..."}, 503
		err = auth()
		if err: return err

		if not plugin_key or not endpoint:
			return {"request": "Failed", "reason": "No Plugin Key Given!"}, 404

		is_loaded  = client.PLUGIN.has_plugin(plugin_key)
		is_pending = any(getattr(p, "key", None) == plugin_key
						 for p in client.PLUGIN.pending_plugins())

		# `load` and `install` act on plugins that are by definition NOT
		# loaded, so the has_plugin() gate that used to wrap everything made
		# them unreachable.
		if not is_loaded and not is_pending:
			return {"request": "Failed", "reason": f"No Plugin '{plugin_key}' loaded or pending."}, 404

		def _threaded(name, work):
			Thread(target=work, name=name, daemon=True).start()

		match endpoint:
			case "info":
				if not is_loaded:
					return {"request": "Success", "key": plugin_key, "loaded": False,
							"pending": True,
							"requirements": client.PLUGIN.plugin_requirements(plugin_key)}, 200
				return {
					"request":       "Success",
					"key":           plugin_key,
					"name":          client.PLUGIN.plugin_name(plugin_key),
					"loaded":        True,
					"dependencies":  client.PLUGIN.get_dependencies(plugin_key),
					"dependants":    client.PLUGIN.get_dependants(plugin_key),
					"can_unload":    client.PLUGIN.can_unload(plugin_key),
					"registrations": client.PLUGIN.registration_count(plugin_key),
					"endpoints":     client.API.endpoints_for(plugin_key),
					"requirements":  client.PLUGIN.plugin_requirements(plugin_key),
				}, 200

			case "reload":
				if not is_loaded:
					return {"request": "Failed", "reason": f"'{plugin_key}' is not loaded."}, 409
				client.call_on_ui(lambda: client.PLUGIN.reload_plugin(plugin_key))
				return {"request": "Success", "message": "Reload queued."}, 200

			case "unload":
				if not is_loaded:
					return {"request": "Failed", "reason": f"'{plugin_key}' is not loaded."}, 409
				dependants = client.PLUGIN.get_dependants(plugin_key)
				if dependants and request.args.get("force") not in ("1", "true", "yes"):
					# Unloading underneath a dependant leaves it calling into a
					# module that is gone, so this is opt-in rather than silent.
					return {"request": "Failed",
							"reason": f"'{plugin_key}' is required by {dependants}. "
									  "Pass ?force=1 to unload anyway.",
							"dependants": dependants}, 409
				client.call_on_ui(lambda: client.PLUGIN.unload_plugin(plugin_key))
				return {"request": "Success", "message": "Unload queued."}, 200

			case "load":
				if is_loaded:
					return {"request": "Failed", "reason": f"'{plugin_key}' is already loaded."}, 409
				client.call_on_ui(lambda: client.PLUGIN.load_pending_plugin(plugin_key))
				return {"request": "Success", "message": "Load queued."}, 200

			case "install":
				# pip, so it is slow and cannot run on the request thread.
				def install_work():
					ok, message = client.PLUGIN.install_pending(
						plugin_key, log=lambda m: client.log("info", f"[API][install] {m}"))
					client.simple_notify(
						"check" if ok else "error",
						f"Plugin: {plugin_key}",
						message or ("Dependencies installed" if ok else "Install failed"))
					if ok:
						client.call_on_ui(lambda: client.PLUGIN.load_pending_plugin(plugin_key))

				_threaded("__api_plugin_install", install_work)
				return {"request": "Success", "message": "Dependency install started."}, 202

			case "uninstall":
				def uninstall_work():
					ok, message = client.PLUGIN.uninstall_plugin_packages(
						plugin_key, log=lambda m: client.log("info", f"[API][uninstall] {m}"))
					client.simple_notify(
						"check" if ok else "error",
						f"Plugin: {plugin_key}",
						message or ("Dependencies removed" if ok else "Uninstall failed"))

				_threaded("__api_plugin_uninstall", uninstall_work)
				return {"request": "Success", "message": "Dependency removal started."}, 202

			case _:
				known = ["info", "reload", "unload", "load", "install", "uninstall"]
				return {"request": "Failed",
						"reason": f"There is no endpoint ({endpoint}) here ...",
						"available": known}, 404

	@app.route("/public/<endpoint>", methods=["GET", "POST"])
	def registered_endpoint_routing(endpoint):
		if not client.BUILT: return {"request": "Failed", "reason": "The Application is still building..."}, 200

		if endpoint:
			api_endpoint = client.API.get_endpoint(endpoint)
			if api_endpoint and isinstance(api_endpoint, tuple):
				owner, end = api_endpoint
				log("info", f"Registry.{owner}")
				if end.authed:
					err = auth()
					if err: return err

				try:
					# The auth parameter is ours, not the endpoint's. Forwarding
					# wholesale meant every authed endpoint was called with an
					# unexpected id= keyword and raised TypeError, which landed
					# here and read as "the endpoint is broken".
					# request.args wholesale meant every endpoint took an
					# unexpected keyword and raised TypeError, which landed on
					# the caller as a bad-arguments error naming a parameter
					# they never sent. `token` was missed when auth moved off
					# `id`, so the same bug came straight back.
					RESERVED = ("id", "token")
					params = {k: v for k, v in request.args.items()
							  if k not in RESERVED}

					# POST was accepted but the body was thrown away - only the
					# query string ever reached the callback.
					if request.method == "POST":
						body = request.get_json(silent=True)
						if isinstance(body, dict):
							params.update(body)
						elif request.form:
							params.update(request.form.to_dict())

						# Uploads, for endpoints that asked for them. Opt-in:
						# forwarding files to every endpoint would hand each
						# one an unexpected keyword and raise TypeError, which
						# is the same trap `id` and `token` already sprang.
						if getattr(end, "accepts_files", False) and request.files:
							params["files"] = request.files

					return end.call(**params)
				except TypeError as e:
					client.log("warning", f"[backend.registered_endpoint_routing] Bad arguments for '{endpoint}': {e}")
					return {"request":"Failed", "reason":f"Bad arguments for '{endpoint}': {e}"}, 400
				except Exception as e:
					client.log("error", f"[backend.registered_endpoint_routing] Endpoint Call Failed: {e}")
					return {"request":"Failed", "reason":f"Public endpoint failed due to: {e}"}, 500
					
			else:
				log("warning", "Registry.None")
				return {"request":"Failed", "reason":f"No Public endpoint under the name '{endpoint}'"}, 404
		
		return {"request":"Failed"}, 200

	return app