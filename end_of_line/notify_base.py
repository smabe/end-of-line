"""Shared types and Protocols for notify backends.

Protocols are @runtime_checkable so backends can be verified with isinstance().
OpenBlocker, Reply, and route_reply live here so Discord inbound can reuse
them without importing the iMessage-specific modules.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, NamedTuple, Protocol, runtime_checkable

from . import state as st

REPLY_RE = re.compile(rf"^\s*(?:({st.SLUG_PATTERN})\s+)?([0-9])\s*$")


@dataclass(frozen=True)
class OpenBlocker:
    project_root: Path
    plan_slug: str
    blocker_id: str  # q-N
    options_count: int
    last_notified_at: str  # ISO ts of most recent EVENT_PHASE_BLOCKED, "" if none


class Reply(NamedTuple):
    target: OpenBlocker
    answer: str


@runtime_checkable
class Notifier(Protocol):
    kind_name: str

    def send(
        self,
        kind: str,
        body: str,
        *,
        plan_slug: str,
        blocker_id: str | None,
    ) -> str | None: ...


@runtime_checkable
class InboundPoller(Protocol):
    def poll(self) -> list[Reply]: ...


def route_reply(
    text: str, open_blockers: list[OpenBlocker],
) -> Reply | None:
    """Return Reply(target, option-index-str) if `text` resolves to a single blocker.

    Bare-digit replies with multiple open blockers route to the
    most-recently-pinged plan whose blocker has that digit in range
    (issue #3). Slug-prefixed replies always win. Returns None when the
    text doesn't match the grammar, the slug is unknown, no plan has a
    valid index for the digit, or the top two candidates tie on ping ts.
    """
    m = REPLY_RE.match(text)
    if not m:
        return None
    slug, digit = m.group(1), m.group(2)
    if slug:
        for ob in open_blockers:
            if ob.plan_slug == slug:
                return Reply(ob, digit)
        return None
    if not open_blockers:
        return None
    if len(open_blockers) == 1:
        return Reply(open_blockers[0], digit)
    picked = _pick_by_last_pinged(open_blockers, digit)
    return Reply(picked, digit) if picked else None


@dataclass
class BlockerDetail:
    project_root: Path
    plan_slug: str
    phase_id: str
    blocker_id: str
    question: str
    options: tuple[str, ...]


def open_blockers_with_details(
    entries: Iterable,
    project_root: Path | str,
) -> list[BlockerDetail]:
    """All open blockers for a given project root, with question + options.

    Returns every unanswered blocker (not just the first per plan) for plans
    whose project_root resolves to the given path. Tolerant of missing/stale
    registry entries.
    """
    from . import registry  # local import to avoid potential circular at module level

    target = str(Path(project_root).resolve())
    out: list[BlockerDetail] = []
    for row in entries:
        if str(Path(row.project_root).resolve()) != target:
            continue
        data = registry.load_entry_state(row)
        if data is None:
            continue
        for b in st.open_blockers(data):
            out.append(BlockerDetail(
                project_root=Path(row.project_root),
                plan_slug=row.plan_slug,
                phase_id=b["phase_id"],
                blocker_id=b["id"],
                question=b.get("question", ""),
                options=tuple(b.get("options", [])),
            ))
    return out


def _pick_by_last_pinged(
    open_blockers: list[OpenBlocker], digit: str,
) -> OpenBlocker | None:
    """Bare-digit ambiguity → most-recently-pinged plan with the digit in range.

    Filters to plans where `digit` is a valid option index, then picks the
    unique max-`last_notified_at`. Returns None if no plan is eligible or
    the top two tie on timestamp (refuse rather than silently misroute).
    """
    idx = int(digit)
    eligible = [b for b in open_blockers if idx < b.options_count]
    if not eligible:
        return None
    eligible.sort(key=lambda b: b.last_notified_at, reverse=True)
    if len(eligible) >= 2 and eligible[0].last_notified_at == eligible[1].last_notified_at:
        return None
    return eligible[0]
