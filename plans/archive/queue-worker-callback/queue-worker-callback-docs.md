# queue-worker-callback-docs — contract + architecture + reference + README sweep (closes #17)

You are phase `docs` of `queue-worker-callback`. Last phase. Sweep the
docs library so the worker-callback contract is discoverable from
authoritative entry points, and close #17 in the final commit message.

## Locked decisions (do NOT re-litigate)

See `plans/queue-worker-callback.md` § Phase 6. Summary:
- `docs/contract.md` owns schema + worker-callback list updates.
- `docs/architecture.md` owns the lock-ordering rule.
- `docs/reference.md` owns the per-command signatures.
- `README.md` owns a one-paragraph user-facing callout.

## Read first

- `docs/_outline.md` — structural contract for the docs library.
  Whatever you write must respect the §-ownership it declares.
- `docs/contract.md` — find existing queue schema section + worker
  callbacks list.
- `docs/architecture.md` — find existing "Queue advancement" /
  "Auto-repair worker" sections; lock-ordering goes adjacent.
- `docs/reference.md` — find `cmd_queue_add` and `cmd_spawn` entries.
- `README.md` — find the queue section.

## Produce

1. **No new tests** (docs-only phase). Run the full suite as
   regression guard at the end.

2. **Documentation updates.**
   - `docs/contract.md`:
     - Queue entry schema gains `source_plan`, `source_phase`,
       `source_token_fp`, `reason`. Note "nullable; operator-side
       entries leave them `None`". Document that `added_by` is
       `"operator" | "worker"`.
     - Worker-callback list gains `clu queue add` with the same
       cell shape as `clu spawn`: signature, exit codes, idempotency
       rules, cap rule.
     - Event list gains `EVENT_QUEUE_APPENDED` and
       `EVENT_QUEUE_REJECTED` (fields documented).
     - Exit-code table gains `QUEUE_CAP = 11`.
   - `docs/architecture.md`:
     - In the queue-advancement section, add a "Worker enqueue
       flow" subsection: validation order, lock-ordering rule
       ("state lock first, queue lock second — never reverse"),
       why the rule exists (no deadlock with cron's queue-pop
       path).
   - `docs/reference.md`:
     - `cmd_queue_add` entry gains worker-mode signature.
     - `_cmd_queue_add_worker` helper entry (sibling of `cmd_spawn`).
   - `README.md`:
     - In the queue section, add a paragraph: "Workers can chain
       follow-up plans mid-phase via `clu queue add <slug> --token
       <T> --plan <source> --phase <source-phase>`. The new plan
       lands in the project queue with lineage stamped (which
       plan, which phase, fingerprinted token). Per-phase cap is
       3 by default (`max_queue_adds_per_phase`). See
       [`docs/contract.md`](docs/contract.md) for the full
       contract."

3. **Acceptance.**
   - All four docs files updated; no broken cross-references.
   - Full suite green (regression guard):
     `python3 -m unittest discover -s tests`.
   - `grep -n "source_token_fp\|max_queue_adds_per_phase" docs/`
     confirms the new schema fields are documented.

4. **Commit + complete.**
   - Title: `queue-worker-callback: phase docs — contract/architecture/reference/README sweep (closes #17)`
   - Stage: `docs/contract.md`, `docs/architecture.md`,
     `docs/reference.md`, `README.md`.
   - The `(closes #17)` in the commit title triggers GitHub's
     auto-close on merge to main.
   - `clu complete --plan queue-worker-callback --phase docs --token <T>`

## Failure modes to watch

- **Docs-only commit but `/simplify` mandate** — `/simplify` is
  "after non-trivial logic changes." Docs-only doesn't qualify;
  skip `/simplify` for this phase.
- **`docs/_outline.md` ownership drift** — if the outline declares
  a §-ownership boundary that any of the above updates violates
  (e.g. "schema lives only in contract.md"), respect the outline.
  Adjust this phase's edits rather than amending the outline.
- **README scope creep** — keep the README addition to one short
  paragraph. The README is for the public pitch + install; the
  worker contract is project-internal detail. One sentence + a doc
  link is enough.
