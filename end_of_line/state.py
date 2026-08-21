"""Plan state: the domain vocabulary, and the routing into its store.

Plan state is the single durable artifact across cold-context phases. It lives
in the project database (`plans/.orchestrator/clu.db`, see `plan_store`), and
this module no longer holds an engine of its own: the flock, the
tmp+fsync+rename write and the whole-document mutate window are gone, and what
remains that touches storage is `load` (one read transaction) plus `key_for`,
which turns the state PATH callers still hold into the store's (dir, slug) key.
Every write in the package names the rows it changes — `plan_store.op_*` — or
declares the preconditions its decision rested on.

Everything else here is domain logic over the loaded dict — claims, blockers,
events, liveness — and is storage-agnostic. It reads and edits snapshots; what
persists an edit is the op the caller picks. The event log is append-only:
projection from events can rebuild any derived field.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import coolant

# Inner char-class body for a slug token — the single source of the slug
# alphabet. Composed into `SLUG_PATTERN` below and into the cmdline token
# boundary in `_cmdline_marker_present`, so the two never drift (drift here is
# a security invariant: path traversal + unmatched inbound replies + the #76
# substring-collision guard). The leading `[a-z0-9]` first-char rule is
# deliberately narrower (no leading `_`/`-`) and stays separate.
_SLUG_CHARS = r"a-z0-9_-"
# Fragment (no anchors) so other modules can compose it into larger patterns
# without redefining the character class.
SLUG_PATTERN = rf"[a-z0-9][{_SLUG_CHARS}]{{0,63}}"
_SLUG_RE = re.compile(rf"^{SLUG_PATTERN}$")


class InvalidSlug(ValueError):
    """Raised when a plan slug or phase id fails validation (path-traversal guard)."""


def validate_slug(slug: str, *, kind: str = "slug") -> None:
    """Reject anything that isn't a safe path component."""
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise InvalidSlug(f"invalid {kind} {slug!r}: must match {_SLUG_RE.pattern}")


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
                "git",
                "-C",
                str(project_root),
                "merge-base",
                "--is-ancestor",
                branch,
                base_ref,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def local_branch_exists(project_root: Path, branch: str) -> bool:
    """Return True iff `branch` exists as a local ref in `project_root`."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "--verify", f"refs/heads/{branch}"],
            capture_output=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


SCHEMA_VERSION = 1

# The suffix that makes a path a plan-state KEY rather than a file. `config`
# builds these paths, `plan_store` re-exports this constant, and the three
# primitives below route on it — one definition so the three can never disagree.
STATE_SUFFIX = ".state.json"


class SchemaVersionMismatch(Exception):
    """Raised when a plan's store was written by a different clu schema version.

    The store translates `db.SchemaTooNew` into this on the way out, so the
    callers that have always caught it by name keep working.
    """


_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"
_TOKEN_LEN = 16  # 64 bits, enough for token-auth use (red team L1).

# Defaults — also embedded in empty_state(); changing here updates both.
DEFAULT_LEASE_TTL_MIN = 60
DEFAULT_SLA_HOURS = 24
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_SPAWNS_PER_PHASE = 10
DEFAULT_MAX_QUEUE_ADDS_PER_PHASE = 3
# Bounds for the derived stalled-heartbeat threshold (minutes). Floor
# keeps short Effort-scaled leases (#58) from triggering too eagerly;
# ceiling keeps long leases from letting wedged workers slip past the
# watchdog until full lease expiry. Both bypassed by an explicit
# `config.stalled_heartbeat_minutes` (operator override).
STALLED_HEARTBEAT_MIN_FLOOR = 15
STALLED_HEARTBEAT_MIN_CEILING = 25

# Plan status (`data["status"]`)
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_HALTED = "halted"
STATUS_HALTED_REPLAN = "halted_for_replan"
STATUS_DONE = "done"
TERMINAL_STATUSES = frozenset({STATUS_PAUSED, STATUS_HALTED, STATUS_HALTED_REPLAN, STATUS_DONE})
# `clu worktree gc` eligibility — terminal minus paused. Paused plans may
# resume and need their worktree intact; done/halted plans won't (operator
# uses `clu retry` only on halted, which the gc action-time re-check
# blocks).
GC_ELIGIBLE_STATUSES = frozenset({STATUS_DONE, STATUS_HALTED, STATUS_HALTED_REPLAN})
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
EVENT_PLAN_ABANDONED = "plan_abandoned"
EVENT_DISPATCH_FAILED = "dispatch_failed"
EVENT_SYSTEMIC_FAILURE = "systemic_failure"
# Quota-death family (#94). QUOTA_DEATH marks a worker killed by the
# operator's subscription quota (kwargs: phase, token, signature, line);
# its phase_started is forgiven in attempts_for_phase, same as systemic
# failures. QUOTA_PAUSED / QUOTA_RESUMED bracket the project-level
# dispatch pause on the triggering plan's event log — the pause itself
# is one row in the project database (`quota`), never in plan status.
EVENT_QUOTA_DEATH = "quota_death"
EVENT_QUOTA_PAUSED = "quota_paused"
EVENT_QUOTA_RESUMED = "quota_resumed"
EVENT_PHASE_STALLED = "phase_stalled"
EVENT_PAUSED = "paused"
EVENT_RESUMED = "resumed"
EVENT_RETRY_REQUESTED = "retry_requested"
# Provenance event written as the FIRST event of a plan created by
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
# Worker-side heartbeat-loop failure surface: fires when the worker's
# heartbeat daemon (`clu heartbeat-daemon`) records 3 consecutive failed
# pings (~6min at 120s interval). Idempotent per claim via
# heartbeat_loop_failing_notified.
EVENT_HEARTBEAT_LOOP_FAILING = "heartbeat_loop_failing"
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
# `cmd_complete` refused on the verify or simplify attestation gate. Fires once
# per refusal call (no dedup — operator-dashboard / #70 wants visibility into
# every gate hit). Fields: phase, gate ("verify" | "simplify"), stamped_at
# (last stamp SHA or None), head_sha (current HEAD that was refused).
EVENT_ATTESTATION_REFUSED = "attestation_refused"
# Supervisor reaped an orphaned worker process after lease expiry.
# Fields: phase, pid, signaled ("SIGTERM" | "SIGTERM+SIGKILL"), cmdline_mismatch (bool).
EVENT_PHASE_ORPHAN_REAPED = "phase_orphan_reaped"
# Supervisor's `_detect_dead_pid` rule fired: the claim's worker PID is gone
# (ESRCH) or has been recycled to an unrelated process (cmdline mismatch),
# but the lease hasn't expired yet — without this rule we'd zombie the claim
# until full lease TTL. Fires before `_detect_stalled` so a fresh-heartbeat
# zombie (issue #72) is caught within one tick of worker death.
# Fields: phase, pid, cmdline_mismatch (bool).
EVENT_PHASE_WORKER_DEAD = "phase_worker_dead"
# Supervisor's process-tree walker detected a long-lived, low-CPU descendant
# of the worker pid — i.e. a Bash tool wedged on something (canonical case:
# xcodebuild hanging on simulator HK auth). Detection only, no auto-kill.
# Fields: plan, phase, worker_pid, descendant_pid, command (first 200 chars),
# elapsed_seconds, cpu_seconds. Deduped per descendant_pid via
# current_claim.stuck_tool_emitted_at — at most one emit per (claim, leaf).
EVENT_TOOL_STUCK = "tool_stuck"
# Supervisor detected worker PID alive but CPU-idle with no active Bash tool:
# the worker's whole process tree accrued almost no processor time across an
# uninterrupted window — classic silent wedge. Detection only;
# operator-approval checkpoint from user-CLAUDE.md applies.
# Fields: plan, phase, pid, low_cpu_minutes. Deduped via worker_idle_notified.
EVENT_WORKER_IDLE = "worker_idle"
# The per-worker heartbeat daemon detected its worker PID dead (cmdline-anchored
# liveness probe) and reported it through the token-validated `notify-worker-dead`
# callback — distinct from EVENT_PHASE_WORKER_DEAD, which is the supervisor's own
# tick-side observation. Two processes, two evidences; collapsing them would make
# the state file lie about who saw what. Deduped via the claim's
# worker_death_reported marker so the supervisor doesn't re-notify.
# Fields: phase, pid, log_path (the ATTEMPT log, not the daemon .hb.log sidecar),
# reporter ("heartbeat_daemon").
EVENT_PHASE_WORKER_DEAD_REPORTED = "phase_worker_dead_reported"

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

# Absolute ceiling on current_claim.cpu_samples — an unbounded-growth guard on
# the claim row's JSON column, and NOTHING ELSE. Retention is by AGE
# (`append_cpu_sample`'s `retain_seconds`), because a count cap silently
# couples the window to the tick cadence: the old cap of 20 at the 30s cadence
# held 570 seconds of history against a 600-second window requirement, which
# made the idle predicate unsatisfiable under continuous sampling and
# satisfiable only after a sampling GAP — i.e. only when the worker had
# demonstrably been working. Keep this generous enough that it never becomes
# the retention rule again.
WORKER_IDLE_SAMPLE_CAP = 200

# Age bound for the active-tool marker when the stuck-tool detector is DISABLED
# (`stuck_tool_threshold_seconds = 0`). Normally the bound IS that threshold, so
# the two watchdogs cannot disagree about whether a tool is still running — but
# with the sibling detector off there is no threshold to borrow, and leaving the
# marker unbounded would mean disabling one watchdog silently deafens another.
# Equal to the config default, so turning stuck-tool detection off changes only
# stuck-tool detection.
ACTIVITY_MARKER_FALLBACK_BOUND_SECONDS = 300

# Default ceiling on a worker-declared quiet span, in minutes — the ONLY bound
# on how much silence a worker can buy by declaring one, so an operator raising
# it is widening the window in which a wedge goes unreported. Lives here rather
# than inline in `config.py` because the config default and this number must
# never drift apart: the clamp that enforces the ceiling reads the config value,
# and a second literal would let the two disagree silently.
# Operator sign-off 2026-08-21 (`plans/false-alarms.md`, Status): 45.
QUIET_SPAN_CEILING_DEFAULT_MINUTES = 45


@dataclass
class ReapResult:
    signaled: str | None
    escalated_kill: bool
    cmdline_mismatch: bool


# A slug token in a cmdline must be bounded by non-slug chars — not matched as
# a bare substring (#76). A bare `marker in cmdline` false-matches slug prefixes
# (`w1` inside `w1-foo`) and incidental substrings (log paths). `\b` is wrong
# here: Python `\w` excludes `-` but includes `_`, so `\bw1\b` would match
# `w1-foo` and miss `w1_foo`. Anchor on the slug alphabet (`_SLUG_CHARS`,
# shared with `SLUG_PATTERN`) instead.
_SLUG_CHAR = rf"[{_SLUG_CHARS}]"


def _cmdline_marker_present(cmdline: str, marker: str) -> bool:
    """True when `marker` appears in `cmdline` as a whole slug-delimited token.

    The marker is bounded by any non-slug char (whitespace, `=`, `/`, quotes)
    or a string edge — so `--plan w1`, `--plan=w1`, and `/clu-phase w1 a` all
    match `w1`, while `w1-foo` / `w1_foo` do not. Multi-token markers
    (`/clu-phase foo bar`) are bounded only at their two ends.
    """
    pattern = rf"(?<!{_SLUG_CHAR}){re.escape(marker)}(?!{_SLUG_CHAR})"
    return re.search(pattern, cmdline) is not None


def claim_worker_alive(claim: dict, cmdline_match: str | None = None) -> bool:
    """Liveness probe for the supervisor's `_detect_dead_pid` rule.

    Returns True when the PID is reachable AND (if `cmdline_match` is given) the
    process cmdline carries the expected marker as a whole slug-delimited token
    (see `_cmdline_marker_present`); False otherwise. ESRCH → dead (False);
    EPERM → exists-but-unsignalable, treated as alive (True).

    PID=None → True. The Popen-to-_stamp_pid race window leaves a brief
    period where current_claim is set but pid is not yet stamped — treat
    that as alive so the supervisor doesn't kill a freshly-claimed phase.
    """
    pid = claim.get("pid")
    if pid is None:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM means the process exists but we can't signal it (cross-user
        # or sandboxed). Treat as alive — EPERM means the process exists.
        return True
    if cmdline_match is None:
        return True
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        # ps wedged / missing / signal-interrupted → can't disprove liveness.
        # Default to alive so the supervisor doesn't kill a real worker on
        # transient host weirdness.
        return True
    if result.returncode != 0:
        return False
    return _cmdline_marker_present(result.stdout, cmdline_match)


def _pgroup_member_cmdlines(pgid: int) -> list[str]:
    """Cmdlines of every live process currently in process group `pgid`.

    Empty list on no members / `ps` failure. Used as the PID-reuse guard for
    `reap_orphan_pgroup`: a recycled pgid won't carry our plan's marker.
    """
    try:
        # `-eo` (GNU/UNIX style) is the repo's portable convention — works on
        # both macOS (BSD ps) and Linux (procps); BSD-style `-ax` risks procps
        # personality differences. Empty `=` headers suppress the title line.
        result = subprocess.run(
            ["ps", "-eo", "pgid=,command="],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    cmdlines: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pg, cmd = parts
        if pg.isdigit() and int(pg) == pgid:
            cmdlines.append(cmd)
    return cmdlines


def reap_orphan_pgroup(pgid: int, cmdline_match: str | None = None) -> ReapResult:
    """SIGTERM→SIGKILL an orphaned worker's whole process GROUP.

    The clu worker is spawned `start_new_session=True`, so its PGID == its PID
    and the backgrounded `clu heartbeat` subshell inherits that group. Reaping
    the group takes worker + heartbeat together — a single-PID SIGTERM would
    kill only the worker and leave the heartbeat reparented to launchd, looping
    for hours (the #75 orphan). Reparenting changes the parent,
    not the PGID, so `killpg` still reaches the heartbeat after the worker dies,
    as long as any group member is alive.

    Guards:
      - `pgid <= 0` (0 == the *caller's own* group to killpg) or
        `pgid == os.getpgid(0)` → no-op. Never signal the clu CLI / cron tick
        that called us.
      - PID-reuse: when `cmdline_match` is given, at least one live group member
        must carry the marker before we signal. No members → "gone" (no-op);
        members but no match → `cmdline_mismatch=True`, no signal.

    Escalation: SIGTERM, poll 5s, then SIGKILL. Best-effort —
    `ProcessLookupError`/`PermissionError` resolve to a no-op rather than
    raising, so a reap during cleanup never crashes the command.
    """
    try:
        own = os.getpgid(0)
    except OSError:
        own = None
    if pgid <= 0 or pgid == own:
        return ReapResult(None, False, False)

    if cmdline_match is not None:
        members = _pgroup_member_cmdlines(pgid)
        if not any(_cmdline_marker_present(cmd, cmdline_match) for cmd in members):
            # members present but unmatched → reused/unrelated group (mismatch);
            # no members → already gone. Either way we do not signal.
            return ReapResult(None, False, bool(members))

    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return ReapResult(None, False, False)

    for _ in range(20):
        time.sleep(0.25)
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return ReapResult(SIGNAL_TERM, False, False)
        except PermissionError:
            # Transient EPERM (a group member mid-exit, or the pgid briefly
            # racing another owner) means we can't confirm death THIS poll —
            # keep polling rather than crash the caller. The SIGTERM/SIGKILL
            # killpg calls already tolerate EPERM; this poll must too, or a
            # best-effort reap (e.g. `clu demo down`'s teardown) blows up.
            continue

    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # EPERM here (e.g. only-remaining member changed credentials between
        # SIGTERM and now) must not crash a best-effort cleanup — honor the
        # no-op-on-failure contract the first killpg already follows.
        pass
    return ReapResult(SIGNAL_TERM_THEN_KILL, True, False)


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


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
        },
        "phases": [],
        "events": [],
        "created_at": utcnow(),
        "batch_id": None,
    }


def _plan_store():
    """The store, imported on use.

    `plan_store` imports this module for `validate_slug` / `SCHEMA_VERSION`, so
    the dependency runs one way at import time and the other way at call time.
    """
    from . import plan_store

    return plan_store


def load(state_path: Path) -> dict:
    """The plan at `state_path`, as a dict — one read transaction on the store.

    The path is the KEY, not a file: `plan_store.snapshot` reads the rows and
    raises the same `FileNotFoundError` / `ValueError` / `SchemaVersionMismatch`
    the file era raised, so every tolerant reader in the fleet keeps its
    `except` clause. The version check is the database's own `user_version`
    (upstream decision #6: a store from a newer clu is skipped, never
    downgraded), which is why this no longer takes an expected version — there
    is no longer a document with a `schema_version` field in it to compare.
    """
    return _plan_store().snapshot(*key_for(state_path))


def key_for(state_path: Path) -> tuple[Path, str]:
    """(orchestrator dir, slug) for a plan-state path — the store's key."""
    return _plan_store().key_for_state_path(state_path)


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


def stalled_threshold_for_phase(data: dict, phase_id: str) -> int:
    """Heartbeat threshold (minutes) before a claim is flagged stalled.

    Explicit `config.stalled_heartbeat_minutes` wins. Otherwise derive
    as `max(STALLED_HEARTBEAT_MIN_FLOOR, lease_ttl_for_phase // 2)`
    capped at `STALLED_HEARTBEAT_MIN_CEILING`. The floor keeps short
    Effort-scaled leases (#58) from triggering too eagerly; the ceiling
    keeps long leases from leaving wedged workers undetected until full
    lease expiry. The heartbeat daemon pings on an independent 120s
    timer (`clu heartbeat-daemon`; see clu-phase SKILL.md), so deep
    tool-use chains do not legitimately skip heartbeats — staleness past
    the ceiling means something is wrong, regardless of lease length.
    """
    explicit = data.get("config", {}).get("stalled_heartbeat_minutes")
    if explicit is not None:
        return int(explicit)
    derived = max(STALLED_HEARTBEAT_MIN_FLOOR, lease_ttl_for_phase(data, phase_id) // 2)
    return min(STALLED_HEARTBEAT_MIN_CEILING, derived)


def claim_is_stalled(
    data: dict,
    claim: dict,
    now: _dt.datetime | None = None,
) -> bool:
    """`is_claim_stalled` paired with `stalled_threshold_for_phase`.

    The supervisor's stalled-detection path doesn't use this wrapper — it
    needs the raw `age` in seconds for the event payload and notify body.
    All other callers (`fleet.summarize_plan`, the CLI status / heartbeat
    / release-claim helpers) just need the boolean.
    """
    return is_claim_stalled(
        claim,
        stalled_threshold_for_phase(data, claim["phase_id"]),
        now=now,
    )


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
        data,
        EVENT_LEASE_EXPIRED,
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
    attempts = (
        sum(
            1
            for evt in data["events"]
            if evt.get("type") == EVENT_PHASE_STARTED and evt.get("phase") == phase_id
        )
        + 1
    )
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
            f"token mismatch: claim is {claim.get('claimed_by')!r}, got {expected_token!r}"
        )
    if claim.get("phase_id") != expected_phase:
        raise ClaimMismatch(
            f"phase mismatch: claim is {claim.get('phase_id')!r}, got {expected_phase!r}"
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
    claim: dict,
    threshold_minutes: int,
    now: _dt.datetime | None = None,
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
        raise ValueError("release_claim: expected_token and expected_phase must be passed together")
    assert_claim_match(data, expected_token, expected_phase)
    data["current_claim"] = None


def release_claim_and_emit(
    data: dict,
    expected_token: str | None = None,
    expected_phase: str | None = None,
    coolant_enabled: bool = True,
    coolant_script_override: str | None = None,
) -> None:
    """Release current_claim AND fire coolant.emit_stop for the released claim.

    Snapshots `phase_id` + `claimed_by` BEFORE delegating to `release_claim`,
    so coolant gets stable values even though release wipes the claim.
    If `release_claim` raises ClaimMismatch the snapshot is discarded — the
    worker still owns the claim, so decrementing coolant would lie about it.

    `coolant_enabled=False` skips the emit (release still happens). Callers
    typically pass `cfg.coolant.enabled` so the per-project opt-out works
    end-to-end.
    """
    claim = data.get("current_claim")
    snapshot_phase = claim.get("phase_id") if claim else None
    snapshot_token = claim.get("claimed_by") if claim else None
    release_claim(
        data,
        expected_token=expected_token,
        expected_phase=expected_phase,
    )
    if not coolant_enabled:
        return
    if not snapshot_phase or not snapshot_token:
        return
    coolant.emit_stop(
        session_id=snapshot_token,
        agent_id=coolant.format_agent_id(data["plan_slug"], snapshot_phase),
        agent_type=coolant.AGENT_TYPE,
        script_override=coolant_script_override,
    )


def terminalize(
    data: dict,
    *,
    status: str = STATUS_HALTED,
    event: str = EVENT_PLAN_ABANDONED,
    **event_fields: Any,
) -> bool:
    """Flip a non-terminal plan to a terminal status + emit an audit event.

    Compare-and-set: returns False (no status change, no event) when the plan
    is already terminal, so a cron tick racing a manual cleanup can't
    double-terminalize. Caller holds the `mutate` lock. Returns True when it
    actually transitioned.

    Closes the #75 zombie: `unregister` / the registry-independent sweep call
    this so no state file is ever left at `running` after the registry row goes.
    """
    if data["status"] in TERMINAL_STATUSES:
        return False
    data["status"] = status
    append_event(data, event, **event_fields)
    return True


def reap_claim(data: dict) -> ReapResult | None:
    """Best-effort reap of the active claim's worker process GROUP.

    Returns None when there's no claim or no recorded pgid/pid. Falls back to
    `pid` for pre-#75 state files (pid == pgid: the worker is a session leader).
    Uses the plan slug as the PID-reuse marker — see the inline note below for
    why the slug, not `/clu-phase <plan> <phase>`.
    """
    claim = data.get("current_claim")
    if not claim:
        return None
    pgid = claim.get("pgid") or claim.get("pid")
    if not pgid:
        return None
    # Marker = the plan slug, NOT `/clu-phase <plan> <phase>`. The slug is the
    # only token present in BOTH the worker cmdline (every dispatch template
    # names the slug) AND the heartbeat cmdline (`clu heartbeat --plan <slug>`),
    # so it matches whichever group member survives — critically the heartbeat,
    # after the worker dies. The token-anchored match (`_cmdline_marker_present`)
    # plus pgid-scoping makes a slug collision with an unrelated reused group a
    # non-issue. No slug → no PID-reuse guard → refuse.
    slug = data.get("plan_slug")
    if not slug:
        return None
    return reap_orphan_pgroup(pgid, cmdline_match=slug)


def is_zombie_state(data: dict) -> bool:
    """A registry-independent zombie: `status=running` but nothing will ever
    advance it. Callers restrict this to UNREGISTERED state files — a registered
    running plan is owned by tick-all / the supervisor (which may legitimately
    sit claimless between phases).

    Two shapes, both from #75:
      - claimless: running + no `current_claim` (the `fm-docs-sweep` zombie — it
        never left `running` and has no worker).
      - dead-claim: running + a claim whose worker PID is gone (an orphaned
        worker that died unclean).

    A running plan with a LIVE worker is NOT a zombie — the OS PID probe
    (`claim_worker_alive`, authoritative over heartbeat TTL) is what gates this,
    so a merely-slow worker is never reaped.
    """
    if data.get("status") != STATUS_RUNNING:
        return False
    claim = data.get("current_claim")
    if not claim:
        return True
    return not claim_worker_alive(claim, cmdline_match=data.get("plan_slug"))


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


def mark_tool_stuck_emitted(claim: dict, descendant_pid: int, at: str) -> None:
    """Record that EVENT_TOOL_STUCK fired for this descendant_pid on this claim.

    Used by the supervisor to dedupe: detect_stuck_tools fires on every tick,
    but a wedged xcodebuild should only emit one event per (claim, leaf).
    Keys are stringified pids because JSON object keys must be strings.
    """
    claim.setdefault("stuck_tool_emitted_at", {})[str(descendant_pid)] = at


def tool_stuck_already_emitted(claim: dict, descendant_pid: int) -> bool:
    """True if EVENT_TOOL_STUCK already fired for this descendant_pid."""
    return str(descendant_pid) in (claim.get("stuck_tool_emitted_at") or {})


def mark_heartbeat_loop_failing_notified(claim: dict) -> bool:
    """Stamp heartbeat_loop_failing_notified on the claim. Returns True if newly set."""
    if claim.get("heartbeat_loop_failing_notified"):
        return False
    claim["heartbeat_loop_failing_notified"] = True
    return True


def worker_idle_already_emitted(claim: dict) -> bool:
    """True if EVENT_WORKER_IDLE already fired for this claim."""
    return bool(claim.get("worker_idle_notified", False))


def mark_worker_idle_emitted(claim: dict, now: _dt.datetime) -> None:
    """Stamp worker_idle_notified + timestamp on the claim."""
    claim["worker_idle_notified"] = True
    claim["worker_idle_notified_at"] = now.strftime(_ISO_FMT)


def worker_death_already_reported(claim: dict) -> bool:
    """True if the heartbeat daemon already reported this claim's worker dead.

    The dedup marker the supervisor consults before firing its own operator
    notification — without it a single death pings the operator twice (daemon
    + tick). See EVENT_PHASE_WORKER_DEAD_REPORTED.
    """
    return bool(claim.get("worker_death_reported", False))


def mark_worker_death_reported(claim: dict, now: _dt.datetime) -> None:
    """Stamp worker_death_reported + timestamp on the claim."""
    claim["worker_death_reported"] = True
    claim["worker_death_reported_at"] = now.strftime(_ISO_FMT)


def append_cpu_sample(
    claim: dict,
    cpu_seconds: float,
    now: _dt.datetime,
    *,
    retain_seconds: float,
) -> None:
    """Append one CUMULATIVE-CPU sample and retire the ones that aged out.

    `cpu_seconds` is the processor time the worker's whole process tree has
    consumed since launch — a monotonically rising total, not a rate. The
    window predicate reads the DELTA across it; a single sample means nothing
    on its own.

    Retention is by age: anything older than `retain_seconds` before this
    sample is dropped, so how much history survives depends on wall time
    rather than on how often the supervisor happens to tick.
    `WORKER_IDLE_SAMPLE_CAP` is only the unbounded-growth backstop.
    """
    samples: list[dict] = claim.setdefault("cpu_samples", [])
    samples.append({"ts": now.strftime(_ISO_FMT), "cpu": cpu_seconds})
    cutoff = now - _dt.timedelta(seconds=retain_seconds)
    kept: list[dict] = []
    for sample in samples:
        try:
            ts = parse_iso(sample["ts"])
        except (KeyError, ValueError, TypeError):
            # An unparseable stamp can never satisfy the predicate and can
            # never age out on its own; drop it rather than wedge the list.
            continue
        if ts >= cutoff:
            kept.append(sample)
    if len(kept) > WORKER_IDLE_SAMPLE_CAP:
        del kept[: len(kept) - WORKER_IDLE_SAMPLE_CAP]
    samples[:] = kept


def worker_idle_window_satisfied(
    claim: dict,
    now: _dt.datetime,
    *,
    min_samples: int,
    window_min: float,
    max_sample_gap: float,
    cpu_delta_threshold: float,
) -> bool:
    """True when the sample history is EVIDENCE that the worker did nothing.

    Four independent conditions, and each one exists because its absence
    produced a false alarm:

    * **span** — `samples[-1] - samples[0]` covers ≥ `window_min` minutes.
      Measured between OBSERVED samples, never from `now`, because the caller
      appends with `now` immediately before asking: measuring to `now` counts
      an interval that was never sampled.
    * **contiguity** — no adjacent gap exceeds `max_sample_gap`. A hole means
      sampling was suppressed, and sampling is suppressed exactly when a tool
      call is running — so a hole is positive evidence the worker was WORKING,
      not evidence it was idle.
    * **recency** — the newest sample is no older than `max_sample_gap`. A
      window that stopped updating says nothing about the present.
    * **quiet** — the tree's cumulative processor time rose by no more than
      `cpu_delta_threshold` across the whole window. A NEGATIVE delta (a
      recycled pid, or a descendant exiting mid-window) means the sum changed
      meaning, so it reads as "cannot judge" rather than "extremely quiet".
    """
    samples: list[dict] = claim.get("cpu_samples") or []
    # `not samples` is not folded into the `min_samples` comparison: this
    # function indexes the newest stamp below, so an empty history must be
    # refused on its own terms rather than on a caller-supplied minimum that
    # could be zero. Config rejects a zero minimum, but this predicate is
    # public and its other callers are not config-bound.
    if not samples or len(samples) < min_samples:
        return False
    try:
        stamps = [parse_iso(s["ts"]) for s in samples]
        cpus = [float(s["cpu"]) for s in samples]
    except (KeyError, ValueError, TypeError):
        return False

    if (stamps[-1] - stamps[0]).total_seconds() < window_min * 60.0:
        return False
    if any(
        (later - earlier).total_seconds() > max_sample_gap
        for earlier, later in zip(stamps, stamps[1:])
    ):
        return False
    if (now - stamps[-1]).total_seconds() > max_sample_gap:
        return False
    delta = cpus[-1] - cpus[0]
    if delta < 0:
        return False
    return delta <= cpu_delta_threshold


def activity_marker_suppresses(
    claim: dict,
    now: _dt.datetime,
    *,
    max_age_seconds: float,
) -> bool:
    """True while the claim's active-tool marker is FRESH enough to believe.

    `active_tool_started_at` is stamped by the PreToolUse hook and cleared by
    PostToolUse — but PostToolUse does not fire for a Bash command that exits
    NONZERO, and `PostToolUseFailure` does not fire at all (probed on Claude
    Code 2.1.238 with all three events registered). So every failing test run,
    failing build and empty `grep` leaves this stamped, and it stays stamped
    until the next Bash call overwrites it — forever, when the failing call
    was the phase's last. Read as bare truthiness, that is a silence switch
    for the idle watchdog.

    Nothing can be wired to close it, so it EXPIRES instead. The bound is the
    caller's `stuck_tool_threshold_seconds`, deliberately not a number of its
    own: past it the sibling stuck-tool detector already considers the call
    long enough to be wedged, and two watchdogs disagreeing about whether a
    tool is still running is the state this shares one number to avoid.

    Three edges, each the safe direction of its own failure:

    * **`max_age_seconds <= 0`** — the stuck-tool detector is DISABLED, so
      there is no sibling window to derive a bound from, and both readings of
      "no window" are wrong in opposite directions. Zero seconds would make
      every live Bash call read as an idle worker; NO bound would hand back
      the silence switch this predicate exists to remove, so disabling one
      watchdog would silently deafen another. It falls back to
      `ACTIVITY_MARKER_FALLBACK_BOUND_SECONDS` instead, which equals the
      config default — so nobody who left the detector alone sees a change.
    * **A stamp whose age cannot be computed** does not suppress. It cannot be
      shown to be fresh, and an unbounded silence is the failure this exists to
      close. `_emit_stuck_tool` is where the operator hears about it.
    * **A stamp in the future** (clock skew between the worker writing and the
      supervisor reading) is "just started", never "very old".
    """
    marker = claim.get("active_tool_started_at")
    if not marker:
        return False
    if max_age_seconds <= 0:
        max_age_seconds = ACTIVITY_MARKER_FALLBACK_BOUND_SECONDS
    try:
        age = (now - parse_iso(marker)).total_seconds()
    except (ValueError, TypeError):
        return False
    return age < max_age_seconds


def build_quiet_span(
    reason: str,
    expected_minutes: float,
    now: _dt.datetime,
    *,
    ceiling_minutes: float,
) -> dict:
    """The claim record for a worker-declared stretch of expected silence.

    A worker about to run a code review or a full test gate knows it is going
    to look wedged for the next N minutes. It says so once, here, instead of
    leaving the supervisor to infer it from process CPU — but what it says is
    a LEASE, not one half of a pair. `expires_at` is stamped at declaration
    time from clu's own clock, so the span ends whether or not the worker
    lives to call `--end`, and a worker that dies mid-review does not leave
    the watchdog deaf.

    Two clamps, both toward LESS silence:

    * `ceiling_minutes` caps the declaration, so a worker cannot buy unlimited
      silence by declaring a ten-hour review. A ceiling of **0 means workers
      may declare no silence at all** — the span is written and expires the
      instant it is created, so `quiet_span_active` is false from the first
      read. That is the disabled setting, and it disables the SUPPRESSION
      rather than the bound: the opposite reading ("no ceiling") would make a
      config value meant to limit silence the switch that removes the limit.
    * A negative or zero `expected_minutes` clamps to zero for the same
      reason. The callback rejects one before reaching here; this function is
      public and its next caller might not.

    `started_at` is recorded even though nothing reads it for the expiry
    decision: it is what tells an operator reading `clu state dump` when the
    worker went quiet and for how long it claimed it would.
    """
    minutes = max(0.0, min(float(expected_minutes), float(ceiling_minutes)))
    return {
        "reason": reason,
        "started_at": now.strftime(_ISO_FMT),
        "expires_at": (now + _dt.timedelta(minutes=minutes)).strftime(_ISO_FMT),
    }


def quiet_span_active(claim: dict, now: _dt.datetime) -> bool:
    """True while the claim carries a declared quiet span that has NOT expired.

    Read-site bounded, like `activity_marker_suppresses` and for the same
    reason — every close-event design this plan examined has failed in the
    field, so a suppression that waits for a message to arrive is a
    suppression that eventually becomes permanent. The bound here rides IN the
    record (`expires_at`, stamped and clamped at declaration time by
    `build_quiet_span`) rather than being supplied by the caller, which is why
    this predicate needs no threshold argument: there is no reading of a
    quiet-span claim under which "how long is it good for" is the reader's
    question to answer.

    Every malformed shape answers FALSE — no span, a non-object, a missing or
    unparseable `expires_at`. That is the safe direction: false means the idle
    watchdog goes on to judge the worker on p1's evidence, which is the floor
    this phase sits in front of rather than replaces. True on a shape nobody
    can date would be silence with no end, which is the one outcome this
    design refuses.
    """
    span = claim.get("quiet_span")
    if not isinstance(span, dict):
        return False
    expires_at = span.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at:
        return False
    try:
        expiry = parse_iso(expires_at)
    except (ValueError, TypeError):
        return False
    return now < expiry


def stamp_activity_marker(
    state_path: Path,
    *,
    token: str,
    phase: str,
    action: str,
    timeout_seconds: float | None = None,
) -> bool:
    """Stamp or clear `current_claim.active_tool_started_at` in one window.

    `action` is "start" (PreToolUse) or "end" (PostToolUse). Token + phase
    are validated against the live claim; mismatch raises `ClaimMismatch`.
    `timeout_seconds` bounds the wait — the hot-path hook entry point passes
    2.0 so a contended store drops the update rather than freezing the
    worker's Bash invocation. Returns True on stamp, False on that drop.
    Shared by `cli.cmd_activity` and the thin `end_of_line.activity_hook`
    entry point.

    One native UPDATE of one column, not a whole-plan rewrite: this fires on
    every Bash tool call a worker makes, which is the highest write rate in
    the fleet after the heartbeat.
    """
    plan_store = _plan_store()
    orch_dir, slug = plan_store.key_for_state_path(state_path)
    return plan_store.op_activity(
        orch_dir,
        slug,
        token=token,
        phase=phase,
        action=action,
        timeout_s=timeout_seconds,
    )


def add_blocker(
    data: dict,
    phase_id: str,
    question: str,
    options: list[str],
    context: str = "",
    blocker_type: str = BLOCKER_INPUT,
) -> str:
    blocker_id = f"q-{len(data['blockers']) + 1}"
    data["blockers"].append(
        {
            "id": blocker_id,
            "phase_id": phase_id,
            "type": blocker_type,
            "question": question,
            "options": list(options),
            "context": context,
            "asked_at": utcnow(),
            "answer": None,
            "answered_at": None,
        }
    )
    append_event(
        data,
        EVENT_PHASE_BLOCKED,
        phase=phase_id,
        blocker_id=blocker_id,
        question=question,
    )
    return blocker_id


def answer_blocker(data: dict, blocker_id: str, answer: str) -> None:
    for b in data["blockers"]:
        if b["id"] == blocker_id and b["answer"] is None:
            b["answer"] = answer
            b["answered_at"] = utcnow()
            append_event(
                data,
                EVENT_BLOCKER_ANSWERED,
                blocker_id=blocker_id,
                answer=answer,
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
    data: dict,
    event_type: str,
    *,
    phase: str | None = None,
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
    and quota deaths (#94) emit EVENT_QUOTA_DEATH, each naming the token that
    hit them. The corresponding phase_started is subtracted: the phase isn't
    at fault, so its attempt budget isn't burned.
    """
    floor = -1
    for i, evt in enumerate(data["events"]):
        if (
            evt.get("type") in (EVENT_RETRY_REQUESTED, EVENT_ATTEMPTS_RESET)
            and evt.get("phase") == phase_id
        ):
            floor = i
    forgiven_tokens = {
        evt.get("token")
        for evt in data["events"][floor + 1 :]
        if evt.get("type") in (EVENT_SYSTEMIC_FAILURE, EVENT_QUOTA_DEATH)
        and evt.get("phase") == phase_id
        and evt.get("token")
    }
    return sum(
        1
        for evt in data["events"][floor + 1 :]
        if evt.get("type") == EVENT_PHASE_STARTED
        and evt.get("phase") == phase_id
        and evt.get("claimed_by") not in forgiven_tokens
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
            return f"SLA exceeded — blocker {evt['blocker_id']} age {evt['age_hours']}h"
        return None
    if status == STATUS_HALTED:
        evt = latest_event(data, EVENT_PHASE_MAX_ATTEMPTS)
        if evt:
            return f"phase {evt['phase']} hit max attempts ({evt['attempts']})"
        return None
    if status == STATUS_HALTED_REPLAN:
        return "worker requested replan"
    return None
