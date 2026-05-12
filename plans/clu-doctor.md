# clu-doctor — `clu doctor --project P` smoke-tests worker subprocess env

Closes [#14](https://github.com/smabe/end-of-line/issues/14). Day-5's
`worker-path-config` (closed #9) lets operators set `dispatch.path` in
`.orchestrator.json` to fix LaunchAgent-sparse-PATH bugs. But operators
have to *guess* what to put in `dispatch.path` — they have no way to
see what their worker subprocess actually inherits. This plan ships
`clu doctor` to close that loop.

## Goal

```
$ clu doctor --project ~/projects/end-of-line
Worker subprocess will see:
  PATH = /opt/homebrew/bin:/usr/local/bin:/Users/smabe/.local/bin:/usr/bin:/bin
  gh   = /opt/homebrew/bin/gh
  pipx = /opt/homebrew/bin/pipx
  clu  = /Users/smabe/.local/bin/clu
  (source: dispatch.path)
```

When `dispatch.path` is unset:
```
  PATH = /usr/bin:/bin   (inherited from supervisor process)
  ...
  (source: inherited)
```

## Locked design (do NOT re-litigate)

- **Subcommand name**: `doctor`. Other names considered (`env`,
  `inspect`) are not better. Stop bikeshedding.
- **Behavior**: load `.orchestrator.json` for the given project, build
  the same `env=` dict that `dispatch_for_tick` would pass to
  `subprocess.Popen`, then run a one-shot subprocess that prints
  `PATH` and resolves a small fixed set of binaries (`gh`, `pipx`,
  `clu`). Capture stdout, format, print.
- **Helper extraction is mandatory**: `dispatch.py` currently builds
  the env inline at line 144-145 (and again at 226-227 for the
  repair worker). Extract into a helper like
  `dispatch.build_worker_env(cfg) -> dict[str, str] | None` (returns
  `None` when no override → caller leaves `env` unset). Both existing
  call sites refactor onto it. `cmd_doctor` reuses it. This is the
  whole point of the issue — parity, not parallel implementations.
- **Binaries to probe**: hard-code `gh`, `pipx`, `clu` for v1. These
  are the three the worker actually shells out to (or that the
  bundled `/clu-phase` skill exemplifies). Don't make this a flag.
- **Probe via**: `subprocess.run(["sh", "-c", "echo PATH=$PATH; for
  b in gh pipx clu; do command -v $b || echo NOT FOUND: $b; done"],
  env=worker_env, capture_output=True)`. The shell `command -v` is
  the same resolution the worker would do.
- **Doesn't touch plan state.** No registry read, no state.json
  write, no notifications. Pure read of config + subprocess probe +
  print.
- **Source attribution**: print `(source: dispatch.path)` when an
  override was applied, `(source: inherited)` otherwise. Operator
  needs to know whether they're seeing the override or the fallback.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| impl | `clu-doctor-impl.md` | Extract `build_worker_env` helper from `dispatch.py`; refactor both inline call sites. New `cmd_doctor` + subparser. New `tests/test_doctor.py` covering env parity, override path, inherited path, missing-binary handling. Update `dispatch.py`'s existing tests if any reference the inline env construction. | 3h |

## Failure modes to anticipate

- **Probe subprocess fails to spawn.** Highly unlikely (we're running
  `sh`, which always exists on macOS), but if it does — print a clear
  error mentioning the env shape we tried to use, exit non-zero.
- **`command -v` exits non-zero per missing binary.** That's expected
  (and the prompt). Don't let the per-binary exit code crash the
  whole probe — the `|| echo NOT FOUND` in the shell snippet is the
  guard. Test this by deliberately probing for a binary that doesn't
  exist (e.g. `nonexistent-binary-xyzzy`) and asserting `NOT FOUND`
  appears in the parsed output.
- **`.orchestrator.json` missing.** `load_project_config` already
  raises a clean error in this case. Let it bubble — `cmd_doctor`
  catches it the same way other operator commands do. Test the
  failure message is helpful (mentions the expected file path).
- **Refactor regression risk in dispatch.** Real workers depend on
  this code path. The refactor is mechanical (extract function,
  inline becomes call), but the test suite must still pass — every
  existing `test_dispatch*.py` case is a regression check. Run the
  full suite, not just the new doctor tests.
- **`dispatch.path` is empty string vs missing.** `build_worker_env`
  must treat both as "no override" and return None. The empty-string
  path through dispatch.py today is `if cfg.dispatch.path:` (line
  144) which is falsy for `""` — preserve that exact semantic in the
  helper.
