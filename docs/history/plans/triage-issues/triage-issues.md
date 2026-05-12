# triage-issues — clu triages its own open backlog

clu has 8 open enhancements / questions on smabe/end-of-line (#1-#8).
The operator wants a structured triage pass — priority, effort, risk,
dependencies — before picking what to work on next. This plan does
that dogfood-style: clu dispatches one phase per issue, each phase
posts a triage comment on the issue, and a final synthesis phase
produces a ranked summary file.

This is also a live correctness test for the dispatch pipeline now
that the docs library has shipped. 9 phases, sequential, no commits
on the first 8 (gh comments only), one commit on synthesis.

## Triage format (all phases use this)

Every issue-N phase MUST post a single comment on its issue with
this exact structure:

```
## Triage assessment (2026-05-11)

**Priority:** P0 / P1 / P2 / P3 — one-sentence rationale
**Effort:** XS (<1h) / S (1-3h) / M (half-day) / L (multi-day)
**Risk:** Low / Med / High — what could go wrong
**Dependencies:** related issue numbers, prerequisites, or "none"
**Recommended approach:** 2-3 sentences on how you'd ship it
**Open questions:** anything unclear that would block kickoff, or "none"

— triaged by clu worker (phase: <phase_id>, token: <token-prefix>)
```

Priority guidance:
- **P0** — blocker for safe operation (data loss, security, can't recover from common failure). None of these 8 should be P0; flag if you think one is.
- **P1** — operator pain hit twice or more, or a foundation other issues depend on.
- **P2** — clear quality-of-life improvement, no current workaround pain.
- **P3** — nice-to-have, scope unclear, design discussion needed before implementation.

Effort: include test/docs/changelog time, not just code.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| Triage #1 logs <plan> | `triage-issues-issue-1.md` | Assess `clu logs` command | 5m |
| Triage #2 inbound auto-tick | `triage-issues-issue-2.md` | Assess inbound poller auto-tick | 5m |
| Triage #3 multi-plan routing | `triage-issues-issue-3.md` | Assess last-pinged routing | 5m |
| Triage #4 replan callback | `triage-issues-issue-4.md` | Assess replan design discussion | 5m |
| Triage #5 tick-all subcommand | `triage-issues-issue-5.md` | Assess `clu tick-all` promotion | 5m |
| Triage #6 prior-blocker helper | `triage-issues-issue-6.md` | Assess `clu prior-blocker` | 5m |
| Triage #7 systemic-failure detection | `triage-issues-issue-7.md` | Assess pause-on-rate-limit | 5m |
| Triage #8 release-claim escape hatch | `triage-issues-issue-8.md` | Assess `clu release-claim` | 5m |
| Synthesis | `triage-issues-synthesis.md` | Read all 8 comments, commit ranked summary | 10m |
