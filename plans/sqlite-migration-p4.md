# sqlite-migration-p4 — native write ops for the hot paths

You are phase `p4` of the `sqlite-migration` plan. This phase delivers, as one commit, native single-purpose write operations for the paths that fire constantly — heartbeats, activity stamps, worker callbacks, dispatch stamps — replacing their `mutate_compat` round-trips (whole-state read + whole-state write) with row-level transactions. This is the phase that actually kills the "every heartbeat rewrites the event history" pathology. The facade keeps carrying every caller NOT named here (operator commands, cross-plan rules) until p7.

## Locked decisions (do NOT re-litigate)
See the master `plans/sqlite-migration.md`. The decisions binding this phase:
- Token validation becomes an in-transaction compare-and-set: `... WHERE plan_slug=? AND claimed_by=? AND phase_id=?`, rowcount 0 → `ClaimMismatch`. Same predicate as `assert_claim_match` (`state.py:770-783`), evaluated atomically. Exception TYPE stays `st.ClaimMismatch` — the CLI's `_translate_claim_mismatch` decorator and the heartbeat daemon's clean-exit branch (`heartbeat_daemon.py:135-136`) catch it by name.
- Every native op bumps `plans.version` in its transaction (p1 decision — it is the per-plan change hint dashboards poll on; an op that forgets makes a live plan look idle to `clu top`). It is NOT the p6 tick's correctness guard; that is a precondition set, recorded in p6's Decisions entry.
- Bounded-wait ops take `timeout_s` and surface `db.DbBusy`; `stamp_activity_marker` keeps its drop-on-contention contract (returns False, worker's Bash call never hangs).
- Events archive move happens ONLY at explicit archival: `cmd_archive` and the auto-archive rule copy rows to `events_archive` with `archived_at`, then delete from `events`, in the same transaction that flips the plan's status. **A halted or paused plan keeps its events in the live table** — operator decision at approval 2026-08-19: "halt or paused should stay in the live table so they can be resumed or archived later". This resolves the narrowing of upstream decision #4 that this shard previously flagged: "terminal" for archival purposes means archived, not merely halted. `clu retry` and `clu resume` therefore never need to pull rows back, and `attempts_for_phase` always reads a complete live history for any plan that can still run.

## Work

### Task 1 — native ops in the store
- end_of_line/plan_store.py — add the op layer. Work-shape sketch (interface half is contract):
  ```python
  def op_heartbeat(orch_dir, slug, *, token, phase, timeout_s=None) -> str
      # ONE txn: UPDATE claims SET last_heartbeat_at=? WHERE plan_slug=? AND claimed_by=? AND phase_id=?
      # rowcount 0 -> ClaimMismatch (claim gone/superseded); bumps plans.version; returns ts
  def op_activity(orch_dir, slug, *, token, phase, action, timeout_s=None) -> bool
      # start/end -> set/clear claims.active_tool_started_at; DbBusy -> False (drop)
  def op_stamp_claim_fields(orch_dir, slug, *, token, fields: dict) -> None
      # dispatch's pid/pgid/log_path/session_id stamping; CAS on claimed_by
  def op_append_events(orch_dir, slug, events: list[dict]) -> None
  def op_release_claim(orch_dir, slug, *, token=None, phase=None) -> dict | None
      # returns the released claim dict (for coolant snapshot); both-or-neither validation
      # exactly like release_claim (state.py:821-839)
  def op_stamp_attestation(orch_dir, slug, *, token, phase, kind, commit_sha) -> None
  def op_answer_blocker(orch_dir, slug, *, blocker_id, answer) -> None
  def op_add_blocker(orch_dir, slug, *, phase_id, question, options, context, blocker_type) -> str
  def op_spawn_task(orch_dir, slug, *, task, status) -> None
  def op_complete_task(orch_dir, slug, *, task) -> None
  def op_set_status(orch_dir, slug, *, status, event: dict | None) -> None
  def op_archive_events(orch_dir, slug) -> int    # events -> events_archive; returns moved count
  ```
  Each op is one `db.write_txn`; domain rules (heartbeat writes no event, `state.py:785-794`; blocker answer targets first unanswered, `state.py:1157-1169`) transcribe from the state.py functions they replace. `plan_store.dump_json` (p3) grows archive-awareness: it renders `events` plus `events_archive` so history survives archival in `clu state dump`. `plan_store.snapshot` does NOT — it queries the hot `events` table only, which is what keeps watch/top/locator on the lean table (upstream decision #4).
- end_of_line/state.py — `record_heartbeat`, `stamp_activity_marker`, `stamp_attestation`, `answer_blocker`, `add_blocker` dict-helpers remain for facade callers, but the CLI entry points below stop routing through `mutate`.
- Consumes: `db.write_txn(conn, *, timeout_s=None)`, `db.DbBusy` (p1); `plan_store.key_for_state_path(Path) -> tuple[Path, str]`, `plan_store.snapshot(orch_dir, slug) -> dict` (p3)
- Produces: the `plan_store.op_*` signatures sketched above (later phases consume `op_heartbeat`, `op_release_claim`, `op_append_events`, `op_set_status`, `op_stamp_claim_fields`, `op_archive_events`)

### Task 2 — hot-path callers switch to ops
- end_of_line/cli.py — the worker callbacks re-route: `cmd_heartbeat` → `op_heartbeat`; `cmd_activity` → `op_activity`; `cmd_verify`/`cmd_attest` stamp path → `op_stamp_attestation` + `op_append_events` (the 600s subprocess stays OUTSIDE any txn — same shape as today: `cmd_verify` at `cli.py:6402`, its `timeout=600` at `:6431`, and its stamp-after-the-run structure unchanged); `cmd_spawn`/`cmd_task_done` → task ops; `cmd_block` → `op_add_blocker`; `cmd_answer` → `op_answer_blocker` (+ its event); `cmd_complete`'s post-gate write → completion event + release via ops (the verify/simplify attestation GATE logic reads a `snapshot` first — gates unchanged); `cmd_notify_heartbeat_failure` / `cmd_notify_worker_dead` → their dedup-marker + event ops with bounded timeout (daemon exit path must not hang — today's contract, `state.py:578-582`). `cmd_archive` adds `op_archive_events` in its terminal transaction; the auto-archive path is `cross_plan_rules.auto_archive_rule` (`cross_plan_rules.py:517`), which reaches archival through `cli._perform_archive`.
- end_of_line/dispatch.py — `_stamp_pid` → `op_stamp_claim_fields`; `_release_with_failure` / `_pause_and_halt` / `_record_quota_fast_fail`'s state half → `op_append_events` + `op_release_claim` + `op_set_status` in one txn each (quota.json write stays file-based until p5 — the state-lock/quota-lock nesting note in the master still holds).
- end_of_line/heartbeat_daemon.py — `_ping` (`heartbeat_daemon.py:61-64`) → `op_heartbeat`; `ClaimMismatch` → clean exit unchanged.
- end_of_line/notify_imessage_inbound.py — `_shell_clu_answer` (`notify_imessage_inbound.py:152-161`) → `op_answer_blocker` (+ resolve via snapshot for the option-index translation, `state.py:1172-1180`).
- end_of_line/notify_discord.py — `_persist_metadata` → a blocker-metadata op (single UPDATE of `blockers.notify_metadata`).
- tests — heartbeat/activity/callback test families re-point where they asserted on write mechanics; behavior assertions unchanged.
- Consumes: `plan_store.op_heartbeat`, `op_activity`, `op_stamp_claim_fields`, `op_append_events`, `op_release_claim`, `op_stamp_attestation`, `op_answer_blocker`, `op_add_blocker`, `op_spawn_task`, `op_complete_task`, `op_set_status`, `op_archive_events` (Task 1)
- Produces: none (CLI surfaces unchanged)

## Decisions & findings

### Decision: coolant emission moves strictly AFTER the release transaction  *(status: active)*
- **Rationale:** today `release_claim_and_emit` shells out to coolant inside the state-lock window (`state.py:842-877` called from `supervisor.py:742-746` and cli handlers). Ops must not shell out inside a txn. `op_release_claim` returns the released claim's snapshot; callers emit coolant from it after commit. Ordering is preserved (durable state first, best-effort emit second — same order the supervisor already documents).
- **Alternatives considered:** keep emit-inside (violates the no-subprocess-in-txn rule this migration exists to establish).
- **Evidence:** state.py:842-877; supervisor.py:731-752.

### Finding: `st.utcnow()` is SECOND-resolution, and it made this phase's headline test unable to fail  *(empirical, this phase — binds p5, p6, p7)*
Three consecutive calls return the identical string. `claim_phase` stamps `last_heartbeat_at` with it, and the test then asserted `claim["last_heartbeat_at"] == op_heartbeat(...)` — comparing a value to itself, true before the op ran at all. **Demonstrated, not theorised:** mutating `op_heartbeat` to write `started_at` instead — breaking the heartbeat outright — left all 53 tests in its own file green and the full 2307-test suite green. The two-process test had the same hole at its exit assertion (`assertIsNotNone` on a value that was already non-null, so a write-back clobbering every beat would have passed). Both now backdate the claim's timestamps first, assert the value ADVANCED, and assert the neighbouring column a wrong-column UPDATE would land in is untouched. **Any later phase asserting that a timestamp column equals a just-computed value must backdate first, or it is writing a test that cannot fail.**

### Finding: the worker-death dedup marker has no reader anywhere  *(empirical, this phase — NOT fixed)*
`st.worker_death_reported()` exists, is documented as "the dedup marker the supervisor consults before firing its own operator notification — without it a single death pings the operator twice (daemon + tick)", and has **zero callers outside `state.py`**; the supervisor consults neither the claim field nor the corresponding event. So the second ping has always fired. The write could not have persisted either: `cmd_notify_worker_dead` stamped the marker onto the claim and released that claim in the same window, deleting the row before commit. Pre-existing and latent. Not fixed here — making the dedup work changes notification behaviour, which is beyond a storage migration whose premise is that behaviour is unchanged. The pointless write is gone; parked in the master.

### Finding: git subprocesses still run inside the project-wide write lock  *(empirical, this phase — p6/p7 own the fix)*
`_maybe_cleanup_worktree` shells out to git several times from inside a `st.mutate` window, reached from both `cmd_complete` and `_perform_archive`. That is p2's lock-consolidation shape, and no phase owns it. This phase moved `cmd_complete`'s copy to AFTER the completion commits and gated it on a worktree existing, but did not restructure the archive path — doing so needs a plan-field op this phase does not name.

### Finding: after archival, `snapshot` returns no events, by design  *(empirical, this phase)*
`clu archive` empties the hot table for that plan. Anything reading a plan's history afterwards must go through `dump_json`, which reads both tables. One existing test asserted on a post-archive audit event and now reads it through the dump path. This is upstream decision #4's intended contract, not a regression — but it is invisible until something reads an archived plan and sees nothing.

### Finding: five state.py helpers are now test-only  *(empirical, this phase — p7 decides)*
`record_heartbeat`, `mark_active_tool_start`, `clear_active_tool`, `mark_worker_death_reported`, `mark_heartbeat_loop_failing_notified` have no production callers left. This phase was told to keep them for facade callers; p7's deletion sweep should decide whether they follow the facade out.

### Finding: this shard's `state.py` line citations are pre-p3 and have drifted  *(empirical, this phase)*
`assert_claim_match` is at 844-856 (cited 770-783), `release_claim` at 895-913, `record_heartbeat` at 859-868, `stamp_activity_marker` at 1164; `heartbeat_daemon._ping` at 63-71. Symbol names all resolved; only the line hints rotted, exactly as the anchor-on-symbols rule predicts.

### Finding: spawn is ~28ms on this host, fast enough to hide contention  *(empirical, this phase — binds p6)*
An unsynchronised two-process test would have had the second process start after the first finished, so the two write patterns never overlapped and the test proved nothing. The two-process test uses a barrier. **p6's tick tests need the same treatment.**

### Decision: nine of twelve declared op signatures shipped different  *(status: active — INTERFACE DEVIATION, surfaced to the operator)*
- **What shipped:** `op_answer_blocker` and `op_spawn_task` return `str` where the sketch declared `None` (the id and the resolved text are minted inside the transaction, so the caller cannot know them otherwise); `op_stamp_claim_fields` returns `bool` where `None` was declared; seven ops gained OPTIONAL keywords (`events=`/`event=`, `release_token=`/`release_phase=`, `token=`/`phase=`, `if_unset=`, `timeout_s=`); and one undeclared public symbol shipped, `op_stamp_blocker_metadata` (Task 2 asks for "a blocker-metadata op" by description only).
- **Rationale:** each added keyword exists so a multi-write call site stays ONE transaction. Without them `clu block`, `clu complete`, both daemon reports and all three dispatch failure paths would each need two or three transactions and LOSE atomicity they have today — so the alternative is a correctness regression, not a smaller diff.
- **Why recorded rather than absorbed:** approved interface lines are contract, and the fix for a divergence is never to edit the plan to match what shipped. An independent reviewer checked the five symbols p5, p6 and p7 name on their `Consumes` lines one by one: a call written to the declared signature still works for every one, so no downstream phase is invalidated. The one live trap is `op_stamp_claim_fields`'s silent `None`→`bool`.

## Failure modes to anticipate
- An op that forgets to bump `plans.version` makes a changing plan look static to the dashboards' change detection — the op layer routes every write through one internal `_plan_txn(orch_dir, slug)` helper that bumps it, so forgetting is structural, not per-op.
- `cmd_complete` reads gates from a snapshot then writes — between the two, a concurrent release could swap the claim; the completion write is CAS-guarded on `claimed_by` so the stale path surfaces as `ClaimMismatch` exactly like today's re-load-under-lock would.
- The heartbeat daemon is `setsid`-detached and reaper-immune — its death-report path must pass a bounded `timeout_s` end-to-end (today: `st.mutate(timeout_seconds=…)`, `state.py:593-612`); a missed timeout strands the daemon.
- Facade/ops interleaving: an op's txn and a concurrent `mutate_compat` full-write are serialized by BEGIN IMMEDIATE, but a compat write-back constructed from a PRE-op snapshot would clobber the op's row — cannot happen inside one process (snapshot and write-back share one txn in `mutate_compat`, p3), and cross-process the txn ordering protects it; add a two-process test proving heartbeat-during-mutate is not lost.
- Blocker option-index resolution reads then writes — do it inside the op's txn, not around it.

## Done criteria
- **Escalated at p3 — every tolerant `except` clause this phase touches or adds is checked against what the STORE can raise.** This class has now recurred twice: p2 found eight `except OSError` guards that a database failure would have walked straight through, and p3 widened four more and STILL missed the zombie sweep's two, which its review caught. The shape is always the same — a clause that was complete for a FILE is incomplete for a database, because a broken store arrives as `sqlite3.Error`, contention as `db.DbBusy` (a `RuntimeError`, so `except OSError` misses it), and a newer schema as `db.SchemaTooNew`. The check: for every `except` guarding a store read or write in this phase's files, say which clauses were examined, which were widened, and which were deliberately left narrow and why. `db.DEGRADABLE_ERRORS` is the tuple for "degrade rather than fail"; it deliberately excludes the `RuntimeError` raised when WAL did not take.
- Observable: a SQL-trace test (sqlite3 `set_trace_callback`) proves `op_heartbeat` executes exactly one BEGIN IMMEDIATE, one UPDATE on `claims`, one `plans.version` bump, zero reads of `events` — the whole-history rewrite is dead on this path.
- Observable: two-process test — process A loops `op_heartbeat` while process B runs a `mutate_compat` cycle; no heartbeat lost, no `DbBusy` escapes at default timeouts.
- `cmd_archive` on a done demo plan moves its events: the test first asserts the plan has **at least 5 events** before archiving (an archive of nothing passes any count-matching assertion vacuously), then `events` count 0 and `events_archive` count equal to that pre-archive number; `clu state dump` still renders the full history (dump reads both tables) while `plan_store.snapshot` returns zero events for that plan (hot table only).
- **Carried in from p1's sweep — the `phase_started` event still carries NO `attempts` key.** `clu watch` prints `attempt 1` for every attempt because the event never held the count and the formatter falls back to 1 (`state.py:762`, `watch.py:121,334`); p1's golden froze that. This phase writes native ops for exactly these paths and is in a position to add the key incidentally, which would break p3's, p6's and p7's golden diffs and present as a migration regression when it is really a latent bug being fixed. Assert it: the event a claim writes has no `attempts` key. Fixing the bug is a separate change after this plan ships.
- Full suite green; basedpyright clean.
