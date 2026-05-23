# clu-ship — one-action post-worker integration (direct + as-pr)

## Goal
Add a `clu ship` verb that collapses the post-`STATUS_DONE` dance
(validate → land on main → archive → cleanup) into one operator
action with a preview-then-confirm gate. Two modes, both in v1:
**direct** (local merge + push to main) and **as-pr** (open a GitHub
PR; let auto_archive_rule pick up the merge after the operator
clicks merge). Mode defaults from `.orchestrator.json` per project;
flags override. Retire the lying-name `clu integrate` to a mode-
agnostic `cmd_validate` along the way and update the `clu-plan`
skill to point at the new surface.

## Non-goals
- **`--resume` for partial-failure recovery.** Defer to a phase-2
  followup once `--all-done` ships in either mode. *Asymmetry-safe:*
  the destructive pairs in both modes (direct: `merge → push`;
  as-pr: `gh pr create → state stamp`) are recovered by a plain
  re-run — push retries trivially, PR creation is idempotent via
  `--head` branch lookup. Resume tokens add state-machine complexity
  that isn't load-bearing for v1.
- **Hard-removing `clu integrate`.** Deprecation alias only;
  operator muscle memory + any external scripts get one version to
  migrate. *Asymmetry-safe:* the alias points at `cmd_validate`,
  the same code path both `--direct` and `--as-pr` use for their
  `--check` flows. No behavior divergence during deprecation.
- **Changing `auto_archive_rule`.** It already does the post-merge
  cleanup we want (`cross_plan_rules.py:421-463`) and works
  identically for direct-merge and PR-merge (both eventually advance
  `origin/main`). `clu ship` composes with it rather than
  reimplementing.
- **Webhook-based PR-merge detection.** v1 polls via the existing
  supervisor tick + a periodic `git fetch`. *Asymmetry-safe:*
  polling already runs (cron); webhook would only reduce latency,
  not change correctness. Add later if latency is a real complaint.
- **Per-PR custom titles/bodies/labels/reviewers.** v1 opens PRs with
  a templated title + the plan-file content as the body. Operator
  edits on GitHub if they want more. Avoids re-implementing `gh pr
  create` config.
- **Project-config knobs for FF-vs-merge-commit policy.** Default to
  `--ff-only`-first-then-merge-commit; revisit only if asked.
  Direct-mode only — PR mode delegates merge strategy to GitHub.

## Files to touch
- `end_of_line/cli.py` — new `cmd_ship` (flags: `--plan`,
  `--all-done`, `--check`, `--yes`, `--direct`/`--as-pr` mutually
  exclusive); new `cmd_validate` (mode-agnostic dry-validate
  extracted from current `cmd_integrate`); rewire `cmd_archive` to
  commit atomically (lift the `unregister=True` commit logic from
  `_perform_archive`); `cmd_integrate` becomes a deprecation alias
  that prints a stderr notice and delegates to `cmd_validate`;
  argparse rewiring.
- `end_of_line/config.py` — add `dispatch.ship_mode: "direct" |
  "as_pr"` config field with validator; default `"direct"` when
  unset.
- `end_of_line/notify.py` — new `KIND_READY_TO_SHIP` event with a
  **mode-aware render function**: `render_ready_to_ship(slugs,
  mode, batch_id=None)` produces "PR ready at <url>" body for
  as-pr mode or "ready to ship to main" + copy-paste `clu ship
  --plan/--batch ...` for direct mode.
- `end_of_line/cross_plan_rules.py` — new `ready_to_ship_rule`
  (slots between `dry_merge_gate_rule` at line 418 and
  `auto_archive_rule` at line 463). Reuses the group-by-batch_id
  helper pattern from `dry_merge_gate_rule:329-342`. Per-plan
  suppression via `data["ship_pending"]` (mode + ts); cleared by
  `auto_archive_rule` when the plan finally archives so the marker
  doesn't leak.
- `end_of_line/skills/clu-plan/SKILL.md` — replace the manual
  integrate/archive language in the post-worker dispatch section
  with the `clu ship --all-done` flow; note that mode comes from
  `.orchestrator.json` so the same command works across projects.
- `tests/test_cli_ship.py` — new test file (happy path + each
  failure mode below, both modes).
- `tests/test_cli_validate.py` — new test file for `cmd_validate`
  (subsumes the dry-validate coverage from current
  `tests/test_cmd_integrate.py`).
- `tests/test_cmd_integrate.py` — trim to just the deprecation-
  alias smoke test (verify it prints to stderr and delegates).
- `tests/test_cli_archive.py` — update for atomic-commit.
- `tests/test_cross_plan_rules.py` — add `ready_to_ship_rule`
  coverage including the suppression-marker-cleared-by-auto-archive
  case.
- `tests/test_config.py` — `dispatch.ship_mode` validator + default.
- `docs/architecture.md` — slot `clu ship` into the operator-facing
  CLI surface and the tick chain; document FF-first-then-merge-
  commit as a deliberate divergence from `gh` / `git-town` / `jj`
  (which all commit to one merge strategy); document the as-pr
  cleanup loop (PR merge → next tick fetches → `is_branch_merged_
  into` detects → auto-archive runs).
- `docs/reference.md` — `cmd_ship`, `cmd_validate`, and updated
  `cmd_integrate` (deprecation alias) public-surface entries.
- `docs/operations.md` — update the integrate/archive sections;
  remove the staged-but-uncommitted footgun callout at line 650
  (resolved by atomic commit).
- `README.md` — refresh CLI usage examples if the post-worker dance
  appears there.

## Failure modes to anticipate
- **Push side-effect race (direct mode).** `is_branch_merged_into`
  reads local `origin/main` without fetching; `git push origin
  main` updates it on success — but only on success. Partial push
  failure could leave a stale local ref. Mitigation: re-read the
  ref after push; refuse to trigger the post-action tick if it
  didn't advance.
- **Canonical-checkout-on-feature-branch (direct mode).** `clu ship
  --direct` needs to `git checkout main` in canonical; uncommitted
  changes would block or be clobbered. Refuse if canonical has
  dirty index/working tree; surface what's dirty.
- **Branch ahead of origin for non-worker reasons.** Operator
  amended or pushed manually to the worker branch. Halt with the
  diff, ask.
- **Stale preview.** Operator runs `clu ship --plan X` (preview),
  delays, then runs `--yes`. Re-validate before destructive steps;
  don't trust the preview's snapshot.
- **Test-command timeout.** `dry_merge.attempt_merge` defaults to
  300s; iOS builds in sister projects routinely exceed that. If
  the suite times out under `--check`, fail closed but surface that
  300s was the cap and where to override it.
- **Concurrent ships of overlapping plans.** Two `--plan`
  invocations racing on the same canonical main — second's `git
  push` will fail non-fast-forward (direct mode), or second's `gh
  pr create` will succeed but then the merged-into-main detection
  could mis-attribute (as-pr mode). Acceptable for v1; document
  the failure shape per mode.
- **Third-party-races-cleanup.** GitHub's auto-delete-head-branches
  setting (or another worker, or operator manual push) deletes the
  branch between our merge and our cleanup-delete. From
  `git-town/issues/1626`: this is a real bug class. Cleanup-delete
  treats "branch already gone" as no-op success, not halt.
- **`auto_archive_rule` first-eligible-wins.** Only one archive
  fires per tick. After `clu ship --all-done` lands N plans on
  main, cleanup unspools across N ticks. Not new — matches today's
  behavior — but document so the operator doesn't think it's stuck.
- **`ship_pending` suppression marker not cleared on archive.** If
  `ready_to_ship_rule` sets `data["ship_pending"]` and
  `auto_archive_rule` doesn't clear it, the marker leaks past the
  plan's lifecycle. Explicit clear in `auto_archive_rule` before
  unregister, with a test that proves the clear happens.
- **PR-mode: `gh` not installed or unauthenticated.** Detect at
  command entry; surface install/auth instructions; don't half-
  execute. (Direct mode shouldn't need `gh` at all.)
- **PR-mode: PR already open for the branch.** `gh pr create`
  refuses with a clear error; we re-print it + suggest `gh pr view
  --web`. Idempotent re-runs are fine.
- **PR-mode: merge-detection lag.** After operator merges the PR on
  GitHub, local `origin/main` doesn't advance until something does
  `git fetch`. Mitigation: the supervisor tick (or a new
  `clu tick --fetch`) periodically fetches; or `clu ship --as-pr`
  registers a per-project flag that bumps fetch frequency until
  archive runs.
- **Deprecation-alias confusion.** Scripts calling `clu integrate
  --no-suite` keep working but the verb is now misleading-by-
  design. The stderr deprecation warning must be loud and link to
  `clu validate` (or `clu ship --check`).

## Done criteria
- `.orchestrator.json` supports `dispatch.ship_mode: "direct" |
  "as_pr"` with `"direct"` default; `clu ship --plan X --yes` picks
  the right mode without flags.
- `clu ship --plan X --direct --yes` validates (suite on by
  default), shows a preview + action list, requires `--yes`,
  merges to main (FF-first, merge-commit fallback), pushes
  `origin main` and the branch, triggers an immediate tick so
  `auto_archive_rule` runs without waiting for cron.
- `clu ship --plan X --as-pr --yes` validates, shows a preview,
  requires `--yes`, opens a PR via `gh pr create` with the plan
  body as the PR body, emits a notification with the PR URL, and
  stamps `data["ship_pending"] = {"mode": "as_pr", "pr_url": ...,
  "ts": ...}` for suppression.
- `clu ship --all-done` runs the appropriate mode's flow across
  every DONE plan with an unmerged branch in one invocation behind
  one `--yes`.
- `clu ship --plan X --check` (mode-aware) runs validate only and
  exits without destructive steps.
- `clu archive` commits the plan-file move atomically.
- `clu integrate` exits with a stderr deprecation notice and
  delegates to `clu validate`; `clu validate` is the canonical
  mode-agnostic verb for dry-validate.
- Supervisor emits `KIND_READY_TO_SHIP` to the inbox when DONE
  plans exist with unmerged branches; body is mode-aware (per-
  project `ship_mode`); for direct mode the body contains the
  exact `clu ship` invocation to copy-paste, for as-pr mode it
  contains the PR URL or "run `clu ship --as-pr --plan X --yes`
  to open."
- `ready_to_ship_rule` is suppressed by `data["ship_pending"]`;
  `auto_archive_rule` clears that marker before unregister; a test
  proves the marker doesn't leak.
- `clu-plan` SKILL.md's post-worker dispatch section references
  `clu ship` and explains that mode comes from `.orchestrator.json`.
- Tests cover, in both modes where applicable: happy path; suite-
  fail halt; textual-conflict halt; canonical-dirty refuse;
  branch-ahead-of-origin refuse; batch with mixed-eligible (some
  DONE, some still RUNNING) plans; push-fail halt + recoverable
  state (direct); `gh` missing/unauth (as-pr); PR-already-open
  (as-pr); third-party-deletes-branch-between-merge-and-cleanup
  (no-op success); `ship_pending` marker cleared by auto_archive.
- Deprecation alias test verifies stderr warning + delegation.
- `docs/architecture.md` documents `clu ship`, the FF-first-then-
  merge-commit divergence with rationale, and the as-pr cleanup
  loop. `docs/reference.md` carries the three new/updated public-
  surface entries. `docs/operations.md` is updated for the
  retired footgun.
- Full test suite green; pass count reported in the final commit.

## Parking lot
(empty)
