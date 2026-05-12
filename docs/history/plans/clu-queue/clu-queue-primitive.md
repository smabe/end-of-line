# clu-queue-primitive — extract `locked_json`, scaffold `queue.py`

You are phase `primitive` of the `clu-queue` plan. Your job: extract
the duplicated lock+load+yield+save pattern from `state.mutate` and
`registry._mutate` into a shared `state.locked_json` primitive, then
build the `end_of_line/queue.py` module on top of it. No new CLI, no
new worker dispatch — just the persistence layer.

Design pass is done. Full context in
`.claude/plans/plan-queue-master.md` and `plans/clu-queue.md`. Do not
redesign.

## Locked decisions (do NOT re-litigate)

- **Extract `state.locked_json(path, *, expected_version, empty)`**
  with signature:
  ```python
  @contextmanager
  def locked_json(
      path: Path,
      *,
      expected_version: int,
      empty: Callable[[], dict],
  ) -> Iterator[dict]:
      """Generic lock + load + yield + atomic-write for clu JSON files.
      Tolerates missing file via `empty()` factory."""
  ```
- **`state.mutate` becomes a thin wrapper.** CLAUDE.md invariant
  `with st.mutate(path) as data:` MUST still work for every existing
  caller. state.mutate calls locked_json with state's version and
  empty factory.
- **`registry._mutate` collapses** to a `locked_json` call with
  registry's version + empty factory.
- **`end_of_line/queue.py`** new module. Top-level:
  - `SCHEMA_VERSION = 1`
  - `_empty()` returns `{"schema_version": 1, "queue": [], "history": []}`
  - `load(path)` — calls `state.load(path, expected_version=SCHEMA_VERSION)` (the existing primitive already raises `SchemaVersionMismatch` correctly).
  - `save_atomic(path, data)` — delegate to `state.save_atomic`.
  - `mutate(path)` — thin wrapper over `state.locked_json` with queue's version + empty factory.
- **`ProjectConfig.queue_path()`** — new method, returns
  `self._orchestrator_dir / "queue.json"` (mirrors how `state_path`
  builds `.orchestrator/<slug>.state.json`).
- **Test helper**: `tests.isolate_queue(self, project_root)` or
  extend the existing `tests.isolate_registry` helper so a single
  setUp call covers both. Pick whichever has less surface; the
  point is that every queue test gets both registry isolation AND a
  per-test queue file path.

## Read first

- `end_of_line/state.py` — full file. The `mutate` context manager
  (look for `@contextmanager` near line 200) and `save_atomic` /
  `load` / `locked` primitives. The `O_NOFOLLOW + flock` ceremony in
  `state.locked` is load-bearing — don't change it.
- `end_of_line/registry.py` — full file (117 lines). `_mutate` is
  the second copy of the pattern. Note that registry's `_load` has
  its own version constant — that's the difference `locked_json` is
  parameterizing over.
- `end_of_line/config.py` — `ProjectConfig.state_path()` to mirror
  the shape for `queue_path()`.
- `tests/` — find `isolate_registry` (likely in `tests/__init__.py`
  or a base test class) to understand the pattern before extending it.
- `CLAUDE.md` — re-read the conventions section, especially the
  `with st.mutate` rule and the test isolation requirement.

## Produce

1. **TDD: failing tests first.** Add `tests/test_queue_primitive.py`
   (new file) with:

   - `test_queue_module_constants` — `queue.SCHEMA_VERSION == 1`;
     `queue._empty()` returns the expected dict shape.
   - `test_load_returns_empty_for_missing_file` — point at a
     non-existent path; `queue.load(path)` raises (mirrors `state.load`'s
     behavior on missing files). OR if the design says "tolerate
     missing" — verify that. Check `state.load`'s actual behavior and
     mirror it; queue.json missing during a tick is treated as
     "empty queue, no error" elsewhere in the plan, but `load` itself
     may still raise. The cron path uses a try/except wrapper, not
     load's tolerance.
   - `test_load_raises_on_schema_mismatch` — write a file with
     `schema_version: 99`; `queue.load(path)` raises
     `SchemaVersionMismatch`.
   - `test_mutate_round_trip` — `with queue.mutate(path) as data:
     data["queue"].append({"slug": "foo", ...})`; reload; entry is
     there. Atomic write semantics verified.
   - `test_mutate_creates_missing_file_with_empty_factory` —
     non-existent path; `with queue.mutate(path) as data:` yields the
     `_empty()` shape; file gets written.
   - `test_mutate_holds_exclusive_lock` — two concurrent mutate
     calls serialize. (Use `threading.Event` to coordinate two threads
     entering the mutate context; assert the second one waits.) If
     this is hard to test deterministically, an equivalent check is
     "both mutations land without losing either's writes," which is
     the actual user-visible property.
   - `test_project_config_queue_path` — `ProjectConfig.queue_path()`
     returns `<orchestrator_dir>/queue.json`, parent dir exists or
     can be created.

   Also add a test for `state.locked_json` directly in
   `tests/test_state.py` (or wherever state primitives live):
   - `test_locked_json_with_custom_empty_factory` — works with a
     non-state schema (use a sentinel dict, not queue, to prove
     generic).
   - `test_locked_json_raises_schema_mismatch` — uses a wrong
     `expected_version`.
   - `test_locked_json_atomic_rename_on_save` — verify the existing
     tmp+rename invariant survives the extraction.

   Run the suite. All new tests must FAIL (queue module doesn't
   exist yet; `locked_json` doesn't exist yet).

2. **Extract `state.locked_json`.** Open `state.py`. Identify the
   `mutate` context manager. Pull the body into a new `locked_json`
   function with the parameterized signature above. Rewrite `mutate`
   as:
   ```python
   @contextmanager
   def mutate(path: Path) -> Iterator[dict]:
       with locked_json(
           path,
           expected_version=SCHEMA_VERSION,
           empty=lambda: empty_state(...),  # match existing behavior
       ) as data:
           yield data
   ```
   Confirm: `state.mutate` callers (`cmd_init`, `tick`, etc.) work
   bit-for-bit unchanged. The existing state test suite must pass
   without edits — that's the contract.

3. **Collapse `registry._mutate`.** Open `registry.py`. The `_mutate`
   context manager becomes:
   ```python
   def _mutate(path: Path) -> Iterator[dict]:
       return st.locked_json(
           path,
           expected_version=SCHEMA_VERSION,
           empty=_empty,
       )
   ```
   (Or `@contextmanager` wrapper if the typing needs it — match the
   existing decoration. Keep the function private; external callers
   use `entries`, `register`, etc.)
   Confirm: registry tests still pass.

4. **Build `end_of_line/queue.py`.** Skeleton:
   ```python
   """Per-project plan queue. See docs/contract.md for the schema."""
   from __future__ import annotations

   from contextlib import contextmanager
   from pathlib import Path
   from typing import Iterator

   from . import state as st

   SCHEMA_VERSION = 1


   def _empty() -> dict:
       return {"schema_version": SCHEMA_VERSION, "queue": [], "history": []}


   def load(path: Path) -> dict:
       return st.load(path, expected_version=SCHEMA_VERSION)


   def save_atomic(path: Path, data: dict) -> None:
       st.save_atomic(path, data)


   @contextmanager
   def mutate(path: Path) -> Iterator[dict]:
       with st.locked_json(path, expected_version=SCHEMA_VERSION, empty=_empty) as data:
           yield data
   ```
   Adjust to match the codebase's idioms (the snippet is
   illustrative — check actual signatures).

5. **Add `ProjectConfig.queue_path()`.** Open `config.py`. Add a
   method alongside `state_path`. Tests for `queue_path` go in the
   primitive test file.

6. **Extend test helpers.** Open `tests/__init__.py` (or wherever
   `isolate_registry` lives). Either:
   - Add `isolate_queue(self, project_root)` that ensures the
     project's `.orchestrator/queue.json` is per-test isolated (most
     likely: the project_root is already tmp_path-scoped, so this is
     a no-op IF every test uses tmp_path projects — verify and keep
     it minimal).
   - OR extend `isolate_registry` to also clean queue paths if the
     existing helper already takes a tmp_path-style scope.
   Pick the smallest change that gives queue tests the same isolation
   as state/registry tests have today.

7. **Run the full suite.** All new tests pass. All existing tests
   pass bit-for-bit unchanged. Expect total count to grow by ~10
   from new primitive tests; the count for existing state/registry
   tests must NOT change.

8. **`/simplify`.** This phase touches state.py, registry.py, new
   queue.py, config.py, tests — multi-file refactor. Run /simplify
   on the diff per CLAUDE.md ("/simplify after non-trivial work —
   diffs >1 file or ~30 lines").

9. **Commit.** Structured message:
   - Title: `clu-queue phase primitive: extract locked_json, scaffold queue.py`
   - Why: queue.json needs the same atomic lock+load+save primitive
     as state and registry; rule-of-three extraction before queue
     becomes the fourth copy.
   - What's new:
     - `state.locked_json(path, *, expected_version, empty)` public
       primitive.
     - `end_of_line/queue.py` (SCHEMA_VERSION, _empty, load,
       save_atomic, mutate).
     - `ProjectConfig.queue_path()`.
   - Under the hood: `state.mutate` and `registry._mutate` now thin
     wrappers around `locked_json`. CLAUDE.md `with st.mutate`
     invariant preserved.
   - Tests: ~10 new primitive tests; existing state/registry tests
     pass unchanged.
   - Co-Authored-By trailer.

10. **`clu complete --token <token>` with SHA + count summary.**

## Failure modes to watch for

- **`state.mutate` callers regress.** Even a subtle change in
  yielded-dict semantics (e.g. mutation visibility, save timing)
  will break `cmd_init`/`tick` invariants. Run the full state-related
  test suite explicitly before committing.
- **`registry._mutate` signature change.** It's private but used in
  multiple registry functions (`register`, `unregister`, etc.). The
  new version must accept the same calls.
- **`@contextmanager` decoration on `_mutate`.** registry's current
  `_mutate` may or may not be decorated; the new version's
  inner `st.locked_json(...)` is a context manager already, so a
  bare `return` may suffice. Match the existing function's
  decoration so callers using `with registry._mutate(...) as data:`
  still work.
- **Test isolation gap.** A queue test that touches the real
  `~/.config/clu/registry.json` is a CLAUDE.md violation. Confirm
  the existing `isolate_registry` covers any registry reads that
  bootstrap-check or queue-pop might trigger (they don't in this
  phase, but the helper extension should anticipate phase 2).
- **`empty` factory called eagerly.** `locked_json` should only call
  `empty()` when the file is missing — calling it on every entry is
  wasted work and would be a behavior change for `state.mutate`
  (where `empty_state` takes args). Lazy-call inside the
  missing-file branch only.
- **`save_atomic` on a brand-new file.** The tmp+rename trick needs
  the parent directory to exist. `mutate` should `mkdir(parents=True,
  exist_ok=True)` on path.parent before the locked section, matching
  how `registry._mutate` does it today (registry.py:51).

## Done criteria for this phase

- `state.locked_json` exists, is public, and is the single source of
  the lock+load+yield+save pattern.
- `state.mutate` and `registry._mutate` are thin wrappers calling
  `locked_json`.
- `end_of_line/queue.py` exists with the listed surface.
- `ProjectConfig.queue_path()` returns the right path; tests cover it.
- Test helpers extended so queue tests get the same isolation as
  state/registry tests.
- ~10 new tests pass; all existing tests pass unchanged.
- One commit, structured message, no `Fixes` trailer.
- `clu complete` with token + SHA + count summary.
