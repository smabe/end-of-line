"""SessionStart hook: instruct the session to arm a long-running Monitor
on `clu watch --all --operator`.

The monitor-lifecycle research (#69) confirmed prior Monitors survive
both `/clear` and `/compact` — pre-reset Monitor tasks keep delivering
events into the new context until their own `timeout_ms`. The remaining
gap is **cold-start**: a brand-new conversation has no prior Monitor.
This hook closes that gap by emitting an arming instruction as
`additionalContext` on SessionStart.

The arming is idempotent on the agent side — Claude doesn't double-arm
when a Monitor is already in flight, and operator-side wedge filtering
is the same regardless of the arming session.

Failure semantics mirror `clu_inbox_surface.py`: exit 0 on any
exception (the hook can't be allowed to crash the session start),
log to `~/.config/clu/session_start_hook.log` for diagnosis.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Kept under the documented 10K additionalContext cap with room to spare.
# Concise on purpose — the session pays a context-token cost for every
# fresh start.
INSTRUCTION = (
    "## clu operator dashboard\n\n"
    "This session was started in a clu-managed environment. To receive "
    "live wedge events (`tool_stuck`, `phase_blocked`, "
    "`attestation_refused`, `stalled_claim_notified`) across every "
    "registered plan on this host, arm a persistent Monitor now — "
    "unless one is already in flight from a prior session "
    "(prior Monitors survive `/clear` and `/compact` per #69 findings):\n\n"
    "```\n"
    "Monitor(\n"
    "    command=\"clu watch --all --operator\",\n"
    "    persistent=True,\n"
    "    description=\"clu operator dashboard\",\n"
    ")\n"
    "```\n\n"
    "Wedge events stream as they happen. The operator-approval "
    "checkpoint from user-level CLAUDE.md still applies — never auto-run "
    "`kill`, `clu release-claim`, or `clu force-complete` against a "
    "surfaced wedge; investigate, recommend, and wait for explicit "
    "operator approval before any destructive action.\n"
)


def _log_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "clu" / "session_start_hook.log"


def main() -> int:
    # Guard against shell-env inheritance of CLU_TEST_MODE — a hook
    # invoked from a test-mode shell must not false-trip XDG guards.
    os.environ.pop("CLU_TEST_MODE", None)
    try:
        # Consume stdin so Claude Code doesn't see a broken-pipe on its
        # write side. The SessionStart payload isn't needed.
        _ = sys.stdin.read()
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": INSTRUCTION,
            }
        }
        sys.stdout.write(json.dumps(payload))
        return 0
    except Exception as exc:  # graceful — never alarm the operator
        try:
            log = _log_path()
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a") as f:
                f.write(f"{exc!r}\n")
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
