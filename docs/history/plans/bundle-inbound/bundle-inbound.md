# bundle-inbound — ship #2 + #3 (smoother blocker reply UX)

Two issues that together close the rough edges in the
reply-via-iMessage round-trip. #2 auto-ticks after a blocker is
answered (no more 5-min cron wait — though with the new 60s cadence
that wait is already shorter). #3 routes ambiguous bare-digit replies
to the most-recently-pinged plan so the operator doesn't need to
remember the slug prefix when running a 3-plan fleet.

Both touch `notify_inbound.py`. Order is independent — picking
**auto-tick first** because it's smaller, isolated, and validates the
pipeline before the heavier routing change.

## Locked design decisions

### #2 — Inbound poller auto-tick
- **Opt-out key:** `notify.inbound_auto_tick` (bool) in
  `.orchestrator.json`. Default `true` (better UX).
- **Spawn pattern:** `subprocess.Popen([...], stdout=DEVNULL,
  stderr=DEVNULL, start_new_session=True)` — fire-and-forget. Mirror
  the existing `dispatch.py` worker-spawn pattern. Never `wait()`.
- **Failure of the auto-tick must not stall the poller.** A bad
  `clu tick` exit is swallowed.
- **Auto-tick only fires when `_cli_dispatch` returned rc=0.** If
  routing or answer failed, don't tick.

### #3 — Multi-plan inbound routing (last-pinged wins)
- **Source of truth:** derive `last_blocker_notified_at` from each
  plan's existing `EVENT_PHASE_BLOCKED` events. **No new state field,
  no new registry mutation.** Reading 4-5 state.json files per
  ambiguous reply is negligible — replies are human-typing-rate, not
  high-frequency.
- **Slug-prefix routing wins.** `halt-bypass 1` always routes to
  `halt-bypass` regardless of last-pinged. Explicit beats inferred.
- **Bare-digit ambiguity resolution:** when 2+ plans have open
  blockers, pick the plan whose most recent `EVENT_PHASE_BLOCKED`
  timestamp is highest.
- **Fallback when last-pinged plan has no matching open blocker:**
  fall through to the next-most-recent-pinged plan with one. If still
  ambiguous (every plan with blockers was pinged at the same
  millisecond), refuse with a clear error — that's a theoretical
  case, not a real one, but don't silently misroute.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format.
- Stage explicit paths.
- Close the GH issue from the worker (PATH-defensive `gh` pattern —
  see `plans/bundle-recovery.md` for the snippet that worked).
- `clu complete --commit <sha>` with the actual SHA.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| auto-tick | `bundle-inbound-auto-tick.md` | Fire-and-forget tick after answered blocker (closes #2) | 30m |
| routing | `bundle-inbound-routing.md` | Last-pinged-wins for bare-digit ambiguity (closes #3) | 1.5h |
