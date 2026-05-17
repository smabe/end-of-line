# queue-worker-callback-render — `clu queue list` source attribution

You are phase `render` of `queue-worker-callback`. Surface
worker-enqueued entries in `clu queue list` output with source
attribution. Operator entries render unchanged.

## Locked decisions (do NOT re-litigate)

See `plans/queue-worker-callback.md` § Phase 5. Summary:
- Worker entries gain an indented annotation line `  (from
  <source_plan>/<source_phase>)` and, if `reason` present, a second
  indented line `  reason: <reason>`.
- Operator entries render exactly as today (single row, no
  annotations).

## Read first

- `end_of_line/cli.py:1826-1860` — `_queue_row` + `_format_table`.
- `end_of_line/cli.py:1887-1959` — `cmd_queue_list` body.
- `tests/test_queue_list.py` — existing rendering tests.
- `tests/test_queue_footer.py` — the in-flight footer convention
  (per-line shape after the table).

## Produce

1. **Failing tests first**
   (`tests/test_queue_worker_render.py`, new):
   - `test_list_worker_entry_shows_source` — seed a queue with one
     worker entry (`source_plan="feature-b"`,
     `source_phase="c-extract"`, no reason). Run `clu queue list`.
     Assert stdout contains a line matching
     `(from feature-b/c-extract)` (substring assertion — don't
     over-specify exact indent / prefix).
   - `test_list_worker_entry_with_reason_shows_reason` — same as
     above but with `reason="follow-up test coverage"`. Assert
     stdout contains `reason: follow-up test coverage`.
   - `test_list_operator_entries_unchanged` — single operator entry,
     no `(from ...)` substring in output.
   - `test_list_mixed_operator_worker_entries` — three entries:
     operator, worker, worker. Assert table has three POS rows and
     two `(from ...)` substrings.
   - `test_list_with_only_worker_entries_no_regression` — pure
     worker queue still produces a header + rows; no IndexError.

2. **Implementation.**
   - Extend `cmd_queue_list` (line ~1916, after `rows = [...]`):
     - After printing the table, iterate the pending entries and
       emit per-row annotation blocks for worker entries:
       ```python
       for i, entry in enumerate(pending, start=1):
           if entry.get("added_by") == "worker":
               src = f"{entry['source_plan']}/{entry['source_phase']}"
               print(f"  {i}: (from {src})")
               if entry.get("reason"):
                   print(f"     reason: {entry['reason']}")
       ```
     - The `i:` prefix lets the operator match annotation to row
       when the table is long. Alternative: weave annotation lines
       into the table itself, accepting column-alignment break for
       annotated rows. Pick whichever matches existing project
       style after eyeballing `tests/test_queue_footer.py`. Both
       shapes pass the substring-based tests.

3. **Acceptance.**
   - 5 new tests green.
   - Existing `test_queue_list.py` + `test_queue_footer.py` green.
   - Full suite green.
   - Eyeball: run `clu queue list` against a seeded fixture queue;
     output is human-readable.

4. **Commit + complete.**
   - Title: `queue-worker-callback: phase render — clu queue list source attribution (#17)`
   - Stage: `end_of_line/cli.py`, `tests/test_queue_worker_render.py`.
   - `clu complete --plan queue-worker-callback --phase render --token <T>`

## Failure modes to watch

- **Trailing whitespace from `rstrip()`** — `_format_table` strips
  trailing whitespace per row. Annotation lines emitted separately
  should match the same hygiene.
- **Test assertions over-specifying format** — assert on substring
  presence ("(from feature-b/c-extract)" appears in stdout), not
  exact line shape. Lets the worker pick the cleanest visual without
  breaking the contract.
