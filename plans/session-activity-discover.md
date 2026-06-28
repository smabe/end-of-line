# session-activity-discover — scan for non-worker sessions, emit session rows

You are phase `discover` of the `session-activity` plan. It delivers one commit: `gather_rows()` returns a `session`-flagged row for each fresh main-session transcript in a registered project that isn't a live claim, named from the transcript. Data layer only — rendering is the next phase, but because both surfaces read `gather_rows`, session rows become visible (un-styled) in `/api/workers` immediately.

## Locked decisions (do NOT re-litigate)
See master `plans/session-activity.md`. Binding here:
- **Passive scan, non-recursive glob** of each registered project's `~/.claude/projects/<encoded-cwd>/*.jsonl`. Sidechains (`<sid>/subagents/…`) are excluded by the non-recursive glob; `_confirms` still rejects any stray `isSidechain` file (belt-and-suspenders).
- **Freshness = mtime < 300s.** Define as a module constant `SESSION_FRESH_SECONDS = 300`.
- **Name precedence:** `customTitle` → `aiTitle` → `lastPrompt` → `<project>:<sid[:8]>`.
- Append-only D10 keys: `session: True`, `session_name: str`, `session_id: str`. Worker/blocked rows do NOT carry `session` (absent ⇒ falsy ⇒ existing render paths unaffected).

## Work
- `end_of_line/top.py`:
  - **`SESSION_FRESH_SECONDS = 300`** module constant (near `_IDENTITY_SCAN_LINES`).
  - **`session_display_name(path) -> str`** — scan the transcript's leading records (reuse the bounded `_IDENTITY_SCAN_LINES` budget) for the first `custom-title`→`customTitle`, else `ai-title`→`aiTitle`, else `last-prompt`→`lastPrompt`; fall back to `f"{path.parent}"`-derived project + `path.stem[:8]`. (Records are `{"type":"custom-title","customTitle":...,"sessionId":...}` etc. — empirically confirmed CC v2.1.174.) Defensive: any record may be missing the key; skip and continue.
  - **`assemble_session_row(session_id, name, activity, now) -> dict`** — `_base_row(activity, now)` (from `refactor`) + `{"session": True, "session_id": session_id, "session_name": name}` + the claim-only keys set to `None`/default so the D10 shape stays uniform (`phase_id`, `ran_seconds`, `heartbeat_age_seconds`, `alive`, `attempts`, `lease_remaining_seconds`, `stuck`, and the blocked trio absent). Mirror how `assemble_blocked_row` zero-fills.
  - **`gather_session_rows(*, projects_root, now, project_filter, claimed_sids) -> list[dict]`** — for each unique registered `project_root` (`{Path(e.project_root).resolve() for e in registry.entries()}`, honoring `project_filter`): encode dir via `encode_project_dir`; if `d.is_dir()`, for each `d.glob("*.jsonl")`: stat mtime (skip if age ≥ `SESSION_FRESH_SECONDS`); `_identity` (skip if `isSidechain` or cwd ≠ project_root); skip if `path.stem in claimed_sids`; else `tail_records` → `extract_activity` → `assemble_session_row(stem, session_display_name(path), activity, now)`; set `row["project"] = Path(project_root).name`, `row["plan"] = None`. Return rows (mtime-desc so freshest first).
  - **`gather_rows`** — after building worker/blocked rows, collect `claimed_sids = {c["session_id"] for ... if claim has session_id}`, then `rows += gather_session_rows(..., claimed_sids=claimed_sids)`. Keep the existing blocked-to-top sort; session rows sort after worker/blocked (give them sort key `2`).
  - **Fix the stale module docstring** (top.py:9–14): it says sidechains are "separate isSidechain subagent transcripts" in the same dir — update to note they now live in a `<sid>/subagents/` subdir (CC v2.1.174) and the non-recursive glob excludes them.
- `tests/` — new `tests/test_top_sessions.py` (fixture transcripts via tmp_path; reuse the project's transcript-fixture helpers if present — grep existing top tests). Cover: fresh main session → row; stale (old mtime) → skipped; sidechain → skipped; cwd-mismatch file → skipped; stem ∈ claimed_sids → skipped (no double-row); name precedence (customTitle>aiTitle>lastPrompt>fallback); a project with a live claim AND a separate fresh session → both a worker row and a session row, distinct.

## Decisions & findings
### Decision: dedup sessions against live-claim session-ids, by stem  *(status: active)*
- **Rationale:** a dispatched worker's transcript is also a fresh main-session `*.jsonl` in the same dir; without dedup it would render twice (once as a worker row, once as a session row). The claim already carries `session_id`, and the transcript filename stem == session id (CC writes `<session-id>.jsonl`). Filtering the scan by `claimed_sids` is exact and cheap.
- **Alternatives considered:** dedup by transcript path equality — equivalent but needs resolving each claim's path first; stem-set is simpler and is what `locate_transcript(session_id=...)` already keys on.
- **Evidence:** worker resolution `gather_rows` top.py:376–377; `locate_transcript` deterministic-filename branch top.py:100–108 (@7dbe001).

### Decision: scan unique project_roots, not registry entries  *(status: active)*
- **Rationale:** multiple plans can share one `project_root`; scanning per-entry would re-glob the same dir N times and risk N duplicate session rows. Dedup the roots first.
- **Evidence:** `registry.entries()` → `PlanEntry(project_root, plan_slug, registered_at)`; no built-in distinct-root API (registry.py).

## Failure modes to anticipate
- **Cross-session contamination (CC #26964):** two live sessions in one dir leak records into each other's files; a tailed record may belong to another session. Mitigation (in-scope per master non-goal): when extracting activity for a session, the row's `session_id` is the file stem — acceptable for v1; note that activity could momentarily reflect a sibling. Full per-record `sessionId` filtering deferred.
- **Filename ≠ internal sessionId (CC #63904):** a synced `<uuid>.jsonl` whose in-file `sessionId` differs. `_identity` reads cwd (not sessionId) for confirm; the stem is still the feed `tid`. Low impact for local sessions; note it.
- **The operator's own clu-top/serve session** appears as a row (it's fresh, in-project). Intended per master (operator chose self-display). Not an error.
- **A blocked plan's just-exited worker** transcript may be fresh and not in `claimed_sids` (claim released) → shows as both a blocked row and a session row. Edge case; acceptable (session row shows the last activity). Note, don't special-case.
- **Empty/locked transcript mid-write:** `tail_records`/`_identity` already tolerate truncated final lines and OSError — confirm a zero-byte file yields no row, not a crash.

## Done criteria
- `gather_rows()` returns a `session`-flagged row for a fresh non-claimed main-session transcript, named per precedence; skips stale/sidechain/cwd-mismatch/claimed.
- Worker and blocked rows are unchanged (no `session` key; existing tests green).
- `tests/test_top_sessions.py` covers every case above; full suite green.
- `/code-review` run on the diff.
