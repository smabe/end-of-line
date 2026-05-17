# small-cli-fixes

## Goal
Three independent operator-facing CLI fixes batched together: install-skill
directory-symlink bug (#23), `clu blockers list|show` so blocker payloads
don't require JSON-spelunking (#32), and `clu archive` plan-file move to
`plans/shipped/` (#31, post-rescope — worktree-cleanup half already shipped
in #34).

## Diagnosis
- **Hypothesis (#23):** `cmd_install_skill` at cli.py:1340-1405 calls
  `target.parent.mkdir(parents=True, exist_ok=True)` at line 1389, which
  follows directory symlinks. If `~/.claude/skills/brainstorm` is a directory
  symlink pointing at `~/projects/abe-skills/skills/brainstorm`, `mkdir`
  + subsequent `write_bytes(SKILL.md)` writes through into the canonical
  source. The existing `is_symlink` check at line 1371 only inspects the
  SKILL.md file, never the parent directory.
- **Falsifiable test (#23):** Create tmp dir `A`, create symlink `B → A`,
  attempt `install-skill` targeting `B/SKILL.md`. With current code: writes
  into A. After fix: detects symlink-on-parent, emits warning, and proceeds
  intentionally (per operator preference for follow-into behavior, since the
  operator's actual setup deliberately symlinks skill dirs to abe-skills).
- **Test result (#23):** Hypothesis confirmed by reading the code; agent's
  exploration cited line 1389 as the bug site. Fix is contained.
- The other two (#32, #31) are pure feature adds — no diagnosis needed.

## Non-goals
- Worktree changes to install-skill (#23 is bug-only; #34 covered worktree
  cleanup separately).
- Adding `clu blockers --closed` filter to include consumed blockers — parking
  lot if the operator asks for it later. The issue body mentions it but it's
  not in the core ask.
- Generalizing the archive plan-move to non-`.md` files — only the plan file
  (`plans/<slug>.md`) is moved.
- Refusing install-skill on directory-symlink without `--force` — the
  operator's actual setup is symlink-by-design; follow-into-with-warning is
  the correct UX.
- Renaming `clu blockers` to anything else — operator already cited the name.

## Files to touch
- `end_of_line/cli.py:1340-1405` (cmd_install_skill) — at the pre-flight
  validation block (around line 1371-1380), additionally check
  `target.parent.is_symlink()` and emit `print(f"warning: {target.parent}
  is a symlink → {target.parent.resolve()}; install-skill will write through",
  file=sys.stderr)`. Then proceed normally — the symlink-follow is intentional
  for the operator's setup. The warning is the signal so the operator
  knows what happened.
- `end_of_line/cli.py` (subcommand registry, around line 369 after
  `uninstall-hook`) — register `blockers` top-level subcommand. Use
  `p_blockers.add_subparsers(dest="blockers_cmd")` with two sub-subcommands:
  `list` (just needs `--project`, `--plan` via `add_common`) and `show`
  (adds positional `blocker_id`).
- `end_of_line/cli.py` — new `cmd_blockers_list(args)` and
  `cmd_blockers_show(args)`. Both load state via the standard
  `load_project_config(args.project.resolve()) → cfg.state_path(args.plan)`
  pattern, validate slug. `list` iterates `data["blockers"]` filtering on
  `answer is None` (open only), formats per the issue body's spec.
  `show` finds the requested blocker_id, prints question/options/context/
  asked_at + related events from `data["events"]` where
  `event.get("blocker_id") == blocker_id`.
- `end_of_line/cli.py` (top-level dispatcher near lines 651-683) — route
  `args.cmd == "blockers"` to a `_dispatch_blockers(args)` helper that
  branches on `args.blockers_cmd` to call list or show.
- `end_of_line/cli.py:2854-2891` (cmd_archive) — after
  `_maybe_cleanup_worktree(...)` at line 2880 (still inside the `mutate`
  window), add a plan-file move step: compute `plan_md =
  cfg.plan_dir_resolved / f"{args.plan}.md"`. If `plan_md.exists()`, ensure
  `plans/shipped/` exists (`plan_md.parent / "shipped"`), then
  `subprocess.run(["git", "mv", str(plan_md), str(shipped_dir / plan_md.name)],
  check=True, cwd=cfg.project_root)`. Idempotent: if `plan_md` doesn't exist
  (already moved), skip silently. Update the status print to mention the
  plan-file move when it happened.
- `tests/test_install_skill.py` — new test: create tmp directory `canonical/`,
  create symlink `linked/` → `canonical/`, run install-skill targeting
  `linked/SKILL.md`. Assert: warning printed to stderr (capture via
  `unittest.mock.patch('sys.stderr')`), install proceeds, file written.
- `tests/test_blockers.py` (new) — coverage:
  (a) `blockers list` with empty `data["blockers"]` prints "no blockers" and
  exits 0;
  (b) `blockers list` with one open + one consumed blocker shows only open;
  (c) `blockers show q-1` prints full question + options + context + asked_at;
  (d) `blockers show q-99` for nonexistent blocker `_die`s with
  `ExitCode.UNKNOWN_TASK`;
  (e) `blockers show q-1` includes related events from `data["events"]`.
- `tests/test_archive.py` — extend (or add if missing — explore noted neither
  `test_archive.py` nor `test_blocker*.py` exists): new tests for plan-file
  move:
  (a) happy path: plan file moves from `plans/<slug>.md` to
      `plans/shipped/<slug>.md`, status print reflects move;
  (b) idempotent: archive called twice; second call no-ops on plan file;
  (c) `plans/shipped/` is created if missing;
  (d) worktree-cleanup half (already shipped) still works after the new step.
- `docs/reference.md` — document `clu blockers list` and `clu blockers show`
  subcommands.
- `docs/operations.md` — update the archive how-to to mention plan-file move
  and the new shipped/ directory.

## Failure modes to anticipate
- **#23 fix shape** — operator confirmed follow-into-with-warning over
  refuse-with-`--force`. The warning to stderr is the operator's signal that
  the symlink behavior triggered. Risk: silent acceptance of an UNINTENDED
  symlink. Mitigation: the warning is mandatory and goes to stderr, not
  stdout, so it's visible in normal `clu install-skill` runs.
- **`clu blockers list` on a plan with no blockers** — print "no blockers"
  to stdout (NOT stderr), exit 0.
- **`clu blockers show q-99` on nonexistent blocker** — `_die(ExitCode.
  UNKNOWN_TASK, f"no blocker {blocker_id} on {args.plan}")`.
- **`clu blockers show` events join** — events have `blocker_id` field only
  on the blocker-specific events (`EVENT_PHASE_BLOCKED`, `EVENT_BLOCKER_ANSWERED`,
  `EVENT_BLOCKER_CONSUMED`). Filter on `event.get("blocker_id") == blocker_id`
  is safe — events without the field just won't match.
- **`cmd_archive` git-mv failure** — uncommitted changes in the plan file,
  permissions, etc. `subprocess.run(..., check=True)` raises
  `CalledProcessError`; surface the git stderr and `_die(ExitCode.GIT_FAILURE,
  ...)` (or whatever existing exit code matches git failures elsewhere in
  the codebase — check `_remove_worktree_and_branch` for the pattern).
- **`plans/shipped/<plan>.md` already exists** — git mv refuses with non-zero
  exit; the previous failure-mode handler surfaces it. Don't overwrite; the
  operator can investigate (this would indicate they unarchived or
  re-shipped, both worth a manual decision).
- **The `add_common(p)` argparse pattern injects `--project`/`--plan`** —
  `clu blockers list` needs both; `clu blockers show <id>` needs both plus
  the positional. Verify the nested subparser registration carries the
  common args correctly down (test `clu blockers list --help` shows both).
- **`cfg.plan_dir_resolved`** may not exist as an attribute — confirm via the
  exploration agent's findings or read `config.py` at the touch site.
  Worst case: compose manually as `cfg.project_root / cfg.plan_dir`.

## Done criteria
- **Phase 1 (#23):** install-skill targeting a SKILL.md under a directory
  symlink emits a stderr warning and proceeds (writing through the symlink
  to the canonical source — operator's intended behavior). Existing
  install-skill tests still pass; new directory-symlink test green. Closes #23.
- **Phase 2 (#32):** `clu blockers --project P --plan S` lists open blockers
  with `id [phase_id] (asked_at)` + question + numbered options.
  `clu blockers --project P --plan S q-1` shows full payload including
  context + related events from `data["events"]`. Empty/missing cases handled
  per failure modes. New tests green. Reference doc updated. Closes #32.
- **Phase 3 (#31):** `clu archive --project P --plan S` moves
  `plans/<plan>.md` → `plans/shipped/<plan>.md` via `git mv`, creating
  `plans/shipped/` if missing, IN ADDITION to the existing worktree+branch
  cleanup. Idempotent against already-moved plan files. New tests green.
  Operations doc updated. Closes #31.
- Full suite green at 536+N tests at every phase boundary.
- All work shipped on the `small-cli-fixes` branch via clu's worktree.
- One commit per phase, `/simplify` between.

## Parking lot
- `clu blockers --closed` filter to include consumed/answered blockers —
  defer to follow-up if the operator finds need after dogfooding `list`.
