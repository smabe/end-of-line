# clu-top-tui — modular multi-pane TUI for `clu top`

Rebuild `clu top` (`end_of_line/top.py`, 506 lines) from one flat curses table
into a btop-like modular TUI: a master worker-list + detail pane, panes built
from a registry of metrics (add one in a single file, no engine edits), laid out
by an engine that adapts to terminal width. Approved design doc:
`plans/clu-top-tui-master.md` (this plan is its execution split). Five sequential
phases, each editing `top.py` and its new sibling modules, so this is ONE plan
run as a phase sequence — never a parallel batch.

The redesign is de-risked by `clu serve`, which already ships this exact
three-geometry UX in the browser (`web/index.html`: strip/split/phone, `w` to
cycle, identity-based cursor). The TUI is porting proven behavior into curses.
The two renderers share exactly one seam — `top.gather_rows()`'s row dict — which
is a FROZEN contract this plan must not break (D10 below).

## Locked design decisions

### Phase 0 — surface-seam (the testability unlock)
- **`Surface` rendering seam in a new `end_of_line/top_render.py`.** A `Surface`
  base with `addstr(y, x, text)` / `width` / `height`, a real `CursesSurface`
  (wraps a curses window; sanitizes via `_clean` at the boundary; keeps the
  bottom-right-corner `try/except curses.error` idiom — verified raises per
  Python 3.11 curses docs), and a `BufferSurface` (list-of-rows, for tests).
- **`Rect` dataclass** (`x, y, w, h`) — frozen; pure geometry, no curses import.
- **`_run_curses` (`top.py:455`) routes its inner draw through a `Surface`** with
  **zero behavior change**: same `format_rows`/`format_detail` output, same
  `q`/`w` keys, same `erase()`-then-draw, same `addnstr` truncation (`top.py:477`).
  This phase adds no panes — it only inserts the seam under today's rendering.
- **Property test lands here** (impossible today): over `(width, height)` in 0–5
  plus named geometries (`200×5`, `30×120`, `1×1`, `80×24`), assert rendering
  *never raises* and *every row ≤ width*, via `BufferSurface`.
- **Lazy import to avoid cycles:** `top.py` imports `top_render` / later TUI
  modules *inside* `_run_curses` (mirrors how it already imports `curses`/`locale`
  inside the function at `top.py:456-457`); the new modules import pure helpers
  *from* `top.py` at module level. One direction, no module-level cycle.

### Phase 1 — registry (metrics/panes + the wire contract)
- **`Metric` and `Pane` are frozen dataclasses** in a new
  `end_of_line/top_registry.py`, registered via `@register_metric` /
  `@register_pane` decorators into two module dicts. Mirrors the repo's
  `@dataclass(frozen=True)` idiom (`notify_base.py:21`, `fleet.py`, `registry.py`).
  Not Protocol — value + 2 funcs.
- **`Metric` = `{key, label, compute(snapshot,row)->v, render(v,width)->str,
  sort_key, cost∈{cheap,transcript}, align}`.** Today's 8 columns
  (name/ran/act/hb/pid/command/wrote/saying + tokens) become built-in metrics.
- **`Snapshot`** owns the single per-tick row gather (wraps `gather_rows()`),
  memoized so a hidden metric costs nothing.
- **`table` pane = today's `format_rows` output, byte-identical.** The pane wraps
  `top.format_rows(rows, width=…)` (`top.py:382`); the existing `_flex_widths`
  allocator (`top.py:343`) is reused unchanged. `RenderOnceTest` stays green 1:1.
- **`gather_rows()` is a FROZEN wire contract (D10).** `clu serve`
  (`webserver.py:346` → `/api/workers` → `web/index.html:235` `toView`) reads the
  raw row dict and renders columns in its own JS — zero shared code with the
  curses layer. Metrics read FROM the row dict (`assemble_row`, `top.py:263-275`);
  they must not rename, drop, or relocate any of its 13 keys: `project`, `plan`,
  `phase_id`, `alive`, `ran_seconds`, `last_activity_seconds`,
  `heartbeat_age_seconds`, `last_command`, `command_running`, `last_write`,
  `last_write_seconds`, `last_text`, `tokens`.
- **Per-pane error boundary:** a metric/pane that raises in compute/render
  renders an inline error band; the TUI survives and other panes draw.
- **Minimal `--cols metric_key,…`** on the `p_top` subparser (near
  `cli.py:1193`, mirror `--interval`), threaded through `cmd_top` (`cli.py:4023`)
  into `top.run`. Validated against the known metric-key set → clean argparse
  usage error on unknown/malformed. **Absorbs `plans/clu-top-column-sizing.md`
  (D9)** — that standalone plan is deleted in this phase. Preset cycling +
  persistence stay parked (parking lot).

### Phase 2 — layout-engine (split-tree + width breakpoints)
- **`LayoutEngine` in a new `end_of_line/top_layout.py`:** a binary split-tree
  (leaf pane | H/V split with weighted children) sized by `Rect` math, choosing
  shape by **terminal width** (D2 ladder, verbatim from the approved mockup):
  `cols≥80` → master-list left / detail right · `50–80` → list above, detail
  stacked below · `<50` → master-only (Enter→fullscreen, wired Phase 3) ·
  `rows<12` → strip (list-only / 1-line ticker) · `<34×2` → one-line fallback.
- **Width-primary, not aspect ratio** — matches every verified tool
  (htop/k9s/lazygit/btop) and `clu serve`'s own JS.
- **`AppState`** (`{selected_key, geometry, layout_preset, focus, scroll}`)
  introduced here, shared, panes read/write it.
- **Fleet-summary header pane** (`N running · N blocked · N dead · oldest-ACT`)
  — highest operator-ranked new pane; mirrors `web/index.html:251` `header()`.
- **`KEY_RESIZE` handled** (absent today): `keypad(True)`, recompute rects +
  recreate sub-windows on resize (stdlib subwindows do NOT auto-resize — verified
  per curses docs). Flicker-free `noutrefresh()`-all-then-one-`doupdate()`.

### Phase 3 — selection-detail (sticky-by-identity + detail pane)
- **Selection sticky-by-identity** `(project, plan, phase_id)`, re-resolved every
  tick — never by list index. Clamp (no wrap), default row 0. Exactly the
  `clu serve` model (`web/index.html:376-385`, the `wkey`/`findIndex` re-resolve).
- **Keys (no F-keys — they break over iPhone ssh):** `↑↓`/`j k` move · `g`/`G`/
  `Home`/`End` · `PgUp`/`PgDn` · `Tab` focus list↔detail (scroll detail) ·
  `Enter` drill-in (fullscreen detail on narrow) · `Esc` back · `w` cycle layout ·
  `?`/`h` help · `q` quit.
- **Detail pane** = full untruncated SAYING → recent transcript tail → time-on-
  phase + lease countdown → attempts X/max → files-touched → token $. Reuses
  `format_detail`/`_wrap_field` (`top.py:404-432`) for word-wrapped fields.
- **Read-only is a hard invariant (D7):** no kill/release/force-complete/signal
  keybind, ever. Visual alarms (color/`!`/move-to-top) only.

### Phase 4 — new-metrics (proves "one file, no engine edits")
- **Fused health glyph** (🟢 ok / 🟡 watch / 🔴 dead from PID + HB + ACT +
  stuck-command) — one signal, so the dangerous PID-ok-but-ACT-stale silent wedge
  can't be missed (D8).
- **New metrics, each added in `top_registry.py` only:** token-$/phase, attempts
  X/max, lease-remaining countdown, phase X-of-N.
- **Web threshold parity:** the health cutoff (`act > 60` = warn) and token-sum
  math already live in `web/index.html:238` (`toView`) and `:217` (`tokenTotal`).
  The Python metrics reimplement the same definitions (different runtime — can't
  share code, not a DRY violation); the thresholds and token math MUST match so
  the two dashboards never disagree on a worker's health. Cross-check constants
  against `index.html` when writing them.

## Non-goals

- **No user plugin dir** (`~/.config/clu/top_plugins/` + `pkgutil.iter_modules`).
  Out of scope, not deferred (D11). In-tree modularity only: add a metric/pane in
  one repo file. *Why safe to exclude:* single operator; every named pane belongs
  in clu's tree; a drop-a-file loader means `exec`-ing operator Python + a
  committed-stable plugin API + third-party error isolation — none earns its keep
  without a pane that shouldn't live in the repo.
- **`gather_rows()` row-dict shape is not changed.** Metrics read it; they don't
  reshape it (the D10 contract, restated as a boundary). *Append-only exception:*
  Phase 4 may ADD a new key for a new metric, but only if it also surfaces that
  key to `web/index.html`'s `toView` so the two dashboards stay in parity — never
  rename or drop an existing key.
- **No live `+`/`-` column-width resize, no column reorder, no preset
  persistence.** No prior art (htop/k9s/btop all auto-fit); parked from the
  absorbed column-sizing plan. *Why safe:* `--cols` delivers declarative width/
  visibility control for far less code; these are pure additions later if wanted.
- **`format_detail` keeps its own full-wrap behavior** — the col-spec/metric
  width sentinels never thread into detail rendering (it never truncates).
- **No new third-party deps.** Python 3.11+, stdlib only, curses, unittest.

## Files touched

- `end_of_line/top.py` — P0,P1,P2,P3,P4 modified — `_run_curses` refactored to
  drive panes via `Surface` + `LayoutEngine`; `gather_rows`/`assemble_row`/
  `format_rows`/`format_detail`/`_flex_widths`/`_clean`/`human_age` preserved and
  reused. **API hotspot: `gather_rows()` row-dict shape is FROZEN (clu serve).**
- `end_of_line/top_render.py` — P0 NEW — `Surface`/`CursesSurface`/
  `BufferSurface`/`Rect`.
- `end_of_line/top_registry.py` — P1 NEW, P3+P4 extended — `Metric`/`Pane`
  frozen dataclasses, `@register_metric`/`@register_pane`, `Snapshot`, built-in
  metrics + panes. **API hotspot: the metric-key set `--cols` validates against.**
- `end_of_line/top_layout.py` — P2 NEW, P3 extended — `LayoutEngine` (split-tree
  + breakpoints), `AppState` (selection/geometry/focus/scroll).
- `end_of_line/cli.py` — P1 modified — `--cols` on `p_top` (near `:1193`),
  threaded through `cmd_top` (`:4023`) into `top.run`.
- `tests/test_top.py` — P0,P1,P2,P3,P4 modified — property test (BufferSurface),
  wire-contract test, metric/pane purity, selection-identity, layout,
  error-boundary. Reuses `FormatRowsTest._row()` (`test_top.py:326`) +
  `GitProjectTestCase`.
- `docs/reference.md` — P1,P2 modified — new public surface under the `top.py`
  section (`:855`): `Metric`/`Pane`/`Snapshot`/`Surface`/`LayoutEngine`.
- `docs/operations.md` — P1,P4 modified — `--cols` syntax + new keybindings/panes
  under `clu top` (`:211`).
- `README.md` — P4 modified — note the modular panes / `--cols` on the `clu top` row.
- `plans/clu-top-column-sizing.md` — P1 DELETE — absorbed by D9.

## Per-phase done checklist

- TDD: failing tests first (AAA, factory helpers).
- `/code-review` after (every phase here is >1 file / >30 lines).
- Full suite green: `python3 -m unittest discover -s tests` (report count).
- Structured commit (Title / Why / What's new / Under the hood / Tests /
  `Co-Authored-By:` trailer). Stage explicit paths — no `git add -A`.
- **After the commit:** `clu verify` then `clu attest --simplify` (each
  `--plan clu-top-tui --phase <id> --token <T>`), then
  `clu complete --plan clu-top-tui --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| surface-seam | `clu-top-tui-surface-seam.md` | Phase 0: `Surface`/`Rect` seam, route `_run_curses` through it, property test, zero behavior change | 2h |
| registry | `clu-top-tui-registry.md` | Phase 1: `Metric`/`Pane` registry, migrate 8 cols to metrics, table pane byte-identical, `gather_rows` wire-contract test, per-pane error boundary, minimal `--cols`, delete column-sizing plan | 3h |
| layout-engine | `clu-top-tui-layout-engine.md` | Phase 2: split-tree `LayoutEngine` + width breakpoints, `AppState`, master+detail panes, fleet-header pane, `KEY_RESIZE` | 3h |
| selection-detail | `clu-top-tui-selection-detail.md` | Phase 3: sticky-by-identity selection, `↑↓`/`Enter`/`Esc`/`Tab` keys, fullscreen detail on narrow, full SAYING + transcript tail | 3h |
| new-metrics | `clu-top-tui-new-metrics.md` | Phase 4: fused health glyph + token-$/attempts/lease/phase-X-of-N metrics, web threshold parity | 2h |
