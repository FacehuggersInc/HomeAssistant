import os
import hmac
import time
import shutil
from threading import Thread

from src.constants import APP_NAME

from flask import Flask, jsonify, redirect, send_from_directory, request, render_template

ADDRESS = "0.0.0.0"
PORT = 5000

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

	# AUTH & HELPERS
	def auth():
		given = request.args.get("id", "").strip()
		if not given:
			return {"request": "Failed", "reason": "Missing required ?id= parameter"}, 401
		if not hmac.compare_digest(given, str(client.CLIENT_ID)):
			# compare_digest, not !=: a plain comparison returns as soon as it
			# finds a differing byte, which leaks the id one character at a
			# time to anything that can measure the reply.
			return {"request": "Failed", "reason": "Invalid client ID"}, 403
		return None

	def log(level:str = "info", extra:str = ""):
		args = {k: ("***" if k == "id" else v) for k, v in request.args.items()}
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

		id_param = request.args.get("id", "")
		return render_template("upload_index.html", assets=uploadable, id=id_param)

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

		id_param = request.args.get("id", "")
		return render_template("upload.html", key=key, path=str(path), id=id_param)

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
				"endpoints":  client.API_REGISTRY.endpoints_for(key),
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
					"endpoints":     client.API_REGISTRY.endpoints_for(plugin_key),
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
			api_endpoint = client.API_REGISTRY.get_endpoint(endpoint)
			client.log("debug", str(api_endpoint))
			if api_endpoint and isinstance(api_endpoint, tuple):
				owner, end = api_endpoint
				log("info", f"Registry.{owner}")
				if end.authed:
					err = auth()
					if err: return err

				try:
					# `id` is ours, not the endpoint's. Forwarding request.args
					# wholesale meant every authed endpoint was called with an
					# unexpected id= keyword and raised TypeError, which landed
					# here and read as "the endpoint is broken".
					params = {k: v for k, v in request.args.items() if k != "id"}

					# POST was accepted but the body was thrown away - only the
					# query string ever reached the callback.
					if request.method == "POST":
						body = request.get_json(silent=True)
						if isinstance(body, dict):
							params.update(body)
						elif request.form:
							params.update(request.form.to_dict())

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