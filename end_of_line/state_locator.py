"""Walk the registry and match a reply text to one open blocker across all plans.

This module is the single point of truth for "which plan's blocker does this
reply target?" Three callers previously each maintained a private walk:
notify_imessage_inbound, cli.cmd_answer, and notify_discord_inbound. The
migrate phase wires them all to call here instead.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from end_of_line import state as st
from end_of_line.notify_base import REPLY_RE, OpenBlocker, route_reply
from end_of_line.registry import PlanEntry

log = logging.getLogger(__name__)

Variant = Literal["FOUND", "AMBIGUOUS", "NOT_FOUND", "STATE_UNREADABLE"]


@dataclass
class LocatorResult:
    variant: Variant
    state_path: Path | None = None
    blocker_id: str | None = None
    answer_index: int | None = None
    candidates: list[OpenBlocker] = field(default_factory=list)


def find_blocker_for_reply(
    entries: list[PlanEntry],
    reply_text: str,
) -> LocatorResult:
    """Walk registry entries, load each plan's state file tolerantly, and
    resolve `reply_text` to a single open blocker.

    Returns:
      FOUND — exactly one blocker matched; state_path/blocker_id/answer_index set.
      AMBIGUOUS — multiple eligible blockers and we can't pick one; candidates set.
      NOT_FOUND — no open blocker matches the reply.
      STATE_UNREADABLE — (reserved for callers; this function never returns it).
    """
    all_open: list[tuple[Path, OpenBlocker]] = []
    for entry in entries:
        result = _load_open_blockers(entry)
        if result is None:
            continue
        state_path, blockers = result
        for b in blockers:
            all_open.append((state_path, b))

    resolved = route_reply(reply_text, [b for _, b in all_open])
    if resolved is not None:
        target = resolved.target
        matched_path = next(sp for sp, b in all_open if b == target)
        return LocatorResult(
            variant="FOUND",
            state_path=matched_path,
            blocker_id=target.blocker_id,
            answer_index=int(resolved.answer),
        )

    # route_reply returned None — distinguish NOT_FOUND from AMBIGUOUS.
    m = REPLY_RE.match(reply_text)
    if not m:
        return LocatorResult(variant="NOT_FOUND")
    slug, digit = m.group(1), m.group(2)
    if slug:
        # Slug-qualified but no matching plan was found (or it was skipped).
        return LocatorResult(variant="NOT_FOUND")
    # Bare digit with no unique winner — check how many blockers are eligible.
    idx = int(digit)
    eligible = [b for _, b in all_open if idx < b.options_count]
    if not eligible:
        return LocatorResult(variant="NOT_FOUND")
    return LocatorResult(variant="AMBIGUOUS", candidates=eligible)


def _load_open_blockers(entry: PlanEntry) -> tuple[Path, list[OpenBlocker]] | None:
    """Resolve state_path from entry, load the state file, hydrate open blockers.

    Returns (state_path, blockers) on success; None on any recoverable failure
    (missing file, schema mismatch, corrupt JSON, missing project config).
    """
    from end_of_line.config import load_project_config  # local to avoid cycle

    try:
        cfg = load_project_config(Path(entry.project_root))
        state_path = cfg.state_path(entry.plan_slug)
    except (OSError, st.InvalidSlug, ValueError) as exc:
        log.warning("state_locator: skipping %s — %s", entry.plan_slug, exc)
        return None
    try:
        data = st.load(state_path)
    except (FileNotFoundError, st.SchemaVersionMismatch, json.JSONDecodeError, OSError) as exc:
        log.warning("state_locator: skipping %s — %s", entry.plan_slug, exc)
        return None
    return state_path, _hydrate_open_blockers(data, entry)


def _hydrate_open_blockers(data: dict, entry: PlanEntry) -> list[OpenBlocker]:
    """Convert raw state data into OpenBlocker dataclasses for all open blockers."""
    open_qs = st.open_blockers(data)
    if not open_qs:
        return []
    last_notified = ""
    for evt in reversed(data["events"]):
        if evt.get("type") == st.EVENT_PHASE_BLOCKED:
            last_notified = evt.get("ts", "")
            break
    return [
        OpenBlocker(
            project_root=Path(entry.project_root),
            plan_slug=entry.plan_slug,
            blocker_id=b["id"],
            options_count=len(b.get("options", [])),
            last_notified_at=last_notified,
        )
        for b in open_qs
    ]
