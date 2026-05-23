# Design brief: `clu ship` — collapse integrate + archive into one operator action

Pre-design seed for the post-worker merge experience. Open to brainstorm.
Not a `/plan` yet — read this, brainstorm the unknowns, then a real
plan + Sessions index gets written from the consolidated output.

## Why this exists

2026-05-23 apple-audit batch (7 parallel research plans, file-disjoint
markdown outputs): every worker called `clu complete`, the inbox
surfaced `PLAN DONE`, and the operator then had to do this dance to
get the findings onto `main`:

1. `clu integrate --branches A,B,C,D,E,F,G --no-suite` — operator
   thought this merged. It returned `outcome: clean` and exited.
2. Realized `--no-suite` is actually a textual-merge-only dry-run that
   doesn't update `main`. Confusing.
3. Manually `git cherry-pick` 7 worker commits onto `main` to actually
   merge.
4. `clu archive --plan A` × 7 — each one *staged* a rename from
   `plans/A.md` → `plans/archive/A/A.md` but did NOT commit. Left repo
   in a confusing dirty state.
5. Manually `git commit` the 14 staged renames.
6. `git push origin main`.
7. Manually `git worktree remove` + `git branch -D` × 7 for cleanup
   (archive didn't volunteer to do this since branches were "ahead of
   origin" — true, because nothing pushed them).

Six manual git operations between "worker said done" and "code on
main, repo clean." For a 7-plan batch with file-disjoint outputs this
is the worst case the current ergonomics expose; for a 1-plan batch
it's smaller but the same shape.

The decision to keep integrate operator-controlled is right — the
operator should review the diff before `main` moves. The pain is
everything around that decision.

## Proposed surface (sketch, not locked)

A new top-level command that bundles integrate → push branches → merge
to main → push main → archive → cleanup worktree + branch into one
operator action, with clear in-flight checkpoints:

```bash
clu ship --plan <slug>             # ship a single completed plan
clu ship --batch <name>            # ship a registered batch
clu ship --all-done                # ship everything in DONE state
clu ship --plan <slug> --as-pr     # open a PR instead of merging direct
clu ship --plan <slug> --dry-run   # show what WOULD happen, no side effects
```

**Default flow (`--plan` and `--all-done`):**

1. Verify worker branch(es) for the plan are in `done` state with no
   uncompleted phases.
2. Run `test_command` against the merged result (this is what the
   current `--no-suite` flag opts out of — keep the opt-out, default on).
3. Fast-forward or merge-commit the worker branch(es) into `main`
   locally.
4. `git push origin main`.
5. Archive: move plan files to `plans/archive/<slug>/`, commit, push.
6. Cleanup: `git worktree remove` + `git branch -D` for each shipped
   plan's branch.
7. Mark plan(s) `shipped` in registry; surface inbox event.

**Failure modes that should halt the ship:**

- Worker branch ahead of `origin` for unrelated reasons (operator
  committed manually) → halt, surface diff, ask.
- `test_command` fails on merged result → halt at step 2, leave repo
  unchanged.
- Merge conflict → halt at step 3, leave repo unchanged. (The existing
  dry-merge gate from #50 should already catch this earlier at queue
  time, but the safety net stays.)
- Operator hasn't approved the merge yet → require `--yes` for
  destructive steps, OR exit after step 2 with a "ready to ship, re-run
  with --yes" message.

**`--as-pr` flow** (deferred / open question; see below):

Instead of merging direct, open a GitHub PR per plan with the worker
diff. Operator reviews on GitHub, clicks merge, clu auto-archives on
PR-merge webhook (or on next `clu tick-all` after detecting the merge).

## Supporting fixes (these unblock `clu ship`)

These would land before `clu ship` or alongside it — the new command
inherits both:

### Fix 1: `clu integrate --no-suite` actually integrates

Today's `--no-suite` does textual-merge validation only and doesn't
update `main`. Two options:

- **Rename to `--check` / `--validate`.** Honest about being a dry-run.
  Drop the implication that the merge happened.
- **Make `--no-suite` actually merge while skipping `test_command`.**
  Matches 99% of operator expectation when they read the flag name.
  Add a separate `--check` flag for the dry-run-only mode.

Operator preference: option B, since the dry-run case is rarer and the
name should match the behavior most operators expect.

### Fix 2: `clu archive` should never leave staged-uncommitted state

Today's archive moves plan files via `git mv` and exits, leaving the
operator with N staged renames they have to commit themselves. Two
options:

- **Commit atomically.** clu generates a `chore: archive <slug>` commit
  with the moves. Operator can amend / squash if they don't like the
  style.
- **Don't touch git.** Use plain `mv` + leave the operator to stage and
  commit themselves. Less magic, but more typing.

Operator preference: option A (commit atomically), because the
"clu touched git but didn't commit" state was the genuine footgun this
morning.

## Worktree config management

### The drift scenario

When a config patch propagates through one worker's worktree, sibling
worktrees that branched off an earlier HEAD are still on the stale
config. From the 2026-05-23 field session
(`docs/design-briefs/clu-ship-field-feedback.md`, Friction #6):

> **Scenario.** When the operator answered `clu answer --plan a11y-pass 0`
> ("patch the config"), the worker patched its own worktree's
> `.orchestrator.json` and committed it. But the other two in-flight
> worker worktrees (fm-polish, la-polish) had branched off pre-patch
> HEAD and still had the stale config. They'd hit the same blocker on
> their next verify pass — Claude had to pre-emptively `Edit`
> `.orchestrator.json` in each worktree to avoid two more identical
> blockers.

### Operator workaround

When answering a blocker that patches the canonical `.orchestrator.json`,
immediately pre-emptively update the same key in every active worktree.
Example: answer `clu answer --plan a11y-pass 0`, then for each sibling
worktree (`fm-polish`, `la-polish`):

```bash
# in each active worktree root
# edit .orchestrator.json to match canonical, then commit
```

Or, from the operator's session, use Claude Code's Edit tool to patch
`.orchestrator.json` in each worktree path before continuing. This is
the pre-emptive dance that prevents identical re-blocks.

### Future tool option

A `clu sync-config --to-worktrees` command that copies the canonical
`.orchestrator.json` into every active worktree registered under the
plan is deferred pending repeat field signal. Cost of building
speculatively exceeds cost of the documented workaround for single-digit
parallel-plan batches; re-evaluate when parallel fleet sizes grow.

## Open scoping questions for brainstorm

1. **Cleanup defaults.** Should `clu ship` always remove the worktree +
   delete the branch, or opt-in (`--cleanup`)? After-ship the branch's
   commits are reachable from `main`, so deletion is safe. But: dev
   habit of `git checkout <worker-branch>` to poke at pre-merge state
   could miss it. Default cleanup = yes, with `--keep-worktree` opt-out?

2. **Atomicity vs. checkpointing.** If the merge succeeds but the
   archive commit fails, is the operator left in a half-shipped state?
   Either roll back the merge (annoying — destructive on `main`) or
   surface the partial state clearly and tell the operator what manual
   step closes it.

3. **`--as-pr` mode storage.** PR-merged-detection requires listening
   for a webhook OR polling on `tick-all`. Polling is simpler and
   consistent with existing patterns; webhook needs `.orchestrator.json`
   config. Open: start with polling, add webhook later?

4. **What's the right verb for the supervisor when it sees a shippable
   batch?** Today it surfaces `PLAN DONE`. Should it additionally
   surface `READY TO SHIP: <slugs>` with a copy-paste
   `clu ship --batch ...` command? Inbox events are the natural surface;
   the inbox hook already routes per-project. (Bonus: the
   `/clu-monitor` skill could be updated to suggest the `clu ship`
   command when it sees the inbox event.)

5. **Boundary with `clu queue`.** `clu queue add` is the "before-worker"
   operator action; `clu ship` is the "after-worker" operator action.
   Should the names rhyme symmetrically? E.g. `clu queue add` +
   `clu queue ship`? Or top-level commands that pair (`clu queue add` +
   `clu ship`)? Surface naming matters more than it looks.

6. **Backwards compat for current `clu integrate` callers.** If
   `--no-suite` semantics flip per Fix 1, any operator with a
   `--no-suite` baked into a script needs to know. Deprecation period
   with a stderr warning?

7. **Cherry-pick vs merge-commit for the actual integration.** Today
   the operator had to cherry-pick to get clean linear history; `clu
   ship` defaults could be merge-commit (preserves branch shape +
   "merged"-status detection) or cherry-pick (linear history, branch
   not strictly "merged"). For solo-agentic projects either works;
   default should match the operator's existing pattern (relaxed
   history per HealthData memory).

## Receipts (so the brief isn't operator-memory)

- 2026-05-23 apple-audit batch: 7 plans × markdown-only diffs, 6
  manual git operations to ship. Operator time ~10 minutes spelunking
  through `git status` / `clu queue list` / `git worktree list` /
  `git branch -a` to figure out the state.
- 2026-05-23 fix-{157, 159, 160} batch (in flight at time of writing):
  3 plans × Swift diffs, expect ~3-5min build per integrate on top of
  the same manual dance. If `clu ship --all-done` existed: one command,
  one approval point, walk away again.

## Not in scope for this brief

- Anything that changes worker behavior or the supervisor's tick loop.
- Anything that changes plan-file format or the Sessions index.
- Renaming the `archive` directory or changing where archived plans
  live.
