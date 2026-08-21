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
    '    command="clu watch --all --operator",\n'
    "    persistent=True,\n"
    '    description="clu operator dashboard",\n'
    ")\n"
    "```\n\n"
    "Wedge events stream as they happen. The operator-approval "
    "checkpoint from user-level CLAUDE.md still applies — never auto-run "
    "`kill`, `clu release-claim`, or `clu force-complete` against a "
    "surfaced wedge; investigate, recommend, and wait for explicit "
    "operator approval before any destructive action.\n"
)

# Open-blocker surfacing budget. Defined HERE — the same-named constants in
# clu_inbox_surface.py belong to the retired inbox surface, which this hook
# must not import. MAX_CONTEXT_CHARS is the ceiling on the WHOLE
# additionalContext block (dashboard + per-plan + protocol + blockers); the
# documented hard cap is 10,000, and this stays under it with headroom for any
# inflation Claude Code applies.
MAX_BLOCKERS = 10
MAX_CONTEXT_CHARS = 9500
# Per-question / per-options truncation keeps a pathological blocker from
# eating the budget. Options are bounded too — a blocker with many long option
# strings is as capable of blowing the ceiling as a long question.
MAX_QUESTION_CHARS = 280
MAX_OPTIONS_CHARS = 600
# Room reserved for the not-shown footer so the reply instruction (which names
# --blocker) always survives the per-entry budget check.
_BLOCKER_FOOTER_RESERVE = 180
_TRUNC_MARKER = " …(truncated)"

BLOCKER_SECTION_HEADER = "\n## Open blockers awaiting a reply\n\n"
BLOCKER_TEMPLATE = (
    "Plan `{slug}`, phase `{phase}`, blocker `{blocker_id}`:\n"
    "Question: {question}\n"
    "Options:\n"
    "{options_list}\n"
)
# Names the `--blocker` form that phase answer-scope made addressable. All
# three of --project / --plan / --blocker are required together for the direct
# path; `.` resolves to the session's project root.
BLOCKER_REPLY_INSTRUCTION = (
    "\nTo answer one, run via Bash:\n"
    "```\n"
    "clu answer --project . --plan <slug> --blocker <q-N> <answer>\n"
    "```\n"
    "`<answer>` is an option number (0-indexed) or free text. When more than "
    "one blocker is open, ask the operator which one they mean and pass that "
    "blocker's `--blocker <q-N>` — never guess.\n"
)

# Compressed from /clu-plan SKILL.md "Reacting to task-list protocol
# notifications". Emitted only when active plans are detected.
TASK_LIST_PROTOCOL_INSTRUCTION = (
    "## clu task-list protocol\n\n"
    "If `TaskCreate` / `TaskUpdate` are unavailable (not in the default "
    "toolset on Opus 4.8 / Sonnet 5 / Fable 5 / Mythos 5 and newer, per "
    "Claude Code 2.1.233), tell the operator once — set "
    "`CLAUDE_CODE_ENABLE_TODO_TOOLS=1` in `~/.claude/settings.json` and "
    "restart — then handle the lines below as plain text: surface "
    "blockers, halts, and plan completion directly rather than dropping "
    "them.\n\n"
    "The `--task-list` Monitor stream emits two line shapes:\n\n"
    "- `TASK_CREATE task=<id> [parent=<slug>] status=pending` — bootstrap "
    "lines at startup, one per plan + phase. The parent line (`task=<slug>`, "
    "no `parent=` field) is the plan itself. Child lines (`task=<slug>/<phase>`) "
    "carry `parent=<slug>`.\n"
    '- `TASK_UPDATE task=<id> [parent=<slug>] status=<state> msg="<one-liner>"` '
    "— fired on state transitions. `parent=` present on phase-scoped events, "
    "absent on plan-scoped events.\n\n"
    "**On TASK_CREATE bootstrap batch:** call `TaskCreate` with all tasks at "
    "`status=pending`. Signal hierarchy in `subject`:\n"
    "- Parent (`task=<slug>`, no `parent=`): `subject = <slug>`\n"
    "- Child (`task=<slug>/<phase>`, with `parent=<slug>`): "
    '`subject = "└ <phase>"` — U+2514 box-drawing char + space + phase id.\n\n'
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


def _scan_entries(entries) -> tuple[bool, list[str], list[dict]]:
    """One pass over the pre-loaded registry list: returns
    (any_live, running_slugs_for_cwd, open_blockers_for_cwd).

    `any_live` — some entry's state is non-terminal (or unreadable, which
    counts as live: a corrupt state file must not silence the dashboard).
    Registry rows outlive plan liveness (done/halted rows persist until an
    archive sweep), so the emit gate keys on state, not mere registration.

    `running_slugs_for_cwd` — STATUS_RUNNING entries registered for the
    current CWD, for the per-plan Monitor blocks.

    `open_blockers_for_cwd` — every unanswered blocker on a plan registered for
    the current CWD, as render-dicts (slug/phase_id/id/question/options). This
    read is scoped to the CWD's plans (the plan cost-control), but rides the
    single host-wide walk `any_live` already needs — a separate
    `entries_for_project` pass would re-open the CWD plans' databases a second
    time. It bypasses the liveness gate ON PURPOSE: a paused plan is terminal,
    and an SLA-breached blocker is exactly what pauses a plan, so gating
    blockers on liveness would hide the one that has waited longest.

    Tolerates all failures by failing open on `any_live`.
    """
    if not entries:
        return (False, [], [])
    try:
        from end_of_line import registry  # local — avoid module-load cost
        from end_of_line import state as st

        cwd = Path(os.getcwd()).resolve()
        any_live = False
        slugs: list[str] = []
        blockers: list[dict] = []
        for entry in entries:
            data = registry.load_entry_state(entry)
            if data is None:
                any_live = True  # unreadable state: assume live
                continue
            status = data.get("status")
            if status not in st.TERMINAL_STATUSES:
                any_live = True
            is_cwd = Path(entry.project_root).resolve() == cwd
            if status == st.STATUS_RUNNING and is_cwd:
                slugs.append(entry.plan_slug)
            if is_cwd:
                for b in st.open_blockers(data):
                    blockers.append(
                        {
                            "slug": entry.plan_slug,
                            "phase_id": b["phase_id"],
                            "id": b["id"],
                            "question": b.get("question", ""),
                            "options": list(b.get("options", [])),
                        }
                    )
        return (any_live, slugs, blockers)
    except Exception:
        return (True, [], [])


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
            f'    command="clu watch --project . --plan {slug} --task-list",\n'
            "    persistent=True,\n"
            "    timeout_ms=3600000,\n"
            f'    description="clu {slug} phase progress",\n'
            ")\n"
            "```\n"
        )
    return intro + "\n".join(blocks) + "\n"


def _render_blocker(b: dict) -> str:
    """One blocker as slug/phase/id + question (truncated) + numbered options."""
    question = b.get("question") or ""
    if len(question) > MAX_QUESTION_CHARS:
        question = question[:MAX_QUESTION_CHARS].rstrip() + " …(truncated)"
    options = b.get("options") or []
    opts = (
        "\n".join(f"  [{i}] {opt}" for i, opt in enumerate(options))
        if options
        else "  (no preset options — answer with free text)"
    )
    if len(opts) > MAX_OPTIONS_CHARS:
        opts = opts[:MAX_OPTIONS_CHARS].rstrip() + _TRUNC_MARKER
    return BLOCKER_TEMPLATE.format(
        slug=b["slug"],
        phase=b["phase_id"],
        blocker_id=b["id"],
        question=question,
        options_list=opts,
    )


def _blockers_block(blockers: list[dict], budget: int) -> str:
    """Render up to MAX_BLOCKERS open blockers + a `--blocker` routing
    instruction, keeping the section within `budget` chars.

    Budget-aware because the dashboard / per-plan / protocol blocks are already
    assembled when this runs; the caller passes the room that remains under
    MAX_CONTEXT_CHARS so the whole additionalContext stays bounded. Entries are
    dropped (not mid-truncated) once they would overrun, and the reply
    instruction is reserved so it always survives.
    """
    if not blockers:
        return ""
    total = len(blockers)
    room = budget - len(BLOCKER_SECTION_HEADER) - len(BLOCKER_REPLY_INSTRUCTION) - _BLOCKER_FOOTER_RESERVE
    parts: list[str] = []
    shown = 0
    for b in blockers[:MAX_BLOCKERS]:
        entry = _render_blocker(b)
        if len(entry) > room:
            if shown:
                break
            # First entry alone overruns even after per-field truncation
            # (pathological options): hard-truncate it so the reserved reply
            # instruction — appended AFTER these entries — always survives.
            entry = entry[: max(0, room - len(_TRUNC_MARKER))].rstrip() + _TRUNC_MARKER
        parts.append(entry)
        room -= len(entry)
        shown += 1
    not_shown = total - shown
    footer = ""
    if not_shown > 0:
        footer = (
            f"\n... and {not_shown} more open blocker(s) not shown — run "
            "`clu blockers list --project . --plan <slug>` for the rest.\n"
        )
    return BLOCKER_SECTION_HEADER + "\n".join(parts) + footer + BLOCKER_REPLY_INSTRUCTION


def _log_path() -> Path:
    from end_of_line._xdg_guard import clu_config_dir  # local — avoid module-load cost

    return clu_config_dir() / "session_start_hook.log"


def main() -> int:
    # Guard against shell-env inheritance of CLU_TEST_MODE — a hook
    # invoked from a test-mode shell must not false-trip XDG guards.
    os.environ.pop("CLU_TEST_MODE", None)
    try:
        # Consume stdin so Claude Code doesn't see a broken-pipe on its
        # write side. The SessionStart payload isn't needed.
        _ = sys.stdin.read()
        # The dashboard instruction costs every fresh session ~150 context
        # tokens; emit nothing unless some registered plan is live (non-
        # terminal state). One registry read feeds both the emit gate and
        # the per-plan scan. A registry read failure falls back to emitting
        # (the pre-gate behavior) rather than silently going dark.
        try:
            from end_of_line import registry  # local — avoid module-load cost

            emit, slugs, blockers = _scan_entries(registry.entries())
        except Exception:
            emit, slugs, blockers = True, [], []
        if not (emit or slugs or blockers):
            return 0
        additional_context = INSTRUCTION
        if slugs:
            additional_context += _per_plan_arming_block(slugs)
            additional_context += TASK_LIST_PROTOCOL_INSTRUCTION
        if blockers:
            additional_context += _blockers_block(
                blockers, MAX_CONTEXT_CHARS - len(additional_context)
            )
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
