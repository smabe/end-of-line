"""Spawn worker sessions for dispatched phases.

Fire-and-forget but observable: each worker's stderr/stdout streams to a
per-token log file, and the dispatched pid is stamped on the claim. A
fast-fail check (0.5s after spawn) catches shell exit-127 / immediate
crashes and releases the claim so the next tick can retry instead of
waiting 30 minutes for the lease to expire silently.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import state as st
from .config import ProjectConfig
from .supervisor import TickResult

# How long to wait before declaring a fast-fail. Long enough for shell
# resolution + exec; short enough not to noticeably slow ticks.
_FAST_FAIL_WAIT_SEC = 0.5


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

    log_fh = open(log_path, "ab")
    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            cwd=str(cfg.project_root),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_fh.close()

    time.sleep(_FAST_FAIL_WAIT_SEC)
    rc = proc.poll()
    if rc is not None and rc != 0:
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
    except Exception as exc:
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
    except Exception as exc:
        print(f"dispatch: failed to stamp pid: {exc}", file=sys.stderr)
