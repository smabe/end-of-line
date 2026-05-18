# plan-locator-migrate — rewire inbound poller + cmd_answer + Discord

You are phase `migrate` of the `plan-locator` plan. Rewire the three
callsites that currently walk the registry through the new locator:
`notify_imessage_inbound.poll_once`, `cli.cmd_answer`, and
`notify_discord_inbound.poll`. No new tests for the rewire; existing
tests for those callsites must remain green.

## Locked decisions (do NOT re-litigate)

See `plans/plan-locator.md`. Summary:

- Poller drops AMBIGUOUS / NOT_FOUND silently with a log line.
- CLI prints AMBIGUOUS candidates to stderr and exits non-zero.
- No lock changes; locator is read-only.

## Read first

- `end_of_line/state_locator.py` — what extract shipped.
- `end_of_line/notify_imessage_inbound.py:poll_once` — the current
  walk + dispatch.
- `end_of_line/cli.py:cmd_answer` — the operator-side path.
- `end_of_line/notify_discord_inbound.py:poll` (post-#11) — the
  third callsite; same shape as iMessage inbound.
- `tests/test_notify_imessage_inbound.py`,
  `tests/test_cli_cmd_answer.py`,
  `tests/test_notify_discord_inbound.py` — existing tests that may
  need mock-pattern updates.

## Produce

1. **No new failing tests for the rewire.** Existing tests are the
   spec — they must stay green. If a test breaks, the rewire is
   wrong.

2. **Implementation.**

   - `end_of_line/notify_imessage_inbound.py:poll_once`:
     ```python
     for row in new_rows:
         result = state_locator.find_blocker_for_reply(
             registry.entries(),
             row.text,
         )
         if result.variant != "FOUND":
             log.info("imessage inbound: dropping %r — %s",
                      row.text, result.variant)
             continue
         _shell_clu_answer(result.state_path, result.blocker_id,
                           result.answer_index)
     ```

   - `end_of_line/cli.py:cmd_answer`:
     ```python
     reply_text = args.answer if args.plan is None else f"{args.plan} {args.answer}"
     result = state_locator.find_blocker_for_reply(
         registry.entries(),
         reply_text,
     )
     if result.variant == "AMBIGUOUS":
         for cand in result.candidates:
             print(f"  {cand.plan_slug}: {cand.blocker_id}",
                   file=sys.stderr)
         return _die(ExitCode.GENERIC,
                     "ambiguous reply — pass --plan")
     if result.variant != "FOUND":
         return _die(ExitCode.UNKNOWN_TASK, result.variant.lower())
     # write answer into the resolved state file
     with st.mutate(result.state_path) as data:
         data["blockers"][result.blocker_id]["answer"] = result.answer_index
         st.append_event(data, EVENT_BLOCKER_ANSWERED,
                         blocker_id=result.blocker_id)
     ```

   - `end_of_line/notify_discord_inbound.py:poll`: same shape as
     iMessage inbound. Note that Discord has the Reply-UI path
     (`message_reference.message_id`) which short-circuits the
     locator — keep that path as-is; only the text-fallback path
     uses the locator.

3. **Acceptance.**
   - Full suite green at the post-extract count plus 0 (rewire
     adds no tests).
   - `grep -rn "for entry in registry" end_of_line/` returns
     exactly one match: `state_locator.py`.
   - Manual smoke: `python3 -m end_of_line.cli answer --help`
     doesn't regress.

4. **Commit + complete.**
   - Title: `plan-locator: phase migrate — inbound + cmd_answer
     call state_locator`
   - Stage: `end_of_line/notify_imessage_inbound.py`,
     `end_of_line/notify_discord_inbound.py`, `end_of_line/cli.py`,
     plus any test files whose mock patterns shift.
   - `clu complete --plan plan-locator --phase migrate --token <T>`.

## Failure modes to watch

- **Test mocks pointing at deleted helpers.** If a test patched
  `notify_imessage_inbound._find_or_halt_blocker`, update it to
  patch `state_locator.find_blocker_for_reply` instead. Don't
  re-introduce the helper just to keep a mock alive.
- **Discord Reply-UI path.** The `message_reference.message_id`
  lookup uses `notify_metadata.discord.message_id` on the blocker
  record — that's a direct correlation, not a locator call. Keep
  it as-is; only the text-fallback path goes through the locator.
- **Exit code for AMBIGUOUS.** Use `ExitCode.GENERIC` (1) and
  print to stderr; don't invent a new exit code.
- **Slug + answer concatenation.** When `args.plan` is set,
  build `f"{plan} {answer}"` so `route_reply` (inside the
  locator) sees the slug-qualified grammar it already understands.
- **Last-pinged routing.** Out of scope; AMBIGUOUS surfaces all
  candidates; the deferred policy from `docs/architecture.md` Day
  2.4 stays deferred.
