# test-fixture-dedupe (#66)

## Goal
Extract a `GitProjectTestCase(CluTestCase)` base in `tests/__init__.py` that
owns the temp-project + git-init + plan-init handshake the four
`test_coolant_callbacks.py` / `test_release_claim.py` / `test_force_complete.py`
/ `test_complete_refusal.py` files currently each reimplement. Net ≥60 LOC
reduction; all 1169 existing tests stay green.

## Non-goals
- No behavior changes. Pure refactor. No new test coverage, no test deletions.
- No migration of other test files. Scope is exactly the four named in #66.
  Other files using a similar pattern get filed as a follow-up, not done now.
- No unification of session-name conventions (`A` vs `a` vs `phase-a`). Each
  file overrides `PLAN_BODY` if it needs a non-default session name.
- No refactor of helpers that are genuinely file-local: `_write_config`,
  `_make_commit`, `_stamp_verify`, `_stamp_simplify`, `_expect_one_stop_call`,
  `_stamp_claim`, `_force_release_events`, `_orphan_reaped_events`, `_events`.
  Those stay in their respective files.
- No change to `make_git_project` / `CluTestCase` / `isolate_registry`.
  The new base composes them; it doesn't reshape them.

## Files to touch
- `tests/__init__.py` — add `GitProjectTestCase(CluTestCase)` + default
  `PLAN_BODY` module constant. Base composes `make_git_project(self.tmp_path)`
  (existing helper, line 74) — does NOT reimplement git init.
  `self.project` resolves to `self.tmp_path / "myrepo"` (the helper's
  default subdir).
- `tests/test_coolant_callbacks.py` — drop `_git` helper, file-local
  `PLAN_BODY`, `setUp`, `tearDown`, `_claim`, `_argv`. Inherit from new
  base. Keep `_expect_one_stop_call` + the four test methods.
- `tests/test_release_claim.py` — drop `setUp`, `tearDown`, `_argv`,
  `_read`. Inherit from new base. Keep `_stamp_claim`, `_write`,
  `_force_release_events`, `_orphan_reaped_events`, all tests. Rewrite
  `_argv` callsites: `self._argv("release-claim", *extra)`.
- `tests/test_force_complete.py` — drop `setUp`, `tearDown`, `_claim`,
  `_read`, `_argv`. Inherit from new base. Keep `_events`, all tests.
- `tests/test_complete_refusal.py` — override `PLAN_BODY` at class level
  (`phase-a` session, not `a`). Drop `setUp`, `tearDown`, `_claim`,
  `_read`. Inherit from new base. Keep `_write_config`, `_make_commit`,
  `_stamp_verify`, `_stamp_simplify`, `_head`, `_complete`,
  `_claim_is_live`, `_events_of_type`. Either alias `self.base_sha` =
  `self.sha` in setUp or replace `self.base_sha` with `self.sha`
  throughout.

## Failure modes to anticipate
- **Session-name mismatch.** `test_complete_refusal.py` uses session
  `phase-a` while the base default uses `a`. Forgetting the class-level
  `PLAN_BODY` override silently breaks `clu init`'s sessions index
  parse → `_claim("phase-a")` fails because the phase isn't known.
- **`_claim` default arg drift.** Three files default to `a`,
  release-claim defaults to `A`. The base's `_claim(phase="a")` default
  is wrong for release-claim — but release-claim's tests always pass
  the phase explicitly via `_stamp_claim`, so the default is unused
  there. Verify with a grep before claiming this is safe.
- **`tearDown` removal.** `CluTestCase` uses `addCleanup`, not
  `tearDown`. Removing per-file `tearDown` (which cleaned up file-local
  `_tmp`) is correct ONLY if the base doesn't keep a file-local `_tmp`
  reference. The base must use `tmp_path` from `CluTestCase`, not its
  own `TemporaryDirectory()`.
- **State path computation.** All four files derive `state_path =
  project / "plans" / ".orchestrator" / "test-plan.state.json"`. The
  base must produce the same path; tests assert on its contents.
- **`isolate_registry` double-call.** Three files call
  `isolate_registry(self, self.project)` redundantly (CluTestCase
  already isolates via `tmp_path`). The base should NOT call it again;
  rely on CluTestCase's call. Confirm CluTestCase's isolation is
  scoped to `tmp_path`, not to the project subdir.
- **Order of operations in setUp.** `super().setUp()` (CluTestCase)
  must run BEFORE git init / clu init, because the env patches
  (XDG_CONFIG_HOME, CLU_TEST_MODE, COOLANT_*) must be in place when
  `main(["init", ...])` runs.

## Done criteria
- New `GitProjectTestCase(CluTestCase)` in `tests/__init__.py` with
  `PLAN_BODY` module constant and `self.project / self.sha /
  self.state_path / _argv / _claim / _read` surface documented in the
  class docstring.
- All four target test files inherit from it; collective LOC reduction
  ≥60 lines (per #66 acceptance).
- `python3 -m unittest discover -s tests` reports 1169 tests, all green,
  no skips beyond the existing baseline.
- No new helpers in the base that the four files don't use. (No
  speculative API.)
- Commit message ties to #66 and follows the project's structured
  commit format.

## Parking lot
- Deferred migration candidates (file follow-up issue after this ships):
  `tests/test_blocker_round_trip.py:38-66`,
  `tests/test_cmd_attest.py:56-76`,
  `tests/test_cmd_verify.py:60-79` — same tempdir+git+plans+`clu init`
  pattern. Out of scope for #66 per its explicit four-file acceptance.
