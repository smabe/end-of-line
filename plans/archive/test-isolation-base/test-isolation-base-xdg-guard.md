# test-isolation-base-xdg-guard — runtime XDG safety net

You are phase `xdg-guard` of the `test-isolation-base` plan. Add a production
runtime guard that refuses writes to real `~/.config/clu/` when `CLU_TEST_MODE=1`
is set. Pairs with phase 1's `CluTestCase` — any future test class that forgets
to subclass it and writes to real XDG paths will hard-fail with a clear error
instead of silently leaking ghost state into the operator's inbox.

## Locked decisions (do NOT re-litigate)

See `plans/test-isolation-base.md`. Summary:

- **Guard helper:** `end_of_line/_xdg_guard.py` exposes
  `assert_xdg_safe(path: Path) -> None`. Raises `RuntimeError` (NOT a custom
  exception — keep it simple) with a message naming `CluTestCase` when
  `os.environ.get("CLU_TEST_MODE")` is truthy AND `path.resolve()` is under
  `Path.home().resolve()`.
- **Performance contract:** O(1) — one env lookup + one string-prefix compare
  on resolved paths. No filesystem stats, no logging on success.
- **Integration sites:** three XDG path producers — `inbox.inbox_root()`
  (inbox.py:28), `registry.registry_path()` (registry.py:31),
  `monitor.marker_path()` (monitor.py).
- **Hook defense:** `end_of_line/hooks/clu_inbox_surface.py` explicitly
  `os.environ.pop("CLU_TEST_MODE", None)` at entry so weird shell inheritance
  can't false-trip the guard in real sessions.

## Read first

- `end_of_line/inbox.py:28` — `inbox_root()` — where the guard call slots in
  (before returning).
- `end_of_line/registry.py:31` — `registry_path()` — same pattern.
- `end_of_line/monitor.py` — `marker_path()` (grep to find exact line).
- `end_of_line/hooks/clu_inbox_surface.py` — the hook entry point. Add the
  `os.environ.pop` call at the top, before any clu module imports (since those
  imports might trigger `assert_xdg_safe` at import time if they evaluate
  module-level XDG paths).
- `tests/__init__.py` — confirm `CluTestCase` exists from phase 1 (you'll
  reference it in the guard's error message).

## Produce

1. **Failing tests first.** New `tests/test_xdg_guard.py` with at minimum:
   - `test_guard_raises_on_real_xdg_in_test_mode` — manually set
     `os.environ["CLU_TEST_MODE"] = "1"`, manually set
     `os.environ["XDG_CONFIG_HOME"]` to `str(Path.home() / ".config")` (real
     path), call `inbox.write_event(...)` with a synthetic event, assert
     `RuntimeError` with a message containing "CluTestCase".
   - `test_guard_silent_on_isolated_xdg_in_test_mode` — set `CLU_TEST_MODE=1`
     and `XDG_CONFIG_HOME` to `tmp_path`, call `inbox.write_event(...)`,
     assert NO exception (the guard is silent when the path is isolated).
   - `test_guard_silent_outside_test_mode` — unset `CLU_TEST_MODE`, set
     `XDG_CONFIG_HOME` to a real path, call any of the three readers, assert
     NO exception.
   - Repeat the first case for `registry.register(...)` and
     `monitor.marker_path()` so all three integration sites are covered.
   These tests deliberately do NOT inherit `CluTestCase` (they need to
   control the env themselves). Use `unittest.TestCase` + manual
   `os.environ` patches with `addCleanup` restore.

2. **Implementation: `end_of_line/_xdg_guard.py`** (new file).
   ```python
   """Refuse XDG writes to real ~/.config/clu/ when CLU_TEST_MODE=1.

   Pairs with CluTestCase in tests/__init__.py. Defense-in-depth so a test
   class that forgets to subclass CluTestCase hard-fails instead of silently
   leaking ghost state into the operator's real inbox / registry / monitor
   marker.
   """
   from __future__ import annotations
   import os
   from pathlib import Path

   _SENTINEL = "CLU_TEST_MODE"


   def assert_xdg_safe(path: Path) -> None:
       if not os.environ.get(_SENTINEL):
           return
       try:
           resolved = path.resolve()
           home = Path.home().resolve()
       except OSError:
           return  # path doesn't exist yet — let the caller fail naturally
       try:
           resolved.relative_to(home)
       except ValueError:
           return  # not under home, safe
       raise RuntimeError(
           f"refusing XDG write to {resolved!s} while CLU_TEST_MODE=1 — "
           f"test class likely missing CluTestCase isolation (see "
           f"tests/__init__.py:CluTestCase)"
       )
   ```

3. **Integrate at the three sites.**
   - `end_of_line/inbox.py`: `from ._xdg_guard import assert_xdg_safe`; in
     `inbox_root()` body, immediately before returning, `assert_xdg_safe(path)`.
   - `end_of_line/registry.py`: same pattern in `registry_path()`.
   - `end_of_line/monitor.py`: same pattern in `marker_path()`.

4. **Hook defense.** `end_of_line/hooks/clu_inbox_surface.py` — at the very top
   of `main()` (or the script's entry, before any clu module imports if the
   imports are deferred): `os.environ.pop("CLU_TEST_MODE", None)`. Add a
   single-line comment naming this as defense against shell-env inheritance.

5. **Acceptance.**
   - `python3 -m unittest discover -s tests` shows 538 + N ≥ 4 tests, all
     green (N = the new guard-test cases).
   - `grep -n "assert_xdg_safe" end_of_line/inbox.py end_of_line/registry.py
     end_of_line/monitor.py` shows one call each.
   - The hook script's `os.environ.pop` is the first executable line of
     `main()`.
   - Manual smoke: `env CLU_TEST_MODE=1 python3 -c "from end_of_line import
     inbox; inbox.inbox_root()"` raises with the expected error message.

6. **Commit + complete.**
   - One commit: structured-format message titled `test-isolation-base: phase
     xdg-guard — runtime XDG safety net (closes #22)`.
   - Stage explicit paths: `end_of_line/_xdg_guard.py`, `end_of_line/inbox.py`,
     `end_of_line/registry.py`, `end_of_line/monitor.py`,
     `end_of_line/hooks/clu_inbox_surface.py`, `tests/test_xdg_guard.py`.
   - `clu complete --plan test-isolation-base --phase xdg-guard --token <T>`
     with the worker token. Summary: closes #22, tests delta, files touched.

## Failure modes to watch

- **`path.resolve()` on a path whose parent doesn't exist** — Python's `resolve`
  is lenient (returns the joined path), but if the path involves a symlink
  cycle it could raise. The `try/except OSError` returns early in that case;
  the caller's own filesystem operation will surface the real error.
- **`Path.home()` resolution under HOME patching** — `Path.home()` reads `HOME`
  env var at call time, so it always tracks the current isolation. No baseline
  to drift from.
- **Hook script inheriting `CLU_TEST_MODE=1` from an unusual parent process** —
  the explicit `os.environ.pop` at hook entry guards this. Test manually:
  `CLU_TEST_MODE=1 python3 end_of_line/hooks/clu_inbox_surface.py` should not
  raise.
- **`monitor.marker_path()` may not be the only XDG-resolving function in
  monitor.py** — grep for `Path.home` and `XDG_CONFIG_HOME` in monitor.py
  before declaring done. Guard ALL of them.
- **The `tests/__init__.py` CluTestCase from phase 1 should not raise** when
  it patches `CLU_TEST_MODE=1` because `XDG_CONFIG_HOME` is also patched to
  the temp dir; the guard sees the temp path is not under `Path.home()` and
  returns silently. Verify by running the full suite after integration.
- **No other XDG-writing path bypasses the three guarded functions** — run
  `grep -rn "Path.home\|XDG_CONFIG_HOME" end_of_line/` and confirm every
  match goes through one of the three guarded helpers.
