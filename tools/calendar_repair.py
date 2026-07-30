#!/usr/bin/env python3
"""
Inspect and repair the calendar store.

Reports first and changes nothing unless told to. Run it against a panel that
is not running, or restart the panel afterwards - the app holds the events in
memory and writes them back on its own schedule.

    ./tools/calendar_repair.py                  # list everything stored
    ./tools/calendar_repair.py --duplicates     # group events that look alike
    ./tools/calendar_repair.py --suspect-spans  # spans longer than their repeat
    ./tools/calendar_repair.py --fix-spans      # clear those spans (asks first)
    ./tools/calendar_repair.py --remove "Standup"   # remove every copy by title

Stdlib only, so it runs anywhere the panel does without the venv.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

GAP_DAYS = {"daily": 1, "weekly": 7, "monthly": 28, "yearly": 365}


def default_store_path() -> Path:
    """Mirrors src.constants.get_data_dir(APP_NAME) / calendar / events.json."""
    app = "HomeAssistant"
    if sys.platform.startswith("win"):
        base = Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "share"
    return base / app / "calendar" / "events.json"


def load(path: Path) -> dict:
    if not path.is_file():
        sys.exit(f"No store at {path}\nPass --path if it lives somewhere else.")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        sys.exit(f"Could not read {path}: {e}")
    if isinstance(raw, dict) and "events" in raw:
        raw = raw["events"]
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        return {e.get("key", str(i)): e for i, e in enumerate(raw)}
    sys.exit(f"Unexpected shape in {path}: {type(raw).__name__}")


def save(path: Path, events: dict, original: object) -> None:
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    if isinstance(original, dict) and "events" in original:
        original["events"] = events
        payload = original
    else:
        payload = events
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWritten. Previous file copied to {backup.name}")


def span_days(ev: dict) -> int:
    try:
        first = date.fromisoformat(ev.get("day", ""))
        last = date.fromisoformat(ev.get("end_day") or ev.get("day", ""))
    except ValueError:
        return 0
    return max(0, (last - first).days)


def describe(key: str, ev: dict) -> str:
    bits = [ev.get("day", "?")]
    if ev.get("end_day"):
        bits.append(f"-> {ev['end_day']} ({span_days(ev) + 1}d)")
    if ev.get("time"):
        bits.append(ev["time"] + (f"-{ev['end_time']}" if ev.get("end_time") else ""))
    else:
        bits.append("all day")
    if ev.get("repeat"):
        bits.append(f"repeats {ev['repeat']}")
        bits.append(f"until {ev['repeat_until']}" if ev.get("repeat_until")
                    else "FOREVER")
    if ev.get("owner"):
        bits.append(f"[{ev['owner']}]")
    if ev.get("source") and ev["source"] != "local":
        bits.append(f"<{ev['source']}>")
    return f"  {key[:14]:16} {ev.get('title', '?')[:28]:30} " + "  ".join(bits)


def group_alike(events: dict) -> dict:
    groups: dict = {}
    for key, ev in events.items():
        if ev.get("source") == "holiday":
            continue
        sig = (ev.get("owner", ""), (ev.get("title") or "").strip().lower(),
               ev.get("time", ""), ev.get("end_time", ""))
        groups.setdefault(sig, []).append((key, ev))
    return groups


def suspect_spans(events: dict) -> list:
    """Events whose per-occurrence span reaches into their own next occurrence."""
    out = []
    for key, ev in events.items():
        repeat = ev.get("repeat") or ""
        if not repeat or not ev.get("end_day"):
            continue
        gap = GAP_DAYS.get(repeat, 0) * max(1, int(ev.get("repeat_interval") or 1))
        if gap and span_days(ev) >= gap:
            out.append((key, ev, gap))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=None)
    ap.add_argument("--duplicates", action="store_true")
    ap.add_argument("--suspect-spans", action="store_true")
    ap.add_argument("--fix-spans", action="store_true")
    ap.add_argument("--remove", metavar="TITLE")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation")
    args = ap.parse_args()

    path = args.path or default_store_path()
    original = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    events = load(path)
    print(f"{path}\n{len(events)} stored events\n")

    if args.remove:
        wanted = args.remove.strip().lower()
        doomed = [(k, e) for k, e in events.items()
                  if (e.get("title") or "").strip().lower() == wanted]
        if not doomed:
            print(f"Nothing titled '{args.remove}'.")
            return
        print(f"Would remove {len(doomed)} event(s):")
        for k, e in doomed:
            print(describe(k, e))
        if not args.yes and input("\nRemove these? [y/N] ").strip().lower() != "y":
            print("Nothing changed.")
            return
        for k, _ in doomed:
            events.pop(k, None)
        save(path, events, original)
        return

    if args.fix_spans or args.suspect_spans:
        bad = suspect_spans(events)
        if not bad:
            print("No event has a span that reaches its own next occurrence.")
            return
        print("Spans at least as long as their own repeat interval.")
        print("These draw as overlapping bars across the calendar:\n")
        for k, e, gap in bad:
            print(describe(k, e))
            print(f"{'':16}   span {span_days(e) + 1}d vs a {gap}d gap "
                  f"-- 'end_day' was probably meant to be 'repeat_until'")
        if not args.fix_spans:
            print("\nRe-run with --fix-spans to clear end_day on these.")
            return
        if not args.yes and input("\nClear end_day on these? [y/N] ").strip().lower() != "y":
            print("Nothing changed.")
            return
        for k, _, _ in bad:
            events[k]["end_day"] = ""
        print(f"Cleared end_day on {len(bad)} event(s).")
        save(path, events, original)
        return

    if args.duplicates:
        groups = group_alike(events)
        dupes = {s: g for s, g in groups.items() if len(g) > 1}
        if not dupes:
            print("Nothing stored more than once.")
            return
        for sig, group in sorted(dupes.items(), key=lambda kv: -len(kv[1])):
            print(f"'{sig[1]}' x{len(group)}")
            for k, e in sorted(group, key=lambda kv: kv[1].get("day", "")):
                print(describe(k, e))
            print()
        print("Remove them all with:  --remove \"<title>\"")
        return

    for key, ev in sorted(events.items(), key=lambda kv: kv[1].get("day", "")):
        print(describe(key, ev))


if __name__ == "__main__":
    main()
