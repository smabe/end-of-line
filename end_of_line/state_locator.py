"""Walk the registry and match a reply text to one open blocker across all plans.

This module is the single point of truth for "which plan's blocker does this
reply target?" Three callers previously each maintained a private walk:
notify_imessage_inbound, cli.cmd_answer, and notify_discord_inbound. The
migrate phase wires them all to call here instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from end_of_line import db, plan_store
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
    project_root: Path | None = None
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
    all_open: list[tuple[Path, Path, OpenBlocker]] = []  # (project_root, state_path, blocker)
    for entry in entries:
        result = _load_open_blockers(entry)
        if result is None:
            continue
        state_path, blockers = result
        project_root = Path(entry.project_root)
        for b in blockers:
            all_open.append((project_root, state_path, b))

    resolved = route_reply(reply_text, [b for _, _, b in all_open])
    if resolved is not None:
        target = resolved.target
        matched = next((pr, sp) for pr, sp, b in all_open if b == target)
        return LocatorResult(
            variant="FOUND",
            state_path=matched[1],
            blocker_id=target.blocker_id,
            answer_index=int(resolved.answer),
            project_root=matched[0],
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
    eligible = [b for _, _, b in all_open if idx < b.options_count]
    if not eligible:
        return LocatorResult(variant="NOT_FOUND")
    return LocatorResult(variant="AMBIGUOUS", candidates=eligible)


def _load_open_blockers(entry: PlanEntry) -> tuple[Path, list[OpenBlocker]] | None:
    """Resolve state_path from entry, load the state file, hydrate open blockers.

    Returns (state_path, blockers) on success; None on any recoverable failure
    (no such plan, schema mismatch, unreadable store, missing project config).
    """
    from end_of_line.config import load_project_config  # local to avoid cycle

    try:
        cfg = load_project_config(Path(entry.project_root))
        state_path = cfg.state_path(entry.plan_slug)
    except (OSError, st.InvalidSlug, ValueError) as exc:
        log.warning("state_locator: skipping %s — %s", entry.plan_slug, exc)
        return None
    try:
        data = plan_store.snapshot(*plan_store.key_for_state_path(state_path))
    except FileNotFoundError:
        # A plan that simply is not there is routine — a registry entry the
        # operator has not pruned. DEBUG, not WARNING; anything else is loud
        # enough to matter and is warned about below.
        log.debug("state_locator: skipping %s — no such plan", entry.plan_slug)
        return None
    except (*db.DEGRADABLE_ERRORS, st.SchemaVersionMismatch, ValueError) as exc:
        # `OSError`/`ValueError`/`SchemaVersionMismatch` were the whole story
        # for a FILE. A database also fails with `sqlite3.Error` when it is
        # broken, `db.DbBusy` (a RuntimeError, which no OSError clause catches)
        # when another plan's tick is holding the project's write lock, and
        # `db.SchemaTooNew` when it came from a newer clu — and this walk runs
        # from the inbound poller, where an escaping exception means a reply
        # the operator typed matches nothing and nobody finds out why.
        log.warning("state_locator: skipping %s — %s", entry.plan_slug, exc)
        return None
    return state_path, _hydrate_open_blockers(data, entry)


def _hydrate_open_blockers(data: dict, entry: PlanEntry) -> list[OpenBlocker]:
    """Convert raw state data into OpenBlocker dataclasses for all open blockers."""
    open_qs = st.open_blockers(data)
    if not open_qs:
        return []
    # Stamp each blocker from ITS OWN phase_blocked event, not the plan's most
    # recent one. Two open siblings share a plan but not a question; giving both
    # the plan-wide latest ts makes them compare EQUAL in _pick_by_last_pinged,
    # which refuses a tie — so a bare digit for either deadlocks forever. Events
    # carry blocker_id (plan_store.op_add_blocker), so this is a lookup, not a
    # schema change. Walk newest-first; the first hit per id is its latest ping,
    # and the first phase_blocked seen is the plan-wide latest — the fallback for
    # any (legacy) event that predates the blocker_id field, so an id-less event
    # keeps the OLD plan-wide behavior instead of collapsing to "" and re-tying.
    last_by_blocker: dict[str, str] = {}
    plan_latest = ""
    for evt in reversed(data["events"]):
        if evt.get("type") != st.EVENT_PHASE_BLOCKED:
            continue
        if not plan_latest:
            plan_latest = evt.get("ts", "")
        bid = evt.get("blocker_id")
        if bid is not None and bid not in last_by_blocker:
            last_by_blocker[bid] = evt.get("ts", "")
    return [
        OpenBlocker(
            project_root=Path(entry.project_root),
            plan_slug=entry.plan_slug,
            blocker_id=b["id"],
            options_count=len(b.get("options", [])),
            last_notified_at=last_by_blocker.get(b["id"], plan_latest),
        )
        for b in open_qs
    ]
