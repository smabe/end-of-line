# clu-top-tui-surface-seam — Surface/Rect rendering seam, zero behavior change

You are phase `surface-seam` of the `clu-top-tui` plan. Insert a testable
rendering seam *under* today's curses loop without changing any visible
behavior, and land the property test that the seam unlocks. One commit.

## Locked decisions (do NOT re-litigate)

See `plans/clu-top-tui.md`. Summary:

- New module `end_of_line/top_render.py`: a `Surface` base (`addstr(y, x, text)`,
  `width`, `height`); `CursesSurface` (wraps a curses window, sanitizes via
  `_clean` at the boundary, keeps the bottom-right `try/except curses.error`);
  `BufferSurface` (list-of-rows, for tests). `Rect(x, y, w, h)` frozen, no curses
  import.
- `_run_curses` (`top.py:455`) routes its inner draw through a `Surface`. **ZERO
  behavior change** — same `format_rows`/`format_detail` output, same `q`/`w`
  keys, same `erase()`-then-draw, same `addnstr` truncation.
- No panes this phase. Just the seam.
- Lazy import: `top.py` imports `top_render` *inside* `_run_curses` (as it already
  does for `curses`/`locale` at `top.py:456-457`); `top_render` imports only pure
  helpers from `top.py` at module level. One direction, no cycle.

## Read first

- `top.py:455-490` — `_run_curses` + inner `_loop`: the exact draw loop to
  refactor (`erase` at 474, `addnstr` at 477, `getmaxyx` at 469, `q`/`w` at
  482-485).
- `top.py:311-314` — `_clean`: sanitization to apply at the `CursesSurface`
  boundary.
- `top.py:382-432` — `format_rows`/`format_detail`: their output is what the
  Surface renders; unchanged here.
- `tests/test_top.py:325-377` (`FormatRowsTest`, `_row()` factory at 326) and
  `:408-416` (`RenderOnceTest`) — test patterns to mirror. Plain
  `unittest.TestCase` is fine; no registry needed for this phase.

## Produce

1. **Failing tests first** (`tests/test_top.py`, a new `SurfaceTest` +
   property test):
   - `BufferSurface` records `addstr` calls; reports `width`/`height` faithfully.
   - **Property test:** for `(w, h)` in `0..5` plus `(200,5)`, `(30,120)`,
     `(1,1)`, `(80,24)`, drive the draw path through a `BufferSurface` and assert
     it *never raises* and *every emitted row length ≤ width*.
   - `CursesSurface` bottom-right write does not propagate: a fake window that
     raises `curses.error` on the last cell → swallowed.

2. **Implementation.**
   - `end_of_line/top_render.py`: `Surface` base, `CursesSurface`,
     `BufferSurface`, `Rect`.
   - `top.py`: extract the inner draw into a function taking a `Surface`;
     `_run_curses` builds a `CursesSurface` and calls it. The `render_once`
     path is untouched.

3. **Acceptance.**
   - All new tests green; full suite green (report count).
   - `clu top --once` output byte-identical to before — capture a snapshot
     before and `diff`.
   - No new module-level import in `top.py` that creates a cycle.

4. **Commit + attest + complete.**
   - Commit: `clu-top-tui: phase surface-seam — Surface/Rect rendering seam`.
   - Stage explicit paths: `end_of_line/top_render.py`, `end_of_line/top.py`,
     `tests/test_top.py`.
   - After the commit: `clu verify --plan clu-top-tui --phase surface-seam
     --token <T>` then `clu attest --simplify --plan clu-top-tui --phase
     surface-seam --token <T>`.
   - `clu complete --plan clu-top-tui --phase surface-seam --token <T>`.

## Failure modes to watch

- **Import cycle** if `top_render` imports `top` at module level — keep `top.py`'s
  import of `top_render` lazy (inside `_run_curses`), exactly as it already does
  for `curses`/`locale`.
- **Behavior drift** — `CursesSurface` must replicate the `maxx-1` truncation of
  `addnstr` (`top.py:477`); don't switch to `addstr` without the width cap.
- **Faithless `BufferSurface`** — it must not silently pad/clip in a way that
  hides a `>width` row, or the property test passes while real curses would
  raise.
