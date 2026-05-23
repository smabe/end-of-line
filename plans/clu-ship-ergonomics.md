# clu-ship-ergonomics — field-feedback follow-up after first parallel-dispatch session

`docs/design-briefs/clu-ship-field-feedback.md` (this branch) captured
6 friction points from the 2026-05-23 HealthData session that drained
3 apple-audit plans in parallel. The new `clu ship` shape held up;
this plan closes the operator-facing ergonomics gaps so the *next*
fleet-drain session doesn't re-hit them.

No GH issue — the field-feedback design brief in this repo is the
plan's contract. Phase commits reference the brief by path instead of
`closes #N`.

Two phases, smallest-blast-radius-first. Phase 1 is the first-impression
fixes every new operator will hit (broken hook prompt + alarming
traceback + a Monitor-fleet workaround). Phase 2 is operational polish
(quieter `--all-done`, quieter `state_locator`, worktree-drift docs).

## Locked design decisions

### Phase 1 — cli (frictions #1 #2 #3)

- **Hook prompt drops `<blocker_id>` placeholder.** `INSTRUCTION` at
  `end_of_line/hooks/clu_inbox_surface.py:42-48` currently emits
  `clu answer --plan <slug> <blocker_id> <answer>` — there's no
  `blocker_id` positional in `cmd_answer` (only `answer` exists, per
  `cli.py:822-833`). Rewrite the literal to
  `clu answer --plan <slug> <answer>`. Multi-open-blocker
  disambiguation is *not* an in-scope use case — state_locator picks
  the single open blocker per plan via `--plan`.
- **`state_blocker.render_blocker` follows the hook fix.**
  `state_blocker.py:104` currently builds
  `answer_cmd = f"clu answer --plan {plan_slug} {blocker_id} <choice>"`
  — drop `{blocker_id}`. `answer_cmd` is referenced again at
  `state_blocker.py:126` (truncated-body branch); the single
  f-string change covers both. `render_stuck_blocker` (L142-151)
  has no `clu answer` text, out of scope. The iMessage reply hint
  at L113-117 (`Reply: <plan_slug> <number>`) is a *separate*
  grammar parsed by `notify_inbound.REPLY_RE` — leave it.
- **`tests/test_notify_render.py:46-49`** flips the assertion + drops
  the stale comment claiming the signature includes `<blocker_id>`.
- **`cmd_ship` defaults `--project` to CWD.** `cli.py:468` currently
  declares `required=True`. Swap to `default=None` and route through
  `_resolve_project_arg(args)` (the helper at `cli.py:2764-2774` that
  20+ other commands use). Operator runs `clu ship` from inside the
  project >95% of the time per the brief; cross-project ship is rare
  enough to accept `--project /path` opt-in.
- **`cmd_blockers_list` + `cmd_blockers_show` switch to
  `_resolve_project_arg`.** Both currently call
  `args.project.resolve()` directly (`cli.py:5043`, `cli.py:5065`),
  which raises `AttributeError` (not a clean `_die`) when `--project`
  is omitted. The fix is mechanical: replace both call sites.
- **Other `required=True --project` subcommands stay required.**
  `archive`, `migrate-archive`, `validate`, `integrate`,
  `worktree-attach`, `worktree-reattach`, `doctor` — all are rare,
  intentional cross-project ops where defaulting would mask
  "wrong target" mistakes. Out of scope.
- **`clu watch --all --task-list` mutex deleted.** `cli.py:3354-3357`
  is the only block; `watch.py` already streams per-plan `task=<slug>`
  / `task=<slug>/<phase>` IDs (verified in `bootstrap_task_list` at
  `watch.py:341-376` and `project_event_task` at `watch.py:306-329`).
  No `watch.py` changes needed. `tests/test_watch_task_cli.py:57-66`
  flips from "asserts rejection" to "asserts success".

### Phase 2 — quiet (frictions #4 #5 #6)

- **`clu ship --all-done` pre-filters to live branches.** In both
  `_cmd_ship_direct_all_done` (`cli.py:4290-4301`) and
  `_cmd_ship_pr_all_done` (`cli.py:4592-4603`), wrap the eligibility
  loop with an additional branch-exists check so plans whose branches
  were manually deleted post-merge stop showing up in the validation
  report. Existing checks (`wt is None`, `is_branch_merged_into`)
  remain — this adds one more pre-filter.
- **`state_locator: skipping` ENOENT moves to DEBUG.** In
  `_load_open_blockers` (`state_locator.py:86-105`), split the
  state-file-load except so `FileNotFoundError` logs at DEBUG and
  everything else (`InvalidSlug`, `ValueError`, `SchemaVersionMismatch`,
  `JSONDecodeError`, other `OSError`) keeps WARNING. The config-load
  except (L98) stays at WARNING — config drift is a real problem
  worth surfacing.
- **Worktree config drift documented, not toolchain'd.** Add a
  "Worktree config management" section to
  `docs/design-briefs/clu-ship.md` documenting the answer-time
  config-patch pattern + the operator's pre-emptive `Edit` workaround.
  No `clu sync-config` command this round — operator's priority list
  explicitly says "document first; tool later if it becomes painful."

## Non-goals

- **No new `clu sync-config` / `clu cleanup --absorbed` commands.**
  Documentation and registry-driven pre-filters cover both pains for
  now; defer tool-shaped fixes until a second field session
  re-surfaces them. (Friction #4 + #6.)
- **No fix for `required=True --project` on `archive`,
  `migrate-archive`, `validate`, `integrate`, `worktree-attach`,
  `worktree-reattach`, `doctor`.** These are rare cross-project ops
  where defaulting would mask "wrong target project" bugs.
- **No multi-open-blocker disambiguation on `cmd_answer`.**
  state_locator handles disambiguation via `--plan`; no plan ever
  carries multiple open blockers in current usage.
- **No worker-quality / docs-verify changes.** The brief mentions a
  docs-verify false positive (worker-side judgment), not a clu bug.
- **No GitHub tracking issue.** Design brief at
  `docs/design-briefs/clu-ship-field-feedback.md` is the contract.

## Files touched

- `end_of_line/hooks/clu_inbox_surface.py` — P1 modified — `INSTRUCTION` literal at L42-48 (drop `<blocker_id>`). API hotspot: prompt text consumed by Claude SessionStart hook.
- `end_of_line/state_blocker.py` — P1 modified — `render_blocker` `answer_cmd` f-string at L104 (drop `{blocker_id}`). API hotspot: rendered notification text shipped to iMessage / Discord.
- `end_of_line/cli.py` — P1, P2 modified — `cmd_ship` argparse + body (L468, ~L4076, ~L4101), `cmd_blockers_list` (L5043), `cmd_blockers_show` (L5065), `cmd_watch` mutex (L3354-3357), `_cmd_ship_direct_all_done` (L4290-4301), `_cmd_ship_pr_all_done` (L4592-4603). API hotspot: `cmd_ship` and `cmd_blockers_*` `--project` flag becomes optional (additive — no operator script breaks).
- `end_of_line/state.py` — P2 possibly modified — may add `_local_branch_exists` helper next to `is_branch_merged_into` if no equivalent exists.
- `end_of_line/state_locator.py` — P2 modified — `_load_open_blockers` (L86-105) splits ENOENT to DEBUG.
- `docs/design-briefs/clu-ship.md` — P2 modified — new "Worktree config management" section.
- `tests/test_notify_render.py` — P1 modified — flip assertion + drop stale comment at L46-49.
- `tests/test_watch_task_cli.py` — P1 modified — flip `test_task_list_and_all_mutually_exclusive` from rejection to success at L57-66.
- `tests/test_blockers*.py` (verify path during phase) — P1 new tests — `--project` omitted defaults to CWD on list + show.
- `tests/test_ship*.py` (verify path) — P1 + P2 new tests — `--project` defaults to CWD; `--all-done` filters dead branches.
- `tests/test_state_locator*.py` (verify path) — P2 new tests — ENOENT silent at WARNING; corrupt-file still warns.
- `tests/test_clu_inbox_surface*.py` (verify or create) — P1 new test — `INSTRUCTION` literal contains correct CLI syntax.

## Per-phase done checklist

- TDD: failing tests first.
- `/code-review` after if diff >1 file or ~30 lines (both phases qualify).
- Full suite green: `python3 -m unittest discover -s tests`. Report pass count.
- Structured commit format (Title / Why / What's new / Under the hood / Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- Call `clu complete --plan clu-ship-ergonomics --phase <id> --token <T>` with the worker token on success.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| cli | `clu-ship-ergonomics-cli.md` | Hook prompt fix + `render_blocker` fix + `--project` defaults on ship/blockers + `clu watch --all --task-list` unblock (#1 #2 #3) | 2h |
| quiet | `clu-ship-ergonomics-quiet.md` | `--all-done` enumeration filter + `state_locator` ENOENT gate + worktree-drift docs (#4 #5 #6) | 1.5h |
