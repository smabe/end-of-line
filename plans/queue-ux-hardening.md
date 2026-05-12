# queue-ux-hardening — multi-arg `queue add` + in-flight head hint on `queue list`

Closes [#18](https://github.com/smabe/end-of-line/issues/18). Two
small queue-UX gaps surfaced during the green-batch ship's post-ship
reflection (`0d0a25c`, 2026-05-12). Both touch `cmd_queue_add` /
`cmd_queue_list` / shared test infra — single bundle.

## Goal

After this plan ships:

```
$ clu queue add foo bar baz
queued at position 1
queued at position 2
queued at position 3
queued 3 plans

$ clu queue list
POS  SLUG     STATUS  NOTE
1    bar      queued  plans/bar.md
2    baz      queued  plans/baz.md

In flight: foo (dispatched 14:32:05 UTC, lease until 15:02:05 UTC)
```

If the batch is atomic, the operator's "I queued 3 plans" mental
model survives a cron pop landing mid-flight, and `queue list` makes
the popped-but-running case visible without a separate `clu list`
trip.

## Locked design (do NOT re-litigate)

The full design is in [#18](https://github.com/smabe/end-of-line/issues/18).
Summary:

- **Multi-arg add**: `slug` positional → `nargs='+'` accepting
  `slugs`. Validate ALL (slug regex, plan-file existence, no dupes
  in pending, no dupes within the batch) before mutating. Any
  failure → reject the entire batch with a message naming the
  offender. Other slugs are NOT added.
- **Single `queue.mutate` window** for the batch — atomicity from
  cron's POV. The whole batch is one append (or one head-insert),
  not N sequential mutations.
- **`--front` semantics**: insert all in argument order at head, so
  `clu queue add a b c --front` → `[a, b, c, ...existing]`. NOT
  reversed.
- **Output shape**: one `queued at position N` line per slug in the
  order they were added; if N>1, a final `queued <N> plans` total.
  Backwards-compatible with the single-arg case (still prints exactly
  one position line).
- **In-flight hint source**: `registry.entries()` filtered to
  `cfg.project_root`, then per-entry `registry.load_entry_state`.
  Reuse the `reg_by_slug` dict `cmd_queue_list` already builds for
  status projection (cli.py around the `_queue_row` helper). Don't
  walk the registry twice.
- **In-flight hint placement**: after the pending table, before the
  `Recent failures:` section (if any). Sort by `started_at` ascending
  if multiple. No flag — always show when present, omit cleanly when
  empty.
- **In-flight hint format**:
  `In flight: <slug> (dispatched <HH:MM:SS UTC>, lease until <HH:MM:SS UTC>)`
  Times derived from `current_claim.started_at` and `lease_expires`.
  Plain ASCII, no emoji.
- **What "in flight" means**: registered to this project AND has
  `current_claim` AND not in pending queue (the not-in-pending check
  is technically redundant — if it's claimed, it was popped — but
  cheap belt-and-suspenders).
- **No queue schema change.** queue.history still records only
  failures (`removed | absorbed | abandoned`). Successful pops live
  only in the registry's plan state.json. Documented in the contract
  edit.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| impl | `queue-ux-hardening-impl.md` | argparse `nargs='+'` + batch validation/mutation in `cmd_queue_add`; in-flight cross-reference + footer in `cmd_queue_list`; new tests in `tests/test_queue_add.py` and `tests/test_queue_list.py`; `docs/operations.md` and `docs/contract.md` updates. | 2h |

## Failure modes to anticipate

- **Single-arg backwards-compat.** `clu queue add foo` (one slug)
  must work exactly as today, including exit code, output text, and
  position number. The `nargs='+'` change must not break the single
  case. Test explicitly.
- **Duplicate-within-batch detection.** `clu queue add a b a` should
  reject with a clear "duplicate slug 'a' in batch" message, NOT
  silently dedupe. The atomic semantic depends on the operator
  knowing exactly what they typed.
- **Mixed pre-existing + batch-internal dupes.** `clu queue add foo
  bar` where `foo` is already pending: existing duplicate check
  fires, batch rejected. Make the error message distinguish "X is
  already queued at position N" (existing) from "duplicate slug X in
  batch" (within-batch). Operator response is different.
- **Plan file missing for one of N.** Reject the whole batch with the
  offending slug named. Don't add the others. The "all-or-nothing"
  contract is the whole point of multi-arg.
- **`--front` with multi-arg ordering.** Easy to get wrong: a naive
  `for s in slugs: queue.insert(0, s)` reverses the input order
  (results in `[c, b, a, ...]`). The correct shape is one slice
  insertion: `data["queue"][0:0] = entries` or insert in reverse.
  Test the order explicitly.
- **In-flight hint with no registered plans.** Must omit cleanly —
  no blank footer line, no "In flight: (nothing)" message. The
  test for "queue is empty + no in-flight" must show the existing
  output verbatim.
- **In-flight hint when the in-flight slug somehow IS in pending.**
  Shouldn't happen (the pop sequence removes from pending before
  registering), but if it does, dedupe by slug — show in the table
  row's STATUS column, not the footer.
- **Stalled claim on the in-flight plan.** If `current_claim` exists
  but the lease has expired, `_project_state_status` already projects
  it as `STATUS_STALLED`. Should the footer still show it? Yes — the
  fact that it WAS dispatched is still relevant context for the
  operator, even if it's gone stalled. Test this case.
- **Multiple in-flight plans across one project.** Shouldn't happen
  with current single-pop-per-tick semantics, but the iteration must
  handle N gracefully. Sort by `started_at`. If two share the same
  start (test fixture artifact), preserve registry order as
  tiebreaker.
