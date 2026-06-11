# end-of-line / clu

Personal plan orchestrator for the `/plan` skill. Cron-driven supervisor,
file state, cold-context phase workers, per-project plan queue for
inter-plan chaining. Tron-themed (binary is `clu`; the program IS End
of Line). Public pitch and install live in [`README.md`](README.md);
this file is the project-private brief for agents starting a fresh
session.

## Stack + run/test

Python 3.11+, stdlib only, zero runtime deps. `unittest`, not pytest.
`pipx install -e .` puts `clu` on `$PATH`.

```bash
python3 -m unittest discover -s tests   # THE gate: pre-commit green, clu verify, canary
python3 scripts/partest.py              # iteration loops ONLY (~20-30s, module-sharded,
                                        # parity-checked) — never the pre-commit green;
                                        # clu verify also runs basedpyright, partest doesn't
python3 -m end_of_line.cli --help
```

## Conventions (mandatory)

For the *why* behind each, see
[`docs/conventions.md`](docs/conventions.md).

- **TDD before logic changes.** AAA, factory helpers, full suite
  before commit.
- **`/code-review` after non-trivial work** — diffs >1 file or ~30 lines.
- **Structured commit format**: Title / Why / What's new / Under the
  hood / Tests / `Co-Authored-By:` trailer.
- **`ExitCode` IntEnum, never bare ints.** Use `_die(ExitCode.X, msg)`.
- **`--token` on every worker callback** (`complete / block / spawn /
  task-done / heartbeat / verify / attest`); validated against the
  live claim.
- **`state.validate_slug` on every external `plan` / `phase_id`** before
  any path join. Regex `^[a-z0-9][a-z0-9_-]{0,63}$`.
- **`EVENT_*` constants, never raw strings.** A typo silently breaks
  `completed_phase_ids` and friends.
- **`with st.mutate(path) as data:`** for state changes — lock + load +
  atomic save in one window.
- **`tests.isolate_registry(self, tmp_path)` in `setUp`** for any test
  that touches `registry.register` (directly or via `main(["init",
  ...])`). Without it, tests pollute the real `~/.config/clu/registry.json`.
- **One tick = one action.** `supervisor.tick` is first-match-wins
  through a 10-priority chain (canonical list in `supervisor.py`
  module docstring); never do two things per tick.

## What NOT to do

- No SwiftUI / iOS code — pure Python; `/review` doesn't apply here.
- No `git add -A` — stage explicit paths.
- No third-party deps without justification + benchmark.
- Don't add a worker callback that skips token validation. The token
  is the entire security boundary.

## Project structure

- **`end_of_line/`** — the `clu` package. `cli.py` is the CLI surface;
  `state.py` the schema; `supervisor.py` the tick loop; `notify_*.py`
  pluggable channels (iMessage / Discord / watch); `watch.py` streams
  state events / `top.py` is the `clu top` worker dashboard (reads worker
  transcripts); `hooks/` ships
  Claude Code SessionStart + PreToolUse hooks; `skills/` bundles
  `/clu-plan`, `/clu-phase`, `/clu-reply`, `/clu-monitor`,
  `/audit-skill` (drift audit of a SKILL.md against current code).
- **`tests/`** — `unittest` suite. `tests/__init__.py` exports
  `CluTestCase` + `isolate_registry` helpers (see Conventions).
- **`docs/`** — depth docs (see "Where to look for depth" below).
  `design-briefs/` for in-flight design; `adr/` for ADRs;
  `history/` for frozen pre-Day-1 brainstorms (read-only).
- **`plans/`** — active master + sub-plan markdown. State at
  `plans/.orchestrator/<slug>.state.json`. `plans/archive/<slug>/`
  holds shipped plans (reference only — they're frozen state, not
  current scope).
- **`examples/`** — example `.orchestrator.json` configs.
- **`experiments/`** — scratch / one-off probes.
- **`CONTEXT.md`** — domain vocabulary (Supervisor / Worker /
  Operator / Plan / Phase). Read once at onboarding; the terms are
  mandatory in code, commits, and docs.

## Where to look for depth

| Doc | Owns |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Process model, tick priority chain, blocker round-trip |
| [`docs/reference.md`](docs/reference.md) | Per-module public surface and invariants |
| [`docs/contract.md`](docs/contract.md) | State schema, event types, worker callback shape |
| [`docs/operations.md`](docs/operations.md) | macOS install, FDA, LaunchAgents, troubleshooting |
| [`docs/conventions.md`](docs/conventions.md) | Project-private policies, with rationale |
| [`docs/_outline.md`](docs/_outline.md) | Structural contract for the docs library |
| [`docs/history/`](docs/history/) | Frozen pre-Day-1 brainstorms |

For ship history + per-feature memory, see
`~/.claude/projects/-Users-smabe-projects-end-of-line/memory/MEMORY.md`
(loaded automatically each session). Live backlog:
`gh issue list --state open`.

## Locked config decisions

Don't re-litigate without a real reason:

- **Notifications:** iMessage to the operator's self-chat handle, no
  Pushover.
- **Quiet hours:** 22:00–08:00 local. Halt bypasses; everything else
  defers.
- **Worker dispatch is hardened (#90):** scoped permissions
  (`--permission-mode dontAsk` + one comma-joined `--allowedTools`) as
  friction, Claude Code's native Seatbelt sandbox as the boundary
  (`~/.config/clu/worker-settings.json`, emitted by `clu init`), and
  `clu block` as the escape hatch on denial. `clu` itself runs
  sandbox-exempt via `sandbox.excludedCommands` so callbacks and
  notifications keep working. Recipe + allowlist rationale:
  `docs/operations.md` "Hardened worker dispatch";
  template: `examples/hardened.orchestrator.json`.

## Sister project

[`/Users/smabe/projects/HealthData`](../HealthData) — the iOS app this
orchestrator was built to drive.
