import os, sys, time, shutil, zipfile, subprocess, tempfile, urllib.request

def log(msg): print(f"[UPDATER]: {msg}")

if __name__ == "__main__":
    os.system("CLS" if os.name == "nt" else "clear")

    if len(sys.argv) < 4:
        log("Usage: python updater.py <install_path> <github_zip_url> <relaunch_path> [args...]")
        sys.exit(1)

    install_path = os.path.abspath(sys.argv[1])
    github_zip_url = sys.argv[2]
    relaunch_path = sys.argv[3]
    relaunch_args = sys.argv[4:]
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "update.zip")

    try:
        # Wait a bit for main.py to close
        log("Waiting for main process to close...")
        time.sleep(1.5)

        log(f"Downloading update: {github_zip_url}")
        with urllib.request.urlopen(github_zip_url) as r, open(zip_path, "wb") as f:
            shutil.copyfileobj(r, f)

        log("Extracting...")
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(temp_dir)

        folders = [f for f in os.listdir(temp_dir) if os.path.isdir(os.path.join(temp_dir, f))]
        if not folders:
            raise RuntimeError("No extracted repo folder found.")
        repo_root = os.path.join(temp_dir, folders[0])

        log(f"Copying files to {install_path} ...")
        for root, _, files in os.walk(repo_root):
            rel = os.path.relpath(root, repo_root)
            dest = os.path.join(install_path, rel)
            os.makedirs(dest, exist_ok=True)
            for f in files:
                shutil.copy2(os.path.join(root, f), os.path.join(dest, f))

        log("Cleaning up...")
        shutil.rmtree(temp_dir, ignore_errors=True)

        # Relaunch main.py (in same console window)
        log("Restarting application...")
        if relaunch_path.lower().endswith(".py"):
            subprocess.Popen([sys.executable, relaunch_path] + relaunch_args)
        else:
            subprocess.Popen([relaunch_path] + relaunch_args)

    except Exception as e:
        log(f"Update failed: {e}")
        input("Press ENTER to exit...")