# unregister-archived-impl — `--all-archived` + `--dry-run` for batch ghost cleanup

You are the only phase of the `unregister-archived` plan. Closes
[#12](https://github.com/smabe/end-of-line/issues/12).

Read the master plan first for the locked design and recommended
argparse approach. Do exactly what's below; don't redesign.

## Locked decisions (do NOT re-litigate)

- "Archived" = master plan file at
  `<project_root>/<plan_dir>/<plan_slug>.md` doesn't exist.
- Subparser approach (a) from master: stop using `add_common` for
  `unregister`; declare `--project` / `--plan` / `--all-archived` /
  `--dry-run` directly. Validate combinations in `cmd_unregister`.
- Mutex: `--all-archived` ↔ `--plan` mutually exclusive. Without
  `--all-archived`, `--plan` and `--project` are required (preserves
  the existing per-plan flow).
- `--all-archived` + missing `.orchestrator.json` → skip + report,
  don't crash, don't auto-unregister (operator decides).
- All removals atomic under one `registry._mutate` window.
- Out of scope: option 2 (auto-prune on tick). Don't touch
  `supervisor.py`.

## Read first

- `end_of_line/cli.py:82-87` — `add_common` (you'll bypass it for
  `unregister`).
- `end_of_line/cli.py:110-113` — current `unregister` subparser
  declaration.
- `end_of_line/cli.py:367` — dispatch table mapping `"unregister"` →
  `cmd_unregister`. **Important**: the existing dispatcher passes
  `(args, cfg, state_path)` because `unregister` was a per-plan
  command. With `--all-archived` there's no single `cfg`. You'll
  need to either: (i) route `unregister` through a different
  dispatcher branch when `args.all_archived` is set, or (ii) move
  `cmd_unregister` to take just `args` and build `cfg` inside when
  needed. Pick (i) if it keeps the rest of the dispatcher clean.
- `end_of_line/cli.py:400-404` — current `cmd_unregister` body.
- `end_of_line/registry.py:53-110` — `entries`, `unregister`,
  `_mutate`, `load_entry_state` (mirror its tolerance pattern).
- `end_of_line/config.py` — `load_project_config`. Need the
  `plan_dir` field to resolve the master path.
- `tests/test_registry.py` — existing test patterns. Use
  `tests.isolate_registry(self, tmp_path)` per CLAUDE.md mandate.

## Produce

1. **TDD: failing tests first.** New file
   `tests/test_unregister_archived.py` (or extend
   `tests/test_registry.py` if you prefer; new file is cleaner).

   Required cases:
   - `test_all_archived_removes_entries_with_missing_master_files` —
     register two plans, create `plans/foo.md` for one, leave the
     other's master absent. `clu unregister --all-archived` removes
     only the one without a master file. Registry entries reflect
     that.
   - `test_all_archived_keeps_entries_with_present_master_files` —
     all masters present → `Unregistered 0 plans`. Registry unchanged.
   - `test_all_archived_dry_run_does_not_mutate` — same setup as
     case 1 but with `--dry-run`. Output lists what would be
     removed; registry unchanged.
   - `test_all_archived_handles_missing_orchestrator_json` — register
     a plan whose project_root has no `.orchestrator.json`. The entry
     is reported as "skipped (config unreadable)" or similar; NOT
     auto-unregistered.
   - `test_all_archived_handles_missing_project_dir` — register a
     plan whose `project_root` directory was deleted. Treat as
     archived; unregister.
   - `test_all_archived_with_plan_arg_rejected` — combining
     `--all-archived --plan foo` exits `ExitCode.GENERIC` with the
     mutex error.
   - `test_unregister_per_plan_still_works` — without
     `--all-archived`, the existing `--project P --plan S` flow is
     unchanged.
   - `test_all_archived_empty_registry_is_ok` — empty registry,
     `--all-archived` → exit OK, prints "(nothing to unregister)" or
     "Unregistered 0 plans".

   Use `isolate_registry` in setUp. Run suite — all new tests must
   FAIL (or error on missing flag).

2. **Reshape the `unregister` subparser** in `cli.py:110-113`:

   ```python
   p_unregister = sub.add_parser(
       "unregister",
       help="Remove plan(s) from the host registry",
   )
   p_unregister.add_argument("--project", type=Path, default=None,
                              help="Project root (required without --all-archived)")
   p_unregister.add_argument("--plan", default=None,
                              help="Plan slug (required without --all-archived)")
   p_unregister.add_argument("--all-archived", action="store_true",
                              help="Remove every registry entry whose master "
                                   "plan file no longer exists.")
   p_unregister.add_argument("--dry-run", action="store_true",
                              help="With --all-archived: print what would be "
                                   "removed without mutating the registry.")
   ```

3. **Dispatcher**: in the dispatch table at `cli.py:367`, route
   `unregister` to a small wrapper that branches on
   `args.all_archived`:

   ```python
   def cmd_unregister_dispatch(args) -> int:
       if args.all_archived:
           return cmd_unregister_all_archived(args)
       return cmd_unregister_one(args)  # the existing per-plan flow
   ```

   Or fold the branch into a single `cmd_unregister(args)` —
   whichever keeps the dispatch table consistent with how `tick` /
   `tick-all` is structured.

4. **`cmd_unregister_one`** — keep the current behavior. Validate
   `--project` and `--plan` are present (since they're now optional
   at parse time); error with `ExitCode.GENERIC` and a clear message
   if missing.

5. **`cmd_unregister_all_archived`**:

   ```python
   def cmd_unregister_all_archived(args) -> int:
       if args.plan is not None:
           return _die(ExitCode.GENERIC,
                       "--all-archived is mutually exclusive with --plan")

       to_remove = []   # [(project_root, plan_slug)]
       skipped = []     # [(project_root, plan_slug, reason)]
       for entry in registry.entries():
           proj = Path(entry.project_root)
           try:
               cfg = load_project_config(proj)
           except (FileNotFoundError, OSError, ValueError) as exc:
               # Project dir missing → archived. Config unreadable but
               # dir present → skip with explanation.
               if not proj.exists():
                   to_remove.append((entry.project_root, entry.plan_slug))
               else:
                   skipped.append((entry.project_root, entry.plan_slug, str(exc)))
               continue
           master_path = cfg.project_root / cfg.plan_dir / f"{entry.plan_slug}.md"
           if not master_path.exists():
               to_remove.append((entry.project_root, entry.plan_slug))

       if args.dry_run:
           if not to_remove:
               print("(nothing to unregister)")
           else:
               print("Would unregister:")
               for proj, slug in to_remove:
                   print(f"  {proj}  ->  {slug}")
           for proj, slug, reason in skipped:
               print(f"  skipped: {proj}  ->  {slug}  ({reason})")
           return ExitCode.OK

       if not to_remove:
           print("(nothing to unregister)")
       else:
           # Atomic batch removal under one _mutate window.
           with registry._mutate(registry.registry_path()) as data:
               keep_keys = {(p, s) for p, s in
                            [(row["project_root"], row["plan_slug"])
                             for row in data["plans"]]}
               for proj, slug in to_remove:
                   keep_keys.discard((proj, slug))
               data["plans"] = [
                   row for row in data["plans"]
                   if (row["project_root"], row["plan_slug"]) in keep_keys
               ]
           print(f"Unregistered {len(to_remove)} plans:")
           for proj, slug in to_remove:
               print(f"  {proj}  ->  {slug}")

       for proj, slug, reason in skipped:
           print(f"  skipped: {proj}  ->  {slug}  ({reason})")
       return ExitCode.OK
   ```

   Note: `registry._mutate` is the underscore-prefixed primitive.
   Using it from `cli.py` is fine (mirrors how other commands use
   `state.mutate`). Don't re-implement the lock/load/write window.

6. **Run the suite — all green.**

7. **`docs/operations.md`** — find the post-ship workflow section
   (or any section mentioning `clu unregister`). Add a one-liner:
   "After archiving plans, run `clu unregister --all-archived` to
   prune ghost registry entries. Use `--dry-run` to preview."

8. **`/simplify`** — diff is multi-file (~40 LOC + tests + docs). Run
   it. Address findings, re-test.

9. **Commit.** Title: `unregister: add --all-archived for batch ghost
   cleanup`. Body references `closes #12`.

## Verification before `clu complete`

Per CLAUDE.md mandate #9: re-run `python3 -m unittest discover -s tests`
right before `clu complete`. Report final test count + any failures
in the completion summary.

## Acceptance

- [ ] `clu unregister --all-archived` removes entries whose master
      file no longer exists
- [ ] `--dry-run` prints what would be removed without mutating
- [ ] `--all-archived --plan X` exits with mutex error
- [ ] Per-plan `clu unregister --project P --plan S` still works
- [ ] Empty registry case exits OK with friendly message
- [ ] Missing-project-dir entries treated as archived
- [ ] Unreadable-config entries reported as skipped (not auto-removed)
- [ ] Tests cover all eight cases listed in the produce step
- [ ] `docs/operations.md` mentions the new flag
- [ ] One commit with `closes #12` in body
