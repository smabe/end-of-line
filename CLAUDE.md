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

## Status (as of 2026-05-15)

Shipped through Day 5 + the `clu-queue` plan: security + correctness
(Day 1), UX surface + notifications + halt (Day 2), real worker
dispatch + docs library (Day 3), backlog drain + self-contained worker
PATH (Day 4), tick-default-dispatch + bundled skills (Day 5), and now
per-project plan queue with auto-repair (`clu-queue`,
[`plans/clu-queue.md`](plans/clu-queue.md) — canonical execution
history). Eight phases shipped: primitive, add, list, pop, repair,
footer, docs, smoke.

What the queue adds operator-side: `clu queue add/list/remove` (bare
`clu queue` → list), a per-project queue file at
`<plan_dir>/.orchestrator/queue.json`, the supervisor's post-loop
queue-advancement step in `cmd_tick_all` (per-project, at-most-one pop
per tick, head-only freeze on HALTED/PAUSED), and an opt-in auto-repair
worker dispatched from a `dispatch.repair_command` template — with
`queue.validate_repair` as the slug-preservation safety boundary, not
the worker's prompt.

v2 (worker-callback enqueue from inside a phase) is deferred to GitHub
issue [#17](https://github.com/smabe/end-of-line/issues/17). Don't
re-litigate without reading [`docs/contract.md`](docs/contract.md) §
"Queue schema" + [`docs/architecture.md`](docs/architecture.md) §
"Queue advancement" + "Auto-repair worker" first.

**clu-monitor** — `/clu-monitor` ships as a bundled skill (#19);
operator runs it once per machine to schedule background notifications
on halts, stuck blockers, and stalled claims. CLI tips in `clu init` /
`clu queue add` and the optional project CLAUDE.md injection prompt
make Claude propose it proactively in new sessions. Marker at
`~/.config/clu/monitor.json`; helpers in `end_of_line/monitor.py`. See
[`docs/operations.md`](docs/operations.md) § "Background monitoring".

**clu-inbox** — `/clu-monitor` now installs a `UserPromptSubmit` hook
(phase 1: `c7aded3`) that surfaces clu events into the active Claude
Code session on every user message, replacing the broken `/schedule`
mechanism from #19. Inbox at `~/.config/clu/inbox/`, mark-and-sweep
dedup into `processed/`; helpers in `end_of_line/inbox.py` and the hook
script at `end_of_line/hooks/clu_inbox_surface.py`. Two new
notification kinds added in the same chain (phase 2: `fa82771`):
`stuck_blocker` (30 min re-ping until consumed) and `stalled_claim`
(one-shot on lease expiry while status RUNNING) — both emit alongside
the tick's primary action via `TickResult.side_notifies`. Marker schema
bumped v1 → v2 (`is_scheduled` treats v1 as "needs reinstall"). Tests
406 → 461. Closes [#20](https://github.com/smabe/end-of-line/issues/20).
Follow-up `3e31551` drops the TTY refusal that blocked the
`/clu-monitor` → Bash → `clu install-hook` path (closes
[#21](https://github.com/smabe/end-of-line/issues/21)).

**clu-worktrees** — opt-in per-plan git worktrees so concurrent plans
in the same project can advance on isolated branches without stomping
each other's diffs (closes [#24](https://github.com/smabe/end-of-line/issues/24)).
Seven phases shipped 2026-05-15:
1. constants + helper + exit code (`4fcb7b4`),
2. `clu init --worktree [PATH] [--branch] [--base-ref]` with rollback
   on save fail (`34dbff4`),
3. `TickResult.worktree` snapshot + dispatch `cwd` routing (`fd7bae3`),
4. missing-worktree detection at dispatch → `EVENT_WORKTREE_MISSING`
   + pause + halt-bypass iMessage; extracted `_pause_and_halt` so
   systemic-failure + missing-worktree share the pause shape (`27e40e5`),
5. tick-time conflict scan + init-time hint; suppression via
   `in_conflict_with` field, canonical-pair rule emits once per
   (project, pair) onset; extracted `_plans_for_project` helper
   (`d267c3e`),
6. `clu worktree gc [--confirm] [--delete-branch]
   [--include-archived]` with status re-check + 30s git timeouts;
   `ProjectConfig.master_plan_path` + `_resolve_project_arg`
   extractions during simplify (`47a15b1`),
7. fleet `WT` column + `clu list` `(worktree)` annotation +
   `clu unregister --all-archived` orphan-worktree stderr warning +
   docs sweep (contract / architecture / operations / reference /
   README). Tests 461 → 494. Worktree v2 (worker-callback enqueue
   inside a phase, etc.) deferred — none filed yet.

**queue-ux-hardening** — `clu queue add a b c` is now atomic
(closes [#18](https://github.com/smabe/end-of-line/issues/18),
`5c510a6`): single `queue.mutate` window, all-or-nothing batch
validation, slice insertion for `--front`. `clu queue list` gains an
`In flight: <slug> (dispatched HH:MM:SS UTC, lease until ...)` footer
when a registered plan has an active claim (reuses the existing
`reg_states` projection — no second registry walk). Tests 359 → 373.

**green-batch** — four backlog issues drained autonomously through the
queue (2026-05-12). `dispatch-path-tilde` expands `~` per-segment in
`dispatch.path` at config load (#15, `b31eb69`). `install-skill-list`
adds `clu install-skill --list` to enumerate bundled skills (#13,
`46230e0`). `unregister-archived` adds `clu unregister --all-archived
[--dry-run]` to batch-prune ghost registry entries (#12, `6db6740`).
`clu-doctor` adds `clu doctor --project P` to smoke-test the worker
subprocess environment (PATH + binary resolution), extracting
`dispatch.build_worker_env` as the single source of truth (#14,
`72c4bad`). Tests 337 → 359.

**Open candidates** — pick from the backlog or propose new work:

- **#4: Replan worker callback** — `STATUS_HALTED_REPLAN` exists in
  the enum but nothing sets it. Worker callback or operator command?
  Underspecified, needs design discussion.
- **#10: Programmatic enforcement of mandate #9** — `clu verify` +
  refuse-on-stale-stamp. Spec locked. Trigger to revisit: ≥2 worker
  summaries observed lying about test results.
- **#11: Pluggable notification backends** — Slack + stdout for
  non-Mac operators. Trigger to revisit: a real non-Mac operator
  trying clu.
- **#17: v2 worker-callback queue enqueue** — `clu queue add --token
  T` from inside a phase. Trigger to revisit: 30 days of v1 use +
  ≥3 real-chain requests.
- **Multi-plan inbound routing.** Day 2.4 deferred last-pinged routing
  for ambiguous bare-digit replies. Not yet filed as an issue.

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
