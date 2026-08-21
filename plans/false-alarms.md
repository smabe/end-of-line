# false-alarms — stop clu asserting false things about its own state

## Phase map  *(the arc and the gates — work detail lives in each shard)*

**Phase p1 — idle watchdog: replace the signal, fix the window**  *(gate: the detector must become capable of a TRUE positive)*  — ✅ SHIPPED
- Enters when: start here
- Done signal: a busy worker driven through 20 real ticks emits no `worker_idle`; a dormant one does — both proven by observed event logs, not by hand-seeded samples
- If it fails: the cumulative-CPU signal doesn't separate the cases in practice → stop, return to research with the measured overlap as the sharper question. Do NOT tune thresholds to force a pass.
- Shard: `plans/false-alarms-p1.md`

**Phase p2 — activity marker: close the leak, delete the dead path**  — ✅ SHIPPED
- Enters when: p1 committed (p2 relies on p1's window predicate existing)
- Done signal: a stale activity marker can no longer silence the watchdog forever — proven by a claim past the age bound emitting `worker_idle` where today it stays silent
- If it fails: no gate — fix-forward
- Shard: `plans/false-alarms-p2.md`

**Phase p3 — workers declare their quiet spans instead of being guessed at**  *(gate: must not become a silence switch)*
- Enters when: p2 committed — a real dependency, not just file serialization: p3's span suppression sits beside p2's bounded activity marker in the same predicate and joins the same compare-and-set set
- Done signal: an open unexpired span suppresses the alert, and an EXPIRED span does not — the second half proving the design cannot be left open forever
- If it fails: the span leaks or the notice proves too noisy → keep p1's inference as the sole path and drop the declaration; p1 and p2 already stand alone
- Shard: `plans/false-alarms-p3.md`

**Phase p4 — hook-installed predicate: derive it from the file that decides it**
- Enters when: p3 committed (both touch `cli.py`; serialized to avoid a conflict)
- Done signal: on THIS machine — hook present in `settings.json`, `monitor` table empty — `clu init` prints no install tip and the `/clu-monitor` check reports installed
- If it fails: no gate — fix-forward
- Shard: `plans/false-alarms-p4.md`

## Status & cold-start

**Approval: APPROVED 2026-08-21**  *(approved for execution in a LATER session — no code was written in the drafting session)*
**Authored at: 5bf714e**

*Every `file:NNN` line hint in this plan and its shards was measured at `5bf714e` and is a
secondary hint only — re-anchor by grepping the named symbol, never by trusting the number.*

Verification — TWO passes, because the plan was restructured between them.

**Pass 1, 3 phases** (grounding · executability · coherence) — ~90 claims checked, 83 resolve ·
22 done criteria across 3 shards · 18 interface entries · 6 stated rules walked against their
mechanisms · 5 characterizations · 0 tiered phases · 0 upstream entries. **14 fixed, 1 refuted,
1 promoted, 0 uncheckable** — itemised below.

**Restructure between passes.** The operator established that a dispatched worker can message
a live session directly (probed: two messages delivered, including under the hardened
`--allowedTools` list). A new phase **p3** — workers declare quiet spans over a token-validated
callback — was inserted, and the former p3 became **p4**. Every "p3" inside pass 1's numbered
fix list below means what is now p4.

**Pass 2, 4 phases** (executability · coherence re-run over the restructured set; grounding not
re-run — p3's new claims are the two `SendMessage` probes recorded verbatim in Background
findings, plus `plan_store.py:115,558`, `config.py:150-151` and `end_of_line/hooks/clu_session_start.py`,
all opened and confirmed directly this session) — 27 done criteria across 4 shards · 15 interface
entries · ~10 stated rules against their mechanisms · 3 characterizations · 6 cross-file
restatements · 0 tiered phases · 0 upstream entries.

**Pass 2 — 10 fixed, each named:** (1) the master promised both issues would close but no
phase's criteria mentioned a closing keyword → added to p3 (#115) and p4 (#116). (2) `Fixes #115`
was placed on p2, which would have closed the issue with a third of the fix unwritten → moved to
p3, the last of the three phases that complete it, with p2 reduced to a bare reference. (3) four
files p3 edits — `supervisor.py`, `state.py`, `config.py`, `plan_store.py` — carried no P3 tag in
Files touched → tagged. (4) three test files kept their pre-renumber P3 tag → retagged P4. (5)
p3's enter-gate claimed mere file serialization when it has a real functional dependency on p2's
predicate → restated. (6) p1 cited `_emit_stuck_tool`'s comparison with no location a worker
could resolve → grounded on `supervisor.py:598-604` and `config.py:150-151`. (7) p2's marker age
bound named no source for its threshold → derived from `config.stuck_tool_threshold_seconds`
(`config.py:150`), so the two watchdogs cannot disagree. (8) p4 said "the SessionStart basename"
without naming it → `clu_session_start.py`. (9) a Non-goal excluded three peer surfaces (inbox,
notify channels, queue/dispatch) with no safety rationale → rationale written. (10) two p2
behaviours had no covering criterion — the compare-and-set membership and the dropped-stamp
logging → criteria added.

**Pass 2 — 1 refuted (cited):** the coherence auditor reported p3's three `Produces:` entries as
unclaimed by any later shard. Correct and expected: p3 is second-to-last and p4 consumes nothing
from it. A `Produces:` with no downstream consumer is not a defect.

**Pass 2 — 1 promoted to approval (exit b), now RESOLVED:** p3's
`worker_quiet_span_ceiling_minutes` default (45) joined p1's four thresholds as an
operator-facing sensitivity decision with no sign-off cited. Both were surfaced at approval and
the operator chose the drafted values — **operator sign-off 2026-08-21: `worker_idle_window_minutes`
10.0 · `worker_idle_max_sample_gap_seconds` 60 · `worker_idle_cpu_delta_threshold_seconds` 1.0 ·
`worker_idle_min_samples` 3 · `worker_quiet_span_ceiling_minutes` 45, all operator-configurable.**
These are now locked decisions rather than drafting defaults; a phase changing one is an approach
switch, not an implementation detail.

**Pass 2 — 1 already fixed before it was reported:** the four missing P3 tags in (3) were
corrected while the audit was mid-flight; the auditor read an unsettled snapshot. Recorded so
the count is not double-claimed.

**14 fixed, each named:** (1) master Done criteria claimed "three GitHub issues close" when
only #115 and #116 exist → corrected to two. (2) `dispatch.py:296` was listed among the four
stale comments the plan promises to fix, but no phase touched that file → added to p1's Work
and the master's Files touched. (3) split fact — `supervisor.py:703` and `:708` were both
cited as "where `cpu_samples` is written" → disambiguated (`:703` appends, `:708` registers
the store write). (4) master Files-touched still read "P2 adds the failure-clear flag" after
p2 was redesigned → corrected. (5) master Non-goals still read "`PostToolUseFailure` is NOT
excluded — folded into p2" → replaced with the probe-based exclusion. (6) p2's central design
rewritten on probe evidence: no new flag, no hook-config change, age bound promoted from
backstop to sole mechanism. (7) p2's Done criteria and Failure modes still described the
discarded failure-event design → cleaned. (8) p1's `ps` pid-recycling failure mode carried no
citation → grounded on `state.py:314-320` (#76), where this project already guards pid reuse.
(9) p3 named `_print_quiet_hours_coverage`; the function is `_print_quiet_hours_coverage_health`
(`cli.py:3271`) → corrected. (10) p1's `docs/architecture.md` scope missed `:191`, which names
`lsof` as load-bearing → added, and the overstated "implies tree-awareness" characterization
corrected to what `:50-52` actually says. (11) two master characterizations overstated the
measurements — socket-holding is a 3-pid sample, and the CPU separation was measured between a
*waiting* process and a *never-ran* one, not against a real wedge → both restated, and the
wedge assumption promoted from background fact to p1's explicit gate. (12) p3 asserted the hook
was "installed and firing" while its own failure mode says presence ≠ execution → reconciled.
(13) `tests/test_state.py` was tagged P1,P2 with no p2 Work item → retagged P1. (14) master
phase-map p2 done-signal described the discarded design → rewritten.

**1 refuted (cited):** the executability auditor reported 10 of 11 `Consumes:` entries as
UNPAIRED. By the template's own definition a `Consumes:` line names symbols that **already
exist**, and pairs with a `Produces:` line only when an earlier phase builds them — so
"unpaired" is the expected state for pre-existing code, and whether those symbols exist is the
grounding auditor's axis, which checked them.

**1 promoted to approval (exit b):** p1's four numeric thresholds set how sensitive an
operator-facing alert is, with no operator sign-off cited. Drafted values stand as the default;
surfaced as a decision below.

**0 uncheckable:** the auditor listed 4 claims it could not verify read-only — all four are
probes run in the drafting session (the hook-event probe, the subagent-token probe, and two CPU
measurements). Their verbatim output is recorded in Diagnosis and Background findings rather
than being asserted, so they are closed by evidence in the file, not left open.

### Execution record

**p1 SHIPPED 2026-08-21 `f4b2550`** — gate met with margin. The phase's branch-on-failure was the real
question (does cumulative tree CPU separate a wedge from a healthy worker?), and it does: driven
through 25 real ticks, a dormant tree moved 0.0000s while the LIGHTEST live rate measured
(0.15s/tick) moved 3.3000s against a 1.0s threshold — 3.3× clear. Full gate: 2394/2394 tests,
basedpyright 0 errors.

**Spec check at p1** — 1/1 task evidenced · interfaces conform · none unclaimed · +5 files added
at execution (`notify.py`, `cli.py`, `docs/reference.md`, `tests/test_supervisor_stuck_tool.py`,
`tests/test_supervisor_tick_restructure.py`), +2 at review (`tests/test_config.py`,
`docs/operations.md`), all re-evidenced. One interface note: `append_cpu_sample` gained a
required keyword-only `retain_seconds` — it appears on p1's `Consumes:` line in its pre-change
form, but p1's own Work section is what authorised changing its retention rule, so the shipped
signature implements the approved text rather than departing from it. The `Produces:` line —
`worker_idle_window_satisfied` with its four keyword thresholds, which p2 and p3 both consume —
shipped exactly as approved.

**Review at p1 — 2 findings, both confirmed by probe, both applied in the same commit.**
(1) `worker_idle_min_samples` was wired to the non-negative validator, so `0` loaded cleanly and
then crashed the tick with `IndexError` on an empty sample history — fixed with a positive-int
validator plus an empty-history guard in the predicate, and the load-path tests that were missing
entirely. (2) The contiguity rule silently disables the watchdog at tick cadences at or above the
max sample gap; **operator decision: document it, no detector** — the caution now sits beside the
`StartInterval` guidance in `docs/operations.md`. Threshold retune was never on the table (signed
off). Also operator-confirmed at this gate: the reworded idle notification, which had been
asserting a socket check that no longer exists.

**Downstream sweep at p1** — p2 1 finding carried in + 1 Done criterion added · p3 clean ·
p4 clean · code: p1 is the plan's first phase, so no earlier-phase source exists to have been
obsoleted; grepped the tree for anything else gating on the deleted socket check and found only
the operator-facing `lsof` hint in `notify.py`, which is advice for a human and stays.

The p2 carry-in is the one that matters: **p2's `cpu_samples` clear is NOT redundant with p1's
contiguity rule.** Contiguity voids a window whose sampling hole exceeds `max_sample_gap`, which
covers a long tool call; a Bash call SHORTER than that leaves a hole contiguity accepts. p2's
clear is what covers any-length tool calls. The two read as interchangeable and are not, so p2
now carries a Done criterion pinning the short-call case.

**p2 SHIPPED 2026-08-21 `8a3c0c2`** — the activity marker can no longer silence the idle watchdog forever.
Full gate: 2413/2413 tests, basedpyright 0 errors.

**Spec check at p2** — 1/1 task evidenced · interfaces conform · none unclaimed · +6 files added
at execution (`supervisor.py`, `activity_hook.py`, `tests/test_state_stuck_tool.py`,
`docs/reference.md`, `docs/architecture.md`, `skills_manifest.json`), all re-evidenced. The
`Produces:` line named "a bounded-suppression predicate consumed by `_emit_worker_idle`" in prose
and shipped as `state.activity_marker_suppresses(claim, now, *, max_age_seconds)` — conforming,
and now named concretely on p3's `Consumes:` line so the next phase is not left resolving prose.

**Review at p2 — 1 finding, confirmed by probe, applied in the same commit.** The age bound is
derived from `stuck_tool_threshold_seconds`, whose documented `0` means "detector disabled" — read
as "no bound", which made the suppression unbounded again and handed back the silence switch this
phase exists to remove, against the master's own plan-level invariant. Probed: a 30-day-old marker
suppressed at `bound=0`, did not at `bound=300`. The first-written test pinned the defect as
intended behaviour, which is why the suite was green. Fixed with a fallback bound equal to the
config default, plus four doc claims that stated the old behaviour.

**Downstream sweep at p2** — p3 3 findings carried in + `Consumes:` line resolved to the concrete
symbol + 1 Done criterion added · p4 1 Done criterion added + 1 vocabulary caution ·
code: p2 pinned `cpu_samples` to two writers, which obsoleted p1's shipped comment asserting the
tick was its only writer and the precondition-free append that rested on it — both corrected in
p2's own commit, which is the guard this question exists to find.

**Escalated to plan level by this sweep — a finding CLASS, not an instance.** Two consecutive
phases shipped a config threshold whose ZERO value silently removed a bound, and review caught
both while the full suite stayed green: p1's `worker_idle_min_samples` accepted `0` and crashed
the tick on an empty history; p2 inherited `stuck_tool_threshold_seconds = 0` and lost its
suppression bound. Both were invisible because every test exercised the value's USE site and none
its zero. **p3 and p4 now carry a Done criterion:** every config threshold a phase adds or reads is
tested at its zero / disabled value, with the test stating which direction is safe.

NEXT phase: **p3** — read `plans/false-alarms-p3.md` FIRST.

The decisions binding p3, pulled inline so a compaction that drops the shard still leaves them visible:
1. **A declared span is a LEASE, not a pair** — it carries an expected duration and expires on its own clock. This is the plan's most expensive lesson: the same "trust the close event" design was disqualified via subagent hooks AND found already broken in the shipped activity marker. A span that can be left open forever is a silence switch. Do not build one.
2. **This does NOT replace p1's inference — it sits in front of it.** Old skill versions, crashed workers and non-`/clu-phase` dispatches all exist, so p1 stays the floor.
3. **The 45-minute ceiling is operator-signed-off and configurable.** It is the only bound on how much silence a worker can claim, so changing it is an approach switch.
4. **Messaging the operator is BEST-EFFORT and never load-bearing** — no live session is the normal case, and a failed send must never fail a phase or change what the supervisor decides.
5. **Carried from p2:** decide what `0` means for the ceiling before writing it, and make the safe direction the one that SHORTENS silence.

*(Superseded — p2 is shipped. Its binding decisions are recorded in `plans/false-alarms-p2.md`.)*

The decisions binding p2, pulled inline so a compaction that drops the shard still leaves them visible:
1. **No new CLI flag and no hook-config change.** The probe removed the reason for one: `PostToolUseFailure` never fires, so there is no failure event to wire. The marker's AGE BOUND is the sole mechanism that closes the leak.
2. **The age bound derives from `config.stuck_tool_threshold_seconds`**, so the two watchdogs cannot disagree about when a tool call has gone on too long.
3. **Any new field gating suppression joins BOTH watchdogs' compare-and-set sets** — `_emit_worker_idle` and `_emit_stuck_tool` — or a mid-tick state change stops voiding a stale emit.
4. **Carried from p1:** the `cpu_samples` clear on tool START covers the short-call case p1's contiguity rule cannot. Do not delete it as redundant.

*(Superseded — p1 is shipped. Its binding decisions are recorded in `plans/false-alarms-p1.md`.)*

The three decisions that bound p1 (shipped — kept as the record of why it did what it did):
1. The `lsof` suppression is **deleted**, not repaired — measured 15.29s against a 1s timeout, so it has never completed; and idle sessions hold the same Anthropic sockets as busy ones, so no repaired form discriminates.
2. Liveness becomes **cumulative processor-time delta across the worker tree**, measured across the whole window, with fractional seconds preserved. Instantaneous `%cpu` is retired.
3. Samples are retained **by age, not by count** — this is what removes the cadence coupling that makes the current predicate unsatisfiable.

## Diagnosis

- **Hypothesis:** `worker_idle` false-fires because its suppression is broken (issue #115's three defects: `lsof` OR-semantics, wrong pid, hostname never resolves).

- **Falsifiable test:** run the exact call the code runs against a live worker pid and check (a) whether it is scoped to that process, (b) whether the string `anthropic` appears, (c) whether it completes inside the 1-second budget.

- **Test result — hypothesis CONFIRMED but INCOMPLETE; the stated cause is not the dominant one.**

  ```
  $ /usr/bin/time -p lsof -p 2113 -i | wc -l
  161            # 20+ unrelated processes: 1Password, rapportd, homed, Signal, opencode…
  real 15.29
  $ /usr/bin/time -p lsof -a -p 2113 -i | wc -l
  6
  real 0.06
  $ lsof -a -p 2113 -i | grep -ci anthropic
  0              # with 12 ESTABLISHED sockets to 160.79.104.10:https across 3 live pids
  ```

  All three documented defects reproduce. But the 15.29s runtime against `timeout=1`
  (`supervisor.py:723`) means the call **always** raises `TimeoutExpired`, and that path
  sets `lsof_text = ""` and falls through to EMIT (`supervisor.py:727-729`). The
  suppression was already dead by timeout before any of its three defects could matter.

  **The dominant cause is elsewhere, and it inverts the detector.** The sample is
  appended with `now` (`supervisor.py:703`) and the window is then checked against that
  same `now` (`supervisor.py:710`), so the newest sample's timestamp IS `now` and the
  measured span is `(N-1)` intervals. At `WORKER_IDLE_SAMPLE_CAP = 20` (`state.py:304`)
  and the 30s cadence (`docs/operations.md:169`) that is 570s, against the 600s
  `window_min` (`state.py:947`):

  > **570 < 600 — under continuous sampling the window can never be satisfied.**

  It becomes satisfiable only when sampling was INTERRUPTED, because
  `_emit_worker_idle` returns before appending whenever a tool is active
  (`supervisor.py:665-666`) and nothing anywhere clears `cpu_samples` — grepped, and the
  only two write sites are the append itself (`supervisor.py:703`, whose list mutation
  lives in `state.py:936`) and the store registration one line later
  (`supervisor.py:708`); there is no clear site anywhere. A gap is positive
  evidence that the worker was WORKING. So every alert this detector is capable of
  producing is a false one, and a genuine wedge — which samples densely — is the one
  case it cannot catch.

- **Second test — what actually creates those sampling gaps.** Probed this session with a
  hook registered on all three tool-exit events (`PreToolUse`, `PostToolUse`,
  `PostToolUseFailure`, matcher `Bash`) against a headless worker:

  ```
  echo ok           (succeeds)   → PreToolUse, PostToolUse      ← marker cleared
  exit 3            (exits 3)    → PreToolUse                   ← NEVER cleared
  ls /nonexistent   (exits 1)    → PreToolUse                   ← NEVER cleared
  Bash denied by --allowedTools  → PreToolUse, PostToolUse      ← marker cleared
  ```

  `PostToolUseFailure` did not fire in ANY case despite being registered. So the marker
  is left stamped by **any command that exits nonzero** — every failing test run, every
  failing build, every `grep` that matches nothing (exit 1). It is then overwritten by
  the next Bash call, because a START "overwrites freely" (`state.py:975`), so it only
  sticks permanently when a failing command is the phase's LAST Bash call. In between it
  suppresses sampling for the whole span, which is precisely how the gaps that make the
  window satisfiable get created. **The leak and the stale-window bug are one mechanism.**

  Note this also refutes the obvious fix: wiring `PostToolUseFailure` to clear the marker
  would change nothing, because it never fires. The age bound in p2 is not a backstop —
  it is the only thing that closes this.

## Non-goals

- **Not adding `SubagentStart`/`SubagentStop` activity spans.** This was the operator's
  initial choice and is deliberately reversed on field evidence: `SubagentStart`
  "frequently missing entirely" with 42% of 370 traces ending partial
  ([#27755](https://github.com/anthropics/claude-code/issues/27755)); subagents
  completing with no stop event, accumulating ghost sessions in exactly this kind of
  tracker ([#33049](https://github.com/anthropics/claude-code/issues/33049)); stops
  firing with no matching start ([#27423](https://github.com/anthropics/claude-code/issues/27423)).
  A span that never closes converts today's false alarms into permanent deafness, which
  is the worse failure for a plan whose goal is trustworthy warnings.

  **p3 reaches the same goal without the unreliable transport.** The reason subagent spans
  were attractive was that they would tell the supervisor "a review is running" — p3 has the
  worker say exactly that, over clu's own token-validated callback. The difference is not
  that the callback is more reliable in the abstract — it is that its failure is OBSERVABLE:
  the worker invokes it directly and sees a non-zero exit, where a hook event that never
  fires leaves nothing behind for anyone to notice. The rejection above is of the transport's
  invisibility, not of the idea.

- **Not using transcript-file freshness as a liveness signal.** Measured on a real 513KB
  transcript: median inter-append gap 0.2s but **max 761.6s**, with 9 gaps over 120s —
  `clu top`'s existing `SESSION_FRESH_SECONDS = 300` (`top.py:50-53`) would have declared
  that live session dead twice. Two further disqualifiers: without a `{session_id}`
  placeholder the transcript is selected by cwd (`top.py:120-143`), so a worker running in
  the project root can resolve to the OPERATOR's own interactive transcript; and under
  tmux the file reportedly is not written at all
  ([#70219](https://github.com/anthropics/claude-code/issues/70219)).

- **Peer-set exclusion — non-Bash tools keep no activity marker, and that asymmetry is
  safe** because the marker's only job is to scope which descendant processes are
  candidates for stuck-tool detection, and only a Bash call spawns descendants. Tools
  that spawn nothing have nothing to scope.

- **Not wiring `PostToolUseFailure`.** Scoped in originally on the exclusion-safety finding,
  then excluded on probe evidence: it did not fire for a nonzero exit, a failed command, or
  a permission denial (second Diagnosis test). Wiring an event that never fires would close
  nothing while reading as a fix. p2's age bound covers the leak instead.

- **Not touching `_emit_stuck_tool`'s own thresholds or detection logic.** p2 adds the
  new marker-clearing event to it where they share state, but its window and thresholds
  are already configurable (`config.py:150-151`) and are not implicated in either issue.

- **Not retiring the inbox surface further, not changing notification channels, not touching
  the queue or dispatch paths.** These are peers of the surfaces this plan does touch — p4
  edits the hook-install path that installs the inbox, and p3 adds a notification-shaped
  notice — so the exclusion needs its reason: none of them participates in either false
  assertion. The inbox's installed-ness is *reported* differently by p4 but its behaviour is
  unchanged; p3's notice is a live-session courtesy that deliberately does not route through
  the notify channels (see p3's second recorded decision); and the queue and dispatch paths
  neither read nor write the two facts this plan corrects.

## Files touched (overview)

- `end_of_line/supervisor.py` — P1, P2, P3 — P2 moves the idle gate onto the freshness predicate and adds the `cpu_samples` precondition (added at execution); P1 deletes the `lsof` branch, adds cumulative-CPU sampling and the fractional duration parse; P3 adds the quiet-span suppression check
- `end_of_line/state.py` — P1, P2, P3 — P1 the window predicate (age retention, contiguity, recency); P2 deletes two uncalled functions and bounds the marker; P3 adds `quiet_span_active`
- `end_of_line/config.py` — P1, P3 — P1 four idle thresholds mirroring the stuck-tool pattern; P3 the quiet-span ceiling
- `end_of_line/plan_store.py` — P1, P2, P3 — P1 drops `lsof` from the tick-transaction comment (added at execution); P2 window invalidation at the real activity write site; P3 the span write op
- `end_of_line/cli.py` — P1, P2, P3, P4 — P1 formats the now-float `Descendant` fields in `clu doctor`'s stuck-tool row (added at execution); P2 logs a dropped activity stamp instead of discarding it (no new flag — see p2's locked decisions); P3 adds the `clu quiet-span` worker callback; P4 replaces the hook-installed predicate and its two call sites
- `end_of_line/monitor.py` — P4 — per-surface predicates derived from `settings.json`; marker demoted to install metadata
- `end_of_line/skills/clu-phase/SKILL.md` — P2, P3 — P2 corrects the false env-inheritance claim at :350 and states the marker's real coverage limit; P3 adds the declare-before-review step and the best-effort operator notice
- `end_of_line/skills/clu-monitor/SKILL.md` — P4 — the "marker rows are the source of truth" claim is corrected
- `docs/architecture.md` — P1, P2, P3 — P2 corrects the claim that the activity stamp cannot false-trip a precondition (added at execution); P1 corrects what the watchdog samples (`:50-52`) and drops the stale `lsof` mention (`:191`); P3 adds the span as a suppression condition in the idle band
- `docs/contract.md` — P2, P3, P4 — claim-field documentation; the new worker callback; the `UserPromptSubmit` mislabel at :347
- `docs/operations.md` — P1, P2, P4 — P1 warns that raising `StartInterval` at or past the max sample gap silently disables idle detection (added at review, operator-decided); the activity-hook recipe's coverage limit; the unimplemented `isatty` refusal claim
- `tests/test_quiet_span.py` — P3 — new: span suppression, expiry, ceiling clamp, token rejection
- `end_of_line/dispatch.py` — P1 — comment-only: `:296` claims tree-awareness exists "so this doesn't false-fire WORKER_IDLE", which was never true of the socket check and is moot once it is deleted
- `end_of_line/activity_hook.py` — P2 — added at execution: the other entry point that discarded a dropped-stamp return
- `end_of_line/skills_manifest.json` — P2 — added at execution: the skill-sync test hard-fails on a missing bundled-SKILL hash
- `tests/test_state_stuck_tool.py` — P2 — added at execution: four tests called the helpers p2 deletes
- `end_of_line/notify.py` — P1 — added at execution: `render_worker_idle` asserted the socket check in the operator-facing warning itself
- `docs/reference.md` — P1, P2 — P2 drops the two deleted symbols and restates the idle gate (added at execution);
- `docs/reference.md` — P1 — added at execution: the `_emit_worker_idle` entry documented `%cpu`, the socket check and two deleted test seams
- `tests/test_config.py` — P1 — added at review: the four new thresholds had no load-path coverage, which is how a validator mismatch reached the diff
- `tests/test_supervisor_stuck_tool.py` — P1 — added at execution: three tests asserted the truncation this phase deletes
- `tests/test_supervisor_tick_restructure.py` — P1 — added at execution: passed the deleted `lsof_output` seam, and its idle fixture failed the new contiguity rule
- `tests/test_supervisor_worker_idle.py` — P1 — real-tick drives replace hand-seeded sample fixtures
- `tests/test_state.py` — P1 — window predicate cases
- `tests/test_activity_marker.py` — P2 — new: failure-path clearing and marker age bounding
- `tests/test_monitor.py`, `tests/test_cli_hints.py`, `tests/test_install_hook.py` — P4 — derived-predicate cases; the inbox-row-suppresses-dashboard-tip test is corrected

## Background findings  *(cross-phase; per-phase findings live in the shards)*

**The two watchdogs share one field and one precondition contract.** Both
`_emit_worker_idle` (`supervisor.py:747`) and `_emit_stuck_tool` (`supervisor.py:598-600`)
gate on `active_tool_started_at` through `require_claim_field`, which is what makes "a Bash
call starting mid-tick correctly cancels a worker-idle emit" (`plan_store.py:1537-1541`).
Any new field that gates suppression must be added to BOTH watchdogs' compare-and-set sets,
or a state change mid-tick will stop voiding a stale emit.

**Comments contradict the code in four places, and each is load-bearing.** (a)
`supervisor.py:670` says the sample reads "instantaneous %cpu"; `man ps` line 143 says
`%cpu` is "a decaying average over up to a minute of previous (real) time" — the code
depends on the false version. (b) `dispatch.py:296` says tree-awareness exists "so this
doesn't false-fire WORKER_IDLE"; tree-awareness covers only the CPU sum, never the `lsof`
call, which stayed on the root pid. (c) `state.py:968` and `state.py:981` document
`mark_active_tool_start` / `clear_active_tool` as what `clu activity` calls — both have
ZERO production callers (grepped); the live path is raw SQL at `plan_store.py:792`. (d)
`skills/clu-phase/SKILL.md:350` states subagent contexts don't inherit parent env so
`CLU_TOKEN` is unset in subagent hooks — disproved by probe this session: a `PreToolUse`
hook firing inside an Explore subagent (`agent_id` present, which per the hook docs occurs
only inside a subagent call) received `CLU_TOKEN` intact on Claude Code 2.1.238.

**`claims.flags` is a JSON catch-all, so no new claim field needs a migration.**
`plan_store.py:558` routes every claim key outside `_CLAIM_OWN_KEYS` into `flags`;
`PROJECT_SCHEMA_VERSION` stays 1. Both writers re-read and merge inside their own
`BEGIN IMMEDIATE` (`plan_store.py:866`), so there is no lost-update risk.

**Measured liveness separation, first-hand this session — and the limit of what it proves.**
Three live Claude Code processes sampled 30s apart moved 0.15s / 0.26s / 1.27s of
cumulative processor time; a fourth, which had consumed 0.00s total since launch, moved
exactly 0.00s. The lightest live one (0.26s over 30s ≈ 0.0087 s/s) was sitting idle at a
prompt, not actively working — so the separation measured is between *a process waiting*
and *a process doing nothing at all*. `_parse_duration` (`supervisor.py:94-100`) truncates
to whole seconds, which is exactly the band that separation lives in.

**What that does NOT establish:** no genuinely WEDGED worker was measured, because none was
available to measure. The design assumes a wedge resembles the dormant case rather than the
waiting one. That assumption is p1's gate rather than a background fact — its Done criteria
exist to test it, and if a wedge turns out to burn event-loop CPU like a waiting process
does, the signal does not separate and p1's branch-on-failure applies.

**A dispatched worker can message the operator's live session — probed, including under the
hardened recipe.** A headless `claude -p` sees `ListAgents` and `SendMessage`, resolves this
repo's interactive session by name, and delivers; two probe messages arrived in the drafting
session. It works with `--permission-mode dontAsk` and the hardened `--allowedTools` list
applied, because that list gates permission PROMPTS rather than tool availability — so no
operator config change is needed for p3's notice. Two limits define its role: a `SendMessage`
writes nothing to clu's database, so it cannot inform the supervisor's decision (which is why
p3 also adds a state-writing callback); and when the operator is away — clu's design centre —
`ListAgents` finds no interactive session at all, so it must never be load-bearing.

**Socket-holding was measured on 3 pids, not established in general.** Each of the three
live processes held ESTABLISHED sockets to `160.79.104.10:https`, including the one that
was merely idling at a prompt. That is enough to disqualify "holds an API socket" as a
*discriminator* — the idling process and the working ones were indistinguishable by it —
but it is a sample, not a proof about every Claude Code process.

## Done criteria  *(plan-level — each phase's own exits live in its shard)*

- `python3 -m unittest discover -s tests` green at every phase commit, and `clu verify`
  (which additionally runs basedpyright) green before the final push.
- Both GitHub issues close: #115 and #116 by `Fixes`. The neighbouring defects folded in
  are named in the commit bodies rather than filed as new issues.
- No comment, docstring, or doc line surviving this plan contradicts the code it describes
  — specifically the four listed in Background findings.
- A cold reader of `docs/architecture.md` and `docs/contract.md` can state correctly what
  the idle watchdog samples, how a worker declares a quiet span, and what decides whether the
  monitor hook is installed.
- **No suppression added by this plan can be left open indefinitely.** Both new ones — p2's
  bounded activity marker and p3's quiet span — have a test proving the expired case still
  alerts. This is the plan-level guard against trading a false-alarm bug for a silent-wedge
  bug, which is the one way this work could end up worse than what it replaces.

## Parking lot

(empty)
