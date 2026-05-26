"""Coolant lifecycle event emission — fire-and-forget shell-out.

Coolant (https://github.com/todd-w-shaffer/coolant) is a Claude Code plugin
that meters parallel-agent thermal load. Its `SubagentStart` / `SubagentStop`
hooks only fire for in-session Task-tool subagents — clu workers are
top-level `claude --print` invocations, so coolant can't see them. We shell
out to its `agent-start.sh` / `agent-stop.sh` scripts directly at our own
dispatch + reap sites to surface workers in coolant's counter and gate.sh's
parallel-mode formula.

Contract (verified against coolant `scripts/common.sh`):
  - JSON on stdin: `{"session_id": str, "agent_id": str, "agent_type": str}`.
  - Coolant's regex accepts empty strings silently — we validate non-empty
    here at the boundary instead of polluting its events log.
  - Scripts emit `{"systemMessage": ...}` JSON on stdout when parallel mode
    auto-engages. We redirect stdout + stderr to DEVNULL so it can't leak
    into clu's output stream.
  - Fire-and-forget: 2s timeout, check=False, swallow TimeoutExpired /
    OSError. Coolant's own counter floors at 0 so a leaked +1 doesn't
    compound.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_ENV_OVERRIDE = "CLU_COOLANT_SCRIPT_DIR"
_PLUGIN_AUTHOR = "todd-w-shaffer"
_PLUGIN_NAME = "coolant"
_SCRIPT_START = "agent-start.sh"
_SCRIPT_STOP = "agent-stop.sh"
_SUBPROCESS_TIMEOUT = 2

# Coolant's `agent_type` field is opaque — we tag everything clu spawns
# with the same value so coolant can distinguish clu workers from
# operator-Task subagents in its event stream if it ever wants to.
AGENT_TYPE = "clu-worker"


def format_agent_id(plan_slug: str, phase_id: str) -> str:
    """Compose the coolant `agent_id` for a clu worker phase."""
    return f"clu-{plan_slug}-{phase_id}"


def emit_start(
    *,
    session_id: str,
    agent_id: str,
    agent_type: str,
    script_override: str | None = None,
) -> None:
    """Emit a SubagentStart-equivalent event to coolant. Never raises."""
    _emit(
        script_name=_SCRIPT_START,
        session_id=session_id,
        agent_id=agent_id,
        agent_type=agent_type,
        script_override=script_override,
    )


def emit_stop(
    *,
    session_id: str,
    agent_id: str,
    agent_type: str,
    script_override: str | None = None,
) -> None:
    """Emit a SubagentStop-equivalent event to coolant. Never raises."""
    _emit(
        script_name=_SCRIPT_STOP,
        session_id=session_id,
        agent_id=agent_id,
        agent_type=agent_type,
        script_override=script_override,
    )


def resolve_script_dir(override: str | None = None) -> Path | None:
    """Locate coolant's `scripts/` directory.

    Precedence: caller override → `CLU_COOLANT_SCRIPT_DIR` env → marketplace
    cache glob. Returns None if nothing resolves to an existing directory.
    """
    for candidate in (override, os.environ.get(_ENV_OVERRIDE)):
        if candidate:
            path = Path(candidate)
            if path.is_dir():
                return path
    return _marketplace_glob()


def _emit(
    *,
    script_name: str,
    session_id: str,
    agent_id: str,
    agent_type: str,
    script_override: str | None = None,
) -> None:
    if not session_id or not agent_id:
        # Empty fields would silently pollute coolant's JSONL events log;
        # short-circuit rather than emit a degraded record.
        return
    script_dir = resolve_script_dir(override=script_override)
    if script_dir is None:
        return
    payload = json.dumps(
        {
            "session_id": session_id,
            "agent_id": agent_id,
            "agent_type": agent_type,
        }
    )
    try:
        subprocess.run(
            [str(script_dir / script_name)],
            input=payload,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        # Fire-and-forget. Coolant's mutex + floor-to-0 counter means a
        # missed call costs us at most one off-by-one in agent census;
        # propagating the error would break dispatch / reap entirely.
        return


def _marketplace_glob() -> Path | None:
    """Find the newest-installed coolant `scripts/` dir under the plugin cache.

    Layout: `<cache_root>/todd-w-shaffer/coolant/<version>/scripts/`.
    Picks the highest version by lexical sort (works for semver-style tags).
    """
    cache_root = _plugin_cache_root()
    plugin_root = cache_root / _PLUGIN_AUTHOR / _PLUGIN_NAME
    if not plugin_root.is_dir():
        return None
    candidates = [
        p / "scripts" for p in plugin_root.iterdir() if p.is_dir() and (p / "scripts").is_dir()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.parent.name)


def _plugin_cache_root() -> Path:
    """`~/.claude/plugins/cache` — Claude Code's plugin cache root."""
    return Path.home() / ".claude" / "plugins" / "cache"
