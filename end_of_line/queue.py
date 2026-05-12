"""Per-project plan queue.

clu's cron tick advances *phases within* a plan, but inter-plan
transitions need someone to invoke `clu init` for the next plan. The
queue holds that list so an operator can scribble plans, queue them,
walk away, and wake up to a drained chain.

Storage lives next to per-plan state files at
`<plan_dir>/.orchestrator/queue.json`. See `docs/contract.md` for the
schema and `.claude/plans/plan-queue-master.md` for the design pass.

Auto-repair: when load() raises (catastrophic JSON / schema corruption),
the supervisor backs up the original bytes and optionally dispatches a
headless Claude repair worker. The worker's output runs through
`validate_repair`, which is the load-bearing safety boundary —
slug-preservation is verified by clu's regex-extracted backup slugs,
NOT by trusting the worker's prompt.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import state as st

SCHEMA_VERSION = 1

# Regex over raw bytes so we can extract slug data from a backup whose
# JSON is otherwise unparseable (a truncated string is enough to break
# json.loads but the earlier slug values usually survive intact).
_SLUG_RE = re.compile(rb'"slug"\s*:\s*"([^"]+)"')
_HISTORY_RE = re.compile(rb'"history"\s*:\s*\[', re.DOTALL)


def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION, "queue": [], "history": []}


def load(path: Path) -> dict:
    return st.load(path, expected_version=SCHEMA_VERSION)


def save_atomic(path: Path, data: dict) -> None:
    st.save_atomic(path, data)


def mutate(path: Path):
    """lock + load + yield-for-mutation + atomic write. Tolerates a missing
    file (first `queue add` creates it) via `state.locked_json`."""
    return st.locked_json(path, expected_version=SCHEMA_VERSION, empty=_empty)


def best_effort_extract_slugs(data: bytes) -> set[str]:
    """All `"slug": "..."` matches in the raw bytes (pending + history).

    Best-effort: the regex is double-quote / case-sensitive and won't
    detect slugs whose preceding string was truncated mid-byte. Catches
    catastrophic loss, not surgical corruption. Use the difference
    against `best_effort_extract_history_slugs` to isolate the pending
    queue's slugs.
    """
    return {m.decode("utf-8", errors="replace") for m in _SLUG_RE.findall(data)}


def best_effort_extract_history_slugs(data: bytes) -> set[str]:
    """Slugs appearing inside the `"history": [ ... ]` array of the bytes.

    Locates the substring starting after `"history": [` and scans to a
    matching `]` (bracket-count over JSON-escaped strings — good enough
    for the well-formed-prefix-then-garbage failure mode the worker most
    commonly produces). Returns an empty set when the history block
    can't be found.
    """
    m = _HISTORY_RE.search(data)
    if not m:
        return set()
    start = m.end()
    depth = 1
    i = start
    in_str = False
    escape = False
    while i < len(data) and depth > 0:
        c = data[i:i+1]
        if escape:
            escape = False
        elif in_str:
            if c == b'\\':
                escape = True
            elif c == b'"':
                in_str = False
        else:
            if c == b'"':
                in_str = True
            elif c == b'[':
                depth += 1
            elif c == b']':
                depth -= 1
        i += 1
    history_bytes = data[start:i-1]
    return {m.decode("utf-8", errors="replace") for m in _SLUG_RE.findall(history_bytes)}


@dataclass
class ValidationResult:
    ok: bool
    reason: str | None = None


def validate_repair(backup_bytes: bytes, repaired_path: Path) -> ValidationResult:
    """Run the hard slug-preservation rules. ok=False ⇒ caller MUST revert.

    Checks (in order):
      1. Repaired file re-loads cleanly. Otherwise the worker handed us
         more garbage.
      2. Every pending slug we could regex out of the backup is present
         in the repaired queue. The set diff is the user-visible reason.
      3. Every history slug from the backup is still in history. History
         is append-only — the worker may add but never remove.

    The "empty queue when original non-empty" case is subsumed by (2) —
    the missing-slugs set will be non-empty.
    """
    try:
        repaired = load(repaired_path)
    except (FileNotFoundError, OSError) as exc:
        return ValidationResult(False, f"still unparseable: {exc}")
    except (json.JSONDecodeError, st.SchemaVersionMismatch) as exc:
        return ValidationResult(False, f"still unparseable: {exc}")

    if not isinstance(repaired.get("queue"), list) or not isinstance(
        repaired.get("history"), list
    ):
        return ValidationResult(False, "repaired file missing queue/history arrays")

    backup_all = best_effort_extract_slugs(backup_bytes)
    backup_history = best_effort_extract_history_slugs(backup_bytes)
    backup_pending = backup_all - backup_history

    repaired_queue_slugs = {e.get("slug") for e in repaired["queue"]}
    missing_pending = backup_pending - repaired_queue_slugs
    if missing_pending:
        return ValidationResult(
            False, f"would drop slugs: {sorted(missing_pending)}"
        )

    repaired_history_slugs = {e.get("slug") for e in repaired["history"]}
    missing_history = backup_history - repaired_history_slugs
    if missing_history:
        return ValidationResult(
            False, f"history entries removed: {sorted(missing_history)}"
        )

    return ValidationResult(True)


def read_throttle(throttle_path: Path, diagnosis_hash: str) -> int:
    """Attempts recorded for `diagnosis_hash`. 0 on any read failure.

    A corrupt or mismatched-hash throttle file resets to 0 — we don't
    want a "repair-the-throttle" sub-failure mode. The cost of an extra
    repair attempt is far less than the cost of getting stuck because a
    counter file went bad.
    """
    try:
        data = json.loads(throttle_path.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return 0
    if data.get("diagnosis_hash") != diagnosis_hash:
        return 0
    try:
        return int(data.get("attempts", 0))
    except (TypeError, ValueError):
        return 0


def increment_throttle(throttle_path: Path, diagnosis_hash: str) -> None:
    """Bump (or initialize) the per-hash attempt counter."""
    attempts = read_throttle(throttle_path, diagnosis_hash) + 1
    payload = {
        "attempts": attempts,
        "last_at": st.utcnow(),
        "diagnosis_hash": diagnosis_hash,
    }
    throttle_path.parent.mkdir(parents=True, exist_ok=True)
    throttle_path.write_text(json.dumps(payload))


def reset_throttle(throttle_path: Path) -> None:
    """Drop the throttle file. Successful repair → next failure starts fresh."""
    try:
        throttle_path.unlink()
    except FileNotFoundError:
        pass
