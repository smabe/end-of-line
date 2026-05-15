"""Spawn worker sessions for dispatched phases.

Fire-and-forget but observable: each worker's stderr/stdout streams to a
per-token log file, and the dispatched pid is stamped on the claim. A
fast-fail check (0.5s after spawn) catches shell exit-127 / immediate
crashes and releases the claim so the next tick can retry instead of
waiting 30 minutes for the lease to expire silently.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from . import notify, state as st
from .config import ProjectConfig
from .supervisor import TickResult

# How long to wait for a fast-fail before declaring the worker healthy.
# `proc.wait(timeout=)` returns immediately if the worker exited sooner —
# we only pay this latency for the genuinely-still-running case. Plenty
# of headroom for fork+exec; longer than this and we'd be re-implementing
# the lease.
_FAST_FAIL_WAIT_SEC = 0.5

# Synchronous: the cron tick blocks waiting on the repair worker, so a
# hung worker can't stall the queue indefinitely. 60s is plenty for a
# small JSON repair; if the cron cadence is faster than this, the next
# tick will still wait for the lock the previous one is holding.
DEFAULT_REPAIR_TIMEOUT_SEC = 60

# Sentinel rc returned by dispatch_repair_worker when the worker hung
# past the timeout and was killed. Distinct from any rc the worker
# itself could plausibly emit.
REPAIR_RC_TIMEOUT = -1

# Suggested schema_json bundle to pass into the repair worker prompt.
# Stays here (not config) because it has to track queue.SCHEMA_VERSION.
_REPAIR_SCHEMA_HINT = json.dumps({
    "schema_version": 1,
    "queue": [{
        "slug": "<plan-slug>",
        "added_at": "<iso8601-utc>",
        "added_by": "operator",
        "position_at_add": "tail|front",
    }],
    "history": [{
        "slug": "<plan-slug>",
        "added_at": "<iso8601-utc>",
        "ended_at": "<iso8601-utc>",
        "outcome": "abandoned|removed|absorbed",
    }],
})

# Exceptions that are recoverable in dispatch fallback paths.
_DISPATCH_FALLBACK_ERRORS = (OSError, json.JSONDecodeError, st.SchemaVersionMismatch)

# Inspect only the tail of the worker log — a 50k-line stack trace
# shouldn't slow the supervisor, and the relevant signal is always at
# the end (rc was just observed).
_SYSTEMIC_TAIL_LINES = 50

# Hard-coded signature list. Grows via PR only; no config field. Order
# matters — first match wins, so put the most specific (rc-gated) one
# first.
_RATE_LIMIT_RE = re.compile(
    r"(rate[\s_-]?limit|RateLimitError)", re.IGNORECASE,
)
_AUTH_FAILURE_RE = re.compile(
    r"(401\s+Unauthorized|AuthenticationError|invalid\s+api\s+key)",
    re.IGNORECASE,
)
_MISSING_BINARY_RE = re.compile(r"command not found", re.IGNORECASE)


def build_worker_env(cfg: ProjectConfig) -> dict[str, str] | None:
    """Return the env dict to pass to subprocess.Popen, or None to inherit.

    Merges (not replaces) os.environ when an override is configured — a bare
    {"PATH": ...} would strip HOME/USER and break `claude --print` in the
    worker (the #9 regression). Empty path == no override == inherit.
    """
    if cfg.dispatch.path:
        return {**os.environ, "PATH": cfg.dispatch.path}
    return None


def _match_systemic_signature(log_path: Path, *, rc: int) -> str | None:
    """Return the matching signature name, or None.

    rc is the worker's exit code; missing_binary requires rc==127 to avoid
    matching a `command not found` substring that shows up inside a benign
    traceback. The other signatures don't care about rc — auth/rate-limit
    errors surface as rc=1 from the SDK and rc=2 from a wrapped shell, both
    legitimate.
    """
    try:
        with open(log_path, "r", errors="replace") as fh:
            lines = fh.readlines()
    except (FileNotFoundError, OSError):
        return None
    tail = "".join(lines[-_SYSTEMIC_TAIL_LINES:])
    if rc == 127 and _MISSING_BINARY_RE.search(tail):
        return "missing_binary"
    if _RATE_LIMIT_RE.search(tail):
        return "rate_limit"
    if _AUTH_FAILURE_RE.search(tail):
        return "auth_failure"
    return None


def dispatch_for_tick(
    result: TickResult,
    cfg: ProjectConfig,
    plan_slug: str,
    state_file: Path,
) -> bool:
    """Spawn the configured worker command. Returns True on spawn, False on no-op."""
    if result.action != "dispatch" or not result.phase_id:
        return False

    cmd_tmpl = cfg.dispatch.command
    if not cmd_tmpl:
        _release_with_failure(
            state_file, result,
            reason="no dispatch.command in .orchestrator.json",
        )
        return False

    if cfg.dispatch.kind != "shell":
        raise ValueError(f"unknown dispatch kind: {cfg.dispatch.kind}")

    cmd = cmd_tmpl.format(
        plan_slug=shlex.quote(plan_slug),
        phase_id=shlex.quote(result.phase_id),
        token=shlex.quote(result.token or ""),
        project=shlex.quote(str(cfg.project_root)),
        state_file=shlex.quote(str(state_file)),
    )

    log_dir = state_file.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{result.phase_id}.{result.token}.log"

    # Worktree-bearing plans run with cwd pointing at the worktree dir;
    # main-repo plans keep cwd at project_root. The `{project}` template
    # substitution always resolves to project_root regardless — that's the
    # callback target, not the working directory.
    cwd = result.worktree["path"] if result.worktree else str(cfg.project_root)
    popen_kwargs: dict = dict(
        shell=True,
        cwd=cwd,
        start_new_session=True,
    )
    if (worker_env := build_worker_env(cfg)) is not None:
        popen_kwargs["env"] = worker_env

    with open(log_path, "ab") as log_fh:
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            **popen_kwargs,
        )

    try:
        rc = proc.wait(timeout=_FAST_FAIL_WAIT_SEC)
    except subprocess.TimeoutExpired:
        rc = None  # still running — the healthy case
    if rc is not None and rc != 0:
        signature = _match_systemic_signature(log_path, rc=rc)
        if signature is not None:
            _pause_for_systemic_failure(
                state_file, result, cfg,
                plan_slug=plan_slug, signature=signature, log_path=log_path,
            )
            print(
                f"dispatch: systemic-failure {signature} rc={rc}, log={log_path}",
                file=sys.stderr,
            )
            return False
        _release_with_failure(
            state_file, result,
            reason=f"worker exited rc={rc} within {_FAST_FAIL_WAIT_SEC}s "
                   f"(see {log_path})",
        )
        print(
            f"dispatch: fast-fail rc={rc}, log={log_path}",
            file=sys.stderr,
        )
        return False

    _stamp_pid(state_file, result, proc.pid, log_path)
    print(
        f"dispatch: spawned `{cmd}` pid={proc.pid} log={log_path}",
        file=sys.stderr,
    )
    return True


def dispatch_repair_worker(
    cfg: ProjectConfig,
    corrupt_path: Path,
    backup_path: Path,
    diagnosis: str,
    log_path: Path,
    *,
    timeout_sec: float = DEFAULT_REPAIR_TIMEOUT_SEC,
) -> int:
    """Spawn the configured repair_command and wait synchronously for it.

    Returns the worker's rc, or `REPAIR_RC_TIMEOUT` if we had to kill it.
    Caller is responsible for running `queue.validate_repair` against
    the corrupt_path bytes regardless of rc — a worker that ignores its
    prompt and writes garbage is what the validation exists to catch.

    Stays separate from `dispatch_for_tick` because the contracts differ:
    repair is synchronous + worker-style logs but no claim/token to stamp.
    """
    cmd_tmpl = cfg.dispatch.repair_command or ""
    if not cmd_tmpl:
        raise ValueError("dispatch_repair_worker called without repair_command")
    cmd = cmd_tmpl.format(
        corrupt_path=shlex.quote(str(corrupt_path)),
        backup_path=shlex.quote(str(backup_path)),
        diagnosis=shlex.quote(diagnosis),
        schema_json=shlex.quote(_REPAIR_SCHEMA_HINT),
        log_path=shlex.quote(str(log_path)),
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)

    popen_kwargs: dict = dict(
        shell=True,
        cwd=str(cfg.project_root),
        start_new_session=True,
    )
    if (worker_env := build_worker_env(cfg)) is not None:
        popen_kwargs["env"] = worker_env

    with open(log_path, "ab") as log_fh:
        proc = subprocess.Popen(
            cmd, stdout=log_fh, stderr=subprocess.STDOUT, **popen_kwargs,
        )

    try:
        return proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return REPAIR_RC_TIMEOUT


def _pause_for_systemic_failure(
    state_file: Path,
    result: TickResult,
    cfg: ProjectConfig,
    *,
    plan_slug: str,
    signature: str,
    log_path: Path,
) -> None:
    """Flip the plan to paused + emit EVENT_SYSTEMIC_FAILURE + halt-bypass ping.

    Reuses STATUS_PAUSED (no new constant) and KIND_HALTED (no new gate); the
    only new vocabulary is the event itself. `attempts_for_phase` subtracts
    the phase_started that this token produced, so the budget isn't burned.
    """
    try:
        with st.mutate(state_file) as data:
            st.append_event(
                data, st.EVENT_SYSTEMIC_FAILURE,
                phase=result.phase_id,
                token=result.token,
                signature=signature,
                log_path=str(log_path),
            )
            try:
                st.release_claim(
                    data,
                    expected_token=result.token,
                    expected_phase=result.phase_id,
                )
            except st.ClaimMismatch:
                # Concurrent operator action already swapped the claim;
                # don't clobber it. The event is still recorded.
                pass
            data["status"] = st.STATUS_PAUSED
    except _DISPATCH_FALLBACK_ERRORS as exc:
        print(
            f"dispatch: failed to record systemic_failure: {exc}",
            file=sys.stderr,
        )
        return
    notify.notify(
        cfg.notify, notify.KIND_HALTED,
        notify.render_systemic_failure(plan_slug, result.phase_id or "", signature),
    )


def _release_with_failure(state_file: Path, result: TickResult, *, reason: str) -> None:
    """Release the just-made claim + emit a dispatch_failed event."""
    try:
        with st.mutate(state_file) as data:
            st.append_event(
                data, st.EVENT_DISPATCH_FAILED,
                phase=result.phase_id, token=result.token, reason=reason,
            )
            try:
                st.release_claim(
                    data,
                    expected_token=result.token,
                    expected_phase=result.phase_id,
                )
            except st.ClaimMismatch:
                # Someone else already changed the claim — leave it alone.
                pass
    except _DISPATCH_FALLBACK_ERRORS as exc:
        print(f"dispatch: failed to record dispatch_failed: {exc}", file=sys.stderr)


def _stamp_pid(state_file: Path, result: TickResult, pid: int, log_path: Path) -> None:
    """Best-effort pid/log_path stamping on the active claim."""
    try:
        with st.mutate(state_file) as data:
            claim = data.get("current_claim") or {}
            if claim.get("claimed_by") == result.token:
                claim["pid"] = pid
                claim["log_path"] = str(log_path)
                data["current_claim"] = claim
    except _DISPATCH_FALLBACK_ERRORS as exc:
        print(f"dispatch: failed to stamp pid: {exc}", file=sys.stderr)
