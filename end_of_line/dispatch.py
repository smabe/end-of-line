"""Spawn worker sessions for dispatched phases.

Fire-and-forget but observable: each worker's stderr/stdout streams to a
per-token log file, and the dispatched pid is stamped on the claim. A
fast-fail check (0.5s after spawn) catches shell exit-127 / immediate
crashes and releases the claim so the next tick can retry instead of
waiting 30 minutes for the lease to expire silently.
"""

from __future__ import annotations

import os
import re
import shlex
import string
import subprocess
import sys
import uuid
from pathlib import Path

from . import coolant, db, notify, plan_store, quota
from . import state as st
from .config import ProjectConfig
from .supervisor import TickResult

# How long to wait for a fast-fail before declaring the worker healthy.
# `proc.wait(timeout=)` returns immediately if the worker exited sooner —
# we only pay this latency for the genuinely-still-running case. Plenty
# of headroom for fork+exec; longer than this and we'd be re-implementing
# the lease.
_FAST_FAIL_WAIT_SEC = 0.5

# Exceptions that are recoverable in dispatch fallback paths.
# What a dispatch-time state write degrades on rather than crashing the tick
# that called it. `ValueError` rather than `json.JSONDecodeError`: a store that
# cannot be read used to be a JSON parse error (a ValueError) and now arrives as
# one from the store's own translation, so the narrower name would let it
# through. `db.DEGRADABLE_ERRORS` is the rest of the database's vocabulary —
# `sqlite3.Error` for a broken store and `DbBusy` for one held past the budget,
# neither of which is an `OSError`; without them a busy project (any other
# plan's tick holds the same write lock now) would take the whole dispatch down
# with a traceback where a stderr line belongs.
_DISPATCH_FALLBACK_ERRORS = (
    *db.DEGRADABLE_ERRORS,
    ValueError,
    st.SchemaVersionMismatch,
)

# Hard-coded signature list. Grows via PR only; no config field. Order
# matters — first match wins, so put the most specific (rc-gated) one
# first. The log tail is read through quota.read_log_tail (the shared
# 50-line discipline). Quota signatures live in quota.py and are checked
# BEFORE this table on fast-fail.
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

# Absolute path to the PTY shim that phase workers are wrapped in. Invoked by
# FILE PATH, not `-m end_of_line._pty_spawn_shim`: the worker runs with cwd at
# the worktree (for its git ops), where the package isn't importable unless clu
# happens to be pip-installed into `sys.executable`. The shim is stdlib-only
# and self-contained, so running it as a standalone script is cwd-independent
# and equivalent — the slug-bearing cmd string still rides in argv for the
# cmdline marker. See end_of_line/_pty_spawn_shim.py.
_PTY_SHIM_PATH = str(Path(__file__).resolve().parent / "_pty_spawn_shim.py")


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
    sessions (#91). Cfg-only calls with no PATH override keep returning
    None (inherit) — cmd_doctor's "(source: inherited)" display depends
    on it.

    Also raises the Bash-tool timeout ceiling (BASH_MAX_TIMEOUT_MS) so a
    long test gate runs in the foreground rather than being auto-
    backgrounded past end-of-turn — the idiom that killed the incident
    worker (#106). Three deliberate choices:
      - setdefault, not assignment: an operator who already exports
        BASH_MAX_TIMEOUT_MS for their own host tuning wins. This departs
        from the unconditional PATH / CLU_* assignments above, which are
        clu's own claim identity and not the operator's to override; the
        Bash ceiling is a host knob clu shouldn't clobber.
      - INSIDE the inject branch, never on the unconditional path. The
        None-vs-dict return is load-bearing for cmd_doctor, whose
        "(source: inherited)" line reads it; setting the ceiling
        unconditionally would flip that silently.
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
        env.setdefault("BASH_MAX_TIMEOUT_MS", str(cfg.dispatch.bash_max_timeout_ms))
    return env


def _match_systemic_signature(log_path: Path, *, rc: int) -> str | None:
    """Return the matching signature name, or None.

    rc is the worker's exit code; missing_binary requires rc==127 to avoid
    matching a `command not found` substring that shows up inside a benign
    traceback. The other signatures don't care about rc — auth/rate-limit
    errors surface as rc=1 from the SDK and rc=2 from a wrapped shell, both
    legitimate.
    """
    tail = quota.read_log_tail(log_path)
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
        worktree = result.worktree
        assert worktree is not None  # both call sites gate on result.worktree
        _pause_for_missing_worktree(
            state_file,
            result,
            cfg,
            plan_slug=plan_slug,
            worktree_path=worktree["path"],
        )
        print(
            f"dispatch: worktree {verb} at {worktree['path']}, paused",
            file=sys.stderr,
        )
        return False

    if result.worktree and not worktree_alive(Path(result.worktree["path"])):
        return _pause_for_missing("missing")

    cwd = result.worktree["path"] if result.worktree else str(cfg.project_root)
    # Route phase workers through the PTY shim: `claude --print` block-buffers
    # stdout (~4-8KB) when it isn't a tty, so a wedged worker leaves a 0-byte
    # log exactly when the post-mortem needs it. The shim allocates a pty so
    # Node line-buffers into the log in real time. It takes the rendered
    # command STRING as one argv element and runs it through `sh -c` itself —
    # so dropping shell=True here preserves command/quoting semantics, and the
    # plan-slug cmdline marker still rides in the shim's argv. The shim becomes
    # claim.pid and the real worker is its CHILD, so every watchdog that reads
    # claim.pid must walk the tree to see the worker at all — the shim itself
    # only copies bytes. Repair workers stay on the direct shell path below —
    # short-lived, not wedge-prone. See end_of_line/_pty_spawn_shim.py.
    shim_argv = [sys.executable, _PTY_SHIM_PATH, "--", cmd]
    popen_kwargs: dict = dict(
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
                shim_argv,
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
        # Quota check BEFORE the systemic table (#94) — the regexes don't
        # overlap today, but order is the contract if a future message
        # contains both wordings.
        if _record_quota_fast_fail(state_file, result, log_path, cfg, plan_slug):
            print(
                f"dispatch: quota-death rc={rc}, log={log_path}",
                file=sys.stderr,
            )
            return False
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


_PREV_ATTEMPT_TIMEOUT_SEC = 5

# Map of EVENT_* type → human-readable phrase for the prior-attempt
# block. Keep narrow — speculative reasons read as confident lies.
_TERMINATION_REASONS = {
    st.EVENT_LEASE_EXPIRED: "lease expired (worker didn't callback in time)",
    st.EVENT_CLAIM_FORCE_RELEASED: "operator force-released the claim",
    st.EVENT_PHASE_BLOCKED: "worker blocked on a question",
    st.EVENT_DISPATCH_FAILED: "previous dispatch failed",
    st.EVENT_SYSTEMIC_FAILURE: "systemic failure (rate-limit, auth, missing binary)",
    st.EVENT_PHASE_WORKER_DEAD_REPORTED: (
        "worker process died mid-phase (heartbeat daemon reported it) — the "
        "likely cause is ending the turn to wait on a background task or Monitor, "
        "which `claude --print` never resumes; run long steps in the foreground"
    ),
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
        state_data = plan_store.snapshot(*st.key_for(state_file))
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
        _pause_recording(
            state_file,
            result,
            {
                "ts": st.utcnow(),
                "type": event_type,
                "phase": result.phase_id,
                "token": result.token,
                **event_kwargs,
            },
        )
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


def _record_quota_fast_fail(
    state_file: Path,
    result: TickResult,
    log_path: Path,
    cfg: ProjectConfig,
    plan_slug: str,
) -> bool:
    """Quota classification on a fast-failed worker; True iff the path was taken.

    On match: quota events + pause file via quota.record_quota_death, then
    release the just-made claim WITHOUT a dispatch_failed event — the quota
    events are the record, and the attempt is forgiven via EVENT_QUOTA_DEATH.
    Unlike `_pause_and_halt`, plan status never flips: the quota pause is a
    project-level dispatch gate (phase `gate`), not a plan-level halt. The
    operator-facing KIND_QUOTA_PAUSED/STUCK ping fires after the state write,
    mirroring `_pause_and_halt`'s post-mutate notify (phase notify-docs).
    """
    match = quota.classify_log_tail(log_path)
    if match is None:
        return False
    paused_until = None
    try:
        # `record_quota_death` writes the project's pause FILE and builds the
        # two plan events. It runs outside the transaction — the pause file has
        # its own lock, and nesting one store's lock inside another's is the
        # shape this migration exists to remove — and the events it produced
        # are then written with the release in one transaction.
        harvest: dict = {"events": []}
        paused_until = quota.record_quota_death(
            harvest,
            match,
            phase_id=result.phase_id or "",
            token=result.token,
            orchestrator_dir=state_file.parent,
        )
        _release_recording(state_file, result, harvest["events"])
    except _DISPATCH_FALLBACK_ERRORS as exc:
        print(f"dispatch: failed to record quota death: {exc}", file=sys.stderr)
        return False
    kind, body = notify.quota_pause_notification(
        plan_slug,
        match.line,
        paused_until,
    )
    notify.notify(cfg.notify, kind, body)
    return True


def _release_recording(state_file: Path, result: TickResult, events: list[dict]) -> None:
    """Record `events` and release the just-made claim, in one transaction.

    The claim is released only if it is still ours — but the events are
    recorded either way. That asymmetry predates the store and is deliberate:
    a concurrent operator action that swapped the claim must not have it
    yanked out from under the new owner, while the failure that brought us
    here still belongs in the log. The compare-and-set makes "still ours" the
    database's answer rather than a re-read's; the second transaction runs only
    on a path where a dispatch has already failed.
    """
    orch_dir, slug = st.key_for(state_file)
    try:
        plan_store.op_release_claim(
            orch_dir,
            slug,
            token=result.token,
            phase=result.phase_id,
            events=events,
        )
    except st.ClaimMismatch:
        plan_store.op_append_events(orch_dir, slug, events)


def _pause_recording(state_file: Path, result: TickResult, event: dict) -> None:
    """`_release_recording` plus the status flip — the dispatch-time pause.

    Status, event and release commit together: a plan left at `running` with a
    claim that outlived its failed dispatch is one bug in two halves.
    """
    orch_dir, slug = st.key_for(state_file)
    try:
        plan_store.op_set_status(
            orch_dir,
            slug,
            status=st.STATUS_PAUSED,
            event=event,
            release_token=result.token,
            release_phase=result.phase_id,
        )
    except st.ClaimMismatch:
        plan_store.op_set_status(orch_dir, slug, status=st.STATUS_PAUSED, event=event)


def _release_with_failure(state_file: Path, result: TickResult, *, reason: str) -> None:
    """Release the just-made claim + emit a dispatch_failed event."""
    event = {
        "ts": st.utcnow(),
        "type": st.EVENT_DISPATCH_FAILED,
        "phase": result.phase_id,
        "token": result.token,
        "reason": reason,
    }
    try:
        _release_recording(state_file, result, [event])
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
    if result.token is None:
        # Nothing to compare-and-set against. A claimless dispatch result never
        # reaches here (the claim is what produced the token), and stamping a
        # pid onto whatever claim happens to be live would be worse than not
        # stamping at all.
        return
    fields: dict = {
        "pid": pid,
        # Worker spawned start_new_session=True ⇒ it leads its own process
        # group, pgid == pid. Record it so cleanup reapers can killpg the whole
        # group (worker + heartbeat loop) — #75.
        "pgid": pid,
        "log_path": str(log_path),
    }
    if session_id is not None:
        # Deterministic transcript filename for `clu top` (#session-id).
        fields["session_id"] = session_id
    try:
        plan_store.op_stamp_claim_fields(
            *st.key_for(state_file),
            token=result.token,
            fields=fields,
        )
    except st.ClaimMismatch:
        # The claim moved between Popen and here (operator release, a racing
        # tick). Stamping it would attach this worker's pid to somebody else's
        # claim, so the stamp is simply dropped — as it always was.
        pass
    except _DISPATCH_FALLBACK_ERRORS as exc:
        print(f"dispatch: failed to stamp pid: {exc}", file=sys.stderr)
