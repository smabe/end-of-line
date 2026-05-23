# Resume prompt — paste this into the session after `/compact`

Resume issue #69 (Monitor lifecycle experiment) — Phase C.

State on arrival: Phase A and Phase B are done. Phase C is in flight —
the operator just `/compact`ed mid-test. Two producers (labels D, E) were
running before `/compact`; their PIDs and last tick timestamps are
recorded in `~/.cache/clu-monitor-lifecycle/{D,E}.{pid,log}` and the
pre-compact snapshot is at
`experiments/monitor_lifecycle/snapshot_before_compact.json`.

Phase B findings (already written) confirmed that `/clear`:
- does NOT reap orphan producer processes
- does NOT cancel armed Monitor tasks (they keep delivering until their
  own timeout)
- a fresh Monitor in the post-/clear session works normally

Phase C is the same probe for `/compact`. Hypothesis (worth confirming
or falsifying): `/compact` is a same-session in-place context compaction
— it should preserve Monitor tasks more cleanly than `/clear` (which is
a session reset). If even Monitor survives `/clear`, `/compact` almost
certainly does too — but verify empirically.

What to do:

1. Run `python3 experiments/monitor_lifecycle/check_state.py` and report
   PID alive + latest tick age for D and E.
2. Wait ~30 seconds, run again. Confirm tick counts grew while alive.
3. Did any pre-/compact Monitor events arrive in this post-/compact
   session? (Look for tick events from D or E delivered as
   task-notifications since the compact happened.) If yes — the prior
   Monitor task survived /compact. If no — it was cancelled or its
   listener is gone.
4. Arm a fresh Monitor on a new producer (label F, interval 5,
   max-ticks 30) to confirm Monitor still works post-/compact.
5. Update findings.md Phase C table + conclusion with concrete
   observations. Mirror the Phase B section structure.
6. Clean up: kill the recorded D/E/F PIDs (use the values in their
   `.pid` files — don't use `pkill -f`, the auto-mode classifier
   blocks it).
7. Decide whether Phase D (SessionStart-hook arming) is still worth
   running. If Phase B+C both show prior Monitors survive
   /clear+/compact, the hook may be unnecessary because the prior
   session's Monitor will deliver into the new context.
8. Final writeup → `docs/research/monitor-lifecycle.md` per the
   operator's approved scope; one-line pointer in `docs/operations.md`.
   (Draft only — operator-approval mandate.)
9. Comment on #69 + #70 with the decision; close #69 if findings
   warrant.

Reference reading if needed:
- `experiments/monitor_lifecycle/README.md` — full protocol
- `experiments/monitor_lifecycle/findings.md` — running observation log
- `experiments/monitor_lifecycle/RESUME_PHASE_B.md` — prior phase's
  resume prompt (for pattern)
- `gh issue view 69` — original issue
- `gh issue view 70` — the dependent followup whose green/red hinges
  on this
