# test-isolation-base

## Goal
Introduce `CluTestCase` base class that auto-isolates XDG paths in setUp, and add a
production safety net that refuses writes to real `~/.config/clu/` when a test-mode
sentinel is set. Defense-in-depth so the clu-inbox dogfood leak (63 ghost events in
operator's real inbox, fixed reactively in #20) cannot recur structurally — closes #22.

## Diagnosis
- **Hypothesis:** The original leak is closed (`isolate_registry` covers all 6
  canonical leak-vector classes — `SupervisorTestCase`, `StalledSupervisorTestCase`,
  `DispatchTestCase`, `HeartbeatWorkerTestCase`, `LifecycleTestCase`, `TickAllTestCase`),
  but the closure is by-convention only. A future test class that calls
  `tick()` / `main(["init"])` without the helper would silently leak again.
- **Falsifiable test:** Write a contrived test that omits `isolate_registry`, run it
  with `CLU_TEST_MODE=1` set; it should raise from the new XDG guard instead of
  writing to real `~/.config/clu/`.
- **Test result:** Will be the new `tests/test_xdg_guard.py` in phase 2 — both
  proves the guard works AND serves as regression coverage.

## Non-goals
- Migrating EVERY test class to the base — only the 6 that currently call
  `isolate_registry` manually, plus any obvious future-writer surfaces.
- Adding test-mode awareness to dispatch / notify (test mode only guards XDG path
  writes, not subprocess execution).
- Changing the `isolate_registry` helper's signature — stays as the lower-level
  primitive that the base class wraps.
- Schema-version bump (no state-file format change).
- A `pytest`-style fixture migration — staying on `unittest`.

## Files to touch
- `tests/__init__.py` — add `CluTestCase(unittest.TestCase)` with `setUp` that
  creates `self.tmp_path` via `tempfile.TemporaryDirectory` (cleaned via
  `addCleanup`), calls `isolate_registry(self, self.tmp_path)`, and patches
  `CLU_TEST_MODE=1` via `mock.patch.dict` with `addCleanup` restore.
- `end_of_line/_xdg_guard.py` (new) — `assert_xdg_safe(path: Path) -> None`
  helper that raises `RuntimeError` with a clear message when
  `os.environ.get("CLU_TEST_MODE")` is truthy AND `path.resolve()` is under
  `Path.home().resolve()`. Cheap (single env lookup + single string prefix
  compare); safe to call on every XDG read.
- `end_of_line/inbox.py:28` — `inbox_root()` calls `assert_xdg_safe(...)` on
  return value before handing back.
- `end_of_line/registry.py:31` — `registry_path()` calls `assert_xdg_safe(...)`
  on the resolved path.
- `end_of_line/monitor.py` — `marker_path()` (and any other XDG-resolving
  function in this module) calls `assert_xdg_safe(...)`.
- `end_of_line/hooks/clu_inbox_surface.py` — explicit `os.environ.pop("CLU_TEST_MODE",
  None)` at hook entry so the hook script in a real session never accidentally
  inherits the sentinel. Defense-in-depth against weird shell inheritance.
- `tests/test_supervisor.py:31`, `tests/test_heartbeat.py:98,137`,
  `tests/test_dispatch.py:33`, `tests/test_lifecycle.py:29`,
  `tests/test_tick_all.py:33` — migrate the 6 setUps to
  `class X(CluTestCase)` + `super().setUp()`. Drop the manual
  `isolate_registry(self, self.project)` call (now redundant). Preserve any
  setUp-specific work after the super call.
- `tests/test_xdg_guard.py` (new) — proves the guard fires on:
  (a) `inbox.write_event` called without isolation under `CLU_TEST_MODE=1`,
  (b) `registry.register` likewise,
  (c) `monitor.marker_path` likewise.
  Each test sets the sentinel manually (does NOT inherit `CluTestCase`) and
  asserts `RuntimeError` with the expected message.

## Failure modes to anticipate
- **Test classes that don't subclass `CluTestCase` and write to real XDG under
  `CLU_TEST_MODE=1`** — the guard raises mid-test, surfacing the omission. This
  is INTENTIONAL behavior; document in the docstring. The risk is a developer
  adding a new test class and being surprised; mitigation is the clear error
  message naming `CluTestCase`.
- **Hook script inheriting `CLU_TEST_MODE=1`** — the explicit pop at hook entry
  guards this, but worth manually verifying with `env CLU_TEST_MODE=1
  python3 end_of_line/hooks/clu_inbox_surface.py` (or equivalent) that no guard
  fires from the hook.
- **`Path.home()` resolution under `HOME` patching** — `addCleanup` runs in LIFO
  order; if `isolate_registry`'s HOME patch is restored before the test body
  finishes (unlikely but possible with manual `addCleanup` interleaving),
  `Path.home()` could resolve to the real home and the guard could false-positive
  on a tmp-resolved path. Mitigation: the guard compares `path.resolve()` to
  `Path.home().resolve()` at call time, not at setUp time, so it tracks current
  HOME consistently.
- **`tempfile.TemporaryDirectory` cleanup ordering** when a subclass defines its
  own setUp — the subclass MUST call `super().setUp()` first, before any
  registry/inbox-touching code. Document this in the base class docstring.
- **Performance: the guard runs on every XDG path read.** Must be O(1) — a
  single env-var lookup + a single string-prefix compare on resolved paths. No
  filesystem stats, no logging on success.
- **The `EVENT_*` event-log writes also go through `inbox.write_event`** — already
  covered by the guard on `inbox.write_event`. Verify no other XDG-writing call
  path bypasses the three guarded functions (`grep -r "Path.home" end_of_line/`).
- **Existing 536-test green count must not regress.** Migration is mechanical
  but easy to typo a `super()` call. Run full suite after every file change.

## Done criteria
- **Phase 1:** `CluTestCase` defined in `tests/__init__.py` with auto-isolation
  + sentinel patching. All 6 existing canonical-leak-vector test classes
  migrated to subclass it (manual `isolate_registry` calls removed). Full suite
  green at 536+ tests, no regressions.
- **Phase 2:** `end_of_line/_xdg_guard.py` defined with `assert_xdg_safe`.
  Integrated into `inbox.inbox_root`, `registry.registry_path`,
  `monitor.marker_path`. Hook script defensively pops the sentinel. New
  `tests/test_xdg_guard.py` (3+ tests) proves the guard fires on contrived
  violations. Full suite green at 536+N tests, where N ≥ 3.
- All work shipped on the `test-isolation-base` branch via clu's worktree.
- Closes #22.
- One commit per phase, `/simplify` between (mechanical retrofit may exempt
  phase 1 from a meaningful `/simplify` — judgment call).

## Parking lot
(empty)
