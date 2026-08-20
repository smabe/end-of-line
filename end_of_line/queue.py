"""Per-project plan queue — two tables in the project database.

clu's cron tick advances *phases within* a plan, but inter-plan
transitions need someone to invoke `clu init` for the next plan. The
queue holds that list so an operator can scribble plans, queue them,
walk away, and wake up to a drained chain.

`queue` holds what is pending, `queue_history` what has left it, both in
`<plan_dir>/.orchestrator/clu.db` beside plan state. `id` is the ORDER:
the head is the smallest id, a tail-add takes the next autoincrement, and
`--front` inserts below the current minimum so nothing already queued is
renumbered.

There is no repair machinery here any more, and its absence is the point.
The old module spent most of itself rescuing slugs out of a half-written
`queue.json` with a regex over raw bytes, because a JSON file interrupted
mid-write is recoverable text. A WAL database never hands a reader a
half-written store: the pop is one transaction that either committed or
did not. What is left — a genuinely corrupt database file — is not
something a headless worker edits back to health, so it surfaces to the
operator instead of being handed to one.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import db
from . import state as st

# Columns of their own; everything else on an entry rides in `extra`.
_ENTRY_COLUMNS = ("slug", "added_at", "added_by", "batch_id")

# What leaving the pending queue is called, in the events and in
# `clu queue list`'s "Recent failures" block.
OUTCOME_POPPED = "popped"
OUTCOME_ABSORBED = "absorbed"
OUTCOME_ABANDONED = "abandoned"
OUTCOME_REMOVED = "removed"


class AlreadyQueued(Exception):
    """An add would duplicate a slug already pending. Carries its position.

    Raised INSIDE the insert transaction so the check and the insert cannot
    be separated by another writer — the reason it is an exception rather
    than a return value the caller checks first.
    """

    def __init__(self, slug: str, position: int) -> None:
        super().__init__(f"{slug!r} already queued at position {position}")
        self.slug = slug
        self.position = position


# --- row <-> entry ------------------------------------------------------------


def _row_to_entry(row: sqlite3.Row) -> dict[str, Any]:
    """One queue row back into the entry dict its writer handed in.

    A column that is NULL is left OUT rather than emitted as None, matching
    how the plan store projects its own optional columns: absent and None
    read the same through `.get`, and re-emitting every column would grow
    keys on entries that never carried them.
    """
    entry: dict[str, Any] = {"slug": row["slug"]}
    for col in _ENTRY_COLUMNS[1:]:
        if row[col] is not None:
            entry[col] = row[col]
    if row["extra"] is not None:
        entry.update(json.loads(row["extra"]))
    return entry


def _entry_values(entry: dict) -> tuple:
    extra = {k: v for k, v in entry.items() if k not in _ENTRY_COLUMNS}
    return (
        *(entry.get(col) for col in _ENTRY_COLUMNS),
        json.dumps(extra) if extra else None,
    )


# --- cursor-level primitives --------------------------------------------------
#
# The queue's two multi-step callers — the worker-mode `queue add` (cap count,
# three idempotency checks, then the insert) and the tick's pop (head check,
# plan create, move to history) — need their steps in ONE transaction, and the
# pop's middle step writes a table this module does not own. So the primitives
# take a cursor and the convenience wrappers below open a transaction around
# one of them.


def pending_in_txn(cur: sqlite3.Cursor) -> list[dict]:
    rows = cur.execute(
        f"SELECT id, {', '.join(_ENTRY_COLUMNS)}, extra FROM queue ORDER BY id"
    ).fetchall()
    return [_row_to_entry(r) for r in rows]


def history_in_txn(cur: sqlite3.Cursor) -> list[dict]:
    rows = cur.execute(
        f"SELECT id, {', '.join(_ENTRY_COLUMNS)}, extra, ended_at, outcome "
        f"FROM queue_history ORDER BY id"
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        entry = _row_to_entry(row)
        entry["ended_at"] = row["ended_at"]
        entry["outcome"] = row["outcome"]
        out.append(entry)
    return out


def insert_in_txn(cur: sqlite3.Cursor, entry: dict, *, front: bool = False) -> int:
    """Insert one entry; return its 1-based position. Raises `AlreadyQueued`.

    A tail insert lets AUTOINCREMENT pick the id. A front insert takes
    `MIN(id) - 1`, which is legal alongside AUTOINCREMENT (the sequence only
    ever tracks the maximum) and goes negative once the queue has been
    front-loaded past zero — deliberately, because renumbering the entries
    already queued is the alternative and it would move rows a concurrent
    reader is looking at.
    """
    slug = entry["slug"]
    st.validate_slug(slug, kind="plan slug")
    existing = [e["slug"] for e in pending_in_txn(cur)]
    if slug in existing:
        raise AlreadyQueued(slug, existing.index(slug) + 1)
    columns = f"{', '.join(_ENTRY_COLUMNS)}, extra"
    values = _entry_values(entry)
    if front:
        low = cur.execute("SELECT MIN(id) FROM queue").fetchone()[0]
        new_id = 1 if low is None else int(low) - 1
        cur.execute(
            f"INSERT INTO queue (id, {columns}) VALUES ({', '.join('?' * (len(values) + 1))})",
            (new_id, *values),
        )
        return 1
    cur.execute(
        f"INSERT INTO queue ({columns}) VALUES ({', '.join('?' * len(values))})",
        values,
    )
    return len(existing) + 1


def pop_head_in_txn(
    cur: sqlite3.Cursor,
    slug: str,
    outcome: str,
    extra: dict | None = None,
) -> bool:
    """Move the head to history IF the head is still `slug`. False = it moved.

    The head re-check that the file-backed pop did by hand after taking the
    lock happens inside the write transaction here, which is what makes two
    ticks racing the same pop safe: the loser reads a head that is no longer
    `slug`, returns False before deleting anything, and repeats none of the
    work the winner already did.
    """
    row = cur.execute(
        f"SELECT id, {', '.join(_ENTRY_COLUMNS)}, extra FROM queue ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None or row["slug"] != slug:
        return False
    cur.execute("DELETE FROM queue WHERE id = ?", (row["id"],))
    entry = {**_row_to_entry(row), **(extra or {})}
    values = _entry_values(entry)
    cur.execute(
        f"INSERT INTO queue_history ({', '.join(_ENTRY_COLUMNS)}, extra, ended_at, outcome) "
        f"VALUES ({', '.join('?' * (len(values) + 2))})",
        (*values, st.utcnow(), outcome),
    )
    return True


def remove_in_txn(cur: sqlite3.Cursor, slug: str) -> bool:
    """Drop `slug` from pending into history with outcome `removed`."""
    row = cur.execute(
        f"SELECT id, {', '.join(_ENTRY_COLUMNS)}, extra FROM queue "
        f"WHERE slug = ? ORDER BY id LIMIT 1",
        (slug,),
    ).fetchone()
    if row is None:
        return False
    cur.execute("DELETE FROM queue WHERE id = ?", (row["id"],))
    values = _entry_values(_row_to_entry(row))
    cur.execute(
        f"INSERT INTO queue_history ({', '.join(_ENTRY_COLUMNS)}, extra, ended_at, outcome) "
        f"VALUES ({', '.join('?' * (len(values) + 2))})",
        (*values, st.utcnow(), OUTCOME_REMOVED),
    )
    return True


# --- whole-operation API ------------------------------------------------------


def _read(orch_dir: Path, fn: Callable[[sqlite3.Cursor], list[dict]]) -> list[dict]:
    """Run a cursor-level read, or return [] when the database does not exist.

    Absent database == empty queue, the direct heir of "no queue.json means
    nothing is queued". Read-only so a read never brings the database into
    being. Everything else — a schema from a newer clu, a corrupt file,
    contention — is raised for the caller to decide about, because silently
    reading an unreadable queue as empty is how a chain drains itself.
    """
    path = db.project_db_path(Path(orch_dir))
    if not path.exists():
        return []
    conn = db.connect(path, readonly=True)
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_project_schema(conn)
        with db.read_txn(conn) as cur:
            return fn(cur)
    finally:
        conn.close()


def pending(orch_dir: Path) -> list[dict]:
    """Entries waiting, head first."""
    return _read(orch_dir, pending_in_txn)


def history(orch_dir: Path) -> list[dict]:
    """Entries that have left the queue, oldest first, with outcome."""
    return _read(orch_dir, history_in_txn)


def add(orch_dir: Path, entry: dict) -> int:
    """Append one entry to the tail; return its 1-based position."""
    return add_many(orch_dir, [entry])[0]


def add_many(orch_dir: Path, entries: list[dict], *, front: bool = False) -> list[int]:
    """Insert several entries in ONE transaction; return their positions.

    One transaction because a half-added batch is a state the dry-merge gate
    would see and act on: `queue add --batch` names a set of plans that are
    meant to run as siblings, and two of three is not a smaller version of
    that, it is a different plan.

    A front-loaded batch is inserted BACK to FRONT, because each front insert
    lands below the previous minimum — so walking the batch in reverse is what
    leaves the caller's order intact at the head, which is what splicing the
    list at index 0 used to do in one step.
    """
    with db.project_conn(Path(orch_dir)) as conn, db.write_txn(conn) as cur:
        if front:
            for entry in reversed(entries):
                insert_in_txn(cur, entry, front=True)
            return list(range(1, len(entries) + 1))
        return [insert_in_txn(cur, entry) for entry in entries]


def remove(orch_dir: Path, slug: str) -> bool:
    """Remove `slug` from pending, recording it in history. False = absent."""
    with db.project_conn(Path(orch_dir)) as conn, db.write_txn(conn) as cur:
        return remove_in_txn(cur, slug)


def pop_head_if(
    orch_dir: Path,
    slug: str,
    outcome: str,
    extra: dict | None = None,
) -> bool:
    """`pop_head_in_txn` in a transaction of its own, for the pops that
    have nothing else to do in the same breath (absorb, abandon)."""
    with db.project_conn(Path(orch_dir)) as conn, db.write_txn(conn) as cur:
        return pop_head_in_txn(cur, slug, outcome, extra)
