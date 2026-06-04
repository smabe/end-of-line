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
import shutil
import sys
import textwrap
from pathlib import Path

from end_of_line import registry
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


def gather_rows(
    *,
    projects_root: Path = PROJECTS_ROOT,
    now: _dt.datetime | None = None,
    project_filter: Path | None = None,
) -> list[dict]:
    """One row per active claim across every registered plan (optionally scoped
    to `project_filter`). Tolerant at the per-plan level: a plan with no live
    claim, or whose state / transcript can't be read, contributes nothing
    rather than raising. A corrupt host registry is not swallowed — it surfaces
    rather than masquerading as an empty dashboard.
    """
    rows: list[dict] = []
    for e in registry.entries():
        if project_filter is not None and Path(e.project_root).resolve() != Path(project_filter).resolve():
            continue
        data = registry.load_entry_state(e)
        if not data:
            continue
        claim = data.get("current_claim")
        if not claim:
            continue
        wt = st.get_worktree(data)
        cwd = Path(wt["path"]) if wt and wt.get("path") else Path(e.project_root)
        tpath = locate_transcript(cwd, projects_root=projects_root, session_id=claim.get("session_id"))
        records = tail_records(tpath) if tpath else []
        row = assemble_row(claim, extract_activity(records), now=now)
        row["plan"] = e.plan_slug
        row["project"] = Path(e.project_root).name
        rows.append(row)
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
# The fixed numeric columns (RAN/ACT/HB/PID) sum to 23; with 7 single-space
# gaps between the 8 columns, non-flex overhead is 30.
_FIXED_OVERHEAD = 30
_FLEX_MIN = {"name": 12, "cmd": 10, "wrote": 6, "saying": 12}
_FLEX_MAX = {"name": 60, "cmd": 160, "wrote": 32, "saying": 600}


def _row_cells(r: dict) -> tuple[str, str, str, str]:
    name = _clean(f"{r.get('project', '?')}/{r.get('plan', '?')}·{r.get('phase_id', '?')}")
    run = "*" if r.get("command_running") else ""
    cmd = _clean(run + (r.get("last_command") or "—"))
    w = r.get("last_write")
    wrote = _clean(f"{Path(w).name} {human_age(r.get('last_write_seconds'))}" if w else "—")
    saying = _clean(r.get("last_text") or "—")
    return name, cmd, wrote, saying


def _flex_widths(cells: list[tuple[str, str, str, str]], width: int) -> dict[str, int]:
    """Width for each flexible column, sized to the terminal.

    Priority: name / command / wrote (the worker's identity + current action)
    get their full content first; SAYING — a long prose line where truncation
    is acceptable — absorbs whatever width is left over. Only when name+cmd+
    wrote alone overflow do those three shrink proportionally."""
    budget = max(40, width - _FIXED_OVERHEAD)
    want = {"name": len(_NAME_HDR), "cmd": len(_CMD_HDR), "wrote": len(_WROTE_HDR), "saying": len(_SAY_HDR)}
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


def _row_line(name, ran, act, hb, pid, cmd, wrote, saying, cw: dict[str, int]) -> str:
    return (
        f"{name:<{cw['name']}} {ran:>7} {act:>6} {hb:>6} {pid:>4} "
        f"{cmd:<{cw['cmd']}} {wrote:<{cw['wrote']}} {saying}"
    )


def format_rows(rows: list[dict], *, width: int = 120) -> list[str]:
    """Compact view: one row per worker, columns sized to `width` so the text
    fields use all available space and truncate only when content genuinely
    won't fit. Pure — both renderers build from this. Header first."""
    cells = [_row_cells(r) for r in rows]
    cw = _flex_widths(cells, width)
    header = _row_line(
        _fit(_NAME_HDR, cw["name"]), "RAN", "ACT", "HB", "PID",
        _fit(_CMD_HDR, cw["cmd"]), _fit(_WROTE_HDR, cw["wrote"]), _SAY_HDR, cw,
    )
    out = [header[:width]]
    for (name, cmd, wrote, saying), r in zip(cells, rows):
        line = _row_line(
            _fit(name, cw["name"]),
            human_age(r.get("ran_seconds")), human_age(r.get("last_activity_seconds")),
            human_age(r.get("heartbeat_age_seconds")), "ok" if r.get("alive") else "dead",
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
        name = _clean(f"{r.get('project', '?')}/{r.get('plan', '?')}·{r.get('phase_id', '?')}")
        meta = (
            f"RAN {human_age(r.get('ran_seconds'))} · ACT {human_age(r.get('last_activity_seconds'))} · "
            f"HB {human_age(r.get('heartbeat_age_seconds'))} · {'ok' if r.get('alive') else 'dead'}"
        )
        out.append(f"{name}   {meta}"[:width])
        run = "* " if r.get("command_running") else ""
        out.extend(_wrap_field("CMD", run + (r.get("last_command") or "—"), width))
        w = r.get("last_write")
        if w:
            out.append(_clean(f"  WROTE {Path(w).name} {human_age(r.get('last_write_seconds'))}")[:width])
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


def _render_region(role: str, snapshot, rect, *, cols: tuple[str, ...] | None, hint: str) -> list[str]:
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
    from end_of_line.top_layout import AppState, LayoutEngine, next_preset
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
        return render_once(stream, projects_root=projects_root, project_filter=project_filter, cols=cols)
    return _run_curses(
        interval=interval, project_filter=project_filter, projects_root=projects_root, cols=cols
    )
