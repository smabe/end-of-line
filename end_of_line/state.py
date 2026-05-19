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
import re
import signal
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from . import coolant

# Fragment (no anchors) so other modules can compose it into larger patterns
# without redefining the character class — drift here is a security invariant
# (path traversal + unmatched inbound replies).
SLUG_PATTERN = r"[a-z0-9][a-z0-9_-]{0,63}"
_SLUG_RE = re.compile(rf"^{SLUG_PATTERN}$")


class InvalidSlug(ValueError):
    """Raised when a plan slug or phase id fails validation (path-traversal guard)."""


def validate_slug(slug: str, *, kind: str = "slug") -> None:
    """Reject anything that isn't a safe path component."""
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise InvalidSlug(
            f"invalid {kind} {slug!r}: must match {_SLUG_RE.pattern}"
        )


def is_branch_merged_into(
    project_root: Path,
    branch: str,
    base_ref: str = "origin/main",
) -> bool:
    """Return True iff `branch`'s HEAD is an ancestor of `base_ref`.

    Wraps `git merge-base --is-ancestor`. Returns False (not exception)
    when either ref doesn't exist, the git invocation times out, or any
    other subprocess error occurs. No `git fetch` is performed.
    """
    try:
        result = subprocess.run(
            [
                "git", "-C", str(project_root), "merge-base",
                "--is-ancestor", branch, base_ref,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


SCHEMA_VERSION = 1


class SchemaVersionMismatch(Exception):
    """Raised when state.json was written by a different clu schema version."""


_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"
_TOKEN_LEN = 16  # 64 bits, enough for token-auth use (red team L1).

# Defaults — also embedded in empty_state(); changing here updates both.
DEFAULT_LEASE_TTL_MIN = 60
DEFAULT_SLA_HOURS = 24
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_SPAWNS_PER_PHASE = 10
DEFAULT_MAX_QUEUE_ADDS_PER_PHASE = 3
DEFAULT_STALLED_HEARTBEAT_MIN = 10

# Plan status (`data["status"]`)
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_HALTED = "halted"
STATUS_HALTED_REPLAN = "halted_for_replan"
STATUS_DONE = "done"
TERMINAL_STATUSES = frozenset(
    {STATUS_PAUSED, STATUS_HALTED, STATUS_HALTED_REPLAN, STATUS_DONE}
)
# `clu worktree gc` eligibility — terminal minus paused. Paused plans may
# resume and need their worktree intact; done/halted plans won't (operator
# uses `clu retry` only on halted, which the gc action-time re-check
# blocks).
GC_ELIGIBLE_STATUSES = frozenset(
    {STATUS_DONE, STATUS_HALTED, STATUS_HALTED_REPLAN}
)
# Display-only labels — fleet view derives these instead of storing them.
STATUS_STALLED = "stalled"
STATUS_MISSING = "missing"

# Event types — string-typo'ing one of these silently breaks projection,
# so always reference the constant.
EVENT_PHASE_STARTED = "phase_started"
EVENT_PHASE_COMPLETED = "phase_completed"
EVENT_PHASE_BLOCKED = "phase_blocked"
EVENT_LEASE_EXPIRED = "lease_expired"
EVENT_CLAIM_FORCE_RELEASED = "claim_force_released"
EVENT_BLOCKER_ANSWERED = "blocker_answered"
EVENT_BLOCKER_CONSUMED = "blocker_consumed"
EVENT_BLOCKER_SLA_EXCEEDED = "blocker_sla_exceeded"
EVENT_PHASE_MAX_ATTEMPTS = "phase_max_attempts"
EVENT_TASK_SPAWNED = "task_spawned"
EVENT_TASK_COMPLETED = "task_completed"
EVENT_PLAN_COMPLETED = "plan_completed"
EVENT_DISPATCH_FAILED = "dispatch_failed"
EVENT_SYSTEMIC_FAILURE = "systemic_failure"
EVENT_PHASE_STALLED = "phase_stalled"
EVENT_PAUSED = "paused"
EVENT_RESUMED = "resumed"
EVENT_RETRY_REQUESTED = "retry_requested"
# Provenance event written as the FIRST event of a state.json created by
# the supervisor's per-project queue advancement step. Fields: slug,
# added_at, added_by, position. Worker dispatched after this event lands
# sees it in its initial state read.
EVENT_QUEUE_POPPED = "queue_popped"
# Written to the *source plan's* events array when a worker enqueues a
# follow-up plan (queue_appended) or is rejected by a gate (queue_rejected).
# Logging against the source plan co-locates the audit trail with the
# worker's other actions.
EVENT_QUEUE_APPENDED = "queue_appended"
EVENT_QUEUE_REJECTED = "queue_rejected"
# Gap-fill notifications surfaced both via iMessage (cmd_tick) and inbox
# (next Claude turn). REPINGED fires every 30 minutes per blocker;
# CLAIM_NOTIFIED fires once per (claim, transition) pair.
EVENT_STUCK_BLOCKER_REPINGED = "stuck_blocker_repinged"
EVENT_STALLED_CLAIM_NOTIFIED = "stalled_claim_notified"
# Worktree lifecycle. MISSING fires once per dispatch when state.worktree
# points at a path that's been deleted or detached (operator removed the
# directory or ran `git worktree prune`); accompanied by a status=PAUSED
# transition. CONFLICT_WARNING fires once per (project, slug-pair) when
# tick-all detects two active plans in the same project without isolated
# worktrees — suppression flag lives on each plan's `in_conflict_with` field.
EVENT_WORKTREE_MISSING = "worktree_missing"
EVENT_WORKTREE_CONFLICT_WARNING = "worktree_conflict_warning"
# Operator ran `clu worktree attach` to retrofit a worktree record onto an
# already-init'd plan (e.g. resume flow where worktrees were built by hand).
# Distinguishes operator-attached from init-created in the audit trail.
EVENT_WORKTREE_ATTACHED = "worktree_attached"
# Worktree + branch cleanup at plan end (cmd_complete on last phase,
# cmd_archive, or cmd_worktree_gc when commits are upstream-reachable).
# Fields: path, branch, worktree_removed, branch_removed, worktree_error,
# branch_error, trigger ("complete" / "archive" / "gc"). state.worktree is
# cleared to None alongside this event.
EVENT_WORKTREE_CLEANED = "worktree_cleaned"
# Cleanup skipped because the branch has commits not reachable from
# origin/<default>. Fields: path, branch, reason, ahead_commits (list of
# short SHAs), trigger. state.worktree is left in place so the operator
# can push or force-delete manually.
EVENT_WORKTREE_RETAINED_AHEAD = "worktree_retained_ahead"
# Operator bumped a live claim's lease_expires without state-file hand-editing.
# Fields: phase, extended_by_minutes, new_expires, operator (True).
EVENT_LEASE_EXTENDED = "lease_extended"
# Operator released a claim with --reset-attempts; zeroes the phase's attempt
# budget so the next dispatch doesn't count operator-driven aborts against it.
# Fields: phase, operator (True).
EVENT_ATTEMPTS_RESET = "attempts_reset"
# Operator ran `clu force-complete` to mark a phase done when the worker died
# after writing code but before calling `clu complete`. Paired with a
# subsequent EVENT_PHASE_COMPLETED so the supervisor's plan_done detection
# fires normally. Fields: phase, commits, reason, operator (True).
EVENT_OPERATOR_FORCE_COMPLETE = "operator_force_complete"
# Worker (or operator) ran `clu verify`; the configured verify command exited 0
# and the result was stamped into attestations.verify on current_claim.
# Fields: phase, commit_sha.
EVENT_VERIFY_STAMPED = "verify_stamped"
# Worker ran `clu attest --simplify`; current HEAD stamped into
# attestations.simplify on current_claim. Fields: phase, commit_sha.
EVENT_SIMPLIFY_STAMPED = "simplify_stamped"
# Operator passed --skip-verify / --skip-simplify on `clu complete` to bypass a
# quality gate. Fields: phase, operator (True). One event per skip per complete.
EVENT_OPERATOR_SKIP_VERIFY = "operator_skip_verify"
EVENT_OPERATOR_SKIP_SIMPLIFY = "operator_skip_simplify"
# Supervisor reaped an orphaned worker process after lease expiry.
# Fields: phase, pid, signaled ("SIGTERM" | "SIGTERM+SIGKILL"), cmdline_mismatch (bool).
EVENT_PHASE_ORPHAN_REAPED = "phase_orphan_reaped"

# Per-project verify opt-out (quality.verify_required: false). Fires on
# every cmd_complete under the opt-out so the audit trail records the
# bypass — distinct from EVENT_OPERATOR_SKIP_VERIFY which records the
# per-invocation --skip-verify flag. (#61)
EVENT_VERIFY_POLICY_SKIPPED = "verify_policy_skipped"

# Attestation kind constants — keys inside current_claim.attestations.
ATTESTATION_VERIFY = "verify"
ATTESTATION_SIMPLIFY = "simplify"

# Blocker types
BLOCKER_INPUT = "blocked_input"
BLOCKER_REPLAN = "blocked_replan"

# Signal strings stored in ReapResult.signaled — constants prevent silent typos.
SIGNAL_TERM = "SIGTERM"
SIGNAL_TERM_THEN_KILL = "SIGTERM+SIGKILL"


@dataclass
class ReapResult:
    signaled: str | None
    escalated_kill: bool
    cmdline_mismatch: bool


def reap_orphan_pid(pid: int, cmdline_match: str | None = None) -> ReapResult:
    """Send SIGTERM to an orphaned worker PID, escalating to SIGKILL after 5s.

    PID-reuse guard: when cmdline_match is given, the process cmdline is
    checked via `ps` before signaling. On mismatch returns without killing.
    Do NOT use os.waitpid — we never forked this PID; WNOHANG → ECHILD.
    """
    if cmdline_match is not None:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return ReapResult(None, False, False)
        if cmdline_match not in result.stdout:
            return ReapResult(None, False, True)

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return ReapResult(None, False, False)

    for _ in range(20):
        time.sleep(0.25)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return ReapResult(SIGNAL_TERM, False, False)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return ReapResult(SIGNAL_TERM_THEN_KILL, True, False)


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def utcnow() -> str:
    return _now_utc().strftime(_ISO_FMT)


def utcnow_compact() -> str:
    """Filename-safe UTC timestamp (`20260512T143415Z`).

    Used in backup / log paths where `:` would be illegal on some filesystems.
    """
    return _now_utc().strftime("%Y%m%dT%H%M%SZ")


def parse_iso(ts: str) -> _dt.datetime:
    # Python 3.9's fromisoformat doesn't accept the trailing 'Z'; normalize it.
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
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
            "max_spawns_per_phase": DEFAULT_MAX_SPAWNS_PER_PHASE,
            "max_queue_adds_per_phase": DEFAULT_MAX_QUEUE_ADDS_PER_PHASE,
            "stalled_heartbeat_minutes": DEFAULT_STALLED_HEARTBEAT_MIN,
        },
        "phases": [],
        "events": [],
        "created_at": utcnow(),
        "batch_id": None,
    }


@contextmanager
def locked(state_path: Path) -> Iterator[None]:
    """Serialize read-modify-write across processes via a sibling lock file.

    O_NOFOLLOW refuses to open if the lockfile path is a symlink — defeats
    a pre-seeded symlink attack that would otherwise truncate the target.
    """
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_name(state_path.name + ".lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def locked_json(
    path: Path,
    *,
    expected_version: int,
    empty: Callable[[], dict] | None = None,
) -> Iterator[dict]:
    """Generic lock + load + yield-for-mutation + atomic write.

    Shared primitive for every clu JSON file (state, registry, queue). The
    `empty` factory makes the missing-file branch a caller choice: state
    files always pre-exist (claim path → save_atomic happens first), so
    state.mutate passes None and lets load() raise FileNotFoundError;
    registry and queue tolerate missing-on-first-write and pass a real
    factory.
    """
    with locked(path):
        if not path.exists() and empty is not None:
            data = empty()
        else:
            data = load(path, expected_version=expected_version)
        yield data
        save_atomic(path, data)


@contextmanager
def mutate(state_path: Path) -> Iterator[dict]:
    """Take the lock, load, yield data for mutation, write atomically on exit.

    Use this for every read-modify-write. Plain `locked()` is for the rare
    case where multiple files need to be coordinated under one lock.
    """
    with locked_json(state_path, expected_version=SCHEMA_VERSION) as data:
        yield data


def load(state_path: Path, *, expected_version: int = SCHEMA_VERSION) -> dict:
    """Read + schema-check a clu JSON file. `expected_version` lets sibling
    schemas (e.g. registry.json) reuse the same loader."""
    data = json.loads(state_path.read_text())
    actual = data.get("schema_version")
    if actual != expected_version:
        raise SchemaVersionMismatch(
            f"{state_path} has schema_version={actual!r}, "
            f"clu expects {expected_version}"
        )
    return data


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


def lease_ttl_for_phase(data: dict, phase_id: str) -> int:
    """Return the effective lease TTL (minutes) for a phase.

    Resolution order: per-phase override → global config → DEFAULT_LEASE_TTL_MIN.
    """
    for ph in data.get("phases", []):
        if ph.get("id") == phase_id and "lease_ttl_minutes" in ph:
            return int(ph["lease_ttl_minutes"])
    return int(data.get("config", {}).get("lease_ttl_minutes", DEFAULT_LEASE_TTL_MIN))


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
    started = utcnow()
    data["current_claim"] = {
        "phase_id": phase_id,
        "claimed_by": token,
        "lease_expires": expires.strftime(_ISO_FMT),
        "started_at": started,
        "last_heartbeat_at": started,
        "attempts": attempts,
    }
    append_event(data, EVENT_PHASE_STARTED, phase=phase_id, claimed_by=token)
    return token


class ClaimMismatch(RuntimeError):
    """Worker callback didn't match the live claim — stale or forged."""


def assert_claim_match(data: dict, expected_token: str, expected_phase: str) -> None:
    """Raise ClaimMismatch unless current_claim matches token AND phase."""
    claim = data.get("current_claim")
    if claim is None:
        raise ClaimMismatch("no active claim")
    if claim.get("claimed_by") != expected_token:
        raise ClaimMismatch(
            f"token mismatch: claim is {claim.get('claimed_by')!r}, "
            f"got {expected_token!r}"
        )
    if claim.get("phase_id") != expected_phase:
        raise ClaimMismatch(
            f"phase mismatch: claim is {claim.get('phase_id')!r}, "
            f"got {expected_phase!r}"
        )


def record_heartbeat(data: dict, expected_token: str, expected_phase: str) -> str:
    """Stamp last_heartbeat_at on the live claim. Returns the new timestamp.

    No event is appended — heartbeats fire every ~2 min and would flood the
    event log. The supervisor derives stalled state from the single field.
    """
    assert_claim_match(data, expected_token, expected_phase)
    ts = utcnow()
    data["current_claim"]["last_heartbeat_at"] = ts
    return ts


def heartbeat_age_seconds(claim: dict, now: _dt.datetime | None = None) -> float | None:
    if not claim:
        return None
    last = claim.get("last_heartbeat_at") or claim.get("started_at")
    if not last:
        return None
    try:
        last_dt = parse_iso(last)
    except ValueError:
        return None
    return ((now or _now_utc()) - last_dt).total_seconds()


def is_claim_stalled(
    claim: dict, threshold_minutes: int, now: _dt.datetime | None = None,
) -> bool:
    age = heartbeat_age_seconds(claim, now)
    if age is None:
        return False
    return age >= threshold_minutes * 60


def release_claim(
    data: dict,
    expected_token: str | None = None,
    expected_phase: str | None = None,
) -> None:
    """Clear current_claim. If both expected_* are given, mismatch raises ClaimMismatch.

    Passing neither clears unconditionally — only the supervisor (which holds
    the lock and just inspected the claim) should do this. Passing only one
    is a programming error: callers either prove they own the claim with both
    pieces or they don't validate at all.
    """
    if expected_token is None and expected_phase is None:
        data["current_claim"] = None
        return
    if expected_token is None or expected_phase is None:
        raise ValueError(
            "release_claim: expected_token and expected_phase must be passed together"
        )
    assert_claim_match(data, expected_token, expected_phase)
    data["current_claim"] = None


def release_claim_and_emit(
    data: dict,
    expected_token: str | None = None,
    expected_phase: str | None = None,
) -> None:
    """Release current_claim AND fire coolant.emit_stop for the released claim.

    Snapshots `phase_id` + `claimed_by` BEFORE delegating to `release_claim`,
    so coolant gets stable values even though release wipes the claim.
    If `release_claim` raises ClaimMismatch the snapshot is discarded — the
    worker still owns the claim, so decrementing coolant would lie about it.
    """
    claim = data.get("current_claim")
    snapshot_phase = claim.get("phase_id") if claim else None
    snapshot_token = claim.get("claimed_by") if claim else None
    release_claim(
        data, expected_token=expected_token, expected_phase=expected_phase,
    )
    if not snapshot_phase or not snapshot_token:
        return
    coolant.emit_stop(
        session_id=snapshot_token,
        agent_id=coolant.format_agent_id(data["plan_slug"], snapshot_phase),
        agent_type=coolant.AGENT_TYPE,
    )


def stamp_attestation(data: dict, kind: str, commit_sha: str) -> None:
    """Stamp current_claim.attestations[kind] with HEAD SHA + now().

    Lazy-inits the attestations map. Overwrites any prior stamp for the
    same kind. Raises ValueError if no current_claim.
    """
    claim = data.get("current_claim")
    if not claim:
        raise ValueError("stamp_attestation: no current_claim")
    claim.setdefault("attestations", {})
    claim["attestations"][kind] = {
        "at": utcnow(),
        "commit_sha": commit_sha,
    }


def attestation_commit_sha(data: dict, kind: str) -> str | None:
    """Return the commit_sha from current_claim.attestations[kind], or None.

    Encapsulates the nested-dict drill so callers (the cmd_complete gate)
    stay oblivious to the attestation map's shape.
    """
    claim = data.get("current_claim") or {}
    attestations = claim.get("attestations") or {}
    entry = attestations.get(kind)
    return entry.get("commit_sha") if entry else None


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
    append_event(
        data, EVENT_PHASE_BLOCKED,
        phase=phase_id, blocker_id=blocker_id, question=question,
    )
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


def get_worktree(data: dict) -> dict | None:
    """The plan's worktree record, or None when the plan runs in the main repo.

    Field is additive optional — readers must tolerate its absence rather than
    relying on a schema_version bump. Shape: `{path, branch, base_ref}`.
    """
    return data.get("worktree")


def claim_git_root(data: dict, cfg) -> Path:
    """Return the git root for the active claim; respects worktree dispatch."""
    wt = get_worktree(data)
    if wt and wt.get("path"):
        return Path(wt["path"])
    return cfg.project_root


def open_blockers(data: dict) -> list[dict]:
    """All blockers with `answer is None`, in order.

    Hot path: fleet view (count), `clu status` (display), inbound poller
    (route by plan). Centralized so the unanswered-predicate can't drift
    between `b["answer"] is None` and `b.get("answer") is None`.
    """
    return [b for b in data.get("blockers", []) if b.get("answer") is None]


def phase_has_open_blocker(data: dict, phase_id: str) -> bool:
    return any(b["phase_id"] == phase_id for b in open_blockers(data))


def latest_event(
    data: dict, event_type: str, *, phase: str | None = None,
) -> dict | None:
    """Most recent event of `event_type`, optionally constrained by phase.

    Centralizes the "find the last X" reverse-scan so the EVENT_* literal
    lives next to its siblings — a typo here silently breaks any caller that
    used to find a match.
    """
    for evt in reversed(data["events"]):
        if evt.get("type") != event_type:
            continue
        if phase is not None and evt.get("phase") != phase:
            continue
        return evt
    return None


def attempts_for_phase(data: dict, phase_id: str) -> int:
    """Count phase_started events for this phase, scoped to the most recent retry.

    Durable across claim clears. `clu retry` appends EVENT_RETRY_REQUESTED and
    `clu release-claim --reset-attempts` appends EVENT_ATTEMPTS_RESET to move
    the floor — only phase_starteds after the most recent of either count, so
    operator-driven aborts don't burn the phase's attempt budget.

    Systemic failures (PATH bug, rate limit, auth) emit EVENT_SYSTEMIC_FAILURE
    naming the token that hit them. The corresponding phase_started is
    subtracted: the phase isn't at fault, so its attempt budget isn't burned.
    """
    floor = -1
    for i, evt in enumerate(data["events"]):
        if (
            evt.get("type") in (EVENT_RETRY_REQUESTED, EVENT_ATTEMPTS_RESET)
            and evt.get("phase") == phase_id
        ):
            floor = i
    systemic_tokens = {
        evt.get("token")
        for evt in data["events"][floor + 1:]
        if evt.get("type") == EVENT_SYSTEMIC_FAILURE
        and evt.get("phase") == phase_id
        and evt.get("token")
    }
    return sum(
        1 for evt in data["events"][floor + 1:]
        if evt.get("type") == EVENT_PHASE_STARTED
        and evt.get("phase") == phase_id
        and evt.get("claimed_by") not in systemic_tokens
    )


def most_recent_halted_phase(data: dict) -> str | None:
    """Phase id from the most recent max-attempts halt, if any."""
    evt = latest_event(data, EVENT_PHASE_MAX_ATTEMPTS)
    return evt["phase"] if evt and "phase" in evt else None


_PAUSE_CAUSE_TYPES: frozenset[str] = frozenset(
    {EVENT_PAUSED, EVENT_BLOCKER_SLA_EXCEEDED},
)


def status_reason(data: dict) -> str | None:
    """One-line human reason for the current status, or None when running/done.

    Derived from the event log so the status string can't drift out of sync
    with the transition that caused it. `clu status` uses this; future
    notifications can hang off it too.
    """
    status = data["status"]
    if status == STATUS_PAUSED:
        # Most recent of {operator pause, SLA escalation} wins — both can
        # land the plan in PAUSED, and the one that did it last is the one
        # the user wants to read about.
        for evt in reversed(data["events"]):
            if evt.get("type") not in _PAUSE_CAUSE_TYPES:
                continue
            if evt["type"] == EVENT_PAUSED:
                reason = evt.get("reason") or ""
                return f"operator pause: {reason}" if reason else "operator pause"
            return (
                f"SLA exceeded — blocker {evt['blocker_id']} "
                f"age {evt['age_hours']}h"
            )
        return None
    if status == STATUS_HALTED:
        evt = latest_event(data, EVENT_PHASE_MAX_ATTEMPTS)
        if evt:
            return f"phase {evt['phase']} hit max attempts ({evt['attempts']})"
        return None
    if status == STATUS_HALTED_REPLAN:
        return "worker requested replan"
    return None
