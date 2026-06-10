# basedpyright-drain-tests-drain-rest — remaining test errors to zero

You are phase `tests-drain-rest` of the `basedpyright-drain` plan. You
deliver, as one commit: `basedpyright` reporting ZERO errors repo-wide
(source was drained in src-drain, the watch family in tests-drain-watch;
you clear everything left — ~69 errors across ~25 test files).

## Locked decisions (do NOT re-litigate)

See `plans/basedpyright-drain.md`. Summary:
- **Reuse `must()` from `tests/__init__.py`** (created by tests-drain-watch
  — read the Findings log for its exact signature). Do NOT create a second
  helper.
- Same idiom preference order: direct indexing > `must()` narrowing >
  rationale'd ignore for true checker limitations.
- Tests-only phase; `end_of_line/` is off-limits.

## Read first

- `plans/basedpyright-drain.md` `## Findings log` — REQUIRED: must() shape +
  prior phases' idiom decisions; follow them for consistency.
- Run `basedpyright --outputjson` for the live list. Planning snapshot
  leftovers (~69): test_systemic_failure 8, test_cli_ship 6,
  test_ready_to_ship_rule 6, test_cmd_verify 4, test_install_hook 4,
  test_notify 4, test_top 4, test_cmd_attest 3, test_auto_archive_rule 3,
  test_init_worktree 3, test_worktree_attach 3, test_worktree_reattach 3,
  test_supervisor 3, test_terminalize 3, test_session_start_hook 3,
  test_dispatch_attempt_context 2, test_notify_protocols 2, plus singletons
  (test_heartbeat, test_coolant, test_monitor, test_logs,
  test_worktree_cleanup, test_watch_task_protocol stragglers if any).
- Named oddballs with locked approaches:
  - `test_top.py:512` — `Rect.x` is read-only: build a new Rect for the
    mutated fixture instead of assigning.
  - `test_logs.py:155` — local `Cfg` stub isn't a `ProjectConfig`: use the
    real config factory from tests/__init__.py.
  - `test_notify.py:239-274` — inbox_writer callable must match the declared
    `((...) -> str) | None` protocol: return a str from the stub.

## Produce

1. **Tests are the diff** — acceptance is basedpyright zero + suite behavior
   unchanged (identical test count).

2. **Implementation**: drain everything `basedpyright --outputjson` still
   reports. You are the last drain phase — after you, the repo is CLEAN.

3. **Acceptance.**
   - `basedpyright` exit 0, zero errors TOTAL, repo-wide.
   - Full suite green, identical test count.

4. **Commit + attest + complete.**
   - Findings: anything the gate phase needs (e.g. errors that only
     reproduce on a newer basedpyright, ignores you had to add with
     rationale).
   - Structured commit: `basedpyright-drain: phase tests-drain-rest — repo
     reaches basedpyright zero (#89)`.
   - Stage explicit paths: the touched test files (+ master if findings
     logged).
   - After the commit:
     - `clu verify --plan basedpyright-drain --phase tests-drain-rest --token <T>`
     - `clu attest --simplify --plan basedpyright-drain --phase tests-drain-rest --token <T>`
   - `clu complete --plan basedpyright-drain --phase tests-drain-rest --token <T>`.

## Failure modes to watch

- **Don't weaken assertions while narrowing** (same ban as
  tests-drain-watch: no assertion becomes conditional).
- **"Zero repo-wide" is YOUR acceptance** — if prior phases left stragglers
  (merge timing, version skew), they're yours now; don't bounce them.
- **Sandbox suite caveat** (env-inject-91 findings): judge green by
  `clu verify`, say so in the summary.
