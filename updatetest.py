"""
End-to-end test for the update + launcher path.

Builds a throwaway install, serves a synthetic "new version" over a local
HTTP server, and drives the real code through:

  1. stage -> apply -> relaunch          (happy path)
  2. preserved paths survive             (.env, plugins/)
  3. plugin settings.json is MERGED      (user values kept, new keys added)
  4. a broken update auto-rolls-back     (the behaviour asked for)
  5. crash policy respects settings      (restart N times, then give up)
  6. restart_on_crash=false is honoured

Nothing here touches the real install.
"""

import os, sys, json, shutil, zipfile, tempfile, subprocess, threading, http.server, socketserver, functools, time, pathlib

SRC_TREE = pathlib.Path(__file__).resolve().parent / "HomeAssistant-main"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))


def build_install(root: pathlib.Path):
    """A minimal install with the real constants/updater/launcher."""
    (root / "src").mkdir(parents=True)
    for f in ("constants.py", "updater.py"):
        shutil.copy2(SRC_TREE / "src" / f, root / "src" / f)
    (root / "src" / "__init__.py").write_text("")
    shutil.copy2(SRC_TREE / "launcher.py", root / "launcher.py")

    (root / "src" / "main.py").write_text("# placeholder\n")
    (root / ".env").write_text("SECRET=keepme\n")
    (root / "plugins").mkdir()
    (root / "plugins" / "myplugin.py").write_text("# user plugin\n")
    (root / "VERSION").write_text("1.0\n")

    d = root / "src" / "assets" / "bundled" / "CoreWidgetsBundle"
    d.mkdir(parents=True)
    (d / "settings.json").write_text(json.dumps({
        "weather": {
            "latitude":  {"type": "float", "default": 0.0, "value": 41.25},
            "longitude": {"type": "float", "default": 0.0, "value": -96.0},
        }
    }, indent=4))
    return root


def make_repo_zip(dest_zip: pathlib.Path, app_body: str, version="2.0"):
    """A synthetic 'new version' laid out like a GitHub source zip."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    repo = tmp / "HomeAssistant-main"
    (repo / "src").mkdir(parents=True)
    shutil.copy2(SRC_TREE / "src" / "constants.py", repo / "src" / "constants.py")
    shutil.copy2(SRC_TREE / "src" / "updater.py", repo / "src" / "updater.py")
    (repo / "src" / "__init__.py").write_text("")
    shutil.copy2(SRC_TREE / "launcher.py", repo / "launcher.py")
    (repo / "src" / "main.py").write_text("# updated\n")
    (repo / "app.py").write_text(app_body)
    (repo / "VERSION").write_text(version + "\n")
    (repo / ".env").write_text("SECRET=CLOBBERED\n")           # must be preserved
    (repo / "plugins").mkdir()
    (repo / "plugins" / "injected.py").write_text("# nope\n")  # must be preserved

    d = repo / "src" / "assets" / "bundled" / "CoreWidgetsBundle"
    d.mkdir(parents=True)
    (d / "settings.json").write_text(json.dumps({
        "weather": {
            "latitude":  {"type": "float", "default": 0.0, "value": 0.0},
            "longitude": {"type": "float", "default": 0.0, "value": 0.0},
            "units":     {"type": "string", "default": "F", "value": "F"},   # NEW key
        }
    }, indent=4))

    with zipfile.ZipFile(dest_zip, "w") as z:
        for p in repo.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(tmp).as_posix())
    shutil.rmtree(tmp, ignore_errors=True)


def serve(directory):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def run_launcher(root, timeout=90, settings=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root)
    if settings:
        data_dir = root / "_data"
        data_dir.mkdir(exist_ok=True)
        (data_dir / "DesktopHomeAssistant.json").write_text(json.dumps(
            {"application": {"updates": {
                k: {"type": "x", "default": v, "value": v} for k, v in settings.items()}}}))
        env["XDG_DATA_HOME"] = str(data_dir.parent)
        env["LOCALAPPDATA"] = str(data_dir.parent)
        (root / "_data").rename(root / "DesktopHomeAssistant") if False else None
        # constants builds <base>/DesktopHomeAssistant, so point base at root
        shutil.rmtree(root / "DesktopHomeAssistant", ignore_errors=True)
        (root / "DesktopHomeAssistant").mkdir(exist_ok=True)
        shutil.copy2(data_dir / "DesktopHomeAssistant.json",
                     root / "DesktopHomeAssistant" / "DesktopHomeAssistant.json")
        env["XDG_DATA_HOME"] = str(root)
        env["LOCALAPPDATA"] = str(root)
    return subprocess.run([sys.executable, str(root / "launcher.py")],
                          cwd=str(root), env=env, capture_output=True,
                          text=True, timeout=timeout)


# ---------------------------------------------------------------- scenarios

def scenario_happy():
    print("\n1. stage -> apply -> relaunch, preserve + merge")
    root = pathlib.Path(tempfile.mkdtemp()) / "install"
    build_install(root)
    web = pathlib.Path(tempfile.mkdtemp())
    httpd, base = serve(web)
    try:
        # v2 app.py: exits 0 immediately, marking that it ran
        make_repo_zip(web / "repo.zip",
                      "import pathlib,sys\n"
                      "pathlib.Path(__file__).parent.joinpath('RAN_V2').write_text('1')\n"
                      "sys.exit(0)\n")
        # v1 app.py: stages the update, then exits 42
        (root / "app.py").write_text(
            "import sys, pathlib\n"
            f"sys.path.insert(0, {str(root)!r})\n"
            "from src import updater\n"
            f"updater.stage(url={base + '/repo.zip'!r})\n"
            "sys.exit(42)\n")

        r = run_launcher(root)
        check("launcher exited 0", r.returncode == 0, f"rc={r.returncode}\n{r.stdout[-800:]}")
        check("update applied (VERSION==2.0)", (root / "VERSION").read_text().strip() == "2.0")
        check("new version actually ran", (root / "RAN_V2").exists())
        check(".env preserved", "keepme" in (root / ".env").read_text())
        check("user plugin preserved", (root / "plugins" / "myplugin.py").exists())
        check("plugin injection blocked", not (root / "plugins" / "injected.py").exists())

        st = json.loads((root / "src/assets/bundled/CoreWidgetsBundle/settings.json").read_text())
        check("user setting value kept on merge", st["weather"]["latitude"]["value"] == 41.25,
              str(st["weather"]["latitude"]))
        check("new setting key added on merge", "units" in st["weather"])
        check("staging cleaned up", not (root / ".update-staging").exists())
    finally:
        httpd.shutdown()


def scenario_rollback():
    print("\n2. broken update auto-rolls-back")
    root = pathlib.Path(tempfile.mkdtemp()) / "install"
    build_install(root)
    web = pathlib.Path(tempfile.mkdtemp())
    httpd, base = serve(web)
    try:
        # v2 app.py crashes instantly
        make_repo_zip(web / "repo.zip",
                      "import sys\nsys.stderr.write('boom\\n')\nsys.exit(3)\n")
        # v1 stages once, then afterwards exits 0 so the test terminates
        (root / "app.py").write_text(
            "import sys, pathlib\n"
            f"sys.path.insert(0, {str(root)!r})\n"
            "flag = pathlib.Path(__file__).parent / 'STAGED_ONCE'\n"
            "if flag.exists():\n"
            "    pathlib.Path(__file__).parent.joinpath('V1_RAN_AGAIN').write_text('1')\n"
            "    sys.exit(0)\n"
            "flag.write_text('1')\n"
            "from src import updater\n"
            f"updater.stage(url={base + '/repo.zip'!r})\n"
            "sys.exit(42)\n")

        r = run_launcher(root, settings={"update_grace_period": 60,
                                         "restart_on_crash": True,
                                         "max_restart_attempts": 5})
        out = r.stdout
        check("rollback was triggered", "Rolling back" in out, out[-1200:])
        check("reverted to VERSION 1.0", (root / "VERSION").read_text().strip() == "1.0")
        check("old version relaunched after rollback", (root / "V1_RAN_AGAIN").exists())
        check("launcher exited 0", r.returncode == 0, f"rc={r.returncode}")
        check("backup cleaned up", not (root / ".update-backup").exists())
    finally:
        httpd.shutdown()


def scenario_crash_policy():
    print("\n3. crash policy: bounded restarts")
    root = pathlib.Path(tempfile.mkdtemp()) / "install"
    build_install(root)
    (root / "app.py").write_text(
        "import sys, pathlib\n"
        "p = pathlib.Path(__file__).parent / 'attempts'\n"
        "n = int(p.read_text()) if p.exists() else 0\n"
        "p.write_text(str(n + 1))\n"
        "sys.exit(7)\n")

    r = run_launcher(root, settings={"restart_on_crash": True,
                                     "max_restart_attempts": 3,
                                     "crash_window": 120,
                                     "update_grace_period": 60})
    n = int((root / "attempts").read_text())
    check("initial run + 3 restarts = 4 launches", n == 4, f"got {n}\n{r.stdout[-900:]}")
    check("gave up rather than looping", "Giving up" in r.stdout)
    check("propagated app exit code", r.returncode == 7, f"rc={r.returncode}")


def scenario_restart_disabled():
    print("\n4. restart_on_crash = false is honoured")
    root = pathlib.Path(tempfile.mkdtemp()) / "install"
    build_install(root)
    (root / "app.py").write_text(
        "import sys, pathlib\n"
        "p = pathlib.Path(__file__).parent / 'attempts'\n"
        "n = int(p.read_text()) if p.exists() else 0\n"
        "p.write_text(str(n + 1))\n"
        "sys.exit(9)\n")

    r = run_launcher(root, settings={"restart_on_crash": False,
                                     "max_restart_attempts": 5,
                                     "crash_window": 120,
                                     "update_grace_period": 60})
    n = int((root / "attempts").read_text())
    check("ran exactly once", n == 1, f"got {n}")
    check("said restart is off", "restart_on_crash is off" in r.stdout)
    check("propagated app exit code", r.returncode == 9)


if __name__ == "__main__":
    for fn in (scenario_happy, scenario_rollback, scenario_crash_policy, scenario_restart_disabled):
        try:
            fn()
        except Exception:
            import traceback; traceback.print_exc()
            FAIL.append(fn.__name__)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("failed:", ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
