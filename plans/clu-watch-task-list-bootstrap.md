# clu-watch-task-list-bootstrap — TASK_CREATE per phase on startup

You are phase `bootstrap` of `clu-watch-task-list`. Add the startup
emission step: for each watched plan, read its master plan via
`parse_sessions_index`, emit one TASK_CREATE per phase id, plus one
TASK_CREATE for the parent task. Pure I/O helper; phase `projector`
wires it into `stream_loop`.

## Locked decisions (do NOT re-litigate)

See `plans/clu-watch-task-list.md` § Phase 2. Summary:
- Master plan path: `cfg.project_root / cfg.plan_dir / f"{slug}.md"`.
- Parent task emitted first (`TASK_CREATE task=<slug> status=pending`),
  then one per phase in Sessions-index order.
- Missing master file → `UNKNOWN_TASK` (6).
- Empty Sessions index (single-phase plan) → emit parent only, no
  error.
- New helper: `bootstrap_task_list(state_paths, cfg_loader, sink)`.
  Takes a callable `cfg_loader(state_path) -> ProjectConfig` for test
  injection. Returns nothing; emits via sink.

## Read first

- `end_of_line/plan_parser.py:1-90` — `parse_sessions_index(plan_path)
  -> list[Phase]` shape. Phase dataclass: `.id`, `.plan_file`,
  `.scope`, `.effort`.
- `end_of_line/config.py` — `ProjectConfig.project_root`,
  `.plan_dir`, `.state_path(slug)`. Find the loader entry point.
- `end_of_line/watch.py:183-220` — `_slug_for_path` (state-path →
  slug helper) and `stream_loop`'s cursor-seed pattern.
- `end_of_line/cli.py:2867-2911` — `cmd_watch` — how state paths
  resolve from registry; the cfg loader is `load_project_config`.

## Produce

1. **Failing tests first** (`tests/test_watch_task_bootstrap.py`,
   new):
   - `test_bootstrap_emits_parent_then_phases_in_order` — master
     plan with 3 phases (a/b/c) → 4 TASK_CREATE lines: parent,
     a, b, c.
   - `test_bootstrap_missing_master_file_errors` — state path
     exists but `<slug>.md` doesn't → raises `BootstrapError`
     (new exception class) or returns sentinel `_die`-friendly
     tuple; assert error message names the missing path. Decide
     shape during implementation; tests use the actual API.
   - `test_bootstrap_single_phase_master_emits_parent_only` —
     master plan with no Sessions index (returns `[]`) → one
     TASK_CREATE for parent only, no error.
   - `test_bootstrap_multiple_plans_each_get_their_own_tree` —
     two state paths → each emits parent + phases. Order
     deterministic (whatever input order).
   - `test_bootstrap_state_path_pointing_at_missing_state_skips` —
     state path doesn't exist on disk → skip silently (mirror
     stream_loop's pattern). No exception.

2. **Implementation.** In `end_of_line/watch.py`:

   ```python
   from .plan_parser import parse_sessions_index


   def bootstrap_task_list(
       state_paths: list[Path],
       cfg_loader: Callable[[Path], "ProjectConfig"],
       sink: TextIO,
   ) -> None:
       """Emit TASK_CREATE lines per plan + per phase, in order.

       For each state path, looks up the corresponding master plan
       via cfg_loader, parses its Sessions index, and emits:
         TASK_CREATE task=<slug> status=pending
         TASK_CREATE task=<slug>/<phase-id> status=pending  (per row)

       Skips state paths that don't exist (mirrors stream_loop's
       silent-drop policy). Raises FileNotFoundError if the master
       plan file is missing for an otherwise-valid state path.
       """
       for state_path in state_paths:
           if not state_path.exists():
               continue
           slug = _slug_for_path(state_path)
           cfg = cfg_loader(state_path)
           plan_path = cfg.project_root / cfg.plan_dir / f"{slug}.md"
           if not plan_path.exists():
               raise FileNotFoundError(
                   f"no master plan at {plan_path}"
               )
           print(f"TASK_CREATE task={slug} status=pending",
                 file=sink, flush=True)
           for phase in parse_sessions_index(plan_path):
               print(f"TASK_CREATE task={slug}/{phase.id} status=pending",
                     file=sink, flush=True)
   ```

   Decision on missing-master error shape: raise `FileNotFoundError`
   from the helper, let the caller (phase `projector` → `cmd_watch`
   via `cli`) catch and `_die(UNKNOWN_TASK, ...)`. Keeps the helper
   pure and CLI exit-code concerns at the CLI layer.

3. **Acceptance.**
   - 5 new tests green.
   - Phase `protocol` tests still green.
   - Full suite green.

4. **Commit + complete.**
   - Title: `clu-watch-task-list: phase bootstrap — parse_sessions_index integration + TASK_CREATE emission`
   - Stage: `end_of_line/watch.py`,
     `tests/test_watch_task_bootstrap.py`.
   - `clu complete --plan clu-watch-task-list --phase bootstrap --token <T>`

## Failure modes to watch

- **cfg_loader signature** — `load_project_config` in cli.py takes a
  `Path` (project root). The helper's `cfg_loader` should match that
  signature contract; tests pass a fake loader returning a fake
  ProjectConfig. Verify the real wiring (next phase) lambda-wraps
  appropriately.
- **plan_dir default** — `ProjectConfig.plan_dir` is "plans" by default
  per the schema. Verify by reading `config.py`. Don't hardcode.
- **Phase id slug regex** — `parse_sessions_index` already validates
  ids match `validate_slug` regex (per its contract). Don't re-validate
  here. Trust the parser.
- **Empty plan slug** — shouldn't happen (registry entries have valid
  slugs), but defensive check is one line: skip with no output if slug
  is empty.
