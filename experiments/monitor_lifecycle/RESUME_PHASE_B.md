# Resume prompt — paste this into the fresh session after `/clear`

Resume issue #69 (Monitor lifecycle experiment).

State on arrival: Phase A is done. Phase B is in flight — I just `/clear`ed
mid-test. Two producers (labels A, B) were running before `/clear`; their
PIDs and last tick timestamps are recorded in
`~/.cache/clu-monitor-lifecycle/{A,B}.{pid,log}`. Phase A results and
Phase B/C/D plan are in `experiments/monitor_lifecycle/findings.md` +
`experiments/monitor_lifecycle/README.md`.

What to do:

1. Run `python3 experiments/monitor_lifecycle/check_state.py` and report
   what it shows (PID alive? latest tick age?).
2. Wait ~30 seconds, run check_state.py again. If tick counts grew while
   alive=False, that's a contradiction worth noting. If counts grew while
   alive=True, the producers survived `/clear` as orphans.
3. Re-arm Monitor on a new producer stream (label C, interval 5, max-ticks
   30) to confirm Monitor still works in the fresh session.
4. Update findings.md Phase B section with concrete observations.
5. Clean up surviving producers: `pkill -f experiments/monitor_lifecycle/producer.py`.
6. Proceed to Phase C (/compact) using the same pattern; create a
   RESUME_PHASE_C.md before arming.
7. Then Phase D (SessionStart-hook arming) — design separately, the README
   has the protocol sketch.
8. Final writeup → `docs/research/monitor-lifecycle.md` per the operator's
   approved scope; one-line pointer in `docs/operations.md`.
9. Comment on #69 + #70 with the decision; close #69 if findings warrant.

Reference reading if needed:
- `experiments/monitor_lifecycle/README.md` — full protocol
- `experiments/monitor_lifecycle/findings.md` — running observation log
- `gh issue view 69` — original issue
- `gh issue view 70` — the dependent followup whose green/red hinges on this
