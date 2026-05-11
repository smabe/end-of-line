"""Atomic state-file management.

The state file is the single durable artifact across cold-context phases.
Every mutation is wrapped in a file lock; every write is tmp+fsync+rename.
The event log is append-only — projection from events can rebuild any
derived field if state ever gets corrupted.
"""
from __future__ import annotations

import datetime as _dt
import fcntl
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1

_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"
_TOKEN_LEN = 8

# Defaults — also embedded in empty_state(); changing here updates both.
DEFAULT_LEASE_TTL_MIN = 30
DEFAULT_SLA_HOURS = 24
DEFAULT_MAX_ATTEMPTS = 3

# Plan status (`data["status"]`)
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_HALTED = "halted"
STATUS_HALTED_REPLAN = "halted_for_replan"
STATUS_DONE = "done"
TERMINAL_STATUSES = frozenset(
    {STATUS_PAUSED, STATUS_HALTED, STATUS_HALTED_REPLAN, STATUS_DONE}
)

# Event types — string-typo'ing one of these silently breaks projection,
# so always reference the constant.
EVENT_PHASE_STARTED = "phase_started"
EVENT_PHASE_COMPLETED = "phase_completed"
EVENT_PHASE_BLOCKED = "phase_blocked"
EVENT_LEASE_EXPIRED = "lease_expired"
EVENT_BLOCKER_ANSWERED = "blocker_answered"
EVENT_BLOCKER_CONSUMED = "blocker_consumed"
EVENT_BLOCKER_SLA_EXCEEDED = "blocker_sla_exceeded"
EVENT_PHASE_MAX_ATTEMPTS = "phase_max_attempts"
EVENT_TASK_SPAWNED = "task_spawned"
EVENT_PLAN_COMPLETED = "plan_completed"

# Blocker types
BLOCKER_INPUT = "blocked_input"
BLOCKER_REPLAN = "blocked_replan"


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def utcnow() -> str:
    return _now_utc().strftime(_ISO_FMT)


def parse_iso(ts: str) -> _dt.datetime:
    # Python 3.11+ fromisoformat handles trailing 'Z' natively.
    return _dt.datetime.fromisoformat(ts)


def empty_state(plan_slug: str, plan_dir: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_slug": plan_slug,
        "plan_dir": plan_dir,
        "status": STATUS_RUNNING,
        "current_claim": None,
        "blockers": [],
        "spawned_tasks": [],
        "config": {
            "lease_ttl_minutes": DEFAULT_LEASE_TTL_MIN,
            "blocked_question_sla_hours": DEFAULT_SLA_HOURS,
            "max_attempts_per_phase": DEFAULT_MAX_ATTEMPTS,
        },
        "events": [],
        "created_at": utcnow(),
    }


@contextmanager
def locked(state_path: Path) -> Iterator[None]:
    """Serialize read-modify-write across processes via a sibling lock file."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_name(state_path.name + ".lock")
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


@contextmanager
def mutate(state_path: Path) -> Iterator[dict]:
    """Take the lock, load, yield data for mutation, write atomically on exit.

    Use this for every read-modify-write. Plain `locked()` is for the rare
    case where multiple files need to be coordinated under one lock.
    """
    with locked(state_path):
        data = load(state_path)
        yield data
        save_atomic(state_path, data)


def load(state_path: Path) -> dict:
    return json.loads(state_path.read_text())


def save_atomic(state_path: Path, data: dict) -> None:
    """Write tmp + fsync + rename. Caller must hold the lock."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=state_path.name + ".",
        suffix=".tmp",
        dir=str(state_path.parent),
    )
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, state_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def append_event(data: dict, event_type: str, **fields: Any) -> None:
    data["events"].append({"ts": utcnow(), "type": event_type, **fields})


def release_if_expired(data: dict) -> bool:
    """If current_claim's lease is past, clear it + emit lease_expired.

    Returns True if released. Shared between claim_phase (reclaim path) and
    supervisor (stale-lease path) so behavior can't drift.
    """
    claim = data.get("current_claim")
    if claim is None:
        return False
    try:
        expires = parse_iso(claim["lease_expires"])
    except (KeyError, ValueError):
        return False
    if expires > _now_utc():
        return False
    append_event(
        data, EVENT_LEASE_EXPIRED,
        phase=claim["phase_id"],
        claimed_by=claim.get("claimed_by"),
    )
    data["current_claim"] = None
    return True


def claim_phase(
    data: dict,
    phase_id: str,
    lease_minutes: int,
    claimed_by: str | None = None,
) -> str:
    """Claim a phase. Returns the claim token. Raises if a live claim exists."""
    prior_phase = (data.get("current_claim") or {}).get("phase_id")
    release_if_expired(data)
    if data.get("current_claim") is not None:
        existing = data["current_claim"]
        raise RuntimeError(
            f"phase {existing['phase_id']} already claimed by "
            f"{existing.get('claimed_by')} until {existing['lease_expires']}"
        )

    token = claimed_by or f"session-{uuid.uuid4().hex[:_TOKEN_LEN]}"
    expires = _now_utc() + _dt.timedelta(minutes=lease_minutes)
    attempts = sum(
        1 for evt in data["events"]
        if evt.get("type") == EVENT_PHASE_STARTED and evt.get("phase") == phase_id
    ) + 1
    _ = prior_phase  # kept for future "reclaim-after-expiry" hooks
    data["current_claim"] = {
        "phase_id": phase_id,
        "claimed_by": token,
        "lease_expires": expires.strftime(_ISO_FMT),
        "started_at": utcnow(),
        "attempts": attempts,
    }
    append_event(data, EVENT_PHASE_STARTED, phase=phase_id, claimed_by=token)
    return token


def release_claim(
    data: dict,
    expected_token: str | None = None,
    expected_phase: str | None = None,
) -> None:
    """Clear current_claim. Pass token OR phase to guard against stale releases."""
    claim = data.get("current_claim")
    if claim is None:
        return
    if expected_token is not None and claim.get("claimed_by") != expected_token:
        raise RuntimeError("claim token mismatch — refusing to release")
    if expected_phase is not None and claim.get("phase_id") != expected_phase:
        return  # different phase claimed; leave alone
    data["current_claim"] = None


def add_blocker(
    data: dict,
    phase_id: str,
    question: str,
    options: list[str],
    context: str = "",
    blocker_type: str = BLOCKER_INPUT,
) -> str:
    blocker_id = f"q-{len(data['blockers']) + 1}"
    data["blockers"].append({
        "id": blocker_id,
        "phase_id": phase_id,
        "type": blocker_type,
        "question": question,
        "options": list(options),
        "context": context,
        "asked_at": utcnow(),
        "answer": None,
        "answered_at": None,
    })
    append_event(data, EVENT_PHASE_BLOCKED, phase=phase_id, blocker_id=blocker_id)
    return blocker_id


def answer_blocker(data: dict, blocker_id: str, answer: str) -> None:
    for b in data["blockers"]:
        if b["id"] == blocker_id and b["answer"] is None:
            b["answer"] = answer
            b["answered_at"] = utcnow()
            append_event(
                data, EVENT_BLOCKER_ANSWERED,
                blocker_id=blocker_id, answer=answer,
            )
            return
    raise KeyError(f"no unanswered blocker {blocker_id}")


def resolve_blocker_answer(data: dict, blocker_id: str, raw_answer: str) -> str:
    """Translate a numeric option-index to the option text, else return as-is."""
    if not raw_answer.isdigit():
        return raw_answer
    idx = int(raw_answer)
    for b in data["blockers"]:
        if b["id"] == blocker_id and idx < len(b["options"]):
            return b["options"][idx]
    return raw_answer


def completed_phase_ids(data: dict) -> set[str]:
    return {
        evt["phase"]
        for evt in data["events"]
        if evt.get("type") == EVENT_PHASE_COMPLETED and "phase" in evt
    }


def phase_has_open_blocker(data: dict, phase_id: str) -> bool:
    return any(
        b["phase_id"] == phase_id and b["answer"] is None
        for b in data["blockers"]
    )


def attempts_for_phase(data: dict, phase_id: str) -> int:
    """Count phase_started events for this phase (durable across claim clears)."""
    return sum(
        1 for evt in data["events"]
        if evt.get("type") == EVENT_PHASE_STARTED and evt.get("phase") == phase_id
    )
