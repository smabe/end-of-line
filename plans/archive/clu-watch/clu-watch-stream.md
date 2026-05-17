# clu-watch-stream — polling loop + cursor + snapshot baseline

You are phase `stream` of `clu-watch`. Build the streaming I/O layer
on top of phase 1's pure projector. NO CLI yet — just a function
`stream_loop(state_paths, *, json_mode, verbose, sink, poll_interval)`
that loops, polls state files, advances cursors, projects new events
to the sink. Phase `cli` wires this to `clu watch`.

## Locked decisions (do NOT re-litigate)

See `plans/clu-watch.md` § Phase 2. Summary:
- Per-plan cursor `last_seen_event_index: dict[Path, int]`.
- Snapshot baseline on first iteration (one line per plan).
- Default poll interval 1s for explicit plans; 5s for `--all`
  (set by the caller via `poll_interval` arg).
- SIGINT → final newline + `ExitCode.OK`.
- Missing state file mid-watch → drop silently.

## Read first

- `plans/clu-watch.md` § Phase 2 + § Phase 1 (the contract for the
  projector this phase calls).
- `end_of_line/watch.py` (just shipped in phase `events`) — read
  `project_event` signature.
- `end_of_line/state.py:303` (`claim_phase`) and the events array
  shape via `data["events"]`.
- `end_of_line/cli.py:_follow_log` (grep for it) — existing
  `tail -f`-style polling pattern. Mirror its SIGINT handling.
- `end_of_line/registry.py` — for `--all` mode the caller resolves
  state paths via registry; this phase takes the resolved list
  as input.
- `tests/test_logs.py` — `tail -f`-style test pattern.

## Produce

1. **Failing tests first** (`tests/test_watch_stream.py`, new):
   - `test_snapshot_baseline_emitted_on_start` — seed a state.json
     with status=running + a `current_claim`; run `stream_loop`
     for one tick (mock `time.sleep` or pass a finite iteration
     count). Assert sink output begins with a snapshot line like
     `[snapshot] my-plan: running, active=foundation`.
   - `test_new_event_emitted_after_baseline` — start `stream_loop`,
     append an `EVENT_PHASE_COMPLETED` to state.json's events array,
     run one more tick, assert sink has the projected line.
   - `test_cursor_advances_no_duplicate_emit` — append 1 event,
     tick, append nothing, tick again — sink has exactly 1 line
     post-baseline.
   - `test_multiple_events_in_one_tick_all_emit` — append 3 events
     between ticks, assert sink gets 3 lines in order.
   - `test_verbose_only_event_filtered_default` — append a
     `EVENT_LEASE_EXTENDED`, tick, assert sink has no line for it.
   - `test_verbose_flag_passes_through` — same event, `verbose=True`
     in call, assert line present.
   - `test_json_mode_emits_json_per_line` — `json_mode=True`,
     append `EVENT_PHASE_COMPLETED`, assert each line is
     `json.loads`-able with `slug`, `ts`, `event` keys.
   - `test_missing_state_file_dropped_silently` — pass a path that
     doesn't exist; tick; assert no exception and no line, cursor
     map drops the path.
   - `test_state_file_deleted_mid_watch` — start with valid file,
     delete it, tick — no exception; path removed from cursors.
   - `test_two_plans_interleaved` — two state files, append events
     to both, single tick — both projected, lines tagged with the
     right slug.
   - `test_sigint_returns_ok` — wrap loop in a thread, send SIGINT,
     assert exit code OK and a final newline emitted. (Or mock
     KeyboardInterrupt directly — easier than threading.)

2. **Implementation** (`end_of_line/watch.py`, extending phase 1):
   ```python
   import json, signal, sys, time
   from pathlib import Path
   from typing import TextIO
   from . import state as st

   def _snapshot_line(slug: str, data: dict) -> str:
       claim = data.get("current_claim")
       active = f"active={claim['phase_id']}" if claim else "active=none"
       return f"[snapshot] {slug}: {data['status']}, {active}"

   def _slug_for_path(path: Path) -> str:
       # state filename is "<slug>.state.json"
       return path.stem.removesuffix(".state")

   def stream_loop(
       state_paths: list[Path], *,
       json_mode: bool = False,
       verbose: bool = False,
       sink: TextIO | None = None,
       poll_interval: float = 1.0,
       max_ticks: int | None = None,  # for tests
   ) -> int:
       if sink is None:
           sink = sys.stdout
       cursors: dict[Path, int] = {}
       # Baseline
       for path in list(state_paths):
           try:
               data = st.load(path)
           except (FileNotFoundError, OSError, json.JSONDecodeError):
               continue
           slug = _slug_for_path(path)
           print(_snapshot_line(slug, data), file=sink, flush=True)
           cursors[path] = len(data.get("events", []))

       ticks = 0
       try:
           while max_ticks is None or ticks < max_ticks:
               for path in list(cursors.keys()):
                   try:
                       data = st.load(path)
                   except (FileNotFoundError, OSError, json.JSONDecodeError):
                       cursors.pop(path, None)
                       continue
                   events = data.get("events", [])
                   slug = _slug_for_path(path)
                   for evt in events[cursors[path]:]:
                       if json_mode:
                           # Apply same filter as text mode
                           line_or_none = project_event(
                               evt, slug, verbose=verbose,
                           )
                           if line_or_none is None:
                               continue
                           print(json.dumps({
                               "ts": evt.get("ts"),
                               "slug": slug,
                               "event": evt,
                           }), file=sink, flush=True)
                       else:
                           line = project_event(
                               evt, slug, verbose=verbose,
                           )
                           if line is not None:
                               print(line, file=sink, flush=True)
                   cursors[path] = len(events)
               ticks += 1
               if max_ticks is None or ticks < max_ticks:
                   time.sleep(poll_interval)
       except KeyboardInterrupt:
           print("", file=sink, flush=True)
       return 0  # ExitCode.OK — phase cli wires the actual enum
   ```
   Note: `max_ticks` is a test seam, kept private. Phase `cli`
   doesn't pass it (uses default `None`).

3. **Acceptance.**
   - 11 new tests green.
   - Phase `events` tests still green.
   - Full suite green.

4. **Commit + complete.**
   - Title: `clu-watch: phase stream — polling loop + cursor +
     snapshot baseline`
   - Stage: `end_of_line/watch.py`, `tests/test_watch_stream.py`.
   - `clu complete --plan clu-watch --phase stream --token <T>`

## Failure modes to watch

- **Polling resource cost** — 1s interval × 20 plans = 20 file
  reads/sec. With `st.load` doing flock acquisition each time,
  this is heavier than ideal. Acceptable for v1; if it bites,
  cache the mtime and skip load when unchanged. Mark as a v2
  optimization, not a phase-stream blocker.
- **Event log array unbounded growth** — `data["events"]` accretes
  forever. Cursor approach handles this fine, but the
  per-iteration `data.get("events", [])` materializes the full
  list. Same v2 perf concern — note, don't fix.
- **Test flake from time.sleep** — use `max_ticks` and avoid
  real sleeps in tests. Pass `poll_interval=0` and `max_ticks=1`
  for deterministic single-tick runs.
- **JSON-mode filter inconsistency** — make sure verbose filtering
  applies in both text AND json modes. Test covers this; don't
  let a "raw passthrough" shortcut leak un-filtered events to
  JSON consumers.
