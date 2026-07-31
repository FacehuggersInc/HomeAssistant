import os
import time
import shutil
from threading import Thread

from src.constants import APP_NAME

from urllib.parse import urlencode

from flask import Flask, jsonify, redirect, send_from_directory, request, render_template, make_response

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

def FlaskService(stop_event, client, flask):
	from werkzeug.serving import make_server
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
		template_folder=os.path.join(here, "templates"),
		static_folder=os.path.join(here, "static"),
	)

	@app.context_processor
	def _panel_identity():
		"""
		The panel's name, in every template.

		A context processor rather than a keyword on each render_template():
		there are several pages and adding one is how a heading ends up saying
		"Home Assistant" on a panel somebody named something else. This way a
		new page gets the name without having to remember to ask for it.
		"""
		try:
			return {"panel": client.panel_name()}
		except Exception:
			return {"panel": APP_NAME}

	# AUTH & HELPERS
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

	def log(level:str = "info", extra:str = ""):
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
		if query and query.strip():
			Thread(target=client.STT.pre_processing, args=[query]).start()
			return {"request": "Success"}, 200
		else:
			return {"request": "Failed", "reason": "No Query(q) Given!"}, 404

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
		from src.webui import chrome_css
		marks = []
		try:
			marks = client.BOOKMARKS.all()
		except Exception as e:
			client.log("warning", f"[Bookmarks] Could not list: {e}")
		return render_template("webhome.html", bookmarks=marks,
							   chrome=chrome_css()), 200

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
		from src.webui import chrome_css
		token = request.args.get("token", "")
		return render_template("goto.html", chrome=chrome_css(),
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
		from src.webui import chrome_css
		# The same way every other page here gets it: whatever the browser
		# arrived with. The cookie set at approval means this is usually
		# already in the URL after one visit.
		token = request.args.get("token", "")
		return render_template("clipboard.html", chrome=chrome_css(),
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
			if not getattr(asset, "is_uploadable", False):
				continue
			info = {"key": key, "path": str(asset), "exists": False, "file_count": 0, "size": "0 B", "size_bytes": 0}
			try:
				import os
				if asset.exists():
					info["exists"] = True
					files = [f for f in asset.iterdir() if f.is_file() and not f.name.startswith(".")]
					total = sum(f.stat().st_size for f in files)
					info["file_count"] = len(files)
					info["size_bytes"] = total
					if total < 1024:
						info["size"] = f"{total} B"
					elif total < 1024 * 1024:
						info["size"] = f"{total / 1024:.1f} KB"
					elif total < 1024 ** 3:
						info["size"] = f"{total / (1024 * 1024):.1f} MB"
					else:
						info["size"] = f"{total / (1024 ** 3):.2f} GB"
			except Exception as e:
				client.log("error", f"[backend.upload_index] Upload Failed: {e}")
			uploadable.append(info)

		# Passed through so the page's own links and its POST carry the token
		# that fetched it - the browser has no other way to authenticate.
		token = request.args.get("token", "")
		from src.webui import chrome_css
		return render_template("upload_index.html", assets=uploadable,
							   token=token, chrome=chrome_css())

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

		token = request.args.get("token", "")
		# The shared chrome rather than a copy in the template. Two templates
		# each carrying their own copy of the same CSS is how one of them
		# ended up with no size on its back-button icon, and an SVG with no
		# size fills whatever contains it.
		from src.webui import chrome_css
		return render_template("upload.html", key=key, path=str(path),
							   token=token, chrome=chrome_css())

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

		token = (request.args.get("token")
				 or request.headers.get("X-Client-Token") or "")
		user = request.environ.get("ha.user")

		pages = [
			{"url": "/docs", "label": "Documentation",
			 "description": "Everything about this panel and how to extend it.",
			 "auth": False},
			{"url": "/upload", "label": "Files",
			 "description": "Send files to anything the panel has opened up.",
			 "auth": True},
			{"url": "/goto/page", "label": "Go to",
			 "description": "Change what the panel is showing, or send it to a "
							"web page.",
			 "auth": True},
			{"url": "/clipboard/page", "label": "Clipboard",
			 "description": "Read what is on the panel's clipboard, or put "
							"something on it.",
			 "auth": True},
		]
		for endpoint in client.API.gui_endpoints():
			pages.append({"url": f"/public/{endpoint.key}", "label": endpoint.gui,
						  "description": endpoint.description, "auth": endpoint.authed})

		# Things worth a button rather than a page. Anything destructive is
		# marked so the template can make it look like what it is.
		actions = []
		# A plugin's own actions first. They are the ones somebody added on
		# purpose; the client's are always there and always the same.
		for endpoint in client.API.action_endpoints():
			actions.append({"url": f"/public/{endpoint.key}",
							"label": endpoint.action, "danger": endpoint.danger})

		actions += [
			{"url": "/ping", "label": "Ping", "danger": False},
			{"url": "/update/check", "label": "Check for an update", "danger": False},
			{"url": "/update", "label": "Update and restart", "danger": True},
			{"url": "/restart", "label": "Restart", "danger": True},
			{"url": "/terminate", "label": "Shut down", "danger": True},
		]

		return render_template("index.html", pages=pages, actions=actions,
							   token=token,
							   device=user.name if user else "this device"), 200

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

		return {"request": "Success", "loaded": loaded, "pending": pending}, 200

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