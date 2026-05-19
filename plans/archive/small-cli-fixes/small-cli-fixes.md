# small-cli-fixes — three operator-facing CLI fixes (#23 #32 #31)

Batch of three independent operator-facing fixes that share a touch surface
(`cli.py` only). Ship sequentially through clu so each phase lands as a clean
commit. Smallest-first: bug fix → new read-only CLI → enhancement to existing
command.

## Locked design decisions

### Phase 1 — install-skill directory-symlink fix (#23)
- **Bug site:** `cmd_install_skill` (cli.py:1340-1405) calls
  `target.parent.mkdir(parents=True, exist_ok=True)` at line 1389, which
  follows directory symlinks. The existing `is_symlink` check at line 1371
  only inspects the SKILL.md file, never the parent directory.
- **Fix shape:** at the pre-flight validation block, additionally check
  `target.parent.is_symlink()`. If True, emit a stderr warning naming the
  symlink target (`f"warning: {target.parent} is a symlink → {target.parent.
  resolve()}; install-skill will write through"`), then proceed normally.
- **Why follow-into, not refuse:** the operator's actual setup
  (`~/.claude/skills/<name>` → `~/projects/abe-skills/skills/<name>`) is
  symlink-by-design — refusing would block the canonical workflow. The
  warning is the signal so the operator knows what happened.

### Phase 2 — `clu blockers list|show` (#32)
- **New top-level subcommand** registered at cli.py around line 369 (after
  `uninstall-hook`). Uses `p_blockers.add_subparsers(dest="blockers_cmd")`
  with two sub-subcommands: `list` (needs `--project`, `--plan` via
  `add_common`) and `show` (adds positional `blocker_id`).
- **`cmd_blockers_list`** — iterates `data["blockers"]` filtering on
  `answer is None` (open only), formats as:
  ```
  <id> [<phase_id>] (asked <asked_at>)
    <question>
    Options:
      0. <option>
      1. <option>
  ```
  Empty case prints `"no open blockers on {args.plan}"` to stdout, exit 0.
- **`cmd_blockers_show`** — finds the blocker by id, prints full payload
  (question, options, context, asked_at, answer if any). Joins related
  events from `data["events"]` where `event.get("blocker_id") ==
  blocker_id` (filters
  `EVENT_PHASE_BLOCKED`/`EVENT_BLOCKER_ANSWERED`/`EVENT_BLOCKER_CONSUMED`).
  Not-found → `_die(ExitCode.UNKNOWN_TASK, f"no blocker {blocker_id} on
  {args.plan}")`.
- **Blocker schema** (state.py:417-427): fields are `id`, `phase_id`,
  `type`, `question`, `options` (list[str]), `context`, `asked_at`,
  `answer`, `answered_at`.

### Phase 3 — `clu archive` plan-file move (#31)
- **Touch site:** `cmd_archive` (cli.py:2854-2891). After
  `_maybe_cleanup_worktree(...)` at line 2880, still inside the `mutate`
  window: move the plan file from `plans/<slug>.md` to
  `plans/shipped/<slug>.md` via `subprocess.run(["git", "mv", ...],
  check=True, cwd=cfg.project_root)`.
- **Idempotent:** if the plan file doesn't exist (already moved by a
  previous archive run, or manual operator move), skip silently.
- **`plans/shipped/` creation:** `mkdir(parents=True, exist_ok=True)` if
  missing. Confirmed by exploration: directory does NOT currently exist.
- **Plan filename:** `f"{args.plan}.md"` literal — slug validated already
  by `st.validate_slug` at the function entrance.
- **git-mv failure** (uncommitted changes, conflicts) surfaces via
  `CalledProcessError`; `_die(ExitCode.GIT_FAILURE, stderr_msg)`.
- **Plan file already moved (`shipped/<plan>.md` exists)** — `git mv`
  refuses; let the error surface — don't silently overwrite.
- **Status print** updated to mention the move when it happened.

## Non-goals

- Worktree changes to install-skill (#23 is bug-only).
- `clu blockers --closed` filter (parking lot).
- Generalizing the archive plan-move to non-`.md` files.
- Refusing install-skill on dir-symlink without `--force` (operator
  confirmed follow-into is the desired behavior).
- Renaming `clu blockers` to anything else.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood / Tests /
  `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- Call `clu complete --plan small-cli-fixes --phase <id> --token <T>` with
  the worker token on success.

## Parking lot

- `clu blockers --closed` filter (include consumed/answered blockers) —
  defer to follow-up if the operator finds need after dogfooding `list`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| install-skill-bug | `small-cli-fixes-install-skill-bug.md` | Directory-symlink follow-with-warning in `cmd_install_skill` (#23) | 1h |
| blockers-cli | `small-cli-fixes-blockers-cli.md` | New `clu blockers list|show` end-to-end (#32) | 2h |
| archive-plan-move | `small-cli-fixes-archive-plan-move.md` | `cmd_archive` git-mv to `plans/shipped/` (closes #23 #32 #31) | 1.5h |
