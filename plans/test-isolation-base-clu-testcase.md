# test-isolation-base-clu-testcase — `CluTestCase` base + migrate 6 leak vectors

You are phase `clu-testcase` of the `test-isolation-base` plan. Add a unittest
base class that auto-isolates XDG paths in `setUp`, then migrate the 6 canonical
leak-vector test classes to subclass it. This is the structural precursor for
phase 2's runtime XDG guard.

## Locked decisions (do NOT re-litigate)

See `plans/test-isolation-base.md`. Summary:

- New base class lives in `tests/__init__.py` next to `isolate_registry` (the
  lower-level primitive stays as-is).
- `CluTestCase.setUp` creates `self.tmp_path` via `tempfile.TemporaryDirectory`
  (cleaned with `addCleanup`), calls `isolate_registry(self, self.tmp_path)`, and
  patches `CLU_TEST_MODE=1` via `mock.patch.dict` with `addCleanup` restore.
- Subclasses MUST call `super().setUp()` FIRST before any registry/inbox-touching
  code. Docstring spells this out.
- Migration is mechanical: replace the manual `isolate_registry(self,
  self.project)` calls in 6 setUps with `class X(CluTestCase)` + first-line
  `super().setUp()` (and adjust `self.project` references to use `self.tmp_path`
  if they're the same temp dir).

## Read first

- `tests/__init__.py:10-21` — current `isolate_registry` body and signature.
  Your base class wraps this; the helper stays.
- The 6 canonical setUps that currently call `isolate_registry`:
  - `tests/test_supervisor.py:31` (`SupervisorTestCase`)
  - `tests/test_heartbeat.py:98` (`HeartbeatWorkerTestCase`),
    `tests/test_heartbeat.py:137` (`StalledSupervisorTestCase`)
  - `tests/test_dispatch.py:33` (`DispatchTestCase`)
  - `tests/test_lifecycle.py:29` (LifecycleTestCase or similar)
  - `tests/test_tick_all.py:33` (`TickAllTestCase`)
  Each currently does roughly: `self.project = tmp_path; isolate_registry(self,
  self.project); main(["init", ...])` or similar.
- One existing class that does NOT need migration (any test class without
  `isolate_registry`) — leave alone unless its setUp obviously calls `tick()` /
  `main(["init"])`. Don't widen scope.

## Produce

1. **Failing test first.** Write `tests/test_clu_testcase.py` with two cases:
   - A class subclassing `CluTestCase` exposes `self.tmp_path` (Path), and
     `os.environ["CLU_TEST_MODE"] == "1"` inside the test body.
   - After test teardown, `CLU_TEST_MODE` is gone from `os.environ` (verify via
     a sibling plain-`unittest.TestCase` that asserts it's not set).
   This will fail at first because `CluTestCase` doesn't exist yet.

2. **Implementation: `tests/__init__.py`.**
   ```python
   class CluTestCase(unittest.TestCase):
       """unittest base that isolates XDG paths and sets CLU_TEST_MODE=1.

       Subclasses MUST call `super().setUp()` BEFORE any registry/inbox-
       touching code. Pairs with the phase-2 XDG guard, which refuses
       writes to real ~/.config/clu/ under CLU_TEST_MODE=1.
       """
       def setUp(self) -> None:
           super().setUp()
           tmp = tempfile.TemporaryDirectory()
           self.addCleanup(tmp.cleanup)
           self.tmp_path = Path(tmp.name)
           isolate_registry(self, self.tmp_path)
           patcher = mock.patch.dict(os.environ, {"CLU_TEST_MODE": "1"})
           patcher.start()
           self.addCleanup(patcher.stop)
   ```
   Required imports added at the top of `tests/__init__.py` (`os`, `tempfile`,
   `unittest`, `mock`, `Path`).

3. **Migration: 6 setUps.**
   For each of the 6 classes, change `class X(unittest.TestCase):` to
   `class X(CluTestCase):`, and rewrite `setUp` so the first line is
   `super().setUp()`. Drop the now-redundant `isolate_registry(...)` call.
   Update any `self.project = self.tmp_path` aliasing if the original used a
   different temp dir name.
   Import: `from . import CluTestCase` (or via `from tests import CluTestCase`
   depending on the existing test module import style — match what's already
   there).

4. **Acceptance.**
   - `python3 -m unittest discover -s tests` shows 536+2 = 538 tests, all green.
   - `grep -n "isolate_registry" tests/test_supervisor.py tests/test_heartbeat.py
     tests/test_dispatch.py tests/test_lifecycle.py tests/test_tick_all.py`
     returns zero matches (calls removed; helper still imported only if other
     classes in the file use it).
   - The new `CluTestCase` is exported from `tests/__init__.py` so subclasses
     can import cleanly.

5. **Commit + complete.**
   - One commit: structured-format message titled `test-isolation-base: phase
     clu-testcase — CluTestCase base + 6-class migration (#22 in flight)`.
   - Stage explicit paths only (`tests/__init__.py` + 5 migrated test files +
     new `tests/test_clu_testcase.py`).
   - `clu complete --plan test-isolation-base --phase clu-testcase --token <T>`
     with the worker token. Summary line: tests delta (536 → 538), files
     touched.

## Failure modes to watch

- **`super().setUp()` ordering** — if a migrated subclass does
  `self.project = tmp_dir; super().setUp()` instead of the other way, the
  isolate happens after the manual tmp_dir setup and the test runs against
  unisolated state. Always `super().setUp()` first.
- **Existing setUps that use `self.project` to mean something other than the
  isolated tmp dir** — read each setUp before changing. If `self.project` is a
  semantically different path (e.g. a fake project root with files), preserve
  it but ensure the registry isolation still happens via `self.tmp_path`
  (different dir).
- **`addCleanup` LIFO ordering** — the `TemporaryDirectory` cleanup runs LAST
  (registered first), so the env patcher stop and registry patcher stop run
  before the tmpdir is removed. Correct order.
- **Forgetting to import `CluTestCase`** in the migrated files — Python will
  NameError at import time. Run the suite immediately after each migration.
