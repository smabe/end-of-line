# clu top — modular multi-pane TUI · Master design doc

*Brainstorm consolidation, 2026-06-03. Six personas (layout, registry, UX,
prior-art, QA, operator) — working docs in `plans/brainstorm-clu-top-tui/`.
**Approved 2026-06-03** — all open questions resolved (see "Resolved
decisions" below); `mockup.html` is the agreed visual. Full 5-phase arc is
green-lit. Nothing built yet.*

---

## TL;DR (read this first)

Turn `clu top` from one flat table into a **btop-like modular TUI**: a master
worker list + a detail pane (full SAYING etc.), panes built from a **registry of
metrics** (add one in a single file, no engine edits), laid out by an engine
that adapts to your two geometries.

**Four decisions I'd make for you, and the one tension to resolve:**
1. **Layout = a binary split-tree, chosen by terminal *width* (with a rows
   floor), not aspect ratio.** Prior art is unanimous and surprising: htop, k9s,
   lazygit, fzf, btop — *none* flip by aspect ratio. They all threshold on
   **width**, with lazygit's `portraitModeAutoMaxWidth: 84` the canonical move.
   Your "opposite aspect ratios" requirement is served by width breakpoints +
   a short-height special case. (This reframes the ask — flagged below.)
2. **Registry = PULL model.** A `Metric` and a `Pane` are each a frozen
   dataclass registered via a decorator; the engine pulls `compute()` only for
   metrics the *visible* panes declare. Today's 8 columns migrate to built-in
   metrics (dogfoods the registry). The in-flight `--cols` plan is **subsumed**.
3. **Selection is sticky-by-identity** (`project/plan·phase`), re-resolved every
   1.5s tick — never by list index. This is both the UX backbone and QA's #1
   risk; index-based selection silently retargets when a worker above completes.
4. **A `Surface` seam makes the whole thing testable** — all rendering goes
   through an `addstr/width/height` interface with a real `CursesSurface` and a
   `BufferSurface` (list-of-rows) for tests. This is the single most important
   architecture decision: today `_run_curses` is untestable; with the seam we
   property-test "never raises, every line ≤ width" across all geometries.

**The tension (your call in the morning):** the operator persona's biggest
**dealbreaker is losing glance density** — "if I see fewer workers than today's
flat table, I revert." A detail pane eats space. Resolution baked into the
design: the **wide-short strip defaults to list-only** (or list + a 1-line
SAYING ticker); the detail pane belongs to the **phone/drill-in** flow. The two
geometries resolve the tension *if* we accept "strip = density, phone = depth."
Confirm that framing and most other decisions fall out.

---

## Context

`clu top` watches autonomous Claude Code workers, reading real activity from
their transcripts (`end_of_line/top.py`: pure-stdlib curses, `gather_rows` →
`format_rows`/`format_detail`, `q`/`w`). Two real workflows drive the redesign:
- **(A) Wide-short strip** docked under coolant — glance, "is everything
  healthy and moving?", little interaction.
- **(B) Phone via ssh** — tall-narrow (~45 cols), awkward keyboard; drill into
  one worker, read what it's saying/doing.

Hard constraints unchanged: **Python 3.11+, stdlib only, zero deps, curses,
unittest.**

---

## Design decisions

| # | Decision | Rationale | Raised by |
|---|---|---|---|
| D1 | **Binary split-tree** layout (leaf pane \| H/V split with weighted children). Not fixed slots (can't adapt), not full flexbox (YAGNI for ≤4 pane kinds). | Adapts to any geometry with ~30 lines of Rect math. | Layout |
| D2 | **Width-primary breakpoints** + a `rows` floor. Ladder: `cols≥80` → master-left/detail-right; `50–80` → stacked; `<50` → master-only, Enter→fullscreen detail; `rows<12` → strip (list-only / 1-line ticker); `<34×2` → one-line fallback. | Every verified tool thresholds on width; lazygit `portraitModeAutoMaxWidth:84`, fzf `<50(hidden)`, terminal.shop rungs. | Prior-art, Layout |
| D3 | **PULL metric model** + per-tick memoized `Snapshot` owning the single JSONL parse. | A hidden metric must cost nothing; a push bus computes everything every tick, defeating a configurable layout. | Registry |
| D4 | **Metric & Pane = frozen dataclasses**, registered via `@register_metric`/`@register_pane` into two dicts in a new `top_registry.py`. Not Protocol (overkill for value+2 funcs). | "Add a pane/metric in one file, no engine edits" — the operator's #1 ask. Mirrors the repo's existing frozen-dataclass registry pattern. | Registry |
| D5 | **Selection sticky-by-identity** (`(project, plan, phase_id)`), clamp (no wrap), default row 0. Master list drives a **passive** detail pane; `Tab` only grabs focus when detail overflows (to scroll it). | Survives the 1.5s refresh, completion, reordering. k9s/lazygit/btop model. | UX, QA |
| D6 | **`Surface` rendering seam** (`CursesSurface` + `BufferSurface`); layout is pure `Rect` math; **per-pane error boundary** (one bad pane renders an error band, never crashes the TUI). | Makes the TUI unit-testable; protects the plugin goal. | QA |
| D7 | **Read-only is a hard invariant** — no kill/signal/release keys in the UI, ever. Visual alarm for wedged workers is fine (color/`!`/move-to-top). | Matches the existing operator-approval discipline. | UX, Operator |
| D8 | **Fused health glyph** per worker (green/amber/red from PID + HB + ACT + stuck-command) instead of 4 separate clocks to AND together. | The dangerous signal is *PID-ok-but-ACT-stale* (silent wedge); fuse it so it can't be missed. | Operator |
| D9 | **`--cols` plan is absorbed.** Column control becomes "which metric keys, with width sentinels, in which pane, per geometry." Close `plans/clu-top-column-sizing.md` into this. | One coherent model beats a bolt-on. | Registry, me |
| D10 | **`gather_rows()` is a frozen contract — `clu serve` depends on it.** The web dashboard (`webserver.py:343` → `/api/workers` → `index.html:234` `toView`) reads the **raw row dicts** and renders columns in its own JS; it shares **zero** code with the curses column layer. Metrics read *from* the row dict; they must not rename, drop, or relocate any key `gather_rows` emits. | The TUI and web are two renderers over one row dict. A migration that mutates the dict shape silently breaks the web, and no TUI test catches it. | clu-serve trace |
| D11 | **No user plugin dir for v1 (Q4 resolved: dropped).** In-tree modularity only — add a metric/pane in one repo file. `~/.config/clu/top_plugins/` + `pkgutil.iter_modules` loader is *not* built (no `exec` of operator Python, no committed-stable plugin API, no third-party error isolation). Single operator, every named pane belongs in clu's tree. | Speculative generality until a pane exists that shouldn't live in the repo. | Operator (Q4) |

---

## Architecture

```
gather_rows()                      # unchanged: clu state + transcript → row dicts
        │
        ▼
Snapshot(rows, transcript_cache)   # per-tick; owns the ONE JSONL parse, memoized
        │
        ▼
AppState{ selected_key, geometry, layout_preset, focus, scroll }   # shared, panes read/write
        │
   ┌────┴───────────────┐
   ▼                    ▼
METRICS registry     PANES registry          # two dicts, decorator-populated
 (key→Metric)         (kind→Pane)             # built-ins dogfood the registry
   │                    │
   └─────► LayoutEngine(split-tree, Rect math) ─────► Surface (Curses | Buffer)
```

- **Metric** `= frozen dataclass{ key, label, compute(snapshot,row)->v,
  render(v,width)->str, sort_key, cost∈{cheap,transcript}, align }`. Today's
  ran/act/hb/pid/command/wrote/saying/tokens become 8 built-in metrics.
- **Pane** `= frozen dataclass{ kind∈{table,detail,text,header},
  metric_keys, render(region, app_state, snapshot), size_hints, focusable }`.
  `format_rows` becomes the `table` pane render (byte-identical output); the
  detail pane is a key/value + free-text (full SAYING) render.
- **LayoutEngine**: picks the split-tree shape by D2 breakpoints, sizes leaves
  with the *existing* `_flex_widths` allocator (`top.py:343`) — it's already a
  min/pref/flex solver; the engine and the column allocator are **one idea at
  two scales** (real DRY win). Reuse `format_rows(width=…)` inside the table
  pane.
- **curses mechanics** (doc-verified): one `stdscr` + `derwin` per pane;
  `noutrefresh()` all panes then a single `doupdate()` (kills flicker);
  `erase()` not `clear()`; keep the lower-right-corner `try/except curses.error`
  wrap. On `KEY_RESIZE`, recompute rects and `erase`/resize each pane (stdlib
  sub-windows don't auto-resize). Full redraw every poll is fine — `doupdate`'s
  diff *is* the dirty-region tracking; the real cost is `gather_rows` I/O.
- **YAGNI line (explicit):** two dicts + two decorators + `Snapshot` +
  `AppState` + `Surface`. **Not** building: push bus, plugin sandbox, hot-reload,
  entry-points, DAG scheduling, **and no user plugin dir** (Q4 resolved — D11).
  Extensibility is in-tree: add a metric/pane in one repo file. The
  `~/.config/clu/top_plugins/` loader is explicitly out of scope, not "deferred."

---

## UX specification

**Keybindings** (no F-keys — they break over iPhone ssh; every primary action is
one unmodified key):
`↑↓`/`j k` move · `g`/`G`/`Home`/`End` ends · `PgUp`/`PgDn` page · `Tab` focus
list↔detail (scroll detail) · `Enter` drill-in (fullscreen detail on narrow) ·
`Esc` back/clear · `w` cycle layout preset · `/` filter · `?`/`h` help overlay ·
`q` quit. State shown **glyph-first, color as redundant reinforcement** (degrades
on no-color / no-unicode; reuses the existing `*`/`ok`/`dead` glyphs + locale
guard at `top.py:459`).

**Wide-short strip (≥80 cols, but few rows):**
```
┌ N running · 1 blocked · 0 dead · oldest-ACT 4m ─────────────────────────────┐  ← fleet header
│● HealthData/logging·impl  RAN 25m ACT  7s  pytest -k logging   logging.py 4s │  ← list (selected = ●/highlight)
│◐ end-of-line/clu-top·two  RAN  2m ACT 41s  git log HEAD ^main  —             │
│✗ foo/bar·three  (dead)                                                       │
└─ q quit · ↑↓ select · w layout · ? help ────────────────────────────────────┘
   (at 3 rows: drop header + legend, keep list + 1-line SAYING ticker pinned)
```

**Phone tall-narrow (~45 cols):** no horizontal split; stacked 2-line list,
`Enter` → fullscreen scrollable detail, `Esc` back.
```
 3 running · 1 blk · oldest 4m        list view              fullscreen detail (after Enter)
 ● HealthData/logging·impl            ─────────────          HealthData / logging · impl
   pytest -k logging · ACT 7s                                health ● ok   RAN 25m  ACT 7s
 ◐ end-of-line/clu-top·two                       Enter →     attempts 1/3   lease 12m left
   git log HEAD ^main · ACT 41s        ───────►              tokens 1.2M · ~$3.40
 ✗ foo/bar·three  dead                                       CMD  pytest -k logging
 ──────────────────────────                                  SAY  (full, word-wrapped,
 ↑↓ sel · Enter open · q quit                                     scrollable) …
```

**Default detail-pane content** (selected worker), ranked by operator value:
full untruncated SAYING → recent transcript tail (last few turns) →
time-on-phase + lease-remaining countdown → **attempts X/max** (invisible today;
signals imminent halt) → files-touched → token spend / $ → git diff stat.

**New modular panes worth building (operator-ranked):** (1) **fleet-summary
header** (`N running · N blocked · N dead · oldest-ACT`) — highest value; (2)
**token cost / $ per phase** — data already extracted, just unsurfaced, catches
runaway loops; (3) **phase progress X-of-N** from the sessions index.

---

## Read-only / safety

The UI stays strictly read-only — no kill, release, force-complete, or signal
from any keybind (those remain operator-approval CLI actions). Visual alarms
(color, `!`, move-to-top for wedged/blocked workers) are allowed and encouraged.
This preserves the existing destructive-action discipline.

---

## Suggested scope (phased; each phase ships green, keeps the flat table + `--once` working)

- **Phase 0 — `Surface` seam + Rect, no behavior change.** Route current
  rendering through `Surface`; `format_rows`/`format_detail` unchanged output;
  land the property test (all geometries: never raises, line ≤ width). *De-risks
  everything; makes the curses loop testable for the first time.*
- **Phase 1 — Metric + Pane registry; migrate the 8 columns to built-in
  metrics.** Table pane = today's `format_rows`, byte-identical. `--cols` maps to
  metric keys (absorbs the column-sizing plan). Per-pane error boundary.
  **Ships a `gather_rows` wire-contract test (D10):** assert the row-dict keys
  `clu serve` reads (`project`, `plan`, `phase_id`, `alive`,
  `last_activity_seconds`, `ran_seconds`, `heartbeat_age_seconds`,
  `last_command`, `command_running`, `last_write`, `last_write_seconds`,
  `last_text`, `tokens`) are all present and unrenamed after the migration — a
  metric's `compute()` reads them, never mutates them. This is the only guard
  against a TUI-green-but-web-broken regression.
- **Phase 2 — Layout engine (split-tree + D2 breakpoints).** Master list + detail
  pane side-by-side / stacked / strip, chosen by `getmaxyx()`. Fleet header pane.
- **Phase 3 — Selection model + detail pane.** Sticky-by-identity, `↑↓`/`Enter`/
  `Esc`/`Tab`, fullscreen detail on narrow. Full SAYING + transcript tail.
- **Phase 4 — New metrics/panes.** Fused health glyph, token-$/phase, attempts,
  lease countdown, phase X-of-N. Each proves "one file, no engine edits."
  **Keep parity with `clu serve`'s JS:** the health thresholds (`act > 60` =
  warn, `index.html:238`) and token-summing (`tokenTotal`, `index.html:217`)
  already exist in the web. The Python metrics reimplement the same definitions
  (different runtime — can't share code, not a DRY violation); the *thresholds
  and token math must match* so the two dashboards never disagree on a worker's
  health. Cross-check the constants against `index.html` when writing them.
- **Deferred (parking lot):** user plugin dir (`~/.config/clu/top_plugins/`);
  `/` filter; column reorder; live `+`/`-` width resize (no prior art, low value).

---

## Resolved decisions (all five — approved 2026-06-03)

1. **Density vs panes (the dealbreaker) — resolved by `mockup.html`, three
   geometries not a tradeoff.** The wide-short **strip stays list-only** (full
   glance density, no detail pane stealing rows); the detail pane lives in the
   **normal desktop split** (≥80 cols *and* tall, where there's vertical room)
   and in the **phone drill-in**. The strip he glances at keeps every worker
   visible; the pane only appears when the screen can afford it.
2. **Aspect ratio → width breakpoints — yes.** `mockup.html`'s last panel is the
   width ladder verbatim (`≥80` split · `50–80` stacked · `<50` list-only ·
   `rows<12` strip · `<34×2` fallback). Matches the unanimous prior art.
3. **Tall-narrow detail — fullscreen drill.** `<50 cols` = no split; dense
   stacked list, `Enter` → fullscreen untruncated SAYING, `Esc` back (mockup
   mode ③). This is also exactly what `clu serve`'s `phone` view already does.
4. **Plugin extensibility — dropped (D11).** In-tree modularity only for v1; no
   `~/.config/clu/top_plugins/` loader. Out of scope, not deferred.
5. **Scope appetite — full 5-phase arc, green-lit.** Build Phase 0→4 end to end.
   Each phase still ships green and keeps the flat table + `--once` working, so
   the arc is interruptible, but the whole thing is approved.

**`clu serve` is the proof the design works (D10).** The web dashboard already
ships the exact three-geometry model this doc proposes — `strip`/`split`/`phone`
views, `w` to cycle, identity-based cursor re-resolution (`index.html:376`, same
as D5). The TUI redesign is porting that proven UX into curses, sharing only the
`gather_rows()` row dict. The wire-contract test (Phase 1) and threshold parity
(Phase 4) keep the two renderers from drifting.

---

## Test plan

- **The seam unlocks it:** property test over `(width, height)` ∈ 0–5 plus named
  geometries (`200×5`, `30×120`, `1×1`, `80×24`) — assert *never raises* and
  *every rendered row ≤ width*, via `BufferSurface`. Impossible today.
- **Selection identity:** three successive `Snapshot`s where the selected worker
  (a) moves position, (b) drops out, (c) the list empties — assert the cursor
  re-binds by identity / degrades gracefully. No terminal needed.
- **Per-pane error boundary:** a metric/pane that raises in compute/render →
  inline error band, TUI survives, other panes render.
- **Metric/pane purity:** each metric's `compute`/`render` and each pane's render
  tested against `BufferSurface` + fixture `Snapshot` — no curses.
- **Regression:** existing `format_rows`/`gather_rows`/`locate_transcript`/
  `RenderOnceTest` (`--once` plain path) stay green or are migrated 1:1.
- **Sanitization:** new panes rendering `last_text`/commands keep `_clean`
  (`top.py:311`) at the boundary — newlines/control/wide-CJK can't corrupt the
  grid (CursesSurface sanitizes).

## Parking lot
(empty — items deferred above live in "Suggested scope · Deferred")
