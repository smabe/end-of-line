# clu-watch-task-list-projector — `stream_loop` task_list_mode wiring

You are phase `projector` of `clu-watch-task-list`. Wire phase
`protocol`'s `project_event_task` and phase `bootstrap`'s
`bootstrap_task_list` into the existing `stream_loop`. Add the
`task_list_mode` kwarg, route accordingly, call bootstrap before the
first poll tick.

## Locked decisions (do NOT re-litigate)

See `plans/clu-watch-task-list.md` § Phase 3. Summary:
- New `stream_loop` kwarg `task_list_mode: bool = False`.
- When True: project events via `project_event_task` not
  `project_event`; call `bootstrap_task_list` before first tick.
- Snapshot baseline still emitted in task-list mode (for operator
  context; Claude's skill instructions tell it to ignore non-TASK_
  lines).
- `json_mode` and `task_list_mode` mutually exclusive at call site
  (CLI gates this; stream_loop just routes).

## Read first

- `end_of_line/watch.py:193-251` — current `stream_loop` body. The
  `_before_first_tick` test seam is the natural insertion point.
- `end_of_line/watch.py:project_event_task` (shipped phase
  `protocol`) and `bootstrap_task_list` (shipped phase `bootstrap`)
  — public surface this phase calls.
- `tests/test_watch_stream.py` — existing fixture state-file pattern;
  mirror for new tests.

## Produce

1. **Failing tests first**
   (`tests/test_watch_task_stream.py`, new):
   - `test_task_list_mode_emits_bootstrap_before_baseline` —
     `stream_loop(..., task_list_mode=True, max_ticks=0)` with a
     fixture master plan having 2 phases → sink has 3 TASK_CREATE
     lines (parent + 2) BEFORE the `[snapshot]` baseline line.
   - `test_task_list_mode_projects_events_as_task_update` —
     append `EVENT_PHASE_COMPLETED` to fixture state, tick → sink
     line matches `TASK_UPDATE task=<slug>/<phase> status=completed
     msg="completed"`.
   - `test_task_list_mode_skips_default_text_lines` — append
     `EVENT_TASK_SPAWNED` (not in task mapping) → no line emitted.
   - `test_task_list_mode_with_verbose_emits_lease_extended` —
     append `EVENT_LEASE_EXTENDED`, `verbose=True, task_list_mode=True`
     → emits TASK_UPDATE line.
   - `test_task_list_mode_off_unchanged` — `task_list_mode=False`
     (default) → behavior matches existing tests (regression guard).
   - `test_bootstrap_missing_master_raises_passthrough` — fixture
     state path exists but master `.md` is missing → `stream_loop`
     propagates `FileNotFoundError` (caller handles). Asserted via
     `with self.assertRaises(FileNotFoundError):`.

2. **Implementation.** In `end_of_line/watch.py`:
   - Extend `stream_loop` signature:
     ```python
     def stream_loop(
         state_paths: list[Path],
         *,
         json_mode: bool = False,
         task_list_mode: bool = False,
         verbose: bool = False,
         sink: TextIO | None = None,
         poll_interval: float = 1.0,
         max_ticks: int | None = None,
         _before_first_tick: Callable[[], None] | None = None,
         cfg_loader: Callable[[Path], "ProjectConfig"] | None = None,
     ) -> int:
     ```
   - Right after cursor seed, before the existing baseline emission,
     add (when `task_list_mode`):
     ```python
     if task_list_mode:
         if cfg_loader is None:
             from .cli import load_project_config  # lazy to avoid cycle
             cfg_loader = lambda sp: load_project_config(_state_path_to_project(sp))
         bootstrap_task_list(state_paths, cfg_loader, sink)
     ```
     The `_state_path_to_project` helper walks up from
     `<project>/plans/.orchestrator/<slug>.state.json` to
     `<project>` — small inline helper, define it in `watch.py`.
   - Then keep the existing snapshot baseline loop (still emit
     `[snapshot]` lines for operator context).
   - Inside the per-tick event projection, route based on mode:
     ```python
     for evt in events[cursors[path]:]:
         if task_list_mode:
             line = project_event_task(evt, slug, verbose=verbose)
         else:
             line = project_event(evt, slug, verbose=verbose)
         if line is None:
             continue
         if json_mode:
             print(json.dumps({"ts": evt.get("ts"), "slug": slug,
                               "event": evt}),
                   file=sink, flush=True)
         else:
             print(line, file=sink, flush=True)
     ```
     Note: `json_mode` and `task_list_mode` are guaranteed mutex by
     CLI; here we treat them as orthogonal (json wins if both somehow
     true, but tests assert exclusion at the CLI layer).

3. **Acceptance.**
   - 6 new tests green.
   - Phases `protocol` + `bootstrap` tests still green.
   - Existing `tests/test_watch_stream.py` tests still green
     (regression guard — `task_list_mode=False` default preserves
     behavior).
   - Full suite green.

4. **Commit + complete.**
   - Title: `clu-watch-task-list: phase projector — stream_loop task_list_mode wiring`
   - Stage: `end_of_line/watch.py`,
     `tests/test_watch_task_stream.py`.
   - `clu complete --plan clu-watch-task-list --phase projector --token <T>`

## Failure modes to watch

- **Lazy import to avoid cycle** — `cli.py` imports `watch`, so
  `watch.py` can't unconditionally `from .cli import
  load_project_config`. Lazy-import inside the function body or
  accept the loader from outside (preferred — already in the
  signature).
- **`_state_path_to_project` helper** — state files live at
  `<project>/plans/.orchestrator/<slug>.state.json`. Walk up 3
  parents. Document in a one-line comment; don't over-engineer.
- **Baseline ordering** — TASK_CREATE bootstrap MUST come BEFORE
  the `[snapshot]` lines, so Claude sees the task tree before any
  noise. Test asserts this order explicitly.
- **`cfg_loader` injection in tests** — tests should pass a fake
  loader returning a fake ProjectConfig with `.project_root` and
  `.plan_dir`. Don't require the real registry.
