# queue-worker-callback — v2 worker-callback queue enqueue (closes #17)

v1 of `clu queue` shipped operator-only: the operator scribbles plan
slugs, the supervisor pops one per cron tick, the chain drains while
they sleep. v2 opens the write path to *workers* so a phase mid-flight
can chain a follow-up plan without an operator round-trip — same shape
as `clu spawn` for in-plan tasks, but lifted to project-level queue
enqueue.

The full persona pass lives in
[`.claude/plans/plan-queue-worker-callback.md`](../.claude/plans/plan-queue-worker-callback.md).
Phase workers must read it once for context, then **trust the locked
decisions below** — do not re-litigate token shape, fingerprint
approach, asymmetry rules, or storage choice.

Closes [#17](https://github.com/smabe/end-of-line/issues/17). Trigger
override: operator requested ahead of the 30-day v1-soak deadline
because the design is fully baked and no v1 quirks have surfaced that
would invalidate it.

## Locked design decisions

### Phase 1 — `foundation` (constants + schema extension)

- **New ExitCode:** `QUEUE_CAP = 11`. Verified next free integer
  (`WORKTREE_SETUP_FAILED = 10` is current max). Design doc said 8 but
  enum advanced post-design.
- **New event constants:** `EVENT_QUEUE_APPENDED = "queue_appended"`
  and `EVENT_QUEUE_REJECTED = "queue_rejected"`. Logged in the **source
  plan's** `events` array (not a separate project-level log) so audit
  history co-locates with the rest of the worker's actions.
- **Queue entry schema extension** — v1 entry already has
  `added_by: "operator"`. Extend (all four fields nullable, default
  `None` on operator path) with:
  - `source_plan: str | None`
  - `source_phase: str | None`
  - `source_token_fp: str | None` — `sha256(token).hexdigest()[:8]`
  - `reason: str | None`
- **Config default:** `DEFAULT_MAX_QUEUE_ADDS_PER_PHASE = 3` in
  `state.py`. New key in `empty_state` config block:
  `"max_queue_adds_per_phase": DEFAULT_MAX_QUEUE_ADDS_PER_PHASE`.
- **Cap counter derivation:** count over `queue.json` `queue + history`
  entries where `source_plan == X AND source_phase == Y`. No new state
  field — derivation keeps schema flat. Trade-off: requires reading
  queue.json before append (we'd do that anyway under the queue lock).

### Phase 2 — `cli` (argparse parsing only, no execution)

- **Worker-mode discriminator:** presence of `--token` switches to
  worker mode. In worker mode: `--plan` + `--phase` required, single
  positional slug required (operator multi-slug forbidden), `--front`
  forbidden. Operator mode: no `--token`, `--plan`, `--phase`; multi-slug
  + `--front` allowed (v1 behavior unchanged).
- **Mutual exclusion:** runtime checks inside `cmd_queue_add` (not
  argparse mutually-exclusive groups) — argparse groups don't compose
  cleanly with multi-arg-presence requirements. Error code on bad
  combos: `ExitCode.GENERIC` with explicit message
  ("--token requires --plan and --phase / forbids --front / requires
  single slug").
- **`--reason TEXT`** optional both sides. Lands on queue entry
  `reason` field and `EVENT_QUEUE_APPENDED.reason`.

### Phase 3 — `dispatch` (worker-mode body — happy path + claim validation)

- **Validation order under lock:** (a) slug syntax via
  `state.validate_slug`; (b) plan-file existence via
  `cfg.plan_dir/<slug>.md`; (c) registered-project check (same as v1
  operator path); (d) **state lock first, queue lock second** — open
  source plan's `state.json` via `st.mutate`, call
  `assert_claim_match(data, token, phase)`, append
  `EVENT_QUEUE_APPENDED` to source state events; (e) inside the same
  outer `with`, open `queue.mutate(queue_path)` and append the entry.
- **Decorator:** wrap worker-mode dispatch with
  `@_translate_claim_mismatch` so a bad token exits `CLAIM_MISMATCH`
  (4), mirroring `cmd_spawn`/`cmd_complete`/`cmd_task_done`.
- **Cross-project rejection:** the `--project` arg IS the source
  project; if the worker tries to point at a different project root,
  the claim won't match because the state file lives under
  `--project`. No separate cross-project check needed — token-match
  failure is the boundary.
- **Token fingerprint:** `hashlib.sha256(token.encode()).hexdigest()[:8]`.
  Computed once at append time. Raw token never persisted to queue.

### Phase 4 — `gates` (cap + idempotency + missing-file refusals)

- **Cap check:** inside the queue lock, count entries in
  `data["queue"] + data["history"]` with matching `source_plan` +
  `source_phase`. If `>= max_queue_adds_per_phase`, emit
  `EVENT_QUEUE_REJECTED` (in source state events) with
  `reason="cap"`, exit `ExitCode.QUEUE_CAP` (11). Operator path
  uncapped (mirrors `max_spawns_per_phase`).
- **Idempotency rules** (worker path; operator v1 behavior unchanged):
  - **Pending slug:** already in `data["queue"]` → OK no-op. Print
    `"already queued: <slug> (position N)"`. No event emitted.
  - **Done slug:** in `data["history"]` → exit
    `STATUS_TRANSITION` (7), message
    `"{slug!r} already ran in this queue; remove from history or pick
    a different slug"`. Worker hitting this is a bug.
  - **Running slug** (popped, in-flight — registered in registry with
    a live `current_claim`): OK no-op, same shape as pending.
- **Missing plan file:** `plans/<slug>.md` doesn't exist → exit
  `UNKNOWN_TASK` (6). Emit `EVENT_QUEUE_REJECTED` with
  `reason="missing_plan_file"`.

### Phase 5 — `render` (`clu queue list` source attribution)

- Worker-enqueued entries render with a second-line annotation:
  `  (from <source_plan>/<source_phase>)` indented under the main
  row. Reason (if present) on a third line: `  reason: <reason>`.
- Operator-enqueued entries render unchanged (v1 shape).
- `_queue_row` helper extended; table headers unchanged.

### Phase 6 — `docs` (sweep)

- `docs/contract.md` — queue entry schema gains the four new fields;
  worker-callback section gains `clu queue add` entry; new event
  constants documented; new exit code listed.
- `docs/architecture.md` — worker-enqueue flow + lock ordering rule
  ("state lock first, queue lock second").
- `docs/reference.md` — `cmd_queue_add` and `cmd_spawn` sibling
  entries reconciled.
- `README.md` — short "worker enqueue" callout under the queue
  section (one paragraph, link to the contract doc).

## Non-goals

- **No `clu queue revoke` worker undo.** Persona pass deferred this
  ("future hook if it becomes a real pattern"). v2 ships add-only.
- **No two-tier queue** (worker-priority vs operator-priority). One
  queue, `added_by` field discriminates rendering.
- **No `--front` for workers.** Preemption is operator-only intent.
- **No `clu queue clear`.** Doesn't exist in v1; not adding it.
- **No `EVENT_QUEUE_APPEND_DEDUPED` event.** Pending-slug no-op is
  silent in the audit log; operator-visible message is enough.
- **No project-level queue event log.** Events live in the source
  plan's `events` array — the worker's audit trail is already there.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood
  / Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- Call `clu complete --plan queue-worker-callback --phase <id>
  --token <T>` on success.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| foundation | `queue-worker-callback-foundation.md` | Exit code, event constants, schema fields, config default | 1h |
| cli | `queue-worker-callback-cli.md` | argparse flags + worker-mode validation | 1h |
| dispatch | `queue-worker-callback-dispatch.md` | Worker-mode happy path + claim/lock plumbing | 2h |
| gates | `queue-worker-callback-gates.md` | Cap + idempotency + missing-file refusals | 2h |
| render | `queue-worker-callback-render.md` | `clu queue list` source attribution | 1h |
| docs | `queue-worker-callback-docs.md` | contract/architecture/reference/README sweep (closes #17) | 1h |
