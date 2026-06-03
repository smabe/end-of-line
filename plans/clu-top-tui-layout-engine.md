# clu-top-tui-layout-engine — split-tree LayoutEngine + width breakpoints + fleet header

You are phase `layout-engine` of the `clu-top-tui` plan. Build the layout engine
that places panes by terminal width, the shared `AppState`, the fleet-summary
header pane, and `KEY_RESIZE` handling. One commit.

## Locked decisions (do NOT re-litigate)

See `plans/clu-top-tui.md`. Summary:

- New module `end_of_line/top_layout.py`: `LayoutEngine` (binary split-tree,
  `Rect` math) + `AppState` (`{selected_key, geometry, layout_preset, focus,
  scroll}`).
- **Width ladder (verbatim):** `≥80` split L/R · `50–80` stacked · `<50`
  master-only (Enter→fullscreen wired in Phase 3) · `rows<12` strip ·
  `<34×2` one-line fallback. Width-primary, not aspect ratio.
- Fleet-summary header pane (`N running · N blocked · N dead · oldest-ACT`),
  mirroring `web/index.html:251` `header()`.
- `KEY_RESIZE`: `keypad(True)`, recompute rects + recreate sub-windows (they do
  NOT auto-resize). Flicker-free: `noutrefresh()` all panes then ONE
  `doupdate()`. `erase()` not `clear()`.

## Read first

- `top.py:455-490` — the `_run_curses` loop you now drive from `LayoutEngine`.
- `end_of_line/top_render.py` (Phase 0) — `Surface`/`Rect` to render panes into.
- `end_of_line/top_registry.py` (Phase 1) — the panes/metrics the engine places.
- `top.py:343-372` — `_flex_widths`: the same min/pref/flex solver; reuse it for
  leaf-pane sizing (the engine and the column allocator are one idea at two
  scales).
- `web/index.html:251-262` (`header()`) and `:319-337` (the three view layouts:
  `split`/`strip`/`phone`) — reference behavior to match.

## Produce

1. **Failing tests first.**
   - `LayoutEngine` picks the correct split-tree per `(w, h)` across every
     breakpoint boundary — pure `Rect` assertions, no curses.
   - Fleet header counts (running/blocked/dead/oldest-ACT) from a fixture row set.
   - Resize: feeding a new `(w, h)` recomputes rects (assert old vs new differ
     where expected).

2. **Implementation.**
   - `end_of_line/top_layout.py`: `LayoutEngine` + `AppState`.
   - Fleet-summary header pane in `end_of_line/top_registry.py`.
   - `top.py`: `_run_curses` drives the engine, handles `KEY_RESIZE`
     (`keypad(True)`, recreate sub-windows), uses `noutrefresh`/`doupdate`.

3. **Acceptance.**
   - All green; full suite (report count).
   - Manual: resize the terminal across the 80 and 50 boundaries → layout flips,
     no grid corruption; shrink to `rows<12` → strip mode.

4. **Commit + attest + complete.**
   - Commit: `clu-top-tui: phase layout-engine — split-tree + width breakpoints`.
   - Stage: `end_of_line/top_layout.py`, `end_of_line/top_registry.py`,
     `end_of_line/top.py`, `tests/test_top.py`.
   - After the commit: `clu verify` then `clu attest --simplify` (each
     `--plan clu-top-tui --phase layout-engine --token <T>`).
   - `clu complete --plan clu-top-tui --phase layout-engine --token <T>`.

## Failure modes to watch

- **Sub-windows not recreated on `KEY_RESIZE`** → grid corruption. Recreate, do
  not reuse (stdlib subwindows do not auto-resize — confirmed in curses docs).
- **`Rect` off-by-one** → bottom-right overflow. Rely on the `CursesSurface`
  `try/except` + width clamp from Phase 0; don't hand-roll a second clamp.
- **Missing single `doupdate()`** after the `noutrefresh()` sweep → blank panes.
