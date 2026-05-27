# wedge-watchdogs-heartbeat-self-report — worker-side heartbeat-failure visibility

You are phase `heartbeat-self-report` of the `wedge-watchdogs` plan. Make the bash heartbeat loop in `end_of_line/skills/clu-phase/SKILL.md` count consecutive non-zero `clu heartbeat` exits and call a new `clu notify-heartbeat-failure` subcommand on the 3rd in a row. One commit.

## Locked decisions (do NOT re-litigate)

See `plans/wedge-watchdogs.md`. Summary:

- Worker-side, not supervisor-polling.
- 3-strike threshold (~6min at the existing 120s loop interval).
- Sidecar stderr log at `logs/heartbeat-errors.<plan>.<phase>.log` (under state-dir `logs/`, same convention as `attempt-context.*`).
- New CLI: `clu notify-heartbeat-failure --project ... --plan ... --phase ... --token ... --log <path>`. Token-validated. Idempotent via `heartbeat_loop_failing_notified` claim flag.
- Emits `EVENT_HEARTBEAT_LOOP_FAILING` + inbox `type=heartbeat_loop_failing` + side notify `KIND_HEARTBEAT_LOOP_FAILING`.

## Read first

- `end_of_line/skills/clu-phase/SKILL.md:103-114` — current heartbeat-ticker snippet; the change replaces this block.
- `end_of_line/cli.py:5251-5256` — `cmd_heartbeat` (the command whose silent failure we're surfacing). Already wears `@_translate_claim_mismatch` — mirror the decorator on the new subcommand.
- `end_of_line/state.py:163` — `EVENT_STALLED_CLAIM_NOTIFIED` constant (pattern to mirror for the new event).
- `end_of_line/supervisor.py:298-356` — `_emit_stalled_claim_notify` (mirror its idempotency stamp + inbox write + side-notify pattern; especially the rule "stamp inside mutate, fire IO outside the lock").
- `end_of_line/notify.py` — find `KIND_STALLED_CLAIM` + `render_stalled_claim` (model for new kind + renderer).
- `end_of_line/watch.py:63, 176` — `EVENT_STALLED_CLAIM_NOTIFIED` rendering + operator-filter membership (model the new pass-through after these).
- `tests/test_cli.py` — look for `cmd_heartbeat` style tests (mirror token-validation test shape).
- Project CLAUDE.md `Conventions (mandatory)` — `validate_slug` on external slug args, `EVENT_*` constants not raw strings, `with st.mutate(...) as data:` window, `--token` on every worker callback.

## Produce

1. **Failing tests first.** New `tests/test_notify_heartbeat_failure.py`:
   - `test_emits_event_and_inbox_on_first_call` — call with valid token, assert state has `EVENT_HEARTBEAT_LOOP_FAILING` event + inbox file present + `claim.heartbeat_loop_failing_notified == True`.
   - `test_idempotent_on_second_call` — call twice; exactly one event + one inbox row.
   - `test_token_mismatch_rejects` — bad token → `ExitCode.CLAIM_MISMATCH` (whatever `_translate_claim_mismatch` produces); no state mutation.
   - `test_log_path_passed_through_to_inbox` — inbox event `details.log_path` matches `--log` arg.
   - Use `CluTestCase` / `isolate_registry(self, tmp_path)` per `tests/__init__.py` patterns.

2. **Implementation.**
   - `end_of_line/state.py`:
     - `EVENT_HEARTBEAT_LOOP_FAILING = "heartbeat_loop_failing"` next to existing `EVENT_*` constants.
     - `mark_heartbeat_loop_failing_notified(claim) -> bool` — returns True if newly stamped, False if already True. Mirror `mark_tool_stuck_emitted`'s shape.
   - `end_of_line/notify.py`:
     - `KIND_HEARTBEAT_LOOP_FAILING = "heartbeat_loop_failing"`.
     - `render_heartbeat_loop_failing(plan_slug, phase_id, log_path) -> str` — short body, mention the sidecar path so the operator can `cat` it.
   - `end_of_line/cli.py`:
     - `@_translate_claim_mismatch` then `def cmd_notify_heartbeat_failure(args, cfg, state_path) -> int:` — `with st.mutate(state_path) as data:` window → `_validate_claim_match(data, args.token, args.phase)` → if `not mark_heartbeat_loop_failing_notified(claim)`: print + return OK; else `append_event(EVENT_HEARTBEAT_LOOP_FAILING)` + remember side-notify body. Inbox write + `notify.notify` happen OUTSIDE the mutate window (don't hold the lock across IO — mirror `_emit_stalled_claim_notify`'s pattern).
     - Wire subparser `notify-heartbeat-failure` with `--plan`, `--phase`, `--token`, `--log` args. Both `--plan` and `--phase` go through `st.validate_slug` per CLAUDE.md mandate.
     - Add the subcommand to the dispatch table inside `main()` (search for `"heartbeat": cmd_heartbeat,` and add alongside).
   - `end_of_line/watch.py`:
     - Add `EVENT_HEARTBEAT_LOOP_FAILING` to the renderer dict + the `--operator` filter set (alongside `EVENT_STALLED_CLAIM_NOTIFIED`).
   - `end_of_line/skills/clu-phase/SKILL.md` (lines 104-113 — replace the snippet):
     ```bash
     WORKER_PID=$PPID
     FAILS=0
     ERRLOG="$(dirname "$STATE")/logs/heartbeat-errors.$PLAN.$PHASE.log"
     mkdir -p "$(dirname "$ERRLOG")"
     ( while kill -0 $WORKER_PID 2>/dev/null; do
         if clu heartbeat --project "$PROJECT_ROOT" --plan "$PLAN" \
                 --phase "$PHASE" --token "$TOKEN" 2>>"$ERRLOG"; then
             FAILS=0
         else
             FAILS=$((FAILS + 1))
             if [ "$FAILS" -eq 3 ]; then
                 clu notify-heartbeat-failure --project "$PROJECT_ROOT" \
                     --plan "$PLAN" --phase "$PHASE" --token "$TOKEN" \
                     --log "$ERRLOG" >/dev/null 2>&1 || true
             fi
         fi
         sleep 120
       done ) &
     HEARTBEAT_PID=$!
     trap "kill $HEARTBEAT_PID 2>/dev/null" EXIT
     ```
   - Update SKILL.md surrounding prose (line 114 area) to mention the 3-strike self-report behavior + sidecar log path. Keep the existing "Both terminators are load-bearing" sentence (still true).

3. **Acceptance.**
   - All new tests green.
   - Full suite green: `python3 -m unittest discover -s tests` — report pre/post count delta.
   - `python3 -m end_of_line.cli notify-heartbeat-failure --help` shows the subparser.
   - `grep -c "notify-heartbeat-failure" end_of_line/skills/clu-phase/SKILL.md` returns 1 (the new call site).

4. **Commit + attest + complete.**
   - Title: `wedge-watchdogs: phase heartbeat-self-report — surface silent clu-heartbeat failures from the worker side`
   - Stage: `end_of_line/skills/clu-phase/SKILL.md`, `end_of_line/state.py`, `end_of_line/notify.py`, `end_of_line/cli.py`, `end_of_line/watch.py`, `tests/test_notify_heartbeat_failure.py`.
   - `/code-review` after staging — diff >1 file qualifies.
   - **Post-commit** (HEAD must be the SHA being attested):
     - `clu verify --plan wedge-watchdogs --phase heartbeat-self-report --token <T>`
     - `clu attest --simplify --plan wedge-watchdogs --phase heartbeat-self-report --token <T>`
   - `clu complete --plan wedge-watchdogs --phase heartbeat-self-report --token <T>`.

## Failure modes to watch

- **`record_heartbeat` raises beyond `ClaimMismatch`.** `cmd_heartbeat` IS decorated with `@_translate_claim_mismatch` (verified cli.py:5251), so claim drift returns a clean exit code. The silent failure surfaced in the wedge was most likely `LockTimeout` from `st.mutate(state_path)` under contention — which still propagates as a Python traceback → non-zero exit → swallowed by the loop. This plan doesn't fix the propagation; it makes any non-zero exit visible after 3 strikes. Document this in the commit's "Why".
- **`ERRLOG` directory must exist.** State-dir `logs/` is created lazily by `attempt-context.*` writers. The new snippet's `mkdir -p "$(dirname "$ERRLOG")"` is defensive — keep it. Don't strip it "as cleanup."
- **`$(dirname "$STATE")` assumes `$STATE` is exported.** Verify earlier in the SKILL.md that `$STATE` is set before this snippet executes. If not exported, the new bash will fail silently — would re-introduce the same kind of silent gap.
- **Inbox write inside `mutate` window.** Don't do it — mirror `_emit_stalled_claim_notify`'s pattern (mutate window stamps state, side-effects fire outside the lock). Holding the state lock across IO blocks every other supervisor call.
- **Subparser arg name collision.** `--log` is short and might collide with an existing flag. Check the global parser first; if so, use `--errlog`. Don't silently shadow.
