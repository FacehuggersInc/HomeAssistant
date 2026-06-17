
from src import Client
import os, sys, subprocess, time

def update():
    here = os.path.abspath(os.path.dirname(sys.argv[0]))

    updater_path = os.path.join(here, "updater.py")
    install_path = here
    repo_zip = "https://github.com/FacehuggersInc/HomeAssistant/archive/refs/heads/main.zip"
    relaunch_path = os.path.join(here, "app.py")

    # DETACHED updater process (so main.py can close)
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    subprocess.Popen(
        [
            sys.executable,
            updater_path,
            install_path,
            repo_zip,
            relaunch_path,
            "force"  # optional arg for main.py
        ],
        creationflags=creationflags,
        close_fds=True
    )

    # Exit main.py immediately — updater will relaunch it later
    sys.exit(0)


if __name__ == "__main__":
    args = sys.argv
    if len(args) > 1 and args[1] == "force":
        APP = Client()
        APP.run()
    else:
        update()