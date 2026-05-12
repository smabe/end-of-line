# clu-queue-footer — bare `clu` footer hint + CLI corruption refusal

You are phase `footer` of the `clu-queue` plan. Phases primitive/add/
list/pop/repair have shipped: the queue feature is operationally
complete; the operator can enqueue, list, remove, watch cron drain,
and a corrupt queue.json auto-repairs (with reverts on destructive
attempts). Your job: a small surface polish — append a one-line
footer to bare `clu`'s fleet view when the queue is non-empty, and
make the queue CLI commands refuse loudly with paste-into-Claude
diagnoses when queue.json is corrupt.

Design pass is done. Full context in
`.claude/plans/plan-queue-master.md` and `plans/clu-queue.md`. Do not
redesign.

## Locked decisions (do NOT re-litigate)

- **Footer placement**: bare `clu` (the fleet view via `cmd_fleet`)
  appends `(queue: N pending — see \`clu queue list\`)` *after* the
  existing PLAN/STATUS/PHASE table, separated by a blank line.
  Hidden when queue is empty, missing, or unreadable.
- **Footer aggregates per-project**: bare `clu` shows ALL projects
  in the registry. The footer should mention the total pending
  count across all queues OR show one line per project with a
  non-empty queue — pick the one that reads better with realistic
  data (typically 1-2 projects). **Recommended: single line with
  the total across registered projects.**
  ```
  (queue: 3 pending across 2 projects — see `clu queue list --project <P>`)
  ```
  If only one project has a non-empty queue, mention it:
  ```
  (queue: 3 pending in ~/projects/end-of-line — see `clu queue list`)
  ```
- **CLI corruption refusal**: `clu queue add`/`list`/`remove` when
  `queue.load(path)` raises → `_die(ExitCode.GENERIC, <diagnosis>)`.
  The diagnosis message MUST be paste-into-Claude friendly:
  ```
  queue.json corrupt at /path/to/queue.json:
    <exception type>: <message>
  Backup may exist at /path/to/queue.json.corrupt-* — `ls -lt` to find it.
  Open Claude in this project to repair.
  ```
- **CLI ops never auto-repair.** Operator is at the keyboard; if
  they want repair, they run Claude themselves. Only `cmd_tick_all`
  triggers the auto-repair pipeline (phase `repair`).
- **No `cmd_init` queue cleanup.** Master plan explicitly says
  absorb-at-pop (phase `pop`) handles the manual-init collision.
  This phase does NOT touch `cmd_init`.

## Read first

- `end_of_line/fleet.py` — `render(entries)`. Where the existing
  PLAN/STATUS/PHASE table comes from. Identify the natural
  insertion point for an appended footer.
- `end_of_line/cli.py` `cmd_fleet` (search for it) — calls
  `fleet.render` and prints. The footer composition happens here
  unless `fleet.render` is extended to take a footer string.
- `end_of_line/cli.py` `cmd_queue_add` / `cmd_queue_list` /
  `cmd_queue_remove` (from phases add/list). The corruption catch
  goes near the `queue.load` / `queue.mutate` calls.
- `end_of_line/queue.py` — `load` raises on corruption. The catch
  pattern matches what phase `repair` does in `_handle_corrupt_queue`,
  but here we just `_die` rather than auto-repair.

## Produce

1. **TDD: failing tests first.** Add `tests/test_queue_footer.py`
   and extend `tests/test_queue_add.py`, `test_queue_list.py`,
   `test_queue_remove.py` with corruption tests:

   Footer tests:
   - `test_fleet_view_no_footer_when_no_queue_files` — registry
     has plans but no queue.json anywhere; bare `clu` output ends
     with the existing PLAN table; no `(queue:` line.
   - `test_fleet_view_no_footer_when_all_queues_empty` — projects
     have queue.json files but all are empty; no footer.
   - `test_fleet_view_footer_for_single_project_with_queue` —
     one project, queue has 3 pending; footer reads
     `(queue: 3 pending in <project_root> — see \`clu queue list\`)`.
   - `test_fleet_view_footer_for_multiple_projects_with_queues` —
     two projects, queues have 2 and 1 pending; footer reads
     `(queue: 3 pending across 2 projects — see \`clu queue list --project <P>\`)`.
   - `test_fleet_view_footer_skips_unreadable_queue` — one
     project's queue.json is corrupt; the footer counts only the
     readable ones AND emits a hint like `(... 1 queue unreadable — run \`clu queue list --project <bad>\` for diagnosis)`.
     (Optional polish — keep it simple if the rendering grows.)
   - `test_fleet_view_footer_skips_unregistered_projects` — a
     project not in the registry doesn't contribute. (Footer
     iterates `registry.entries()` distinct project_roots, same
     as the tick-all post-loop step.)

   CLI corruption tests (add to phase 2/3 test files):
   - `test_queue_add_refuses_on_corrupt_queue` — queue.json is
     malformed JSON; `clu queue add foo` exits GENERIC with a
     diagnosis containing "queue.json corrupt" + "Open Claude in
     this project to repair".
   - `test_queue_list_refuses_on_corrupt_queue` — same path for
     list; exits GENERIC with the same diagnosis shape.
   - `test_queue_remove_refuses_on_corrupt_queue` — same for remove.
   - `test_queue_list_diagnosis_mentions_backup_paths` — diagnosis
     instructs the operator how to find the auto-repair backup files.

   Use `isolate_registry` + `isolate_queue`. Run suite — all new
   tests must FAIL.

2. **Extend `cmd_fleet` to compose the footer.** Simplest shape:
   ```python
   def cmd_fleet(args) -> int:
       entries = registry.entries()
       output = fleet.render(entries)
       footer = _queue_footer(entries)
       if footer:
           output += "\n" + footer + "\n"
       print(output, end="")
       return ExitCode.OK

   def _queue_footer(entries) -> str | None:
       counts = []  # list[(project_root: Path, pending: int)]
       unreadable = []
       seen = set()
       for entry in entries:
           root = Path(entry.project_root).resolve()
           if root in seen: continue
           seen.add(root)
           cfg = load_project_config(root)
           qp = cfg.queue_path()
           if not qp.exists():
               continue
           try:
               data = queue.load(qp)
           except Exception:
               unreadable.append(root)
               continue
           if data["queue"]:
               counts.append((root, len(data["queue"])))

       if not counts and not unreadable:
           return None

       total = sum(n for _, n in counts)
       parts = []
       if counts:
           if len(counts) == 1:
               root, n = counts[0]
               parts.append(f"queue: {n} pending in {root} — see `clu queue list`")
           else:
               parts.append(
                   f"queue: {total} pending across {len(counts)} projects — see `clu queue list --project <P>`"
               )
       if unreadable:
           parts.append(
               f"{len(unreadable)} queue file{'s' if len(unreadable) > 1 else ''} unreadable"
           )
       return "(" + "; ".join(parts) + ")"
   ```
   Match the codebase's idioms.

3. **Add corruption refusal to `cmd_queue_add` / `cmd_queue_list` /
   `cmd_queue_remove`.** The pattern:
   ```python
   def _refuse_on_corrupt_queue(queue_path, exception):
       backup_glob = queue_path.parent.glob(f"{queue_path.name}.corrupt-*")
       backups = sorted(backup_glob, reverse=True)
       hint = (
           f"queue.json corrupt at {queue_path}:\n"
           f"  {type(exception).__name__}: {exception}\n"
       )
       if backups:
           hint += f"Backup at {backups[0]} (and {len(backups)-1} older).\n"
       else:
           hint += "No backup files found.\n"
       hint += "Open Claude in this project to repair."
       return _die(ExitCode.GENERIC, hint)
   ```
   Apply at the top of each command, wrapping `queue.load` /
   `queue.mutate`:
   ```python
   try:
       data = queue.load(queue_path)
   except (json.JSONDecodeError, st.SchemaVersionMismatch, KeyError) as e:
       return _refuse_on_corrupt_queue(queue_path, e)
   ```
   `cmd_queue_add` and `cmd_queue_remove` use `queue.mutate`, which
   internally calls `queue.load` — so the catch wraps the `with
   queue.mutate(...) as data:` block. Either catch outside the
   `with` (cleaner) or inside (might leave the lock orphaned —
   verify that `mutate`'s exception path releases the lock).
   **Recommendation: catch outside the `with` by pre-loading with
   `queue.load` first, then entering `mutate` if load succeeded.**
   The double-read is cheap (sub-millisecond) and the catch
   semantics are clean.

4. **Run the full suite.** All new tests pass. Existing tests
   unchanged. Count grows by ~10.

5. **`/simplify`.** Touches cli.py + tests. Run /simplify.

6. **Commit.** Structured:
   - Title: `clu-queue phase footer: fleet-view footer hint + CLI corruption refusal`
   - Why: bare `clu` should surface that a queue exists when it's
     non-empty (avoids the "I forgot a queue is running" failure
     mode); CLI ops should refuse loudly on corruption with
     paste-into-Claude friendly diagnosis (operator-at-keyboard
     path doesn't go through auto-repair).
   - What's new: `_queue_footer` aggregator in cli.py; corruption
     refusal in all three queue CLI commands with backup-aware
     diagnostic.
   - Under the hood: footer iterates distinct project_roots
     (same shape as tick-all post-loop); single vs. multi-project
     rendering picks the natural wording; unreadable queues
     surfaced inline.
   - Tests: ~10 new tests covering single/multi-project footers,
     empty/missing/unreadable queues, corruption refusal in all
     three CLI ops.
   - Co-Authored-By trailer.

7. **`clu complete --token <token>` with SHA + count summary.**

## Failure modes to watch for

- **Footer iteration cost.** Loads every project's queue.json on
  every bare `clu`. For 2-3 projects this is negligible; for 50,
  visible latency. The CLI is interactive — keep it fast. If you
  measure >50ms, add a fast-path: only load queue.json if its mtime
  is newer than a cached "last empty-check" timestamp. Don't
  over-engineer unless real measurements demand it.
- **Footer string for single project with quotes/spaces in path.**
  `<project_root>` might have spaces or unusual characters; the
  backtick-quoted suggestion `\`clu queue list\`` is fine for the
  one-project case (CWD is implied). For multi-project, the
  `--project <P>` hint uses a placeholder, not a real path — so no
  shell-quoting concern.
- **Footer's "queue unreadable" hint races with auto-repair.** If
  cron is mid-repair when bare `clu` runs, the queue file might be
  in flux (rare; the writes are atomic via rename). The footer
  treats "load fails" as "unreadable" — acceptable false signal for
  the duration of the repair (1-60s).
- **CLI refusal vs. tick-all auto-repair.** Two different paths
  with two different intents. The CLI refuses; tick-all repairs.
  Make sure the failure-mode tests don't accidentally trigger the
  repair path (the CLI tests should fix the queue.json out-of-band
  if they want a different state, not via cron tick).
- **`queue.load` raising `OSError`.** Permission denied on the
  file: the diagnosis should still surface the path and the system
  message. Test the EACCES case with a mode-0000 file.
- **Empty queue.json file (0 bytes).** `queue.load` raises
  `JSONDecodeError`; caught by the same path. Diagnosis is correct.
- **`_die`'s message rendering.** It typically prints to stderr.
  The footer renders to stdout. Tests should capture both streams.

## Done criteria for this phase

- Bare `clu` shows the queue footer when ≥1 project has a non-empty
  queue; hidden otherwise.
- Multi-project case renders a roll-up line with total + project
  count.
- `clu queue add`/`list`/`remove` refuse loudly on corrupt
  queue.json with a paste-into-Claude diagnosis that mentions
  backup paths if present.
- `cmd_init` is unchanged (no queue cleanup logic).
- ~10 new tests pass; full suite green.
- One commit, structured message, no `Fixes` trailer.
- `clu complete` with token + SHA + count summary.
