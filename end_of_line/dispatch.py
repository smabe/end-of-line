"""Spawn worker sessions for dispatched phases.

The worker is a fresh process (typically a Claude session) that picks up the
claim, reads the phase plan, executes, and reports back via `eol complete`
or `eol block`. Dispatch is fire-and-forget — failures are recovered via
lease expiry, not by waiting.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from .config import ProjectConfig
from .supervisor import TickResult


def dispatch_for_tick(
    result: TickResult,
    cfg: ProjectConfig,
    plan_slug: str,
    state_file: Path,
) -> bool:
    """Spawn the configured worker command. Returns True on spawn, False on no-op.

    Template substitutions are shell-escaped — plan_slug and phase_id come
    from filesystem state and parsed markdown respectively, so they should be
    treated as untrusted for shell injection purposes.
    """
    if result.action != "dispatch" or not result.phase_id:
        return False

    cmd_tmpl = cfg.dispatch.command
    if not cmd_tmpl:
        print(
            f"dispatch: no command configured — would dispatch "
            f"phase={result.phase_id} token={result.token}",
            file=sys.stderr,
        )
        return False

    cmd = cmd_tmpl.format(
        plan_slug=shlex.quote(plan_slug),
        phase_id=shlex.quote(result.phase_id),
        token=shlex.quote(result.token or ""),
        project=shlex.quote(str(cfg.project_root)),
        state_file=shlex.quote(str(state_file)),
    )
    if cfg.dispatch.kind != "shell":
        raise ValueError(f"unknown dispatch kind: {cfg.dispatch.kind}")
    subprocess.Popen(
        cmd,
        shell=True,
        cwd=str(cfg.project_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"dispatch: spawned `{cmd}`", file=sys.stderr)
    return True
