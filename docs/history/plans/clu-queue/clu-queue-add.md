# clu-queue-add — `cmd_queue_add` with bootstrap + slug + dedup checks

You are phase `add` of the `clu-queue` plan. Phase `primitive` has
shipped: `end_of_line/queue.py` exists with `load`/`save_atomic`/
`mutate` + `SCHEMA_VERSION=1`, and `ProjectConfig.queue_path()`
returns the per-project queue file path. Your job: add the operator
CLI subcommand `clu queue add` end to end with all six exit paths
covered by tests.

Design pass is done. Full context in
`.claude/plans/plan-queue-master.md` and `plans/clu-queue.md`. Do not
redesign.

## Locked decisions (do NOT re-litigate)

- **CLI shape**: `clu queue add <slug> [--front] [--project PATH]`.
  No `--token` (v1 operator-only; worker mode is v2 / issue #17).
- **Slug validation**: `state.validate_slug(slug, kind="plan slug")`
  before any path join. Per CLAUDE.md.
- **Bootstrap check**: if no `registry.entries()` row has
  `project_root == cfg.project_root` (after `Path.resolve()` on both
  sides), `_die(ExitCode.GENERIC, "...")` with a bootstrap-instruction
  message. Match `registry.register`'s path resolution
  (registry.py:68).
- **Plan file existence check**: `(cfg.plan_dir / f"{slug}.md").exists()`.
  If not: `_die(ExitCode.UNKNOWN_TASK, "no plan file at <path>")`.
- **Duplicate against pending queue**: if slug already in
  `data["queue"]`, `_die(ExitCode.STATUS_TRANSITION, "already queued
  at position N; remove first to re-order")`. Slug present only in
  `history` is OK (new chain).
- **`--front` insertion**: `data["queue"].insert(0, entry)`. Default
  is append.
- **Entry shape**:
  ```python
  {
      "slug": slug,
      "added_at": st.utcnow_iso(),  # match existing convention
      "added_by": "operator",        # v1 hardcoded
      "position_at_add": "front" if args.front else "tail",
  }
  ```
- **No `--notes` / `--reason` / `--token` flags in v1.** Cut per
  master plan.
- **Exit OK = 0.** Print one line: `queued at position N`.

## Read first

- `end_of_line/queue.py` (built in phase primitive). Confirm `mutate`
  signature, `_empty` shape.
- `end_of_line/cli.py` argparse setup (around lines 100-200) for how
  existing subcommands are registered. `cmd_init` and `cmd_register`
  are the closest analogues — they take `--project` and validate
  slugs.
- `end_of_line/cli.py` `_die` helper. The canonical call shape is
  `_die(ExitCode.X, msg)`. Find one existing usage for the pattern.
- `end_of_line/state.py` — `validate_slug` regex + how it's invoked.
- `end_of_line/registry.py` — `entries()` and `register()`. The
  bootstrap check uses `entries()` to confirm the project is known.
- `end_of_line/config.py` — `load_project_config(Path)` and
  `ProjectConfig.plan_dir` for the existence check.
- `CLAUDE.md` — "ExitCode IntEnum, never bare ints" and "state.validate_slug
  on every external slug before any path join."

## Produce

1. **TDD: failing tests first.** Add `tests/test_queue_add.py`:

   - `test_add_success_appends_to_tail` — fresh project with one
     registered plan; `clu queue add new-plan` succeeds; queue file
     has new-plan at position 0 (only entry); print contains "position 1".
   - `test_add_front_inserts_at_position_0` — pre-populate queue
     with [a, b]; `clu queue add c --front` results in [c, a, b].
   - `test_add_appends_when_queue_nonempty` — queue has [a]; `clu
     queue add b` results in [a, b].
   - `test_add_rejects_invalid_slug` — `clu queue add 'Bad Slug!'`
     exits `INVALID_SLUG` (2); queue file unchanged.
   - `test_add_rejects_unknown_project` — project has zero registered
     plans; `clu queue add foo` exits `GENERIC` (1) with a message
     that includes the suggested `clu init` command; queue file
     not created.
   - `test_add_rejects_missing_plan_file` — project bootstrapped (one
     registered plan); `clu queue add nonexistent` (no
     `plans/nonexistent.md`) exits `UNKNOWN_TASK` (6); queue file
     unchanged.
   - `test_add_rejects_duplicate_pending` — queue has [foo]; `clu
     queue add foo` exits `STATUS_TRANSITION` (7) with a message
     including the current position; queue file unchanged.
   - `test_add_allows_re_add_of_history_only_slug` — queue is empty
     but history contains a `removed` entry for foo; `clu queue add
     foo` succeeds (foo is in pending again, history unchanged).
   - `test_add_idempotency_on_currently_running_slug` — foo was
     popped + dispatched and is running (registered, has state.json,
     status RUNNING, gone from queue.queue); `clu queue add foo`
     succeeds (re-enqueue is allowed). Position should be 1 (or
     wherever tail lands).
   - `test_add_entry_shape` — verify the entry dict has slug,
     added_at, added_by="operator", position_at_add fields.
   - `test_add_uses_resolved_path_for_bootstrap` — project_root is
     a symlink; `clu queue add` works (bootstrap check resolves both
     sides).

   Use `isolate_registry` and `isolate_queue` (from phase primitive)
   in setUp. Run the suite — all 11 new tests must FAIL.

2. **Argparse: register `queue add` subcommand.** In `cli.py`'s
   argparse setup, add a `queue` subparser group with an `add`
   subcommand:
   ```python
   p_queue = subparsers.add_parser("queue", help="Manage the project's plan queue.")
   queue_subs = p_queue.add_subparsers(dest="queue_cmd")
   p_queue_add = queue_subs.add_parser("add", help="Append a plan slug to the queue.")
   p_queue_add.add_argument("slug")
   p_queue_add.add_argument("--front", action="store_true", help="Insert at head instead of tail.")
   p_queue_add.add_argument("--project", default=Path.cwd(), type=Path)
   ```
   (Phases 3 and beyond will add list/remove under the same `queue`
   group. Make sure the subparser group structure supports this.)

3. **Implement `cmd_queue_add(args)`:**

   ```python
   def cmd_queue_add(args) -> int:
       slug = args.slug
       st.validate_slug(slug, kind="plan slug")  # raises -> exit INVALID_SLUG via decorator

       cfg = load_project_config(args.project)
       project_root_resolved = cfg.project_root.resolve()

       # Bootstrap check
       registered_roots = {Path(e.project_root).resolve() for e in registry.entries()}
       if project_root_resolved not in registered_roots:
           return _die(
               ExitCode.GENERIC,
               f"project {project_root_resolved} has no registered plans; "
               f"run `clu init --project {project_root_resolved} --plan <slug>` first",
           )

       # Plan file existence
       plan_file = cfg.plan_dir / f"{slug}.md"
       if not plan_file.exists():
           return _die(ExitCode.UNKNOWN_TASK, f"no plan file at {plan_file}")

       # Mutate queue with duplicate check
       with queue.mutate(cfg.queue_path()) as data:
           existing_positions = [
               (i, e) for i, e in enumerate(data["queue"]) if e["slug"] == slug
           ]
           if existing_positions:
               idx, _ = existing_positions[0]
               return _die(
                   ExitCode.STATUS_TRANSITION,
                   f"already queued at position {idx + 1}; "
                   f"`clu queue remove {slug}` first to re-order",
               )

           entry = {
               "slug": slug,
               "added_at": st.utcnow_iso(),
               "added_by": "operator",
               "position_at_add": "front" if args.front else "tail",
           }
           if args.front:
               data["queue"].insert(0, entry)
               position = 1
           else:
               data["queue"].append(entry)
               position = len(data["queue"])

       print(f"queued at position {position}")
       return ExitCode.OK
   ```

   Adjust to match the codebase's actual idioms (`st.utcnow_iso` may
   be named differently — check state.py; `validate_slug`'s
   exception → exit translation may go through a decorator pattern
   already used by other commands).

4. **Wire `cmd_queue_add` into the dispatch table** at the top of
   `cli.py`'s main dispatch (same pattern as `cmd_init`,
   `cmd_register`, etc.). Make sure the `args.queue_cmd == "add"`
   branch routes to `cmd_queue_add`.

5. **Run the full suite.** All 11 new tests pass. All existing tests
   pass unchanged. Count grows by ~11.

6. **`/simplify`.** This phase touches cli.py + tests. Per CLAUDE.md
   ("/simplify after non-trivial work"), run it.

7. **Commit.** Structured message:
   - Title: `clu-queue phase add: cmd_queue_add with bootstrap + dedup`
   - Why: operator needs to enqueue plans for cron to drain
     overnight; this is the entry point. Bootstrap + duplicate
     checks prevent silent overnight failures.
   - What's new: `clu queue add <slug> [--front]` subcommand;
     argparse `queue` subparser group registered.
   - Under the hood: slug + bootstrap + plan-file + duplicate
     validation; queue.mutate writes atomic; entries carry
     added_at/added_by/position_at_add.
   - Tests: 11 new tests covering 6 exit paths + `--front` + history
     re-add + running-slug re-add + symlink resolution.
   - Co-Authored-By trailer.

8. **`clu complete --token <token>` with SHA + count summary.**

## Failure modes to watch for

- **Bootstrap check by string equality.** Project paths can be
  symlinked, have trailing slashes, etc. Use `Path.resolve()` on
  BOTH the queried path AND the registry rows. registry.py:68 does
  this on register; mirror it.
- **`_die` vs raised exception.** `validate_slug` raises; some
  decorators in the codebase translate raises into `_die`. Check
  the existing pattern for `cmd_register` or `cmd_init` and match it.
  If there's no decorator, the slug validation may need a try/except
  wrapper to emit the right exit code.
- **Subparser nesting.** Adding `queue` as a subparser group with
  its own subcommands is a small argparse-fu. Test that `clu queue`
  (with no subcommand) doesn't crash — it falls through to phase 3
  later (defaults to list). For now, an explicit help message or a
  fall-through to print the queue subcommand help is fine.
- **`args.project` default.** `Path.cwd()` at argparse-parse time
  is fine. Mirror how `cmd_init` handles `--project` default.
- **`queue.mutate` blocking on a contended lock.** Should be
  microseconds; if a test deadlocks, check that the test isolates
  the queue path properly (each test gets its own tmp project root).
- **Duplicate-check window vs `--front`.** A simultaneous `add foo`
  and `add foo --front` from two terminals: serialized by the
  flock; the second one sees the first's write and exits with
  STATUS_TRANSITION. No race.

## Done criteria for this phase

- `clu queue add <slug>` works end-to-end with all six documented
  exit codes (OK, INVALID_SLUG, GENERIC=bootstrap, UNKNOWN_TASK,
  STATUS_TRANSITION, and the re-add allowed cases).
- `--front` inserts at position 0.
- Entry schema includes slug, added_at, added_by="operator",
  position_at_add.
- Symlinked project roots accepted (path resolution match registry).
- 11 new tests pass; full suite green.
- One commit, structured message, no `Fixes` trailer (this plan
  doesn't close an open issue; #17 stays open for v2).
- `clu complete` with token + SHA + count summary.
