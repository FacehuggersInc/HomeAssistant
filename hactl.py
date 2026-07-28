#!/usr/bin/env python3
"""
hactl - drive a Desktop Home Assistant panel over its backend API.

Standalone on purpose: stdlib only, no imports from src/, single file. Copy it
onto any machine that can reach the panel and it runs, including one that has
no checkout of the project at all.

    ./hactl.py update --wait
    ./hactl.py plugins list
    ./hactl.py settings set assistant.model.value small.en

Pairing happens once per machine: `hosts add` asks the panel for access, then
somebody standing at it allows or denies the request. The token that comes back
is this device's alone and can be revoked from the panel without affecting
anything else.

The config file holds those tokens, so it is written 0600 and they are never
printed in full.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_PORT = 5000
DEFAULT_TIMEOUT = 10

# Exit codes, so this is usable from a shell script.
EXIT_OK = 0
EXIT_FAILED = 1      # the panel answered, and said no
EXIT_USAGE = 2       # nothing was attempted
EXIT_UNREACHABLE = 3 # the panel never answered


## ── config ────────────────────────────────────────────────────────────────────

def config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "hactl" / "hosts.json"


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        return {"default": None, "hosts": {}}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        die(f"Could not read {path}: {exc}", EXIT_USAGE)
    data.setdefault("default", None)
    data.setdefault("hosts", {})
    return data


def save_config(config: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")
    if os.name != "nt":
        # The client id is in here. Anyone who can read it can terminate the
        # panel, rewrite its settings and unload its plugins.
        try:
            path.chmod(0o600)
        except OSError:
            pass


def mask(value: str) -> str:
    value = str(value or "")
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


## ── target resolution ─────────────────────────────────────────────────────────

class Target:
    def __init__(self, host: str, port: int, token: str, name: str = None):
        self.host = host
        self.port = int(port)
        self.token = token
        self.name = name

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def __str__(self) -> str:
        label = f"{self.name} " if self.name else ""
        return f"{label}({self.base}, token {mask(self.token)})"


def resolve_target(args, config: dict, allow_prompt: bool = True) -> Target:
    """Command line wins, then the named host, then the default, then ask."""
    if args.host:
        token = args.token or _token_for_host(config, args.host)
        if not token and allow_prompt:
            token = pair(args.host, args.port or DEFAULT_PORT)
        if not token:
            die("No token for that host. Pair it with 'hosts add'.", EXIT_USAGE)
        return Target(args.host, args.port or DEFAULT_PORT, token)

    name = args.target or config.get("default")
    if name and name in config["hosts"]:
        entry = config["hosts"][name]
        return Target(entry["host"],
                      args.port or entry.get("port", DEFAULT_PORT),
                      args.token or entry.get("token", ""),
                      name)

    if name:
        die(f"No saved host called '{name}'. Try 'hactl.py hosts list'.", EXIT_USAGE)

    if not allow_prompt or not sys.stdin.isatty():
        die("No host configured. Run 'hactl.py hosts add <name> --host <ip>' first.",
            EXIT_USAGE)

    return first_run(config)


def _token_for_host(config: dict, host: str) -> str:
    for entry in config["hosts"].values():
        if entry.get("host") == host:
            return entry.get("token", "")
    return ""


def pair(host: str, port: int, name: str = None) -> str:
    """
    Ask the panel for access and wait for somebody to answer.

    The token is issued by the panel, not chosen here - a device picking its
    own would let anything claim one it had seen, and there would be no point
    at which the panel decided.
    """
    import platform
    name = name or f"hactl on {platform.node()}"
    base = f"http://{host}:{port}"

    try:
        request = urllib.request.Request(
            f"{base}/access/request?{urllib.parse.urlencode({'name': name})}",
            method="POST")
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            token = json.loads(response.read()).get("token", "")
    except Exception as exc:
        die(f"Could not reach {base}: {exc}", EXIT_UNREACHABLE)

    if not token:
        die("The panel did not issue a token.", EXIT_FAILED)

    print(f"Asked '{name}' to be allowed.")
    print("Approve it on the panel - a dialog is waiting there.")

    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            url = f"{base}/access/state?{urllib.parse.urlencode({'token': token})}"
            with urllib.request.urlopen(url, timeout=5) as response:
                state = json.loads(response.read()).get("state", "")
        except Exception:
            state = ""

        if state == "approved":
            print("Approved.")
            return token
        if state == "denied":
            die("The panel denied that request.", EXIT_FAILED)
        print(".", end="", flush=True)
        time.sleep(2)

    die("Nobody answered on the panel.", EXIT_FAILED)


def first_run(config: dict) -> Target:
    print("No panel configured yet. Let's set one up.\n")
    host = input("Panel IP or hostname: ").strip()
    if not host:
        die("Nothing entered.", EXIT_USAGE)

    port_raw = input(f"Port [{DEFAULT_PORT}]: ").strip()
    port = int(port_raw) if port_raw.isdigit() else DEFAULT_PORT

    token = pair(host, port)

    name = input("Save as [default]: ").strip() or "default"
    config["hosts"][name] = {"host": host, "port": port, "token": token}
    config["default"] = name
    save_config(config)
    print(f"\nSaved to {config_path()}\n")
    return Target(host, port, token, name)


## ── requests ──────────────────────────────────────────────────────────────────

def call(target: Target, path: str, params: dict = None, timeout: int = DEFAULT_TIMEOUT,
         method: str = "GET", body: dict = None) -> tuple[int, object]:
    """Returns (status, decoded_body). Never raises for an HTTP error status."""
    query = dict(params or {})
    query["token"] = target.token
    url = f"{target.base}{path}?{urllib.parse.urlencode(query)}"

    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, decode(response.read())
    except urllib.error.HTTPError as exc:
        # A 4xx here is an answer, not a failure to reach the panel - the body
        # carries the reason and is worth showing.
        return exc.code, decode(exc.read())
    except urllib.error.URLError as exc:
        die(f"Could not reach {target.base}: {exc.reason}", EXIT_UNREACHABLE)
    except TimeoutError:
        die(f"Timed out after {timeout}s waiting on {target.base}", EXIT_UNREACHABLE)
    except Exception as exc:  # malformed status line, connection reset, ...
        die(f"Request to {target.base} failed: {exc}", EXIT_UNREACHABLE)


def decode(raw: bytes):
    text = (raw or b"").decode("utf-8", "replace").strip()
    try:
        return json.loads(text)
    except ValueError:
        return text


def probe(target: Target, timeout: int = 3) -> str:
    """
    ready | starting | unauthorized | unreachable.

    Read-only, so it is safe to poll. `unauthorized` is its own answer rather
    than being folded into `ready`: the panel is up, but a wrong client id
    means nothing else in this tool will work, and reporting that as ready
    sends you looking at the network instead of the id.
    """
    url = f"{target.base}/plugins?{urllib.parse.urlencode({'token': target.token})}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return "ready" if response.status == 200 else "starting"
    except urllib.error.HTTPError as exc:
        if exc.code == 503:
            return "starting"          # up, still building
        if exc.code in (401, 403):
            return "unauthorized"
        return "ready"                 # it answered, so it is up
    except Exception:
        return "unreachable"


def wait_for(target: Target, seconds: int) -> bool:
    deadline = time.time() + seconds
    went_away = False
    print("  waiting for the panel to come back", end="", flush=True)
    while time.time() < deadline:
        state = probe(target)
        if state == "unauthorized":
            # Polling forever would never resolve this one.
            print(" stopped: the client id is being rejected.")
            return False
        # It has to drop before it can return. Without this a poll that lands
        # before the restart reports success against the process on its way out.
        if state == "unreachable":
            went_away = True
        elif went_away and state == "ready":
            print(" back up.")
            return True
        print(".", end="", flush=True)
        time.sleep(2)
    print(" gave up.")
    return False


## ── output ────────────────────────────────────────────────────────────────────

def die(message: str, code: int = EXIT_FAILED):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def report(status: int, body, as_json: bool = False) -> int:
    if as_json:
        print(json.dumps(body, indent=2) if not isinstance(body, str) else body)
    elif isinstance(body, dict):
        state = body.get("request")
        if state == "Success":
            message = body.get("message") or ""
            extra = {k: v for k, v in body.items()
                     if k not in ("request", "message")}
            print(f"ok  {message}".rstrip())
            for key, value in extra.items():
                print(f"    {key}: {value}")
        else:
            print(f"failed ({status})  {body.get('reason', body)}")
    else:
        # Flask's own 404/500 pages are full HTML documents; dumping one over
        # the terminal buries the useful line.
        text = " ".join(str(body).split())
        if len(text) > 200:
            text = text[:200] + " ..."
        print(f"[{status}] {text}")

    if isinstance(body, dict) and body.get("request") == "Success":
        return EXIT_OK
    return EXIT_OK if 200 <= status < 300 else EXIT_FAILED


## ── commands ──────────────────────────────────────────────────────────────────

def cmd_hosts(args, config):
    action = args.hosts_action

    if action == "list":
        if not config["hosts"]:
            print("No saved hosts. Add one with:  hactl.py hosts add panel --host 192.168.1.50")
            return EXIT_OK
        for name, entry in sorted(config["hosts"].items()):
            marker = "*" if name == config.get("default") else " "
            print(f" {marker} {name:<14} {entry['host']}:{entry.get('port', DEFAULT_PORT)}"
                  f"   token {mask(entry.get('token'))}")
        print(f"\n  ({config_path()})")
        return EXIT_OK

    if action == "add":
        token = args.token or pair(args.host, args.port or DEFAULT_PORT,
                                   name=f"hactl ({args.name})")
        config["hosts"][args.name] = {
            "host": args.host,
            "port": args.port or DEFAULT_PORT,
            "token": token,
        }
        if args.make_default or config.get("default") is None:
            config["default"] = args.name
        save_config(config)
        print(f"Saved '{args.name}' -> {args.host}:{args.port or DEFAULT_PORT}")
        return EXIT_OK

    if action == "remove":
        if args.name not in config["hosts"]:
            die(f"No saved host called '{args.name}'.", EXIT_USAGE)
        del config["hosts"][args.name]
        if config.get("default") == args.name:
            config["default"] = next(iter(config["hosts"]), None)
        save_config(config)
        print(f"Removed '{args.name}'.")
        return EXIT_OK

    if action == "use":
        if args.name not in config["hosts"]:
            die(f"No saved host called '{args.name}'.", EXIT_USAGE)
        config["default"] = args.name
        save_config(config)
        print(f"Default is now '{args.name}'.")
        return EXIT_OK

    die("Unknown hosts action.", EXIT_USAGE)


def cmd_ping(args, config):
    target = resolve_target(args, config)
    state = probe(target, timeout=args.timeout)
    print(f"{target}  ->  {state}")
    return EXIT_OK if state == "ready" else EXIT_FAILED


def cmd_update(args, config):
    target = resolve_target(args, config)

    if args.check:
        status, body = call(target, "/update/check", timeout=args.timeout)
        if args.json or not isinstance(body, dict) or body.get("request") != "Success":
            return report(status, body, args.json)
        latest = body.get("latest") or {}
        if body.get("available"):
            print(f"update available: {latest.get('sha','')[:7]}  {latest.get('message','')}")
            print(f"  by {latest.get('author','?')} on {latest.get('date','?')}")
            print(f"  installed: {(body.get('installed') or '?')[:7]}")
            return EXIT_OK
        print(body.get("reason", "Up to date."))
        return EXIT_OK

    print(f"Updating {target}")
    status, body = call(target, "/update", timeout=args.timeout)
    code = report(status, body, args.json)
    if code == EXIT_OK and args.wait:
        # Staging runs on the panel and ends with a restart, so this is not a
        # quick call - the default wait is generous on purpose.
        return EXIT_OK if wait_for(target, args.wait) else EXIT_FAILED
    return code


def cmd_restart(args, config):
    target = resolve_target(args, config)
    status, body = call(target, "/restart", timeout=args.timeout)
    code = report(status, body, args.json)
    if code == EXIT_OK and args.wait:
        return EXIT_OK if wait_for(target, args.wait) else EXIT_FAILED
    return code


def cmd_terminate(args, config):
    target = resolve_target(args, config)
    if not args.yes and sys.stdin.isatty():
        answer = input(f"Terminate {target}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Cancelled.")
            return EXIT_OK
    status, body = call(target, "/terminate", timeout=args.timeout)
    return report(status, body, args.json)


def cmd_notify(args, config):
    target = resolve_target(args, config)
    status, body = call(target, "/notify", {
        "icon": args.icon, "title": args.title, "body": args.body,
    }, timeout=args.timeout)
    return report(status, body, args.json)


def cmd_settings(args, config):
    target = resolve_target(args, config)
    params = {} if args.value is None else {"v": args.value}
    status, body = call(target, f"/settings/{args.path}", params, timeout=args.timeout)
    if args.json or not isinstance(body, dict) or body.get("request") != "Success":
        return report(status, body, args.json)
    print(f"{args.path} = {json.dumps(body.get('setting'))}")
    return EXIT_OK


def cmd_plugins(args, config):
    target = resolve_target(args, config)

    if args.plugins_action == "list":
        status, body = call(target, "/plugins", timeout=args.timeout)
        if args.json or not isinstance(body, dict) or body.get("request") != "Success":
            return report(status, body, args.json)

        loaded = body.get("loaded", [])
        pending = body.get("pending", [])
        print(f"loaded ({len(loaded)}):")
        for item in loaded:
            lock = "" if item.get("can_unload") else \
                   f"   required by {', '.join(item.get('dependants', []))}"
            print(f"   {item['key']:<24} {item.get('name', '')}{lock}")
        if pending:
            print(f"\npending ({len(pending)}):")
            for item in pending:
                needs = ", ".join(item.get("requirements", [])) or "no packages"
                print(f"   {item['key']:<24} needs {needs}")
        return EXIT_OK

    params = {"force": "1"} if getattr(args, "force", False) else {}
    status, body = call(target, f"/plugins/{args.key}/{args.plugins_action}",
                        params, timeout=args.timeout)
    return report(status, body, args.json)


def cmd_public(args, config):
    target = resolve_target(args, config)
    params = parse_pairs(args.params)
    status, body = call(target, f"/public/{args.endpoint}", params, timeout=args.timeout)
    return report(status, body, args.json)


def cmd_goto(args, config):
    target = resolve_target(args, config)
    key = args.page if args.page.startswith("#") else f"#{args.page}"
    params = parse_pairs(args.params)
    if args.override:
        params["override"] = "true"
    # The '#' is percent-encoded, or everything after it is a URL fragment the
    # server never sees.
    status, body = call(target, "/goto/" + urllib.parse.quote(key, safe=""),
                        params, timeout=args.timeout)
    return report(status, body, args.json)


def cmd_pages(args, config):
    target = resolve_target(args, config)
    status, body = call(target, "/pages", {}, timeout=args.timeout)
    if not args.json and isinstance(body, dict) and body.get("pages"):
        current = body.get("current")
        for page in body["pages"]:
            print(f"{'*' if page == current else ' '} {page}")
        return EXIT_OK
    return report(status, body, args.json)


def cmd_clipboard(args, config):
    target = resolve_target(args, config)
    if args.action == "clear":
        status, body = call(target, "/clipboard/clear", {}, timeout=args.timeout)
        return report(status, body, args.json)
    if args.action == "set":
        status, body = call(target, "/clipboard", {"text": args.text},
                            timeout=args.timeout)
        return report(status, body, args.json)
    status, body = call(target, "/clipboard", {}, timeout=args.timeout)
    if not args.json and isinstance(body, dict) and "text" in body:
        print(body["text"])
        return EXIT_OK
    return report(status, body, args.json)


def cmd_raw(args, config):
    target = resolve_target(args, config)
    params = parse_pairs(args.params)
    status, body = call(target, "/" + args.path.lstrip("/"), params,
                        timeout=args.timeout, method=args.method)
    return report(status, body, args.json)


def parse_pairs(pairs: list) -> dict:
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            die(f"Expected key=value, got '{pair}'.", EXIT_USAGE)
        key, value = pair.split("=", 1)
        out[key] = value
    return out


## ── argument parsing ──────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hactl.py",
        description="Control a Desktop Home Assistant panel over its backend API.",
        epilog="First run pairs with the panel: it asks for access, and somebody "
               "at the panel allows it.",
    )
    parser.add_argument("-t", "--target", help="saved host to use (default: the starred one)")
    parser.add_argument("--host", help="IP or hostname, bypassing saved hosts")
    parser.add_argument("--token", help="device token, bypassing the saved one")
    parser.add_argument("--port", type=int, help=f"port (default {DEFAULT_PORT})")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"seconds to wait per request (default {DEFAULT_TIMEOUT})")
    parser.add_argument("--json", action="store_true", help="print the raw reply")

    sub = parser.add_subparsers(dest="command", required=True)

    hosts = sub.add_parser("hosts", help="manage saved panels")
    hosts_sub = hosts.add_subparsers(dest="hosts_action", required=True)
    hosts_sub.add_parser("list", help="show saved panels")
    add = hosts_sub.add_parser("add", help="save a panel")
    add.add_argument("name")
    add.add_argument("--host", required=True)
    add.add_argument("--token", help="skip pairing and use this token")
    add.add_argument("--port", type=int)
    add.add_argument("--default", dest="make_default", action="store_true")
    remove = hosts_sub.add_parser("remove", help="forget a panel")
    remove.add_argument("name")
    use = hosts_sub.add_parser("use", help="set the default panel")
    use.add_argument("name")

    sub.add_parser("ping", help="check whether the panel is up and built")

    update = sub.add_parser("update", help="stage an update and restart")
    update.add_argument("--check", action="store_true",
                        help="only report whether one exists; download nothing")
    update.add_argument("--wait", nargs="?", type=int, const=180, default=0,
                        metavar="SECONDS", help="poll until it comes back (default 180)")

    restart = sub.add_parser("restart", help="restart the panel")
    restart.add_argument("--wait", nargs="?", type=int, const=90, default=0,
                         metavar="SECONDS")

    terminate = sub.add_parser("terminate", help="shut the panel down")
    terminate.add_argument("-y", "--yes", action="store_true", help="skip the confirmation")

    notify = sub.add_parser("notify", help="show a notification on the panel")
    notify.add_argument("icon")
    notify.add_argument("title")
    notify.add_argument("body")

    settings = sub.add_parser("settings", help="read or write a setting")
    settings.add_argument("path", help="dotted path, e.g. assistant.model.value")
    settings.add_argument("value", nargs="?", help="omit to read")

    plugins = sub.add_parser("plugins", help="inspect and manage plugins")
    plugins_sub = plugins.add_subparsers(dest="plugins_action", required=True)
    plugins_sub.add_parser("list", help="what is loaded and what is pending")
    for action, help_text in (
        ("info",      "show one plugin in detail"),
        ("reload",    "reload it"),
        ("load",      "load a pending plugin"),
        ("install",   "install its pip requirements, then load it"),
        ("uninstall", "remove its pip requirements"),
    ):
        node = plugins_sub.add_parser(action, help=help_text)
        node.add_argument("key")
    unload = plugins_sub.add_parser("unload", help="unload it")
    unload.add_argument("key")
    unload.add_argument("-f", "--force", action="store_true",
                        help="unload even if other plugins depend on it")

    public = sub.add_parser("public", help="call a plugin-registered endpoint")
    public.add_argument("endpoint")
    public.add_argument("params", nargs="*", metavar="key=value")

    raw = sub.add_parser("raw", help="call any path directly")
    raw.add_argument("path")
    raw.add_argument("params", nargs="*", metavar="key=value")
    raw.add_argument("-X", "--method", default="GET")

    goto = sub.add_parser("goto", help="switch the panel to a page")
    goto.add_argument("page", help="page key, with or without the leading '#'")
    goto.add_argument("params", nargs="*", metavar="key=value",
                      help="passed to the page as its data")
    goto.add_argument("--override", action="store_true",
                      help="rebuild even if that page is already showing")

    sub.add_parser("pages", help="list the pages the panel can show")

    clip = sub.add_parser("clipboard", help="read, set or clear the clipboard")
    clip_sub = clip.add_subparsers(dest="action")
    clip_sub.required = False
    clip_set = clip_sub.add_parser("set", help="put text on the clipboard")
    clip_set.add_argument("text")
    clip_sub.add_parser("clear", help="empty it")
    clip_sub.add_parser("get", help="print what is on it")

    return parser


HANDLERS = {
    "hosts": cmd_hosts,
    "ping": cmd_ping,
    "update": cmd_update,
    "restart": cmd_restart,
    "terminate": cmd_terminate,
    "notify": cmd_notify,
    "settings": cmd_settings,
    "plugins": cmd_plugins,
    "public": cmd_public,
    "raw": cmd_raw,
    "goto": cmd_goto,
    "pages": cmd_pages,
    "clipboard": cmd_clipboard,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config()
    try:
        return HANDLERS[args.command](args, config)
    except KeyboardInterrupt:
        print("\nCancelled.")
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
