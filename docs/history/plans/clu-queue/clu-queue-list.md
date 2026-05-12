# clu-queue-list — `cmd_queue_list` + `cmd_queue_remove` + bare `clu queue`

You are phase `list` of the `clu-queue` plan. Phases `primitive` and
`add` have shipped: queue.py exists, `clu queue add` works, the
`queue` argparse subparser group is registered. Your job: ship the
remaining two operator commands (`list`, `remove`), make bare `clu
queue` default to `list`, and render the failure-history section
when present.

Design pass is done. Full context in
`.claude/plans/plan-queue-master.md` and `plans/clu-queue.md`. Do not
redesign.

## Locked decisions (do NOT re-litigate)

- **`clu queue list` (no flags)** renders the pending queue as a
  table, then a "Recent failures" section below it iff `history[]`
  is non-empty. No `--all` flag. No history filter knob.
- **`clu queue remove <slug>`** moves the pending entry to `history`
  with `outcome: "removed"`, `ended_at: utcnow_iso()`. Slug not in
  pending → `_die(ExitCode.UNKNOWN_TASK)`.
- **Bare `clu queue`** (no subcommand) defaults to running `list`.
  Argparse with `dest="queue_cmd"` will leave it as `None` — handle
  that in the dispatch table.
- **Status projection**: each pending entry's STATUS column is
  derived by cross-referencing the registry. If the slug is
  registered, project status from `state.json` via
  `registry.load_entry_state` + fleet's existing projection helpers
  (`fleet.summarize_plan` or equivalent). If not registered: status
  is `queued`. If queue head's slug is registered with status in
  `{HALTED, HALTED_REPLAN, PAUSED}`, render `HALTED — chain frozen
  at head` (or `PAUSED — ...`) as the NOTE for that row.
- **`MISSING` status**: if a queued slug's `plans/<slug>.md` no
  longer exists at render time, render NOTE as `⚠ plan file missing`
  (plain ASCII per project convention — use `! plan file missing`
  or `MISSING` — pick what matches existing fleet view; check
  `fleet.py`).
- **Table columns**: `POS  SLUG  STATUS  NOTE`. Plain text, aligned
  with `str.ljust`. Match the existing `fleet.render` formatting
  conventions.
- **Failure history rendering**: header `Recent failures:`, then
  one line per history entry: `  <slug>  <outcome>  <reason>  (<age> ago)`.
  Age computed from `ended_at` via the existing age-formatting
  helper (look in `notify.py` or `fleet.py` for the pattern).
- **Exit OK = 0** even on empty queue. Empty queue prints
  `(queue is empty)` and exits OK.

## Read first

- `end_of_line/queue.py` (phase primitive output).
- `end_of_line/cli.py` `cmd_queue_add` and argparse setup (phase
  add output) — your subparsers follow the same pattern.
- `end_of_line/fleet.py` — `summarize_plan` projection and the
  status constants it surfaces. Reuse, don't reimplement.
- `end_of_line/registry.py` — `entries()` and `load_entry_state()`
  for status projection.
- `end_of_line/state.py` — `STATUS_*` constants, especially
  HALTED/HALTED_REPLAN/PAUSED for the freeze-at-head check.
- `CLAUDE.md` — plain ASCII, no emoji unless requested.

## Produce

1. **TDD: failing tests first.** Add `tests/test_queue_list.py` and
   `tests/test_queue_remove.py`:

   List tests:
   - `test_list_empty_queue` — fresh queue; output is `(queue is empty)`.
   - `test_list_one_pending` — queue has [foo]; output has POS=1,
     SLUG=foo, STATUS=queued, NOTE=path-to-plan-file.
   - `test_list_multiple_pending_preserves_order` — queue has
     [a, b, c]; positions 1, 2, 3 in that order.
   - `test_list_renders_running_status_from_registry` — foo is
     registered with status RUNNING + active claim; STATUS=running
     for that row.
   - `test_list_renders_halted_freeze_marker` — queue head foo is
     registered with status HALTED; NOTE column for foo includes
     `chain frozen at head`.
   - `test_list_renders_paused_freeze_marker` — same, with STATUS_PAUSED.
   - `test_list_renders_missing_plan_file` — queue has [foo] but
     plans/foo.md was deleted post-add; NOTE includes `plan file missing`
     (or your project-specific MISSING token).
   - `test_list_renders_failure_history_when_present` — history has
     entries with outcomes `abandoned` + `removed`; output includes
     `Recent failures:` section with both, age-formatted.
   - `test_list_omits_failure_section_when_history_empty` — no
     `Recent failures:` line in output.
   - `test_list_bare_clu_queue_defaults_to_list` — `clu queue` (no
     subcommand) runs list and exits OK.
   - `test_list_unregistered_project` — `clu queue list --project P`
     where P isn't in registry → empty queue path; print
     `(queue is empty)` (NOT a bootstrap error — list should be
     tolerant; only `add` enforces bootstrap).
   - `test_list_handles_missing_queue_file` — queue.json doesn't
     exist; output is `(queue is empty)`, no error.

   Remove tests:
   - `test_remove_success_moves_to_history` — queue has [foo];
     `clu queue remove foo` → queue is empty, history has foo with
     outcome=removed, ended_at populated.
   - `test_remove_preserves_other_entries` — queue has [a, b, c];
     `clu queue remove b` → queue is [a, c], history has b.
   - `test_remove_rejects_invalid_slug` — exit INVALID_SLUG.
   - `test_remove_rejects_slug_not_in_pending` — queue has [foo];
     `clu queue remove bar` → exit UNKNOWN_TASK with message naming
     valid slugs OR saying "not in queue".
   - `test_remove_does_not_touch_running_slug` — foo was popped +
     dispatched; queue is empty (foo not in pending). `clu queue
     remove foo` → exit UNKNOWN_TASK (it's not pending; the running
     plan's state.json is the source of truth for canceling it).

   Use `isolate_registry` + `isolate_queue`. Run suite — all new
   tests must FAIL.

2. **Implement `cmd_queue_list(args)`:**

   ```python
   def cmd_queue_list(args) -> int:
       cfg = load_project_config(args.project)
       queue_path = cfg.queue_path()

       if not queue_path.exists():
           print("(queue is empty)")
           return ExitCode.OK

       data = queue.load(queue_path)
       pending = data["queue"]
       history = data["history"]

       if not pending:
           print("(queue is empty)")
       else:
           # Build registry projection
           reg_by_slug = {
               e.plan_slug: registry.load_entry_state(e)
               for e in registry.entries()
               if Path(e.project_root).resolve() == cfg.project_root.resolve()
           }
           head_slug = pending[0]["slug"]
           head_state = reg_by_slug.get(head_slug)
           head_frozen = head_state and head_state.get("status") in {
               st.STATUS_HALTED, st.STATUS_HALTED_REPLAN, st.STATUS_PAUSED,
           }

           rows = []
           for i, entry in enumerate(pending, start=1):
               status, note = _project_status(entry, cfg, reg_by_slug, is_head=(i == 1), head_frozen=head_frozen)
               rows.append((str(i), entry["slug"], status, note))
           print(_format_table(["POS", "SLUG", "STATUS", "NOTE"], rows))

       if history:
           print()  # blank line separator
           print("Recent failures:")
           for entry in history[-10:]:  # show last 10
               age = _format_age(st.parse_iso(entry["ended_at"]))
               print(f"  {entry['slug']}  {entry['outcome']}  ({age} ago)")

       return ExitCode.OK
   ```

   Helpers (`_project_status`, `_format_table`, `_format_age`) —
   put them in cli.py near the queue commands, or in a new
   `end_of_line/queue_render.py` if they grow. Prefer inline for
   v1 unless they hit ~50 lines.

3. **Implement `cmd_queue_remove(args)`:**

   ```python
   def cmd_queue_remove(args) -> int:
       slug = args.slug
       st.validate_slug(slug, kind="plan slug")

       cfg = load_project_config(args.project)
       with queue.mutate(cfg.queue_path()) as data:
           positions = [i for i, e in enumerate(data["queue"]) if e["slug"] == slug]
           if not positions:
               return _die(ExitCode.UNKNOWN_TASK, f"{slug} is not in the queue")
           idx = positions[0]
           entry = data["queue"].pop(idx)
           data["history"].append({
               **entry,
               "ended_at": st.utcnow_iso(),
               "outcome": "removed",
           })

       print(f"removed {slug} from queue")
       return ExitCode.OK
   ```

4. **Register argparse subparsers** for `list` and `remove` under
   the existing `queue` group:
   ```python
   p_queue_list = queue_subs.add_parser("list", help="Show pending queue.")
   p_queue_list.add_argument("--project", default=Path.cwd(), type=Path)
   p_queue_remove = queue_subs.add_parser("remove", help="Remove a slug from the queue.")
   p_queue_remove.add_argument("slug")
   p_queue_remove.add_argument("--project", default=Path.cwd(), type=Path)
   ```

5. **Bare `clu queue` defaults to list.** In the main dispatch
   table, if `args.queue_cmd is None`, route to `cmd_queue_list`.
   Test that `clu queue` with no flags works.

6. **Run the full suite.** All new tests pass. Existing tests
   unchanged. Count grows by ~17.

7. **`/simplify`.** Multi-file change; run /simplify per CLAUDE.md.

8. **Commit.** Structured:
   - Title: `clu-queue phase list: cmd_queue_list + cmd_queue_remove + bare default`
   - Why: operator needs to read what's queued and pull entries out;
     bare `clu queue` matching bare `clu` defaults shape.
   - What's new: `clu queue list`, `clu queue remove`, bare `clu
     queue` → list, status projection from registry, freeze-at-head
     marker, failure history rendering.
   - Under the hood: helpers `_project_status`, `_format_table`,
     `_format_age`; argparse subparsers extended; registry filtered
     by resolved project_root.
   - Tests: ~17 new tests covering rendering shapes + freeze
     markers + history section + remove paths.
   - Co-Authored-By trailer.

9. **`clu complete --token <token>` with SHA + count summary.**

## Failure modes to watch for

- **`fleet.summarize_plan` doesn't exist.** Search for the
  actual projection helper name; it may be `summarize` or live in a
  different module. Reuse what's there.
- **Status column for the head when host is busy with a different
  plan.** The freeze-marker check is `head_slug` registered with
  HALTED/HALTED_REPLAN/PAUSED. A *different* plan being running
  shouldn't trigger the marker. Test this explicitly.
- **`load_entry_state` returns None for broken state.json.**
  registry.py:84 is tolerant — the row exists, state.json may not
  load. Treat None as "registered, status unknown" — render
  `queued (registered)` or similar. Don't crash.
- **Long slugs or paths overflow the table.** `_format_table` should
  align columns but not truncate. Slugs are ≤64 chars (validate_slug
  cap); paths can be long. Long NOTE cells are OK to be wide.
- **Age formatting**: if `parse_iso` and an age-formatter don't
  exist, write them in this phase (they're small). Otherwise reuse.
- **Bare `clu queue` argparse behavior.** Without an explicit
  default subcommand, `args.queue_cmd` is None. Handle in dispatch.
  Don't add `set_defaults(queue_cmd="list")` to argparse — that
  hides the case from test assertions.
- **`--all` accidentally creeps in.** The OOS list explicitly cuts
  it. Don't add the flag "just in case." History is always rendered
  when present; not flag-gated.

## Done criteria for this phase

- `clu queue list` renders pending entries with POS/SLUG/STATUS/NOTE
  columns; failure history below when present.
- `clu queue` (no subcommand) routes to list.
- `clu queue list` on a project with no queue file prints
  `(queue is empty)` and exits OK.
- Head-freeze marker shown when queue head is HALTED/HALTED_REPLAN/PAUSED.
- Plan-file-missing slug rendered with MISSING note (or project's
  equivalent token).
- `clu queue remove <slug>` moves pending → history with outcome=removed.
- Remove rejects unknown/invalid slugs with the right exit codes.
- ~17 new tests pass; full suite green.
- One commit, structured message, no `Fixes` trailer.
- `clu complete` with token + SHA + count summary.
