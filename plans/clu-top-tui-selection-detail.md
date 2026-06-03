# clu-top-tui-selection-detail — sticky-by-identity selection + detail pane

You are phase `selection-detail` of the `clu-top-tui` plan. Wire the selection
model (sticky by identity), the keybindings, and the detail pane (full SAYING +
transcript tail), including the fullscreen drill on narrow terminals. One commit.

## Locked decisions (do NOT re-litigate)

See `plans/clu-top-tui.md`. Summary:

- **Selection sticky-by-identity** `(project, plan, phase_id)`, re-resolved every
  tick — never by list index. Clamp (no wrap), default row 0. This mirrors
  `web/index.html:376-385` exactly.
- **Keys (no F-keys):** `↑↓`/`j k` move · `g`/`G`/`Home`/`End` · `PgUp`/`PgDn` ·
  `Tab` focus list↔detail (scroll detail) · `Enter` drill-in (fullscreen detail
  on narrow) · `Esc` back · `w` cycle layout · `?`/`h` help · `q` quit.
- **Detail pane:** full untruncated SAYING → transcript tail → time-on-phase +
  lease countdown → attempts X/max → files-touched → token $. Reuse
  `format_detail`/`_wrap_field`.
- **Read-only is a hard invariant (D7):** no kill/release/force-complete/signal
  keybind, ever. Visual alarms only.

## Read first

- `web/index.html:376-385` (`wkey` + `findIndex` re-resolve) and `:280-300`
  (`detailHTML`) — the exact reference model the curses side reproduces.
- `top.py:404-432` — `format_detail`/`_wrap_field` to reuse for word-wrapped
  fields.
- `end_of_line/top_layout.py` (Phase 2) — `AppState`: `selected_key`/`focus`/
  `scroll` live here.
- `end_of_line/top_registry.py` — the detail pane registers here.
- `tests/test_top.py:282-322` — `GatherRowsTest`/`GitProjectTestCase` for
  multi-row fixtures.

## Produce

1. **Failing tests first.**
   - **Selection identity** across three successive snapshots where the selected
     worker (a) moves position → cursor follows by identity, (b) drops out →
     degrades gracefully (clamps, no crash), (c) the list empties → no crash.
     Pure, no curses.
   - Detail pane renders the full untruncated SAYING for the selected row.

2. **Implementation.**
   - `AppState` selection re-resolve by `(project, plan, phase_id)` key.
   - Key handlers (`↑↓`/`j k`/`g`/`G`/`PgUp`/`PgDn`/`Tab`/`Enter`/`Esc`).
   - Detail pane in `end_of_line/top_registry.py`.
   - Fullscreen drill on `<50` cols (`Enter` opens, `Esc` returns).

3. **Acceptance.**
   - All green; full suite (report count).
   - Manual: select a worker, let one above it complete → highlight stays on the
     same worker; `Tab` scrolls the detail without moving selection; on a narrow
     terminal `Enter`→fullscreen, `Esc` back.

4. **Commit + attest + complete.**
   - Commit: `clu-top-tui: phase selection-detail — sticky selection + detail pane`.
   - Stage: `end_of_line/top_layout.py`, `end_of_line/top_registry.py`,
     `end_of_line/top.py`, `tests/test_top.py`.
   - After the commit: `clu verify` then `clu attest --simplify` (each
     `--plan clu-top-tui --phase selection-detail --token <T>`).
   - `clu complete --plan clu-top-tui --phase selection-detail --token <T>`.

## Failure modes to watch

- **Index-based retargeting** when a worker above the cursor completes — THE #1
  QA risk. Selection must re-resolve by identity, not by row index.
- **Any state-mutating keybind** — forbidden. Read-only is the invariant; only
  navigation and focus keys exist.
- **Detail scroll offset not reset** when the selection changes → the pane shows
  a stale scroll position for a different worker.
