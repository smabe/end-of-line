# bundle-recovery — ship #7 + #8 (clu recovers from stuck workers)

Two issues that together close the "what happens when a worker dies
weirdly" gap. #7 auto-detects known systemic failures (PATH bug, rate
limit, auth) and pauses with a distinct event. #8 is the manual
escape hatch for everything #7 doesn't pattern-match.

Both touch `state.py` (new `EVENT_*` constants) and the
operator-recovery story in `docs/operations.md`. The triage flagged
them as a coherent bundle.

Order is **#7 first** so it establishes the `EVENT_*` + reason-field
pattern that #8 mirrors. Reversing the order would force #8 to
invent the audit-trail shape on its own and #7 to retrofit.

## Locked design decisions

These were settled by operator in the session that wrote this plan;
don't re-litigate:

### #7 — Systemic-failure detection
- **Status:** reuse `STATUS_PAUSED` with a distinct event + reason.
  Do NOT mint `STATUS_PAUSED_SYSTEMIC`. The state machine stays boring;
  `clu resume` works unchanged.
- **Quiet hours:** the pause iMessage **bypasses quiet hours** (loud
  at 3am), reusing the halt-bypass plumbing. Don't invent a third
  notification gate.
- **Signature list governance:** hard-coded regexes in `dispatch.py`.
  No config-driven list, no `worker.signatures` field in
  `.orchestrator.json`. v1 = hard-code + grow via PR if a new
  signature shows up.
- **Cross-plan coordination:** independent observation. If plan A
  detects a rate-limit, plan B's next dispatch is NOT preemptively
  paused — it discovers the same failure on its own. N iMessages for
  one underlying problem is acceptable for v1.

### #8 — release-claim
- No open design questions. The issue body specifies the safety
  semantics (refuse if running + fresh heartbeat unless `--force`,
  accept `--reason` for audit, emit a distinct event).

## Per-phase done checklist (applies to both)

- TDD: failing tests first, then minimal implementation.
- `/simplify` after if diff crosses 1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood /
  Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- Close the GitHub issue from the worker after the commit. **Use the
  absolute path** for `gh` since the worker subprocess PATH is not
  reliable (we just filed #9 about exactly this):
  ```bash
  GH="$(command -v gh || echo /opt/homebrew/bin/gh)"
  test -x "$GH" || GH=/usr/local/bin/gh
  "$GH" issue close <N> --repo smabe/end-of-line --reason completed \
      --comment "Shipped in $(git rev-parse --short HEAD)."
  ```
  If `gh` still resolves nowhere, note it in your `clu complete`
  summary and the operator will close manually — don't block the
  phase on it.
- `clu complete --commit <sha>` with the actual SHA.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| systemic-failure | `bundle-recovery-systemic-failure.md` | Detect rc=127/rate-limit/auth in worker logs, pause with distinct event (closes #7) | 1.5h |
| release-claim | `bundle-recovery-release-claim.md` | Operator command + force/reason flags + audit event (closes #8) | 1h |
