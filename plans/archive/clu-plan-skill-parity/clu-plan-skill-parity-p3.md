# clu-plan-skill-parity-p3 — pre-ship verification: auditors + dry-run prober

You are phase `p3` of the `clu-plan-skill-parity` plan. Add a verification step to clu-plan between drafting (Step 3) and presenting (Step 4): three read-only auditors plus a dry-run prober run over the in-memory drafts BEFORE the operator is asked to `ship`, and their outcome is recorded in the master draft. One commit, all in `end_of_line/skills/clu-plan/SKILL.md`.

## Locked decisions (do NOT re-litigate)

See the master `plans/clu-plan-skill-parity.md`. Binding here:
- **The step is lettered `Step 3b`, not a renumber.** External references pin clu-plan's step numbers (operator memory `~/.claude/projects/-Users-smabe-projects-end-of-line/memory/feedback_clu_plan_task_list_monitor.md` cites "Step 5"; the SessionStart hook cites sections). Steps 1-5 keep their numbers; the new step slots between 3 and 4 as 3b.
- **Everything is pasted, nothing is read from disk.** clu-plan drafts in memory (Step 3) and writes to disk only on `ship` (Step 5) — so the auditors and prober receive the draft text VERBATIM in their prompts. This also matches worktree reality: a prober's worktree branches from the default branch with tracked files only (code.claude.com/docs/en/worktrees), so unwritten drafts could never be read there anyway.
- **Self-contained + marker-free**, same as p2: adapted briefs live inline; no line-start `Plan slug:` / `Plan audit:` / `Plan work:`.
- Source material to adapt FROM (on this machine; adapt, never point): `~/.claude/skills/plan/SKILL.md` Mode 1 step 7 and `~/.claude/skills/plan/references/AUDIT_BRIEFS.md` (briefs 1-4).

## Read first

- `end_of_line/skills/clu-plan/SKILL.md` Steps 3-5 (@39264f8 — Step 3 :497, Step 4 :709, Step 5 :750; task-list protocol section :860; file is 1255 lines) — the seam the new step lands in, including Step 4's forced-binary presentation blocks the record must feed.
- `~/.claude/skills/plan/references/AUDIT_BRIEFS.md` — grounding / executability / coherence auditor briefs + the dry-run prober brief with report items (i)-(viii).
- p2's shipped boilerplate block (now real: fenced block at :369 @39264f8, first line `Researching for clu plan {slug}. Goal: {one-line goal}.`) — restate its citation and no-invoke lines in the auditor briefs.
- Master `## Background findings` — vocabulary mapping constraints.

## Work

- `end_of_line/skills/clu-plan/SKILL.md`:
  1. **New `Step 3b: Verify the drafts (mandatory — runs before the operator sees the master)`.** Adapt /plan step 7's machinery with clu vocabulary throughout — the re-mapping is: shard → sub-plan file; `## Work` → the sub-plan's `## Produce` items (tests/implementation/acceptance); Done criteria → the sub-plan's numbered `3. **Acceptance.**` item under `## Produce` (a list item, not a heading — :680 @39264f8) + master Per-phase done checklist; `## Files touched (overview)` → `## Files touched`; Locked decisions → `## Locked design decisions`; phase map → `## Sessions index` rows. Three auditors, all `subagent_type: "general-purpose"`, READ-ONLY over pasted drafts, dispatched in one message: **grounding** (every file:line / URL+section claim in master + sub-plans is opened and checked — a claim the auditor did not open a source for is reported as unchecked, never resolved; medium effort), **executability** (each sub-plan self-sufficient for a cold worker: files exist, symbols exist, Read-first pointers resolve, `## Files touched` matches the union of sub-plan file lists, Effort column parseable per the p1-corrected formats; medium effort), **coherence** (contradictions WITHIN the drafts, no sources opened, quotes both halves; low effort). Verify by execution, not plausibility.
  2. **Dry-run prober** (fires when the plan modifies existing code — read the draft `## Files touched`): one agent, `isolation: "worktree"`, session effort, receives the FIRST sub-plan's text pasted verbatim, attempts the work, and reports: what did not survive contact labeled SKETCH / APPROACH / MEASURED; files edited split LISTED / MISSING vs the sub-plan's file list; untouched listed items; the workaround confession ("did you work around any constraint to reach green?" — a yes is a design fork, never good news); behaviors the OLD code provided and where each is re-established. Route: MISSING file → add to the sub-plan AND `## Files touched`, no weighing; SKETCH → fix the draft; APPROACH → back to Step 2 research with the failure as the sharper question; MEASURED (an Acceptance check the probe produced and failed) → fix whichever of the design or the check is wrong. Keep the adaptation to these five channels — items (vii)/(viii) and the deferred-probe machinery are /plan features tied to its phase-start re-probe, which clu (cold workers, no resume mode) has no seam for; say so in a one-line note rather than silently dropping them.
  3. **One pass, blocking:** dispatch once, fix once, no second findings round; a finding not cleanly closeable is PROMOTED to the operator at Step 4 as a forced binary decision (drafted with the auditor's reading as default). Every fix is named. Exactly three carve-outs, each a first pass over work or text no agent saw rather than a second round, and the step's text names all three beside the rule so they cannot read as contradicting it: (1) an auditor that cannot report counts did not run — re-dispatch it; (2) a fix that invents a construct appearing in neither the draft nor the finding earns ONE scoped re-probe of the affected sub-plan section; (3) the Step 5 ship-guard re-run over sub-plans the operator changed after the pass (Work item 6).
  4. **`## Verification record` section added to the master template** (after `## Sessions index`), with a filled example: one line per auditor with counts ("grounding: N claims checked, N fixed, N promoted, N refuted"), one line for the prober's LISTED/MISSING split or `prober: not fired (no existing code modified)`. The record is written from reported counts, never intention; an auditor that cannot name what it checked did not run — re-dispatch it (the one permitted re-dispatch). Note in the template commentary: the section is inert to clu's parser (verified — parse_sessions_index reads only its own table) and must not begin any line with `Approval:`.
  5. **Step 4 presentation updated:** the "Here's the master" block adds one line summarizing the verification record, and a third bracketed variant for promoted findings: "**Verification finding needs your call:** <finding>. Drafted with <auditor's reading> as default."
  6. **Step 5 ship-time guard:** on `ship`, if any sub-plan changed after the verify pass (operator edits at Step 4), the affected auditor re-runs over the changed text before files land on disk — a first pass over text no agent saw, not a second round.

- Consumes: p2's adapted brief boilerplate block (its citation + no-invoke lines are restated inside the auditor briefs)
- Produces: the `## Verification record` master-template section and its line format (future clu masters carry it; nothing machine-parses it)

## Decisions & findings

**SHIPPED at a0d4693 (2026-08-10).** Worker findings, transcribed from its report:
- The prober brief gained a clause /plan's original never needed: the probe SKIPS the sub-plan's live `clu complete` / `clu verify` / `clu attest` callbacks — a clu sub-plan literally instructs firing real pipeline callbacks, which a probe must not do. clu-specific hazard, now in the shipped brief.
- The auditors' no-invoke line was widened to "and NOT to edit any file" (p2's "Research only" is wrong for an auditor); citation line restated in grounding + executability only, per the probe-settled reading.
- /plan's interfaces (g) and task-disjointness (h) auditor axes were DROPPED, replaced by a clu format check (Effort parseability incl. the `45m`/bare-int silent-None trap, phase-id slug validity) — the clu format deliberately has neither construct (master Non-goal 4).
- The worked example gained a filled `## Verification record` (same file; leaving it out would make the exemplar signal "the pass never ran" under the new legacy-draft rule).
- Parser citation refined to plan_parser.py:48-61 (the shard's :47-60 was one line off); fence balance verified (34 fences, even).

**Phase-start probe (2026-08-10, worktree, per the deferred-probe record): GREEN — no APPROACH/MEASURED failures, no MISSING files, fixture observable measured (2 phases parsed, record section inert).** Probe-settled readings — build these, they are validated:
- The `## Verification record` section lands BETWEEN `## Sessions index` and `## Findings log` in the master template.
- The Step 5 ship-guard is an UNNUMBERED PREAMBLE before item 1 — never a renumber; external references pin Step 5's item numbers (same rationale as the 3b lettering).
- The template commentary words the parser-inertness mechanism precisely: `parse_sessions_index` stops at the first blank line or `##` heading after the table rows (plan_parser.py:47-60).
- Auditor briefs: grounding + executability restate p2's citation AND no-invoke lines; coherence restates ONLY the no-invoke line (its no-source rule contradicts a citation requirement).
- The prober trigger keys on the `modified` tag in the draft `## Files touched`; prober "green" is the project's test gate (clu projects need no build).
- Add one line for legacy in-flight drafts: a master presented without a `## Verification record` (drafted before this step existed) gets the Step 3b pass run before presentation — the record's absence is not an exemption.

### Decision: verification runs over in-memory drafts, not written files  *(status: active)*
- **Rationale:** clu-plan's approval mandate is "no disk writes before ship"; /plan's write-then-check order exists for its draft-gate hook, which keys on markers clu masters must not carry (`Approval:`). Pasting drafts preserves both contracts and is what the briefs require anyway.
- **Alternatives considered:** adopting /plan's write-DRAFT-first flow — rejected: requires an `Approval:` marker whose line-start form is exactly what the draft gate matches, and clu masters must stay on the Sessions-index side of that discriminator.
- **Evidence:** clu-plan SKILL.md:230-237 (in-memory mandate); plan_draft_gate.py:926,934,1020-1022 (discriminator order) @3d51805.

## Failure modes to anticipate

- The record section or its example accidentally starts a line with `Approval:` — that is the draft-gate's plan-detection form; the Sessions-index exemption is checked first so a clu master survives, but keep the invariant clean: no line-start `Approval:` anywhere in the template.
- Auditor briefs inherit /plan vocabulary that clu sub-plans don't have (`## Work`, Done criteria, `Consumes:`/`Produces:` interface lines, `### Task` tiers) — every such term the executability auditor is told to check must exist in the clu template, or the auditor reports phantom gaps. Use the re-mapping table in Work item 1.
- The prober brief tells the agent to READ the plan file — it cannot (worktree has tracked files only; drafts are unwritten). Paste-only, stated in the brief itself.
- The new step's text pushes the task-list protocol section's line numbers further from the hook comment's citation — p1 already removed the line range; do not reintroduce one.

## Done criteria

- Produced observable: build a fixture master (in the scratchpad, not `plans/`) containing a `## Sessions index` with two rows AND the new `## Verification record` section with the filled example; run `end_of_line.plan_parser.parse_sessions_index` on it and show the output in the phase report: exactly the two expected phases, proving the new section is inert to the parser.
- Step 4's presentation block quotes the verification-record summary line and carries the promoted-finding variant, and Step 5 carries the changed-after-verify re-run guard (Work items 5-6, confirmed by reading the committed diff).
- `grep -nE "^(Plan slug|Plan audit|Plan work):" end_of_line/skills/clu-plan/SKILL.md` still returns nothing; no line in the file starts with `Approval:`.
- Step numbering 1-5 unchanged (`grep -n "^### Step" end_of_line/skills/clu-plan/SKILL.md` shows 1, 2, 3, 3b, 4, 5).
- Full suite green.
