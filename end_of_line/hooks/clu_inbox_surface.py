"""UserPromptSubmit hook: surface unprocessed clu inbox events.

Reads stdin (the hook payload — we don't need its contents but consume
it to be a well-behaved hook), filters `~/.config/clu/inbox/` to events
for the current project, emits `hookSpecificOutput` JSON on stdout, and
marks each surfaced event processed.

Failure semantics: exits 0 on any exception. A noisy crash would surface
on the operator's screen as a stderr line, which is louder than the
inbox event itself. The exception is logged to `~/.config/clu/inbox_hook.log`
for diagnosis.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

# Absolute import (not `from .. import`) so the script works when launched
# directly via `python /path/to/clu_inbox_surface.py` — Claude Code's
# UserPromptSubmit hook invokes it that way, with no package context. The
# pipx venv that installed end_of_line guarantees the absolute import resolves.
from end_of_line import inbox, registry
from end_of_line.notify_base import BlockerDetail, open_blockers_with_details

MAX_EVENTS = 20
MAX_BLOCKERS = 10
# Buffer under the documented 10K additionalContext cap; leaves room for
# the truncation footer and any inflation Claude Code may apply.
MAX_CONTEXT_CHARS = 9500

SECTION_HEADER = "\n## Active blockers\n\n"
BLOCKER_TEMPLATE = (
    "Plan `{slug}`, phase `{phase}`, blocker `{blocker_id}`:\n"
    "Question: {question}\n"
    "Options:\n"
    "{options_list}\n"
)
INSTRUCTION = (
    "\nIf the user's next message reads as a reply to one of these "
    "blockers (letter, number, or natural pick), call "
    "`clu answer --plan <slug> <answer>` via Bash. "
    "If multiple blockers are open and the reply is ambiguous, ask "
    "the user which plan they mean — don't guess.\n"
)

# Investigate-then-recommend contract appended when any tool_stuck event
# appears in the surfaced set. The primary session should diagnose
# autonomously but never auto-intervene — destructive recovery requires
# explicit operator approval per the operator-approval checkpoint.
TOOL_STUCK_INSTRUCTION = (
    "\n## Stuck-tool events\n\n"
    "One or more clu workers have a Bash tool that's been running with "
    "near-zero CPU for several minutes (TOOL_STUCK events above). "
    "Investigate autonomously: walk the worker's process tree via "
    "`ps -p <worker_pid>` then `pgrep -P <worker_pid>` to find the "
    "wedged subprocess, and synthesize a kill recommendation naming the "
    "specific PIDs. Surface the recommendation proactively so the operator "
    "sees it without asking. **Do NOT run `kill`, `clu release-claim`, "
    "or `clu force-complete` until the operator explicitly approves** — "
    "the operator-approval checkpoint in user-level CLAUDE.md mandates "
    "this for any destructive intervention.\n"
)

# #70 operator-dashboard instruction blocks. Each fires once per surface
# pass when its event class is present in the inbox set. Same
# investigate-then-recommend-then-await-approval contract as TOOL_STUCK.

ATTESTATION_REFUSED_INSTRUCTION = (
    "\n## Attestation gate refusal\n\n"
    "One or more clu workers hit the verify or simplify attestation gate "
    "(`attestation_refused` events above). The worker's claim is still "
    "live — the gate refused with `STATUS_TRANSITION` and left the claim "
    "in place so the worker can stamp + retry. Investigate autonomously: "
    "read the per-worker log at "
    "`<project>/plans/.orchestrator/logs/<phase>.<token>.log`, check "
    "`git log` on the worker's branch, compare the `stamped_at` SHA in "
    "the event payload to the worker's current HEAD. Recommend the fix "
    "proactively — typically `clu verify` to re-stamp (when the verify "
    "command would now pass), `clu attest --simplify` to confirm a "
    "narrow diff, OR `clu complete --skip-verify` / `--skip-simplify` "
    "when the gate is a false positive. **Do NOT run any of these until "
    "the operator explicitly approves** — every bypass is an audit-event "
    "the operator owns the decision on, per the operator-approval "
    "checkpoint in user-level CLAUDE.md.\n"
)

STALLED_CLAIM_INSTRUCTION = (
    "\n## Stalled-claim events\n\n"
    "One or more clu plans have a live claim whose lease expired without "
    "a `clu complete` (`stalled_claim` events above). The worker is "
    "either dead or has uncommitted work on disk it never reported. "
    "Investigate autonomously: read the per-worker log at "
    "`<project>/plans/.orchestrator/logs/<phase>.<token>.log` for the "
    "claim's token, run `ps -p <pid>` against the `claimed_by.pid` in "
    "the event payload, check `git status` and `git log` in the project "
    "/ worktree for uncommitted work the worker may have written. "
    "Recommend a recovery path — `clu force-complete --plan <P> --phase "
    "<X> --commit <sha>` if work is on disk, `clu release-claim` if the "
    "worker is dead with nothing recoverable, or `clu retry` if the "
    "worker exited cleanly and the phase should re-dispatch from scratch. "
    "**Do NOT run any of these until the operator explicitly approves** — "
    "every recovery path mutates state the operator owns.\n"
)


# Registry of (event_type, instruction) pairs for #70 wedge-class
# composition. main() iterates this once and appends each instruction
# block at most once per surface pass. Adding a new wedge class is
# one entry, not a four-step ritual (constant + predicate + main()
# wire + parts splice). Order here is the render order in
# additionalContext — most actionable first.
WEDGE_INSTRUCTION_BLOCKS: list[tuple[str, str]] = [
    ("tool_stuck", TOOL_STUCK_INSTRUCTION),
    ("attestation_refused", ATTESTATION_REFUSED_INSTRUCTION),
    ("stalled_claim", STALLED_CLAIM_INSTRUCTION),
]


def _has_event_type(events: Iterable[dict], type_: str) -> bool:
    return any(e.get("type") == type_ for e in events)


def _wedge_sections(events: list[dict]) -> list[str]:
    """Return the instruction blocks whose event class is present."""
    return [block for type_, block in WEDGE_INSTRUCTION_BLOCKS
            if _has_event_type(events, type_)]


def _log_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "clu" / "inbox_hook.log"


def _resolve_project_root() -> str:
    """`git rev-parse --show-toplevel`, falling back to CWD on failure.

    `cwd=None` so the subprocess inherits this process's CWD (which is
    Claude Code's CWD at the moment of the user prompt). Timeout caps
    the latency floor at 2s even on a pathological filesystem.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return os.getcwd()


def _format_event(e: dict) -> str:
    ts = e.get("timestamp", "?")
    slug = e.get("plan_slug", "?")
    kind = e.get("type", "?")
    summary = e.get("summary", "")
    return f"  [{ts}] {slug} / {kind}: {summary}"


def _build_context(events: Iterable[dict]) -> str:
    events = list(events)
    if not events:
        return ""
    # Show the most recent MAX_EVENTS; summarize the rest in a footer.
    if len(events) > MAX_EVENTS:
        truncated_count = len(events) - MAX_EVENTS
        capped = events[-MAX_EVENTS:]
    else:
        truncated_count = 0
        capped = events
    lines = ["clu inbox (unprocessed):"]
    lines.extend(_format_event(e) for e in capped)
    if truncated_count > 0:
        lines.append(
            f"  (+ {truncated_count} older events — run `clu inbox` "
            "to see all)"
        )
    out = "\n".join(lines)
    if len(out) > MAX_CONTEXT_CHARS:
        footer = "\n  (truncated)"
        out = out[: MAX_CONTEXT_CHARS - len(footer)] + footer
    return out


def _build_blockers_section(blockers: list[BlockerDetail]) -> str:
    if not blockers:
        return ""
    capped = blockers[:MAX_BLOCKERS]
    overflow = len(blockers) - MAX_BLOCKERS
    parts = []
    for b in capped:
        opts = "\n".join(f"  [{i}] {opt}" for i, opt in enumerate(b.options))
        parts.append(BLOCKER_TEMPLATE.format(
            slug=b.plan_slug,
            phase=b.phase_id,
            blocker_id=b.blocker_id,
            question=b.question,
            options_list=opts,
        ))
    body = "\n".join(parts)
    if overflow > 0:
        body += (
            f"\n... +{overflow} more open blockers — "
            "see `clu list` for the full set."
        )
    return SECTION_HEADER + body + INSTRUCTION


def main() -> int:
    # Guard against shell-env inheritance of CLU_TEST_MODE — a hook invoked
    # from a test-mode shell must not false-trip the XDG guard.
    os.environ.pop("CLU_TEST_MODE", None)
    try:
        # Consume stdin so Claude Code doesn't see a broken-pipe on its
        # write side. We don't actually need the payload.
        _ = sys.stdin.read()
        project_root = _resolve_project_root()
        events = inbox.list_for_project(project_root)
        events_context = _build_context(events)

        entries = registry.entries()
        blockers = open_blockers_with_details(entries, project_root)
        blockers_section = _build_blockers_section(blockers)

        # Append the investigate-then-recommend contracts once per class
        # for any wedge event present, in the registry order. The inbox
        # cap in _build_context never drops these wedge events because
        # they're rare.
        wedge_sections = _wedge_sections(events)

        parts = [s for s in (events_context, blockers_section, *wedge_sections) if s]
        if not parts:
            return 0
        context = "\n\n".join(parts)

        payload = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }
        sys.stdout.write(json.dumps(payload))
        # Mark only the events we actually surfaced (the capped set).
        for e in events[-MAX_EVENTS:]:
            inbox.mark_processed(e["id"])
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
