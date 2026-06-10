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
import string
import subprocess
import sys
import uuid
from pathlib import Path

from . import coolant, notify
from . import state as st
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
_REPAIR_SCHEMA_HINT = json.dumps(
    {
        "schema_version": 1,
        "queue": [
            {
                "slug": "<plan-slug>",
                "added_at": "<iso8601-utc>",
                "added_by": "operator",
                "position_at_add": "tail|front",
            }
        ],
        "history": [
            {
                "slug": "<plan-slug>",
                "added_at": "<iso8601-utc>",
                "ended_at": "<iso8601-utc>",
                "outcome": "abandoned|removed|absorbed",
            }
        ],
    }
)

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
    r"(rate[\s_-]?limit|RateLimitError)",
    re.IGNORECASE,
)
_AUTH_FAILURE_RE = re.compile(
    r"(401\s+Unauthorized|AuthenticationError|invalid\s+api\s+key)",
    re.IGNORECASE,
)
_MISSING_BINARY_RE = re.compile(r"command not found", re.IGNORECASE)


_MODEL_FLAG = "--model"
_MODEL_FLAG_EQ = "--model="


def resolved_model(cmd_tmpl: str) -> str | None:
    """Return the `--model X` value from the dispatch template, or None.

    Stdlib-only — never reads settings.json or shells out. Malformed
    templates (unbalanced quotes) → None; a template clu can't parse
    is one the worker can't run either.
    """
    try:
        tokens = shlex.split(cmd_tmpl)
    except ValueError:
        return None
    for i, tok in enumerate(tokens):
        if tok == _MODEL_FLAG and i + 1 < len(tokens):
            return tokens[i + 1]
        if tok.startswith(_MODEL_FLAG_EQ):
            return tok[len(_MODEL_FLAG_EQ) :]
    return None


def build_worker_env(
    cfg: ProjectConfig,
    *,
    plan_slug: str | None = None,
    phase_id: str | None = None,
    token: str | None = None,
) -> dict[str, str] | None:
    """Return the env dict to pass to subprocess.Popen, or None to inherit.

    Merges (not replaces) os.environ when an override is configured — a bare
    {"PATH": ...} would strip HOME/USER and break `claude --print` in the
    worker (the #9 regression). Empty path == no override == inherit.

    When the claim kwargs are provided (phase dispatch), also injects
    CLU_PLAN / CLU_PHASE / CLU_TOKEN / CLU_PROJECT so processes inside the
    worker — specifically Claude Code hooks, which inherit the worker's
    env — know the claim identity. Worker-side `export` can't do this:
    env doesn't persist across Bash tool calls in headless `--print`
    sessions (#91). Repair workers pass no kwargs on purpose: they carry
    no claim or token, and the activity hook's empty-token short-circuit
    is the correct behavior for them. Cfg-only calls with no PATH
    override keep returning None (inherit) — cmd_doctor's
    "(source: inherited)" display depends on it.
    """
    inject = plan_slug is not None or phase_id is not None or token is not None
    if not cfg.dispatch.path and not inject:
        return None
    env = {**os.environ}
    if cfg.dispatch.path:
        env["PATH"] = cfg.dispatch.path
    if inject:
        env["CLU_PLAN"] = plan_slug or ""
        env["CLU_PHASE"] = phase_id or ""
        env["CLU_TOKEN"] = token or ""
        env["CLU_PROJECT"] = str(cfg.project_root)
    return env


def _match_systemic_signature(log_path: Path, *, rc: int) -> str | None:
    """Return the matching signature name, or None.

    rc is the worker's exit code; missing_binary requires rc==127 to avoid
    matching a `command not found` substring that shows up inside a benign
    traceback. The other signatures don't care about rc — auth/rate-limit
    errors surface as rc=1 from the SDK and rc=2 from a wrapped shell, both
    legitimate.
    """
    try:
        with open(log_path, errors="replace") as fh:
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


def _template_uses_session_id(cmd_tmpl: str) -> bool:
    """True iff the command has a real `{session_id}` format field.

    A substring test would misfire on escaped `{{session_id}}` (which
    `str.format` renders as the literal text and consumes no argument) — that
    would stamp a uuid the worker never receives. `Formatter().parse` reports
    escaped braces as literal text with no field name, so this distinguishes
    them.
    """
    try:
        return any(field == "session_id" for _, field, _, _ in string.Formatter().parse(cmd_tmpl))
    except ValueError:
        return False


def render_command(
    cmd_tmpl: str,
    *,
    plan_slug: str,
    phase_id: str,
    token: str,
    project: str,
    state_file: str,
    session_id: str,
) -> str:
    """Render a dispatch template — the single home of the placeholder set.

    Every value is shlex-quoted for the `shell=True` Popen. `cmd_doctor`'s
    dispatch-marker guard renders through this same helper, so a placeholder
    added here is automatically part of what the doctor check exercises —
    the two renders can't drift apart.
    """
    return cmd_tmpl.format(
        plan_slug=shlex.quote(plan_slug),
        phase_id=shlex.quote(phase_id),
        token=shlex.quote(token),
        project=shlex.quote(project),
        state_file=shlex.quote(state_file),
        session_id=shlex.quote(session_id),
    )


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
            state_file,
            result,
            reason="no dispatch.command in .orchestrator.json",
        )
        return False

    if cfg.dispatch.kind != "shell":
        raise ValueError(f"unknown dispatch kind: {cfg.dispatch.kind}")

    # Generate a session id ONLY when the command opts in via {session_id}
    # (e.g. `claude --session-id {session_id} ...`). Then the worker's
    # transcript filename is known here, so we stamp it on the claim and
    # `clu top` finds the transcript deterministically. Without the
    # placeholder, Claude Code picks its own id, so stamping ours would lie —
    # leave it unset and let `clu top` fall back to cwd-matching.
    session_id = str(uuid.uuid4()) if _template_uses_session_id(cmd_tmpl) else None
    cmd = render_command(
        cmd_tmpl,
        plan_slug=plan_slug,
        phase_id=result.phase_id,
        token=result.token or "",
        project=str(cfg.project_root),
        state_file=str(state_file),
        session_id=session_id or "",
    )

    log_dir = state_file.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{result.phase_id}.{result.token}.log"

    if result.worktree:
        _maybe_write_attempt_context(
            state_file,
            log_dir,
            plan_slug,
            result.phase_id,
            result.worktree,
        )

    # Worktree-bearing plans run with cwd pointing at the worktree dir;
    # main-repo plans keep cwd at project_root. The `{project}` template
    # substitution always resolves to project_root regardless — that's the
    # callback target, not the working directory.
    def _pause_for_missing(verb: str) -> bool:
        # verb distinguishes the stat-time miss ("missing") from the
        # Popen-time race ("vanished") in stderr forensics; everything
        # else funnels through one path so the two cases can't drift.
        _pause_for_missing_worktree(
            state_file,
            result,
            cfg,
            plan_slug=plan_slug,
            worktree_path=result.worktree["path"],
        )
        print(
            f"dispatch: worktree {verb} at {result.worktree['path']}, paused",
            file=sys.stderr,
        )
        return False

    if result.worktree and not worktree_alive(Path(result.worktree["path"])):
        return _pause_for_missing("missing")

    cwd = result.worktree["path"] if result.worktree else str(cfg.project_root)
    popen_kwargs: dict = dict(
        shell=True,
        cwd=cwd,
        start_new_session=True,
    )
    worker_env = build_worker_env(
        cfg,
        plan_slug=plan_slug,
        phase_id=result.phase_id,
        token=result.token,
    )
    if worker_env is not None:
        popen_kwargs["env"] = worker_env

    try:
        with open(log_path, "ab") as log_fh:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                **popen_kwargs,
            )
    except FileNotFoundError as exc:
        # Pre-Popen stat passed but the dir vanished in the gap. Operator
        # gets one explanation, not two competing failure signals.
        if result.worktree:
            return _pause_for_missing("vanished")
        # Non-worktree case: usually the log dir vanished or the shell
        # binary is missing. Pre-clu-worktrees, the bare `raise` here
        # propagated up and crashed the whole `cmd_tick_all` loop —
        # taking out every other plan's tick along with it. Funnel into
        # the same release-and-record path as a fast-fail rc so one
        # broken plan can't poison the cadence.
        _release_with_failure(
            state_file,
            result,
            reason=f"Popen FileNotFoundError: {exc}",
        )
        print(
            f"dispatch: Popen FileNotFoundError: {exc}, log={log_path}",
            file=sys.stderr,
        )
        return False

    try:
        rc = proc.wait(timeout=_FAST_FAIL_WAIT_SEC)
    except subprocess.TimeoutExpired:
        rc = None  # still running — the healthy case
    if rc is not None and rc != 0:
        signature = _match_systemic_signature(log_path, rc=rc)
        if signature is not None:
            _pause_for_systemic_failure(
                state_file,
                result,
                cfg,
                plan_slug=plan_slug,
                signature=signature,
                log_path=log_path,
            )
            print(
                f"dispatch: systemic-failure {signature} rc={rc}, log={log_path}",
                file=sys.stderr,
            )
            return False
        _release_with_failure(
            state_file,
            result,
            reason=f"worker exited rc={rc} within {_FAST_FAIL_WAIT_SEC}s (see {log_path})",
        )
        print(
            f"dispatch: fast-fail rc={rc}, log={log_path}",
            file=sys.stderr,
        )
        return False

    _stamp_pid(state_file, result, proc.pid, log_path, session_id)
    if cfg.coolant.enabled:
        coolant.emit_start(
            session_id=result.token or "",
            agent_id=coolant.format_agent_id(plan_slug, result.phase_id),
            agent_type=coolant.AGENT_TYPE,
            script_override=cfg.coolant.script_dir,
        )
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
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            **popen_kwargs,
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


_PREV_ATTEMPT_TIMEOUT_SEC = 5

# Map of EVENT_* type → human-readable phrase for the prior-attempt
# block. Keep narrow — speculative reasons read as confident lies.
_TERMINATION_REASONS = {
    st.EVENT_LEASE_EXPIRED: "lease expired (worker didn't callback in time)",
    st.EVENT_CLAIM_FORCE_RELEASED: "operator force-released the claim",
    st.EVENT_PHASE_BLOCKED: "worker blocked on a question",
    st.EVENT_DISPATCH_FAILED: "previous dispatch failed",
    st.EVENT_SYSTEMIC_FAILURE: "systemic failure (rate-limit, auth, missing binary)",
}


def _run_git_safe(cwd: str, args: list[str]) -> str | None:
    """Return stdout on rc=0; None on any failure (timeout, non-zero, missing git).

    Used by the prior-attempt context block — degrading to None lets the
    caller emit "unavailable" rather than fail dispatch outright when a
    worktree is in a weird state.
    """
    try:
        result = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=_PREV_ATTEMPT_TIMEOUT_SEC,
        )
        return result.stdout if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def _last_termination_reason(state_data: dict, phase_id: str) -> str | None:
    for evt in reversed(state_data.get("events", [])):
        if evt.get("phase") != phase_id:
            continue
        reason = _TERMINATION_REASONS.get(evt.get("type"))
        if reason:
            return reason
    return None


def _prev_attempt_context(
    worktree_path: str,
    base_ref: str,
    phase_id: str,
    attempt: int,
    termination_reason: str | None,
) -> str:
    """Build a markdown block describing prior-attempt worktree state.

    Three git probes (status, diff stat, log against base). Each degrades
    gracefully to an "unavailable" line on failure — dispatch must never
    fail because a worktree probe timed out.
    """
    parts = [f"## Previous attempt state (attempt {attempt})"]
    if termination_reason:
        parts.append(f"Prior attempt ended: {termination_reason}")

    status = _run_git_safe(worktree_path, ["status", "--short"])
    if status is None:
        parts.append("(git status unavailable)")
    elif status.strip() == "":
        parts.append("Worktree is clean — no uncommitted changes from prior attempts.")
    else:
        parts.append("### Uncommitted changes\n```\n" + status.rstrip() + "\n```")

    diff_stat = _run_git_safe(worktree_path, ["diff", "--stat", "HEAD"])
    if diff_stat and diff_stat.strip():
        parts.append("### Diff stat\n```\n" + diff_stat.rstrip() + "\n```")

    log = _run_git_safe(worktree_path, ["log", "--oneline", "HEAD", f"^{base_ref}"])
    if log is None:
        parts.append("(commit log unavailable)")
    elif log.strip() == "":
        parts.append("No commits landed by prior attempts.")
    else:
        parts.append("### Commits landed by prior attempts\n```\n" + log.rstrip() + "\n```")

    parts.append(
        f"You may keep, continue, or reset these edits — decide based on "
        f"whether they align with the sub-plan. Reset is "
        f"`git reset --hard {base_ref} && git clean -fd`. Otherwise inspect "
        f"and continue."
    )
    return "\n\n".join(parts) + "\n"


def _context_path(log_dir: Path, plan_slug: str, phase_id: str) -> Path:
    return log_dir / f"attempt-context.{plan_slug}.{phase_id}.md"


def _write_prev_attempt_context(
    log_dir: Path,
    plan_slug: str,
    phase_id: str,
    content: str,
) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = _context_path(log_dir, plan_slug, phase_id)
    path.write_text(content)
    return path


def _delete_stale_attempt_context(
    log_dir: Path,
    plan_slug: str,
    phase_id: str,
) -> None:
    path = _context_path(log_dir, plan_slug, phase_id)
    if path.exists():
        path.unlink()


def _maybe_write_attempt_context(
    state_file: Path,
    log_dir: Path,
    plan_slug: str,
    phase_id: str,
    worktree: dict,
) -> None:
    """Write or delete the prior-attempt context sidecar based on attempt count."""
    try:
        state_data = json.loads(state_file.read_text())
    except _DISPATCH_FALLBACK_ERRORS:
        return
    claim = state_data.get("current_claim") or {}
    attempts = int(claim.get("attempts", 1))
    base_ref = worktree.get("base_ref")
    if attempts <= 1 or not base_ref:
        _delete_stale_attempt_context(log_dir, plan_slug, phase_id)
        return
    content = _prev_attempt_context(
        worktree_path=worktree["path"],
        base_ref=base_ref,
        phase_id=phase_id,
        attempt=attempts,
        termination_reason=_last_termination_reason(state_data, phase_id),
    )
    _write_prev_attempt_context(log_dir, plan_slug, phase_id, content)


def worktree_alive(path: Path) -> bool:
    """True iff `path` exists AND `git -C path rev-parse --git-dir` succeeds.

    Catches both the "operator deleted the dir" and "operator ran
    `git worktree prune`" failure modes. Plain `path.exists()` would miss
    the prune case — the dir is still there but git won't operate on it.
    """
    if not path.exists():
        return False
    check = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
    )
    return check.returncode == 0


def _pause_and_halt(
    state_file: Path,
    result: TickResult,
    cfg: ProjectConfig,
    *,
    event_type: str,
    event_kwargs: dict,
    notify_body: str,
    log_label: str,
) -> None:
    """Shared dispatch-time pause shape: release claim, flip PAUSED, halt-bypass ping.

    Every dispatch-time fatal — systemic failure, missing worktree, future
    additions — does the same dance: append the failure event, release the
    just-made claim (so `attempts_for_phase` doesn't burn a budget on a
    failure that wasn't the worker's fault), set status=PAUSED, then notify
    via KIND_HALTED so the iMessage bypasses quiet hours. Callers pick the
    event constant + kwargs and the rendered iMessage body.
    """
    try:
        with st.mutate(state_file) as data:
            st.append_event(
                data,
                event_type,
                phase=result.phase_id,
                token=result.token,
                **event_kwargs,
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
            f"dispatch: failed to record {log_label}: {exc}",
            file=sys.stderr,
        )
        return
    notify.notify(cfg.notify, notify.KIND_HALTED, notify_body)


def _pause_for_missing_worktree(
    state_file: Path,
    result: TickResult,
    cfg: ProjectConfig,
    *,
    plan_slug: str,
    worktree_path: str,
) -> None:
    _pause_and_halt(
        state_file,
        result,
        cfg,
        event_type=st.EVENT_WORKTREE_MISSING,
        event_kwargs={"worktree_path": worktree_path},
        notify_body=notify.render_worktree_missing(plan_slug, worktree_path),
        log_label="worktree_missing",
    )


def _pause_for_systemic_failure(
    state_file: Path,
    result: TickResult,
    cfg: ProjectConfig,
    *,
    plan_slug: str,
    signature: str,
    log_path: Path,
) -> None:
    _pause_and_halt(
        state_file,
        result,
        cfg,
        event_type=st.EVENT_SYSTEMIC_FAILURE,
        event_kwargs={"signature": signature, "log_path": str(log_path)},
        notify_body=notify.render_systemic_failure(
            plan_slug,
            result.phase_id or "",
            signature,
        ),
        log_label="systemic_failure",
    )


def _release_with_failure(state_file: Path, result: TickResult, *, reason: str) -> None:
    """Release the just-made claim + emit a dispatch_failed event."""
    try:
        with st.mutate(state_file) as data:
            st.append_event(
                data,
                st.EVENT_DISPATCH_FAILED,
                phase=result.phase_id,
                token=result.token,
                reason=reason,
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


def _stamp_pid(
    state_file: Path,
    result: TickResult,
    pid: int,
    log_path: Path,
    session_id: str | None = None,
) -> None:
    """Best-effort pid/log_path/session_id stamping on the active claim."""
    try:
        with st.mutate(state_file) as data:
            claim = data.get("current_claim") or {}
            if claim.get("claimed_by") == result.token:
                claim["pid"] = pid
                # Worker spawned start_new_session=True ⇒ it leads its own
                # process group, pgid == pid. Record it so cleanup reapers can
                # killpg the whole group (worker + heartbeat loop) — #75.
                claim["pgid"] = pid
                claim["log_path"] = str(log_path)
                if session_id is not None:
                    # Deterministic transcript filename for `clu top` (#session-id).
                    claim["session_id"] = session_id
                data["current_claim"] = claim
    except _DISPATCH_FALLBACK_ERRORS as exc:
        print(f"dispatch: failed to stamp pid: {exc}", file=sys.stderr)
