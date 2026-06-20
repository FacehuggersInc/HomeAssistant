from src import *
from flask import Flask, jsonify, redirect, send_from_directory, request, render_template

ADDRESS = "0.0.0.0"
PORT = 5000

def FlaskService(stop_event, client, flask):
	from werkzeug.serving import make_server
	server = make_server(ADDRESS, PORT, flask)
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
		"""Return error response tuple if ?id= is missing or wrong, else None."""
		given = request.args.get("id", "").strip()
		if not given:
			return {"request": "Failed", "reason": "Missing required ?id= parameter"}, 401
		if given != client.CLIENT_ID:
			return {"request": "Failed", "reason": "Invalid client ID"}, 403
		return None

	def log(level:str = "info", extra:str = ""):
		"""
		Log the current request endpoint and query args.
		Masks the id= parameter if present.
		Call at the top of any route function:
			@app.route("/something")
			def something():
				log()
				...
		"""
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
		time.sleep(1)
		client.call_on_ui(client.stop)
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
			client.log("error", f"[backend.update_client] Update Failed: {e}")
			return {"request": "Failed", "reason": str(e)}, 500
		finally:
			shutil.rmtree(temp_dir, ignore_errors=True)



	## TASKS ENDPOINTS
	@app.route("/notify/", methods=["GET"])
	def redirects_bad_endpoint():
		return redirect(f"{request.base_url.rstrip('/')}?{request.query_string.decode()}")

	@app.route("/notify", methods=["GET"])
	def backend_notify():
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
			"""Exclude dotfiles and anything under src/."""
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



	## PLUGIN ENDPOINTS
	@app.route("/plugins/<plugin_key>/<endpoint>", methods=["GET"])
	def reload_plugin(plugin_key, endpoint):
		log()
		
		if not client.BUILT: return {"request": "Failed", "reason": "The Application is still building..."}, 200
		err = auth()
		if err: return err

		if plugin_key and endpoint:
			if client.plugin_manager.plugins.has_plugin(plugin_key):
				match endpoint:
					case "reload":
						client.plugin_manager.reload_plugin(plugin_key)
						return {"request": "Success"}, 200
					case _: #! NOT BUILT YET
						try:
							return {"request":"Failed", "reason":f"There is no endpoint ({endpoint}) here ..."}, 404
						except Exception as e:
							return {"request":"Failed", "reason":f"There is no endpoint ({endpoint}) here AND it errored: {e}"}, 404
			else:
				return {"request": "Failed", "reason": f"No Plugin '{plugin_key}' loaded."}, 404
		else:
			return {"request": "Failed", "reason": "No Plugin Key Given!"}, 404

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
					return end.call(**request.args)
				except Exception as e:
					client.log("error", f"[backend.registered_endpoint_routing] Endpoint Call Failed: {e}")
					return {"request":"Failed", "reason":f"Public endpoint failed due to: {e}"}, 200
					
			
			else:
				log("warning", "Registry.None")
				return {"request":"Failed", "reason":f"No Public endpoint under the name '{endpoint}'"}, 404
		
		return {"request":"Failed"}, 200

	return app