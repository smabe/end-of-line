# Issue triage — 2026-05-11

Triaged by clu's `triage-issues` plan. 8 phases, one per open issue,
each worker posted a comment with priority / effort / risk / deps /
recommended approach. This file is the synthesis.

## Top 3 picks (ranked)

1. **#7 — Detect systemic worker failure and pause gracefully** (P1,
   S 1-3h). Already fired live twice in Day 3 — three consecutive
   `rc=127` dispatch_failed events on the `clu-docs` `architecture`
   phase plus a separate budget-cap incident — so it's the only P1
   with two real failure observations on the books, and it burns
   `attempts` 3× per phase across every plan in the fleet when it
   trips. It's also foundational: #2 (auto-tick) and #4 (replan) both
   want a "paused with reason" surface to build on, so landing #7
   first prevents parallel inventions of the same field. Med risk
   (signature false-positives) is real but bounded — start with the
   two signatures we've already observed.

2. **#5 — Promote `clu tick-all` to a subcommand** (P1, S 1-3h, Low
   risk). The smallest pure-win on the list: removes hardcoded paths
   from `examples/clu.tick.plist`, deletes the fragile shell parser in
   `examples/clu-tick-all.sh`, and every operator hits these on
   install. Purely additive Python, three tests, no design questions.
   Ship before the install pattern proliferates further.

3. **#1 — `clu logs <plan>`** (P1, S 1-3h, Low risk). The single
   most-hit operator-pain papercut today — every debugging session
   starts with `ls plans/.orchestrator/logs/ | tail` and a guess.
   `current_claim.log_path` is already stamped at `dispatch.py:127`,
   so the hard part is done. Read-only, no claim/token interaction,
   no deps. Punt `--follow` rotation semantics; ship the happy path.

## Full table

| # | Title | Priority | Effort | Risk | Depends on | Triage comment |
|---|---|---|---|---|---|---|
| 1 | `clu logs <plan>` | P1 | S (1-3h) | Low | — | [link](https://github.com/smabe/end-of-line/issues/1#issuecomment-4425071809) |
| 2 | Inbound poller auto-tick | P1 | S (1-3h) | Low | adjacent #3, #7 | [link](https://github.com/smabe/end-of-line/issues/2#issuecomment-4425081414) |
| 3 | Multi-plan inbound routing (last-pinged) | P1 | M (half-day) | Med | adjacent #2 | [link](https://github.com/smabe/end-of-line/issues/3#issuecomment-4425120710) |
| 4 | Replan worker callback — design | P3 | M brainstorm + XS-S impl | Low | should be decided alongside #6 | [link](https://github.com/smabe/end-of-line/issues/4#issuecomment-4425153057) |
| 5 | `clu tick-all` subcommand | P1 | S (1-3h) | Low | — | [link](https://github.com/smabe/end-of-line/issues/5#issuecomment-4425189669) |
| 6 | `clu prior-blocker` helper | P2 | S (1-3h) | Low | — | [link](https://github.com/smabe/end-of-line/issues/6#issuecomment-4425226985) |
| 7 | Detect systemic worker failure | P1 | S (1-3h) | Med | adjacent #2, #4, #8 | [link](https://github.com/smabe/end-of-line/issues/7#issuecomment-4425257995) |
| 8 | `clu release-claim` escape hatch | P1 | S (1-3h) | Low | paired with #7 | [link](https://github.com/smabe/end-of-line/issues/8#issuecomment-4425286226) |

## Bundling recommendation

**The Day-3 brainstorm flagged #1 + #5 + #6 as a possible small
bundle. After triage, the bundle still holds and is the strongest
candidate.** All three are read-side or additive subcommands in
`end_of_line/cli.py`, all S / Low risk, all share the same review
context (argparse wiring, `tests.isolate_registry`, a row in
`docs/reference.md` each). #1 + #5 are P1, #6 is P2 — bundling
upgrades #6's effective shipping urgency without rewriting the rank.
One plan, three phases, one PR.

**Secondary bundle: #7 + #8.** The #8 triage explicitly frames them
as defense-in-depth on the same operator-pain class — #7
auto-detects known systemic failures, #8 is the manual fallback for
the long tail (OOM, segfault, budget cap, novel signatures). Both
touch `state.py` (new `EVENT_*` constants) and `cli.py`, both unlock
the same recovery story. Could land either order, but landing them in
the same plan keeps the docs row and the operations.md troubleshooting
section coherent.

## Open design discussions

- **#4 — Replan callback** (`question` label). Status-vs-blocker
  shape is load-bearing and unresolved; the four questions in the
  issue body plus six more in the triage comment need a brainstorm
  pass before any code. Don't pick this up as a clu plan yet — open
  it as `/brainstorm` instead and produce a mini-spec in
  `docs/contract.md` first.
- **#7 — Quiet-hours bypass for systemic-failure pause.** The
  triage flagged an open question: does the pause iMessage bypass
  quiet hours like halt does, or stay gated like a normal pause?
  Halt-bypass shipped in Day 2.9 with a "loud at 3am" decision;
  systemic pause is less urgent. Decide before the impl phase fires.

## Recommended next move

**Kick off `bundle-operator-subcommands` as a clu plan (3 phases:
#1, #5, #6) — it's the lowest-risk way to validate that the
dispatch pipeline can ship a multi-phase code-producing plan, and it
clears the three daily papercuts in one PR.** Land #7 + #8 as the
next plan after that, once the bundle proves the pipeline. Open #4
as a brainstorm, not a plan.
