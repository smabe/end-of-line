# basedpyright-drain — zero out 188 type errors and promote the gate (#89)

The clean-clone canary (`scripts/canary.sh:76-80`) carries basedpyright as
advisory because main has 188 standing errors. Re-measured fresh 2026-06-10
(basedpyright 1.39.6, full JSON at `/tmp/clu89-bp.json` during planning):
**exactly 188**, zero drift across the last three ships — 157 in `tests/`,
31 in `end_of_line/`. Source first (latent real bugs live there), then two
test slices, then the gate promotion last so it can only land on a clean tree.

**Strategy locked: fix all 188 — no baseline, no test-directory relaxation.**
- basedpyright's native baseline matches file+rule+COLUMN; its own docs admit
  resurfacing/re-matching imperfections
  (docs.basedpyright.com/latest/benefits-over-pyright/baseline/), and field
  regrets are concrete: per-source-file baseline parsing blowing 17s runs to
  ~2min (DetachHead/basedpyright#1454), auto-update rewriting baselines on
  partial discovery failure (#1404), column matching dirtying the file on
  rebases.
- `executionEnvironments` per-directory relaxation IS verified to support
  per-rule overrides in `[tool.basedpyright]`
  (microsoft.github.io/pyright/#/configuration, execution-environment
  options) — documented here for the record, deliberately unused: the test
  error mass is one finite mechanical idiom, and tests exercise the public
  API, exactly where checking pays.
- Exit-code contract (pyright CLI docs): 0 = clean, 1 = errors (warnings
  alone exit 0) — `basedpyright` bare is a valid hard gate command.

## Locked design decisions

### Cross-phase
- **Fix idioms, preference order**: (1) in tests index `d["k"]` instead of
  `.get("k")` — missing key should fail the test loudly; (2) narrow with
  `assert x is not None` via an intermediate variable (narrowing doesn't
  survive container subscripts); (3) `cast()` / `# pyright: ignore[rule]`
  ONLY for true checker limitations, each with a one-line rationale comment.
  No bulk suppressions, no blanket file-level ignores.
- **No typing overhaul of the state schema.** dict-based state.json shapes
  stay; source fixes are local narrowing (guards, early returns), never new
  TypedDicts/dataclasses for state. Any fix that seems to demand a signature
  change → flag in Findings log, pick the local guard instead, note the
  candidate refactor.
- **Per-phase acceptance**: `basedpyright` exit 0 on the phase's claimed
  files (and no NEW errors anywhere), full suite green
  (`python3 -m unittest discover -s tests`).
- Workers run `basedpyright` directly during TDD — `Bash(basedpyright *)` is
  in the hardened allowlist (operator added at queue time). `clu verify`
  remains the authoritative sandbox-exempt gate run.

### Phase 1 — src-drain (31 errors, 10 files)
- Real-bug candidates get real guards, not silence: each fix decides whether
  the None path is reachable, and if so what behavior the guard chooses.
- `webserver.py:479` (`compare_digest` with `str | None`) is
  security-adjacent: a missing/None token is a DENY, never an assert.
- `watch.py:446` reportRedeclaration = genuine parameter shadowing cleanup;
  `demo.py:96` Literal-list return typing; `top.py:194-211` /
  `top_registry.py:319,471` are Optional-flow narrowing in the dashboard
  data path.

### Phase 2 — tests-drain-watch (~88 errors)
- Files: `test_watch_project_event.py` (34), `test_watch_task_protocol.py`
  (30), `test_fleet.py` (13), `test_webserver.py` (8),
  `test_watch_operator_filter.py` (3).
- **Extract the narrowing helper here**: `must(x: T | None) -> T`
  (assert-not-None + return, TypeVar'd) in `tests/__init__.py` beside the
  existing factory helpers. The `assertIn(needle, str_or_none)` swarm becomes
  `assertIn(needle, must(line))`. P3 reuses it — do not duplicate.

### Phase 3 — tests-drain-rest (~69 errors, ~25 files)
- Reuses `must()`. Includes the oddballs: `test_top.py:512` read-only
  `Rect.x` (restructure the fixture, don't assign), `test_logs.py:155` local
  `Cfg` stub (use the real config factory), `test_notify.py:239-274`
  inbox_writer callable signature (match the declared protocol).

### Phase 4 — gate (closes #89)
- **Pin `basedpyright==1.39.7`** (PyPI stable 2026-06-07) in the dev extra —
  exact pin, not `>=1.39`: the error set varies across versions and the
  canary builds a fresh venv. Validate clean on 1.39.7 in a scratch venv
  (drain phases ran local 1.39.6; fix any version-skew stragglers here —
  expected zero-to-few).
- **Canary**: replace the advisory block (`scripts/canary.sh:76-80`) with a
  hard `|| fail basedpyright`; drop the GH #89 comment.
- **Enforced gate**: set `quality.verify_command` in the local (untracked)
  `.orchestrator.json` to
  `basedpyright && python3 -m unittest discover -s tests`. `clu verify` runs
  sandbox-exempt, so this works inside hardened workers; `test_command`
  stays pure-suite. Document in docs/operations.md (+ a line in
  docs/conventions.md's quality-gate area).
- Operator follow-up note in completion summary: `pipx upgrade basedpyright`
  locally to match the pin.

## Non-goals
- **No baseline file** — failure modes cited above outweigh the one-time
  drain cost for a finite two-idiom backlog.
- **No `executionEnvironments` relaxation** — mechanism recorded, unused.
- **No `typeCheckingMode` upgrade** (stays `basic`), **no `--warnings`
  strictness** — revisit after the gate has lived a while.
- **No state-schema typing project** — cross-phase rule above.

## Files touched
- `end_of_line/`: cli.py, watch.py, webserver.py, top.py, top_registry.py,
  notify_discord_inbound.py, notify_imessage_inbound.py, dispatch.py,
  cross_plan_rules.py, demo.py — P1 — narrowing guards only; no signature
  changes without a Findings-log flag
- `tests/__init__.py` — P2 NEW `must()` helper (hotspot: P3 depends on it)
- `tests/test_watch_project_event.py`, `tests/test_watch_task_protocol.py`,
  `tests/test_fleet.py`, `tests/test_webserver.py`,
  `tests/test_watch_operator_filter.py` — P2
- Remaining ~25 test files with errors — P3 (disjoint from P2's set)
- `pyproject.toml` — P4 — dev-extra exact pin
- `scripts/canary.sh` — P4 — advisory → hard fail
- `.orchestrator.json` (untracked, local) — P4 — quality.verify_command
- `docs/operations.md`, `docs/conventions.md` — P4

## Per-phase done checklist
- TDD where logic changes (P1 guards get regression tests when the None path
  is reachable); type-only edits are verified by basedpyright + suite.
- `/code-review` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format; stage explicit paths.
- **Post-commit attestations:** `clu verify` then `clu attest --simplify`
  (each with `--plan basedpyright-drain --phase <id> --token <T>`).
- Call `clu complete --plan basedpyright-drain --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| src-drain | `basedpyright-drain-src-drain.md` | 31 source errors, real guards | 1.5h |
| tests-drain-watch | `basedpyright-drain-tests-drain-watch.md` | ~88 watch/fleet/webserver test errors + `must()` | 1.5h |
| tests-drain-rest | `basedpyright-drain-tests-drain-rest.md` | remaining ~69 test errors | 1.5h |
| gate | `basedpyright-drain-gate.md` | pin 1.39.7 + canary fail + verify_command + docs (closes #89) | 1h |

## Findings log

_Empty at plan time. Workers append one dated bullet per cross-phase finding
(gotcha, spike result, API surprise, wrong assumption) with file:line._
