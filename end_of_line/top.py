"""`clu top` — read-only live view of what active workers are doing right now.

Phase 1 (this module's core): locate a worker's Claude Code session transcript,
tail it cheaply, and extract the activity fields the operator watches —
current/last Bash command, last file write, last assistant line, last-activity
time, token usage. Joined with claim state (phase, start time, heartbeat,
PID liveness) into a render-agnostic row dict.

Why the locator is careful: `~/.claude/projects/<enc>` encodes the worker's cwd
lossily (every non-ascii-alnum char -> '-', non-reversible), and one dir holds
many `<session-id>.jsonl` files (retries). On current Claude Code (v2.1.174)
subagent transcripts moved out to a per-session `<sid>/subagents/` subdir, so
the non-recursive `*.jsonl` glob already excludes them; the `isSidechain`
rejection in `_confirms` stays as belt-and-suspenders against an older layout.
So we forward-encode the known cwd to find the dir, then *confirm* each
candidate by its in-file `cwd` field and reject sidechains — never trust the dir
name or newest-mtime alone.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sys
import textwrap
from pathlib import Path

from end_of_line import registry
from end_of_line import state as st

PROJECTS_ROOT = Path.home() / ".claude" / "projects"

# Tool names whose tool_use entry means "the worker wrote a file".
_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

# Tool names that spawn a subagent (a /code-review fan-out, an Explore, etc.).
# Both spellings exist across Claude Code versions — match either. This is the
# one place to extend if the tool is renamed again.
_AGENT_TOOLS = frozenset({"Agent", "Task"})

# How many leading lines to scan for a session's identifying `cwd` record.
# Transcripts open with meta/snapshot records that carry no cwd; the cwd line
# is well within the first lines in practice. Bounded so a huge transcript is
# never read in full just to identify it.
_IDENTITY_SCAN_LINES = 200

# A non-clu session is shown only while its transcript is "live": modified
# within this window. No reliable end-of-session marker exists in the JSONL
# (CC #27361), so mtime is the only liveness signal; 300s matches the community
# idle threshold (claude-code-trace SESSION_IDLE_THRESHOLD_SECONDS).
SESSION_FRESH_SECONDS = 300


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
        # Exact file absent (launch window before the transcript appears, or an
        # unexpected filename) — fall through to cwd-matching rather than
        # reporting no activity for a live worker.
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
        raw_message = rec.get("message")
        message = raw_message if isinstance(raw_message, dict) else {}
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
                    raw_input = block.get("input")
                    inp = raw_input if isinstance(raw_input, dict) else {}
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


def _remaining_seconds(ts: str | None, now: _dt.datetime | None = None) -> float | None:
    """Seconds until `ts` (negative once `ts` is in the past). None when the
    timestamp is missing or unparseable. The mirror of `_age_seconds`, which
    measures elapsed time; this measures time left (the lease countdown)."""
    if not ts:
        return None
    try:
        then = st.parse_iso(ts)
    except ValueError:
        return None
    try:
        return (then - (now or _dt.datetime.now(_dt.UTC))).total_seconds()
    except TypeError:
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


def human_remaining(seconds: float | None) -> str:
    """Lease countdown: `12m00s` left, `exp` once past, `—` when unknown. Shared
    by `format_detail` (detail pane) and `top_registry`'s `lease` metric so the
    two render the countdown identically."""
    if seconds is None:
        return "—"
    if seconds < 0:
        return "exp"
    return human_age(seconds)


def _base_row(activity: dict, now: _dt.datetime | None = None) -> dict:
    """The D10 activity-key block shared by every row type — worker, blocked,
    session. The single source of truth for the seven transcript-derived keys,
    so a new row type can't hand-copy (and drift) the wire contract; callers
    `.update()` their own discriminator/claim keys on top. An empty `activity`
    dict collapses to the None/False defaults a claimless row needs."""
    return {
        "last_command": activity.get("last_command"),
        "command_running": activity.get("command_running", False),
        "last_write": activity.get("last_write"),
        "last_write_seconds": _age_seconds(activity.get("last_write_ts"), now),
        "last_text": activity.get("last_text"),
        "last_activity_seconds": _age_seconds(activity.get("last_activity_ts"), now),
        "tokens": activity.get("tokens"),
    }


def assemble_row(claim: dict, activity: dict, now: _dt.datetime | None = None) -> dict:
    """Join one claim's state with its transcript activity into a render row.

    PID liveness uses the cheap kill-probe (no cmdline_match -> no `ps`), so a
    dead worker is flagged rather than shown as quietly idle.
    """
    # Single literal with `_base_row` spliced at the original key position, so
    # the D10 wire-contract order (claim keys, then the 7 activity keys, then
    # the new-metrics keys) is byte-for-byte preserved — `_base_row` dedupes the
    # activity block without reordering the JSON `clu serve` emits.
    return {
        "phase_id": claim.get("phase_id"),
        "ran_seconds": _age_seconds(claim.get("started_at"), now),
        "heartbeat_age_seconds": st.heartbeat_age_seconds(claim, now),
        "alive": st.claim_worker_alive(claim),
        **_base_row(activity, now),
        # Phase 4 (new-metrics): claim-derived signals for the fused health
        # glyph + attempts/lease metrics. Append-only (D10) — surfaced to
        # web/index.html's toView so `clu serve` reads the same keys.
        "attempts": claim.get("attempts"),
        "lease_remaining_seconds": _remaining_seconds(claim.get("lease_expires"), now),
        "stuck": claim.get("stuck_tool_emitted_at") is not None,
    }


def assemble_blocked_row(blocker: dict, now: _dt.datetime | None = None) -> dict:
    """The claimless counterpart of `assemble_row`: a plan waiting on the
    operator. `clu block` releases the claim (so there's no live worker), but
    the blocker persists in `data["blockers"]` — this reads it back into a row.

    Same flat schema as a claim row (D10) so both renderers and `clu serve`
    read identical keys; the claim-only fields are `None` (no PID, no lease, no
    transcript activity). The three append-only keys `blocked`/
    `blocker_question`/`blocked_seconds` discriminate it. `alive=False`, but the
    `blocked` flag must be checked BEFORE the dead path in every render surface —
    a blocked plan is needs-you, not work-died.
    """
    # `_base_row({})` (claimless: no transcript activity -> all defaults)
    # spliced at the original position keeps the D10 key order exactly as the
    # prior inline dict had it.
    return {
        "phase_id": blocker.get("phase_id"),
        "ran_seconds": None,
        "heartbeat_age_seconds": None,
        "alive": False,
        **_base_row({}, now),
        "attempts": None,
        "lease_remaining_seconds": None,
        "stuck": False,
        # Append-only blocked discriminator (D10) — mirrored into toView.
        "blocked": True,
        "blocker_question": blocker.get("question"),
        "blocked_seconds": _age_seconds(blocker.get("asked_at"), now),
    }


def _session_name(records: list[dict], *, project: str, session_id: str) -> str:
    """Best display name from a session's tail records: a user-set `customTitle`
    beats the auto-generated `aiTitle` beats the latest prompt; falls back to
    `<project>:<short-id>` when no title record is in the read window. (A title
    emitted before the tail window falls back to the prompt / short-id — a
    display nicety, not a correctness concern.) Record shapes are sidecar types
    keyed by session, empirically present on CC v2.1.174."""
    custom = ai = last_prompt = None
    for rec in records:
        if not isinstance(rec, dict):
            continue
        rtype = rec.get("type")
        if rtype == "custom-title" and rec.get("customTitle"):
            custom = rec["customTitle"]
        elif rtype == "ai-title" and rec.get("aiTitle"):
            ai = rec["aiTitle"]
        elif rtype == "last-prompt" and rec.get("lastPrompt"):
            last_prompt = rec["lastPrompt"]
    return custom or ai or last_prompt or f"{project}:{session_id[:8]}"


def assemble_session_row(
    session_id: str, name: str, activity: dict, now: _dt.datetime | None = None
) -> dict:
    """A non-clu Claude session row: transcript activity with no claim, phase, or
    lease. Carries the FULL D10 key set a worker/blocked row has (so both render
    surfaces and `clu serve` read identical keys and a `row[...]` subscript never
    KeyErrors) — every claim/plan-derived field zero-filled, the way
    `assemble_blocked_row` zero-fills — plus the append-only `session`/
    `session_name`/`session_id` discriminators. `alive` is `None`: the PID
    liveness probe doesn't apply to a passively-discovered session. The render
    surfaces must branch on `session` BEFORE the dead path (same rule as
    `blocked`); that routing lands in the `classify` phase — until then a session
    row renders with the generic dead/`—` cells."""
    return {
        "phase_id": None,
        "ran_seconds": None,
        "heartbeat_age_seconds": None,
        "alive": None,
        **_base_row(activity, now),
        "attempts": None,
        "lease_remaining_seconds": None,
        "stuck": False,
        # Plan-config-derived D10 keys gather_rows sets for worker/blocked rows;
        # a session has no plan, so they're None — but present, so the wire
        # contract holds for every row type.
        "max_attempts": None,
        "phase_total": None,
        "phase_index": None,
        # Append-only session discriminator (D10) — mirrored into toView.
        "session": True,
        "session_name": name,
        "session_id": session_id,
    }


def gather_session_rows(
    roots: set[str],
    *,
    projects_root: Path = PROJECTS_ROOT,
    now: _dt.datetime | None = None,
    claimed_paths: set[Path] | None = None,
    claimed_sids: set[str] | None = None,
) -> list[dict]:
    """One row per fresh, non-claim Claude session in a registered project's
    transcript dir. A session qualifies when it's a main-session transcript (not
    a sidechain) whose in-file cwd is the project root (`_confirms`, the same
    check the worker locator uses), it was modified within `SESSION_FRESH_SECONDS`,
    and it isn't already a live worker's transcript. Dedup is by the worker's
    resolved transcript PATH (`claimed_paths`) first — robust even when the
    dispatch template omits `{session_id}` so the claim carries none — and by
    `claimed_sids` as a belt. `roots` is the caller's already-project-filtered set
    of registered root strings (gather_rows hands them over so the registry is
    scanned once). Freshest first; per-file tolerant — an odd/unreadable file is
    skipped, never raised."""
    claimed_paths = claimed_paths or set()
    claimed_sids = claimed_sids or set()
    now_ts = (now or _dt.datetime.now(_dt.UTC)).timestamp()
    scored: list[tuple[float, dict]] = []
    for root in roots:
        d = projects_root / encode_project_dir(root)
        if not d.is_dir():
            continue
        for f in d.glob("*.jsonl"):
            if f in claimed_paths or f.stem in claimed_sids:
                continue
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            if now_ts - mtime >= SESSION_FRESH_SECONDS:
                continue
            if not _confirms(f, root):  # main-session transcript whose cwd is root
                continue
            records = tail_records(f)
            project = Path(root).name
            row = assemble_session_row(
                f.stem, _session_name(records, project=project, session_id=f.stem),
                extract_activity(records), now=now,
            )
            row["plan"] = None
            row["project"] = project
            scored.append((mtime, row))
    scored.sort(key=lambda mr: mr[0], reverse=True)  # freshest first
    return [r for _m, r in scored]


def gather_rows(
    *,
    projects_root: Path = PROJECTS_ROOT,
    now: _dt.datetime | None = None,
    project_filter: Path | None = None,
) -> list[dict]:
    """One row per active claim — plus a claimless 'blocked' row for any plan
    waiting on the operator — across every registered plan (optionally scoped to
    `project_filter`). Tolerant at the per-plan level: a plan with no live claim
    AND no open blocker, or whose state / transcript can't be read, contributes
    nothing rather than raising. A corrupt host registry is not swallowed — it
    surfaces rather than masquerading as an empty dashboard.

    Three stable tiers: blocked plans sort to the top (a plan waiting on the
    operator is the single most actionable state), then running/dead clu workers
    in registry order, then non-clu Claude sessions (`gather_session_rows`) —
    fresh transcripts in registered projects that aren't a live worker.
    """
    rows: list[dict] = []
    roots: set[str] = set()  # the project-filtered roots, handed to the session scan
    claimed_paths: set[Path] = set()  # a live worker's own transcript -> not a session
    claimed_sids: set[str] = set()  # belt: dedup by claim session id where present
    for e in registry.entries():
        if project_filter is not None and (
            Path(e.project_root).resolve() != Path(project_filter).resolve()
        ):
            continue
        roots.add(e.project_root)
        data = registry.load_entry_state(e)
        if not data:
            continue
        claim = data.get("current_claim")
        if claim:
            if claim.get("session_id"):
                claimed_sids.add(claim["session_id"])
            wt = st.get_worktree(data)
            cwd = Path(wt["path"]) if wt and wt.get("path") else Path(e.project_root)
            tpath = locate_transcript(
                cwd, projects_root=projects_root, session_id=claim.get("session_id")
            )
            if tpath:
                claimed_paths.add(tpath)  # the worker's own transcript, by path
            records = tail_records(tpath) if tpath else []
            row = assemble_row(claim, extract_activity(records), now=now)
        else:
            # No live claim: `clu block` released it, but an open blocker means
            # the plan is waiting on the operator. Surface the first (primary)
            # open blocker as a claimless blocked row. Claim AND open blocker
            # can't co-occur (block releases the claim first), so the claim
            # branch always wins above.
            blockers = st.open_blockers(data)
            if not blockers:
                continue
            row = assemble_blocked_row(blockers[0], now=now)
        row["plan"] = e.plan_slug
        row["project"] = Path(e.project_root).name
        # Plan-config-derived keys (only gather_rows has `data`): the attempts
        # ceiling (resolved exactly as supervisor.py:761) and phase X-of-N from
        # the sessions index. Append-only (D10), mirrored into toView. Computed
        # the same way for claim and blocked rows — a blocked row still shows
        # WHERE in the plan it's stuck.
        row["max_attempts"] = data.get("config", {}).get(
            "max_attempts_per_phase", st.DEFAULT_MAX_ATTEMPTS
        )
        phases = data.get("phases", [])
        ids = [p.get("id") for p in phases]
        phase_id = row.get("phase_id")
        row["phase_total"] = len(phases) or None
        row["phase_index"] = (ids.index(phase_id) + 1) if phase_id in ids else None
        rows.append(row)
    # Non-clu sessions: fresh transcripts in the same registered projects that
    # aren't a live worker's own transcript. Appended after the clu rows; the
    # stable sort below groups them. Reuses the roots already filtered above so
    # the registry isn't scanned twice.
    rows += gather_session_rows(
        roots, projects_root=projects_root, now=now,
        claimed_paths=claimed_paths, claimed_sids=claimed_sids,
    )
    # Stable grouping: blocked plans first (most actionable), then running/dead
    # clu workers in registry order, then non-clu sessions (freshest first, as
    # gather_session_rows already ordered them). Web sticky-by-identity selection
    # re-resolves by key, so a reorder is safe.
    rows.sort(key=lambda r: 0 if r.get("blocked") else 2 if r.get("session") else 1)
    return rows


def _clean(text: str) -> str:
    """Collapse newlines / control chars to spaces so a multi-line command or
    assistant message can't break a single row's layout."""
    return "".join(c if c.isprintable() else " " for c in text)


def _fit(text: str, width: int) -> str:
    """Truncate a (already-cleaned) field to `width` with an ellipsis."""
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


# Header labels for the flexible (width-driven) columns.
_NAME_HDR, _CMD_HDR, _WROTE_HDR, _SAY_HDR = "PROJECT/PLAN·PHASE", "COMMAND", "WROTE", "SAYING"
# Fixed numeric columns RAN/ACT/HB/PID/PHASE sum to 28 (7+6+6+4+5); with 8
# single-space gaps between the 9 columns, non-flex overhead is 36.
_FIXED_OVERHEAD = 36
_FLEX_MIN = {"name": 12, "cmd": 10, "wrote": 6, "saying": 12}
_FLEX_MAX = {"name": 60, "cmd": 160, "wrote": 32, "saying": 600}

# Phase-progress glyph strip (#86): done / active / pending. Unicode-only, like
# the health glyphs and the `·` in names — there is no ASCII-fallback switch;
# they degrade the way the cursor already does on a no-unicode terminal.
_PHASE_DONE, _PHASE_ACTIVE, _PHASE_PENDING = "●", "◉", "○"
_PHASE_STRIP_MAX = 8


def _phase_strip(idx: int, total: int) -> str:
    """A done/active/pending glyph strip for phase `idx` of `total`. Empty past
    `_PHASE_STRIP_MAX` (too wide to read; the numeric `x/N` stands in)."""
    if total > _PHASE_STRIP_MAX:
        return ""
    return "".join(
        _PHASE_DONE if i < idx - 1 else _PHASE_ACTIVE if i == idx - 1 else _PHASE_PENDING
        for i in range(total)
    )


def _phase_cell(r: dict) -> str:
    """The fixed-width PHASE table cell: `x/N`, or `—` when the worker has no
    sessions index (non-clu / demo). Mirrors `top_registry._render_pair`."""
    idx, total = r.get("phase_index"), r.get("phase_total")
    return f"{idx}/{total}" if idx is not None and total is not None else "—"


def row_display_name(r: dict) -> str:
    """The identity shown in the NAME column / detail header. A non-clu session
    has no plan/phase — show its `session_name` under the project; a clu row
    keeps the `project/plan·phase` form. Single source of truth shared by the
    compact table (`_row_cells`), the detail header (`format_detail`), and the
    registry `name` metric (`top_registry._m_name`)."""
    if r.get("session"):
        return f"{r.get('project', '?')} · {r.get('session_name', '?')}"
    return f"{r.get('project', '?')}/{r.get('plan', '?')}·{r.get('phase_id', '?')}"


def row_kind(r: dict) -> str:
    """The render tier of a row — `session` / `blocked` / `worker`. The single
    source of the session-before-blocked-before-dead precedence every render
    surface keys on: both a session (`alive=None`) and a blocked row
    (`alive=False`) carry a non-live `alive`, so both MUST be classified before
    the dead path. Consumed by `_liveness_cell` and `top_registry._m_health` so
    the precedence lives in exactly one place (a missed surface is the bug class
    that otherwise renders a live row as a red `dead`)."""
    if r.get("session"):
        return "session"
    if r.get("blocked"):
        return "blocked"
    return "worker"


def _liveness_cell(r: dict) -> str:
    """The PID/liveness label — `sess` / `blk` / `ok` / `dead`, off `row_kind`.
    Shared by the compact table (`format_rows`), the detail meta line
    (`format_detail`), and the registry `pid` metric."""
    kind = row_kind(r)
    if kind == "session":
        return "sess"
    if kind == "blocked":
        return "blk"
    return "ok" if r.get("alive") else "dead"


def _row_cells(r: dict) -> tuple[str, str, str, str]:
    name = _clean(row_display_name(r))
    run = "*" if r.get("command_running") else ""
    cmd = _clean(run + (r.get("last_command") or "—"))
    w = r.get("last_write")
    wrote = _clean(f"{Path(w).name} {human_age(r.get('last_write_seconds'))}" if w else "—")
    # A blocked row has no `last_text`; the SAYING column carries the blocker
    # question instead (the actionable thing the operator must answer).
    saying = _clean((r.get("blocker_question") if r.get("blocked") else r.get("last_text")) or "—")
    return name, cmd, wrote, saying


def _flex_widths(cells: list[tuple[str, str, str, str]], width: int) -> dict[str, int]:
    """Width for each flexible column, sized to the terminal.

    Priority: name / command / wrote (the worker's identity + current action)
    get their full content first; SAYING — a long prose line where truncation
    is acceptable — absorbs whatever width is left over. Only when name+cmd+
    wrote alone overflow do those three shrink proportionally."""
    budget = max(40, width - _FIXED_OVERHEAD)
    want = {
        "name": len(_NAME_HDR),
        "cmd": len(_CMD_HDR),
        "wrote": len(_WROTE_HDR),
        "saying": len(_SAY_HDR),
    }
    for name, cmd, wrote, saying in cells:
        want["name"] = max(want["name"], len(name))
        want["cmd"] = max(want["cmd"], len(cmd))
        want["wrote"] = max(want["wrote"], len(wrote))
        want["saying"] = max(want["saying"], len(saying))
    want = {k: min(v, _FLEX_MAX[k]) for k, v in want.items()}

    core = want["name"] + want["cmd"] + want["wrote"]
    if core + _FLEX_MIN["saying"] <= budget:
        # Identity/action columns fit in full; SAYING takes the remainder.
        return {**{k: want[k] for k in ("name", "cmd", "wrote")},
                "saying": min(want["saying"], budget - core)}
    # Even the core overflows: give SAYING its floor and shrink the rest to fit.
    saying = _FLEX_MIN["saying"]
    avail = max(3, budget - saying)
    return {
        "name": max(_FLEX_MIN["name"], want["name"] * avail // core),
        "cmd": max(_FLEX_MIN["cmd"], want["cmd"] * avail // core),
        "wrote": max(_FLEX_MIN["wrote"], want["wrote"] * avail // core),
        "saying": saying,
    }


def _row_line(name, ran, act, hb, pid, phase, cmd, wrote, saying, cw: dict[str, int]) -> str:
    return (
        f"{name:<{cw['name']}} {ran:>7} {act:>6} {hb:>6} {pid:>4} {phase:>5} "
        f"{cmd:<{cw['cmd']}} {wrote:<{cw['wrote']}} {saying}"
    )


def format_rows(rows: list[dict], *, width: int = 120) -> list[str]:
    """Compact view: one row per worker, columns sized to `width` so the text
    fields use all available space and truncate only when content genuinely
    won't fit. Pure — both renderers build from this. Header first."""
    cells = [_row_cells(r) for r in rows]
    cw = _flex_widths(cells, width)
    header = _row_line(
        _fit(_NAME_HDR, cw["name"]), "RAN", "ACT", "HB", "PID", "PHASE",
        _fit(_CMD_HDR, cw["cmd"]), _fit(_WROTE_HDR, cw["wrote"]), _SAY_HDR, cw,
    )
    out = [header[:width]]
    for (name, cmd, wrote, saying), r in zip(cells, rows):
        line = _row_line(
            _fit(name, cw["name"]),
            human_age(r.get("ran_seconds")), human_age(r.get("last_activity_seconds")),
            human_age(r.get("heartbeat_age_seconds")), _liveness_cell(r),
            _phase_cell(r),
            _fit(cmd, cw["cmd"]), _fit(wrote, cw["wrote"]), _fit(saying, cw["saying"]), cw,
        )
        out.append(line[:width])
    return out


def _wrap_field(label: str, text: str, width: int) -> list[str]:
    """A labelled, word-wrapped block — full text, no truncation, hanging indent."""
    pieces = textwrap.wrap(_clean(text), max(20, width - 7)) or ["—"]
    lines = [f"  {label:<4} {pieces[0]}"]
    lines.extend(f"       {cont}" for cont in pieces[1:])
    return [ln[:width] for ln in lines]


def format_detail(rows: list[dict], *, width: int = 120) -> list[str]:
    """Detail view: each worker is a small block — a metadata line plus full,
    word-wrapped COMMAND and SAYING. Nothing truncates, at the cost of height."""
    if not rows:
        return ["(no active workers)"]
    out: list[str] = []
    for r in rows:
        name = _clean(row_display_name(r))
        meta = (
            f"RAN {human_age(r.get('ran_seconds'))} · "
            f"ACT {human_age(r.get('last_activity_seconds'))} · "
            f"HB {human_age(r.get('heartbeat_age_seconds'))} · {_liveness_cell(r)}"
        )
        out.append(f"{name}   {meta}"[:width])
        # Phase position / attempts / lease (#86) — each rendered only when its
        # datum is present; a non-clu / demo worker (no sessions index, no claim
        # lease) omits the lines entirely rather than showing `None`.
        idx, total = r.get("phase_index"), r.get("phase_total")
        if idx is not None and total is not None:
            strip = _phase_strip(idx, total)
            line = f"  PHASE  {strip + ' ' if strip else ''}{idx}/{total}"
            active = _clean(r.get("phase_id") or "")
            if active:
                line += f" · {active}"
            out.append(line[:width])
        extras = []
        att, mx = r.get("attempts"), r.get("max_attempts")
        if att is not None and mx is not None:
            extras.append(f"ATT {att}/{mx}")
        lease = r.get("lease_remaining_seconds")
        if lease is not None:
            extras.append(f"LEASE {human_remaining(lease)}")
        if extras:
            out.append(("  " + "    ".join(extras))[:width])
        # A blocked row's headline: how long it's been waiting + the question
        # the operator must answer. Above CMD/SAY (both `—` for a blocked plan).
        if r.get("blocked"):
            q = _clean(r.get("blocker_question") or "—")
            out.append(f"  BLOCKED {human_age(r.get('blocked_seconds'))} · {q}"[:width])
        run = "* " if r.get("command_running") else ""
        out.extend(_wrap_field("CMD", run + (r.get("last_command") or "—"), width))
        w = r.get("last_write")
        if w:
            wrote = _clean(f"  WROTE {Path(w).name} {human_age(r.get('last_write_seconds'))}")
            out.append(wrote[:width])
        out.extend(_wrap_field("SAY", r.get("last_text") or "—", width))
        out.append("")
    return out


def _compact_lines(rows: list[dict], *, width: int, cols: tuple[str, ...] | None) -> list[str]:
    """The compact worker table. Default (`cols is None`) is `format_rows`
    verbatim; a `--cols` subset routes through the table pane (registry), behind
    its per-pane error boundary. Lazy import mirrors how `_run_curses` already
    imports its render modules — keeps the module-level cycle out."""
    if not cols:
        return format_rows(rows, width=width)
    from end_of_line.top_registry import PANES, Snapshot, safe_render

    return safe_render(PANES["table"], Snapshot(rows), width=width, cols=cols)


def render_once(
    stream,
    *,
    projects_root: Path = PROJECTS_ROOT,
    project_filter: Path | None = None,
    now: _dt.datetime | None = None,
    width: int | None = None,
    cols: tuple[str, ...] | None = None,
) -> int:
    """Write a single snapshot to `stream`. Used for `--once` and non-TTY.

    Width defaults to the terminal's, so a wide terminal gets wide columns even
    in snapshot mode; falls back to 120 when there's no terminal (piped)."""
    if width is None:
        width = shutil.get_terminal_size((120, 24)).columns
    rows = gather_rows(projects_root=projects_root, now=now, project_filter=project_filter)
    for line in _compact_lines(rows, width=width, cols=cols):
        stream.write(line + "\n")
    return 0


_HINT = "q quit · ↑↓ select · Enter open · Tab detail · w layout · ? help"


def _render_region(
    role: str, snapshot, rect, *, cols: tuple[str, ...] | None, hint: str
) -> list[str]:
    """Lines for one pane region, each routed through the registry's per-pane
    error boundary so a single bad pane degrades to an inline band, never a
    crash. The `hint` is the one raw string the layout owns, so clip it to the
    region width here (the panes fit their own rows)."""
    from end_of_line.top_registry import PANES, safe_render

    if role == "hint":
        return [hint[: rect.w]]
    pane = PANES.get(role if role != "list" else "table")
    if pane is None:
        return []
    return safe_render(pane, snapshot, width=rect.w, cols=cols if role == "list" else None)


# The list cursor: a fixed-width gutter glyph marking the selected row. Text,
# not a curses attribute, so the read-only seam (Surface has no attr support) is
# untouched and the cursor degrades to a printable char on any terminal. Both
# glyphs are the same width, which is the gutter the table is inset by.
_CURSOR = "▸ "
_NO_CURSOR = "  "
_GUTTER = len(_CURSOR)


def _blit(surface, lines, rect, *, x: int = 0) -> None:
    """The shared inner draw loop: write `lines` top-aligned into `rect` at an
    optional x offset. `CursesSurface` clips each line per cell as a backstop."""
    for i, line in enumerate(lines[: rect.h]):
        surface.addstr(rect.y + i, rect.x + x, line)


def _selected_row(rows: list[dict], app) -> dict | None:
    """The row the cursor is bound to this tick, or None for an empty fleet."""
    i = app.selected_index
    return rows[i] if rows and 0 <= i < len(rows) else None


def _draw_list(surface, snapshot, rect, cols: tuple[str, ...] | None, app) -> None:
    """The master list with a selection cursor. The table pane stays byte-pure —
    it renders into the rect minus the cursor gutter, and the cursor glyph is
    drawn in that gutter for the selected data row (data rows sit one below the
    table header). Reuses the same per-pane error boundary as every other pane."""
    from end_of_line.top_registry import PANES, safe_render

    gutter = _GUTTER if rect.w > _GUTTER + 1 else 0
    lines = safe_render(PANES["table"], snapshot, width=rect.w - gutter, cols=cols)
    selected_line = app.selected_index + 1  # +1 for the table's own header row
    if gutter:
        marks = [_CURSOR if i == selected_line else _NO_CURSOR for i in range(len(lines))]
        _blit(surface, marks, rect)
    _blit(surface, lines, rect, x=gutter)


def _draw_detail(surface, snapshot, rect, app) -> None:
    """The detail pane, tracking the cursor: the selected worker's full block,
    scrolled by `app.scroll` (clamped here against the rendered line count so a
    focused pane can't scroll past its end). An empty fleet renders the detail
    pane's own placeholder."""
    from end_of_line.top_registry import PANES, Snapshot, safe_render

    sel = _selected_row(snapshot.rows, app)
    lines = safe_render(PANES["detail"], Snapshot([sel] if sel is not None else []), width=rect.w)
    app.scroll = min(app.scroll, max(0, len(lines) - rect.h))
    _blit(surface, lines[app.scroll :], rect)


def _draw_panes(
    surface, snapshot, layout, *, cols: tuple[str, ...] | None = None, hint: str = "", app=None
) -> None:
    """Render every pane of `layout` into its `Rect` on `surface`.

    The seam that makes the curses loop testable: a `BufferSurface` drives this
    across every geometry and forced preset (property test) without a terminal.
    Each pane fits its own rows to its region width; `CursesSurface` clips per
    cell as a backstop, so an off-by-one `Rect` can never corrupt the grid.

    With an `app` (the live curses loop) the `list`/`detail` regions become
    selection-aware: the list grows a cursor gutter and the detail tracks the
    selected worker. Without one (`--once`, the property test) every region
    routes through the plain `_render_region`, byte-identical to before."""
    for role, rect in layout.rects.items():
        if rect.w <= 0 or rect.h <= 0:
            continue
        if app is not None and role == "list":
            _draw_list(surface, snapshot, rect, cols, app)
        elif app is not None and role == "detail":
            _draw_detail(surface, snapshot, rect, app)
        else:
            _blit(surface, _render_region(role, snapshot, rect, cols=cols, hint=hint), rect)


# Bare control codes curses delivers as ints (keypad mode names the rest).
_TAB, _ENTER_LF, _ENTER_CR, _ESC = 9, 10, 13, 27


def _handle_key(ch: int, app, rows: list[dict], layout, curses) -> bool:
    """Translate one keypress into a state change. Returns True iff the user
    asked to quit. **Read-only by invariant (D7):** every branch is navigation,
    focus, or layout — there is deliberately no kill/release/signal key.

    When the detail pane is the focus (Tab) or a fullscreen drill is open, the
    arrow/page keys scroll that pane instead of moving the cursor — mirroring the
    web's list-vs-drill key split (web/index.html:360-369)."""
    from end_of_line.top_layout import next_preset

    if ch in (ord("q"), ord("Q")):
        return True
    if ch in (ord("w"), ord("W")):
        app.layout_preset = next_preset(app.layout_preset)
        return False

    detail_visible = "detail" in layout.rects
    if not detail_visible and app.focus == "detail":
        app.focus = "list"  # a stale focus from a wider geometry that lost its pane
    scrolling = detail_visible and (app.focus == "detail" or app.drill)
    # A page is the height of whichever pane the arrows act on — the detail when
    # scrolling it (incl. the fullscreen drill, where there is no list), else the
    # list. Using the wrong pane's height made PgDn crawl one line in a drill.
    active_rect = layout.rects.get("detail") if scrolling else layout.rects.get("list")
    page = max(1, (active_rect.h - 1) if active_rect else 1)

    def _nav(delta: int) -> None:
        if scrolling:
            app.scroll_by(delta)
        else:
            app.move(delta, rows)

    if ch in (curses.KEY_DOWN, ord("j")):
        _nav(1)
    elif ch in (curses.KEY_UP, ord("k")):
        _nav(-1)
    elif ch == curses.KEY_NPAGE:
        _nav(page)
    elif ch == curses.KEY_PPAGE:
        _nav(-page)
    elif ch in (ord("g"), curses.KEY_HOME):
        app.move_to(0, rows)
    elif ch in (ord("G"), curses.KEY_END):
        app.move_to(len(rows) - 1, rows)
    elif ch == _TAB:
        if detail_visible:
            app.toggle_focus()
    elif ch in (curses.KEY_ENTER, _ENTER_LF, _ENTER_CR):
        if layout.preset == "master":
            app.drill_in()
    elif ch == _ESC:
        app.drill_out()
    return False


def _run_curses(
    *,
    interval: float,
    project_filter: Path | None,
    projects_root: Path,
    cols: tuple[str, ...] | None = None,
) -> int:
    import curses
    import locale

    # lazy imports — avoid an import cycle (top_render/top_layout/top_registry
    # all import pure helpers from `top` at their module level).
    from end_of_line.top_layout import AppState, LayoutEngine
    from end_of_line.top_registry import Snapshot
    from end_of_line.top_render import CursesSurface

    try:
        locale.setlocale(locale.LC_ALL, "")  # honor UTF-8 for the glyphs
    except locale.Error:
        pass  # misconfigured/forwarded locale — fall back to the C locale

    engine = LayoutEngine()
    app = AppState()

    def _loop(stdscr) -> int:
        curses.curs_set(0)
        stdscr.keypad(True)  # deliver arrow keys + KEY_RESIZE as keysyms
        stdscr.timeout(max(100, int(interval * 1000)))  # getch doubles as pace + quit poll
        while True:
            rows = gather_rows(projects_root=projects_root, project_filter=project_filter)
            snapshot = Snapshot(rows)
            # Re-resolve the cursor by worker identity BEFORE drawing, so a
            # worker that completed above it never silently retargets selection.
            app.sync_selection(rows)
            # Lay out within the surface's usable area (it reserves the
            # bottom-right cell), not raw getmaxyx, so rects never reach the
            # corner curses raises on.
            surface = CursesSurface(stdscr)
            app.geometry = (surface.width, surface.height)
            layout = engine.layout(
                surface.width, surface.height, override=app.layout_preset, drill=app.drill
            )
            stdscr.erase()
            _draw_panes(surface, snapshot, layout, cols=cols, hint=_HINT, app=app)
            # Single window, but use the flicker-free pair so adding derwins
            # later is a drop-in: stage all writes, then one screen update.
            stdscr.noutrefresh()
            curses.doupdate()
            ch = stdscr.getch()
            if _handle_key(ch, app, rows, layout, curses):
                return 0
            # KEY_RESIZE needs no special case: the next iteration reads the new
            # getmaxyx, recomputes rects, and erase()+redraws. keypad(True) keeps
            # it from being misread as a printable key.

    try:
        return curses.wrapper(_loop)
    except KeyboardInterrupt:
        return 0


def run(
    *,
    once: bool = False,
    interval: float = 1.5,
    project_filter: Path | None = None,
    projects_root: Path = PROJECTS_ROOT,
    stream=None,
    cols: tuple[str, ...] | None = None,
) -> int:
    """Entry point for `clu top`. Curses when attached to a TTY; otherwise (or
    with --once) a single plain snapshot. `cols` narrows the compact table to
    the named metric columns (default: all)."""
    stream = stream or sys.stdout
    if once or not stream.isatty():
        return render_once(
            stream, projects_root=projects_root, project_filter=project_filter, cols=cols
        )
    return _run_curses(
        interval=interval, project_filter=project_filter, projects_root=projects_root, cols=cols
    )
