# clu-worktrees

## Goal
Add opt-in per-plan git worktrees so concurrent plans in the same project
can run in isolated working trees on their own branches, preventing the
intertwined-diff failure mode that aborted the HealthData
workout-rearchitect run on 2026-05-15. Closes #24.

## Non-goals
- **No auto-merge / auto-PR / auto-push.** Worker commits stay on the
  local `clu/<slug>` branch; operator decides when (or whether) to share.
- **No `clu worktree reattach` subcommand.** Rare recovery — operator
  edits state JSON by hand or rebuilds the plan.
- **No `worktree.base_dir` config knob.** Sibling default only.
- **No schema_version bump.** `worktree` is additive optional; readers
  use `data.get("worktree")`.
- **No default-on / auto-detect.** Manual opt-in via `--worktree` only.
- **No refusing concurrent non-worktree dispatch.** Warn only, never refuse.
- **No changes to `clu pause` / `clu abort` semantics.** They leave the
  worktree alone — operator's call.
- **No `{cwd}` template variable or auto-`cd` injection in
  `dispatch.command`.** Mechanism is `Popen(cwd=worktree.path)`.
- **No `clu archive` command.** It doesn't exist today; archive is an
  external file-move done by the `post-ship` skill. The warning hooks
  into `cmd_unregister_all_archived` instead.
- **No new `KIND_*` notify constant.** Reuse `KIND_HALTED` (already in
  `QUIET_HOURS_BYPASS_KINDS`) with new render functions.

## Files to touch
- `end_of_line/state.py` — add `EVENT_WORKTREE_MISSING` and
  `EVENT_WORKTREE_CONFLICT_WARNING` constants; add `get_worktree(data)`
  reader helper; no `SCHEMA_VERSION` bump.
- `end_of_line/cli.py` — `init` parser gains `--worktree [PATH]`
  (nargs=?), `--branch`, `--base-ref`; `cmd_init` performs git worktree
  creation with rollback on state-save failure + echoes resolved SHA
  and symbolic ref to stderr; new `worktree` subcommand group (mirrors
  `cmd_queue` two-tier dispatch) with `gc` subcommand; `cmd_list`
  annotates worktree rows; `cmd_unregister_all_archived` warns about
  orphan worktrees during prune; new `ExitCode.WORKTREE_SETUP_FAILED`
  entry; new `_resolve_ref(project_root, ref)` helper (git rev-parse;
  `_verify_commit_shas` is not reusable — it cat-file-e's SHAs, not refs).
- `end_of_line/supervisor.py` — `tick()` reads `data.get("worktree")`
  while in the state lock and stuffs it onto a new `TickResult.worktree`
  field. Avoids a second state load in dispatch. New conflict-detection
  pass either here or in cli.cmd_tick_all's post-loop project iteration
  (cli.py:1438-1455 already groups by project).
- `end_of_line/dispatch.py` — `dispatch_for_tick` reads
  `result.worktree`; if present, stats path + runs
  `git -C <path> rev-parse --git-dir`. On miss: append
  `EVENT_WORKTREE_MISSING`, `release_claim`, set `status=PAUSED`, call
  `notify.notify(cfg.notify, KIND_HALTED, render_worktree_missing(...))`
  (mirrors `_pause_for_systemic_failure` at lines 255-299). On hit:
  pass `cwd=result.worktree["path"]` to `Popen` instead of
  `cfg.project_root` (today's line 152).
- `end_of_line/fleet.py` — `render()` tuple construction (lines 86-92)
  gains a worktree column; dynamic-width formatting picks it up
  automatically.
- `end_of_line/notify.py` — `render_worktree_missing(plan_slug, path)`
  and `render_worktree_conflict(project_root, slug_a, slug_b)` after
  existing render_* siblings (lines 155-211).
- `tests/test_init_worktree.py` (new) — happy path; rollback on
  state-save fail; refuse-existing-branch; refuse-existing-path; `~`
  expansion; SHA echo; non-git-repo refusal. Uses real `git init` in
  tempdir per test_worker_callbacks.py convention.
- `tests/test_dispatch.py` (extend) — assert `Popen` called with
  `cwd=worktree.path` when state has worktree; assert main-repo cwd
  when state lacks worktree; missing-worktree → pause + event +
  release_claim path.
- `tests/test_worktree_gc.py` (new) — list candidates (done+halted);
  `--confirm` removes dir only; `--delete-branch` drops branch ref;
  `--include-archived` widens scope; non-existent-worktree on plan is
  a no-op.
- `tests/test_conflict_warning.py` (new) — init stderr hint when sibling
  active plan exists without worktree; tick emits event + notify once,
  suppresses on second tick via per-plan `in_conflict_with` flag, clears
  when one plan finishes.
- `tests/test_list_fleet.py` (new or extend) — worktree annotation appears
  for worktree-bearing rows; absent for plain rows.
- `tests/test_unregister.py` (extend) — `--all-archived` stderr warning
  when a pruned ghost entry's state had a `worktree` field.
- `docs/contract.md` — `worktree = {path, branch, base_ref}` field shape;
  new `EVENT_WORKTREE_MISSING` and `EVENT_WORKTREE_CONFLICT_WARNING`
  semantics; new per-plan `in_conflict_with` field.
- `docs/architecture.md` — worktree-aware dispatch in the tick chain;
  TickResult.worktree handoff; conflict-warning per-project pass in
  cmd_tick_all post-loop.
- `docs/operations.md` — operator walkthrough: init with `--worktree`;
  gc workflow; recovery from missing-worktree pause; conflict-warning
  meaning + remediation.
- `docs/reference.md` — `clu worktree gc` subcommand surface; new
  `cmd_init` flags.
- `README.md` — mention `--worktree` in the init example.

## Failure modes to anticipate
- **`git worktree add` succeeds but `save_atomic` fails** — orphan worktree
  + branch on disk. Mitigation: try/except around `save_atomic` inside
  `cmd_init`, rollback with `git worktree remove <path>` + `git branch -D
  <branch>` before returning `_die(ExitCode.WORKTREE_SETUP_FAILED, ...)`.
- **Operator deletes worktree dir while plan is paused** — next dispatch
  finds path missing. Mitigation: dispatch-time stat + `git rev-parse
  --git-dir` check → pause + halt-bypass iMessage.
- **`--base-ref` points at unrelated branch** (operator ran init from a
  stale feature branch) → worker forks from wrong place. Mitigation: echo
  resolved SHA + symbolic ref back at init.
- **Race between dispatch-time stat and `Popen`** — worktree disappears in
  the millisecond gap. Mitigation: catch `FileNotFoundError` from Popen,
  funnel into the same `EVENT_WORKTREE_MISSING` path.
- **`clu worktree gc --confirm` race** — done plan gets resumed between
  `gc` list and `--confirm`. Mitigation: gc filters by status at
  action-time, not list-time. Stale-list risk accepted in v1.
- **Branch `clu/<slug>` pre-exists** → refuse at init with clear error;
  no `--branch-exists-ok` escape hatch.
- **`--worktree PATH` already exists** → `git worktree add` refuses;
  init aborts cleanly (no rollback needed — nothing created).
- **Worktree path "valid" but `.git` detached** (operator ran
  `git worktree prune`) → cwd works, git ops misroute. Mitigation:
  `git -C <path> rev-parse --git-dir` at dispatch-time stat.
- **State has `worktree` but `git worktree list` doesn't list it** —
  caught by the rev-parse check above.
- **`_verify_commit_shas` reuse trap** — it validates SHAs via
  `cat-file -e`, not refs. Mitigation: new `_resolve_ref(project_root,
  ref)` helper using `git rev-parse <ref>` returning the resolved SHA.
- **TickResult.worktree field defaults `None`** — supervisor.tick callers
  that don't read it (queue advancement, post-loop conflict pass) keep
  working. Field is documented as optional.

## Done criteria
- `clu init --plan foo --worktree` creates worktree at
  `<project-parent>/<basename>-foo`, branch `clu/foo`, persists
  `{path, branch, base_ref}` in state, echoes resolved SHA + symbolic
  ref to stderr.
- `clu init --plan foo --worktree /custom/path` honors path with `~`
  expansion.
- `clu init --plan foo --worktree --branch other --base-ref main`
  honors all overrides.
- `clu init` refuses cleanly when branch or path exists; no half-state.
- `git worktree add` failure → no state written; `save_atomic` failure
  → worktree + branch rolled back + `ExitCode.WORKTREE_SETUP_FAILED`.
- Dispatch routes `Popen(cwd=worktree.path)` when state has worktree;
  `{project}` substitution still resolves to main project_root.
- Missing worktree at dispatch → `EVENT_WORKTREE_MISSING` + `status=
  PAUSED` + halt-bypass iMessage naming slug + path.
- `tick-all` emits `EVENT_WORKTREE_CONFLICT_WARNING` + halt-bypass
  notify once per (project, sorted-slug-pair) conflict; suppression
  persisted via `in_conflict_with: [other_slug, ...]` field on each
  involved plan's state, cleared automatically when one plan transitions
  out of "active" (current_claim present OR status=RUNNING).
- `clu init` prints stderr hint when sibling active plan in same
  project lacks a worktree.
- `clu list` / `clu fleet` show worktree annotation on rows with one.
- `clu unregister --all-archived` prints stderr warning per ghost
  entry whose state had a worktree, naming the orphan path.
- `clu worktree gc` lists `done`+`halted` plans with worktrees;
  `--confirm` removes directory only via `git worktree remove`;
  `--delete-branch` also drops branch via `git branch -D`;
  `--include-archived` widens to archived plans.
- Full test suite green (currently 461 → target ~500+).
- `docs/contract.md`, `docs/architecture.md`, `docs/operations.md`,
  `docs/reference.md`, `README.md` updated per the file list.

## Phases
1. **Schema field + state helpers + event constants + ExitCode.** Add
   `EVENT_WORKTREE_MISSING`, `EVENT_WORKTREE_CONFLICT_WARNING`,
   `get_worktree(data)`, `ExitCode.WORKTREE_SETUP_FAILED`. No behavior
   change yet — just constants and a reader.
2. **`clu init --worktree` end-to-end** — flag parsing, `_resolve_ref`
   helper, pre-checks (git repo, branch nonexistent, path nonexistent,
   ref resolvable), `git worktree add`, state save with rollback,
   SHA echo to stderr.
3. **TickResult.worktree handoff + dispatch routing** —
   `supervisor.tick` reads `data.get("worktree")` in the lock,
   stuffs on TickResult; `dispatch_for_tick` consumes it for
   `Popen(cwd=...)`. Tests assert cwd is set correctly + callback
   `{project}` still resolves to main.
4. **Missing-worktree at dispatch** — post-claim, pre-Popen stat +
   `git rev-parse --git-dir` check; on miss, run
   `_pause_for_systemic_failure`-shaped flow with
   `EVENT_WORKTREE_MISSING` + `render_worktree_missing()` +
   `KIND_HALTED` notify.
5. **Conflict warning (init + tick)** — init stderr hint via
   per-project active-plan scan; tick-time detection in
   `cmd_tick_all` post-loop project iteration; per-plan
   `in_conflict_with` suppression; `render_worktree_conflict()` +
   `KIND_HALTED` notify once per pair.
6. **`clu worktree gc` subcommand** — two-tier subparser mirroring
   `clu queue`; lists candidates by status; `--confirm` invokes
   `git worktree remove`; `--delete-branch` invokes `git branch -D`;
   `--include-archived` widens scope.
7. **`clu list` / `clu fleet` annotation +
   `cmd_unregister_all_archived` warning + docs.** Final UX polish
   and full doc sweep.

Each phase ends with the standard Phase Completion Cycle:
`/simplify` → full unittest suite → commit. Per project convention,
structured commit message with Title / Why / What's new / Under the
hood / Tests trailer.

## Parking lot
- Mixed-mode conflict warning (one plan with worktree, one without):
  currently only warns when BOTH lack one. Revisit if a real incident
  shows the non-worktree plan stomping the worktree plan's main-repo state.
- `worktree.base_dir` config knob — add when an operator asks to
  centralize worktrees out of `~/projects/`.
- `clu worktree reattach --plan X --path NEW` — formalize the recovery
  path. Defer until needed.
- Stale-list hash on `clu worktree gc --confirm` — paranoid safety
  against a plan resuming mid-gc. v1 accepts the race.
- `clu doctor --worktree` — health check walking `git worktree list`
  vs state.worktree.path for every registered plan, surfaces drift.
- Decision-point if conflict-warning suppression on per-plan state
  proves wrong (e.g. operator manually edits state and clears the flag,
  causing re-notify spam): move to a project-level scratch file under
  `.orchestrator/supervisor.json`.
