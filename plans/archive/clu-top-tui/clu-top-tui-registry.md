# clu-top-tui-registry — Metric/Pane registry + frozen gather_rows wire contract

You are phase `registry` of the `clu-top-tui` plan. Introduce the metric/pane
registry, migrate today's 8 columns to built-in metrics with the table pane
byte-identical, land the `gather_rows` wire-contract test that protects
`clu serve`, and add a minimal `--cols`. One commit.

## Locked decisions (do NOT re-litigate)

See `plans/clu-top-tui.md`. Summary:

- New module `end_of_line/top_registry.py`: `Metric` + `Pane` as
  `@dataclass(frozen=True)` (mirror `notify_base.py:21`), registered via
  `@register_metric`/`@register_pane` into two module dicts. `Snapshot` wraps
  `gather_rows`, memoized.
- `Metric = {key, label, compute(snapshot,row)->v, render(v,width)->str,
  sort_key, cost, align}`. The 8 columns become built-in metrics.
- `table` pane wraps `top.format_rows` **byte-identical**; reuse `_flex_widths`
  unchanged.
- **`gather_rows` is FROZEN (D10):** metrics read FROM the row dict, never
  reshape it. The 13 keys are listed in the master.
- Per-pane error boundary: a pane/metric that raises → inline error band, TUI
  survives, other panes draw.
- Minimal `--cols metric_key,…` on `p_top` (`cli.py` near 1193), threaded via
  `cmd_top` (`cli.py:4023`). Validate against the known key set → clean usage
  error. Delete `plans/clu-top-column-sizing.md`. Presets/persistence PARKED.

## Read first

- `top.py:255-308` — `assemble_row` (the 13 keys at 263-275) + `gather_rows`:
  the wire contract you must not break.
- `top.py:329-401` — `_FLEX_MIN`/`_FLEX_MAX`, `_flex_widths`, `_row_cells`,
  `_row_line`, `format_rows`: what becomes metrics; the table pane reuses these.
- `webserver.py:343-351` (`workers_json`) + `web/index.html:234-247` (`toView`)
  — the consumer the contract test protects. Read both so you understand which
  keys the web depends on.
- `notify_base.py:21` — the `@dataclass(frozen=True)` idiom to mirror.
- `cli.py:1172-1194` (`p_top`), `:4020-4023` (`cmd_top`) — where `--cols` threads
  (mirror `--interval` at `:1193`).
- `tests/test_top.py:282-377` — `GatherRowsTest` (`GitProjectTestCase`) +
  `FormatRowsTest` (`_row()` at 326) patterns.

## Produce

1. **Failing tests first.**
   - Metric/pane purity: each built-in metric's `compute`/`render` against a
     fixture `Snapshot` + a `_row()` dict — no curses.
   - **Wire-contract test:** build a row via `GitProjectTestCase` + a claim,
     assert the `gather_rows` row dict contains all 13 keys, unrenamed. This is
     the `clu serve` guard — do not weaken it.
   - Table pane render `==` `format_rows` output byte-identical across widths.
   - Per-pane error boundary: a metric that raises → error band, others render.
   - `--cols` parsing: valid keys accepted; unknown key → usage error; malformed
     → usage error.

2. **Implementation.**
   - `end_of_line/top_registry.py`: `Metric`/`Pane`/registries/`Snapshot`/8
     built-in metrics/`table` pane/error boundary.
   - `end_of_line/cli.py`: `--cols` on `p_top`, threaded through `cmd_top` into
     `top.run`.
   - Delete `plans/clu-top-column-sizing.md`.

3. **Acceptance.**
   - All green; full suite (report count).
   - `clu top --once` byte-identical to before.
   - `clu top --cols saying,cmd` shows only those metrics.
   - `clu serve` `/api/workers` still returns all 13 keys (the wire-contract test
     covers this; spot-check by reading `workers_json` is enough).

4. **Commit + attest + complete.**
   - Commit: `clu-top-tui: phase registry — metric/pane registry + wire contract`.
   - Stage: `end_of_line/top_registry.py`, `end_of_line/cli.py`,
     `tests/test_top.py`, and the `plans/clu-top-column-sizing.md` deletion.
   - After the commit: `clu verify` then `clu attest --simplify` (each
     `--plan clu-top-tui --phase registry --token <T>`).
   - `clu complete --plan clu-top-tui --phase registry --token <T>`.

## Failure modes to watch

- **Silent `clu serve` break:** a metric pulls a key out of the row dict and a
  later "tidy" of `gather_rows` drops it. The wire-contract test is the only
  guard — keep it asserting every one of the 13 keys.
- **`--cols` hides everything** → keep `name` always visible, or refuse with a
  clean error.
- **`Snapshot` memoized on the wrong key** → stale rows across ticks. Memoize
  per gather, not globally.
