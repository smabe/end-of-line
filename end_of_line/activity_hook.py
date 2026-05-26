"""Thin entry point for Claude Code PreToolUse / PostToolUse hooks.

Hot path — fires on every Bash tool call. Imports only `end_of_line.state`
(+ stdlib) so cold-start cost stays minimal. Compare to `clu activity`,
which imports the full orchestrator surface (dispatch, fleet, monitor,
queue, supervisor, watch, notify) and pays ~30ms extra per invocation.

Invoke as `python3 -m end_of_line.activity_hook --start-bash ...`. The
full `clu activity ...` CLI still works for backward compat; both
delegate to `state.stamp_activity_marker`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import state as st


def _resolve_state_path(project_root: Path, plan: str) -> Path:
    """Resolve `<project>/<plan_dir>/.orchestrator/<plan>.state.json`.

    Reads `plan_dir` from `<project>/.orchestrator.json` directly to avoid
    importing the full `end_of_line.config` module. Falls back to "plans"
    if the config file is missing or unparseable — same default the
    orchestrator uses.
    """
    plan_dir = "plans"
    cfg_path = project_root / ".orchestrator.json"
    try:
        with cfg_path.open() as fh:
            raw = json.load(fh)
        if isinstance(raw, dict) and isinstance(raw.get("plan_dir"), str):
            plan_dir = raw["plan_dir"]
    except (OSError, json.JSONDecodeError):
        pass
    return project_root / plan_dir / ".orchestrator" / f"{plan}.state.json"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="clu-activity-hook")
    p.add_argument("--project", default=os.getcwd())
    p.add_argument("--plan", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--token", required=True)
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--start-bash", action="store_true")
    grp.add_argument("--end-bash", action="store_true")
    args = p.parse_args(argv)

    if not (args.start_bash or args.end_bash):
        print(
            "activity_hook: one of --start-bash / --end-bash required",
            file=sys.stderr,
        )
        return 1

    state_path = _resolve_state_path(Path(args.project).resolve(), args.plan)
    action = "start" if args.start_bash else "end"
    try:
        st.stamp_activity_marker(
            state_path,
            token=args.token,
            phase=args.phase,
            action=action,
            timeout_seconds=2.0,
        )
    except st.ClaimMismatch as exc:
        # Stale token (claim released, phase advanced, supervisor reaped).
        # The hook snippet's `|| true` masks the non-zero exit so the
        # worker's Bash call proceeds. Stderr surfaces the cause for
        # operators debugging hook setup who invoke this manually.
        print(f"activity_hook: claim mismatch — {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
