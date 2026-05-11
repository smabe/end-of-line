# end-of-line / clu

Personal plan orchestrator for the `/plan` skill. Cron-driven supervisor,
file state, cold-context phase workers. Tron-themed (binary is `clu`;
the program IS End of Line). Public pitch and install live in
[`README.md`](README.md); this file is the project-private brief for
agents starting a fresh session.

## Stack + run/test

Python 3.11+, stdlib only, zero runtime deps. `unittest`, not pytest.
`pipx install -e .` puts `clu` on `$PATH`.

```bash
python3 -m unittest discover -s tests
python3 -m end_of_line.cli --help
```

## Conventions (mandatory)

For the *why* behind each, see
[`docs/conventions.md`](docs/conventions.md).

- **TDD before logic changes.** AAA, factory helpers, full suite
  before commit.
- **`/simplify` after non-trivial work** — diffs >1 file or ~30 lines.
- **Structured commit format**: Title / Why / What's new / Under the
  hood / Tests / `Co-Authored-By:` trailer.
- **`ExitCode` IntEnum, never bare ints.** Use `_die(ExitCode.X, msg)`.
- **`--token` on every worker callback** (`complete / block / spawn /
  task-done / heartbeat`); validated against the live claim.
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
  through an 8-priority chain; never do two things per tick.

## What NOT to do

- No SwiftUI / iOS code — pure Python; `/review` doesn't apply here.
- No `git add -A` — stage explicit paths.
- No third-party deps without justification + benchmark.
- Don't add a worker callback that skips token validation. The token
  is the entire security boundary.

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

## Status (as of 2026-05-11)

Shipped through Day 3.4: security + correctness (Day 1), UX surface +
notifications + halt (Day 2), real worker dispatch end to end and the
docs library (Day 3). Recent orientation:

```
e226bd0 Day 3.4 phase 5: docs/conventions.md
756e1d5 Day 3.4 phase 4: docs/operations.md
a8d0311 Day 3.4 phase 3: docs/reference.md
1daef22 Day 3.4 phase 2: docs/architecture.md
5987bdc Day 3.4 phase 1: docs/_outline.md (audit)
```

**Pick up here.** Day 3.4 phase 6 (this commit) closes out the docs
plan. Day-4 candidates, none chosen — talk to the operator before
starting:

- **Replan path.** `STATUS_HALTED_REPLAN` exists in the enum but
  nothing sets it. Worker callback or operator command? Underspecified.
- **Multi-plan inbound routing.** Day 2.4 deferred last-pinged routing
  for ambiguous bare-digit replies.
- **`clu logs <plan>`.** Tail `.orchestrator/logs/<token>` without
  knowing the token.
- **Wire to a real plan.** `clu init --project ~/projects/HealthData
  --plan watch-start-workout` — the integration test the brainstorm
  called for.

## Locked config decisions

Don't re-litigate without a real reason:

- **Notifications:** iMessage to the operator's self-chat handle, no
  Pushover.
- **Quiet hours:** 22:00–08:00 local. Halt bypasses; everything else
  defers.
- **Worker sandbox:** document-only for v0.1. The operator owns what
  the worker LLM does.

## Sister project

[`/Users/smabe/projects/HealthData`](../HealthData) — the iOS app this
orchestrator was built to drive.
