"""`clu top` — read-only live view of what active workers are doing right now.

Phase 1 (this module's core): locate a worker's Claude Code session transcript,
tail it cheaply, and extract the activity fields the operator watches —
current/last Bash command, last file write, last assistant line, last-activity
time, token usage. Joined with claim state (phase, start time, heartbeat,
PID liveness) into a render-agnostic row dict.

Why the locator is careful: `~/.claude/projects/<enc>` encodes the worker's cwd
lossily (every non-ascii-alnum char -> '-', non-reversible), and one dir holds
many `<session-id>.jsonl` files (retries) plus separate `isSidechain` subagent
transcripts. So we forward-encode the known cwd to find the dir, then *confirm*
each candidate by its in-file `cwd` field and reject sidechains — never trust
the dir name or newest-mtime alone.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

from end_of_line import state as st

PROJECTS_ROOT = Path.home() / ".claude" / "projects"

# Tool names whose tool_use entry means "the worker wrote a file".
_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

# How many leading lines to scan for a session's identifying `cwd` record.
# Transcripts open with meta/snapshot records that carry no cwd; the cwd line
# is well within the first lines in practice. Bounded so a huge transcript is
# never read in full just to identify it.
_IDENTITY_SCAN_LINES = 200


def encode_project_dir(cwd: Path | str) -> str:
    """Encode an absolute cwd into its `~/.claude/projects/` subdir name.

    Mirrors Claude Code's transform (CC issue #19972): every character that
    isn't ASCII-alphanumeric or '-' becomes '-', leading slash included. This
    is lossy and non-reversible (`a_b`, `a-b`, `a.b` all collapse) — which is
    why callers must confirm a match via the in-file `cwd` field.
    """
    s = str(cwd)
    return "".join(c if ((c.isascii() and c.isalnum()) or c == "-") else "-" for c in s)


def _identity(path: Path) -> tuple[str | None, bool]:
    """Return (cwd, is_sidechain) read from the first cwd-bearing line.

    Transcripts open with meta records that carry no `cwd`; skip them until a
    line identifies the session. Returns (None, False) if no line does.
    """
    try:
        with open(path, errors="replace") as fh:
            for _ in range(_IDENTITY_SCAN_LINES):
                line = fh.readline()
                if not line:
                    break
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # A non-null cwd identifies the session; `cwd` and `isSidechain`
                # co-occur on the same record. Skip explicit-null cwd (meta
                # lines) rather than treating it as the answer.
                if rec.get("cwd"):
                    return rec.get("cwd"), bool(rec.get("isSidechain", False))
    except OSError:
        return None, False
    return None, False


def _confirms(path: Path, target_cwd: str) -> bool:
    """True iff `path` is a main-session transcript whose cwd is `target_cwd`."""
    file_cwd, is_sidechain = _identity(path)
    return file_cwd == target_cwd and not is_sidechain


def locate_transcript(
    cwd: Path | str,
    projects_root: Path = PROJECTS_ROOT,
    session_id: str | None = None,
) -> Path | None:
    """Best transcript file for a worker running in `cwd`, or None.

    With `session_id`, the filename is deterministic — return it if it exists.
    Otherwise glob the encoded dir, keep only main-session files whose in-file
    `cwd` matches, and return the most recently modified.
    """
    encoded = encode_project_dir(cwd)
    d = projects_root / encoded
    target = str(cwd)
    if session_id:
        # Deterministic filename, but still confirm cwd + reject sidechains —
        # a stale/misrouted id must not surface another session's activity.
        cand = d / f"{session_id}.jsonl"
        if cand.exists() and _confirms(cand, target):
            return cand
        return None
    if not d.is_dir():
        return None
    # A cwd dir accumulates many session files (retries) plus sidechain
    # transcripts. Order by mtime (a cheap stat, no read), then confirm lazily
    # and stop at the first main-session match — opens one file in the common
    # case where the active transcript is newest.
    candidates: list[tuple[float, Path]] = []
    for f in d.glob("*.jsonl"):
        try:
            candidates.append((f.stat().st_mtime, f))
        except OSError:
            continue
    for _mtime, f in sorted(candidates, reverse=True):
        if _confirms(f, target):
            return f
    return None


def tail_records(path: Path, want: int = 60) -> list[dict]:
    """Parse up to the last `want` JSON records from a (possibly growing) file.

    Reads a bounded tail from the end, tolerates a truncated/half-written final
    line (writer mid-append), and skips any line that doesn't parse.
    """
    chunk = 64 * 1024
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            buf = b""
            while pos > 0 and buf.count(b"\n") <= want:
                step = min(chunk, pos)
                pos -= step
                f.seek(pos, os.SEEK_SET)
                buf = f.read(step) + buf
    except OSError:
        return []
    lines = buf.split(b"\n")
    if pos > 0:
        # First slice may be a partial line cut by our window — drop it.
        lines = lines[1:]
    out: list[dict] = []
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        try:
            out.append(json.loads(s))
        except json.JSONDecodeError:
            continue
    # Cap on parsed records, so a trailing newline or skipped bad line never
    # eats into the `want` budget.
    return out[-want:]


def _content_blocks(message: dict) -> list[dict]:
    """Normalize `message.content` (string OR list) to a list of block dicts."""
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def extract_activity(records: list[dict]) -> dict:
    """Reduce transcript records (file order) to the latest activity signals.

    Defensive against schema drift: switches on `type`, ignores unknowns, and
    tolerates missing fields / string-or-array content.
    """
    last_command = last_write = last_write_ts = last_text = last_activity_ts = None
    tokens = None
    last_bash_id: str | None = None
    result_ids: set[str] = set()

    for rec in records:
        if not isinstance(rec, dict):
            continue
        ts = rec.get("timestamp")
        if ts:
            last_activity_ts = ts
        rtype = rec.get("type")
        message = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        if rtype == "assistant":
            usage = message.get("usage")
            if isinstance(usage, dict):
                tokens = usage
            for block in _content_blocks(message):
                btype = block.get("type")
                if btype == "text" and isinstance(block.get("text"), str):
                    last_text = block["text"]
                elif btype == "tool_use":
                    name = block.get("name")
                    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                    if name == "Bash":
                        last_command = inp.get("command")
                        last_bash_id = block.get("id")
                    elif name in _WRITE_TOOLS:
                        last_write = inp.get("file_path")
                        last_write_ts = ts
        elif rtype == "user":
            for block in _content_blocks(message):
                if block.get("type") == "tool_result" and block.get("tool_use_id"):
                    result_ids.add(block["tool_use_id"])

    return {
        "last_command": last_command,
        "command_running": last_bash_id is not None and last_bash_id not in result_ids,
        "last_write": last_write,
        "last_write_ts": last_write_ts,
        "last_text": last_text,
        "last_activity_ts": last_activity_ts,
        "tokens": tokens,
    }


def _age_seconds(ts: str | None, now: _dt.datetime | None = None) -> float | None:
    if not ts:
        return None
    try:
        then = st.parse_iso(ts)
    except ValueError:
        return None
    try:
        return ((now or _dt.datetime.now(_dt.UTC)) - then).total_seconds()
    except TypeError:
        # Foreign/naive timestamp (no offset) can't be compared to tz-aware
        # now — treat as unknown rather than crashing the whole view.
        return None


def human_age(seconds: float | None) -> str:
    """Compact age like top's TIME column: `5s`, `1m30s`, `1h01m`, `—`."""
    if seconds is None:
        return "—"
    secs = int(seconds)
    if secs < 0:
        secs = 0
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


def assemble_row(claim: dict, activity: dict, now: _dt.datetime | None = None) -> dict:
    """Join one claim's state with its transcript activity into a render row.

    PID liveness uses the cheap kill-probe (no cmdline_match -> no `ps`), so a
    dead worker is flagged rather than shown as quietly idle.
    """
    ran = _age_seconds(claim.get("started_at"), now)
    hb = st.heartbeat_age_seconds(claim, now)
    return {
        "phase_id": claim.get("phase_id"),
        "ran_seconds": ran,
        "heartbeat_age_seconds": hb,
        "alive": st.claim_worker_alive(claim),
        "last_command": activity.get("last_command"),
        "command_running": activity.get("command_running", False),
        "last_write": activity.get("last_write"),
        "last_write_seconds": _age_seconds(activity.get("last_write_ts"), now),
        "last_text": activity.get("last_text"),
        "last_activity_seconds": _age_seconds(activity.get("last_activity_ts"), now),
        "tokens": activity.get("tokens"),
    }
