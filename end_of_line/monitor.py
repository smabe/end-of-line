"""Background-monitoring marker file.

A successful `clu install-hook` writes a marker at
`$XDG_CONFIG_HOME/clu/monitor.json` (default `~/.config/clu/monitor.json`)
so subsequent invocations are idempotent and clu CLI hints can suppress
themselves when monitoring is already wired up. Account-wide, not
per-project — one hook watches every plan on the host.

Schema v2 (current): {schema_version: 2, hook_installed_at, hook_path,
settings_json_path}. Written by `clu install-hook` / `cmd_install_hook`.

Schema v1 (legacy `/schedule` install — the broken pre-#20 mechanism):
{schema_version: 1, schedule_id, cadence, scheduled_at}. v1 markers are
treated as "needs reinstall" by `is_scheduled` and `load_marker` — both
return None/False so the CLI hint fires and `/clu-monitor` re-runs the
install cleanly.

Tolerant by design: missing file, corrupt JSON, schema mismatch, and v1
markers all surface as `None` / `False` so callers can branch on a
single "do we need to install?" predicate. The marker is advisory,
never load-bearing.
"""

from __future__ import annotations

from pathlib import Path

from . import state as st
from ._xdg_guard import assert_xdg_safe, clu_config_dir

SCHEMA_VERSION = 2


def marker_path() -> Path:
    path = clu_config_dir() / "monitor.json"
    assert_xdg_safe(path)
    return path


def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION}


def load_marker(path: Path | None = None) -> dict | None:
    """Return the marker dict on a current-schema match; None otherwise.

    A v1 marker (legacy `/schedule` install) returns None so callers
    treat the host as un-monitored and re-run the hook install.
    """
    path = path or marker_path()
    if not path.exists():
        return None
    try:
        return st.load(path, expected_version=SCHEMA_VERSION)
    except (OSError, ValueError, st.SchemaVersionMismatch):
        return None


def is_scheduled(path: Path | None = None) -> bool:
    return load_marker(path) is not None


def record_hook_installed(
    hook_path: str,
    settings_json_path: str,
    *,
    path: Path | None = None,
) -> None:
    """Stamp the v2 marker. Atomically overwrites any prior v1 marker.

    `path` parameter is for tests; production uses the default
    XDG-derived `marker_path()`.
    """
    path = path or marker_path()
    # Overwrite-on-mismatch: a stale v1 marker (or any schema mismatch)
    # would make locked_json's load refuse. Drop it so the v2 write
    # succeeds atomically. The marker carries no data we'd lose.
    if path.exists() and load_marker(path) is None:
        path.unlink()
    with st.locked_json(
        path,
        expected_version=SCHEMA_VERSION,
        empty=_empty,
    ) as data:
        data["hook_installed_at"] = st.utcnow()
        data["hook_path"] = hook_path
        data["settings_json_path"] = settings_json_path


def record_session_start_installed(
    session_start_hook_path: str,
    *,
    path: Path | None = None,
) -> None:
    """Stamp the SessionStart hook path onto the existing v2 marker (#70).

    Additive — does not bump schema_version. Operators running
    `clu install-hook --session-start` get both this field and the
    existing `hook_path` field populated. The marker remains v2-schema
    so older code reading the marker doesn't refuse.
    """
    path = path or marker_path()
    if path.exists() and load_marker(path) is None:
        path.unlink()
    with st.locked_json(
        path,
        expected_version=SCHEMA_VERSION,
        empty=_empty,
    ) as data:
        data["session_start_hook_path"] = session_start_hook_path
        # Stamp install-time too, so the operator can audit when the
        # SessionStart hook was added separately from UserPromptSubmit.
        data["session_start_installed_at"] = st.utcnow()


def clear_marker(path: Path | None = None) -> None:
    path = path or marker_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
