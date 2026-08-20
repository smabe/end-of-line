"""Background-monitoring marker.

A successful `clu install-hook` writes a marker into the `monitor` table of
the host database at `$XDG_CONFIG_HOME/clu/clu.db` (default
`~/.config/clu/`) so subsequent invocations are idempotent and clu CLI hints
can suppress themselves when monitoring is already wired up. Account-wide,
not per-project — one hook watches every plan on the host.

The marker is a set of key/value rows: `hook_installed_at`, `hook_path`,
`settings_json_path`, plus `session_start_hook_path` /
`session_start_installed_at` once `clu install-hook --session-start` has run.
NO rows means "not installed", which is the whole predicate `is_scheduled`
answers.

The v1 marker (the broken pre-#20 `/schedule` install) has no equivalent
here and needs none: it was a JSON file, that file is not read any more, and
a host that only ever had one reads as un-monitored — which is precisely the
"needs reinstall" answer the v1 branch used to compute.

Tolerant by design: no rows, a locked database, or one written by a newer clu
all surface as `None` / `False` so callers can branch on a single "do we need
to install?" predicate. The marker is advisory, never load-bearing.
"""

from __future__ import annotations

from pathlib import Path

from . import db
from . import state as st

SCHEMA_VERSION = 2

# Where the marker lived before it moved into the host database. Nothing in
# clu reads or writes this file any more; the path is kept only so the
# quarantine sweep has a name to point at.
LEGACY_MARKER_FILENAME = "monitor.json"


def load_marker(path: Path | None = None) -> dict | None:
    """Return the marker dict when one is installed; None otherwise.

    `path` names the host DATABASE, not a marker file — the keyword is the
    same test seam it always was.
    """
    try:
        with db.host_conn(path) as conn:
            rows = conn.execute("SELECT k, v FROM monitor").fetchall()
    except db.DEGRADABLE_ERRORS:
        return None
    if not rows:
        return None
    marker: dict = {"schema_version": SCHEMA_VERSION}
    marker.update(dict(rows))
    return marker


def is_scheduled(path: Path | None = None) -> bool:
    return load_marker(path) is not None


def _stamp(pairs: list[tuple[str, str]], path: Path | None) -> None:
    with db.host_conn(path) as conn, db.write_txn(conn) as cur:
        cur.executemany("INSERT OR REPLACE INTO monitor (k, v) VALUES (?, ?)", pairs)


def record_hook_installed(
    hook_path: str,
    settings_json_path: str,
    *,
    path: Path | None = None,
) -> None:
    """Stamp the marker. Per-key upsert, so a `session_start_hook_path`
    recorded by an earlier `--session-start` run survives a re-install.

    `path` parameter is for tests; production uses the default XDG-derived
    host database.
    """
    _stamp(
        [
            ("hook_installed_at", st.utcnow()),
            ("hook_path", hook_path),
            ("settings_json_path", settings_json_path),
        ],
        path,
    )


def record_session_start_installed(
    session_start_hook_path: str,
    *,
    path: Path | None = None,
) -> None:
    """Stamp the SessionStart hook path onto the marker (#70).

    Additive — operators running `clu install-hook --session-start` get this
    field and the existing `hook_path` field populated. Also stamps
    install-time, so the operator can audit when the SessionStart hook was
    added separately from UserPromptSubmit.
    """
    _stamp(
        [
            ("session_start_hook_path", session_start_hook_path),
            ("session_start_installed_at", st.utcnow()),
        ],
        path,
    )


def clear_marker(path: Path | None = None) -> None:
    with db.host_conn(path) as conn, db.write_txn(conn) as cur:
        cur.execute("DELETE FROM monitor")
