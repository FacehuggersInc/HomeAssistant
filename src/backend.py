from src import *
from flask import Flask, jsonify, redirect, send_from_directory, request

ADDRESS = "0.0.0.0"
PORT = 5000

def FlaskService(stop_event, client, flask):
	from werkzeug.serving import make_server
	server = make_server(ADDRESS, PORT, flask)
	server.timeout = 1

	while not stop_event.is_set():
		server.handle_request()  # handle one request at a time

def FlaskApp(client):
	app = Flask(f"{APP_NAME.replace(" ","")}_backend")

	@app.route("/restart")
	def restart_client():
		if not client.BUILT:
			return {"request":"Failed", "reason": "Wait until the Program has started fully."}
		client.RESTART = True
		return {"request":"Success"}

	@app.route("/update")
	def update_client():
		if not client.BUILT:
			return {"request":"Failed", "reason": "Wait until the Program has started fully."}

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
			client.UPDATE = True
			client.call_on_ui(client.stop)
			return {"request": "Success", "message": f"{copied} files updated, restarting."}

		except Exception as e:
			client.simple_notify("error", "Update Failed", str(e))
			return {"request": "Failed", "reason": str(e)}, 500
		finally:
			shutil.rmtree(temp_dir, ignore_errors=True)

	@app.route("/notify/", methods=["GET"])
	def redirects_bad_endpoint():
		return redirect(f"{request.base_url.rstrip('/')}?{request.query_string.decode()}")
	
	@app.route("/notify", methods=["GET"])
	def backend_notify():
		try:
			__ico = request.args.get("icon")
			icon = __ico.split(".")[-1] 
			title = request.args.get("title")
			body = request.args.get("body")

			if icon and title and body:
				client.simple_notify(icon, title, body)
				return {"request":"Success"}, 200
			else:
				missing_args = []
				if not icon: missing_args.append("icon")
				if not title: missing_args.append("title")
				if not body: missing_args.append("body")
				return {"request":"Failed", "reason":f"missing -> {missing_args}"}, 404
		except Exception as e:
			return {"request":"Failed", "reason":e}, 500
		
	@app.route("/asset/<key>/<filename>", methods=["GET"])
	def asset_download(key, filename):
		path = client.asset("FOLDER", key)
		if path:
			
			actual = None
			if len(filename.split(".")) == 1:
				for file in path.iterdir():
					if file.stem == filename:
						actual = path / file
						break
			else:
				actual = path / filename

			if actual.exists():
				if len(filename.split(".")) > 1:
					return send_from_directory(path.as_posix(), actual.name, as_attachment=True)
				else:
					return send_from_directory(path.as_posix(), actual.name)
			else:
				return {"request":"Failed", "reason":f"File {filename} does not exists in {key}"}
		else:
			return {"request":"Failed", "path":[path, key, filename]}
		
	@app.route("/settings/<path>", methods=["GET", "POST"])
	def setting_set(path):
		if path:
			setting = client.SETTINGS.get_path(path)
			if not setting == None:
				if not request.args.get("v"):
					return {"request":"Success", "setting":setting}, 200
				else:
					client.SETTINGS.set_path(path, request.args.get("v"))
					return {"request":"Success", "setting":client.SETTINGS.get_path(path)}, 200
			else:
				return {"request": "Failed", "reason": f"No Setting at {path}"}, 404
		else:
			return {"request": "Failed", "reason": f"No given Path"}, 404

	@app.route("/plugins/<plugin_key>/reload", methods=["GET"])
	def reload_plugin(plugin_key):
		if not client.BUILT: return {"request":"Failed", "reason":"The Application is still building and setting up... please wait 5 seconds"}, 200
		if plugin_key:
			if client.plugin_manager.plugins.get(plugin_key):
				client.plugin_manager.reload_plugin( plugin_key )
				return {"request":"Success"}, 200
			else:
				return {"request":"Failed", "reason": f"No Plugin by the name '{plugin_key}' is loaded."}, 404
		else:
			return {"request":"Failed", "reason":"No Plugin Key Given!"}, 404
		
	@app.route("/process", methods=["GET"])
	def start_intent():
		query = request.args.get("q")
		if query.strip():
			Thread(target = client.STT.pre_processing, args = [query, ]).start()
			return {"request":"Success"}, 200
		else:
			return {"request":"Failed", "reason":"No Query(q) Given!"}, 404

	return app