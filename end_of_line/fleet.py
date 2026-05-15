"""Host-wide plan summary for bare `clu`. Pure projection — never mutates."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import registry, state as st


@dataclass(frozen=True)
class PlanSummary:
    plan_slug: str
    project_root: Path
    status: str
    current_phase: str | None
    open_blocker_count: int
    last_event_age_seconds: float | None
    has_worktree: bool = False


def summarize_plan(entry: registry.PlanEntry) -> PlanSummary | None:
    """Project (registry entry → state.json) into a one-line summary.

    Returns None when the state file is missing/unreadable so the caller
    can render a `missing` placeholder line; never raises on stale registry.
    """
    data = registry.load_entry_state(entry)
    if data is None:
        return None

    claim = data.get("current_claim")
    threshold = data["config"].get(
        "stalled_heartbeat_minutes", st.DEFAULT_STALLED_HEARTBEAT_MIN,
    )
    if claim and st.is_claim_stalled(claim, threshold):
        status = st.STATUS_STALLED
    else:
        status = data["status"]

    open_count = len(st.open_blockers(data))

    last_age: float | None = None
    if events := data.get("events"):
        try:
            last_ts = st.parse_iso(events[-1]["ts"])
            last_age = (st._now_utc() - last_ts).total_seconds()
        except (KeyError, ValueError):
            last_age = None

    return PlanSummary(
        plan_slug=entry.plan_slug,
        project_root=Path(entry.project_root),
        status=status,
        current_phase=claim["phase_id"] if claim else None,
        open_blocker_count=open_count,
        last_event_age_seconds=last_age,
        has_worktree=st.get_worktree(data) is not None,
    )


def humanize_age(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m"
    hours = minutes / 60
    if hours < 24:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def render(entries: Iterable[registry.PlanEntry]) -> str:
    """Return the formatted fleet view (header + one line per plan)."""
    entries = list(entries)
    if not entries:
        return "No plans registered. Run `clu init` or `clu register` to add one.\n"

    rows: list[tuple[str, str, str, str, str, str]] = []
    for entry in entries:
        summary = summarize_plan(entry)
        if summary is None:
            rows.append(
                (entry.plan_slug, st.STATUS_MISSING, "-", "-", "-", "-")
            )
            continue
        rows.append((
            summary.plan_slug,
            summary.status,
            summary.current_phase or "-",
            str(summary.open_blocker_count),
            humanize_age(summary.last_event_age_seconds),
            "yes" if summary.has_worktree else "-",
        ))

    header = ("PLAN", "STATUS", "PHASE", "BLOCKERS", "LAST", "WT")
    widths = [
        max(len(header[i]), max(len(r[i]) for r in rows))
        for i in range(len(header))
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*header)]
    for row in rows:
        lines.append(fmt.format(*row))
    return "\n".join(lines) + "\n"
