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

# Compressed from /clu-plan SKILL.md "Reacting to task-list protocol
# notifications" (lines 327-373). Emitted only when active plans are detected.
TASK_LIST_PROTOCOL_INSTRUCTION = (
    "## clu task-list protocol\n\n"
    "The `--task-list` Monitor stream emits two line shapes:\n\n"
    "- `TASK_CREATE task=<id> [parent=<slug>] status=pending` — bootstrap "
    "lines at startup, one per plan + phase. The parent line (`task=<slug>`, "
    "no `parent=` field) is the plan itself. Child lines (`task=<slug>/<phase>`) "
    "carry `parent=<slug>`.\n"
    "- `TASK_UPDATE task=<id> [parent=<slug>] status=<state> msg=\"<one-liner>\"` "
    "— fired on state transitions. `parent=` present on phase-scoped events, "
    "absent on plan-scoped events.\n\n"
    "**On TASK_CREATE bootstrap batch:** call `TaskCreate` with all tasks at "
    "`status=pending`. Signal hierarchy in `subject`:\n"
    "- Parent (`task=<slug>`, no `parent=`): `subject = <slug>`\n"
    "- Child (`task=<slug>/<phase>`, with `parent=<slug>`): "
    "`subject = \"└ <phase>\"` — U+2514 box-drawing char + space + phase id.\n\n"
    "**On each TASK_UPDATE:** call `TaskUpdate` matching by `task=`. "
    "**Do NOT re-set subject** — only update `status` and `description`. "
    "Re-setting the subject strips the `└ ` glyph and visually un-nests the tree.\n\n"
    "**Teardown:** when a `TASK_UPDATE task=<slug> status=completed` arrives "
    "with NO `parent=` field (plan-scoped), call `TaskStop`. "
    "Paused plans are NOT teardown triggers — the operator may resume them.\n\n"
    "**Out-of-order arrivals:** if `TASK_UPDATE` arrives before its `TASK_CREATE` "
    "(race, rare), buffer ~1s and retry. If still no match, create on-the-fly "
    "using the same nesting convention (`└ <phase>` if `parent=` present).\n"
)


def _active_plans_for_cwd() -> list[str]:
    """Return slugs with status=running in the current CWD's registry entries.

    Local import keeps the no-active-plans path cheap; tolerates all failures
    by returning [].
    """
    try:
        from end_of_line import registry, state as st  # local — avoid module-load cost
        cwd = Path(os.getcwd()).resolve()
        slugs: list[str] = []
        for entry in registry.entries_for_project(cwd):
            data = registry.load_entry_state(entry)
            if data is None:
                continue
            if data.get("status") == st.STATUS_RUNNING:
                slugs.append(entry.plan_slug)
        return slugs
    except Exception:
        return []


def _per_plan_arming_block(slugs: list[str]) -> str:
    """One fenced Monitor(...) block per active slug with --task-list flag."""
    intro = (
        "## clu per-plan task-list Monitors\n\n"
        "The following plans are active in this project. Arm one persistent "
        "Monitor per plan now — skip any whose Monitor is already in flight "
        "(prior Monitors survive `/clear` and `/compact` per #69):\n\n"
    )
    blocks = []
    for slug in slugs:
        blocks.append(
            "```\n"
            "Monitor(\n"
            f"    command=\"clu watch --project . --plan {slug} --task-list\",\n"
            "    persistent=True,\n"
            "    timeout_ms=3600000,\n"
            f"    description=\"clu {slug} phase progress\",\n"
            ")\n"
            "```\n"
        )
    return intro + "\n".join(blocks) + "\n"


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
        additional_context = INSTRUCTION
        try:
            slugs = _active_plans_for_cwd()
            if slugs:
                additional_context += _per_plan_arming_block(slugs)
                additional_context += TASK_LIST_PROTOCOL_INSTRUCTION
        except Exception:
            pass
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": additional_context,
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
