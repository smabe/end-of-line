# clu-doctor-impl — `clu doctor` + extract `build_worker_env` helper

You are the only phase of the `clu-doctor` plan. Closes
[#14](https://github.com/smabe/end-of-line/issues/14).

Read the master plan first. Do exactly what's below.

## Locked decisions (do NOT re-litigate)

- Subcommand name: `doctor`. Not `env`, not `inspect`.
- Helper extraction is mandatory: `dispatch.build_worker_env(cfg) ->
  dict[str, str] | None`. Both inline call sites in `dispatch.py`
  refactor onto it.
- Probed binaries: hard-coded `gh`, `pipx`, `clu`. Not configurable
  in v1.
- Probe via `subprocess.run(["sh", "-c", ...], env=worker_env,
  capture_output=True)`. The `|| echo NOT FOUND: $b` guard is in the
  shell snippet, not in Python.
- Source attribution in output: `(source: dispatch.path)` vs
  `(source: inherited)`. Operator needs to see which path applied.
- No state.json reads, no registry reads, no notifications, no
  worker token. Pure config + probe.

## Read first

- `end_of_line/dispatch.py:104-187` — `dispatch_for_tick` and the
  inline env construction at line 144-145.
- `end_of_line/dispatch.py:209-227` — the repair-worker dispatch
  with the *same* inline env construction at line 226-227.
- `end_of_line/config.py` — `Dispatch` dataclass and how `path`
  field is loaded. Confirm `cfg.dispatch.path` is `""` when missing
  (matches the `if cfg.dispatch.path:` guard).
- `end_of_line/cli.py` — subparser style for `clu list` (a no-arg
  read-only command) is the closest analog. Mirror.
- `tests/test_dispatch.py` — existing tests that touch the env
  construction. They'll exercise the helper after the refactor; make
  sure they still pass.

## Produce

1. **TDD: failing tests first.** New file `tests/test_doctor.py`.
   Required cases:

   - `test_build_worker_env_with_path_override` — `cfg.dispatch.path
     = "/foo:/bar"` → returns `{**os.environ, "PATH": "/foo:/bar"}`.
     HOME/USER preserved.
   - `test_build_worker_env_without_path_override` — `cfg.dispatch.path
     = ""` (the missing-field default) → returns `None`.
   - `test_doctor_prints_path_and_resolved_binaries` — config with
     `dispatch.path` set to a known good path. Run `clu doctor
     --project P`. Stdout contains `PATH = ...the override...`,
     contains `gh = ...` (or `NOT FOUND: gh`), contains
     `(source: dispatch.path)`.
   - `test_doctor_prints_inherited_when_no_override` — config with
     `dispatch.path` empty. Output includes
     `(source: inherited)`.
   - `test_doctor_handles_missing_binary` — probe for a fake binary
     by temporarily extending the probed list (or asserting the
     guard fires when one of `gh`/`pipx`/`clu` isn't on PATH in the
     test env). Output includes `NOT FOUND: <name>`.
   - `test_doctor_missing_orchestrator_json` — pass `--project` to a
     dir without `.orchestrator.json`. Exits non-zero with a message
     mentioning the missing file.
   - `test_doctor_does_not_touch_state` — register a plan, snapshot
     the state file mtime, run `clu doctor`, assert mtime unchanged.
     Also assert no `_mutate` call on registry (or at minimum, no
     state file written).

   Run suite — all new tests must FAIL.

2. **Extract `build_worker_env`** in `end_of_line/dispatch.py`:

   ```python
   def build_worker_env(cfg: ProjectConfig) -> dict[str, str] | None:
       """Return the env dict to pass to subprocess.Popen, or None to
       inherit. Merges (not replaces) os.environ when an override is
       configured — bare {"PATH": ...} would strip HOME/USER and break
       `claude --print`.
       """
       if cfg.dispatch.path:
           return {**os.environ, "PATH": cfg.dispatch.path}
       return None
   ```

   Refactor `dispatch_for_tick` line 144-145:
   ```python
   env = build_worker_env(cfg)
   if env is not None:
       popen_kwargs["env"] = env
   ```

   Same refactor at the repair-worker site (line 226-227).

3. **Run the suite — existing dispatch tests must stay green.** This
   is the regression gate for the refactor. If a dispatch test breaks,
   the refactor isn't pure — fix the helper or call site, don't edit
   the test.

4. **Add the `doctor` subparser** in `cli.py`:

   ```python
   p_doctor = sub.add_parser(
       "doctor",
       help="Show what PATH and binary resolutions a worker subprocess "
            "would see (read-only; doesn't touch plan state).",
   )
   p_doctor.add_argument("--project", type=Path, required=True,
                          help="Project root (contains .orchestrator.json)")
   ```

5. **Implement `cmd_doctor(args)`**:

   ```python
   def cmd_doctor(args) -> int:
       cfg = load_project_config(args.project)
       env = dispatch.build_worker_env(cfg)
       source = "dispatch.path" if env is not None else "inherited"
       probe_env = env if env is not None else dict(os.environ)

       script = (
           'echo "PATH=$PATH"; '
           'for b in gh pipx clu; do '
           '  printf "%s = " "$b"; '
           '  command -v "$b" || echo "NOT FOUND: $b"; '
           'done'
       )
       result = subprocess.run(
           ["sh", "-c", script],
           env=probe_env, capture_output=True, text=True,
       )

       print("Worker subprocess will see:")
       for line in result.stdout.splitlines():
           print(f"  {line}")
       print(f"  (source: {source})")
       return ExitCode.OK
   ```

   Adjust imports as needed (`subprocess`, `os`, the `dispatch`
   module).

6. **Wire into the dispatch table** at `cli.py:367` (or wherever the
   subparser dispatch happens): add `"doctor": cmd_doctor`.
   `cmd_doctor` takes only `args` (no `cfg, state_path`); follow the
   pattern of read-only commands like `cmd_list` that already do
   that.

7. **Run the suite — all green.**

8. **`/simplify`** — refactor + new subcommand crosses two files.
   Definitely run it.

9. **Commit.** Title: `doctor: add clu doctor for worker env smoke
   test`. Body references `closes #14` and notes the
   `build_worker_env` extraction.

## Verification before `clu complete`

Per CLAUDE.md mandate #9: re-run the full suite right before `clu
complete`. Both new doctor tests AND existing dispatch tests must
pass — the dispatch suite is the regression gate for the helper
extraction. Report final test count and explicit confirmation that
existing dispatch tests stayed green.

## Acceptance

- [ ] `clu doctor --project P` runs, prints PATH and resolved
      binaries, exits OK
- [ ] Honors `dispatch.path` when set (source: dispatch.path)
- [ ] Falls back to inherited when unset (source: inherited)
- [ ] Doesn't touch state.json or registry
- [ ] `dispatch.build_worker_env` is the single source of truth for
      both real dispatch sites and `clu doctor`
- [ ] Existing dispatch tests still pass (regression gate)
- [ ] All new doctor tests pass; full suite green
- [ ] One commit with `closes #14` in body
