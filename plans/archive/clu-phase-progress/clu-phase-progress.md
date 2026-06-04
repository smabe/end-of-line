# clu-phase-progress

## Goal
Surface a worker's **phase position** in both `clu top` (curses) and `clu serve`
(web), closing #86. Two placements (decision Ⓑ):
- **Detail pane** — `PHASE 4/5 · <active-stage>` + a done/active/pending strip,
  plus the two adjacent already-wired metrics **attempts X/max** (imminent-halt
  signal) and **lease countdown** (decision ②, the bundle) — same grid, data
  present, all three useful when reading one worker.
- **List rows** — a compact phase indicator per row: a **mini strip + `4/5`** in
  the web rows, and the **`progress` (`PHASE`) column promoted into the default
  `clu top` table** (curses rows stay narrow — `4/5`, not a full glyph strip).

The data already ships in the row dict (`phase_index`/`phase_total` at
`top.py:338-339`, `attempts`/`max_attempts`/`lease_remaining_seconds` at
`:294,332,295`) and is carried into the web `toView` (`index.html`); this plan
is purely the missing render.

## Non-goals
- **No full per-row glyph strip in the curses table.** The list row shows the
  compact `4/5` (`progress` metric promoted to default columns); the `●●●◉○`
  dot-strip stays a detail-pane element. *Why:* a per-row glyph strip eats table
  width that the flex columns (cmd/wrote/saying) need.
- **No attempts/lease in the list rows.** List rows get phase position only;
  attempts + lease stay in the detail pane (decision ② is detail-scoped). *Why
  safe:* both read the same row dict — this is a display-density choice, no
  shared-state/ordering coupling; the `attempts`/`lease` metrics remain
  `--cols`-selectable (`top_registry.py:313,333`) for anyone who wants them in
  the table.
- **No new row-dict keys, no `assemble_row`/`gather_rows`/`toView` changes.**
  The data shipped in merge `8a18478` (append-only, D10). This plan reads it.
- **No docs rewrite.** The display is self-evident. (A one-line
  `docs/reference.md` `top.py` note is optional, folded into Phase 1 if cheap.)

## Files to touch
**Phase 1 — `clu top` (curses):**
- `end_of_line/top.py` — (1) `format_detail` (`:445`+): add `PHASE x/N` + glyph
  strip + active-stage name, `ATT x/max`, `LEASE <left>` lines, each only when
  non-null. (2) `format_rows` + the column allocator (`_row_cells`/`_row_line`/
  `_flex_widths`/headers, `~:358`+): add a fixed-width **`PHASE` column (`4/5`)**
  to the default table. New helpers: `_phase_strip(idx, total, width)` (glyph
  `●/◉/○`, ASCII `#/>/-` fallback under the existing locale guard) and a shared
  lease-countdown formatter — **hoist the `12m`/`exp` formatter so both
  `format_detail` and `top_registry._render_lease` (`:334`) call one helper in
  `top.py`** (top_registry imports from top → no cycle).
- `end_of_line/top_registry.py` — add `"progress"` to `DEFAULT_COLS` (`:139`) so
  the `--cols` metric set matches the now-default table column. Verify no
  double-render: the table pane delegates to `format_rows` (`:411`), so the
  column comes from `format_rows`; `DEFAULT_COLS` only drives `--cols` subsets.
- `tests/test_top.py` — `format_detail` tests (strip + `4/5` + name present;
  lines omitted when `None`; ASCII fallback) **and** the byte-identical
  table-pane / `format_rows` test updated for the new `PHASE` column (the
  registry-phase byte-identical guard changes deliberately).

**Phase 2 — `clu serve` (web):**
- `end_of_line/web/index.html` — (1) `detailHTML` (`:354`+): add `PHASE`/`ATT`/
  `LEASE` rows to the `.kv` grid + a `.steps` segmented strip (new CSS in
  `<style>`: flex of N spans, done=filled / active=glow / pending=hollow, shape
  not color-only). (2) `buildRow`/`patchRow` (the list-row render): add a compact
  **mini strip + `4/5`** per row. Both `null`-guarded; numeric `3/12` fallback
  past the threshold (`N>8`); active-stage name through `esc()`.
- `tests/test_webserver.py` — frontend substring guard (`IndexResourceTest`):
  `detailHTML`/row render reference `phaseIndex`/`phaseTotal` + build the strip.

## Failure modes to anticipate
- **`phase_index`/`phase_total` absent or `None`** for non-clu / demo workers or
  state with no `phases` array — `gather_rows` only sets them when `data` has
  phases (`top.py:335-339`); the `assemble_row`-only path never sets them. Both
  renderers must **omit** the phase line cleanly (no `None/None`, no crash, no
  empty strip). This is the primary correctness case, unit-tested both sides.
- **Lease expired → negative `lease_remaining_seconds`.** Show `exp`/`0`, never a
  negative — the shared formatter must clamp (the existing `_render_lease`
  already does; reusing it inherits the behavior).
- **`attempts` present but `max_attempts` absent** (assemble_row sets `attempts`
  from the claim; `max_attempts` only in the `gather_rows` path) — render `x/?`
  or omit the `/max`, don't crash on `None`.
- **Many phases (N large) in a narrow phone column** — a 12-segment strip
  collapses/overflows. Threshold (`N <= 8`): render the strip, else numeric
  `3/12` only. Ties into the just-shipped `.kv` overflow wrap (#87).
- **Curses wide-char / locale hazard** — use width-1 glyphs `●/◉/○`; fall back to
  ASCII `#/>/-` under the C-locale guard, same scheme as clu top's existing
  glyphs. A wrong glyph width drifts the whole grid.
- **XSS** — the active-stage name is a validated slug but must still pass
  `esc()` in the web build (the page's invariant: every worker-derived string is
  escaped).
- **Two parallel renderers** (Python curses + JS/HTML) — can't share code
  (D10 parity-by-discipline). Keep the *representation* (X/N, done/active/pending
  semantics, threshold) consistent so the two dashboards agree; the pixels
  differ (curses glyph strip vs CSS segmented bar) by design.
- **New `PHASE` table column squeezes the flex columns** on narrow terminals.
  It's short (`4/5`, ~5 cols in the fixed block); the existing `_flex_widths`
  allocator already clamps cmd/wrote/saying to their minimums, so the new column
  rides the fixed-overhead budget. Confirm a `--once` snapshot at 80 cols still
  fits. Rows where `phase_index` is `None` (non-clu workers) render the column
  blank/`—`, not `None`.
- **The byte-identical table-pane test changes deliberately.** Adding a default
  column means `format_rows` output legitimately differs from the registry-phase
  baseline — update the expected output, don't weaken or delete the guard.

## Done criteria
- **`clu top` detail pane** (the `_detail` pane + the `w`-toggle `format_detail`
  view) shows `PHASE x/N · <active>` + a done/active/pending glyph strip,
  `ATT x/max`, `LEASE <left>` — only when present; omitted (not `None`) when
  absent. Unit-tested incl. the None + ASCII-fallback cases.
- **`clu top` default table** shows a `PHASE` (`4/5`) column; `--once` at 80 cols
  still fits; `format_rows` test updated.
- **`clu serve` detail pane** shows the same info (parity) via the `.steps`
  strip, numeric fallback past the threshold, `null`-handled.
- **`clu serve` list rows** show a compact mini strip + `4/5` per row.
- Verified live: `clu top` + `clu serve` against a real clu plan show the active
  phase position in both list and detail (restart `clu serve` to pick up page).
- Full suite green (report count); `/code-review` clean per phase.

## Parking lot
(empty at start)
