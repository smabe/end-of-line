# end-of-line / clu

Personal plan orchestrator for the `/plan` skill. Cron-driven supervisor,
file state, cold-context phase workers, per-project plan queue for
inter-plan chaining. Tron-themed (binary is `clu`; the program IS End
of Line). Public pitch and install live in [`README.md`](README.md);
this file is the project-private brief for agents starting a fresh
session.

## Mandate references

The cross-system verification + operator-approval checkpoint mandates
that govern scoping and novel artifacts live in user-level CLAUDE.md
(`~/.claude/CLAUDE.md` ↔ `~/projects/abe-skills/CLAUDE.md`) so they
apply across all projects. clu's #19 (`/clu-monitor` calling
`/schedule` without verifying it's a remote-only mechanism) was the
canonical failure that motivated them — ~6h of worker time on a
broken design, fixed by the `clu-inbox` rebuild in #20. Receipts in
`docs/history/plans/clu-monitor/` and `docs/history/plans/clu-inbox/`.

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

## Status (as of 2026-05-19)

The substrate is mature: per-plan **worktrees** (#24), multi-channel
**notifications** (iMessage / Discord / clu-watch-only per #11), the
**inbox-hook** session surface (#20) with stuck-blocker + stalled-claim
re-pings, per-project **plan queue** + auto-repair (`clu-queue`), and
the cron-driven supervisor with 8-priority tick chain. CLI surface
covers the operator lease lifecycle (`extend-lease`, `release-claim
[--reset-attempts]`, `force-complete`), worker-callback queue enqueue
(#17), worktree GC (`clu worktree gc/attach/reattach`), and
introspection (`clu watch [--task-list]`, `clu doctor`,
`clu blockers`). Plan files in `plans/`; shipped plans archived to
`plans/archive/<slug>/<filename>.md` (master + sub-plans grouped per
slug), state at
`<plan_dir>/.orchestrator/<slug>.state.json`. Architecture canonical
in [`docs/architecture.md`](docs/architecture.md); module API map in
[`docs/reference.md`](docs/reference.md).

**Recent ships (2026-05-15 → 2026-05-19), newest first** — for
per-ship detail and commit ranges, follow the linked memory entries:

- **#56 — gate-worktree-head** (`ca5e4c0`, merged `1c8011c`).
  Worktree-aware HEAD resolution in `cmd_verify` / `cmd_attest` /
  `cmd_complete` / `_compute_phase_diff`. Fixes the canonical-vs-
  canonical no-op gate from #55: `state.claim_git_root(data, cfg)`
  helper resolves to the worktree path when active, so stamps record
  the worker's actual HEAD instead of canonical-main. Tests 1037 →
  1040.
- **#55 — attestation-gate** (`aee9ffb → 8b54321`, merged `a4c6352`,
  supersedes #10). Programmatic enforcement of `/simplify` + verify
  mandates: `current_claim.attestations` slot, `clu verify` runs
  the project's test command + stamps, `clu attest --simplify` is
  worker self-attestation, `cmd_complete` refuses with
  `STATUS_TRANSITION` when stamps are missing or stale (diff
  threshold defaults `{files:1, lines:30}`). Tests 986 → 1037.
- **#48 + #49 — force-complete + osascript-stderr** (`7f07392`,
  `ca26a64`). Operator-rescue followups from notify-multi-channel.
  `clu force-complete --plan P --phase X --commit SHA` for
  stall-with-work-on-disk; `_osascript_send` now captures stderr to
  `~/.config/clu/imessage.log`. Tests 816 → 835.
- **#11 — notify-multi-channel** (`f903c71 → 15935cc`, merged
  `bb4b6b8`). Notifier / InboundPoller protocols, Discord backend
  (stdlib REST + reply correlation), `channels: [...]` config schema
  with auto-migration from flat `notify.imessage`, runtime
  `--no-notify` flag, `clu notify-test`. clu is now clone-and-go off
  macOS. Tests 816 → 880+.
- **#39 — clu-watch --task-list** (`c6caa7b → 6b3b39a`, merged
  `cb6118e`). TASK_CREATE / TASK_UPDATE protocol over the watch
  stream so AI agents can drive Claude's TaskCreate UI; /clu-plan
  auto-arms `--task-list` Monitor. Tests 698 → 737.
- **#37 + #38 — clu-watch** (`753a4b8 → 8b17c8f`, merged `929216f`).
  New `end_of_line/watch.py` streams state events for AI agents;
  /clu-plan auto-arms Monitor. Tests 614 → 698.
- **#17 — queue-worker-callback** (`7795c5d → a4a54dc`, merged
  `39a9af4`). v2 worker-callback queue enqueue (`clu queue add
  --token T` from inside a phase). Tests 580 → 614.
- **Day 5–6 small ships** — `/clu-plan` skill + cwd fix (#35 #36),
  small-cli-fixes (#23 #31 #32), lease-claim-operator-control (#26
  #27 #29 #30), test-isolation-base (#22), worktree-blocker-followups
  (#25 #28 #33 #34). See `MEMORY.md` for each.

**Open candidates** — pick from the backlog or propose new work:

- **#54 — Emit coolant lifecycle events on worker dispatch and reap.**
  Observability surface for the supervisor/worker handshake.
- **Multi-plan inbound routing.** Day 2.4 deferred last-pinged routing
  for ambiguous bare-digit replies. Not yet filed.

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

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` to keep the graph current
