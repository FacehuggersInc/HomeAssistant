from src import *
from flask import Flask, jsonify, redirect, send_from_directory, request

ADDRESS = "0.0.0.0"
PORT = 5000

def FlaskService(stop_event, client, flask):
	from werkzeug.serving import make_server
	server = make_server(ADDRESS, PORT, flask)
	server.timeout = 1

	while not stop_event.is_set():
		server.handle_request()

def FlaskApp(client):
	app = Flask(APP_NAME.replace(" ", "") + "_backend")

	# ── Auth helper ───────────────────────────────────────────────────────────

	def auth():
		"""Return error response tuple if ?id= is missing or wrong, else None."""
		given = request.args.get("id", "").strip()
		if not given:
			return {"request": "Failed", "reason": "Missing required ?id= parameter"}, 401
		if given != client.CLIENT_ID:
			return {"request": "Failed", "reason": "Invalid client ID"}, 403
		return None

	def log():
		"""
		Log the current request endpoint and query args.
		Masks the id= parameter if present.
		Call at the top of any route function:
			@app.route("/something")
			def something():
				_log()
				...
		"""
		args = {k: ("***" if k == "id" else v) for k, v in request.args.items()}
		arg_str = "  " + "  ".join(f"{k}={v}" for k, v in args.items()) if args else ""
		client.log("info", f"[API] {request.method} {request.path}{arg_str}")

	## CLIENT CONTROL

	@app.route("/terminate")
	def terminate_client():
		log()
		err = auth()
		if err: return err
		client.simple_notify("kill", "Termination", "Was asked to Terminate via API")
		time.sleep(1)
		client.call_on_ui(client.stop)
		return {"request": "Success"}

	@app.route("/restart")
	def restart_client():
		log()
		err = auth()
		if err: return err
		if not client.BUILT:
			return {"request": "Failed", "reason": "Wait until the Program has started fully."}
		client.RESTART = True
		return {"request": "Success"}

	@app.route("/update")
	def update_client():
		log()
		err = auth()
		if err: return err
		if not client.BUILT:
			return {"request": "Failed", "reason": "Wait until the Program has started fully."}

		import shutil, zipfile, tempfile, urllib.request, os, sys

		here    = os.path.abspath(os.path.dirname(sys.argv[0]))
		zip_url = "https://github.com/FacehuggersInc/HomeAssistant/archive/refs/heads/main.zip"
		preserve    = {"startup.sh", "update.sh", ".env", ".venv", "plugins"}
		ignore_exts = {"sh"}

		def should_preserve(rel):
			rel = rel.replace("\\", "/")
			return any(rel == p or rel.startswith(p + "/") for p in preserve)

		client.simple_notify("download", "Update", "Downloading update...")

		temp_dir = tempfile.mkdtemp()
		zip_path = os.path.join(temp_dir, "update.zip")
		try:
			with urllib.request.urlopen(zip_url) as r, open(zip_path, "wb") as f:
				shutil.copyfileobj(r, f)

			with zipfile.ZipFile(zip_path, "r") as z:
				z.extractall(temp_dir)

			folders = [d for d in os.listdir(temp_dir)
					   if os.path.isdir(os.path.join(temp_dir, d)) and d != "__MACOSX"]
			if not folders:
				raise RuntimeError("No repo folder found in zip")

			repo_root = os.path.join(temp_dir, folders[0])
			copied = skipped = 0
			for root, dirs, files in os.walk(repo_root):
				dirs[:] = [d for d in dirs if not d.startswith(".")]
				rel_dir  = os.path.relpath(root, repo_root)
				dest_dir = os.path.join(here, rel_dir)
				os.makedirs(dest_dir, exist_ok=True)
				for filename in files:
					ext      = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
					rel_file = os.path.normpath(os.path.join(rel_dir, filename))
					if ext in ignore_exts or should_preserve(rel_file):
						skipped += 1
						continue
					shutil.copy2(os.path.join(root, filename), os.path.join(dest_dir, filename))
					copied += 1

			client.simple_notify("check", "Update", f"Done. {copied} files updated. Restarting...")
			time.sleep(2)
			client.UPDATE = True
			client.call_on_ui(client.stop)
			return {"request": "Success", "message": f"{copied} files updated, restarting."}

		except Exception as e:
			client.simple_notify("error", "Update Failed", str(e))
			return {"request": "Failed", "reason": str(e)}, 500
		finally:
			shutil.rmtree(temp_dir, ignore_errors=True)

	## TASKS

	@app.route("/notify/", methods=["GET"])
	def redirects_bad_endpoint():
		return redirect(f"{request.base_url.rstrip('/')}?{request.query_string.decode()}")

	@app.route("/notify", methods=["GET"])
	def backend_notify():
		log()
		try:
			__ico = request.args.get("icon")
			icon  = __ico.split(".")[-1]
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
			return {"request": "Failed", "reason": str(e)}, 500

	@app.route("/asset/<key>", methods=["GET"])
	@app.route("/asset/<key>/<filename>", methods=["GET"])
	def asset_download(key, filename=None):
		log()
		err = auth()
		if err: return err

		type_ = request.args.get("type", "FOLDER").upper()
		path  = client.asset(type_, key)

		if not path:
			return {"request": "Failed", "reason": f"Asset '{key}' not found"}, 404

		def safe(f):
			"""Exclude dotfiles and anything under src/."""
			name = f.name
			rel  = f.as_posix()
			return (
				f.is_file()
				and not name.startswith(".")
				and "src/" not in rel
			)

		if filename is None:
			files = [f.name for f in path.iterdir() if safe(f)]
			return {"request": "Success", "key": key, "files": files}

		if filename.startswith(".") or "src/" in filename.replace("\\", "/"):
			return {"request": "Failed", "reason": "Access denied"}, 403

		if "." not in filename:
			match = next((f for f in path.iterdir() if f.stem == filename and safe(f)), None)
			if match:
				return send_from_directory(path.as_posix(), match.name)
			return {"request": "Failed", "reason": f"No file with stem '{filename}' in '{key}'"}, 404

		actual = path / filename
		if actual.exists() and safe(actual):
			return send_from_directory(path.as_posix(), actual.name, as_attachment=True)
		if actual.exists():
			return {"request": "Failed", "reason": "Access denied"}, 403
		return {"request": "Failed", "reason": f"File '{filename}' not found in '{key}'"}, 404

	@app.route("/settings/<path:path>", methods=["GET", "POST"])
	def setting_set(path):
		log()
		if path:
			setting = client.SETTINGS.get_path(path)
			if setting is not None:
				if not request.args.get("v"):
					return {"request": "Success", "setting": setting}, 200
				else:
					client.SETTINGS.set_path(path, request.args.get("v"))
					return {"request": "Success", "setting": client.SETTINGS.get_path(path)}, 200
			else:
				return {"request": "Failed", "reason": f"No Setting at {path}"}, 404
		else:
			return {"request": "Failed", "reason": "No given Path"}, 404

	@app.route("/plugins/<plugin_key>/reload", methods=["GET"])
	def reload_plugin(plugin_key):
		log()
		if not client.BUILT:
			return {"request": "Failed", "reason": "The Application is still building..."}, 200
		if plugin_key:
			if client.plugin_manager.plugins.get(plugin_key):
				client.plugin_manager.reload_plugin(plugin_key)
				return {"request": "Success"}, 200
			else:
				return {"request": "Failed", "reason": f"No Plugin '{plugin_key}' loaded."}, 404
		else:
			return {"request": "Failed", "reason": "No Plugin Key Given!"}, 404

	@app.route("/process", methods=["GET"])
	def start_intent():
		log()
		query = request.args.get("q")
		if query and query.strip():
			Thread(target=client.STT.pre_processing, args=[query]).start()
			return {"request": "Success"}, 200
		else:
			return {"request": "Failed", "reason": "No Query(q) Given!"}, 404

	return app