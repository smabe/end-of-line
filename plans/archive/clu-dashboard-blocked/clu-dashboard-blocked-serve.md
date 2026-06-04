# clu-dashboard-blocked-serve — blocked rows in the clu serve web dashboard

You are phase `serve` of the `clu-dashboard-blocked` plan. Render the blocked
state (shipped into the `gather_rows` wire contract by phase `top`) in
`clu serve`: an amber blocked row + the blocker question + a blocked count in the
header. One commit.

## Locked decisions (do NOT re-litigate)

See `plans/clu-dashboard-blocked.md`. Summary:

- The row dict now carries `blocked`/`blocker_question`/`blocked_seconds` (phase
  `top`, append-only D10). `toView` reads them.
- `health = r.blocked ? "blocked" : (!alive ? "dead" : …)` — blocked checked
  FIRST (mirrors `_m_health`). `.dot.blocked` = amber (distinct from red dead).
- Row: amber `BLK` badge + the blocker question in `.sub` (`esc()`'d) + `—
  blocked <Nm> —` metrics. Detail: a `blocker` kv row + health-chip. Header: a
  blocked count.
- Read-only — no answer-from-dashboard.

## Read first

- `end_of_line/web/index.html` `toView` (~:235-247, the view-model map + the
  `health` ternary + the `DOT` map) — where blocked classifies.
- `index.html` `buildRow`/`patchRow` (the row skeleton + the `.bdg` badge slot +
  `.sub` + `.metrics`/`— pid gone —`), `detailHTML` (the `.kv` grid + health-chip
  `hc`), `statusInner` (the `running · dead` counts), the `.dot.ok/.warn/.dead`
  CSS + the `esc()` helper.
- `end_of_line/top.py` `assemble_blocked_row` (phase `top`) — the exact keys the
  web reads (`blocked`, `blocker_question`, `blocked_seconds`, `phase_index`,
  `phase_total`).
- `tests/test_webserver.py` `IndexResourceTest` — the frontend substring-guard
  pattern (e.g. `test_frontend_renders_phase_progress`).

## Produce

1. **Failing tests first** (`tests/test_webserver.py`, `IndexResourceTest`):
   - `test_frontend_renders_blocked_state`: the page references `r.blocked` /
     `blockerQuestion`, defines `.dot.blocked` CSS, and the header builds a
     blocked count.

2. **Implementation** (`end_of_line/web/index.html`):
   - `toView`: carry `blocked`/`blockerQuestion`/`blockedSeconds`; `health =
     r.blocked ? "blocked" : …`; add `blocked` to the `DOT` map.
   - CSS: `.dot.blocked{background:var(--amber);box-shadow:0 0 8px var(--amber)}`
     (amber; add a `--amber`-based color if not already a dot color).
   - `buildRow`/`patchRow`: blocked → amber `BLK` badge, question in `.sub`
     (`esc()`'d), `— blocked <age> —` in metrics. `detailHTML`: a `blocker` kv
     row + a "blocked" health-chip. `statusInner`: a `N blocked` count.
   - A small `blockedFmt(seconds)` helper (mirror the `age()` formatter) for the
     blocked-since age, null-safe.

3. **Acceptance.**
   - New test green; full suite green (report count).
   - `node --check` on the extracted `<script>` passes (no JS syntax error).
   - Headless render (mock a `blocked:true` worker like the phase-progress
     verification) → the row shows an amber dot + `BLK` + the question; the
     detail pane shows the blocker; the header shows the blocked count;
     blocked reads distinctly from a red dead worker.

4. **Commit + attest + complete.**
   - Commit: `clu-dashboard-blocked: phase serve — blocked rows in clu serve`.
   - Stage: `end_of_line/web/index.html`, `tests/test_webserver.py`.
   - After the commit: `clu verify --plan clu-dashboard-blocked --phase serve --token <T>`
     then `clu attest --simplify --plan clu-dashboard-blocked --phase serve --token <T>`.
   - `clu complete --plan clu-dashboard-blocked --phase serve --token <T>`.

## Failure modes to watch

- **Blocked classified as dead in `toView`.** `!alive` is true for a blocked row
  (`alive=False`) → it'd read as dead unless `r.blocked` is checked FIRST in the
  health ternary. Same trap as the curses `_m_health`.
- **XSS** — `blocker_question` is operator/worker-authored free text → `esc()` it
  (the page's invariant: every worker-derived string is escaped).
- **The `patchRow` `w.dead ? "— pid gone —"` branch** must not swallow a blocked
  row — a blocked row is not `dead`; gate the blocked metrics/badge on
  `w.blocked` before the dead branch.
- **Page is cached at serve startup** — note in the acceptance that a live check
  needs a `clu serve` restart (the test + headless render cover correctness).
