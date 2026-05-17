# queue-worker-callback-dispatch — worker-mode happy path + claim/lock plumbing

You are phase `dispatch` of `queue-worker-callback`. Fill in
`_cmd_queue_add_worker`: validate the worker's token against the
source plan's state, append a lineage-stamped entry to the queue,
emit `EVENT_QUEUE_APPENDED` in the source plan's events. NO cap
enforcement yet, NO idempotency edge cases yet — phase `gates` owns
those. This phase is the happy path + claim-mismatch + cross-project.

## Locked decisions (do NOT re-litigate)

See `plans/queue-worker-callback.md` § Phase 3. Summary:
- Lock order: **state lock first, queue lock second**.
- Reuse `state.assert_claim_match(data, token, phase)`.
- `@_translate_claim_mismatch` decorator on `_cmd_queue_add_worker`.
- Token fingerprint: `hashlib.sha256(token.encode()).hexdigest()[:8]`.
- `EVENT_QUEUE_APPENDED` in source plan's `events`, fields:
  `slug`, `source_phase`, `token_fp`, `reason`.

## Read first

- `end_of_line/cli.py:179-192` — `_translate_claim_mismatch` decorator.
- `end_of_line/cli.py:2874-2904` — `cmd_spawn` for the
  claim-validation pattern.
- `end_of_line/state.py:303-390` — `claim_phase` /
  `assert_claim_match` / `ClaimMismatch`.
- `end_of_line/config.py` — `ProjectConfig.queue_path` and state-file
  resolution helpers (find the per-plan state path resolver).
- `end_of_line/queue.py:49-52` — `queue.mutate` context manager
  (`locked_json` under the hood — first call creates the file).
- `tests/test_queue_add.py:_bootstrap` — fixture pattern.

## Produce

1. **Failing tests first**
   (`tests/test_queue_worker_dispatch.py`, new):
   - `test_worker_add_happy_path` — bootstrap source plan `feature-b`
     with a live claim on phase `c-extract`; ensure
     `plans/feature-c.md` exists; call worker add. Assert
     `ExitCode.OK`, queue.json has one entry with
     `added_by="worker"`, `source_plan="feature-b"`,
     `source_phase="c-extract"`, `source_token_fp` is 8 hex chars,
     `reason` matches input. Assert source plan's `events` has one
     `EVENT_QUEUE_APPENDED` with matching `slug`/`source_phase`.
   - `test_worker_add_no_reason_still_works` — reason omitted →
     `reason: None` in entry, no `reason` key (or `null`) in event.
   - `test_worker_add_token_fingerprint_is_sha256_prefix` — assert
     `source_token_fp == hashlib.sha256(token.encode()).hexdigest()[:8]`.
   - `test_worker_add_claim_mismatch` — wrong token →
     `ExitCode.CLAIM_MISMATCH` (4), no queue entry, no event.
   - `test_worker_add_wrong_phase` — token correct but
     `--phase` doesn't match live claim → `ExitCode.CLAIM_MISMATCH`,
     no entry, no event.
   - `test_worker_add_no_live_claim` — source plan exists but has
     `current_claim: None` → `ExitCode.CLAIM_MISMATCH`, no entry,
     no event.
   - `test_worker_add_unknown_source_plan` — `--plan` points at a
     plan slug with no state.json → `ExitCode.UNKNOWN_TASK` (6).
   - `test_worker_add_raw_token_not_in_queue` — read the entire
     queue.json bytes after a successful add, assert the raw token
     string does NOT appear (only the 8-char fingerprint).

2. **Implementation.**
   - `end_of_line/cli.py`: replace stub `_cmd_queue_add_worker` with:
     ```python
     @_translate_claim_mismatch
     def _cmd_queue_add_worker(args) -> int:
         slug = args.slugs[0]
         try:
             st.validate_slug(slug, kind="plan slug")
             st.validate_slug(args.source_plan, kind="plan slug")
             st.validate_slug(args.source_phase, kind="phase id")
         except st.InvalidSlug as exc:
             return _die(ExitCode.INVALID_SLUG, str(exc))

         cfg = load_project_config(_resolve_project_arg(args))
         source_state_path = <resolve via cfg + args.source_plan>
         if not source_state_path.exists():
             return _die(ExitCode.UNKNOWN_TASK,
                 f"no state for plan {args.source_plan!r}")

         plan_file = cfg.project_root / cfg.plan_dir / f"{slug}.md"
         if not plan_file.exists():
             return _die(ExitCode.UNKNOWN_TASK,
                 f"no plan file at {plan_file}")

         token_fp = hashlib.sha256(args.token.encode()).hexdigest()[:8]
         queue_path = cfg.queue_path()

         # Lock order: state lock outer, queue lock inner.
         with st.mutate(source_state_path) as state_data:
             st.assert_claim_match(state_data, args.token, args.source_phase)
             with queue.mutate(queue_path) as qdata:
                 qdata["queue"].append({
                     "slug": slug,
                     "added_at": st.utcnow(),
                     "added_by": "worker",
                     "position_at_add": "tail",
                     "source_plan": args.source_plan,
                     "source_phase": args.source_phase,
                     "source_token_fp": token_fp,
                     "reason": args.reason,
                 })
                 pos = len(qdata["queue"])
             st.append_event(
                 state_data, st.EVENT_QUEUE_APPENDED,
                 slug=slug, source_phase=args.source_phase,
                 token_fp=token_fp, reason=args.reason,
             )
         print(f"queued at position {pos}")
         return ExitCode.OK
     ```
   - Add `import hashlib` if not already present.
   - Find the exact helper to resolve the source state path
     (`config.load_project_config` + `cfg.state_path(slug)` likely;
     verify when reading the config module).

3. **Acceptance.**
   - 8 new tests green.
   - All previous-phase tests still green.
   - Full suite green.

4. **Commit + complete.**
   - Title: `queue-worker-callback: phase dispatch — worker-mode body + claim validation (#17)`
   - Stage: `end_of_line/cli.py`,
     `tests/test_queue_worker_dispatch.py`.
   - `clu complete --plan queue-worker-callback --phase dispatch --token <T>`

## Failure modes to watch

- **Lock acquisition order regression** — phase `dispatch` MUST hold
  state lock OUTER, queue lock INNER. Don't invert. The design's lock
  graph proof depends on this direction (queue-pop path acquires
  queue-only; worker-enqueue acquires state-then-queue; no cycle).
- **Decorator stacking** — `@_translate_claim_mismatch` must wrap the
  function BEFORE the `cmd_queue_add` dispatcher calls it. Place the
  decorator directly above `_cmd_queue_add_worker`.
- **Cross-project rejection** — there's no explicit check; the
  state-file resolution uses `--project` and the source plan's claim
  is bound to that state file. A worker passing a `--project` for
  a different project root will fail `assert_claim_match` naturally
  because the state file at that path won't have the worker's token
  in its `current_claim`. Test only the natural path; don't add a
  redundant explicit check.
- **`reason: None` in event** — `state.append_event` may or may not
  serialize `reason=None` cleanly; if it strips None values, test the
  no-reason case asserts the absence of the key rather than its
  presence as null. Verify when reading `append_event`.
