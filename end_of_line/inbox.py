"""Per-event inbox surfaced to active Claude Code sessions.

Pattern: clu writes one JSON file per event into `~/.config/clu/inbox/`.
The bundled UserPromptSubmit hook script
(`end_of_line.hooks.clu_inbox_surface`) reads the inbox at the start of
every Claude turn, filters to events whose `project_root` matches the
current working tree, formats them into a system reminder, and marks
each one processed by moving it into `inbox/processed/`.

Filenames carry an 8-char random hex suffix — collision-free under
concurrent writes from supervisor + worker callbacks without a global
counter.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

from . import state as st
from ._xdg_guard import assert_xdg_safe, clu_config_dir

SCHEMA_VERSION = 1


def inbox_root() -> Path:
    path = clu_config_dir() / "inbox"
    assert_xdg_safe(path)
    return path


def _processed_root(inbox_dir: Path) -> Path:
    return inbox_dir / "processed"


def _is_event_file(p: Path) -> bool:
    return not p.is_dir() and not p.name.startswith(".") and p.name.endswith(".json")


def write_event(
    *,
    type: str,
    plan_slug: str,
    project_root: str,
    summary: str,
    details: dict | None = None,
    inbox: Path | None = None,
) -> str:
    """Drop a single event file. Returns the event id."""
    inbox_dir = inbox or inbox_root()
    inbox_dir.mkdir(parents=True, exist_ok=True)
    event_id = f"evt-{secrets.token_hex(4)}"
    ts = st.utcnow()
    # Filename: <compact-ts>-<19-digit-ns>-<type>-<short>.json. The
    # nanosecond suffix makes filenames strictly monotonic under tight-loop
    # writes (the second-resolution `ts` ties otherwise), and the random
    # short id is the collision tiebreaker against `time.time_ns()` returning
    # the same value across processes.
    safe_ts = ts.replace(":", "").replace("-", "")
    short = event_id[-8:]
    filename = f"{safe_ts}-{time.time_ns():019d}-{type}-{short}.json"
    payload = {
        "id": event_id,
        "schema_version": SCHEMA_VERSION,
        "type": type,
        "plan_slug": plan_slug,
        "project_root": str(Path(project_root).resolve()),
        "timestamp": ts,
        "summary": summary,
        "details": details or {},
    }
    target = inbox_dir / filename
    tmp = inbox_dir / f".{filename}.tmp"
    tmp.write_text(json.dumps(payload, indent=2))
    os.rename(tmp, target)
    return event_id


def read_unprocessed(inbox: Path | None = None) -> list[dict]:
    """Return all event payloads in the inbox, sorted by timestamp ascending.

    Corrupt files are silently skipped — the surfacer must never crash
    on a malformed sibling.
    """
    inbox_dir = inbox or inbox_root()
    if not inbox_dir.exists():
        return []
    # Sort by filename — the embedded nanosecond suffix makes lexical
    # order equivalent to arrival order (the event's `timestamp` field
    # is second-resolution and ties under tight-loop writes).
    events: list[dict] = []
    for p in sorted(inbox_dir.iterdir(), key=lambda x: x.name):
        if not _is_event_file(p):
            continue
        try:
            events.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    return events


def mark_processed(event_id: str, inbox: Path | None = None) -> None:
    """Move the file with `id == event_id` into `processed/`.

    Idempotent: missing inbox, empty inbox, or unknown id all return
    silently — the surfacer should never propagate cleanup failures.
    """
    inbox_dir = inbox or inbox_root()
    if not inbox_dir.exists():
        return
    processed = _processed_root(inbox_dir)
    processed.mkdir(parents=True, exist_ok=True)
    for p in inbox_dir.iterdir():
        if not _is_event_file(p):
            continue
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if data.get("id") == event_id:
            os.rename(p, processed / p.name)
            return


def list_for_project(
    project_root: str,
    inbox: Path | None = None,
) -> list[dict]:
    """Return unprocessed events whose `project_root` matches `project_root`."""
    target = str(Path(project_root).resolve())
    return [e for e in read_unprocessed(inbox) if e.get("project_root") == target]
