"""Inbound iMessage poller — dispatches replies to `clu answer`.

Cron handles outbound, but inbound needs a long-lived process: chat.db
has no API, only the on-disk file. The LaunchAgent template at
`examples/clu.inbound.plist` keeps this script alive while the user is
logged in.

Reply grammar (locked, see render_blocker for the user-facing prompt):

    ^\\s*(?:<plan-slug>\\s+)?[0-9]\\s*$

A bare digit is only honored when exactly one plan on the host has an
open blocker; with more than one we refuse to guess and force the user
to disambiguate with the slug prefix. The render_blocker hint already
nudges them toward `<plan-slug> <number>`, so this is the lower-surprise
default.
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import registry, state as st
from .config import load_project_config

DEFAULT_CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
DEFAULT_SEEN_PATH = Path.home() / ".clu" / "seen_msg_rowid"
DEFAULT_POLL_SECONDS = 4
POLL_BATCH_LIMIT = 500

REPLY_RE = re.compile(rf"^\s*(?:({st.SLUG_PATTERN})\s+)?([0-9])\s*$")


@dataclass(frozen=True)
class OpenBlocker:
    project_root: Path
    plan_slug: str
    blocker_id: str  # q-N
    options_count: int
    last_notified_at: str  # ISO ts of most recent EVENT_PHASE_BLOCKED, "" if none


Dispatcher = Callable[["OpenBlocker", str], None]
OpenBlockersFn = Callable[[], list["OpenBlocker"]]
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


def route_reply(
    text: str, open_blockers: list[OpenBlocker],
) -> tuple[OpenBlocker, str] | None:
    """Return (target, option-index-str) if `text` resolves to a single blocker.

    Bare-digit replies with multiple open blockers route to the
    most-recently-pinged plan whose blocker has that digit in range
    (issue #3). Slug-prefixed replies always win. Returns None when the
    text doesn't match the grammar, the slug is unknown, no plan has a
    valid index for the digit, or the top two candidates tie on ping ts.
    """
    m = REPLY_RE.match(text)
    if not m:
        return None
    slug, digit = m.group(1), m.group(2)
    if slug:
        for ob in open_blockers:
            if ob.plan_slug == slug:
                return ob, digit
        return None
    if not open_blockers:
        return None
    if len(open_blockers) == 1:
        return open_blockers[0], digit
    picked = _pick_by_last_pinged(open_blockers, digit)
    return (picked, digit) if picked else None


def _pick_by_last_pinged(
    open_blockers: list[OpenBlocker], digit: str,
) -> OpenBlocker | None:
    """Bare-digit ambiguity → most-recently-pinged plan with the digit in range.

    Filters to plans where `digit` is a valid option index, then picks the
    unique max-`last_notified_at`. Returns None if no plan is eligible or
    the top two tie on timestamp (refuse rather than silently misroute).
    """
    idx = int(digit)
    eligible = [b for b in open_blockers if idx < b.options_count]
    if not eligible:
        return None
    eligible.sort(key=lambda b: b.last_notified_at, reverse=True)
    if len(eligible) >= 2 and eligible[0].last_notified_at == eligible[1].last_notified_at:
        return None
    return eligible[0]


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
    """Fire-and-forget `clu tick --dispatch` for the plan whose blocker was just
    answered, so the next phase dispatches immediately instead of waiting for
    the next cron firing. Mirrors `dispatch.py`'s Popen pattern."""
    subprocess.Popen(
        [
            sys.executable, "-m", "end_of_line.cli", "tick",
            "--project", str(project_root),
            "--plan", plan_slug,
            "--dispatch",
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


def main(argv: list[str] | None = None) -> int:
    db_path = DEFAULT_CHAT_DB
    seen_path = DEFAULT_SEEN_PATH
    if not db_path.exists():
        print(f"notify_inbound: chat.db not found at {db_path}", file=sys.stderr)
        return 1
    conn = open_chat_db(db_path)
    last = read_seen(seen_path)
    while True:
        try:
            new_last = poll_once(
                conn, last,
                open_blockers_fn=lambda: open_blockers_for_host(registry.entries()),
            )
            if new_last != last:
                write_seen(seen_path, new_last)
                last = new_last
        except Exception as exc:
            print(f"notify_inbound: poll error: {exc}", file=sys.stderr)
        time.sleep(DEFAULT_POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main() or 0)
