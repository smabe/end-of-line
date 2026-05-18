"""Pure blocker state machine.

All functions take `data: dict` (the plan state data dict) and return
derived values — events, transitions, rendered bodies. No I/O, no
st.mutate, no notify.send calls.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .state import (
    EVENT_BLOCKER_CONSUMED,
    STATUS_RUNNING,
    parse_iso,
)

KIND_STUCK_BLOCKER = "stuck_blocker"
STUCK_BLOCKER_THRESHOLD_MINUTES = 30
_STUCK_THRESHOLD_SECONDS = STUCK_BLOCKER_THRESHOLD_MINUTES * 60

# Render limits — iMessage has a practical body cap; past it we fall back to
# a compact form that still carries the terminal answer command.
BLOCKER_BODY_SOFT_LIMIT = 800
_QUESTION_TRUNCATE = 200
_OPTION_TRUNCATE = 80


def _truncate_at_word(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    cut = s.rfind(" ", 0, max_len - 1)
    if cut <= 0:
        cut = max_len - 1
    return s[:cut].rstrip() + "…"


def process_answered_blockers(
    data: dict[str, Any],
) -> tuple[list[tuple[str, str]], str | None]:
    """Return (events_to_append, target_status) for answered, not-yet-consumed blockers.

    Caller appends the events to data["events"] and flips data["status"] to
    target_status when non-None (supervisor rule 4).
    """
    events: list[tuple[str, str]] = []
    for blocker in data.get("blockers") or []:
        if blocker.get("answer") is None:
            continue
        if blocker.get("consumed"):
            continue
        events.append((EVENT_BLOCKER_CONSUMED, blocker["id"]))
    target_status = STATUS_RUNNING if events else None
    return events, target_status


def stuck_blocker_repings(
    data: dict[str, Any],
    now: datetime,
) -> list[tuple[str, str, str]]:
    """Return (blocker_id, kind, body) for blockers needing a re-ping.

    A blocker qualifies when it is open (no answer, not consumed) AND
    either 30+ minutes old with no prior re-ping, or its last re-ping was
    30+ minutes ago.

    Caller stamps last_repinged_at on each returned blocker_id and appends
    the (kind, body) pair to side_notifies — this function is pure and does
    neither.
    """
    results: list[tuple[str, str, str]] = []
    plan_slug = data.get("plan_slug", "")
    for b in data.get("blockers") or []:
        if b.get("consumed") or b.get("answer") is not None:
            continue
        try:
            created = parse_iso(b["asked_at"])
        except (KeyError, ValueError):
            continue
        age_seconds = (now - created).total_seconds()
        if age_seconds < _STUCK_THRESHOLD_SECONDS:
            continue
        if b.get("last_repinged_at"):
            try:
                last_pinged = parse_iso(b["last_repinged_at"])
            except ValueError:
                last_pinged = None
            if last_pinged is not None:
                if (now - last_pinged).total_seconds() < _STUCK_THRESHOLD_SECONDS:
                    continue
        age_min = int(age_seconds // 60)
        body = render_stuck_blocker(
            plan_slug, b["id"], b["phase_id"],
            b["question"], b["options"], age_min,
        )
        results.append((b["id"], KIND_STUCK_BLOCKER, body))
    return results


def render_blocker(
    plan_slug: str, blocker_id: str, phase: str, question: str, options: list[str],
) -> str:
    q = _truncate_at_word(question, _QUESTION_TRUNCATE)
    answer_cmd = f"clu answer --plan {plan_slug} {blocker_id} <choice>"
    if options:
        opts_block = "\n".join(
            f"[{i}] {_truncate_at_word(o, _OPTION_TRUNCATE)}"
            for i, o in enumerate(options)
        )
        middle = f"{q}\n{opts_block}\n\n"
    else:
        middle = f"{q}\n\n"
    # Reply hint grammar must stay in sync with notify_inbound.REPLY_RE.
    body = (
        f"❓ {plan_slug}/{blocker_id} [{phase}]\n{middle}"
        f"Reply: `{plan_slug} <number>` or just the number if this is the "
        f"only open question.\n"
        f"Terminal: {answer_cmd}"
    )
    if len(body) <= BLOCKER_BODY_SOFT_LIMIT:
        return body
    n = len(options)
    noun = "option" if n == 1 else "options"
    return (
        f"❓ {plan_slug}/{blocker_id} [{phase}]\n{q}\n\n"
        f"{n} {noun}. Run `{answer_cmd}` to answer."
    )


def render_stalled(plan_slug: str, phase: str, age_seconds: float) -> str:
    minutes = int(age_seconds // 60)
    return f"⚠️  {plan_slug}/{phase} stalled — no heartbeat for {minutes} min."


def render_halted(plan_slug: str, phase: str, attempts: int) -> str:
    return (
        f"🛑 {plan_slug}/{phase} halted — {attempts} attempts. "
        f"`clu retry --plan {plan_slug}` to resume."
    )


def render_stuck_blocker(
    plan_slug: str, blocker_id: str, phase: str,
    question: str, options: list[str], age_min: int,
) -> str:
    opts = "\n".join(f"[{i}] {o}" for i, o in enumerate(options))
    return (
        f"⏰ {plan_slug}/{blocker_id} still open ({age_min}min) [{phase}]\n"
        f"{question}\n{opts}\n\n"
        f"Reply: `{plan_slug} <number>`."
    )
