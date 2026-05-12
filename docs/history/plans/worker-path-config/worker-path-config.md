# worker-path-config — fix issue #9 (worker subprocess PATH)

Make worker subprocesses see a deterministic PATH so they can resolve
`gh`, `pipx`, and other user-installed tools across phases. Phase 1
(config field) shipped in commit `2f9316b`; this master file now
indexes the two remaining phases.

## Goal
Add optional `dispatch.path` field, thread it through
`subprocess.Popen(env=...)` at dispatch time, and document the
troubleshooting path so operators can self-serve when a worker
reports `<tool>: command not found`.

## Locked design decisions (do NOT re-litigate)

- **Field name is `dispatch.path`**, not `worker.path`. Mirrors the
  existing `dispatch.command` shape. The issue body used the wrong
  name loosely.
- **Empty string means "inherit parent env"** (current behavior). The
  worker subprocess only gets a custom env when `cfg.dispatch.path`
  is non-empty.
- **Custom env MUST merge with `os.environ`**, not replace it. Passing
  `env={"PATH": ...}` alone strips `HOME`/`USER`/etc and breaks the
  worker's `claude --print` invocation. Use
  `env = {**os.environ, "PATH": cfg.dispatch.path}`.
- **No tilde expansion, no auto-detection.** Operator sets absolute
  paths. Documented as a constraint.
- **`/clu-phase` skill update is OUT OF SCOPE** for this plan. That
  skill lives in `~/projects/abe-skills` (cross-repo) and was
  decoupled in Day 4. Park as a follow-up.

## Status of phase 1 (already shipped)

`DispatchSpec.path: str = ""` exists. `load_project_config` parses it.
Round-trip coverage in `tests/test_config.py`. Commit `2f9316b`. Both
sub-phases below can rely on `cfg.dispatch.path` being a `str` (never
`None`, never missing).

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| env | `worker-path-config-env.md` | `dispatch.py` threads `env=` to `subprocess.Popen` when `dispatch.path` is set; spawn-test asserts the worker sees the configured PATH (the Diagnosis falsifiable test). | 45m |
| docs | `worker-path-config-docs.md` | Troubleshooting line in `docs/operations.md`; schema entry in `docs/contract.md`; commit closes #9. | 30m |

## Failure modes the worker should know about

- **`subprocess.Popen(env=)` strips parent env unless merged.** See
  locked decision above.
- **Empty string handling.** `cfg.dispatch.path == ""` means "don't
  override env"; do NOT pass `env={"PATH": ""}` to Popen — that gives
  the worker an unusable PATH.
- **Test sentinel must be `env=`-aware.** A test that asserts the
  spawned subprocess sees a specific PATH must capture it from the
  subprocess (e.g. `sh -c 'echo $PATH > <file>'`), not from the test
  harness's own environment.
- **`tests.isolate_registry(self, tmp_path)` in `setUp`** for any test
  that hits `registry.register` (per CLAUDE.md).
- **`shell=True` + `env=` interaction.** The current dispatch already
  uses `shell=True`; passing `env=` is orthogonal — the shell uses
  whatever `PATH` it finds in its env to resolve commands.

## Done criteria (for the whole plan)

- `dispatch.path` is read by `dispatch.py` and threaded through
  `subprocess.Popen(env=)` when non-empty.
- Diagnosis falsifiable test is committed and green (assertion: when
  `DispatchSpec(path="/usr/bin:/bin")` drives a spawn, the spawned
  subprocess's `$PATH` is exactly that string).
- Full `python3 -m unittest discover -s tests` suite passes.
- `docs/operations.md` documents the troubleshooting line + an example
  `dispatch.path` value.
- `docs/contract.md` lists `dispatch.path` alongside `dispatch.command`
  in the config-schema reference.
- Issue #9 closed via commit trailer (`Fixes #9`) on the docs commit.

## Parking lot
- Update `/clu-phase` skill at `~/projects/abe-skills/skills/clu-phase/SKILL.md`
  line ~100 to mention `dispatch.path` as the operator-side fix when
  workers can't resolve a tool. Cross-repo, separate commit.
- Auto-expand `~` in `dispatch.path` segments (convenience, not
  correctness).
- `clu doctor`-style smoke-test subcommand to enumerate the worker
  subprocess PATH (issue #9 suggested this).
