# worker-path-config-env — thread `env=PATH` through dispatch.Popen

You are phase `env` of the `worker-path-config` plan. Implement the
dispatch-side wiring for issue #9: when `cfg.dispatch.path` is
non-empty, pass `env={**os.environ, "PATH": cfg.dispatch.path}` to
`subprocess.Popen` so the worker subprocess sees a deterministic
PATH.

## Locked decisions (do NOT re-litigate)

See `plans/worker-path-config.md`. Summary:

- Field is `cfg.dispatch.path: str`. Phase 1 already shipped the
  config plumbing in commit `2f9316b`.
- Empty string (`""`) means "do NOT override env" — current behavior
  preserved. The `env=` kwarg is only passed when path is non-empty.
- Custom env merges with `os.environ`, never replaces it. Stripping
  `HOME`/`USER`/etc breaks the worker's `claude --print` invocation.

## Read first

- `end_of_line/dispatch.py` — the spawn site at line 108. The current
  `subprocess.Popen` call passes `cmd`, `shell=True`, `cwd=`,
  `stdout=`, `stderr=`, `start_new_session=True`. No `env=`. Your job
  is to add `env=` conditionally.
- `end_of_line/config.py` — confirm `DispatchSpec.path` exists (it
  does, post phase 1). You just consume it; do not re-edit.
- `tests/test_dispatch.py` — existing patterns for dispatch tests.
  Especially the `setUp` and how it fakes `DispatchSpec`. You will
  add new tests that follow the same fixture style.
- The repo conventions in `CLAUDE.md`:
  - **TDD before logic changes.** Failing test first.
  - **`tests.isolate_registry(self, tmp_path)` in `setUp`** if your
    test touches `registry.register` (directly or via `main(["init",
    ...])`). The new tests don't need to register a plan — they call
    the dispatch path directly — so isolate_registry is likely not
    needed. Confirm by reading the existing dispatch tests.
  - **`with st.mutate(path) as data:`** for state mutations.
  - **`ExitCode` IntEnum** if you touch CLI exit codes (you should
    not need to in this phase).

## Produce

1. **Failing test first.** Add to `tests/test_dispatch.py` (or a new
   `tests/test_dispatch_env.py` if cleaner — check the existing test's
   length and style before deciding):
   - **`test_dispatch_no_path_omits_env`**: when
     `cfg.dispatch.path == ""`, the spawned subprocess inherits the
     parent's PATH. Write a sentinel command (e.g. `sh -c 'echo
     "$PATH" > <captured>'`) and assert the captured value matches
     `os.environ["PATH"]`.
   - **`test_dispatch_with_path_overrides_env`**: when
     `cfg.dispatch.path == "/usr/bin:/bin"`, the spawned subprocess
     sees `PATH=/usr/bin:/bin` (read from a sentinel file). This is
     the Diagnosis falsifiable test from the master plan.
   - **`test_dispatch_with_path_preserves_home`**: when
     `cfg.dispatch.path` is set AND `os.environ["HOME"]` is present,
     the spawned subprocess still sees `$HOME` — proves the merge
     happened, not a replace. (You may need to mock or rely on the
     real `os.environ["HOME"]`.)
   - Run the suite. The two `with_path_*` tests must FAIL before any
     dispatch.py edit. If they pass before the edit, the test is
     wrong — fix the test, do not skip this gate.

2. **Implement the dispatch edit.** In `end_of_line/dispatch.py`,
   modify the `subprocess.Popen(...)` call at line 108:
   - Add `import os` at top if not already imported.
   - Build the kwargs dict (or use a conditional):
     ```python
     popen_kwargs = dict(
         shell=True,
         cwd=str(cfg.project_root),
         stdout=log_fh,
         stderr=subprocess.STDOUT,
         start_new_session=True,
     )
     if cfg.dispatch.path:
         popen_kwargs["env"] = {**os.environ, "PATH": cfg.dispatch.path}
     proc = subprocess.Popen(cmd, **popen_kwargs)
     ```
     Or pass `env=` directly as a one-liner conditional — your call.
     Match the existing style.
   - Do NOT change anything else about the call. `start_new_session`,
     `cwd`, log handling all stay.

3. **Re-run the suite.** All three new tests pass. Full
   `python3 -m unittest discover -s tests` must be green. The new
   test count is +3 (was 226 after phase 1, expect 229).

4. **`/simplify`** the change. The dispatch.py edit is a few lines but
   single-file single-logical-change — the trivial-diff escape hatch
   from the /plan skill applies. Skip `/simplify` unless the diff
   ballooned past ~30 lines.

5. **Commit** with this structure (Title / Why / What's new / Under
   the hood / Tests / Co-Authored-By trailer). Title:
   `worker-path-config phase 2: thread env=PATH to dispatch.Popen`.
   Do NOT close #9 in this commit — phase 3 (docs) closes it.

6. **Call `clu complete` with the worker token** when done. Include
   the commit SHA in the summary. Per mandate #9 of the worker skill,
   re-run the full test suite from a clean process right before
   calling `complete` and report the count + delta.

## Failure modes to watch for

- **Test asserts wrong shape.** If you assert
  `proc.environ.get("PATH") == ...` you're reading the WRONG object —
  Popen objects don't expose the child's env. You must capture it
  from inside the spawned subprocess (write to a tempfile, read it
  back).
- **`env=` typo or wrong precedence.** Passing
  `env=os.environ` does NOTHING — it gives the child the same env as
  the parent. You need
  `env={**os.environ, "PATH": cfg.dispatch.path}` (the second key
  wins). Triple-check with the assertion in
  `test_dispatch_with_path_overrides_env`.
- **Sentinel command quoting.** `shell=True` with a single string and
  `sh -c '...'` nesting can get hairy. Use the same `cmd_tmpl.format`
  pattern as production, or write a tiny helper.
- **PATH leak from test env.** If the test runner exports `PATH` with
  some weird value, your "inherit parent" assertion might match a
  surprising string. That's fine — the assertion is "matches
  `os.environ['PATH']`", whatever that is at runtime.

## Done criteria for this phase

- 3 new tests pass; full suite green (≥229 tests).
- `dispatch.py` only adds `env=` kwarg conditional on
  `cfg.dispatch.path` being non-empty. No other changes.
- One commit, structured message, no `Fixes #9` trailer (docs phase
  closes the issue).
- `clu complete` called with the token, summary including SHA and
  test count.
