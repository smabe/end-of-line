# bundle-recovery-systemic-failure — detect + pause on systemic worker failures

You are phase `systemic-failure` of the `bundle-recovery` plan.
Implement GH issue #7: when the post-spawn fast-fail catches a
worker exit, inspect the log for known systemic-failure signatures.
On match, pause the plan with a distinct event and notify the operator
via the halt-bypass path. On no match, fall through to the existing
`dispatch_failed` behavior.

## Locked decisions (do NOT re-litigate)

Read `plans/bundle-recovery.md` for the full rationale. Summary:

- Reuse `STATUS_PAUSED` — do NOT mint `STATUS_PAUSED_SYSTEMIC`.
- Pause iMessage **bypasses quiet hours**, reusing halt-bypass plumbing.
- Signature list is **hard-coded** in `dispatch.py`. No config field.
- Each plan observes independently — no cross-plan preemption.

## Read first

- GH issue #7 body (full acceptance criteria):
  ```
  gh issue view 7 --repo smabe/end-of-line
  ```
- The triage comment on #7 (already posted; canonical recommendation):
  ```
  gh issue view 7 --repo smabe/end-of-line --comments \
      --jq '.comments[].body' --json comments
  ```
- `end_of_line/dispatch.py` — the fast-fail block at **dispatch.py:82**.
  This is where the new log-inspection branches before
  `_record_dispatch_failed`.
- `end_of_line/state.py` — `STATUS_PAUSED` at **state.py:58**,
  `EVENT_*` constants block starting at **state.py:71**. New event
  `EVENT_SYSTEMIC_FAILURE = "systemic_failure"` slots in adjacent to
  `EVENT_DISPATCH_FAILED` at line 82.
- `end_of_line/notify.py` — `KIND_HALTED` at **notify.py:25** and the
  halt-bypass code path it threads through. Reuse this — your new
  iMessage uses the same bypass; don't invent a third gate.
- `plans/.orchestrator/clu-docs.state.json` — read the real
  `dispatch_failed` events with `rc=127`. Those are the canonical
  example of signature 1 (missing binary) and will inform your test
  fixtures.

## Initial signature list (hard-coded)

Three signatures, ordered. Match on first hit:

1. **`missing_binary`** — `rc == 127` AND the log contains
   `command not found` (case-insensitive). Live evidence: the Day-3
   `claude`-binary PATH bug; the `gh`-PATH bug filed as #9.
2. **`rate_limit`** — log contains `rate limit` OR `429` OR
   `RateLimitError` (case-insensitive). Conservative: `429` alone is
   not enough — pair it with a keyword to avoid false positives.
3. **`auth_failure`** — log contains `401 Unauthorized` OR
   `AuthenticationError` OR `invalid api key` (case-insensitive).

Read the last 50 lines of the log (not the whole thing — a long log
shouldn't slow the supervisor) and match against the patterns
in-order. First match wins; record the signature name in the event.

## Produce

1. **Failing tests first.** New file `tests/test_systemic_failure.py`.
   Cover at minimum:
   - **`missing_binary` match** → status flips to `paused`,
     `EVENT_SYSTEMIC_FAILURE` event appended with `signature =
     "missing_binary"`, **attempts counter NOT incremented**, claim
     released, notify called with the halt-bypass kind.
   - **`rate_limit` match** → same shape, `signature = "rate_limit"`.
   - **`auth_failure` match** → same shape, `signature = "auth_failure"`.
   - **No match (generic bug-shaped exit)** → existing
     `_record_dispatch_failed` path; attempts increments, status stays
     `running`, no `EVENT_SYSTEMIC_FAILURE`.
   - **Multi-plan independence** — two plans both hit the same
     signature; both pause; neither preempts the other. (The unit test
     can simulate by spawning two state files in parallel and
     asserting the events on each are independent.)
   - **Long log truncation** — log is 5000 lines and the signature is
     in the last 50; still matches. (Don't read the whole file.)
   - **No log file** (edge case — fast-fail caught the worker before
     anything was written) → no match, existing path. Don't crash.
   Use `isolate_registry(self, tmp_path)` in `setUp`.

2. **Implementation.**
   - **`end_of_line/state.py`:** add `EVENT_SYSTEMIC_FAILURE = "systemic_failure"`
     adjacent to `EVENT_DISPATCH_FAILED` (state.py:82). No new `STATUS_*`.
   - **`end_of_line/dispatch.py`:** in the fast-fail block (around
     line 82, right before `_record_dispatch_failed`), call a new
     helper `_match_systemic_signature(log_path)` that returns a
     signature name (`"missing_binary" | "rate_limit" | "auth_failure"`)
     or `None`. On match, take the systemic-failure branch:
     - `with st.mutate(state_path) as data:` — flip
       `data["status"] = STATUS_PAUSED`, append
       `EVENT_SYSTEMIC_FAILURE` with `signature`, `phase`, `token`,
       `log_path` fields, and **clear `current_claim` without
       incrementing attempts**.
     - Call `notify.notify(cfg.notify, KIND_HALTED, body)` (reuse the
       halt kind — that's the bypass plumbing). Body example:
       `f"🚨 {plan}/{phase} paused — systemic failure: {signature}. Run `clu resume` once cleared."`
       (Add a `render_systemic_failure(plan, phase, signature)` helper
       in `notify.py` to keep `render_halted` style consistent.)
     - On no match, fall through to `_record_dispatch_failed` exactly
       as today.
   - **`end_of_line/notify.py`:** add the `render_systemic_failure`
     helper next to `render_halted` (notify.py:136). Same emoji
     pattern, same one-liner shape.

3. **`/simplify`** if the diff spans >1 module or >30 lines (it will —
   dispatch.py + state.py + notify.py + tests). Run it.

4. **Docs row.** Add a short section to `docs/operations.md` under the
   troubleshooting block: "Systemic failures clu detects" listing the
   three signatures + what operator action clears each. Reference the
   `EVENT_SYSTEMIC_FAILURE` event so operators reading the audit trail
   know what it means.

5. **Full suite green:** `python3 -m unittest discover -s tests`.

6. **Commit** (structured format). Stage explicit paths:
   `git add end_of_line/dispatch.py end_of_line/state.py end_of_line/notify.py docs/operations.md tests/test_systemic_failure.py`.

7. **Close GH #7:** use the PATH-defensive close pattern from
   `plans/bundle-recovery.md`.

## Constraints

- **No new `STATUS_*` constant.** Reuse `STATUS_PAUSED`.
- **No new notification gate.** Reuse `KIND_HALTED` for bypass.
- **No config-driven signature list.** Hard-coded only.
- **No cross-plan preemption.** Each plan observes independently.
- **No attempts increment** on systemic match — the phase isn't at
  fault. This is the whole point of the feature; if you accidentally
  increment attempts you defeat it.
- Don't widen the matched-signature surface beyond the 3 listed.
  Adding a fourth signature is a follow-up issue. Three is enough to
  prove the design.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan bundle-recovery --phase systemic-failure \
    --token <token> --commit <sha>
```

## Escape hatch

`clu block` if:
- `KIND_HALTED` does not actually bypass quiet hours. The triage
  assumed it does; if you find it doesn't, surface and ask whether
  to (a) fix the bypass plumbing here vs (b) defer systemic-failure
  to a follow-up. Options: "fix bypass here", "ship gated for now",
  "halt plan".
- The fast-fail block in `dispatch.py` looks materially different from
  what the triage described (rc != 127, different control flow, etc.).
  Surface what you found and what your alternative impl would do.
- The signature patterns false-positive on the existing test suite's
  fixtures during your test run (i.e. a benign test log happens to
  contain "rate limit" in a comment string). Surface so the operator
  can decide whether to tighten the patterns or quote-escape the fixtures.
