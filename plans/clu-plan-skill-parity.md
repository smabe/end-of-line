# clu-plan-skill-parity — bring /clu-plan to parity with the rewritten /plan

## Phase map

**Phase p1 — format-contract corrections**
- Enters when: start here
- Done signal: the three documented-format lies are fixed and the parse measurement in the shard's Done criteria is produced; see `plans/clu-plan-skill-parity-p1.md`
- If it fails: no gate — fix-forward
- Shard: `plans/clu-plan-skill-parity-p1.md`

**Phase p2 — research-model rewrite (teams + stage-zero forks)**
- Enters when: p1 committed
- Done signal: Step 2 carries the adapted team model, the dispatch-gate run in the shard's Done criteria shows the new briefs are not denied; see `plans/clu-plan-skill-parity-p2.md`
- If it fails: if the gate denies a brief assembled from the new text, the marker rules in Background findings were violated — fix the text, not the gate
- Shard: `plans/clu-plan-skill-parity-p2.md`

**Phase p3 — pre-ship verification step (auditors + prober)**
- Enters when: p2 committed
- Done signal: Step 3b exists, the master template carries `## Verification record`, and the parser fixture measurement in the shard's Done criteria is produced; see `plans/clu-plan-skill-parity-p3.md`
- If it fails: no gate — fix-forward
- Shard: `plans/clu-plan-skill-parity-p3.md`

**Phase p4 — bundled /plan fork resync**
- Enters when: p3 committed (no output dependency — p4 touches a different file and adapts from upstream directly; sequenced last only because it is the largest)
- Done signal: the fork is a current-upstream single-file adaptation, the dispatch-gate fidelity run in the shard's Done criteria is produced; see `plans/clu-plan-skill-parity-p4.md`
- If it fails: no gate — fix-forward
- Shard: `plans/clu-plan-skill-parity-p4.md`

## Status & cold-start

**Approval: APPROVED 2026-08-10**
**Authored at: 3d51805**
**Drafting session: e7e5c0de-2708-4aa8-9868-d5915b29c24f**

VERIFICATION RECORD (read-back of the plan as written at approval, from the agents' reported counts):
- **Claims (grounding):** checked 46 claims — 42 resolve, 0 fail, 4 partial, 1 uncheckable (in-session research reports; every load-bearing fact they grounded was independently re-verified against source), 1 uncited (memory-file pin). Fixes, all corrections: "five Critical rules" → three (:686-707, :761-782); p2's boilerplate sentence now marks the unopened-source clause and effort-objection ban as ADDED from /plan, not existing clu-plan text; p3's Steps 3-5 range corrected to :226-641; p3's `### 3. Acceptance` corrected to the numbered `3. **Acceptance.**` list item (:398); memory-file path citation added.
- **Done criteria (executability):** checked 16 done criteria across 3 shards, 6 interface entries, 0 tasks in 0 tiered phases. Fixes: p3 gained a criterion covering Work items 5-6 (Step 4 presentation + Step 5 ship guard); p2 now pins brief 6's source file and the gate's stdin payload shape (verified plan-dispatch-gate.sh:131-137); Non-goal 3 gained its asymmetry-safety sentence; p2/p3 interface wording aligned ("restates"). Refuted with citation: three master criteria owned by no phase are plan-level ship-time exits by design — Mode 3 walks the master level (PLAN_TEMPLATES.md, Done criteria commentary); tagged "(ship-time)" for clarity.
- **Coherence:** checked 6 cross-file restatements, 8 behavior characterizations, 1 contradiction. The contradiction (p3 "dispatch once" vs the Step 5 ship-guard re-run) is fixed: the one-pass rule now names its three carve-outs inline. All 8 characterizations were execution-verified by the grounding pass this session.
- **Dry run (p1, worktree):** files LISTED 2 / MISSING 0; no workarounds; no SKETCH/APPROACH/MEASURED failures; observable produced and matched (`1h`→60, `2.5h`→150, `90min`→90, `1-2h`→120, `45m`→None, `90`→None); suite 1967/1967 green from the worktree. Two confessed decisions written back into the shard: the byte-exact note's placement (after the template fence) and range-resolves-to-upper-bound semantics. **Probe of p3 deferred to phase start** (its dependencies are p2's shipped text). **Probe of p4 deferred to phase start** (now the largest Work list after the 2026-08-10 restructure; independent of p1-p3 but a plan-time probe would be the whole phase performed twice).
- **Restructure re-audit (2026-08-10, p4 added at operator direction):** grounding 27 claims — 22 resolve, 0 fail, 5 partial (all wording-precision, corrected: hooks-vs-checker registration split, fork softening phrase ":102 if available", source line-count ~1,030 not ~2,000, sanitize token list marked over-inclusive-by-design, archive-docs mention added); executability 4 findings (phase count, stale exclusion parenthetical, inventory-criterion gaps — all corrected; in-shard drop of Drafting-session mechanics + RATIONALE noted as drafted defaults); coherence 1 contradiction ("all three phases" — corrected to four).

SHIPPED: p1 @ c040966 (2026-08-10).
**Spec check at p1** — work items 4/4 evidenced · interfaces conform (none/none) · none uncovered
Downstream sweep at p1 — p2 clean (its line hints all sit below the p1 edit zone at :347+, so @3d51805 tags remain accurate) · p3 3 hints re-tagged @c040966 (:226-652 / :409 / section :589) · p4 clean (p1 touched neither the fork nor upstream) · master 1 hint re-tagged (:531) · code: nothing pinned (doc-only phase)

SHIPPED: p2 @ 39264f8 (2026-08-10).
**Spec check at p2** — work items 6/6 evidenced · interfaces conform (Produces: boilerplate block shipped at :369) · none uncovered
Downstream sweep at p2 — p3 5 hints re-tagged @39264f8 (:497/:680/:709/:750/:860) and its Consumes pointer made concrete (:369) · p4 1 failure mode strengthened (gate normalizes whitespace/wrapping/case — only rewording fails, observed live at p2's control deny) · master 1 hint re-tagged (:802) · code: nothing pinned (doc-only phase)

SHIPPED: p3 @ a0d4693 (2026-08-10). Its deferred probe ran at phase start (GREEN — settled readings transcribed into the shard before the worker dispatched).
**Spec check at p3** — work items 6/6 evidenced (+1 worked-example record, flagged by the worker, same claimed file) · interfaces conform (Consumes: p2 boilerplate restated; Produces: `## Verification record` shipped at :612) · none uncovered
Downstream sweep at p3 — p4 clean (no file overlap: p3 touched clu-plan only, p4 targets the fork; the p2-carried gate-normalization note already covers p4's paste fidelity) · master 1 hint re-tagged (:1155) · code: nothing pinned (doc-only phase)

SHIPPED: p4 @ 2e17c83 (2026-08-10). Its deferred probe ran at phase start (GREEN — ten settled readings transcribed into the shard before the worker dispatched). No downstream sweep — p4 was the last phase.
**Spec check at p4** — work items 5/5 evidenced · interfaces conform (none/none) · none uncovered

ALL PHASES SHIPPED. Plan-level Done criteria walked 2026-08-10: suite green at every commit (c040966 / 39264f8 / a0d4693 / 2e17c83, 1967/1967 each) · `clu install-skill --force --only clu-plan` run, `clu doctor` drift section absent · decoupling grep empty (no /plan routing in clu-plan) · fork inventory passed (p4 spec check) · memory citation fixed (feedback_clu_plan_task_list_monitor.md now finds the Monitor item by name). Binding decisions: (1) all edits go to the REPO copy `end_of_line/skills/clu-plan/SKILL.md`, never the installed copy — reinstall happens once, at ship, via `clu install-skill --force --only clu-plan` (`--only` is mandatory: a bare `--force` writes clu's stale bundled `plan` fork through the `~/.claude/skills/plan` symlink into the abe-skills repo, destroying the rewritten /plan — cli.py:2394-2415 @3d51805); (2) clu-plan stays SELF-CONTAINED — no instruction in it may route the author into `/plan`'s files; (3) clu-plan's adapted briefs carry NO line-start `Plan slug:` / `Plan audit:` / `Plan work:` markers — while p4's fork keeps upstream's markers and verbatim fragments, because the two skills sit on opposite sides of the dispatch gate by design.

## Goal

Bring `end_of_line/skills/clu-plan/SKILL.md` to parity with the rewritten personal `/plan` skill in three areas: (1) replace the pre-rewrite three-dimension research model (which mandates a now-banned `Explore`-typed agent) with the team model — change impact / adversarial read / implementation specialists — plus neutral briefs and the agent-count table; (2) settle design forks at plan time via an adapted stage-zero comparison-probe rule; (3) add a pre-ship adversarial read-back — three auditors plus a dry-run prober over the in-memory drafts — before the operator is asked to `ship`; and (4) resync the bundled `/plan` fork (`end_of_line/skills/plan/SKILL.md`) from its 2026-06-10 snapshot to a sanitized single-file adaptation of the current upstream.

## Non-goals

- ~~Bundled `/plan` fork resync~~ — **promoted into scope as p4** (operator, 2026-08-10, adopting the exclusion-safety specialist's fold-in recommendation). p2's decoupling still ships on its own merits — clu-plan stays self-contained regardless of the fork's state — and the fork stays VENDORED (drift from installed copies remains the designed steady state, cli.py:2285-2293).
- **Extending `clu install-skill` to ship skill directories.** p4 inherits the single-file constraint instead (adapted content lands inline); changing install machinery is runtime code, excluded below.
- **Dispatch-gate marker adoption.** clu-plan's briefs stay invisible to `~/.claude/hooks/plan-dispatch-gate.sh` rather than adopting its `Plan slug:` marker + verbatim fragments. Safety: the gate only polices dispatches that carry its markers; carrying them would hard-couple clu-plan to `/plan`'s exact boilerplate wording, and the next `/plan` rewording would break clu authoring machine-wide (gate fragments fail CLOSED — plan-dispatch-gate.sh:198-208).
- **Interface lines / task tier for the clu sub-plan format.** A clu format change touching `plan_parser.py` and templates; needs its own design pass. Safe to exclude because nothing in clu reads interface lines or task tiers today — no parser, no gate, no reviewer keys on them — so adding the syntax without a consumer would be dead structure, and p3's verification step is deliberately specified against the format WITHOUT them.
- **Relocating monitoring / `clu ship` content out of clu-plan** (B1's accretion finding) — parked; see Parking lot.
- **No runtime code changes.** `plan_parser.py`, `cli.py`, `supervisor.py` are cited, never edited. The only non-markdown edit is one stale comment citation in `end_of_line/hooks/clu_session_start.py` (no behavior change).

## Files touched (overview)

- `end_of_line/skills/clu-plan/SKILL.md` — P1, P2, P3 modified — the skill under parity; API hotspot: section names `## Sessions index` / `## Findings log` and the task-list protocol section are pinned by tests and by clu-phase — must survive untouched
- `end_of_line/hooks/clu_session_start.py` — P1 modified — one stale comment citation; no behavior change
- `end_of_line/skills/plan/SKILL.md` — P4 rewritten — the bundled fork, brought to current upstream as a sanitized single file; only frozen history/archive documents (`docs/history/`, `plans/archive/`) reference its old shape (read-only by convention)

## Background findings

- **Gate discriminators (verified this session @3d51805).** The machine-wide draft gate skips any `plans/*.md` with byte-exact line-start `## Sessions index` (`~/.claude/hooks/plan_draft_gate.py:934,1020-1021` — case-sensitive, single space), checked BEFORE the `Approval:` match; clu's own parser is looser (`^##\s+Sessions?\s+index` case-insensitive, plan_parser.py:20), so only the byte-exact spelling keeps clu masters exempt. The dispatch gate sees a dispatch only via line-start `Plan slug:` (research) or `Plan audit:` / `Plan work:` (audit) markers (plan-dispatch-gate.sh:153-155); a `Plan slug:`-marked brief missing the exact normalized fragment "you are not to invoke the /plan skill" is DENIED (:198-208, :242-255) — clu-plan's natural "/clu-plan or /plan" phrasing does not match it — and a `Plan work:`-marked brief silently enters audit mode with invariants unchecked.
- **Install topology (verified this session).** `clu install-skill` writes exactly one file per skill, `~/.claude/skills/<name>/SKILL.md`, unlink-then-write (cli.py:2354-2425). Therefore every adapted brief must live INLINE in clu-plan's SKILL.md — a `references/` sibling would never be installed. The installed clu-plan is a real file, byte-compared by `clu doctor` (BUNDLED, drift flagged); `plan` is VENDORED (drift expected, not flagged).
- **Claude Code facts (vendor docs, fetched this session).** "Explore and Plan are the only subagents that omit CLAUDE.md and git status. There is no frontmatter field or per-agent setting to change which agents skip them" — code.claude.com/docs/en/sub-agents, "What loads at startup". Agent worktrees branch from the DEFAULT branch and carry only tracked files — code.claude.com/docs/en/worktrees — which is why every prober/auditor gets pasted text, never a path.
- **Parser truths (verified this session @3d51805).** `parse_effort_minutes` accepts `Nh` / `Nmin` (decimals ok, case-insensitive) and ranges `N-Mh` / `N-Mmin`; `45m` and bare integers return None → default lease (plan_parser.py:17-18,94-104). An absent Sessions index returns `[]` (plan_parser.py:3-6); `clu init` tolerates it but the supervisor errors `no Sessions index in <path>` at dispatch (supervisor.py:834-836) — so clu-plan's intro claim is right about the outcome, imprecise about the mechanism.
- **Team B's from-scratch shape, recorded, not adopted.** B1 proposed clu-plan as a ~150-line delta over `/plan` (gate/refusal + clu format contract + "run /plan Mode 1 steps 4-7 with clu overrides" + ship pipeline). Not adopted because the dependency is unpinned by design: a public install resolves `/plan` to the drift-expected VENDORED fork, this machine resolves it to the abe-skills symlink — two different documents, and clu-plan's own header mandates bundle self-containment (SKILL.md:7-13). Surfaced at approval.
- **Load-bearing passages the rewrite must preserve** (A2, all @3d51805): the Findings-log divergence — findings live in the MASTER, not a shard, because clu-phase reads the master (clu-plan SKILL.md:709-719, clu-phase SKILL.md:114,127); the specialist briefs enforce three back-referencing Critical rules (:181-208 ↔ :686-707, :761-782); verify-or-block has ONE full statement (:721-742) with pointers at :216-224 and :243-247; Step 5's incidental cargo (tight-pipeline write :472-476, push-before-init :478-481, delayed-init drift sweep :483-494, worker-model line :510-513, Monitor arming :521-529, watch-ownership hygiene :562-576, abandonment path :634-641); the task-list protocol section and its literal strings pinned by tests/test_task_list_skill_wire.py:16-31.

## Done criteria (plan-level)

- All four phases shipped; full suite green at each commit (`python3 -m unittest discover -s tests`).
- *(ship-time — Mode 3 walks these, no phase owns them:)* `clu install-skill --force --only clu-plan` run once at ship, and `clu doctor` shows clu-plan absent from the drift section (produced output, not assumed).
- `grep -n "plan.*Mode 1\|/plan skill" end_of_line/skills/clu-plan/SKILL.md` shows no instruction routing an author into `/plan`'s files (the decoupling invariant — it stands on its own now that the fork resync is p4: clu-plan must not depend on any /plan copy being current).
- The bundled fork (p4) passes its section-heading inventory: stage-zero, three teams, read-back briefs, both templates, execution brief, spec-check + sweep machinery, Modes 2-5, Scope Check, and Rules all present in the single file, with the sanitization header (blind-overwrite warning included) and clu-boundary rule retained.
- Operator memory `~/.claude/projects/-Users-smabe-projects-end-of-line/memory/feedback_clu_plan_task_list_monitor.md` re-checked against the shipped step numbering and its citation corrected if still off (it pins "Step 5 · 6"; the item is currently item 7 at SKILL.md:1155 @a0d4693).

## Parking lot

- Relocate the task-list Monitor protocol + teardown forensics + `clu ship` runbook out of clu-plan into clu-monitor / operations docs (B1: operations content parked in an authoring skill).
