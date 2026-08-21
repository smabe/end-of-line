# mid-session-blockers — make the live "waiting on you" line answerable

`session-start-blockers` (`ab2a9aa`, `00f1178`) put open blockers, with options
and the `clu answer --blocker` invocation, into a session at start. A blocker
raised *while* a session runs still reaches it only through the watch Monitor,
as `slug/phase: BLOCKED q-1 — <question truncated to 100 chars>`
(`watch.py:113-119`; `_trunc` default 100 at `watch.py:100`). The choices are not
there, and neither is any way to answer.

The obvious framing — "widen the line" — is wrong, and that is the finding this
plan is built on. **`_fmt_blocked` cannot render options because the event does
not contain them.** `op_add_blocker` writes exactly
`{ts, type, phase, blocker_id, question}` (`plan_store.py:1030-1037`); options go
only to the blockers table (`plan_store.py:1013-1023`, `"options"` at `:1019`).
The formatter is a layer that lacks the information to be correct, so the fix is
to get the record to it — not to tune the render.

Phase 1 is unrelated to the feature and comes first because it protects the
stream everything else flows through: `_escape_msg` escapes backslash and quote
and nothing else (`watch.py:317-318`), while `_task_line` interpolates `msg` raw
into `msg="{msg}"` (`watch.py:321-331`). A worker that calls `clu block` with a
newline in its question breaks the protocol framing **today** — the quote never
closes and the consumer drops the tail as a non-`TASK_*` line
(`end_of_line/skills/clu-plan/SKILL.md:1133-1135`). No test covers it:
`_escape_msg` has zero test references, and only quote and backslash cases exist
(`tests/test_watch_task_protocol.py:192,205`).

Phase 2 gets the record to the text render and emits a block.

## Diagnosis

- **Hypothesis:** the mid-session line is thin because the formatter is blind —
  the `phase_blocked` event carries no options — not because the render was
  written badly.
- **Falsifiable test:** read `op_add_blocker`'s event insert and check for an
  options field; separately, confirm a multi-line payload survives the Monitor
  transport, since a render nobody receives intact is not worth building.
- **Test result:** CONFIRMED on both counts.
  (a) `plan_store.py:1030-1037` inserts `{ts, type, phase, blocker_id, question}`
  — no options.
  (b) **Probe, run 2026-08-21 during authoring. To re-run:** arm
  `Monitor(command="printf '%s\n' 'L1' '  L2' '  L3'; sleep 1; echo 'L4'")` and
  read the notifications. Observed: L1–L3 arrived as ONE notification, intact
  and ordered; L4 arrived as a SEPARATE one. So multi-line is safe and event
  boundaries hold. The same run showed **leading whitespace is stripped** —
  `  L2` arrived flush-left — so indentation cannot carry structure. This is an
  empirical finding about an undocumented transport; the official docs give a
  size limit for the WebSocket source only, and the repo's own
  `docs/research/monitor-lifecycle.md` covers `/clear`, `/compact` and
  concurrency but says nothing about output shape. Re-run the probe rather than
  trusting this line if it matters to a decision.

## Locked design decisions

### Phase 1 — msg-framing

- **`_escape_msg` also escapes `\n` and `\r`** to their two-character literal
  forms, so `msg="…"` is always exactly one line and the text survives instead
  of being cut at the newline. Escaping rather than stripping keeps the
  operator's wording recoverable by a reader.
- **Replacement order is load-bearing.** `_escape_msg` chains `str.replace`
  (`watch.py:318`); the backslash pass must stay FIRST or it double-escapes what
  the newline pass emits.
- **The invariant is asserted, not assumed:** one case per key in
  `_TASK_STATUS_MAP` (the literal at `watch.py:279-289` plus the three
  conditional keys at `:290-295` — note `:297-300` is a DIFFERENT map,
  `_TASK_VERBOSE_STATUS_MAP`), each with a newline-bearing fixture, asserting
  `"\n" not in project_event_task(...)`.
- **Independent of Phase 2.** A live defect on today's `main`; it ships on its
  own merits and would be worth doing if Phase 2 were cancelled.

### Phase 2 — blocked-render

- **Keep watch's first line exactly as it is; append below it.** Every line in
  the stream reads `slug/phase: …`, and `tests/test_demo_script.py:54-68`
  asserts `any(line.startswith(f"{slug}/{phase}: BLOCKED "))`.
- **Do NOT call `render_blocker` from watch.** Two independently disqualifying
  reasons, both checked: its answer command is hardcoded
  `clu answer --plan {slug} <choice>` (`state_blocker.py:113`), which is not the
  scoped form this plan exists to surface — and `--blocker` now REQUIRES
  `--project` (`cli.py:4932`), so the hardcoded string cannot be adapted by
  argument; and it opens with `❓ {slug}/{blocker_id} [{phase}]`
  (`state_blocker.py:127`), which is alien to the stream and fails the assertion
  above.
- **Extract, don't duplicate and don't consolidate.** Lift the option-block
  formatting and per-option truncation out of `render_blocker`
  (`state_blocker.py:114-118`) into a shared helper both it and `_fmt_blocked`
  call. `render_blocker`'s rendered output must stay BYTE-IDENTICAL — it feeds
  Discord (`cli.py:6798-6810`) — and a test must pin that. This is a
  rule-of-three extraction of one shared concern, NOT the
  single-renderer-plus-profile consolidation the Non-goals defer.
- **Pass the record in; do not widen the event.** `project_event(evt, slug, …)`
  (`watch.py:715-733`) receives only the event. The event log is append-only
  (`watch.py:591-594` — ids monotonic, never reused), so widening the payload
  would leave every blocker raised before the change unrenderable, while the
  blockers table is always current.
- **Where the options come from, and what it costs.** The poll loop does NOT
  hold plan state: `plan_store.snapshot` runs once BEFORE the loop
  (`watch.py:613`) into `baseline`, which is consumed at `:636-637` and never
  read again. The loop retains `keys[path]` (`watch.py:614`). So on encountering
  a `phase_blocked` event — and ONLY then — re-read with
  `plan_store.snapshot(*keys[path])` and find the row by `blocker_id` via
  `state.open_blockers` (`state.py:1108-1115`). This IS a new read inside a loop
  deliberately engineered so the idle case costs "one PRAGMA and no query at
  all" (`watch.py:659-660`), and the decision is that the cost is acceptable
  because it is paid only on a blocked event, which is rare and is already the
  moment a human is being asked to stop and act. Do not re-snapshot per tick.
- **Multi-line, and never indented.** Structure comes from explicit markers on
  each line — the probe showed leading whitespace is stripped in transit.
- **The answer command is a reserved field, not a trailing line.** Prior art is
  consistent: PagerDuty truncates SMS to 160 chars yet the action survives
  because it is a separate field
  (https://support.pagerduty.com/main/docs/sms-notifications), and its alerting
  principles state an alert carrying neither context nor action is useless
  (https://response.pagerduty.com/oncall/alerting_principles/).
  `clu_session_start.py:67` already reserves a footer budget for exactly this
  (`_BLOCKER_FOOTER_RESERVE = 180`, consumed at `:244`) — mirror that shape. The
  question shrinks first and the answer line is never sacrificed.
- **No option cap.** Render every option. Measured across every real blocker
  notification in this repo's history: 8 had two options, 21 had three, one had
  four — never more. And `notify_base.py:18`'s `REPLY_RE` matches a SINGLE digit,
  so index 10+ is unanswerable by digit reply and a blocker cannot usefully
  exceed ten options. A cap plus an omitted-count is machinery for a caller that
  does not exist; the per-option truncation inherited from the shared helper is
  the only bound needed.
- **Text and `--operator` only.** `--task-list` is excluded and the exclusion is
  enforced by the CLI: `cli.py:4457-4463` refuses the two together at runtime.
  The modes are separate functions in `stream_loop` (`watch.py:689-692`) and one
  process is only ever one of them, so the richer render cannot reach the
  protocol path.
- **Keep the block short, without pretending to know the limit.** The Monitor's
  tool schema states monitors producing too many events are automatically
  stopped, and an auto-stopped stream looks exactly like a quiet plan — but the
  threshold is undocumented, and the repo's own `docs/research/monitor-lifecycle.md`
  characterizes `/clear`, `/compact` and concurrency without recording anything
  about volume. Treat it as a reason not to pad the block, not a number to tune
  against. Measured real blockers render as roughly six lines, which is not near
  any plausible threshold.

## Non-goals

- **Not consolidating the blocker renderers into one.** Three live renderers
  exist — `state_blocker.py:105`, `clu_session_start.py:209`, `watch.py:113` —
  plus a fourth at `clu_inbox_surface.py:212` that is retired with the inbox
  surface (`415200b`), so it does not count. They serve different surfaces with
  different budgets and different answer commands, and the notify-path form
  `clu answer --plan <slug> <choice>` still resolves, because omitting
  `--project` is the documented host-wide default (`cli.py:4885-4889`) that
  `session-start-blockers` deliberately preserved. A single renderer plus a
  per-surface profile is the right end state and is a design pass of its own.
- **Not widening the `phase_blocked` event payload.** Rationale above: the log
  is append-only, so a payload change is invisible to every blocker already
  raised, and the blockers table is current truth for a question still awaiting
  an answer.
- **Not wiring the operator filter into `--task-list` mode.** That exclusion
  predates this plan and is stated in the refusal at `cli.py:4459-4462`; Phase 1
  hardens the protocol that mode depends on rather than extending it.
- **Not adding a re-ping for an unanswered mid-session blocker.** The peer case
  to surfacing it once, so it needs its rationale: a stuck-blocker re-ping
  already exists on the notify path (`supervisor.py:438-446`, `KIND_STUCK_BLOCKER`),
  and the evidence against a second one is strong — Alertmanager's
  `repeat_interval` defaults to 4h and suppresses repeats unless the alert set
  changed (https://prometheus.io/docs/alerting/latest/configuration/), and
  reminder acceptance falls ~30% per additional identical reminder in the same
  encounter (Ancker et al., https://pmc.ncbi.nlm.nih.gov/articles/PMC5387195/).
- **Not touching `_DEFAULT_VISIBLE`.** Grepped this session: its only reference
  outside its own definition is an assertion in
  `tests/test_supervisor_stuck_tool.py:379`, and `project_event` never reads it.
  Deleting dead code is unrelated cleanup that would make this diff harder to
  review; file it separately.
- **Not tightening the `msg=` cap test.** `tests/test_watch_task_protocol.py:219`
  is named `test_long_question_truncated_to_100_chars` but asserts
  `assertLessEqual(..., 120)` at `:229`; the 100 is documented
  (`docs/reference.md:1193`, `docs/operations.md:1793`) and implemented
  (`_trunc` default), but nothing pins it. Phase 1 must not move that number and
  must not tighten it either — a cap change is a behavior change that deserves
  its own diff.

## Files touched

- `end_of_line/watch.py` — P1, P2 modified — `_escape_msg` (P1); `_fmt_blocked`,
  `project_event`'s signature, and its `stream_loop` call site (P2).
  **API hotspot:** `project_event(evt, slug, *, verbose, operator)` has exactly
  ONE production caller, `watch.py:692`, plus two test modules.
  `tests/demo_script.py:211` calls `stream_loop`, NOT `project_event` — it is
  affected through the golden, not the signature.
- `end_of_line/state_blocker.py` — P2 modified — the option-formatting
  extraction only. **API hotspot:** `render_blocker` feeds Discord
  (`cli.py:6798-6810`); its output must not change by one byte.
- `tests/test_watch_task_protocol.py` — P1 modified — the newline invariant.
- `tests/test_watch_project_event.py`, `tests/test_watch_operator_filter.py` —
  P2 modified — the rendered block. Both already cover `EVENT_PHASE_BLOCKED`.
- `tests/goldens/watch-demo.txt` — P2 modified — line 5 is the blocked line.
  `tests/test_demo_script.py:44-52` compares the WHOLE LIST
  (`assertEqual(live, golden_lines())`), so a multi-line render INSERTS entries
  and shifts everything after — a rewrite of the file's tail, not a one-line edit.
- `tests/test_demo_script.py` — P2 modified — `:54-68` asserts the blocked line
  `startswith(f"{slug}/{phase}: BLOCKED ")`. Preserving watch's first-line
  grammar should keep it true; verify rather than assume.
- `docs/operations.md` — P2 modified — the watch output documentation
  (`:1683-1733`) only. Phase 1 changes escaping, not the cap, and touches no docs.
- `plans/mid-session-blockers.md` — P1, P2 modified — `## Findings log`.

## Per-phase done checklist

- TDD: failing tests first.
- `/code-review` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood / Tests /
  `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- **Stamp attestations AFTER the commit** — the gate compares stamp SHA to HEAD.
  - `clu verify --plan mid-session-blockers --phase <id> --token <T>`
  - `clu attest --simplify --plan mid-session-blockers --phase <id> --token <T>`
- `clu complete --plan mid-session-blockers --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| msg-framing | `mid-session-blockers-msg-framing.md` | Escape newlines in the task-list `msg=` field; assert one-line-per-event | 1.5h |
| blocked-render | `mid-session-blockers-blocked-render.md` | Extract the shared option renderer; emit question + options + answer command on the text path | 2.5h |

## Verification record

- grounding: 43 claims checked, 27 resolve · 13 fixed (a wrong ship SHA, four
  ±2 line drifts in `watch.py`, a `_TASK_STATUS_MAP` range that spanned a second
  map, the false "the stream loop already holds the snapshot", the false
  "`demo_script.py` calls `project_event`", a `msg=` cap the test does not
  actually assert, a retired renderer counted as live, and three uncited
  external claims now carrying URLs) · 0 promoted · 0 refuted
- executability: 5 + 7 named tests across 2 sub-plans, 19 Read-first pointers,
  9 Files-touched entries · 8 fixed (an acceptance check no Produce item could
  satisfy, an undefined lookup mechanism, a missing `tests/test_demo_script.py`,
  an uncovered docs edit, and the golden's whole-list comparison) · 0 promoted
- coherence: 11 stated rules against their mechanisms, 6 characterizations,
  7 cross-file restatements · 4 fixed, including one contradiction all three
  auditors reached independently

**The finding that reshaped the plan.** The first draft locked "reuse
`state_blocker.render_blocker`" while also requiring a `--project/--plan/--blocker`
command and forbidding any change to that function because it feeds Discord —
three constraints that cannot hold together, since `state_blocker.py:113`
hardcodes `clu answer --plan {slug} <choice>`. Reuse would also have replaced
watch's line grammar with `❓ {slug}/{blocker_id} [{phase}]` and broken
`tests/test_demo_script.py:54-68`. Resolved by keeping watch's first line and
extracting only the shared option formatting — which also removes the collision
with the "not consolidating the renderers" Non-goal.

**One decision carried to the operator and approved:** the render is now
multi-line where it was a single line.

**One decision reversed by measurement after the audit:** the draft capped
rendered options with an omitted-count. Real option counts in this repo are 2
(x8), 3 (x21) and 4 (x1), and `REPLY_RE` bounds a digit-answerable blocker at ten
options regardless — so the cap was speculative generality and was removed.

## Findings log

_(empty at plan time — workers append cross-phase findings as phases run)_
