# serve-activity-feed — scrolling per-worker activity feed in clu serve

SAYING text in the serve detail pane flips past faster than it can be read —
the dashboard shows only the *latest* transcript signals
(top.py:174-225 `extract_activity` keeps last-of-each-kind). This adds a
capped, sticky-scroll activity feed to the detail pane, fed by a new
cursor-based endpoint that tails the worker's transcript incrementally.
Transcripts, not stdout logs, are the source — `claude --print` keeps stdout
quiet until exit even post-PTY-shim; the interim narrative only exists in the
transcript JSONL.

Verified ground truth (2026-06-11 session, three research passes):

- Server is `ThreadingHTTPServer` (webserver.py:46, 508-521); short-poll +
  byte-offset cursor is house style (1.5s `poll()`, index.html:575) and the
  consensus transport for localhost dashboards — SSE on stdlib pins a thread
  per open connection.
- Security gate order is inherited for free when the endpoint joins the
  exact-match chain after auth: Host allowlist (421, webserver.py:385-427) →
  token (`hmac.compare_digest`, :411-419) → exact-match routes (:453-466).
- `--no-transcript` strips `last_command`/`last_text`/`last_write` from rows
  (webserver.py:53-55, 353-361) — the feed is 100% that data class, so the
  flag must disable the endpoint.
- Worker→transcript resolution exists: registry entry → claim `session_id` +
  worktree cwd → `locate_transcript` (top.py:86-125, sidechain-rejecting,
  cwd-confirming).
- `tail_records` (top.py:127-161) is bounded-tail with NO cursor — the
  incremental read is new code; JSONL tail recipe: seek to cursor, consume
  only to last `\n`, carry partial line; `st_size < cursor` → reset.
- D10 row contract UNTOUCHED — new endpoint, no new row keys
  (`GatherRowsWireContractTest` stays at 19 keys).

## Locked design decisions

### Phase feed
- **Endpoint**: `GET /api/feed?plan=<slug>&proj=<name>&phase=<id>&cursor=<n>&tid=<id>`,
  exact-match chain after auth. `state.validate_slug` on plan/phase.
  Transcript resolved via the same registry→claim→`locate_transcript` path
  `gather_rows` uses. Response JSON:
  `{events: [{ts, kind, text}], cursor, tid, reset}`.
- **Cursor mechanics**: `tid` binds the cursor to the transcript identity
  (session id). `reset:true` when tid changes (new attempt) or
  `st_size < cursor` (rotation). First call (`cursor=-1`) backfills from
  `max(0, size - 64KB)` (same bound as `tail_records`). Per-poll read cap
  ~256KB; consume to last `\n` only.
- **Event mapping**: per-record decode of the shapes `extract_activity`
  understands — assistant text → `say`; `tool_use` Bash → `tool`; `tool_use`
  in `_WRITE_TOOLS` → `write`; `tool_result` → `result`. Extract a shared
  per-record helper from `extract_activity` ONLY if it factors cleanly (two
  real call sites); otherwise keep the feed's decoder local — do not contort
  `extract_activity`. **Server-side truncation ~400 chars/event** (transcript
  lines can embed whole files).
- **Privacy**: `include_transcript=False` → `/api/feed` 404 (route not
  registered); documented beside the flag's existing semantics.
- **UI** (vanilla, inline, themed): feed pane in the detail view; polls ONLY
  when a worker is selected AND the tab visible (compose with the existing
  `visibilitychange` gating, index.html:569-583); 1.5s cadence;
  **sticky scroll** — auto-follow only within ~10px of bottom, pause-follow
  on scroll-up; **cap 1000 entries**, prune oldest DOM nodes on append;
  every string through `esc()` (index.html:217-225); NO
  `backdrop-filter`/`mix-blend-mode` (enforced by
  `test_frontend_avoids_continuous_gpu_compositing`,
  tests/test_webserver.py:170); reuse `:root` theme vars + `.panel` chrome.

## Non-goals
- **No SSE/WebSocket** — thread-pinning on stdlib server; polling is the
  measured choice.
- **No stdout-log pane** — that file is the post-mortem artifact; revisit
  only if a real post-mortem wants it in-browser.
- **No feed polling for unselected workers / no fleet-wide feed.**
- **No D10 row-dict changes, no new deps, no new external resources.**

## Files touched
- `end_of_line/webserver.py` — endpoint + routing + privacy gating (API
  hotspot: exact-match route list)
- `end_of_line/web/index.html` — feed pane, sticky scroll, capped scrollback
- `end_of_line/top.py` — only if the per-record decoder factors cleanly
  (flag in findings either way)
- `tests/test_webserver.py`, `tests/test_top.py` — endpoint + cursor + UI
  substring guards + fixture transcripts
- `docs/reference.md` — webserver section: endpoint contract + privacy note

## Per-phase done checklist
- TDD: failing tests first.
- `/code-review` after (this diff spans >1 file).
- Full suite green: `python3 -m unittest discover -s tests` (judge by
  `clu verify`; ~30 in-sandbox environment failures are known).
- Structured commit format; stage explicit paths.
- **Post-commit attestations:** `clu verify` then `clu attest --simplify`
  (each with `--plan serve-activity-feed --phase feed --token <T>`).
- `clu complete --plan serve-activity-feed --phase feed --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| feed | `serve-activity-feed-feed.md` | cursor endpoint + sticky-scroll pane + tests | 2.5h |

## Findings log

_Empty at plan time. Workers append one dated bullet per cross-phase finding
with file:line._
