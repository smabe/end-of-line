# Worker subprocess PATH config (issue #9)

## Goal
Add optional `dispatch.path` config field so operators can configure a deterministic PATH for worker subprocesses, fixing the class of bug where workers can't resolve `gh` / `pipx` / other user-installed tools because LaunchAgent context inherits a sparse PATH.

## Diagnosis
- **Hypothesis:** Worker subprocesses currently inherit whatever PATH `claude --print` exposes from a LaunchAgent context, which doesn't include `/opt/homebrew/bin` or `~/.local/bin` deterministically. Setting `env={**os.environ, "PATH": cfg.dispatch.path}` on `subprocess.Popen` at `end_of_line/dispatch.py:108` will let operators pin a PATH that includes those locations once, and worker tool resolution will become deterministic across phases.
- **Falsifiable test:** Add a unit test that spawns a no-op shell command via the dispatch pathway with `DispatchSpec(path="/usr/bin:/bin")` and asserts the spawned subprocess sees `PATH=/usr/bin:/bin` (capture the env via a sentinel command like `printenv PATH > $log`). If `env=` is not threaded through, the test sees the parent's full PATH and fails.
- **Test result:** TBD — this is phase 1's first commit (test must fail before the dispatch.py edit, then pass after).

## Non-goals
- Not adding a `worker.path` field — it's `dispatch.path` to match the existing `dispatch.command` naming. The issue body used `worker.path` loosely.
- Not changing `dispatch.kind` semantics or adding new kinds.
- Not implementing systemic-failure pattern matching for `command not found` (issue #7's territory; defense in depth).
- Not auto-detecting a sane default PATH on macOS — operator opts in by setting the field. Empty string / unset = current behavior (inherit parent env).
- Not updating the `/clu-phase` skill in abe-skills (cross-repo; park as follow-up).
- Not migrating existing `.orchestrator.json` files — additive, optional field.

## Files to touch
- `end_of_line/config.py` — add `path: str = ""` to `DispatchSpec`; parse `disp.get("path", "")` in `load_project_config`.
- `end_of_line/dispatch.py` — when `cfg.dispatch.path` is non-empty, build `env = {**os.environ, "PATH": cfg.dispatch.path}` and pass `env=env` to `subprocess.Popen` at line 108. Otherwise omit `env=` (inherit, current behavior).
- `tests/test_dispatch.py` — add tests: (a) no path set → no env override; (b) path set → spawned subprocess sees that PATH; (c) malformed path (e.g. empty string) → treated as unset.
- `tests/test_config.py` — NEW. Round-trip `load_project_config` with `dispatch.path` present, absent, and as wrong type. (This file doesn't exist yet — first config-loader coverage.)
- `docs/operations.md` — troubleshooting block: "worker says `<tool>: command not found`" → set `dispatch.path` in `.orchestrator.json`; example value with `/opt/homebrew/bin:/usr/local/bin:~/.local/bin:/usr/bin:/bin`.
- `docs/contract.md` — add `dispatch.path` to the config-schema section if it documents `dispatch.command` (verify before editing).

## Failure modes to anticipate
- **Test pollutes real registry.** Any test that calls `main(["init", ...])` needs `tests.isolate_registry(self, tmp_path)` per CLAUDE.md. Phase 1's config-loader tests instantiate `ProjectConfig` directly so should be fine, but the dispatch test currently uses real-ish fixtures — check `test_dispatch.py:setUp`.
- **`subprocess.Popen(env=)` semantics gotcha.** Passing `env={"PATH": ...}` ALONE strips every other env var, including `HOME`, `USER`, `SHELL` — the worker's `claude --print` invocation needs at least `HOME`. Must merge with `os.environ`, not replace it.
- **Shell builtin vs binary lookup.** When `shell=True` and the shell is `sh`, PATH is consulted on `exec` of the command, not on shell startup. Empty PATH segments (`::`) and leading `:` are legal but mean "current dir" — undesired. Document that `dispatch.path` is passed as-is; operator owns the value.
- **Tilde expansion.** `~/.local/bin` won't expand inside subprocess env. Either document "use absolute paths" or call `os.path.expanduser` on each segment. Default to "absolute paths only, documented" — adding expansion is a separable convenience.
- **Tests run from a context where `os.environ["PATH"]` matters.** A test that asserts the spawned subprocess sees `PATH=X` must not be confused by what the *test harness's* PATH is. Use `env=`-aware sentinel (e.g. `sh -c 'echo $PATH > /tmp/...'`) and read the file.
- **`shell=True` and quoting.** The current command template uses `shlex.quote` for substitution — confirm that passing `env=` doesn't change shell selection (it uses `/bin/sh` by default on POSIX, which is fine).
- **Empty string handling.** `cfg.dispatch.path = ""` should NOT cause `env={"PATH": ""}` — that breaks worker. Treat empty as "not set."

## Done criteria
- New `dispatch.path` field on `DispatchSpec`, parsed by `load_project_config`, with round-trip test.
- `dispatch.py` passes `env={**os.environ, "PATH": cfg.dispatch.path}` to `Popen` iff the field is non-empty; otherwise current behavior unchanged.
- Falsifiable test from Diagnosis is committed and green.
- Full `python3 -m unittest discover -s tests` suite passes (currently 221 tests; expect ~225 after this work).
- `docs/operations.md` has the troubleshooting line + an example `dispatch.path` value.
- `docs/contract.md` lists `dispatch.path` alongside `dispatch.command` if the config schema is documented there.
- Issue #9 closed via commit trailer (`Fixes #9`).
- Commit format follows project convention: Title / Why / What's new / Under the hood / Tests / Co-Authored-By trailer.

## Parking lot
- Update `/clu-phase` skill in `~/projects/abe-skills/skills/clu-phase/SKILL.md` line ~100 to mention `dispatch.path` as the operator-side fix when workers can't resolve a tool. Cross-repo, separate commit.
- Auto-expand `~` in `dispatch.path` segments (convenience, not correctness).
- Smoke-test snippet operators can run to enumerate the worker subprocess PATH (issue #9 mentions this; could be a `clu doctor`-style subcommand later).
