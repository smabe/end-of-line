# dry-merge-gate-cli-docs — `clu integrate` operator command + docs (closes #50)

You are phase `cli-docs` of the `dry-merge-gate` plan. Ship the
operator-facing surface: `clu integrate` for on-demand re-runs,
`.orchestrator.json:test_command` config field, and docs updates
across contract / architecture / operations. This phase CLOSES #50.

## Locked decisions (do NOT re-litigate)

See `plans/dry-merge-gate.md`. Summary:

- `clu integrate --project P --batch B [--branches a,b,c]
  [--no-suite] [--base-ref REF]`. Wraps `dry_merge.attempt_merge`
  directly; does NOT fire the cross-plan rule (no state mutation,
  no follow-up emission). Useful for replay-after-fix, stuck
  batches, or CI-side validation.
- `--branches` overrides batch-resolution (lets operator dry-merge
  arbitrary branches not tied to clu plans).
- `--no-suite` forces textual-merge-only even when `test_command`
  is configured (fast operator check).
- `--base-ref` defaults to `main`.
- `.orchestrator.json:test_command: str | None` — optional. When
  set, the gate rule (phase `rule`) and `clu integrate` both use
  it. Absent → textual-merge-only mode.
- Exit codes: `OK` on clean; `GENERIC` on dirty (with stderr
  describing outcome). Operator can shell-script around this.

## Read first

- `end_of_line/cli.py:362-400` — `p_archive` argparse pattern (same
  shape as `p_integrate`).
- `end_of_line/cli.py:3218-3286` — `cmd_archive` for the
  validate-slug / load-cfg / do-work pattern.
- `end_of_line/cli.py:1988-2090` — `cmd_queue_add` for multi-arg
  parsing (`nargs="+"`-style).
- `end_of_line/cli.py:870-880` — command dispatch (`if args.cmd ==
  "...": return cmd_...`). Wire `integrate` here.
- `end_of_line/config.py:68-150` — `ProjectConfig` dataclass +
  `load_project_config` body. Add `test_command: str | None = None`.
- `end_of_line/dry_merge.py` (phase engine output).
- `end_of_line/cross_plan_rules.py` `dry_merge_gate_rule` (phase
  rule output) — `clu integrate` and the rule share the engine but
  not the side-effect surface; document the split in operations.md.
- `docs/contract.md` — find the queue-entry schema section and the
  plan-state schema section.
- `docs/architecture.md` — find the cross-plan rule chain section.
- `docs/operations.md` — find the CLI reference section.
- `docs/_outline.md` — verify which doc owns the new content per
  the structural contract.

## Produce

1. **Failing tests first.** New file `tests/test_cmd_integrate.py`:
   - `test_integrate_resolves_batch_to_done_member_branches` — 2
     DONE plans in batch "b1" with worktrees; `clu integrate
     --batch b1` calls engine with both branches.
   - `test_integrate_explicit_branches_overrides_batch_resolution` —
     `--branches a,b` skips batch resolution, calls engine with
     [a, b].
   - `test_integrate_requires_batch_or_branches` — neither given →
     `ExitCode.GENERIC` with clear error message.
   - `test_integrate_no_suite_flag_skips_test_command` — even with
     `test_command` set, `--no-suite` passes `None` to engine.
   - `test_integrate_dirty_returns_nonzero` — stub engine to return
     `textual_conflict`; CLI exit code != 0; stderr mentions
     conflict files.
   - `test_integrate_clean_returns_ok` — engine returns clean; exit
     0; stdout reports outcome.
   - `tests/test_config.py` (or new file): `test_test_command_field
     _loaded_from_orchestrator_json` — write
     `{"test_command": "make test"}` to a tmp `.orchestrator.json`;
     `load_project_config` returns `cfg.test_command == "make test"`.
   - `test_test_command_default_none_when_absent`.

2. **Implementation.**
   - `end_of_line/config.py` `ProjectConfig`:
     ```python
     @dataclass
     class ProjectConfig:
         ...
         test_command: str | None = None
     ```
     In `load_project_config`: pull `raw.get("test_command")` and
     pass through.
   - `end_of_line/cli.py` argparse:
     ```python
     p_integrate = sub.add_parser(
         "integrate",
         help="Dry-merge a batch's branches in a scratch worktree "
              "and optionally run the project's test_command. "
              "Operator-on-demand replay; does NOT mutate plan "
              "state or file follow-ups (the cross-plan rule "
              "owns that).",
     )
     p_integrate.add_argument("--project", type=Path, required=True)
     p_integrate.add_argument("--batch")
     p_integrate.add_argument("--branches",
         help="Comma-separated branches; overrides --batch")
     p_integrate.add_argument("--no-suite", action="store_true")
     p_integrate.add_argument("--base-ref", default="main")
     ```
   - Wire in dispatch:
     ```python
     if args.cmd == "integrate":
         return cmd_integrate(args)
     ```
   - `cmd_integrate(args) -> int`:
     - Resolve project, validate `--batch` slug if set.
     - If `--branches`: split on comma, strip whitespace.
     - Else: load plans from registry, filter to
       `status==DONE AND batch_id==args.batch AND
       get_worktree(state) is not None`, extract branch names.
       Empty/single → `_die(GENERIC, "...")`.
     - `test_cmd = None if args.no_suite else cfg.test_command`.
     - Call `dry_merge.attempt_merge(...)`.
     - Print structured outcome to stdout (outcome,
       conflict_files, exit_code, stderr_tail summary).
     - Return `ExitCode.OK` if clean, `ExitCode.GENERIC` otherwise.
   - **`docs/contract.md`**:
     - Plan state schema: add `batch_id: str | None` and
       `gate_result: { sha_key, ts, batch_id, outcome,
       follow_up_plan? } | null`.
     - Queue entry schema: add `batch_id: str | None`.
     - `.orchestrator.json` schema: add `test_command: str | null`.
   - **`docs/architecture.md`**:
     - New subsection "Multi-plan batch integration gate" under the
       cross-plan rule chain. Cover: rule trigger condition,
       eligibility filter, idempotency key, granularity, operator
       approval boundary (rule writes plan files but does NOT
       queue them).
   - **`docs/operations.md`**:
     - CLI reference: `clu integrate` entry with flags + exit codes.
     - "Multi-plan batches" operator workflow section:
       1. `clu init` per plan.
       2. `clu queue add --batch <name> <slug-1> <slug-2> ...`.
       3. Workers drain to DONE on their own branches.
       4. Gate fires automatically on plan_done; clean → notify;
          dirty → follow-up plan written, operator runs `clu queue
          add` on it to repair.
       5. After all green: operator merges each branch to main and
          archives.
     - `.orchestrator.json:test_command` reference: example value
       (`"python3 -m unittest discover -s tests"`) + shell-execution
       caveat (runs inside scratch worktree, no env isolation).

3. **Acceptance.**
   - All ~8 new tests green.
   - `python3 -m unittest discover -s tests` zero regressions.
   - `clu integrate --help` shows all flags.
   - `python3 -m end_of_line.cli integrate --project /tmp/nope` →
     non-zero exit with sensible error (project not found).
   - Doc sections referenced from existing index nav (e.g.
     `docs/_outline.md` linkage if applicable).
   - Issue #50 referenced as `closes #50` in the commit body.

4. **Commit + complete.**
   - `dry-merge-gate: phase cli-docs — clu integrate + test_command
     + docs (closes #50)`
   - Stage: `end_of_line/cli.py`, `end_of_line/config.py`,
     `docs/contract.md`, `docs/architecture.md`,
     `docs/operations.md`, `tests/test_cmd_integrate.py`,
     `tests/test_config.py` (if touched).
   - `clu complete --plan dry-merge-gate --phase cli-docs
     --token <T>`.

## Failure modes to watch

- **`closes #50` placement.** Use it in the COMMIT BODY (not title)
  per the project's structured commit convention. GitHub still
  parses it from anywhere in the message.
- **`--batch` vs `--branches` UX.** Both default-None; require
  exactly one. Test `test_integrate_requires_batch_or_branches`
  covers the negative path. Don't silently fall back if neither
  given — operators need a clear error.
- **`test_command` runtime trust.** `shell=True` in
  `attempt_merge` runs whatever the operator put in
  `.orchestrator.json`. Mirror the trust model of
  `dispatch.command` (already shell-executed). Document this in
  operations.md alongside the example.
- **Docs drift.** `docs/_outline.md` is the structural contract for
  the docs library. Verify additions fit the existing ownership map;
  if `architecture.md` already has a "Cross-plan rules" section,
  EXTEND it instead of creating a sibling.
- **Branch presence at integrate time.** `--branches a,b` is given
  by operator; engine will fail loudly if a branch doesn't exist.
  That's fine — no need for pre-check. But for `--batch` resolution
  path, drop plans whose worktree branch can't be `git rev-parse`-d
  (consistent with the rule's behavior).
