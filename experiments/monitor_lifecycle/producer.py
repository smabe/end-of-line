#!/usr/bin/env python3
"""Tick producer for the Monitor lifecycle experiment (#69).

Emits `tick {n} {iso_ts} label={label} pid={pid}` lines to stdout on a fixed
interval. Each tick is also appended to `{log-dir}/{label}.log` and the
process PID is written to `{log-dir}/{label}.pid` so a fresh session (post
/clear or /compact) can observe whether the producer process survived
independently of any Monitor that was watching it.

Run:
    python3 producer.py --label A
    python3 producer.py --label A --interval 2 --max-ticks 60
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import time
from pathlib import Path

DEFAULT_LOG_DIR = Path.home() / ".cache" / "clu-monitor-lifecycle"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--label", required=True, help="short tag (A, B, C...) used in filenames + tick lines")
    p.add_argument("--interval", type=float, default=5.0, help="seconds between ticks (default 5)")
    p.add_argument("--max-ticks", type=int, default=360, help="hard cap (default 360 = 30min at 5s)")
    p.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    args = p.parse_args()

    args.log_dir.mkdir(parents=True, exist_ok=True)
    pid_path = args.log_dir / f"{args.label}.pid"
    log_path = args.log_dir / f"{args.label}.log"

    pid = os.getpid()
    pid_path.write_text(f"{pid}\n")

    for n in range(1, args.max_ticks + 1):
        ts = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        line = f"tick {n} {ts} label={args.label} pid={pid}"
        print(line, flush=True)
        with log_path.open("a") as f:
            f.write(line + "\n")
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
