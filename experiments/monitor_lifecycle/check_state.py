#!/usr/bin/env python3
"""Inspect producer state from any session (#69 lifecycle experiment).

Reports for every `{label}.pid` file in the log dir:
- PID and whether the process is still alive
- Latest tick number, timestamp, and seconds-since-now
- Total ticks observed

Run from a fresh session after `/clear` or `/compact` to determine whether the
producer processes survived the destructive operation independently of the
Monitor tool that was watching them.

Run:
    python3 check_state.py
    python3 check_state.py --log-dir /custom/path
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

DEFAULT_LOG_DIR = Path.home() / ".cache" / "clu-monitor-lifecycle"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _summarize(log_dir: Path, label: str) -> dict:
    pid_path = log_dir / f"{label}.pid"
    log_path = log_dir / f"{label}.log"
    out: dict = {"label": label}
    try:
        pid = int(pid_path.read_text().strip())
        out["pid"] = pid
        out["alive"] = _pid_alive(pid)
    except (FileNotFoundError, ValueError) as exc:
        out["pid"] = None
        out["alive"] = False
        out["pid_error"] = str(exc)

    try:
        lines = log_path.read_text().splitlines()
        out["total_ticks"] = len(lines)
        if lines:
            last = lines[-1]
            out["last_line"] = last
            parts = last.split()
            ts = parts[2] if len(parts) > 2 else ""
            try:
                dt = _dt.datetime.fromisoformat(ts)
                age = (_dt.datetime.now(_dt.timezone.utc) - dt).total_seconds()
                out["age_seconds"] = round(age, 1)
            except ValueError:
                pass
    except FileNotFoundError:
        out["total_ticks"] = 0
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = p.parse_args()

    if not args.log_dir.exists():
        print(f"(no log dir: {args.log_dir})")
        return 0

    labels = sorted({f.stem for f in args.log_dir.iterdir() if f.suffix == ".pid"})
    if not labels:
        print(f"(no producers found in {args.log_dir})")
        return 0

    rows = [_summarize(args.log_dir, lbl) for lbl in labels]

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    print(f"# producer state @ {args.log_dir}")
    for r in rows:
        alive = "ALIVE" if r.get("alive") else "DEAD "
        pid = r.get("pid") or "?"
        ticks = r.get("total_ticks", 0)
        age = r.get("age_seconds")
        age_str = f"{age:.1f}s ago" if age is not None else "?"
        print(f"  {r['label']}  pid={pid:<8} {alive}  ticks={ticks:<4} last={age_str}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
