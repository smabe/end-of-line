"""Per-event inbox surfaced to active Claude Code sessions.

Pattern: clu writes one row per event into the `inbox` table of the host
database. The bundled UserPromptSubmit hook script
(`end_of_line.hooks.clu_inbox_surface`) reads the inbox at the start of every
Claude turn, filters to events whose `project_root` matches the current
working tree, formats them into a system reminder, and flags the ones it
surfaced as processed — reading and flagging in ONE transaction, via
`claim_for_project`.

The `processed` flag replaces the move-into-`processed/` protocol the
directory version used. That protocol's dedup came from `os.rename` being
atomic, which it is — but two sessions surfacing the same event both saw it
unprocessed first and both rendered it. `UPDATE … WHERE processed = 0` inside
a write transaction is strictly stronger: the loser of the race sees nothing
to claim.

Arrival order comes from the autoincrement `id` rather than the 19-digit
nanosecond filenames the directory version sorted lexically.

The `inbox` keyword every function takes names the host DATABASE — the same
test seam the directory path was, pointed at a different kind of store.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from . import db
from . import state as st

SCHEMA_VERSION = 1

# Where the events lived before they moved into the host database. Nothing in
# clu reads or writes this directory any more; the path is kept only so the
# quarantine sweep has a name to point at.
LEGACY_INBOX_DIRNAME = "inbox"

# Every column of an event payload, in the order `_payload` unpacks them.
_EVENT_COLUMNS = "event_id, ts, type, plan_slug, project_root, summary, details"


def _payload(row: tuple) -> dict:
    """One row → the event dict every consumer has always been handed."""
    event_id, ts, type_, plan_slug, project_root, summary, details = row
    try:
        parsed = json.loads(details) if details else {}
    except ValueError:
        parsed = {}
    return {
        "id": event_id,
        "schema_version": SCHEMA_VERSION,
        "type": type_,
        "plan_slug": plan_slug,
        "project_root": project_root,
        "timestamp": ts,
        "summary": summary,
        "details": parsed if isinstance(parsed, dict) else {},
    }


def write_event(
    *,
    type: str,
    plan_slug: str,
    project_root: str,
    summary: str,
    details: dict | None = None,
    inbox: Path | None = None,
) -> str:
    """Record a single event. Returns the event id."""
    event_id = f"evt-{secrets.token_hex(4)}"
    with db.host_conn(inbox) as conn, db.write_txn(conn) as cur:
        cur.execute(
            "INSERT INTO inbox (event_id, ts, type, plan_slug, project_root, summary, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                st.utcnow(),
                type,
                plan_slug,
                str(Path(project_root).resolve()),
                summary,
                json.dumps(details or {}),
            ),
        )
    return event_id


def read_unprocessed(inbox: Path | None = None) -> list[dict]:
    """Every unprocessed event, in arrival order.

    Tolerant by design, exactly as the missing-directory case was: an absent,
    unreadable, or newer-schema store reads as an empty inbox. The surfacer
    must never crash on the way to rendering a prompt.
    """
    try:
        with db.host_conn(inbox) as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM inbox WHERE processed = 0 ORDER BY id"
            ).fetchall()
    except db.DEGRADABLE_ERRORS:
        return []
    return [_payload(r) for r in rows]


def mark_processed(event_id: str, inbox: Path | None = None) -> None:
    """Flag the event with `id == event_id` processed.

    Idempotent: an unknown id, or one already processed, updates nothing and
    returns silently — the surfacer should never propagate cleanup failures.
    `AND processed = 0` makes the check and the flag one statement, so a
    second session cannot claim an event this one already took.
    """
    with db.host_conn(inbox) as conn, db.write_txn(conn) as cur:
        cur.execute(
            "UPDATE inbox SET processed = 1, processed_at = ? WHERE event_id = ? AND processed = 0",
            (st.utcnow(), event_id),
        )


def list_for_project(
    project_root: str,
    inbox: Path | None = None,
) -> list[dict]:
    """Return unprocessed events whose `project_root` matches `project_root`."""
    target = str(Path(project_root).resolve())
    try:
        with db.host_conn(inbox) as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM inbox "
                f"WHERE processed = 0 AND project_root = ? ORDER BY id",
                (target,),
            ).fetchall()
    except db.DEGRADABLE_ERRORS:
        return []
    return [_payload(r) for r in rows]


def claim_for_project(
    project_root: str,
    *,
    limit: int,
    inbox: Path | None = None,
) -> list[dict]:
    """Read this project's unprocessed events and flag the newest `limit` of
    them processed, both inside ONE transaction.

    Returns the SAME list `list_for_project` would — every unprocessed event
    for the project, in arrival order — not just the flagged ones, and the
    asymmetry is deliberate on both sides:

    * Flagging is capped because the caller RENDERS at most `limit` events.
      Claiming everything would silently consume events the operator never
      saw; what is not claimed stays unprocessed for the next turn.
    * The return is uncapped because the caller also needs to know how many
      it is NOT showing, to say so in its truncation footer. Handing back
      only the claimed set would erase that count.

    The single transaction is the point: two sessions surfacing the same
    event both used to see it unprocessed and both rendered it. Here the
    loser's SELECT runs after the winner's UPDATE committed and finds
    nothing.
    """
    target = str(Path(project_root).resolve())
    with db.host_conn(inbox) as conn, db.write_txn(conn) as cur:
        rows = cur.execute(
            f"SELECT id, {_EVENT_COLUMNS} FROM inbox "
            f"WHERE processed = 0 AND project_root = ? ORDER BY id",
            (target,),
        ).fetchall()
        claimed = rows[-limit:] if limit > 0 else []
        if claimed:
            now = st.utcnow()
            cur.executemany(
                "UPDATE inbox SET processed = 1, processed_at = ? "
                "WHERE id = ? AND processed = 0",
                [(now, r[0]) for r in claimed],
            )
    return [_payload(r[1:]) for r in rows]
