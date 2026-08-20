"""Per-plan state in the project database, behind today's dict-shaped API.

Plan state used to be one JSON document per plan under
`plans/.orchestrator/<slug>.state.json`, mutated under a sibling flock. It now
lives in the project database (`plans/.orchestrator/clu.db`) as normalized rows
— a `plans` head row plus `claims`, `blockers`, `spawned_tasks` and `events` —
while every consumer keeps calling `st.load` / `st.mutate` and keeps getting the
same dict back.

`mutate_compat` is what makes that true, and it is deliberately temporary. It
loads the whole plan into today's dict shape, hands the caller that dict, and
writes the WHOLE thing back inside one `BEGIN IMMEDIATE` transaction — the same
information a JSON rewrite carried, so it is correctness-equivalent to what it
replaces. The facade exists so the backend swap and the call-site migration are
separate, reviewable commits, and it is deleted once no caller needs it.

The `op_*` functions at the bottom are the other half, and the destination: one
purpose, one transaction, only the rows that change. Every path that fires
constantly is on them — heartbeats, activity stamps, the worker callbacks, the
dispatcher's pid stamp — so a ping no longer rewrites a plan's whole event
history to record one timestamp. The facade still carries the operator commands
and the tick until later phases move them.

Two seams, not one. The `Path` a caller already holds is the KEY:
`<orch_dir>/<slug>.state.json` names (the database in `<orch_dir>`, plan
`<slug>`), which is why `config.state_path`'s slug validation and traversal
guard still gate every external slug. And `Path.exists()` on a state path was
its own engine — 21 sites asked the filesystem "does this plan exist" before
ever reaching a load — so `exists` is a first-class part of this module rather
than a convenience on top of it.

**Exception types are contract.** Callers catch `FileNotFoundError`,
`ValueError` and `st.SchemaVersionMismatch` by name around what used to be file
IO, and a `sqlite3.Error` escaping into those call sites would take down a fleet
walk that used to skip one unreadable plan. So a missing plan (or missing
database) raises `FileNotFoundError`, a database newer than this clu raises
`st.SchemaVersionMismatch` (upstream decision #6: skip, never downgrade), and a
database that cannot be read at all raises `ValueError` — the same shape a
corrupt JSON document raised.
"""

from __future__ import annotations

import errno
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import db
from . import state as st

# Re-exported from `state`, which owns the definition because its three
# primitives route on it. Named here too so the store's own callers do not have
# to know which module holds the constant.
STATE_SUFFIX = st.STATE_SUFFIX

# What `mutate_compat` waits when the caller names no budget. Today's
# `st.mutate` blocks on the flock indefinitely, and the closest safe stand-in is
# a wait long enough that every real contender finishes first: the supervisor's
# tick holds its window across a `reap_orphan_pgroup` that polls for up to 5s,
# and heartbeats/dispatch stamps are milliseconds. Bounded rather than infinite
# so a genuinely wedged holder surfaces as an error instead of a hung fleet.
BLOCKING_TIMEOUT_S = 60.0

# --- column maps -------------------------------------------------------------
#
# Scalar columns are stored raw (a SQL query in a later phase says
# `WHERE status = 'running'`, so `status` must not be JSON-quoted). Sub-objects
# with a closed shape are JSON-valued columns. Everything a writer invents that
# has no column of its own lands in the catch-all — `plans.extra`,
# `claims.flags`, `spawned_tasks.payload` — so a field added by some future
# caller round-trips instead of vanishing.

_PLAN_SCALARS = ("status", "plan_dir", "created_at", "batch_id")
# JSON-valued, and NULL means the KEY WAS ABSENT rather than "present and null":
# `json.dumps(None)` is the text `null`, which is distinguishable from SQL NULL.
# `get_worktree` reads `data.get("worktree")` while `cmd_complete` sets it to
# None explicitly, and those two must stay different states.
_PLAN_JSON = ("config", "phases", "worktree")
_PLAN_DERIVED = (
    "schema_version",
    "plan_slug",
    "current_claim",
    "blockers",
    "spawned_tasks",
    "events",
)
_PLAN_OWN_KEYS = frozenset(_PLAN_SCALARS + _PLAN_JSON + _PLAN_DERIVED)

# Order mirrors `claim_phase` + `_stamp_pid`, so a snapshot reads like the dict
# those two build.
_CLAIM_SCALARS = (
    "phase_id",
    "claimed_by",
    "lease_expires",
    "started_at",
    "last_heartbeat_at",
    "attempts",
    "pid",
    "pgid",
    "log_path",
    "session_id",
    "active_tool_started_at",
)
_CLAIM_JSON = ("attestations", "cpu_samples")
_CLAIM_OWN_KEYS = frozenset(_CLAIM_SCALARS + _CLAIM_JSON)

# `add_blocker` always writes these nine, and `answer_blocker` indexes
# `b["answer"]` directly — so they are emitted even when null.
_BLOCKER_ALWAYS = (
    ("id", "blocker_id"),
    ("phase_id", "phase_id"),
    ("type", "type"),
    ("question", "question"),
    ("options", "options"),
    ("context", "context"),
    ("asked_at", "asked_at"),
    ("answer", "answer"),
    ("answered_at", "answered_at"),
)
# Stamped later by the supervisor / the Discord notifier, and read with
# `.get()` — absent and false are different, so NULL stays absent.
_BLOCKER_OPTIONAL = (
    ("consumed", "consumed"),
    ("last_repinged_at", "last_repinged_at"),
    ("notify_metadata", "notify_metadata"),
)
_BLOCKER_JSON = frozenset({"options", "notify_metadata"})
_BLOCKER_KNOWN = frozenset(k for k, _ in _BLOCKER_ALWAYS) | frozenset(
    k for k, _ in _BLOCKER_OPTIONAL
)

_TASK_OWN_KEYS = frozenset({"id", "status"})

# What the two questions that CANNOT fail — "does this plan exist" and "which
# plans are there" — swallow. Both replace filesystem calls that had no failure
# mode, and both feed callers with no except clause.
_TOLERANT_READ_ERRORS: tuple[type[BaseException], ...] = (
    FileNotFoundError,
    st.SchemaVersionMismatch,
    *db.DEGRADABLE_ERRORS,
)


# --- keys --------------------------------------------------------------------


def key_for_state_path(state_path: Path) -> tuple[Path, str]:
    """`<orch_dir>/<slug>.state.json` → (orchestrator dir, slug).

    The orchestrator DIR rather than the database file, because every other
    function here takes the dir and derives the database from it — one place
    knows the filename. The slug is re-validated even though `state_path` was
    usually built by `config.state_path` (which validates): this function is
    also handed paths from tests and from `watch`'s poll set, and the slug is
    about to become a SQL parameter naming a plan.
    """
    path = Path(state_path)
    if not path.name.endswith(STATE_SUFFIX):
        raise ValueError(f"not a plan-state path: {path}")
    slug = path.name[: -len(STATE_SUFFIX)]
    st.validate_slug(slug, kind="plan slug")
    return path.parent, slug


def _state_path_for(orch_dir: Path, slug: str) -> Path:
    """The key a caller would have used — for error messages that still name it."""
    return Path(orch_dir) / f"{slug}{STATE_SUFFIX}"


def _missing(orch_dir: Path, slug: str) -> FileNotFoundError:
    path = _state_path_for(orch_dir, slug)
    return FileNotFoundError(errno.ENOENT, "no such plan", str(path))


# --- connections -------------------------------------------------------------


@contextmanager
def _read_conn(orch_dir: Path) -> Iterator[sqlite3.Connection]:
    """A read-only connection with the schema checked, or `FileNotFoundError`.

    Read-only on purpose: a read must never bring a database into existence as
    a side effect, and `db.connect(readonly=True)` cannot create one.
    """
    path = db.project_db_path(Path(orch_dir))
    if not path.exists():
        raise FileNotFoundError(errno.ENOENT, "no clu database", str(path))
    conn = db.connect(path, readonly=True)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def _write_conn(orch_dir: Path, *, timeout_s: float | None = None) -> Iterator[sqlite3.Connection]:
    budget = BLOCKING_TIMEOUT_S if timeout_s is None else timeout_s
    conn = db.connect(db.project_db_path(Path(orch_dir)), timeout_s=budget)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        yield conn
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    try:
        db.ensure_project_schema(conn)
    except db.SchemaTooNew as exc:
        raise st.SchemaVersionMismatch(str(exc)) from exc


@contextmanager
def _tolerant_read() -> Iterator[None]:
    """Map a database that cannot be read onto the ValueError callers catch.

    A corrupt JSON document raised `json.JSONDecodeError` (a `ValueError`) and
    every tolerant reader in the fleet catches it by that name. A corrupt — or
    schema-less, or half-created — database arrives as `sqlite3.Error`, which
    those same callers would let through as a crash.
    """
    try:
        yield
    except sqlite3.Error as exc:
        raise ValueError(f"unreadable clu database: {exc}") from exc


# --- reads -------------------------------------------------------------------


def exists(orch_dir: Path, slug: str) -> bool:
    """Does this plan exist? The replacement for asking the filesystem.

    It cannot raise, because the filesystem call it replaces could not, and the
    21 gates that ask it have no except clause. What each failure answers is
    chosen to keep the CALLER's behaviour the same as it was:

    - no database (or a newer-schema one, which upstream decision #6 says to
      skip, or a momentarily busy one) → **no**, exactly as a missing state
      file answered;
    - a database that is present but unreadable → **yes**. A corrupt state
      file also passed this gate and then failed the load, which is what put
      the plan's name in front of the operator. Answering "no" here would make
      a broken store look like an empty project and lose that report.

    Init's duplicate guard does not rest on this — `create` is an atomic INSERT
    that raises `FileExistsError` on a race.
    """
    st.validate_slug(slug, kind="plan slug")
    try:
        with _read_conn(orch_dir) as conn:
            row = conn.execute("SELECT 1 FROM plans WHERE slug = ?", (slug,)).fetchone()
    except (FileNotFoundError, st.SchemaVersionMismatch, db.DbBusy):
        return False
    except (sqlite3.Error, OSError):
        return True
    return row is not None


def exists_for_path(state_path: Path) -> bool:
    """`exists`, keyed by the state path a caller already holds.

    The 21 gates that used to put that question to the filesystem hold the
    path, not the (dir, slug) pair — and the path IS the key, so the split belongs here
    rather than repeated at every call site. An unparseable path (a slug
    outside the alphabet) answers "no", because that is what the filesystem
    call it replaces would have answered.
    """
    try:
        orch_dir, slug = key_for_state_path(state_path)
    except (ValueError, st.InvalidSlug):
        return False
    return exists(orch_dir, slug)


def plan_slugs(orch_dir: Path) -> list[str]:
    """Every plan in this project's database, sorted — replaces the file glob.

    Tolerant for the same reason `exists` is: it replaces `glob`, which had no
    failure mode. The zombie sweep's per-plan load still fails loudly per plan.
    """
    try:
        with _read_conn(orch_dir) as conn:
            rows = conn.execute("SELECT slug FROM plans ORDER BY slug").fetchall()
    except _TOLERANT_READ_ERRORS:
        return []
    return [row[0] for row in rows]


def snapshot(orch_dir: Path, slug: str) -> dict:
    """The plan as the dict `st.load` returned yesterday.

    One read transaction across all five tables, so a writer committing halfway
    through cannot produce a claim from before it and events from after it.
    """
    st.validate_slug(slug, kind="plan slug")
    with _tolerant_read(), _read_conn(orch_dir) as conn, db.read_txn(conn) as cur:
        return _snapshot_in_txn(cur, orch_dir, slug)


def dump_json(orch_dir: Path, slug: str) -> str:
    """`clu state dump`'s body — the plan as indented JSON, archive included.

    The one reader that renders `events_archive` alongside `events`. `snapshot`
    deliberately does not: watch, top and the locator read the HOT table only,
    which is what keeps it lean (upstream decision #4). But `clu state dump` is
    the operator's escape hatch — the thing that replaced opening the state
    file in an editor — and a history that vanished at archive time would make
    it a worse hatch than the file it replaced.
    """
    st.validate_slug(slug, kind="plan slug")
    with _tolerant_read(), _read_conn(orch_dir) as conn, db.read_txn(conn) as cur:
        data = _snapshot_in_txn(cur, orch_dir, slug)
        archived = _read_archived_events(cur, slug)
    if archived:
        # Ids are carried over rather than minted, so one sort restores the
        # order the plan actually lived through.
        data["events"] = sorted([*archived, *data["events"]], key=lambda e: e.get("id") or 0)
    return json.dumps(data, indent=2)


def _snapshot_in_txn(cur: sqlite3.Cursor, orch_dir: Path, slug: str) -> dict:
    row = cur.execute(
        "SELECT status, plan_dir, created_at, batch_id, config, phases, worktree, extra "
        "FROM plans WHERE slug = ?",
        (slug,),
    ).fetchone()
    if row is None:
        raise _missing(orch_dir, slug)

    data: dict[str, Any] = {
        "schema_version": st.SCHEMA_VERSION,
        "plan_slug": slug,
        "plan_dir": row["plan_dir"],
        "status": row["status"],
        "current_claim": _read_claim(cur, slug),
        "blockers": _read_blockers(cur, slug),
        "spawned_tasks": _read_tasks(cur, slug),
    }
    for col in ("config", "phases"):
        if row[col] is not None:
            data[col] = json.loads(row[col])
    data["events"] = _read_events(cur, slug)
    data["created_at"] = row["created_at"]
    data["batch_id"] = row["batch_id"]
    if row["worktree"] is not None:
        data["worktree"] = json.loads(row["worktree"])
    if row["extra"] is not None:
        data.update(json.loads(row["extra"]))
    return data


def _read_claim(cur: sqlite3.Cursor, slug: str) -> dict | None:
    row = cur.execute("SELECT * FROM claims WHERE plan_slug = ?", (slug,)).fetchone()
    if row is None:
        return None
    claim: dict[str, Any] = {}
    for col in _CLAIM_SCALARS:
        if row[col] is not None:
            claim[col] = row[col]
    for col in _CLAIM_JSON:
        if row[col] is not None:
            claim[col] = json.loads(row[col])
    if row["flags"] is not None:
        claim.update(json.loads(row["flags"]))
    return claim


def _read_blockers(cur: sqlite3.Cursor, slug: str) -> list[dict]:
    rows = cur.execute(
        "SELECT * FROM blockers WHERE plan_slug = ? ORDER BY rowid",
        (slug,),
    ).fetchall()
    blockers: list[dict] = []
    for row in rows:
        blocker: dict[str, Any] = {}
        for key, col in _BLOCKER_ALWAYS:
            value = row[col]
            if key in _BLOCKER_JSON and value is not None:
                blocker[key] = json.loads(value)
            else:
                blocker[key] = value
        for key, col in _BLOCKER_OPTIONAL:
            value = row[col]
            if value is None:
                continue
            if key in _BLOCKER_JSON:
                blocker[key] = json.loads(value)
            elif key == "consumed":
                blocker[key] = bool(value)
            else:
                blocker[key] = value
        blockers.append(blocker)
    return blockers


def _read_tasks(cur: sqlite3.Cursor, slug: str) -> list[dict]:
    rows = cur.execute(
        "SELECT task_id, status, payload FROM spawned_tasks WHERE plan_slug = ? ORDER BY rowid",
        (slug,),
    ).fetchall()
    tasks: list[dict] = []
    for row in rows:
        task: dict[str, Any] = {"id": row["task_id"]}
        if row["payload"] is not None:
            task.update(json.loads(row["payload"]))
        task["status"] = row["status"]
        tasks.append(task)
    return tasks


def _read_archived_events(cur: sqlite3.Cursor, slug: str) -> list[dict]:
    """Events moved out of the hot table by an explicit archival.

    Same projection as `_read_events` plus `archived_at`, so a dump says which
    rows are history rather than presenting them as live.
    """
    rows = cur.execute(
        "SELECT id, ts, type, payload, archived_at FROM events_archive "
        "WHERE plan_slug = ? ORDER BY id",
        (slug,),
    ).fetchall()
    events: list[dict] = []
    for row in rows:
        event: dict[str, Any] = {"ts": row["ts"], "type": row["type"]}
        if row["payload"] is not None:
            event.update(json.loads(row["payload"]))
        event["id"] = row["id"]
        event["archived_at"] = row["archived_at"]
        events.append(event)
    return events


def _read_events(cur: sqlite3.Cursor, slug: str) -> list[dict]:
    rows = cur.execute(
        "SELECT id, ts, type, payload FROM events WHERE plan_slug = ? ORDER BY id",
        (slug,),
    ).fetchall()
    events: list[dict] = []
    for row in rows:
        event: dict[str, Any] = {"ts": row["ts"], "type": row["type"]}
        if row["payload"] is not None:
            event.update(json.loads(row["payload"]))
        # Last so it reads like today's `{ts, type, ...fields}` with the row id
        # appended — and so a payload field can never shadow it.
        event["id"] = row["id"]
        events.append(event)
    return events


# --- writes ------------------------------------------------------------------


def create(orch_dir: Path, data: dict) -> None:
    """Insert a brand-new plan. Raises `FileExistsError` if the slug is taken.

    The duplicate check IS the insert: `plans.slug` is the primary key, so two
    concurrent inits (or a tick racing a queue pop) cannot both see "absent"
    first. That replaces today's re-check-inside-the-lock.
    """
    with _write_conn(orch_dir) as conn, db.write_txn(conn) as cur:
        create_in_txn(cur, data, orch_dir=orch_dir)


def create_in_txn(cur: sqlite3.Cursor, data: dict, *, orch_dir: Path | None = None) -> None:
    """`create`, on a transaction somebody else opened.

    The queue pop needs the plan's rows and the queue head's move to be one
    transaction, and it cannot get that by calling `create` — that would be a
    second connection asking for the write lock this one already holds, on the
    same database, which is a deadlock rather than a wait.

    `orch_dir` only shapes the path in the `FileExistsError`, which is the
    contract `create` has always raised on a taken slug and which the pop
    relies on: the insert IS the duplicate check, so a racing tick that
    already created the plan loses here rather than writing a second copy.
    """
    slug = data["plan_slug"]
    st.validate_slug(slug, kind="plan slug")
    try:
        _insert_plan(cur, slug, data)
    except sqlite3.IntegrityError as exc:
        path = _state_path_for(orch_dir, slug) if orch_dir is not None else slug
        raise FileExistsError(errno.EEXIST, "plan already exists", str(path)) from exc


def write_full(orch_dir: Path, slug: str, data: dict) -> None:
    """Replace this plan's rows with `data`, creating the plan if absent.

    The store-side meaning of `save_atomic(state_path, data)`: the document is
    replaced wholesale. Events are re-inserted rather than diffed, so ids are
    minted fresh — which is what "the file was overwritten" always meant, and
    why no production writer takes this path after the engine swap.
    """
    st.validate_slug(slug, kind="plan slug")
    with _write_conn(orch_dir) as conn, db.write_txn(conn) as cur:
        exists_row = cur.execute("SELECT 1 FROM plans WHERE slug = ?", (slug,)).fetchone()
        if exists_row is None:
            _insert_plan(cur, slug, data)
            return
        for table in ("claims", "blockers", "spawned_tasks", "events"):
            cur.execute(f"DELETE FROM {table} WHERE plan_slug = ?", (slug,))
        _update_head(cur, slug, data)
        _write_claim(cur, slug, data.get("current_claim"))
        _write_blockers(cur, slug, data.get("blockers") or [])
        _write_tasks(cur, slug, data.get("spawned_tasks") or [])
        _insert_events(cur, slug, data.get("events") or [])


@contextmanager
def mutate_compat(
    orch_dir: Path,
    slug: str,
    *,
    timeout_s: float | None = None,
) -> Iterator[dict]:
    """Load → yield the dict → write the whole thing back, in ONE transaction.

    The dict handed out is the one serialized on the way out — never a copy
    taken before the yield — because callers keep references into it
    (`claim = data["current_claim"]; claim["pid"] = ...`) and mutate those
    after the store has already seen the top level.

    `timeout_s` bounds the wait for the write lock; exceeding it raises
    `db.DbBusy`, which `state.mutate` re-raises as `LockTimeout` so the
    activity hook's drop-on-contention contract survives verbatim.

    One consequence worth naming: the lock this takes is the PROJECT's, not one
    plan's file. Work done inside the window — a `ps`, a reap poll, a coolant
    subprocess — now blocks every plan in the project rather than one. That is
    inherent to running the whole read-modify-write in one transaction, and it
    is what the tick restructure exists to remove.
    """
    st.validate_slug(slug, kind="plan slug")
    with _write_conn(orch_dir, timeout_s=timeout_s) as conn:
        with db.write_txn(conn, timeout_s=timeout_s) as cur:
            data = _snapshot_in_txn(cur, orch_dir, slug)
            event_ids = [evt.get("id") for evt in data["events"]]
            yield data
            _write_back(cur, slug, data, event_ids)


def _write_back(cur: sqlite3.Cursor, slug: str, data: dict, event_ids: list[Any]) -> None:
    events = data.get("events") or []
    _assert_history_intact(slug, events, event_ids)
    _update_head(cur, slug, data)
    _write_claim(cur, slug, data.get("current_claim"))
    # Blockers and tasks are small, bounded lists that callers reorder and
    # restamp in place; replacing them wholesale is what keeps `rowid` order
    # equal to list order, which is what makes `q-{n}` ids and the snapshot
    # agree.
    cur.execute("DELETE FROM blockers WHERE plan_slug = ?", (slug,))
    _write_blockers(cur, slug, data.get("blockers") or [])
    cur.execute("DELETE FROM spawned_tasks WHERE plan_slug = ?", (slug,))
    _write_tasks(cur, slug, data.get("spawned_tasks") or [])
    _insert_events(cur, slug, events[len(event_ids) :])


def _assert_history_intact(slug: str, events: list[dict], event_ids: list[Any]) -> None:
    """The event log is append-only; anything else is a caller bug.

    Only entries past the original length are written back, so a caller that
    replaced or truncated history would have its edit silently ignored — which
    is worse than the loud failure, and is a bug against the JSON engine too
    (projection rebuilds every derived field from this log).
    """
    if len(events) < len(event_ids):
        raise RuntimeError(
            f"{slug}: events were truncated inside a mutate window "
            f"({len(event_ids)} → {len(events)}); the event log is append-only"
        )
    for i, prior in enumerate(event_ids):
        if events[i].get("id") != prior:
            raise RuntimeError(
                f"{slug}: event {i} was rewritten inside a mutate window; "
                f"the event log is append-only"
            )


def _insert_plan(cur: sqlite3.Cursor, slug: str, data: dict) -> None:
    cur.execute(
        "INSERT INTO plans (slug, status, plan_dir, created_at, batch_id, version, "
        "config, phases, worktree, extra) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
        (slug, *_head_values(data)),
    )
    _write_claim(cur, slug, data.get("current_claim"))
    _write_blockers(cur, slug, data.get("blockers") or [])
    _write_tasks(cur, slug, data.get("spawned_tasks") or [])
    _insert_events(cur, slug, data.get("events") or [])


def _update_head(cur: sqlite3.Cursor, slug: str, data: dict) -> None:
    cur.execute(
        "UPDATE plans SET status = ?, plan_dir = ?, created_at = ?, batch_id = ?, "
        "config = ?, phases = ?, worktree = ?, extra = ?, version = version + 1 "
        "WHERE slug = ?",
        (*_head_values(data), slug),
    )


def _head_values(data: dict) -> tuple:
    extra = {k: v for k, v in data.items() if k not in _PLAN_OWN_KEYS}
    return (
        *(data.get(col) for col in _PLAN_SCALARS),
        *(json.dumps(data[col]) if col in data else None for col in _PLAN_JSON),
        json.dumps(extra) if extra else None,
    )


def _write_claim(cur: sqlite3.Cursor, slug: str, claim: dict | None) -> None:
    if claim is None:
        cur.execute("DELETE FROM claims WHERE plan_slug = ?", (slug,))
        return
    flags = {k: v for k, v in claim.items() if k not in _CLAIM_OWN_KEYS}
    columns = (*_CLAIM_SCALARS, *_CLAIM_JSON, "flags")
    values = (
        *(claim.get(col) for col in _CLAIM_SCALARS),
        *(json.dumps(claim[col]) if col in claim else None for col in _CLAIM_JSON),
        json.dumps(flags) if flags else None,
    )
    # REPLACE rather than DELETE-then-INSERT: `release_if_expired` followed by
    # `claim_phase` inside one window would otherwise trip the primary key.
    cur.execute(
        f"INSERT OR REPLACE INTO claims (plan_slug, {', '.join(columns)}) "
        f"VALUES ({', '.join('?' * (len(columns) + 1))})",
        (slug, *values),
    )


def _write_blockers(cur: sqlite3.Cursor, slug: str, blockers: list[dict]) -> None:
    for blocker in blockers:
        unknown = set(blocker) - _BLOCKER_KNOWN
        if unknown:
            # A blocker field with no column would be dropped on the next read.
            # Loud beats silent: every writer in the tree stays inside the
            # twelve columns, so this fires only for genuinely new state.
            raise ValueError(
                f"{slug}: blocker {blocker.get('id')!r} carries unknown field(s) "
                f"{sorted(unknown)} — add a column before writing them"
            )
        columns = [col for _, col in _BLOCKER_ALWAYS]
        values = [
            json.dumps(blocker.get(key)) if key in _BLOCKER_JSON else blocker.get(key)
            for key, _ in _BLOCKER_ALWAYS
        ]
        for key, col in _BLOCKER_OPTIONAL:
            if key not in blocker:
                continue
            columns.append(col)
            raw = blocker[key]
            if key in _BLOCKER_JSON:
                values.append(json.dumps(raw))
            elif key == "consumed":
                values.append(int(bool(raw)))
            else:
                values.append(raw)
        cur.execute(
            f"INSERT INTO blockers (plan_slug, {', '.join(columns)}) "
            f"VALUES ({', '.join('?' * (len(columns) + 1))})",
            (slug, *values),
        )


def _write_tasks(cur: sqlite3.Cursor, slug: str, tasks: list[dict]) -> None:
    for task in tasks:
        payload = {k: v for k, v in task.items() if k not in _TASK_OWN_KEYS}
        cur.execute(
            "INSERT INTO spawned_tasks (plan_slug, task_id, status, payload) VALUES (?, ?, ?, ?)",
            (slug, task.get("id"), task.get("status"), json.dumps(payload) if payload else None),
        )


def _insert_events(cur: sqlite3.Cursor, slug: str, events: list[dict] | None) -> None:
    for event in events or []:
        payload = {k: v for k, v in event.items() if k not in ("ts", "type", "id")}
        cur.execute(
            "INSERT INTO events (plan_slug, ts, type, payload) VALUES (?, ?, ?, ?)",
            (slug, event.get("ts"), event.get("type"), json.dumps(payload) if payload else None),
        )
        # Stamp the id back onto the caller's dict so the list it is still
        # holding matches what a fresh snapshot would return.
        event["id"] = cur.lastrowid


# --- native ops ---------------------------------------------------------------
#
# One op = one purpose = one `BEGIN IMMEDIATE` transaction touching only the
# rows that change. This is what `mutate_compat` is not: a heartbeat here is a
# single-row UPDATE, where the facade read the whole plan (events included) and
# wrote the whole plan back — so every ~2min ping rewrote the entire history.
#
# Three rules hold across the whole layer:
#
# 1. **The token check IS the WHERE clause.** `assert_claim_match` evaluated the
#    same predicate on a loaded dict; here it is `WHERE plan_slug = ? AND
#    claimed_by = ? AND phase_id = ?`, evaluated by the database inside the
#    write transaction. Zero rows updated means the claim moved under the
#    caller — a stale or forged worker — and that surfaces as `st.ClaimMismatch`
#    with the same message shapes, because the CLI decorator and the heartbeat
#    daemon's clean-exit branch both catch it by name.
# 2. **Every op bumps `plans.version`,** the per-plan change hint dashboards
#    poll on. Forgetting is not a per-op discipline: `_plan_txn` does the bump
#    itself, and the same statement doubles as the plan's existence check.
# 3. **Nothing slow runs inside the transaction.** No subprocess, no HTTP call,
#    no read of another database. Consolidating N state files into one database
#    consolidated their locks too, so an op that shelled out would hold the
#    whole PROJECT's write lock while it did. Callers that need to shell out
#    (coolant on release, git on archive) do it after the commit.


@contextmanager
def _plan_txn(
    orch_dir: Path,
    slug: str,
    *,
    timeout_s: float | None = None,
) -> Iterator[sqlite3.Cursor]:
    """One write transaction against one plan, with the version bump built in.

    The bump is the FIRST statement rather than the last, and it is also the
    existence check: `UPDATE plans SET version = version + 1 WHERE slug = ?`
    touches no rows when the plan is absent, which is exactly the
    `FileNotFoundError` its callers already catch. One statement, two jobs, and
    no way for an op to forget either.

    A few ops decide, mid-transaction, that they have nothing to write (a dedup
    guard already set, an unknown blocker) and the bump still lands. That is
    the harmless direction: the version is a hint that says "look again", and a
    reader that looks and finds nothing changed has lost one poll — where a
    missed bump makes a live plan read as idle.
    """
    st.validate_slug(slug, kind="plan slug")
    with (
        _write_conn(orch_dir, timeout_s=timeout_s) as conn,
        db.write_txn(conn, timeout_s=timeout_s) as cur,
    ):
        cur.execute("UPDATE plans SET version = version + 1 WHERE slug = ?", (slug,))
        if cur.rowcount == 0:
            raise _missing(orch_dir, slug)
        yield cur


# The compare-and-set that replaced `assert_claim_match`. Composed into every
# op that a worker token guards, so the predicate has one definition.
_CLAIM_CAS = "plan_slug = ? AND claimed_by = ? AND phase_id = ?"


def _claim_mismatch(
    cur: sqlite3.Cursor,
    slug: str,
    token: str | None,
    phase: str | None = None,
) -> st.ClaimMismatch:
    """Build the exception a failed compare-and-set deserves.

    The CAS says only "no row matched"; operators (and three test families)
    read WHICH part did not match. One extra SELECT of the claim row — on the
    failure path only — recovers the three messages `assert_claim_match`
    produced, verbatim.
    """
    row = cur.execute(
        "SELECT claimed_by, phase_id FROM claims WHERE plan_slug = ?",
        (slug,),
    ).fetchone()
    if row is None:
        return st.ClaimMismatch("no active claim")
    if row["claimed_by"] != token:
        return st.ClaimMismatch(f"token mismatch: claim is {row['claimed_by']!r}, got {token!r}")
    if phase is not None and row["phase_id"] != phase:
        return st.ClaimMismatch(f"phase mismatch: claim is {row['phase_id']!r}, got {phase!r}")
    # The row matches now but did not when the UPDATE ran — impossible inside
    # one transaction, so this is a bug guard rather than a race report.
    return st.ClaimMismatch("claim changed inside the write transaction")


def _release_in_txn(
    cur: sqlite3.Cursor,
    orch_dir: Path,
    slug: str,
    token: str | None,
    phase: str | None,
) -> dict | None:
    """Clear the claim row, validating the token+phase pair when both are given.

    Both-or-neither, exactly like `state.release_claim`: passing one piece is a
    programming error, because a caller either proves it owns the claim or
    declares that it is not validating at all.
    """
    if (token is None) != (phase is None):
        raise ValueError("release: token and phase must be passed together")
    claim = _read_claim(cur, slug)
    if token is not None and (
        claim is None or claim.get("claimed_by") != token or claim.get("phase_id") != phase
    ):
        raise _claim_mismatch(cur, slug, token, phase)
    cur.execute("DELETE FROM claims WHERE plan_slug = ?", (slug,))
    return claim


def op_heartbeat(
    orch_dir: Path,
    slug: str,
    *,
    token: str,
    phase: str,
    timeout_s: float | None = None,
) -> str:
    """Stamp `last_heartbeat_at` on the live claim. Returns the new timestamp.

    The hottest write in the fleet — every worker, every ~2 minutes — and the
    reason this phase exists. No event is appended (heartbeats would flood the
    log; the supervisor derives stalled state from this single field), and no
    event is READ either.
    """
    ts = st.utcnow()
    with _plan_txn(orch_dir, slug, timeout_s=timeout_s) as cur:
        cur.execute(
            f"UPDATE claims SET last_heartbeat_at = ? WHERE {_CLAIM_CAS}",
            (ts, slug, token, phase),
        )
        if cur.rowcount == 0:
            raise _claim_mismatch(cur, slug, token, phase)
    return ts


def op_activity(
    orch_dir: Path,
    slug: str,
    *,
    token: str,
    phase: str,
    action: str,
    timeout_s: float | None = None,
) -> bool:
    """Stamp (`start`) or clear (`end`) the active-tool window on the claim.

    Returns False when the write was DROPPED because the store was busy past
    `timeout_s` — the PreToolUse hook's contract: never freeze the worker's
    Bash call over a marker. A stale token is a different thing entirely and
    still raises `ClaimMismatch`.
    """
    if action not in ("start", "end"):
        raise ValueError(f"action must be 'start' or 'end', got {action!r}")
    value = st.utcnow() if action == "start" else None
    try:
        with _plan_txn(orch_dir, slug, timeout_s=timeout_s) as cur:
            cur.execute(
                f"UPDATE claims SET active_tool_started_at = ? WHERE {_CLAIM_CAS}",
                (value, slug, token, phase),
            )
            if cur.rowcount == 0:
                raise _claim_mismatch(cur, slug, token, phase)
    except db.DbBusy:
        return False
    return True


def op_stamp_claim_fields(
    orch_dir: Path,
    slug: str,
    *,
    token: str,
    fields: dict,
    phase: str | None = None,
    if_unset: str | None = None,
    events: list[dict] | None = None,
    timeout_s: float | None = None,
) -> bool:
    """Write arbitrary fields onto the live claim, compare-and-set on the token.

    Fields with a column of their own land in that column; everything else
    merges into the `flags` catch-all, the same split `_write_claim` makes, so
    a caller keeps writing the claim dict it always wrote.

    `phase` narrows the compare-and-set to token AND phase (the worker-callback
    shape); omitting it validates the token alone (the dispatcher's shape — it
    stamps the pid of a claim it just made and has no phase argument to hand).

    `if_unset` names a claim field that must be falsy for the write to happen:
    the dedup markers (`heartbeat_loop_failing_notified`) whose whole job is to
    make a second call a no-op. Returns False when that guard fires, True when
    the write landed. A claim that does not match the token raises rather than
    returning False — a guard hit and a forged token are not the same answer.
    """
    with _plan_txn(orch_dir, slug, timeout_s=timeout_s) as cur:
        row = cur.execute("SELECT * FROM claims WHERE plan_slug = ?", (slug,)).fetchone()
        if (
            row is None
            or row["claimed_by"] != token
            or (phase is not None and row["phase_id"] != phase)
        ):
            raise _claim_mismatch(cur, slug, token, phase)
        flags = json.loads(row["flags"]) if row["flags"] is not None else {}
        if if_unset is not None:
            current = flags.get(if_unset)
            if current is None and if_unset in _CLAIM_SCALARS:
                current = row[if_unset]
            if current:
                return False
        columns: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key in _CLAIM_SCALARS:
                columns.append(key)
                values.append(value)
            elif key in _CLAIM_JSON:
                columns.append(key)
                values.append(json.dumps(value))
            else:
                flags[key] = value
        if flags:
            columns.append("flags")
            values.append(json.dumps(flags))
        if columns:
            # Column names come from the two frozen whitelists above (plus the
            # literal "flags"), never from caller text.
            assignments = ", ".join(f"{col} = ?" for col in columns)
            cur.execute(
                f"UPDATE claims SET {assignments} WHERE plan_slug = ?",
                (*values, slug),
            )
        _insert_events(cur, slug, events)
    return True


def op_append_events(
    orch_dir: Path,
    slug: str,
    events: list[dict],
    *,
    token: str | None = None,
    phase: str | None = None,
    timeout_s: float | None = None,
) -> None:
    """Append events and nothing else. Ids are stamped back onto the dicts.

    `token`/`phase` add the claim compare-and-set the other worker-facing ops
    carry, for the callbacks whose whole effect IS an event: the worker-mode
    `queue add` records its append or its rejection on the SOURCE plan, and a
    caller with a stale token must be refused there exactly as it is on a
    complete or a spawn.
    """
    with _plan_txn(orch_dir, slug, timeout_s=timeout_s) as cur:
        if token is not None:
            row = cur.execute(
                "SELECT claimed_by, phase_id FROM claims WHERE plan_slug = ?",
                (slug,),
            ).fetchone()
            if (
                row is None
                or row["claimed_by"] != token
                or (phase is not None and row["phase_id"] != phase)
            ):
                raise _claim_mismatch(cur, slug, token, phase)
        _insert_events(cur, slug, events)


def op_release_claim(
    orch_dir: Path,
    slug: str,
    *,
    token: str | None = None,
    phase: str | None = None,
    events: list[dict] | None = None,
    timeout_s: float | None = None,
) -> dict | None:
    """Release the claim, optionally with the events that explain why.

    Returns the released claim so the caller can emit coolant from it AFTER the
    commit — the emit shells out, and nothing slow belongs inside a transaction
    that holds the project's write lock.
    """
    with _plan_txn(orch_dir, slug, timeout_s=timeout_s) as cur:
        released = _release_in_txn(cur, orch_dir, slug, token, phase)
        _insert_events(cur, slug, events)
    return released


def op_stamp_attestation(
    orch_dir: Path,
    slug: str,
    *,
    token: str | None,
    phase: str | None,
    kind: str,
    commit_sha: str,
    event: dict | None = None,
) -> None:
    """Stamp `attestations[kind]` on the live claim, with its event.

    `token=None` is the operator path: `clu verify` run by hand has no token
    and stamps whatever claim is live, exactly as it did through the facade.
    With no claim at all the two paths answer differently, and both answers are
    inherited: a token-bearing caller gets `ClaimMismatch` (there is nothing to
    match), and the operator gets the `ValueError` `state.stamp_attestation`
    raised (there is nothing to attest against).
    """
    with _plan_txn(orch_dir, slug) as cur:
        row = cur.execute(
            "SELECT claimed_by, phase_id, attestations FROM claims WHERE plan_slug = ?",
            (slug,),
        ).fetchone()
        if token is not None:
            if (
                row is None
                or row["claimed_by"] != token
                or (phase is not None and row["phase_id"] != phase)
            ):
                raise _claim_mismatch(cur, slug, token, phase)
        elif row is None:
            raise ValueError("stamp_attestation: no current_claim")
        attestations = json.loads(row["attestations"]) if row["attestations"] is not None else {}
        attestations[kind] = {"at": st.utcnow(), "commit_sha": commit_sha}
        cur.execute(
            "UPDATE claims SET attestations = ? WHERE plan_slug = ?",
            (json.dumps(attestations), slug),
        )
        _insert_events(cur, slug, [event] if event is not None else None)


def op_add_blocker(
    orch_dir: Path,
    slug: str,
    *,
    phase_id: str,
    question: str,
    options: list[str],
    context: str = "",
    blocker_type: str = st.BLOCKER_INPUT,
    release_token: str | None = None,
    release_phase: str | None = None,
) -> str:
    """Record a blocker (and its `phase_blocked` event). Returns the new id.

    `release_token`/`release_phase` release the claim in the SAME transaction,
    which is what `clu block` needs: a crash between "claim released" and
    "blocker recorded" would leave a claimless running plan the supervisor
    would happily redispatch, losing the worker's question.

    The id is minted inside the transaction from the row count — `q-{n+1}`,
    the same id the list-length expression produced — so two writers cannot
    mint the same one.
    """
    with _plan_txn(orch_dir, slug) as cur:
        if release_token is not None or release_phase is not None:
            _release_in_txn(cur, orch_dir, slug, release_token, release_phase)
        count = cur.execute(
            "SELECT COUNT(*) FROM blockers WHERE plan_slug = ?",
            (slug,),
        ).fetchone()[0]
        blocker_id = f"q-{int(count) + 1}"
        _write_blockers(
            cur,
            slug,
            [
                {
                    "id": blocker_id,
                    "phase_id": phase_id,
                    "type": blocker_type,
                    "question": question,
                    "options": list(options),
                    "context": context,
                    "asked_at": st.utcnow(),
                    "answer": None,
                    "answered_at": None,
                }
            ],
        )
        _insert_events(
            cur,
            slug,
            [
                {
                    "ts": st.utcnow(),
                    "type": st.EVENT_PHASE_BLOCKED,
                    "phase": phase_id,
                    "blocker_id": blocker_id,
                    "question": question,
                }
            ],
        )
    return blocker_id


def _resolve_answer(options_json: str | None, raw_answer: str) -> str:
    """`state.resolve_blocker_answer` against one blocker's options column."""
    if not str(raw_answer).isdigit():
        return raw_answer
    options = json.loads(options_json) if options_json is not None else []
    idx = int(raw_answer)
    if idx < len(options):
        return options[idx]
    return raw_answer


def op_answer_blocker(
    orch_dir: Path,
    slug: str,
    *,
    blocker_id: str,
    answer: str,
) -> str:
    """Answer the blocker and emit `blocker_answered`. Returns the stored text.

    The option-index translation happens INSIDE the transaction, against the
    options the row actually holds — a read-then-write done around the write
    would be answering from a snapshot. `answer` is therefore the RAW reply
    (an index or free text) and the return value is what was stored, which is
    what the operator surfaces print.

    Targets the first UNANSWERED blocker with this id (`answer IS NULL` in the
    WHERE clause), so a double reply raises `KeyError` instead of overwriting
    an answer the worker may already have acted on.
    """
    with _plan_txn(orch_dir, slug) as cur:
        row = cur.execute(
            "SELECT options FROM blockers WHERE plan_slug = ? AND blocker_id = ? "
            "AND answer IS NULL",
            (slug, blocker_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"no unanswered blocker {blocker_id}")
        resolved = _resolve_answer(row["options"], answer)
        cur.execute(
            "UPDATE blockers SET answer = ?, answered_at = ? "
            "WHERE plan_slug = ? AND blocker_id = ? AND answer IS NULL",
            (resolved, st.utcnow(), slug, blocker_id),
        )
        _insert_events(
            cur,
            slug,
            [
                {
                    "ts": st.utcnow(),
                    "type": st.EVENT_BLOCKER_ANSWERED,
                    "blocker_id": blocker_id,
                    "answer": resolved,
                }
            ],
        )
    return resolved


def op_stamp_blocker_metadata(
    orch_dir: Path,
    slug: str,
    *,
    blocker_id: str,
    channel: str,
    metadata: dict,
) -> None:
    """Record a notifier's correlation ids under `notify_metadata[channel]`.

    Merged rather than replaced: two channels can carry a blocker, and the
    Discord notifier writing its message id must not erase iMessage's. An
    unknown blocker is a no-op — the notifier's own contract, since a
    notification can outlive the plan it describes.
    """
    with _plan_txn(orch_dir, slug) as cur:
        row = cur.execute(
            "SELECT notify_metadata FROM blockers WHERE plan_slug = ? AND blocker_id = ?",
            (slug, blocker_id),
        ).fetchone()
        if row is None:
            return
        current = json.loads(row["notify_metadata"]) if row["notify_metadata"] is not None else {}
        current[channel] = metadata
        cur.execute(
            "UPDATE blockers SET notify_metadata = ? WHERE plan_slug = ? AND blocker_id = ?",
            (json.dumps(current), slug, blocker_id),
        )


def op_spawn_task(
    orch_dir: Path,
    slug: str,
    *,
    task: dict,
    status: str,
    token: str | None = None,
    phase: str | None = None,
) -> str:
    """Record a spawned task (and its `task_spawned` event). Returns the id.

    `task` carries the descriptive fields; the id is minted inside the
    transaction (`task-{n+1}`) for the same reason a blocker's is, which is
    also why the event is built here — the caller cannot name an id that does
    not exist yet.
    """
    with _plan_txn(orch_dir, slug) as cur:
        if token is not None:
            row = cur.execute(
                "SELECT claimed_by, phase_id FROM claims WHERE plan_slug = ?",
                (slug,),
            ).fetchone()
            if (
                row is None
                or row["claimed_by"] != token
                or (phase is not None and row["phase_id"] != phase)
            ):
                raise _claim_mismatch(cur, slug, token, phase)
        count = cur.execute(
            "SELECT COUNT(*) FROM spawned_tasks WHERE plan_slug = ?",
            (slug,),
        ).fetchone()[0]
        task_id = f"task-{int(count) + 1}"
        _write_tasks(cur, slug, [{**task, "id": task_id, "status": status}])
        _insert_events(
            cur,
            slug,
            [
                {
                    "ts": st.utcnow(),
                    "type": st.EVENT_TASK_SPAWNED,
                    "task": task_id,
                    "source": task.get("source"),
                    "spawned_by_phase": task.get("spawned_by_phase"),
                }
            ],
        )
    return task_id


def op_complete_task(
    orch_dir: Path,
    slug: str,
    *,
    task: str,
    token: str | None = None,
    phase: str | None = None,
    event: dict | None = None,
) -> None:
    """Flip a spawned task to done, with its event. Idempotent on an already-
    done task (no second event), `KeyError` on one that was never spawned.

    `token` is optional because `clu task-done --force` is the operator's
    manual-cleanup path and deliberately carries none.
    """
    with _plan_txn(orch_dir, slug) as cur:
        if token is not None:
            claim = cur.execute(
                "SELECT claimed_by, phase_id FROM claims WHERE plan_slug = ?",
                (slug,),
            ).fetchone()
            if (
                claim is None
                or claim["claimed_by"] != token
                or (phase is not None and claim["phase_id"] != phase)
            ):
                raise _claim_mismatch(cur, slug, token, phase)
        row = cur.execute(
            "SELECT status, payload FROM spawned_tasks WHERE plan_slug = ? AND task_id = ?",
            (slug, task),
        ).fetchone()
        if row is None:
            raise KeyError(f"no task {task!r}")
        if row["status"] == "done":
            return
        payload = json.loads(row["payload"]) if row["payload"] is not None else {}
        payload["completed_at"] = st.utcnow()
        cur.execute(
            "UPDATE spawned_tasks SET status = ?, payload = ? WHERE plan_slug = ? AND task_id = ?",
            ("done", json.dumps(payload), slug, task),
        )
        _insert_events(cur, slug, [event] if event is not None else None)


def op_set_status(
    orch_dir: Path,
    slug: str,
    *,
    status: str,
    event: dict | None = None,
    release_token: str | None = None,
    release_phase: str | None = None,
) -> None:
    """Flip the plan's status, with the event that explains it.

    `release_token`/`release_phase` release the claim in the same transaction —
    the dispatch-time pause shape, where a claim that outlived its failed
    dispatch and a plan left at `running` are two halves of one bug.
    """
    with _plan_txn(orch_dir, slug) as cur:
        if release_token is not None or release_phase is not None:
            _release_in_txn(cur, orch_dir, slug, release_token, release_phase)
        cur.execute("UPDATE plans SET status = ? WHERE slug = ?", (status, slug))
        _insert_events(cur, slug, [event] if event is not None else None)


def op_archive_events(orch_dir: Path, slug: str) -> int:
    """Move this plan's events into `events_archive`. Returns the row count.

    Fires ONLY on explicit archival — never on a halt or a pause, because a
    halted plan can be retried and a paused one resumed, and both read their
    own history to do it (`attempts_for_phase` counts `phase_started` rows).
    Ids are carried over rather than minted, so `clu state dump` can interleave
    the two tables back into one ordered history.
    """
    with _plan_txn(orch_dir, slug) as cur:
        cur.execute(
            "INSERT INTO events_archive (id, plan_slug, ts, type, payload, archived_at) "
            "SELECT id, plan_slug, ts, type, payload, ? FROM events WHERE plan_slug = ?",
            (st.utcnow(), slug),
        )
        moved = cur.rowcount
        cur.execute("DELETE FROM events WHERE plan_slug = ?", (slug,))
    return max(moved, 0)
