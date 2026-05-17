"""iMessage inbound poller — IMessageInboundPoller + chat.db helpers.

All iMessage-specific inbound logic lives here. notify_inbound.py is a thin
shim that re-exports this module's surface and provides the __main__ entry.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable

from . import registry, state as st
from .config import load_project_config
from .notify_base import OpenBlocker, Reply, route_reply

DEFAULT_CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
DEFAULT_SEEN_PATH = Path.home() / ".clu" / "seen_msg_rowid"
DEFAULT_POLL_SECONDS = 4
POLL_BATCH_LIMIT = 500

Dispatcher = Callable[[OpenBlocker, str], None]
OpenBlockersFn = Callable[[], list[OpenBlocker]]
TickSpawner = Callable[[Path, str], None]


def open_blockers_for_host(
    entries: Iterable[registry.PlanEntry],
) -> list[OpenBlocker]:
    """Walk registry → state files → first open blocker per plan.

    Tolerant of missing state files and unreadable JSON: a stale registry
    entry must not be able to take the poller down.
    """
    out: list[OpenBlocker] = []
    for row in entries:
        data = registry.load_entry_state(row)
        if data is None:
            continue
        open_qs = st.open_blockers(data)
        if not open_qs:
            continue
        first = open_qs[0]
        last_notified = ""
        for evt in reversed(data["events"]):
            if evt.get("type") == st.EVENT_PHASE_BLOCKED:
                last_notified = evt.get("ts", "")
                break
        out.append(OpenBlocker(
            project_root=Path(row.project_root),
            plan_slug=row.plan_slug,
            blocker_id=first["id"],
            options_count=len(first.get("options", [])),
            last_notified_at=last_notified,
        ))
    return out


def _cli_dispatch(target: OpenBlocker, answer: str) -> None:
    """Fire `clu answer` as a subprocess. Raises CalledProcessError on rc!=0
    so callers know whether to auto-tick; the outer poll loop catches it so a
    bad `clu answer` can't tank the poller."""
    subprocess.run(
        [
            sys.executable, "-m", "end_of_line.cli", "answer",
            "--project", str(target.project_root),
            "--plan", target.plan_slug,
            target.blocker_id, answer,
        ],
        check=True,
    )


def _spawn_tick(project_root: Path, plan_slug: str) -> None:
    """Fire-and-forget `clu tick` for the plan whose blocker was just
    answered, so the next phase dispatches immediately instead of waiting for
    the next cron firing. Mirrors `dispatch.py`'s Popen pattern."""
    subprocess.Popen(
        [
            sys.executable, "-m", "end_of_line.cli", "tick",
            "--project", str(project_root),
            "--plan", plan_slug,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def poll_once(
    conn: sqlite3.Connection,
    last_rowid: int,
    *,
    open_blockers_fn: OpenBlockersFn,
    dispatcher: Dispatcher = _cli_dispatch,
    tick_spawner: TickSpawner = _spawn_tick,
) -> int:
    """Scan chat.db for inbound rows after `last_rowid`. Returns new high-water.

    Always advances the high-water past every row we read, matched or
    not — otherwise a chatty stranger could keep the cursor stuck on an
    old digit-shaped message and resurrect it once a future blocker
    opens.
    """
    # LIMIT caps first-tick blowup (e.g. seen_rowid=0 against a chat.db with
    # a year of history); next tick advances the cursor and picks up the rest.
    rows = conn.execute(
        "SELECT ROWID, text FROM message "
        "WHERE ROWID > ? AND is_from_me = 0 AND text IS NOT NULL "
        "ORDER BY ROWID ASC LIMIT ?",
        (last_rowid, POLL_BATCH_LIMIT),
    ).fetchall()
    if not rows:
        return last_rowid
    # Open-blocker set can't change mid-poll (the poller holds no claim),
    # so resolve it once instead of per-row.
    blockers = open_blockers_fn()
    for _rowid, text in rows:
        match = route_reply(text, blockers)
        if match is None:
            continue
        target, answer = match
        try:
            dispatcher(target, answer)
        except Exception as exc:
            # `clu answer` failed — don't auto-tick on stale state. The cursor
            # still advances so a wedged reply can't re-fire forever.
            print(f"notify_inbound: dispatch failed: {exc}", file=sys.stderr)
            continue
        if not _auto_tick_enabled(target.project_root):
            continue
        try:
            tick_spawner(target.project_root, target.plan_slug)
        except Exception as exc:
            # Auto-tick is a latency optimization, not a correctness boundary;
            # cron will pick up the answered blocker on the next firing.
            print(f"notify_inbound: tick spawn failed: {exc}", file=sys.stderr)
    return rows[-1][0]


def _auto_tick_enabled(project_root: Path) -> bool:
    """Resolve the per-project opt-out. Defaults True; config errors fall back
    to True so a malformed `.orchestrator.json` doesn't silently disable UX."""
    try:
        return load_project_config(project_root).notify.inbound_auto_tick
    except Exception:
        return True


def read_seen(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(path.read_text().strip() or "0")
    except (OSError, ValueError):
        return 0


def write_seen(path: Path, rowid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(str(rowid))
    tmp.replace(path)


def open_chat_db(db_path: Path = DEFAULT_CHAT_DB) -> sqlite3.Connection:
    """Open chat.db read-only via SQLite URI — never widen this mode."""
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


class IMessageInboundPoller:
    def __init__(
        self,
        db_path: Path | None = None,
        registry_loader: Callable | None = None,
    ) -> None:
        self._db_path = db_path or DEFAULT_CHAT_DB
        self._registry_loader = registry_loader or registry.entries
        self._conn: sqlite3.Connection | None = None
        self._last_rowid: int | None = None  # cached; read from disk once on first poll

    def poll(self) -> list[Reply]:
        """Run one poll iteration; dispatches matched replies internally.

        Returns [] always — dispatch happens inside poll_once so the
        Protocol return type is satisfied without duplicating dispatch logic.
        Phase 2+ can refactor to actual reply collection when a shared
        dispatch loop exists.
        """
        if self._conn is None:
            self._conn = open_chat_db(self._db_path)
        if self._last_rowid is None:
            self._last_rowid = read_seen(DEFAULT_SEEN_PATH)
        new_last = poll_once(
            self._conn, self._last_rowid,
            open_blockers_fn=lambda: open_blockers_for_host(self._registry_loader()),
        )
        if new_last != self._last_rowid:
            write_seen(DEFAULT_SEEN_PATH, new_last)
            self._last_rowid = new_last
        return []
