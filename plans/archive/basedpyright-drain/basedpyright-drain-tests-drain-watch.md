# basedpyright-drain-tests-drain-watch — watch/fleet/webserver test errors + `must()`

You are phase `tests-drain-watch` of the `basedpyright-drain` plan. You
deliver, as one commit: zero basedpyright errors in the five watch-family
test files (~88 of the 157 test errors), plus the shared narrowing helper
every later test fix reuses.

## Locked decisions (do NOT re-litigate)

See `plans/basedpyright-drain.md`. Summary:
- **Create `must()` in `tests/__init__.py`**: `def must(x: T | None) -> T`
  — assert-not-None + return, TypeVar'd, docstring one-liner ("narrow
  Optional in test assertions; AssertionError here means the fixture/regex
  produced nothing"). Place beside the existing factory helpers.
- Fix idioms in preference order: direct indexing over `.get()` in tests;
  `must(...)` for genuinely-Optional sources (`re.search`, helpers returning
  `str | None`); `# pyright: ignore[rule]` only for true checker limitations
  with rationale.
- Tests-only phase: do NOT touch `end_of_line/` source (src-drain owns it).

## Read first

- `plans/basedpyright-drain.md` `## Findings log` — src-drain may have noted
  version drift or helper naming surprises.
- `tests/__init__.py` — existing helper/factory conventions to match.
- Run `basedpyright --outputjson` for the live list. Planning snapshot
  (2026-06-10): `test_watch_project_event.py` 34, `test_watch_task_protocol.py`
  30, `test_fleet.py` 13, `test_webserver.py` 8,
  `test_watch_operator_filter.py` 3. Dominant shapes: `assertIn(needle,
  str_or_none)` (reportArgumentType on the container param) and
  `None.startswith/split` (reportOptionalMemberAccess) from line-capture
  helpers returning `str | None`; `test_fleet.py` is attribute access on an
  Optional row object; `test_webserver.py:110` passes a str literal where
  `Path | None` is expected.

## Produce

1. **Tests are the diff** — there's no failing-test-first step for type-only
   edits; the acceptance gate is basedpyright + the suite staying green
   (these edits must not change any test's pass/fail behavior).

2. **Implementation**: `must()` + the five files drained. Where one file
   repeats `must(...)` on the same expression many times, hoist to one
   narrowed local at the top of the test method — readability over
   mechanical wrapping.

3. **Acceptance.**
   - `basedpyright` → zero errors in the five claimed files; no new errors
     anywhere.
   - Full suite green with IDENTICAL test count to before your diff (type
     narrowing must not skip or add tests).

4. **Commit + attest + complete.**
   - Findings: log the `must()` location + signature for tests-drain-rest,
     and any idiom decisions that diverged from the preference order.
   - Structured commit: `basedpyright-drain: phase tests-drain-watch —
     watch-family test errors + must() helper (#89)`.
   - Stage explicit paths: `tests/__init__.py` + the five test files
     (+ master if findings logged).
   - After the commit:
     - `clu verify --plan basedpyright-drain --phase tests-drain-watch --token <T>`
     - `clu attest --simplify --plan basedpyright-drain --phase tests-drain-watch --token <T>`
   - `clu complete --plan basedpyright-drain --phase tests-drain-watch --token <T>`.

## Failure modes to watch

- **Don't weaken assertions while narrowing.** `assertIn(x, must(line))`
  keeps the failure signal; replacing an assertion with an `if line:` guard
  silently skips it — banned.
- **Narrowing through subscripts needs an intermediate variable** —
  `must(d)["k"]` is fine, but `assert d is not None` then `d["k"]["j"]` can
  un-narrow across statements; prefer `must()` returns into locals.
- **Sandbox suite caveat** (env-inject-91 findings): judge green by
  `clu verify`, note it in the summary.
