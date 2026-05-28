# delete-remote-branches-on-archive

## Goal
Stop leaving merged worktree branches stranded on `origin`. Default
behavior: when a plan auto-archives (or operator runs `clu archive`),
delete `origin/<branch>` after the local branch + worktree are removed.
Add a project-config knob `keep_remote_branches: false` so users who
want the current behavior can opt in. Direct-mode ship stops pushing
the branch entirely unless the knob is on (no point pushing what we're
about to delete).

## Non-goals
- **No retroactive cleanup of the 33 already-stranded branches.** Out
  of scope — operator can `git push origin --delete` them by hand or
  we add a `clu prune-remote-branches` command later. Safe asymmetry:
  going-forward branches behave correctly; historical branches are
  untouched and don't block anything.
- **No change to `--as-pr` mode's branch push.** Required to open the
  PR; can't be gated.
- **No new CLI flag** (`--keep-remote-branch`). User explicitly chose
  project-config over CLI flag.
- **No change to `_rollback_worktree` (worktree-setup-failed path).**
  The branch was never pushed to origin in that path, so there's
  nothing to delete remotely.

## Files to touch
- `end_of_line/config.py` — new `keep_remote_branches: bool = False`
  field on `ProjectConfig` + `_validate_keep_remote_branches` helper.
- `end_of_line/cli.py` — four sites:
  1. `_remove_worktree_and_branch` (L1356): accept `delete_remote: bool`
     param; when true, run `git push origin --delete <branch>` after
     the local delete. Best-effort. Match stderr substring `remote
     ref does not exist` → log debug, success. Any other nonzero →
     log warning, success. Never raises. Returns
     `((wt_ok, wt_err), (br_ok, br_err), (remote_ok, remote_err))`
     so callers can event the outcome.
  2. `_maybe_cleanup_worktree` (L1469): pass
     `delete_remote=not cfg.keep_remote_branches` through.
  3. `cmd_worktree_gc` (L3302): same — pass
     `delete_remote=not cfg.keep_remote_branches`. No new CLI flag;
     the project knob is the single source of truth.
  4. `_ship_apply_one_direct` (L4607-4611): gate the
     `git push origin <branch>` on `cfg.keep_remote_branches`. Default
     (False) → skip the push. Knob on → keep pushing as today.
  5. `_rollback_worktree` (L1541): explicit `delete_remote=False` —
     branch was never pushed in this path, nothing to clean up.
- `tests/test_worktree_cleanup.py` — 3 new tests:
  1. Default config: cleanup deletes `origin/<branch>` after local
     cleanup when origin is configured.
  2. `keep_remote_branches: true`: origin branch is preserved.
  3. `_remove_worktree_and_branch(delete_remote=True)` with origin
     not configured: doesn't crash, returns a benign skip in the
     remote tuple.
- `tests/test_auto_archive_rule.py` — 1 new test: rule firing on
  a merged branch deletes origin/<branch> when origin is configured.
- `tests/test_cli_ship.py` — 1 new test: `--direct` mode ship with
  default config does NOT push the feature branch to origin (only
  pushes main).
- `tests/test_config.py` — 1 new test: parser accepts the new knob,
  rejects non-bool, defaults to False when omitted.
- `README.md` — add bullet under "Other config fields:" (L112)
  documenting `keep_remote_branches`.
- `docs/operations.md` — append to "Auto-archive on merge" subsection
  (around L531-535 where `auto_archive` is documented) explaining the
  default delete behavior and the opt-out knob.
- `end_of_line/skills/clu-ship/SKILL.md` — note the new default
  behavior in the ship-mode docs so workers know direct mode no longer
  pushes the branch.

## Failure modes to anticipate
- **Test repo has no `origin` remote.** Local tests usually init a
  bare git repo without origin. The remote-delete call needs to
  no-op gracefully (return benign error, not crash). Mirror the
  existing pattern in `_is_branch_reachable_from_origin` which
  already handles missing-origin.
- **Race against another worker that already deleted the branch.**
  Worker B archives before worker A's tick fires. `git push origin
  --delete` against a missing branch returns nonzero stderr; we
  must treat it the same as the local `git branch -D` race — best-
  effort, log, don't fail.
- **GitHub's "Automatically delete head branches" double-deletes.**
  In `--as-pr` mode, GitHub may have already deleted the branch on
  PR merge. Our delete attempt then fails. Same handling: best-effort,
  warn, don't fail.
- **Operator runs `clu archive` on a plan whose branch was never
  pushed to origin** (e.g. plan failed before ship). Remote-delete
  finds no upstream ref. Must no-op without surfacing a scary error.
- **Existing `auto_archive` boolean knob and the new
  `keep_remote_branches` knob get conflated.** Different concepts:
  `auto_archive` gates the rule firing at all; `keep_remote_branches`
  shapes WHAT cleanup does. Tests should make this distinction
  explicit so future readers don't merge them.
- **Protected-branch / hook-rejected remote delete must not be
  swallowed as benign.** The benign-race signal is *only* stderr
  substring `remote ref does not exist`. Stderr like `[remote
  rejected] <branch> (protected branch hook declined)` is a real
  operator-actionable error — log as warning, don't surface as a
  hard error (archive still completes), but make it visible.
- **Workers that read SKILL.md mid-flight.** If clu-ship SKILL.md
  changes, in-flight workers may have a stale copy. Acceptable:
  next worker spawn picks up the new behavior; no in-flight
  contract changes.

## Done criteria
- `ProjectConfig.keep_remote_branches` exists, defaults to `False`,
  parses + validates from project config.
- Default behavior (no config override): `clu archive` and
  `auto_archive_rule` delete `origin/<branch>` along with the local
  branch when origin exists and branch is reachable from origin's
  default branch.
- `clu ship --direct` with default config does NOT push the feature
  branch to origin (main is pushed; branch is local-only until
  archive deletes it).
- All new tests pass; full suite green (current baseline 1414 →
  ~1420).
- README + docs/operations.md document the knob.
- One commit per phase, structured commit format per
  docs/conventions.md.

## Parking lot
- Reinstall `clu-ship` skill after the SKILL.md edit ships
  (`clu install-skill clu-ship` or operator's equivalent).
