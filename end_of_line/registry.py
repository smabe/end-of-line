"""Host-level registry of known (project, plan) pairs.

clu is multi-plan from day one: one host can drive N plans across M
projects. Features that walk all plans on a host (fleet view, inbound
reply routing) need a central index because the state files themselves
live scattered under each project's `plans/.orchestrator/`.

Stored at `$XDG_CONFIG_HOME/clu/registry.json` (default `~/.config/clu/`).
Writes go through the same tmp+fsync+rename + flock primitives as the
per-plan state files.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from . import state as st
from ._xdg_guard import assert_xdg_safe

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PlanEntry:
    project_root: str
    plan_slug: str
    registered_at: str


def registry_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    path = root / "clu" / "registry.json"
    assert_xdg_safe(path)
    return path


def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION, "plans": []}


def _load(path: Path) -> dict:
    if not path.exists():
        return _empty()
    return st.load(path, expected_version=SCHEMA_VERSION)


def _mutate(path: Path):
    """lock + load + yield-for-mutation + atomic write. Tolerates a missing
    file (first-register creates it) via the shared `state.locked_json`
    primitive."""
    return st.locked_json(path, expected_version=SCHEMA_VERSION, empty=_empty)


def entries(path: Path | None = None) -> list[PlanEntry]:
    path = path or registry_path()
    return [PlanEntry(**row) for row in _load(path).get("plans", [])]


def register(project_root: Path, plan_slug: str, *, path: Path | None = None) -> bool:
    """Add (project_root, plan_slug). Returns False if it was already present."""
    st.validate_slug(plan_slug, kind="plan slug")
    project_root = project_root.resolve()
    if not project_root.is_dir():
        raise FileNotFoundError(f"project_root not a directory: {project_root}")

    with _mutate(path or registry_path()) as data:
        key = (str(project_root), plan_slug)
        if any((row["project_root"], row["plan_slug"]) == key for row in data["plans"]):
            return False
        data["plans"].append(asdict(PlanEntry(
            project_root=str(project_root),
            plan_slug=plan_slug,
            registered_at=st.utcnow(),
        )))
    return True


def load_entry_state(entry: PlanEntry) -> dict | None:
    """Project (registry entry → loaded state.json) or None on any failure.

    Tolerant by design: a stale registry entry — missing project dir,
    deleted state file, schema drift — must not take callers that walk
    every plan (fleet view, inbound poller) down. Returns None on every
    recoverable failure mode; never raises.
    """
    from .config import load_project_config  # local import to avoid cycle
    try:
        cfg = load_project_config(Path(entry.project_root))
        state_path = cfg.state_path(entry.plan_slug)
    except (OSError, st.InvalidSlug, ValueError):
        return None
    if not state_path.exists():
        return None
    try:
        return st.load(state_path)
    except (OSError, ValueError, st.SchemaVersionMismatch):
        return None


def unregister(project_root: Path, plan_slug: str, *, path: Path | None = None) -> bool:
    project_root = project_root.resolve()
    target = path or registry_path()
    if not target.exists():
        return False
    with _mutate(target) as data:
        before = len(data["plans"])
        data["plans"] = [
            row for row in data["plans"]
            if (row["project_root"], row["plan_slug"]) != (str(project_root), plan_slug)
        ]
        return len(data["plans"]) != before
