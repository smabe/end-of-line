# clu top — operator column control

## Goal
Let the operator control `clu top`'s columns instead of only the automatic
priority sizing: hide/show columns and set per-column width (floor or greedy)
via a declarative `--cols` spec, with a live keybind to cycle a few built-in
presets, persisted across runs. Covers the field-feedback ask ("add options for
resizing the columns") and matches the column-control UX every major TUI
monitor ships.

## Non-goals
- **No interactive per-column width drag / `+`/`-` resize in the live view.**
  No major monitor does live width-resize (htop/top/k9s/btop all auto-fit
  widths — see prior art); `--cols name:40` delivers width control declaratively
  for far less code. Parked, not built.
- **No column *reorder*.** `_row_line` (top.py:375) interleaves the fixed
  numeric block (RAN/ACT/HB/PID) between the flex columns, so reordering the
  flex columns around it is a separate, larger change. Parked.
- **Detail view (`format_detail`, top.py:412) is untouched.** *Why this
  exclusion is safe:* detail mode already word-wraps every field in full and
  never truncates, so width/visibility control has no effect there — the
  feature is purely a `format_rows` (compact-view) concern. The col-spec must
  not be threaded into `format_detail`.
- No per-project `.orchestrator.json` column override — column layout is a
  per-operator display preference, not a project property. Host-level only.

## Files to touch
- **`end_of_line/top.py`** — (1) a `colspec` parse helper + the sentinel model
  (`hide`/`full`/int-floor); (2) `_flex_widths` (top.py:343) and `format_rows`
  (top.py:382) accept an optional spec: hidden columns drop out of the layout,
  floored columns clamp `_FLEX_MIN`/`_FLEX_MAX` (top.py:329-330) upward,
  `full` marks a greedy column; (3) `_run_curses` (top.py:455) — `keypad(True)`,
  a preset-cycle key added beside the `w` handler (~top.py:481), a `KEY_RESIZE`
  branch, status-line shows the active preset; (4) load/save the active
  preset+spec under `clu_config_dir()` (`_xdg_guard.py`, commit 98049b0) via
  temp-file + `os.replace`, tolerant of missing/corrupt.
- **`end_of_line/cli.py`** — add `--cols` (type=the colspec callable) to the
  `p_top` subparser (cli.py:1169-1194, mirror `--interval` at :1190); thread it
  through `cmd_top` (cli.py:3891) into `top.run`.
- **`tests/test_top.py`** — extend `FormatRowsTest` (test_top.py:325): spec
  parsing (valid + malformed + unknown-column), allocator-with-spec
  (hide drops a column, floor widens it, `full` claims remainder), preset→spec
  application (pure), persistence round-trip + corrupt-file fallback.
- **`docs/operations.md`** — `--cols` syntax + the preset key under the
  `clu top` section (ops.md:211).
- **`docs/reference.md`** — update the `top.py` section with the new public
  surface (`colspec`, spec-aware `format_rows`, persistence helpers).
- **`README.md`** — note `--cols` on the `clu top` operator-commands row.

## Failure modes to anticipate
- **`--cols` hides every flex column** → rows collapse to just the numeric
  block. Refuse to hide the last visible flex column (or always keep `name`),
  with a clear error.
- **Floor wider than the terminal** → a width floor is a *request*, not a
  guarantee; the final `line[:width]` clamp in `format_rows` still wins. The
  spec must feed the allocator as a floor, never bypass the clamp, or a wide
  floor overflows the row.
- **Unknown / malformed column token** (`name:huge`, `bogus:30`) → must surface
  as a clean argparse usage error (validate names against the known set after
  parse), never a silent no-op or a crash mid-render.
- **`KEY_RESIZE` while a preset is active** → if the loop doesn't handle it the
  grid corrupts; widths must re-derive from terminal size every redraw.
- **CLI `--cols` vs the live preset key** — precedence must be defined: `--cols`
  sets the initial layout; the preset key cycles from there. Persisting on every
  keypress would clobber a `--cols`-driven session — persist only on an explicit
  save key (top's `W` model), not on every toggle.
- **Corrupt/missing `top.json`** → load must fall back to the auto-layout
  default, never raise out of `clu top`.
- **Spec leaking into detail view** — a shared code path could accidentally
  apply the col-spec to `format_detail`; keep them separate (per the non-goal).

## Done criteria
- `clu top --cols "saying:hide,cmd:full"` hides SAYING and gives COMMAND the
  greedy remainder; `--cols name:40` floors the name column ≥40 when width
  allows — both unit-tested against `format_rows`.
- A malformed/unknown `--cols` value exits with a clean usage error (tested);
  hiding the last flex column is refused (tested).
- The live view cycles ≥2 presets via a documented key, shows the active preset
  in a status line, and survives `KEY_RESIZE`; the pure preset→spec application
  is unit-tested (curses loop itself stays thin/untested).
- The active layout persists to `clu_config_dir()/top.json` (atomic write) and
  reloads next run; round-trip + corrupt-file fallback unit-tested.
- `docs/operations.md`, `docs/reference.md`, `README.md` updated. Full suite
  green (report count).

## Parking lot
(empty at start)
