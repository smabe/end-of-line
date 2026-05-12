# queue-ux-hardening-impl — multi-arg `queue add` + in-flight head footer

You are the only phase of the `queue-ux-hardening` plan. Closes
[#18](https://github.com/smabe/end-of-line/issues/18).

Read the master plan first for locked design + failure modes. Do
exactly what's below.

## Locked decisions (do NOT re-litigate)

- **Multi-arg add**: argparse `nargs='+'`, single positional renamed
  `slugs`. All-or-nothing batch validation. Single `queue.mutate`
  window.
- **`--front` ordering**: `clu queue add a b c --front` → `[a, b, c,
  ...existing]`. Use slice insertion (`data["queue"][0:0] = entries`),
  not a loop with insert(0).
- **In-flight footer source**: `reg_states` dict that
  `cmd_queue_list` already builds (cli.py:950-954). Reuse it; don't
  walk the registry twice.
- **Footer placement**: after the pending table, before the
  `Recent failures:` section.
- **Footer format**: `In flight: <slug> (dispatched <HH:MM:SS UTC>,
  lease until <HH:MM:SS UTC>)`. Plain ASCII, one line per
  in-flight slug, sorted by `started_at` ascending.
- **No queue schema change.** queue.history still records only
  failures.

## Read first

- `end_of_line/cli.py:195-205` — `p_queue_add` subparser declaration.
  Single-positional `slug` becomes `nargs='+'` `slugs`.
- `end_of_line/cli.py:811-880` — current `cmd_queue_add`. Line 812
  reads `args.slug` (single); refactor to iterate `args.slugs`.
- `end_of_line/cli.py:931-976` — current `cmd_queue_list`. Reuse
  `reg_states` (line 950) for the in-flight footer.
- `end_of_line/cli.py:887-903` — `_queue_row`. Untouched, but
  understand how it consumes `reg_states` to avoid double-counting.
- `end_of_line/state.py` — `STATUS_RUNNING`, `STATUS_STALLED`,
  `parse_iso`. The "in flight" predicate is essentially "state has a
  `current_claim`" (covers both RUNNING and STALLED).
- `end_of_line/registry.py:77-96` — `load_entry_state`. Already
  tolerant; no changes needed.
- `tests/test_queue_add.py` and `tests/test_queue_list.py` —
  existing patterns. Use `isolate_registry` + `isolate_queue` in
  setUp per CLAUDE.md.
- `docs/operations.md` queue section + `docs/contract.md` queue
  schema section — find via grep.

## Produce

1. **TDD: failing tests first.**

   Add to `tests/test_queue_add.py`:
   - `test_add_multiple_slugs_appends_in_order` — register a project
     with three plan files. `clu queue add a b c` succeeds; queue is
     `[a, b, c]`; output has three position lines + `queued 3 plans`.
   - `test_add_single_slug_unchanged_output` — `clu queue add foo`
     prints `queued at position 1` and nothing else (no batch total
     line). Backwards-compat regression check.
   - `test_add_multiple_atomic_on_invalid_slug` — `clu queue add a
     INVALID-SLUG c` rejects with the slug-validation error naming
     `INVALID-SLUG`; queue stays empty.
   - `test_add_multiple_atomic_on_missing_plan_file` — `clu queue
     add a missing c` (where `plans/missing.md` doesn't exist)
     rejects naming `missing`; queue stays empty.
   - `test_add_multiple_atomic_on_within_batch_dupe` — `clu queue
     add a b a` rejects with `duplicate slug 'a' in batch`; queue
     stays empty.
   - `test_add_multiple_atomic_on_pre_existing_dupe` — queue starts
     `[foo]`. `clu queue add bar foo baz` rejects with `'foo'
     already queued at position 1`; queue stays `[foo]` (NOT
     `[foo, bar]`).
   - `test_add_multiple_front_preserves_arg_order` — queue starts
     `[x, y]`. `clu queue add a b c --front` results in `[a, b, c,
     x, y]`. NOT `[c, b, a, x, y]`.
   - `test_add_multiple_dispatched_under_single_lock` — assert (via
     mock or by counting `_mutate` invocations) that one
     `queue.mutate` call covers the whole batch.

   Add to `tests/test_queue_list.py`:
   - `test_list_in_flight_footer_when_plan_dispatched` — register
     `foo`, set its state to RUNNING with a `current_claim`. Pending
     queue is empty (foo was popped). Output: `(queue is empty)` +
     blank + `In flight: foo (dispatched HH:MM:SS UTC, lease until
     HH:MM:SS UTC)`.
   - `test_list_in_flight_footer_with_pending` — pending `[bar]`,
     `foo` is in-flight. Table renders bar; footer shows foo.
   - `test_list_no_in_flight_footer_when_empty` — pending `[bar]`,
     no registered plans have a claim. Output is the table only,
     no `In flight:` line.
   - `test_list_in_flight_sorts_by_started_at` — two in-flight
     plans (multi-pop scenario, even though current semantics
     forbid it — test the iteration anyway). Earlier `started_at`
     comes first.
   - `test_list_in_flight_includes_stalled` — claim with
     past-lease `lease_expires`. Footer still shows the slug. The
     pending-table STATUS column will project STALLED if it's also
     in the table; the footer is independent.
   - `test_list_in_flight_omits_slug_also_in_pending` — defensive:
     if a slug somehow appears in both the registry-with-claim and
     the pending queue (shouldn't happen; race condition test),
     dedupe by skipping the footer entry.

   Run suite — all new tests must FAIL.

2. **Subparser change** at `cli.py:198`:

   ```python
   p_queue_add.add_argument("slugs", nargs="+", help="One or more plan slugs")
   ```

   Update the subparser help string at `cli.py:195-197` to mention
   multi-arg ("Append one or more plan slugs to the queue (--front to
   insert at head).").

3. **Refactor `cmd_queue_add`**:

   ```python
   def cmd_queue_add(args) -> int:
       slugs = args.slugs

       # Slug regex first — cheapest validation, do it for all.
       for s in slugs:
           try:
               st.validate_slug(s, kind="plan slug")
           except st.InvalidSlug as exc:
               return _die(ExitCode.INVALID_SLUG, str(exc))

       # Within-batch duplicates.
       seen = set()
       for s in slugs:
           if s in seen:
               return _die(
                   ExitCode.STATUS_TRANSITION,
                   f"duplicate slug {s!r} in batch",
               )
           seen.add(s)

       project = args.project if args.project is not None else Path.cwd()
       cfg = load_project_config(project)

       # Bootstrap (unchanged).
       registered_roots = {
           Path(e.project_root).resolve() for e in registry.entries()
       }
       if cfg.project_root not in registered_roots:
           return _die(
               ExitCode.GENERIC,
               f"project {cfg.project_root} has no registered plans; "
               f"run `clu init --project {cfg.project_root} --plan <slug>` first",
           )

       # Plan-file existence — all must exist before any mutation.
       for s in slugs:
           plan_file = cfg.project_root / cfg.plan_dir / f"{s}.md"
           if not plan_file.exists():
               return _die(
                   ExitCode.UNKNOWN_TASK, f"no plan file at {plan_file}",
               )

       queue_path = cfg.queue_path()
       if queue_path.exists():
           try:
               queue.load(queue_path)
           except _QUEUE_LOAD_ERRORS as exc:
               return _refuse_on_corrupt_queue(queue_path, exc)

       # Single mutation window — atomic from cron's POV.
       positions: list[int] = []
       with queue.mutate(queue_path) as data:
           # Pre-existing dupe check, after lock so the snapshot is fresh.
           existing_by_slug = {
               entry["slug"]: i + 1
               for i, entry in enumerate(data["queue"])
           }
           for s in slugs:
               if s in existing_by_slug:
                   return _die(
                       ExitCode.STATUS_TRANSITION,
                       f"{s!r} already queued at position {existing_by_slug[s]}; "
                       f"`clu queue remove {s}` first to re-order",
                   )

           entries = [
               {
                   "slug": s,
                   "added_at": st.utcnow(),
                   "added_by": "operator",
                   "position_at_add": "front" if args.front else "tail",
               }
               for s in slugs
           ]
           if args.front:
               data["queue"][0:0] = entries  # slice insert preserves arg order
               positions = list(range(1, len(entries) + 1))
           else:
               start = len(data["queue"]) + 1
               data["queue"].extend(entries)
               positions = list(range(start, start + len(entries)))

       for pos in positions:
           print(f"queued at position {pos}")
       if len(slugs) > 1:
           print(f"queued {len(slugs)} plans")
       return ExitCode.OK
   ```

   IMPORTANT: the early-return on a queued-error inside the `with
   queue.mutate(...)` block triggers a re-raise from the context
   manager (mutations are committed on clean exit, NOT on early
   return). Verify by reading `queue.mutate` — if it commits on
   `__exit__` regardless of return, the dedup check needs to raise
   (then the manager rolls back) instead of `return _die`. If
   `queue.mutate` is a `contextmanager` that commits unconditionally,
   restructure: do the dedup check before the `with` block (re-load
   fresh inside the existing try/except) OR raise an exception inside
   the block and catch it after.

   **Read `end_of_line/queue.py`'s `mutate` definition before
   committing the structure.** This is the single sharpest edge in
   this refactor.

4. **Refactor `cmd_queue_list`** to add the in-flight footer:

   At cli.py:950-954, `reg_states` is built when pending is
   non-empty. Move the `reg_states` construction OUT of the
   `if not pending:` branch so it's always available — empty-pending
   + in-flight is a real case (just popped the only entry, dispatch
   in flight).

   After the existing `if history: ...` block (cli.py:968-975),
   add:

   ```python
   pending_slugs = {e["slug"] for e in pending}
   in_flight = []
   for slug, state in reg_states.items():
       if not state:
           continue
       claim = state.get("current_claim")
       if not claim:
           continue
       if slug in pending_slugs:
           continue  # defensive: race-condition dedup
       in_flight.append((slug, claim))
   in_flight.sort(key=lambda sc: sc[1].get("started_at", ""))

   if in_flight:
       print()  # blank line separator
       for slug, claim in in_flight:
           started = _format_iso_clock(claim.get("started_at"))
           lease = _format_iso_clock(claim.get("lease_expires"))
           print(f"In flight: {slug} (dispatched {started}, lease until {lease})")
   ```

   Add a small helper near `_format_age_iso`:

   ```python
   def _format_iso_clock(ts_iso: str | None) -> str:
       """ISO timestamp → 'HH:MM:SS UTC'. Unknown / unparseable → '?'."""
       if not ts_iso:
           return "?"
       try:
           dt = st.parse_iso(ts_iso)
       except (TypeError, ValueError):
           return "?"
       return dt.strftime("%H:%M:%S UTC")
   ```

5. **Re-verify the `if not pending:` early branch.** If you moved
   `reg_states` construction up, the empty-pending branch must still
   print `(queue is empty)` BEFORE the in-flight footer. Order:
   - `(queue is empty)` OR table
   - `Recent failures:` section if history non-empty
   - `In flight: ...` lines if any

   Tests will catch ordering bugs; eyeball the test fixtures.

6. **Run the suite — all green.**

7. **Documentation:**
   - `docs/operations.md` — find the queue section. Add a multi-arg
     example to `clu queue add` and a one-paragraph note on the
     in-flight footer with sample output.
   - `docs/contract.md` — find the queue schema section. Add a note:
     "queue.history records only failures (`removed | absorbed |
     abandoned`); successful pops live only in the popped plan's
     state.json. The `clu queue list` in-flight footer derives from
     the registry, not queue.history."

8. **`/simplify`** — multi-file diff, definitely run it.

9. **Commit.** Title: `queue: multi-arg add + in-flight head footer
   on list`. Body references `closes #18`.

## Verification before `clu complete`

Per CLAUDE.md mandate #9: re-run `python3 -m unittest discover -s tests`
right before `clu complete`. Report final test count + explicit
confirmation that all existing queue tests stayed green (regression
gate for the cmd_queue_add refactor).

## Acceptance

- [ ] `clu queue add a b c` adds three slugs in one transaction
- [ ] Partial-failure batches are atomic (queue unchanged on any
      validation error)
- [ ] Single-arg `clu queue add foo` output unchanged from today
- [ ] `--front` with multi-arg preserves argument order
- [ ] `clu queue list` shows `In flight: ...` footer when registered
      plans have an active claim
- [ ] Footer omitted cleanly when no in-flight plans
- [ ] All new tests pass; existing queue tests stay green
- [ ] `docs/operations.md` + `docs/contract.md` updated
- [ ] One commit with `closes #18` in body
