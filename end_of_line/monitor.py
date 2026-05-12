"""Background-monitoring marker file.

A successful `/clu-monitor` invocation writes a marker at
`$XDG_CONFIG_HOME/clu/monitor.json` (default `~/.config/clu/monitor.json`)
so subsequent invocations are idempotent and clu CLI hints can suppress
themselves when monitoring is already scheduled. Account-wide, not
per-project — one /schedule routine watches every plan on the host.

Tolerant by design: missing file, corrupt JSON, and schema mismatch
all surface as `None` / `False` so callers can branch on a single
"do we need to schedule?" predicate. The pattern mirrors
`registry.load_entry_state` — the marker is advisory, never load-bearing.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import state as st

SCHEMA_VERSION = 1


def marker_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "clu" / "monitor.json"


def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION}


def load_marker(path: Path | None = None) -> dict | None:
    """Return the marker dict, or None on any failure mode."""
    path = path or marker_path()
    if not path.exists():
        return None
    try:
        return st.load(path, expected_version=SCHEMA_VERSION)
    except (OSError, ValueError, st.SchemaVersionMismatch):
        return None


def is_scheduled(path: Path | None = None) -> bool:
    return load_marker(path) is not None


def record_scheduled(
    schedule_id: str, cadence: str, *, path: Path | None = None,
) -> None:
    path = path or marker_path()
    # Overwrite-on-mismatch: if a stale marker has a different schema_version,
    # locked_json would refuse to load it. Drop it so re-recording works after
    # a clu upgrade — the marker is advisory, no information loss.
    if path.exists() and load_marker(path) is None:
        path.unlink()
    with st.locked_json(
        path, expected_version=SCHEMA_VERSION, empty=_empty,
    ) as data:
        data["scheduled_at"] = st.utcnow()
        data["schedule_id"] = schedule_id
        data["cadence"] = cadence


def clear_marker(path: Path | None = None) -> None:
    path = path or marker_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
