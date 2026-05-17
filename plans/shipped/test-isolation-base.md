# test-isolation-base — bulletproof `CluTestCase` + XDG safety net (#22)

Defense-in-depth so the clu-inbox dogfood leak (63 ghost events in operator's real
inbox, fixed reactively in #20) cannot recur structurally. The 6 canonical
leak-vector classes (`SupervisorTestCase`, `StalledSupervisorTestCase`,
`DispatchTestCase`, `HeartbeatWorkerTestCase`, `LifecycleTestCase`,
`TickAllTestCase`) currently call `isolate_registry` manually; future test classes
must inherit isolation by default and writes to real `~/.config/clu/` must hard-fail
in test mode.

Two phases, smallest-first: introduce the base class and migrate existing classes
(mechanical), then add the runtime guard that catches anything that bypasses the
base.

## Locked design decisions

### Phase 1 — `CluTestCase` base class
- **Location:** `tests/__init__.py` alongside `isolate_registry` (which stays as the
  lower-level primitive the base class wraps).
- **setUp contract:** creates `self.tmp_path` via `tempfile.TemporaryDirectory`
  (cleaned via `addCleanup`), calls `isolate_registry(self, self.tmp_path)`, and
  patches `CLU_TEST_MODE=1` via `mock.patch.dict` with `addCleanup` restore.
- **Subclasses MUST call `super().setUp()` first** before any registry/inbox
  touching code. Document in docstring.
- **Migrate the 6 canonical leak-vector setUps** to subclass `CluTestCase` +
  `super().setUp()`. Drop the now-redundant manual `isolate_registry` call.
  Preserve any setUp-specific work after the super call.

### Phase 2 — Production XDG safety net
- **New module:** `end_of_line/_xdg_guard.py` exposes `assert_xdg_safe(path: Path)
  -> None`. Raises `RuntimeError` with a clear message naming `CluTestCase` when
  `os.environ.get("CLU_TEST_MODE")` is truthy AND `path.resolve()` is under
  `Path.home().resolve()`.
- **Performance contract:** O(1) — single env lookup + single string-prefix compare
  on resolved paths. No filesystem stats, no logging on success. Safe to call on
  every XDG read.
- **Integration:** call from `inbox.inbox_root()` (inbox.py:28),
  `registry.registry_path()` (registry.py:31), `monitor.marker_path()` — the three
  XDG path producers identified by exploration.
- **Hook script defense-in-depth:** `end_of_line/hooks/clu_inbox_surface.py`
  explicitly `os.environ.pop("CLU_TEST_MODE", None)` at entry so weird shell
  inheritance can't false-trip the guard in real sessions.
- **New tests:** `tests/test_xdg_guard.py` proves the guard fires on contrived
  violations (inbox / registry / monitor each, without `CluTestCase`).

## Non-goals

- Migrating EVERY test class to `CluTestCase` — only the 6 canonical leak-vector
  classes plus any obvious future writers.
- Test-mode awareness in dispatch / notify (only guarding XDG path writes).
- Changing `isolate_registry`'s signature.
- Schema-version bump (no state-file format change).
- `pytest`-style fixture migration (staying on `unittest`).

## Per-phase done checklist

- TDD: failing tests first (Phase 2 — the new `test_xdg_guard.py` IS the test;
  phase 1 is migration with the existing 536-test suite as the regression guard).
- `/simplify` after if diff >1 file or ~30 lines (phase 1 is mechanical migration
  across 5 test files — `/simplify` still gets a pass to catch any duplication).
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood / Tests /
  `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- Call `clu complete --plan test-isolation-base --phase <id> --token <T>` with the
  worker token on success.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| clu-testcase | `test-isolation-base-clu-testcase.md` | Add `CluTestCase` base class + migrate 6 canonical setUps. Mechanical retrofit; 536-test suite is the regression guard. | 1h |
| xdg-guard | `test-isolation-base-xdg-guard.md` | `_xdg_guard.py` + integrations into inbox/registry/monitor + hook defense + new `test_xdg_guard.py` | 1.5h |
