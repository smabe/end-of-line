"""Host-level registry of known (project, plan) pairs.

clu is multi-plan from day one: one host can drive N plans across M
projects. Features that walk all plans on a host (fleet view, inbound
reply routing) need a central index because the state files themselves
live scattered under each project's `plans/.orchestrator/`.

Stored in the `registry` table of the host database at
`$XDG_CONFIG_HOME/clu/clu.db` (default `~/.config/clu/`). The `path`
keyword every function still takes now names that DATABASE rather than a
JSON file — the argument exists for the same reason it always did, so a
test can point the store somewhere of its own.

`entries()` is the hottest read in the system: `clu top` and `clu serve`
call it every frame. It is one SELECT on a connection opened and closed
inside the call — never a transaction held across frames, which would pin
the WAL and grow the file without bound.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import db, plan_store
from . import state as st


@dataclass(frozen=True)
class PlanEntry:
    project_root: str
    plan_slug: str
    registered_at: str


def entries(path: Path | None = None) -> list[PlanEntry]:
    """Every registered pair, in registration order.

    Tolerant by design, exactly as the missing-file case was: a host
    database that does not exist yet, is momentarily locked, or was written
    by a newer clu all read as an EMPTY registry rather than taking down a
    caller that walks every plan on the host.
    """
    try:
        with db.host_conn(path) as conn:
            rows = conn.execute(
                "SELECT project_root, plan_slug, registered_at FROM registry ORDER BY rowid"
            ).fetchall()
    except db.DEGRADABLE_ERRORS:
        return []
    return [PlanEntry(*row) for row in rows]


def entries_for_project(project_root: Path, path: Path | None = None) -> list[PlanEntry]:
    target = project_root.resolve()
    return [e for e in entries(path) if Path(e.project_root).resolve() == target]


def register(project_root: Path, plan_slug: str, *, path: Path | None = None) -> bool:
    """Add (project_root, plan_slug). Returns False if it was already present."""
    st.validate_slug(plan_slug, kind="plan slug")
    project_root = project_root.resolve()
    if not project_root.is_dir():
        raise FileNotFoundError(f"project_root not a directory: {project_root}")

    with db.host_conn(path) as conn, db.write_txn(conn) as cur:
        # INSERT OR IGNORE against the (project_root, plan_slug) primary key:
        # the duplicate check and the insert are one statement, so two clu
        # processes registering the same pair cannot both see "absent" first.
        cur.execute(
            "INSERT OR IGNORE INTO registry (project_root, plan_slug, registered_at) "
            "VALUES (?, ?, ?)",
            (str(project_root), plan_slug, st.utcnow()),
        )
        return cur.rowcount > 0


def load_entry_state(entry: PlanEntry) -> dict | None:
    """Project (registry entry → the plan's rows) or None on any failure.

    THE fleet-read seam: `clu`'s summary, `clu top`, `clu serve`, the blocker
    locator and the SessionStart hook all reach plan state through here, so the
    tolerance rules have one definition and one place to be tested.

    Tolerant by design: a stale registry entry — missing project dir, deleted
    plan, schema drift, a store another process is mid-write on — must not take
    a caller that walks every plan down. Returns None on every recoverable
    failure mode; never raises.
    """
    from .config import load_project_config  # local import to avoid cycle

    try:
        cfg = load_project_config(Path(entry.project_root))
        state_path = cfg.state_path(entry.plan_slug)
    except (OSError, st.InvalidSlug, ValueError):
        return None
    if not plan_store.exists_for_path(state_path):
        return None
    try:
        return plan_store.snapshot(*plan_store.key_for_state_path(state_path))
    except (*db.DEGRADABLE_ERRORS, ValueError, st.SchemaVersionMismatch):
        # The file-era clause named `OSError`, `ValueError` and
        # `SchemaVersionMismatch`, and none of those catches what a DATABASE
        # fails with: `sqlite3.Error` for a broken store, `db.DbBusy` (a
        # RuntimeError) for one held past the budget, `db.SchemaTooNew` for one
        # written by a newer clu. Every caller of this function walks the whole
        # fleet, so any of those escaping would replace a dashboard with a
        # traceback because one plan's tick happened to hold its project lock.
        return None


def unregister(project_root: Path, plan_slug: str, *, path: Path | None = None) -> bool:
    project_root = project_root.resolve()
    with db.host_conn(path) as conn, db.write_txn(conn) as cur:
        cur.execute(
            "DELETE FROM registry WHERE project_root = ? AND plan_slug = ?",
            (str(project_root), plan_slug),
        )
        return cur.rowcount > 0


def _unregister_many(
    targets: Iterable[tuple[str, str]],
    *,
    path: Path | None = None,
) -> int:
    """Remove several (project_root, plan_slug) pairs in ONE transaction.

    `clu unregister --all-archived` prunes a batch and the operator sees one
    all-or-nothing transition, not a half-pruned registry. Private because
    the batch shape has exactly one caller; `unregister` is the public one.
    """
    rows = [(root, slug) for root, slug in targets]
    if not rows:
        return 0
    with db.host_conn(path) as conn, db.write_txn(conn) as cur:
        cur.executemany(
            "DELETE FROM registry WHERE project_root = ? AND plan_slug = ?",
            rows,
        )
        return cur.rowcount
