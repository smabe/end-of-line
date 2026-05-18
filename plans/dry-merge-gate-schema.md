# dry-merge-gate-schema — `--batch` flag + `batch_id` schema additions

You are phase `schema` of the `dry-merge-gate` plan. Add an additive
`batch_id` field to queue entries, history entries, and plan state,
plus a `--batch <name>` flag on `clu queue add`. No behavior change
yet — the gate rule lands in phase `rule`.

## Locked decisions (do NOT re-litigate)

See `plans/dry-merge-gate.md`. Summary:

- `--batch <name>` is operator-only — workers (`--token`) get
  rejected with `_die(GENERIC, "--batch is operator-only")`.
- Single `--batch` applied uniformly to every slug in the `queue add`
  call. No per-slug batching.
- `batch_id` validated via `st.validate_slug(args.batch, kind="batch
  id")` if not None. Same regex as plan slugs.
- Additive only. Verify `st.load` tolerates the new field on existing
  state files before assuming. If it rejects unknown fields → bump
  `SCHEMA_VERSION` + migration. Likely NOT needed — keep diff small.

## Read first

- `end_of_line/queue.py:37-38` — `_empty()` shape (add `batch_id`? no
  — `batch_id` lives on entries, not the queue-level dict).
- `end_of_line/queue.py:55-100` — regex helpers; confirm additive
  field doesn't break `best_effort_extract_slugs` (it shouldn't —
  scans for `"slug":` only).
- `end_of_line/cli.py:1988-2090` — `cmd_queue_add` flow: arg parsing,
  validation, entry construction.
- `end_of_line/cli.py:1900-1985` — `_cmd_queue_add_worker` (worker
  callback path); reject `--batch` here BEFORE any mutation.
- `end_of_line/cross_plan_rules.py:182-203` — normal queue-pop path;
  propagate `batch_id` from queue entry to fresh plan state.
- `end_of_line/cross_plan_rules.py:150-180` — absorbed / abandoned
  history-append paths; verify `**entry` spread already preserves
  `batch_id` (no code change needed if so — test it).
- `end_of_line/state.py:62-150` — `STATUS_*` constants and
  `empty_state` shape; add `"batch_id": None` to baseline.
- `tests/test_queue_add.py` — patterns for queue-add tests
  (operator vs worker; idempotency).
- `tests/test_cross_plan_rules.py` (if exists) or equivalent — pattern
  for queue_advancement_rule pop-path test.

## Produce

1. **Failing tests first.** New file `tests/test_queue_batch_schema.py`
   with:
   - `test_queue_add_stamps_batch_id_uniformly` — 3 slugs + `--batch
     foo` → all 3 entries have `batch_id="foo"`.
   - `test_queue_add_without_batch_id_is_null` — entries default
     `batch_id=None`.
   - `test_queue_add_invalid_batch_slug_rejects_invalid_slug` —
     `--batch "BAD_SLUG"` → `ExitCode.INVALID_SLUG`.
   - `test_worker_queue_add_rejects_batch_flag` — worker callback
     (`--token T --plan P --phase X`) with `--batch foo` →
     `ExitCode.GENERIC`, no state mutation.
   - `test_queue_pop_propagates_batch_id_to_plan_state` —
     `queue_advancement_rule` normal-pop path copies `batch_id` from
     queue entry into the fresh plan state's top-level dict.
   - `test_history_entry_preserves_batch_id_on_absorbed` — absorbed
     pop preserves `batch_id` in history entry via `**entry` spread.
   - `test_empty_state_has_null_batch_id` — `st.empty_state(...)`
     returns dict with `"batch_id": None`.

2. **Implementation.**
   - `end_of_line/cli.py` argparse: add `--batch BATCH` to
     `p_queue_add.add_argument(...)`. Help: `"Tag this batch of
     plans with a shared batch_id (validated as a slug). Required for
     the multi-plan dry-merge gate to fire (#50)."`
   - `end_of_line/cli.py` `cmd_queue_add`:
     ```python
     if args.batch is not None:
         try:
             st.validate_slug(args.batch, kind="batch id")
         except st.InvalidSlug as exc:
             return _die(ExitCode.INVALID_SLUG, str(exc))
     ```
     Entry construction: `"batch_id": args.batch`.
   - `end_of_line/cli.py` `_cmd_queue_add_worker`: top of function,
     before any state mutation:
     ```python
     if args.batch is not None:
         return _die(ExitCode.GENERIC, "--batch is operator-only")
     ```
     Entry construction: `"batch_id": None`.
   - `end_of_line/cross_plan_rules.py` normal-pop block (around line
     195): after `fresh = st.empty_state(slug, cfg.plan_dir)`, add:
     ```python
     if head.get("batch_id"):
         fresh["batch_id"] = head["batch_id"]
     ```
   - `end_of_line/state.py` `empty_state(...)`: add `"batch_id":
     None` to the baseline dict it returns.

3. **Acceptance.**
   - All 7 new tests green.
   - Existing queue tests still pass (additive backward-compat).
   - `python3 -m unittest discover -s tests` shows test count
     increased by ~7 with zero regressions.
   - `grep -rn '"batch_id"' end_of_line/` returns ≥4 hits.
   - `clu queue add --help` shows `--batch BATCH` in the args.

4. **Commit + complete.**
   - `dry-merge-gate: phase schema — --batch flag + batch_id schema
     additions (#50)`
   - Stage: `end_of_line/cli.py`, `end_of_line/cross_plan_rules.py`,
     `end_of_line/queue.py` (if touched), `end_of_line/state.py`,
     `tests/test_queue_batch_schema.py`.
   - `clu complete --plan dry-merge-gate --phase schema --token <T>`.

## Failure modes to watch

- **Schema version bump pressure.** `st.load` may or may not reject
  unknown fields. Check `end_of_line/state.py:SCHEMA_VERSION` +
  `load()` body. If additive-field-tolerant, don't bump — keep diff
  small. If not, bump + minimal migration.
- **Worker token enforcement.** The `--batch` rejection in
  `_cmd_queue_add_worker` MUST happen before any `with queue.mutate`
  block, or a malicious worker could partially stamp entries before
  the rejection fires. The test for worker rejection should also
  assert queue file is unchanged.
- **Absorbed / abandoned history paths.** `**entry` spread should
  preserve `batch_id` automatically. Don't add explicit copying —
  test it instead, so a future refactor can't drop the field
  silently.
- **`st.validate_slug` `kind` parameter.** The kind string flows into
  error messages. Use `"batch id"` so the operator-facing error reads
  cleanly: `"batch id 'BAD' violates slug regex ..."`.
