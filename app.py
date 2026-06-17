import os
import sys


def do_update():
    """
    Download and install the latest version from GitHub, then exit with
    code 42 so startup.sh knows to relaunch.
    Runs in the same process/environment so display vars are preserved.
    """
    import shutil, zipfile, tempfile, urllib.request

    here         = os.path.abspath(os.path.dirname(sys.argv[0]))
    repo_zip     = "https://github.com/FacehuggersInc/HomeAssistant/archive/refs/heads/main.zip"
    preserve     = {"startup.sh", "update.sh", ".env", ".venv", "plugins"}
    ignore_exts  = {"sh"}

    def log(msg): print(f"[UPDATE] {msg}", flush=True)
    def should_preserve(rel):
        rel = rel.replace("\\", "/")
        return any(rel == p or rel.startswith(p + "/") for p in preserve)

    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "update.zip")

    try:
        log("Downloading latest version...")
        with urllib.request.urlopen(repo_zip) as r, open(zip_path, "wb") as f:
            shutil.copyfileobj(r, f)

        log("Extracting...")
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(temp_dir)

        folders = [d for d in os.listdir(temp_dir)
                   if os.path.isdir(os.path.join(temp_dir, d)) and d != "__MACOSX"]
        if not folders:
            raise RuntimeError("No repo folder found in zip")

        repo_root = os.path.join(temp_dir, folders[0])
        log(f"Installing to {here}...")

        copied = skipped = 0
        for root, dirs, files in os.walk(repo_root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            rel_dir  = os.path.relpath(root, repo_root)
            dest_dir = os.path.join(here, rel_dir)
            os.makedirs(dest_dir, exist_ok=True)
            for filename in files:
                ext     = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
                rel_file = os.path.normpath(os.path.join(rel_dir, filename))
                if ext in ignore_exts or should_preserve(rel_file):
                    skipped += 1
                    continue
                shutil.copy2(os.path.join(root, filename), os.path.join(dest_dir, filename))
                copied += 1

        log(f"Done. {copied} files updated, {skipped} skipped.")

    except Exception as e:
        log(f"Update failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    log("Exiting with code 42 so startup.sh will relaunch...")
    sys.exit(42)


if __name__ == "__main__":
    args = sys.argv[1:]

    if "update" in args:
        # Update then relaunch via startup.sh loop
        do_update()

    elif "force" in args or not args:
        # Normal launch
        from src import Client
        CLIENT = Client()
        CLIENT.run()

    else:
        print(f"Unknown arguments: {args}")
        sys.exit(1)