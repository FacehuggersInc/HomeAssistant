import os
import sys
import time
import shutil
import zipfile
import subprocess
import tempfile
import urllib.request


def log(msg: str) -> None:
    print(f"[UPDATER] {msg}", flush=True)


# Files and directories that should never be overwritten by an update.
# Add paths relative to the install root.
PRESERVE = {
    "startup.sh",
    ".env",
    ".venv",
    "plugins",                                          # user plugins
    "src/assets/data/new-template.json",                # settings template
}


def should_preserve(rel_path: str) -> bool:
    """Return True if this relative path should not be overwritten."""
    parts = rel_path.replace("\\", "/")
    for p in PRESERVE:
        if parts == p or parts.startswith(p + "/"):
            return True
    return False


if __name__ == "__main__":
    os.system("cls" if os.name == "nt" else "clear")

    if len(sys.argv) < 4:
        log("Usage: python updater.py <install_path> <zip_url> <relaunch_path> [args...]")
        sys.exit(1)

    install_path   = os.path.abspath(sys.argv[1])
    github_zip_url = sys.argv[2]
    relaunch_path  = sys.argv[3]
    relaunch_args  = sys.argv[4:]

    # Extensions to never overwrite (e.g. shell scripts on the target machine)
    IGNORE_EXTS = {"sh"}

    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "update.zip")

    try:
        log("Waiting for main process to close...")
        time.sleep(1.5)

        log(f"Downloading: {github_zip_url}")
        with urllib.request.urlopen(github_zip_url) as response, \
             open(zip_path, "wb") as f:
            shutil.copyfileobj(response, f)

        log("Extracting...")
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(temp_dir)

        folders = [
            d for d in os.listdir(temp_dir)
            if os.path.isdir(os.path.join(temp_dir, d)) and d != "__MACOSX"
        ]
        if not folders:
            raise RuntimeError("No extracted repo folder found in zip.")

        repo_root = os.path.join(temp_dir, folders[0])
        log(f"Copying files to {install_path} ...")

        skipped = 0
        copied  = 0

        for root, dirs, files in os.walk(repo_root):
            # Skip hidden dirs like .git
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            rel_dir = os.path.relpath(root, repo_root)
            dest_dir = os.path.join(install_path, rel_dir)
            os.makedirs(dest_dir, exist_ok=True)

            for filename in files:
                ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

                # Skip by extension
                if ext in IGNORE_EXTS:
                    log(f"  skip (ext)  {os.path.join(rel_dir, filename)}")
                    skipped += 1
                    continue

                rel_file = os.path.normpath(os.path.join(rel_dir, filename))

                # Skip preserved paths
                if should_preserve(rel_file):
                    log(f"  skip (keep) {rel_file}")
                    skipped += 1
                    continue

                src  = os.path.join(root, filename)
                dest = os.path.join(dest_dir, filename)
                shutil.copy2(src, dest)
                copied += 1

        log(f"Done. {copied} files updated, {skipped} skipped.")

    except Exception as e:
        log(f"Update failed: {e}")
        import traceback
        traceback.print_exc()
        input("Press ENTER to exit...")
        sys.exit(1)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    log("Restarting application...")
    if relaunch_path.lower().endswith(".py"):
        subprocess.Popen([sys.executable, relaunch_path] + relaunch_args)
    else:
        subprocess.Popen([relaunch_path] + relaunch_args)