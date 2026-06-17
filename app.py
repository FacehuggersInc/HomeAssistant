import os
import sys
import subprocess


def update():
    here          = os.path.abspath(os.path.dirname(sys.argv[0]))
    updater_path  = os.path.join(here, "updater.py")
    repo_zip      = "https://github.com/FacehuggersInc/HomeAssistant/archive/refs/heads/main.zip"
    relaunch_path = os.path.join(here, "app.py")

    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            [sys.executable, updater_path, here, repo_zip, relaunch_path, "force"],
            creationflags=creationflags,
            close_fds=True,
        )
    else:
        # start_new_session detaches updater from this terminal's process group
        # so it survives when app.py exits and the terminal closes
        subprocess.Popen(
            [sys.executable, updater_path, here, repo_zip, relaunch_path, "force"],
            start_new_session=True,
            close_fds=True,
        )

    sys.exit(0)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "force":
        from src import Client
        CLIENT = Client()
        CLIENT.run()
    else:
        update()