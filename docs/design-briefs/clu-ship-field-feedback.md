# clu-ship field feedback — first full parallel-dispatch session

**Source session:** 2026-05-23, HealthData repo, draining 3 apple-audit
follow-up issues (#161 #162 #163) in parallel after a manual #158 ship.
~75 min wall-clock from `clu queue add` to all-DONE to `clu ship
--all-done --yes` landing on `origin/main`.

**Validates the design brief at `docs/design-briefs/clu-ship.md` (commit
`db9f4c2`).** Net: the new `clu ship` command shape is a real upgrade
over the deprecated `clu integrate`. Field friction below is
prioritized for an `ergonomics-pass` issue.

---

## What worked well

These should be preserved, not changed:

1. **`clu ship --all-done --yes` batch form.** One command merged 3
   plans, pushed `origin/main` + each branch, triggered
   `auto_archive_rule` ticks immediately (not on cron). The
   validation report distinguished `clean` from `textual_conflict`
   per plan so the skip semantics were obvious.

2. **Atomic `clu queue add slug1 slug2 slug3`.** Cleaner than three
   separate calls and avoids the queue-pop-mid-author race the
   `clu-plan` skill warns about.

3. **`--task-list` protocol on `clu watch`.** Structured output:
   ```
   TASK_CREATE task=fm-polish status=pending
   TASK_CREATE task=fm-polish/main parent=fm-polish status=pending
   TASK_UPDATE task=fm-polish status=in_progress msg="bootstrap: plan running"
   TASK_UPDATE task=fm-polish/main parent=fm-polish status=in_progress msg="started (attempt 1)"
   TASK_UPDATE task=fm-polish/main parent=fm-polish status=in_progress \
       msg="BLOCKED q-1 — docs-verify hook flagged 2 false positives on FM polish commit — override?"
   ```
   Maps trivially to TaskCreate/TaskUpdate calls in Claude's Monitor
   harness. The `msg=` field carrying transition reasons (BLOCKED q-N
   …, started (attempt N), completed) made it possible to surface
   actionable events without parsing free-form text.

4. **Per-`clu init` worker-model echo.** Each init printed:
   ```
   worker model: claude-opus-4-7 (pinned via --model in dispatch.command)
   ```
   Satisfies the operator's CLAUDE.md "confirm worker model before
   dispatch" rule without grepping `.orchestrator.json`. Cost-aware
   operators expect this surface.

5. **`KIND_READY_TO_SHIP` iMessage on plan completion.** Hit the
   operator's iMessage before Claude's queue-list cache refreshed.
   The user pinged Claude with "they're done" based on the texts;
   without that notify path Claude wouldn't have known to re-check.

6. **Worker blocker UX.** When the stale-sim-UUID blocker fired
   (worktree had `7C19B100-…` from a pre-macOS-upgrade snapshot),
   the worker:
   - Detected it instead of guessing
   - Presented 3 clear options (patch config / skip-verify / halt)
   - Echoed exact `clu answer` command via the SessionStart hook
   - Included full reasoning in `clu blockers show` output
   This is the right shape.

---

## Friction points

### 1. `clu answer` syntax mismatch with the inbox hook prompt

**Reproducer.** The clu-answer hook prompt installed at the operator
level reads:

> If the user's next message reads as a reply to one of these
> blockers (letter, number, or natural pick), call `clu answer
> --plan <slug> <blocker_id> <answer>` via Bash.

Following this, Claude called `clu answer --plan a11y-pass q-1 0`
and got:

```
clu: error: unrecognized arguments: 0
```

Actual CLI: `clu answer [--project PROJECT] [--plan PLAN] answer` —
no `blocker_id` positional. It auto-resolves to the single open
blocker on the plan.

**Fix options:**
- Update the hook prompt template to drop `<blocker_id>`.
- OR accept `blocker_id` as an optional positional, useful when a
  plan has multiple open blockers (the hook narrative already
  handles disambiguation via `--plan`, but mid-session a plan could
  hypothetically have stacked blockers).

**Severity:** MED — every first-time user will hit this on their
first blocker answer until they read the usage line.

---

### 2. `--project` required but not visually flagged in usage

**Reproducers.**

```
$ clu ship --plan fm-polish --yes
usage: clu ship [-h] --project PROJECT (--plan PLAN | --all-done) ...
clu ship: error: the following arguments are required: --project
```

```
$ clu blockers show --plan fm-polish q-1
Traceback (most recent call last):
  File "/Users/smabe/.local/bin/clu", line 6, in <module>
    sys.exit(main())
  File "/Users/smabe/projects/end-of-line/end_of_line/cli.py", line 1076, in main
    return cmd_blockers(args)
  File "/Users/smabe/projects/end-of-line/end_of_line/cli.py", line 4914, in cmd_blockers
    return cmd_blockers_show(args)
  File "/Users/smabe/projects/end-of-line/end_of_line/cli.py", line 4946, in cmd_blockers_show
    cfg = load_project_config(args.project.resolve())
                              ^^^^^^^^^^^^^^^^^^^^
AttributeError: 'NoneType' object has no attribute 'resolve'
```

**Fix options:**
- Default `--project` to CWD when omitted (most invocations are from
  inside the project anyway; `clu watch` / `clu queue list` /
  `clu init` already work this way).
- OR for commands where defaulting isn't safe, catch the `None`
  before `.resolve()` and emit a clean error: `clu blockers show:
  error: --project is required (try --project .)`.

**Severity:** LOW for `ship` (recoverable from the usage line);
HIGH for `blockers show` (the stack trace is alarming and obscures
what to fix).

---

### 3. `clu watch --all --task-list` rejected

**Reproducer.**

```
$ clu watch --project . --all --task-list
error: --task-list requires --plan or single-project (mutually exclusive with --all)
```

**Impact.** Operator had to spawn N separate Monitors (3 in this
session) — one per plan — even though the task-list protocol's
`task=<slug>/<phase>` ID is namespaced and could be fleet-multiplexed.

**Fix.** Allow `--all --task-list`. Each `TASK_CREATE` / `TASK_UPDATE`
line already carries the plan slug in its `task=` field, so no
disambiguation is lost. The single-stream form is also strictly
better for harnesses that don't want N concurrent subprocesses.

**Severity:** MED — works around with N Monitors but the workaround
is heavier and tooling-specific.

---

### 4. `--all-done` enumerates every dead plan-state file

**Reproducer.**

```
$ clu ship --project . --all-done --yes
... [18 lines of "skipped <slug>: validate failed"]
... [21 lines of validation results, 18 textual_conflict + 3 clean]
shipped 'fm-polish': 'clu/fm-polish' → main
shipped 'la-polish': 'clu/la-polish' → main
shipped 'a11y-pass': 'clu/a11y-pass' → main

shipped 3/21 plan(s).
```

The 18 skipped plans are old absorbed/shipped plans whose
`plans/.orchestrator/<slug>.state.json` files were never cleaned up.
They're noise — the operator knows they're stale.

**Fix options:**
- Filter `--all-done` enumeration to plans whose branch still exists
  and is non-empty against `origin/main`.
- OR run a janitor pass (`clu cleanup --absorbed`) that removes
  state files for plans whose branches are already merged/deleted.
  Could run automatically on init.

**Severity:** LOW — works correctly, just noisy. Becomes annoying
in long-running clu projects with history.

---

### 5. `state_locator` noise leaking on every `clu answer`

**Reproducer.**

```
$ clu answer --plan a11y-pass 0
state_locator: skipping throwaway — [Errno 2] No such file or directory: '/private/tmp/clu-init-probe/plans/.orchestrator/throwaway.state.json'
state_locator: skipping watch-bootstrap-active — [Errno 2] No such file or directory: '/Users/smabe/projects/end-of-line/plans/.orchestrator/watch-bootstrap-active.state.json'
state_locator: skipping simplify-refactor-batch-1 — [Errno 2] No such file or directory: '/Users/smabe/projects/HealthData/plans/.orchestrator/simplify-refactor-batch-1.state.json'
state_locator: skipping test — [Errno 2] No such file or directory: '/private/var/folders/g1/.../tmp.USBupcHDnN/plans/.orchestrator/test.state.json'
state_locator: skipping test — [Errno 2] No such file or directory: '/private/var/folders/g1/.../tmp.86nEP69X1i/plans/.orchestrator/test.state.json'
Answered q-1: Patch .orchestrator.json UUID to 3D32F890-… (fixes all queued plans)
```

The `state_locator: skipping X` lines are looking in `/private/tmp`
and `/private/var/folders` paths from prior throwaway test sessions
that no longer exist. They should be silent (the lookup is best-
effort).

**Fix.** Suppress `state_locator: skipping ...` for `ENOENT`. Only
log when the file exists but fails to parse, or other unexpected
errors. Move the existing log to `--verbose` if any operator
actually wants to see it.

**Severity:** LOW — cosmetic but it makes every operational command
noisier than it should be.

---

### 6. Worktree config drift after answer-time patches

**Scenario.** When the operator answered `clu answer --plan a11y-pass 0`
("patch the config"), the worker patched its own worktree's
`.orchestrator.json` and committed it. But the other two in-flight
worker worktrees (fm-polish, la-polish) had branched off pre-patch
HEAD and still had the stale config. They'd hit the same blocker on
their next verify pass — Claude had to pre-emptively `Edit`
`.orchestrator.json` in each worktree to avoid two more identical
blockers.

**This is the price of the worktree-isolation model, not a bug.**
But a `clu sync-config --to-worktrees` command (or auto-sync on
config edits to main) would close the gap. The operator's CLAUDE.md
now documents the pre-patch dance as a worktree-workflow rule.

**Fix options (in order of intrusiveness):**
- Document the pattern in `clu-ship.md` so operators expect it.
- `clu sync-config` explicit command that copies the main repo's
  `.orchestrator.json` into every active worktree.
- Auto-sync as a post-receive hook on `main` that pushes the config
  diff into every active worktree.

**Severity:** LOW for individual plans (worker reblocks; operator
re-answers). HIGH when running 5+ parallel plans where each reblock
costs operator attention.

---

## Workflow observations (for the design brief, not bugs)

- **The blocker → SessionStart hook → `clu answer` → worker-resume
  loop is the right primitive.** Three blockers hit this session
  (one stale-UUID, one docs-verify false positive, one auto-resolved);
  all three were addressable with a single Claude-side command after
  the inbox hook surfaced the question. The operator never had to
  cd into a worktree or read worker logs.

- **The `--all-done` + auto-archive sequence is satisfying.** Run
  one batch ship; come back to find all three feature commits on
  `origin/main`, all three worktrees gone, all three branches tagged
  with auto-archive cleanup commits. The 21-plan enumeration noise
  (Friction #4) is the only blemish.

- **Worker-quality observation:** the Opus 4.7 workers behaved
  conservatively when uncertain. The docs-verify false positive
  (Friction #N/A — not a clu bug, but worth knowing) was caught
  because the worker verified against the iOS 26.5 SDK's
  `.swiftinterface` BEFORE deciding to override the hook. That's
  exactly the verification pattern the operator's CLAUDE.md mandates
  ("verify cross-system contracts before scoping"). Workers ship
  feedback this skill is necessary for them to internalize too.

---

## Priority order (operator's read)

1. **Friction #1** (clu answer hook-prompt mismatch) — fix first;
   every new operator hits this.
2. **Friction #2** (--project not flagged, stack trace on blockers
   show) — the traceback is alarming; quick fix.
3. **Friction #3** (--all --task-list rejected) — enables single-
   Monitor fleet view in Claude harnesses.
4. **Friction #5** (state_locator noise) — cosmetic but pervasive.
5. **Friction #4** (--all-done enumeration noise) — needs a
   cleanup story but works correctly.
6. **Friction #6** (worktree config drift) — document first; tool
   later if it becomes painful.

Net: ship clu-ship as-is, address #1 + #2 in a fast follow-up
ergonomics pass.
