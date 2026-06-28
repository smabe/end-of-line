# session-activity — surface non-clu Claude sessions + agent activity in clu-top & clu-serve

## Phase map  *(arc + gates; work detail lives in each shard)*

**Phase `refactor` — extract a shared row-builder so a 3rd row type doesn't triple the D10 schema**  *(no gate — pure refactor)*
- Enters when: start here.
- Done signal: `assemble_row` + `assemble_blocked_row` both build from one shared base helper; full suite green; zero behavior change.
- If it fails: no gate — fix-forward (refactor is mechanical).
- Shard: `plans/session-activity-refactor.md`

**Phase `discover` — scan registered project dirs for fresh non-worker sessions, emit session rows**
- Enters when: `refactor` committed.
- Done signal: `gather_rows()` returns a `session`-flagged row for a fresh main-session transcript that isn't a live claim; deduped vs claim session-ids; named from the transcript.
- If it fails: no gate — fix-forward.
- Shard: `plans/session-activity-discover.md`

**Phase `classify` — render session rows distinctly (curses + web) and make their detail feed resolvable**
- Enters when: `discover` committed (session rows exist in the data layer).
- Done signal: `clu top` shows a session row with a `sess` marker (not `x/N`); web badges it distinctly; clicking it streams its transcript via `/api/feed` keyed by session-id.
- If it fails: no gate — fix-forward.
- Shard: `plans/session-activity-classify.md`

**Phase `agents` — decode Agent/Task tool_use into a new `agent` feed kind (workers AND sessions)**
- Enters when: `classify` committed (sessions are viewable end-to-end).
- Done signal: a worker/session that runs `/code-review` or spawns an Agent shows an `agent`-kind event in the detail feed; `extract_activity` reflects it in the row.
- If it fails: no gate — fix-forward.
- Shard: `plans/session-activity-agents.md`

## Status & cold-start

**Approval: APPROVED 2026-06-28**

SHIPPED: `refactor` (`_base_row` extraction; suite 1913 green, basedpyright clean; key order byte-preserved). Commit pending this phase's bookkeeping.
NEXT phase: `discover`. **Read `plans/session-activity-discover.md` FIRST**, then execute. Its 3 binding decisions: passive non-recursive glob per registered project_root; freshness `SESSION_FRESH_SECONDS = 300`; name precedence customTitle→aiTitle→lastPrompt→`project:sid[:8]`; dedup vs claim session-ids by stem.
Line hints below were measured at `7dbe001`; re-anchor by symbol.

Binding decisions carried inline (so a compaction that drops the shard still shows them):
- **Passive scan, no hook.** Discover sessions by globbing each registered project's `~/.claude/projects/<encoded-cwd>/*.jsonl` (non-recursive — sidechains now live in a `<sid>/subagents/` subdir, so they're already excluded). No SessionStart/End registration. (Operator chose passive scan over self-register.)
- **Freshness = mtime < 300s.** Matches community idle threshold (claude-code-trace `SESSION_IDLE_THRESHOLD_SECONDS=300`). A session idle >5min drops off the "active" view; a just-finished one lingers ≤5min. Both acceptable for a live view.
- **Name precedence:** in-transcript `custom-title.customTitle` → `ai-title.aiTitle` → `last-prompt.lastPrompt` → fallback `<project>:<sid[:8]>`. (Empirically confirmed these records exist, CC v2.1.174.)

## Goal

When any Claude session — not just clu-dispatched phase workers — runs in a registered project, surface it as a distinct row in `clu top` and `clu serve`, and decode Agent/Task tool_use (e.g. `/code-review` fan-outs, subagent spawns) into the activity feed so the operator can see "claude is running code-review / spawned Explore" live.

## Non-goals

- **No SessionStart/SessionEnd hook registration.** Passive scan chosen; a hook is a possible later enhancement (operator-set names, clean teardown) but adds an install dependency and a crash-deregister gap (CC #27361) for marginal gain over mtime liveness.
- **No nesting of a subagent's INTERNAL steps under its parent.** P3 decodes the *spawn* and enriches from the parent's `tool_result` (`agentType`/`status`/`totalTokens`/`totalDurationMs`). Reading the child `agent-<agentId>.jsonl` to nest its own Bash/edits is deferred. *Safe asymmetry:* display-only; showing the spawn without the child's interior narrows detail, it does not couple state or race anything.
- **No discovery of sub-directory / worktree cwds.** Scan only each registered `project_root`'s encoded dir (most interactive sessions run at project root). *Safe asymmetry:* display-only; excluding some sessions only shows fewer rows — the row schema and render path are identical, so subdir scanning folds in later with no rework.
- **No new config knobs** beyond the freshness-cutoff constant. No per-project enable/disable.
- **No cross-session-contamination repair** beyond filtering tailed activity to the transcript's own `sessionId` (CC #26964: two live sessions in one dir can leak entries). Full dedup-by-`message.id` is out of scope.

## Files touched (overview)

- `end_of_line/top.py` — `refactor`,`discover`,`classify`,`agents` — base-row extract; session scan + `assemble_session_row` + name derivation + stale-docstring fix; session render cells; `extract_activity` Agent decode.
- `end_of_line/top_registry.py` — `classify` — `_m_health` session class + session render cells.
- `end_of_line/webserver.py` — `classify`,`agents` — feed resolution by session-id; `record_events` Agent decode.
- `end_of_line/web/index.html` — `classify`,`agents` — `toView` session class + badge + `?sid=` feed route; `agent` feed-kind styling.
- `docs/reference.md` — `classify`,`agents` — D10 row-contract (`reference.md:1213 @7dbe001`) + feed-kind list get the `session`/`session_name` keys and `agent` kind.
- `tests/` — every phase — failing tests first (TDD).

## Background findings  *(cross-phase; per-phase detail lives in shards)*

- **Shared data layer.** `top.gather_rows()` (top.py:347 @7dbe001) feeds BOTH the curses renderers and `/api/workers` (webserver.py:357). Add session rows once in `gather_rows`; both surfaces inherit them. Row dict is the D10 frozen wire contract — additions are append-only keys (reference.md:1213).
- **Transcript shape (empirical, CC v2.1.174).** Identity fields (`cwd`, `sessionId`, `version`, `gitBranch`, `isSidechain`, `timestamp`, `userType`) ride on every conversational record, not a single meta line. Title sidecar records exist: `type:"custom-title"`/`"ai-title"`/`"agent-name"`/`"last-prompt"`, keyed by `sessionId`.
- **Sidechains relocated.** Now `<projectdir>/<parentSessionId>/subagents/agent-<agentId>.jsonl`; recent main transcripts carry zero embedded `isSidechain:true`. `locate_transcript`'s non-recursive `d.glob("*.jsonl")` (top.py:116) already skips them. The existing `_confirms` sidechain rejection (top.py:80) becomes belt-and-suspenders — keep it.
- **Agent correlation IS available** (corrects the earlier "broken" read). Parent chain: assistant `tool_use.id` → matching `tool_result.tool_use_id` (a `user` record) → `toolUseResult.agentId` → child file `agent-<agentId>.jsonl`. The `tool_result` also carries `agentType`, `status`, `totalTokens`, `totalDurationMs`. (#32175 is about the *child* lacking a parent ref — irrelevant here.) Agent `input` keys: `description`, `prompt`, `subagent_type`.
- **Liveness = mtime only.** No end-of-session marker record exists in the corpus (CC #27361). Don't parse for an "ended" signal; threshold mtime.
- **Defensive parsing already in place.** `tail_records` / `_identity` / `record_events` tolerate per-line `JSONDecodeError` and string-or-array content. Schema is version-internal (CC docs warn it shifts) — keep the try/except + optional-field posture; never hard-require a key.
- **Render seams** (measured @7dbe001): web `toView` (index.html:277–306) classifies `blocked` before `alive` (line 288) — mirror that ordering for `session`; feed query built at index.html:595–597. Curses health via `top_registry._m_health` (~:305), default cols `("name","ran","act","hb","pid","progress","cmd","wrote","saying")` (~:142); PID/PHASE cells via `top._liveness_cell`/`_phase_cell` (top.py:460,453). `registry.entries()` → `PlanEntry(project_root, plan_slug, registered_at)`; dedup project_roots with `{Path(e.project_root).resolve() for e in registry.entries()}`.

## Done criteria  *(plan-level — cross-cutting; per-phase exits live in shards)*

- Full suite green via the gate: `python3 -m unittest discover -s tests` (report pass count). `clu verify` clean incl. basedpyright.
- `clu top` shows a fresh non-worker session as a distinct `sess`-classified row, named from the transcript.
- `clu serve` `/api/workers` returns session rows; web UI badges them apart from plans; clicking one streams its transcript via `/api/feed` keyed by session-id.
- A worker OR session running `/code-review` / spawning an Agent shows an `agent`-kind event in the detail feed, and `extract_activity` surfaces it on the row.
- `docs/reference.md` records the new `session`/`session_name` row keys and the `agent` feed kind.
- `/code-review` run on the diff at each non-trivial phase; findings applied in-phase.

## Parking lot
(empty)
