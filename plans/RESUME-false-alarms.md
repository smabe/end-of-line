# Resume — false-alarms (approved 2026-08-21, execution deferred to a later session)

```
Resume the `false-alarms` plan: /plan false-alarms

It is APPROVED (2026-08-21) and NO code has been written. Four phases, in order,
p1 → p2 → p3 → p4. Start at p1; read `plans/false-alarms-p1.md` FIRST.

What it fixes: clu asserts two false things about its own state. The idle
watchdog false-fires on healthy workers (#115), and `clu init` / `clu queue add`
nag to install a monitor hook that IS installed (#116). Both teach the operator
to filter out clu's warnings, which is the real cost.

Three things the research established that the ISSUES DO NOT SAY, and that the
plan is built on — do not re-derive them, and do not trust the issue text over
the plan where they disagree:

1. The socket check named in #115 is dead by TIMEOUT before any of its three
   documented defects matter. Measured: `lsof -p <pid> -i` takes 15.29s against
   a hardcoded `timeout=1`, and the timeout path falls through to EMIT. It is
   also unrepairable — idle Claude sessions hold the same Anthropic sockets as
   busy ones, so no repaired form discriminates. p1 deletes it.

2. The DOMINANT cause is the window, not the socket check. The sample is
   appended with `now` and the window is checked against that same `now`, so the
   span is (N-1) intervals: 570s at the 20-sample cap and 30s cadence, against a
   600s requirement. Under continuous sampling it can NEVER be satisfied — it
   only becomes satisfiable after a sampling GAP, and gaps happen exactly when a
   tool was running. Every alert it can currently produce is a false one.

3. The activity marker leaks on every nonzero-exit Bash command. Probed: `echo
   ok` fires PreToolUse+PostToolUse; `exit 3` and `ls /nonexistent` fire
   PreToolUse and nothing else; a permission-DENIED call clears fine.
   `PostToolUseFailure` never fires at all. So wiring it — the obvious fix —
   would close nothing. p2's age bound is the only thing that closes it.

Rejected mechanisms, with evidence, so they are not re-proposed:
- SubagentStart/SubagentStop spans — field-unreliable (42% partial traces,
  missing stop events); a span that never closes makes the watchdog permanently
  deaf, which is worse than today.
- Worker log mtime — a real completed phase log is 2055 bytes written only at
  turn END, so mtime sits at dispatch time for the whole run.
- Transcript mtime — measured max inter-append gap 761s on a real transcript;
  `clu top`'s own 300s freshness threshold would call a live session dead.

Operator sign-off already recorded (do not re-ask): the five thresholds —
window 10.0 min, max sample gap 60s, CPU delta 1.0s, min samples 3, quiet-span
ceiling 45 min, all operator-configurable.

p3 exists because the OPERATOR pointed out that a dispatched `claude -p` worker
can `SendMessage` the operator's live session directly — proven this session,
including under the hardened `--allowedTools` list. The lesson generalised: the
worker KNOWS what it is doing, so it declares its quiet spans to clu over a
token-validated callback instead of being inferred at. p1's inference stays as
the floor for workers that declare nothing.

The one invariant that must not be lost: EVERY suppression this plan adds
expires on its own clock. Two designs in this space already failed by trusting a
close event that never came. A span or marker that can be left open forever is a
silence switch, and each has a test proving the EXPIRED case still alerts.

Read first:
- plans/false-alarms.md — Diagnosis (both probes, verbatim) and Background findings
- plans/false-alarms-p1.md — the NEXT phase
- ~/.claude/projects/-Users-smabe-projects-end-of-line/memory/project_false_alarms.md

Open questions or blockers: none. The plan is approved and every fork is settled.
```
