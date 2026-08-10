---
name: plan
description: Create, resume, hand off, ship, or abandon a phased plan (single file for single-phase work; master + one shard per phase for multi-phase) in plans/ before starting multi-file work. Keeps ADHD scope creep in check by making scope explicit and approved upfront.
user_invocable: true
---

<!--
clu-adapted fork — sanitized for the clu package. The canonical upstream is
the operator's personal /plan skill, which is a DIRECTORY (a SKILL.md spine
plus references/ and scripts/); this fork inlines what it needs into one file
because `clu install-skill` ships exactly one SKILL.md per skill. The
sanitization rules, which any future re-sync MUST re-apply:
- NO hard dependency on skills a public clu install won't have. Hard
  references to the operator's personal-only skills (knowledge-graph
  ingestion, the personal security review pass) are removed; diagnose /
  code-review / brainstorm survive only as clearly-optional examples
  ("if your setup has one").
- Upstream's hook-and-checker machinery (its dispatch gate, draft gate, and
  plan checker script) is re-expressed as the session's OWN obligations, with
  one Optional enforcement note at the end. The artifact conventions those
  checks key on (`Approval:` markers, the `Plan slug:` / `Plan audit:` /
  `Plan work:` brief openers, the verbatim brief fragments) are KEPT
  byte-for-byte — inert without the hooks, load-bearing wherever they exist.
- Upstream's `references/` briefs and templates land INLINE as the trailing
  sections of this file; nothing here requires a sibling file to exist.
Do NOT re-sync by blind overwrite — re-apply this sanitization, or
personal-skill references and required-file pointers leak back into the
shipped package.
-->

## Plan Workflow

This skill enforces a "plan before code" discipline: for any non-trivial multi-file change, write a plan under `plans/` (a single file `plans/<slug>.md` for single-phase work; a master `plans/<slug>.md` plus one shard `plans/<slug>-<phase>.md` per phase for multi-phase work) and get user agreement before coding. The rules, briefs, and templates below are the authoritative source — this file is self-contained on purpose so it works in any project, including fresh clones with no extra project memory loaded. Why this discipline exists: without an explicit plan, work drifts ("while I'm here" fixes turn a 2-file change into a 7-file commit); the plan file is the anti-drift contract. Trivial changes skip the plan flow, gated on observable shape — never on a time estimate, which is self-certifiable: the change touches a single file AND is one logical change (typo fix, constant tweak, comment/doc edit, an obvious one-function bug fix). Multi-file, more than one logical change, or any new file/symbol → plan.

The skill has five modes, auto-detected from context. Bare `/plan` (no slug) is a status query, not an error: list every plan in `plans/` (excluding `shipped/` and `archive/`) with its Approval marker and NEXT phase, flag any legacy-shape files (see Mode 2's reshape rule), and ask which to resume — or whether to create a new one.

### Mode 1: Create a new plan (`/plan <slug>` — no existing file)

1. **Normalize the slug**: lowercase, hyphens for spaces, strip non-alphanumeric except hyphens. Example: "Pipeline Hardening!" → `pipeline-hardening`.
2. **Find the project's plans/ directory.** Start from the current working directory, walk up to the git root if needed. If `plans/` doesn't exist at the git root, create it.
3. **Check for an existing file at `plans/<slug>.md`.** If it exists, switch to Mode 2 (resume).

   **A slug that names a SHARD resolves to its master, it never opens as a plan of its own.** If `plans/<slug>.md` does not exist but `<slug>` ends in a `-<phase>` suffix whose prefix DOES have a master (`plans/<prefix>.md`), resume the master and treat the suffix as "start at that phase". A shard has no Phase map, no Status, and no Approval marker, so working one directly silently skips the staleness validation and the whole Phase Completion Cycle. **Do not key this on the phase id's shape** — real corpora use `p1`, `P1`, `1`, `0`, `A`–`D`, `W1`–`W3` and word ids like `logic`/`ui`, and the id does not determine the filename. Test for "the prefix before the last hyphen has a master", not for a naming convention.
4. **EXPLORE — mandatory, unconditional** (the full no-exceptions wording lives in Rules: "EPCC: Explore is unconditional"). The plan is *not* drafted until exploration completes: three research TEAMS — change impact, adversarial code read, implementation specialists — dispatched before a single line of plan text gets written.

   **🛑 STAGE ZERO — settle the design forks BEFORE dispatching the teams.** Read the operator's goal, the spec if there is one, and the code long enough to answer one question: **is there a fork between two or more candidate designs whose outcome would change the phase map** — different phases, different Work, different Done criteria, a different container / schema / protocol the later phases get written against? If yes, that fork is settled HERE, by experiment, before Team A or B is briefed. Dispatch a **comparison probe** per fork (brief 6 in the Plan audit briefs section below, `isolation: "worktree"`) and write its verdict into the plan as a locked decision.

   **The ordering is the whole point and it is not negotiable.** Team A asks what the change BREAKS and Team B asks what shape it should be — neither question has an answer while the design is a fork, so a probe run after them buys tidiness and nothing else. Run it first and the research is briefed against a design that exists.

   **When research surfaces a fork nobody knew about**, the same rule applies retroactively rather than being waived: probe it, then **RE-DISPATCH Teams A and B against the winner.** That re-dispatch is not optional and is not a second opinion — the first pass answered about a shape you have now discarded, so its change-impact findings describe code you are not going to write. Cheaper than it sounds: it is two agents, and it is strictly cheaper than the phase that would otherwise exist to resolve the fork.

   **Neither the probes nor a re-dispatch count against the agent-count table below.** The table sizes *research breadth*; a comparison probe is not a research agent and cutting one to stay under a baseline is trimming the only agent in this step that produces a measurement. Same immunity step 7's dispatch has from the same table, for the same reason.

   **What does NOT trigger a probe:** a question whose answer changes an implementation detail inside a phase but leaves the phase map alone; and the algorithmic load-test and the Diagnosis falsifiable test, which have their own placement rules and stay where those rules put them, even though a failure in either can send you back here. The trigger is "would the PLAN be different", not "could this go wrong later".

   **Why teams and not topics.** A topic-shaped dispatch asks three questions that are all versions of *what exists and what's canonical*. None asks **what breaks when I touch this** — which is where implementation surprises on refactors actually come from. Knowing a function has four callers says nothing about whether one of them depends on it being slow, on it running before something else, or on a side effect nobody wrote down. Team A exists for that question.

   **Hand hard diagnostic cases to a dedicated diagnosis pass first.** If the symptom is genuinely opaque (no obvious hypothesis, multiple plausible causes, intermittent reproduction), find the root cause BEFORE scoping the fix — with a disciplined diagnosis loop (e.g. a `/diagnose` skill if your setup has one). The diagnosis pass finds the cause; the plan scopes the fix. Don't try to do disciplined diagnosis inside the plan flow — they're sized for different jobs.

   **Three research teams.** Dispatch every agent in a single message using the Agent tool so they run in parallel. Full briefs: the Research team briefs section below — pass them verbatim; paraphrasing into one generic "go research this" is the degradation the split exists to prevent.

   **All agents are `subagent_type: "general-purpose"`.** Never `Explore` — per Claude Code's subagent docs, "Explore and Plan are the only subagents that omit CLAUDE.md and git status. There is no frontmatter field or per-agent setting to change which agents skip them." An Explore agent silently loses every standing rule — the verify-before-stating gate, any project-specific API gate — which is exactly what research correctness depends on.

   **Team A — CHANGE IMPACT** (3 agents). *Skipped only when the plan creates new code and modifies none.* That trigger is observable — look at the Work list — not a judgment call about whether something "counts as a refactor."
   - **A1 fan-in and observable behavior** — who calls this, who calls them, out to the point a user would notice a difference.
   - **A2 incidental behavior** — what the code being changed or deleted does *beyond its stated job*: timing, ordering, side effects, the guard something else quietly relies on.
   - **A3 shared state and ordering contracts** — what else reads or writes the same state; what breaks if this runs earlier, later, twice, or not at all.

   **Team B — ADVERSARIAL CODE READ** (1 agent; 2 when the change spans modules). Attacks the *existing* design: undocumented invariants, where naming lies about behavior, what you must know that isn't in the file — and **"if you rewrote this from scratch, what shape would it be?"** That last question is the point of the team. It puts the structural option on the table from an agent with no investment in the current shape and no memory of anyone proposing something smaller.

   **Team C — IMPLEMENTATION SPECIALISTS** (2 agents + conditionals). Unlike A and B, these MAY be told the intended approach — their job is to check it against how the thing is meant to be used.
   - **C1 project-local API documentation + canonical samples** — the framework's official guidance and working examples, found wherever this project keeps them, fetched from the vendor when there's no local copy.
   - **C2 web prior art / community evidence** — with WebSearch + WebFetch. Independent corroboration is the point: vendor docs routinely describe an intended contract that doesn't match shipped reality.
   - Plus the three conditional specialists (algorithmic, reuse, exclusion-safety) on their existing triggers — summarised below, full triggers and briefs in the Research team briefs section below.

   **NEUTRAL BRIEFS FOR TEAMS A AND B.** They receive the operator's goal in the operator's words plus the files in play — **never the approach you have in mind.** A brief that says "we're adding a helper to X" has already handed the worker your assumption, and what comes back will agree with it. If you cannot describe the territory without naming your solution, that is the signal your solution is already doing load-bearing work before any research ran.

   **Non-negotiable** for any plan that touches code, regardless of plan size, file count, or category — with Team A's skip condition as the single exception, and it is observable rather than self-certified. Each brief describes the *shape* of what to look for; the agent figures out where this project's equivalent lives (Rules: "Generic-skill discipline"). This rule governs whether the teams RUN, never their headcount — presence is unconditional; scale is the agent-count table below plus its single-phase floor.

   **Three framing questions to hold in mind while designing the dispatch.** Everything below is scaffolding for these; if you can answer them well, the rest mostly takes care of itself:

   1. **What's the shape of failure if the research is wrong?** Frame this as a concrete falsifiable test scenario (initial state, applied conditions, expected vs. failing behavior). That scenario is the load-test that lands at phase 1.
   2. **What finding would a generalist agent bury?** Whatever it is, that's the brief for one specialist whose ONLY job is to surface it as a primary finding — not as a footnote under broader topic coverage.
   3. **How many genuinely distinct dimensions am I researching?** That's your agent count beyond the three teams. Specialists compose on top per their trigger rules below.

   **Agent type choice — default to `general-purpose` for every agent this skill dispatches.** Reach for `Explore` ONLY for a pure locate-this-symbol sweep where losing the standing rules costs nothing; never for an agent that judges, audits, or grounds a claim. The reason is mechanical, not stylistic: Explore and Plan are the only subagent types that omit CLAUDE.md, and there is no setting that opts them back in — so an Explore agent researches without the rules that make its research correct, and does so invisibly.

   **Taking that exception is an on-record act, and where the full /plan skill's dispatch-gate hook is registered, a hook enforces the ban** — it denies any dispatch that declares itself a plan agent while using `Explore` or `Plan`. To claim the locate-sweep exception, the prompt must carry this sentence, verbatim, at the start of a line:

   ```
   Explore carve-out: this agent only locates symbols. It judges nothing, audits nothing, and grounds no claim.
   ```

   It is a claim about the dispatch, not a magic word — do not use it for an agent that judges, audits, or grounds anything, and note it exempts the agent TYPE only, never the brief. The gate's deny message deliberately does NOT reproduce this sentence: a denial that quotes its own escape hatch can be pasted back into a retry, which authorizes the dispatch that was just refused.

   **Baseline agent count, then scale with research surface area** — not with plan size, not with category. (The FLOOR below is the one sanctioned exception: its trigger reads the Work list, which is observable, and it compresses one team's headcount without cutting a question.)
   - **4 agents** for a plan that only creates new code: B1 + C1 + C2 + any triggered conditional. Team A is skipped because there is no existing behavior to break.
   - **7 agents** for a plan that modifies existing code: A1–A3 + B1 + C1 + C2, plus conditionals.
   - **+1–2** when the plan spans extra dimensions beyond those: an LLM orchestration change may want prompt-design and caching specialists; a UI change may want state-flow or accessibility.
   - **+3 or more** only when the plan is genuinely cross-cutting AND specialization buys clarity. Extra slots are for *role specialization*, not for chasing the same question harder.
   - **Stop adding agents** when the marginal one would re-cover ground from another. Consolidation overhead grows with agent count, and the evidence on fan-out is that returns flatten fast while cost does not — budget it intentionally.
   - **Effort is the cost control, not headcount.** Agent count is set by the number of distinct questions; how hard each agent thinks is a separate dial, and it is the cheaper one to turn. Dispatch Teams A, B, and C at the session's effort. Dispatch the three conditional specialists at **low** — each answers one narrow forced-binary question against a trigger that already fired, and quality holds there. Trimming an agent cuts a question; lowering its effort does not.

   **The scale has a floor as well as a ceiling, and its trigger is observable — count the phases.** When the plan is **single-phase**, Team A collapses to ONE agent carrying the A1+A2+A3 briefs verbatim, concatenated — 4 agents total (A-combined, B1, C1, C2). The floor compresses TEAM A ONLY: no question is cut, and Team B's second agent (dispatched when the change spans modules, per its brief) plus the three conditional specialists keep their own triggers and attach on top as ever.

   *(The floor used to also require ≤2 files and no new file. Widened: the extra clauses cost three agents on plans whose research surface a single agent covers, and the concatenated brief was never the thing that failed.)*

   **Role specialization is the principle that funds high agent counts.** Each agent should have a single sharp job no other agent is doing. This prevents the failure mode that motivates this rule: a generalist agent mentions the load-bearing detail in passing, the consolidation report buries it under broader findings, and the bug surfaces in phase 3. A specialist whose *only* job is "the inner-loop / the prompt structure / the cache invalidation contract / the migration path" surfaces those findings as primary, not as asides.

   **Examples of additional role splits by domain** (illustrative, not prescriptive — these compose on top of the three teams):
   - **Algorithmic / numerical / physics**: math-and-formulas agent · per-tick-inner-loop specialist · integration-with-existing-system · (optional: failure-modes-under-load, platform-quirks).
   - **LLM orchestration**: prompt-design and structured-output agent · caching-and-token-budget agent · model-version-and-migration agent · evals-and-regression-fixtures agent · integration-with-existing-brain agent.
   - **UI feature**: component-and-layout agent · state-and-data-flow agent · animation-and-interaction agent · accessibility-and-test agent.
   - **Backend feature**: schema-and-migration agent · API-contract-and-versioning agent · caching-and-invalidation agent · error-and-retry semantics agent.
   - **Security review** (illustrative): threat-model-and-attack-surface agent · authn-and-authz agent · data-handling-and-privacy agent · dependency-and-supply-chain agent.
   - **Cross-cutting refactor**: callers-and-impact agent · test-coverage agent · deprecation-path agent · integration-test-strategy agent.

   **Conditional specialists — three of them, each with a trigger and a forced-binary-decision contract. Full triggers and briefs: the Research team briefs section below.** Check all three against every plan; each exists because of a specific shipped failure.
   - **Algorithmic implementation-details specialist** — fires when the plan cites a paper, a talk, engine docs, or a third-party library's primitive. Surfaces what the inner loop actually does beyond the formula.
   - **Reuse / refactor specialist** — fires when a new SOURCE file mirrors an existing source file. Does NOT fire for markdown, docs, skill definitions, prompt templates, or config. Recommends Phase-0-refactor vs copy-and-defer as a decision the operator must make.
   - **Exclusion-safety specialist** — fires when a Non-goal excludes some members of a peer set. Recommends fold-in vs a documented invariant, and defaults to folding in.

   **Each RESEARCH agent's brief includes** — Teams A, B, C and the conditional specialists, and NOT the stage-zero comparison probe, which recommends nothing, cites nothing and measures instead (its own brief says why, and warns that no gate will catch the mistake):
   - The slug + a one-line goal of the plan being scoped (so the agent knows what it's researching for)
   - Specific questions tailored to its role — sharp enough that a generalist wouldn't have written them
   - For algorithmic plans, the four required questions (in the conditional specialists' briefs below) concentrated in the implementation-details specialist's brief
   - An explicit instruction: "You are NOT to invoke the `/plan` skill. Your job is research only. Report in under 400 words."
   - **The citation requirement, verbatim:** "Cite file:line for local sources, URL+section for fetched ones. A claim you did not open a source for is reported as unverified, not as a finding." *(State the literal, don't paraphrase — where the dispatch gate is registered it matches the literal, so a brief assembled from a paraphrase reads as complete and is denied.)*
   - **The effort-objection ban, verbatim:** "Diff size, file count, and implementation effort are not your inputs. Recommend what is correct."

   **Divergent-design note**: if exploration surfaces significant scope questions the user can't decisively answer (e.g. "should we refactor this whole subsystem or just patch it?"), suggest a separate divergent-design pass (e.g. a `/brainstorm` skill if your setup has one) before re-running `/plan`. Do NOT run that pass inline from within this skill — it's user-interactive and doesn't compose cleanly.

   **Consolidate findings as ground truth for the plan** (internal step; not surfaced to the user as a separate report).

   **STRUCTURAL FINDINGS BAKE IN BY DEFAULT — you do not get to shrink them quietly.** When any agent recommends reworking a whole file or more, the plan is drafted WITH that rework. Talking it down to something smaller requires showing the larger approach is *infeasible* and citing the blocker (`path:line` or doc URL), and the downgrade is surfaced to the operator at approval as a forced binary decision — the same treatment the reuse and exclusion specialists get. **The evidentiary exit is the probe, not the argument:** when a structural proposal shaped the phase map, step 7 aims a dry-run prober at the structural phase, and an APPROACH-failure verdict from that probe IS the citable blocker this talk-down requires — cite the probe report, collapse the structure, and surface the collapse at approval like any other downgrade. The infeasibility bar cannot be cleared by argument against code that does not exist yet; the probe is the evidentiary route.

   **Diff size, file count, and implementation effort are not inputs to this decision, and that applies to YOU here, not only to the agents.** "It's a lot of work", "that's a big diff", "fewer files is lower risk", "let's keep this contained" are the sentences this rule exists to stop. You are the party who formed an opinion about the size of this change from a skim before any research ran; the agents are not. When their finding and your prior disagree, the prior is the thing without evidence.

   Walk away from exploration with:
   - The corrected understanding of the area being changed — including **what the change will break**, not only what exists (Team A) and what the canonical pattern is (Team C)
   - Team B's from-scratch shape, recorded even when the plan does not adopt it, so the approval conversation can see what was on the table
   - Any forced binary decisions surfaced by reuse / exclusion specialists, with the specialist's recommended option
   - Any load-bearing implementation details surfaced by the algorithmic specialist (for algorithmic plans)
   - **No unverified claims survive EXPLORE** (Rules: "No research deferrals — verify or block"). The only things research legitimately can't close are (a) genuine operator decisions (surfaced at approval) and (b) empirical/runtime unknowns (which become the Diagnosis falsifiable test or the algorithmic load-test, never plan-body facts) — **and (b) is narrower than it reads: an empirical unknown whose answer would change the phase map is settled NOW by a comparison probe, not routed into execution.** Anything else unresolved means EXPLORE isn't done — finish it, or STOP and resolve it with the operator before drafting.
     - **(b) has a membership test — apply it, don't self-certify into it.** A question qualifies as an empirical/runtime unknown ONLY if a Read / grep / doc-fetch *this session* genuinely cannot close it — i.e. it truly needs a running app, live external system, or real model output. If reading the code or docs would settle it, it is NOT empirical: verify it now. The tell that this rule is failing is a plan that says "Phase 1 must verify X" where X is statically checkable (does this function branch on that flag? does this type have that field?) — that "verification" is the EXPLORE work you skipped, not a legitimate deferral. Ask "does closing this need runtime, or just a Read?" before routing anything to (b).
     - **🛑 (b) DOES NOT MEAN "resolve it during execution" when the answer changes the plan** — that fork was settled at STAGE ZERO above, before the teams were briefed, and if this pass surfaced a new one it gets probed and the affected teams re-dispatched rather than the question being carried forward. Needing runtime says what closes a question, never when. **The anti-pattern, and its tell: a phase whose done signal is "a decision is recorded" rather than "something works."** That phase is an unresolved fork wearing a gate's clothing — every phase after it was drafted against an assumed answer, and if the answer goes the other way the plan is rewritten, which is the outcome planning exists to prevent. The origin case deferred a container choice to phase 1; the composition it sealed carried a view-identity change that killed per-view state on every data change, and no gate here could see it because at research time there was no composition to see. Exploration phases during implementation defeat the purpose of a planning stage — planning is the time set aside for figuring out whether a proposed route will even work. **An unresolvable fork** — the experiment needs hardware, a live service, or a real user — stays (b) and routes to execution, but name WHICH it is and why the probe cannot reach it rather than letting "empirical" carry the deferral on its own.

5. **Draft the plan** using the template in the Plan templates section below, with research findings as ground truth. Three rules for drafting:
   - **Every factual claim in the plan must be supported by exploration findings.** Cite file:line / URL+section in the plan body where the claim depends on a specific verified source.
   - **Verify or block — no deferral channel** (full rule in Rules: "No research deferrals — verify or block"). Every claim in a drafted plan is verified this session and cited with file:line or URL+section, or the plan isn't written yet.
   - **Bake forced binary decisions into the plan as the recommended option.** If the reuse specialist recommended a Phase 0 refactor, draft the plan with a Phase 0 refactor included. If the exclusion specialist recommended folding excluded items into scope, draft the plan with them included. The decision still gets surfaced explicitly at approval (see step 8) so the user can override, but the plan reflects the recommendation by default.

6. **Write the file(s).** Single-phase plan → one file `plans/<slug>.md`. Multi-phase plan → the master `plans/<slug>.md` AND every shard `plans/<slug>-<phase>.md`, written in the same step. Every plan is written with `**Approval: DRAFT**` as the first line of its Status section (single-phase: `## Status`; master: `## Status & cold-start`) — the marker flips only at explicit approval in step 9 — and an `**Authored at: <current HEAD commit>**` line beneath it, which Mode 2's staleness check diffs from. **Drafting the shards is not optional and not deferrable to "when the phase starts" — a multi-phase plan whose shards don't exist yet is not written.** Every shard a phase needs to be self-sufficient is authored now, at plan time, from the same research pass; a future session resuming the plan reads a shard that exists or it has nothing to resume from.

   **Then run the mechanical self-check over what you just wrote, before step 7 dispatches anything.** These checks are this session's OWN obligations: every Phase-map block names a shard file that exists on disk; every block carries its four sub-fields (Enters when / Done signal / If it fails / Shard); no stray `<slug>-research.md` exists; no literal deferral token survives (`TODO: verify`, "confirm during implementation", a placeholder); every phase's `## Work` carries its `- Consumes:` / `- Produces:` lines; file paths ride dash bullets, never numbered lists; and every tiered phase's task file sets are disjoint. Settling the mechanical half here is what lets the step-7 auditors spend their one pass on claims instead. One trap worth naming: in a repo whose plans are ABOUT planning machinery, a prose sentence *mentioning* a deferral token is indistinguishable from one *deferring* — fence it in a code span or reword it. (Where the full /plan skill is installed, a checker script runs this set mechanically — see Optional enforcement below. It has no PASS state on purpose: it ends by naming the judgment obligations it could not check, and none of those are discharged by it running.)

7. **VERIFY — adversarial read-back of the written plan (mandatory, unconditional; runs BEFORE the user ever sees it).** This gate exists because the plan's grounding rules — verify-or-block, shard self-sufficiency, peer-set exclusion rationale — are all self-certified by the same session that just wrote the plan, and a self-certified rule is the one that rots (same enforcement logic as the sweep record, Phase Completion Cycle step 5(c)). Running the pass *after* presenting the plan inverts it into the failure it prevents: the operator becomes the reviewer. Applies to both shapes — a single-phase plan gets the same pass, scoped to its one file.

   **Dispatch all agents in a single message so they run in parallel. The three auditors are READ-ONLY — they never edit a plan file; you apply every fix.** Every agent here → `subagent_type: "general-purpose"`. **Never `Explore` here** — the grounding auditor's entire job is checking claims against sources under the verify-or-block rule, and Explore omits CLAUDE.md, so an Explore grounding auditor audits without the rule it is auditing against. **Three auditors, fixed, plus the dry-run prober on its trigger** — the auditor axes are disjoint and none can answer another's question. This is not step 4's scaling dispatch; don't grow it by plan size. **Pass every brief verbatim** — paraphrasing them into one generic "review this plan" is the degradation this step's specificity exists to prevent.

   **Every brief in this step opens with `Plan audit: <slug>.` on its own line, and that line is load-bearing.** Where the full /plan skill's dispatch gate is registered, it keys on that line to apply the `Explore`/`Plan` ban to audit dispatches — without it the gate cannot see this step at all, and the one dispatch the paragraph above bans by name is the one nothing checks. It is a *separate* marker from step 4's `Plan slug:` on purpose: the three research invariants that marker demands are research-shaped (an auditor recommends nothing, so the effort-objection ban is meaningless to it, and the dry-run prober is not read-only), so an audit dispatch is held to the agent-type rule and nothing else.

   **The auditors verify by EXECUTION, not by reasoning** — with one deliberate exception: the coherence auditor is told not to open a source file at all, because its evidence IS the plan's own text and it quotes both halves of every contradiction. For the other two, an auditor that reasons about whether a claim is plausible reproduces the drafting session's assumptions, and the failure mode is a whole panel confidently agreeing on a shared error — the documented case is 80+ agents, including a senior arbiter, unanimously confirming an OpenSSL vulnerability that did not exist, killed by one instance that compiled the code and ran three test cases. **The instruction is to RUN the check**: open the file, run the grep, fetch the URL, and quote what came back. A claim the auditor did not open a source for is reported as unchecked, never as resolved — plausibility is not a resolution. Do NOT substitute "use a different model" for this; the decisive variable in that case was empirical execution, not model choice.

   **Dispatch effort is fixed per auditor, and it is set low on purpose.** Coherence at **low** — its evidence is the plan's own text, it opens no source, and it quotes both halves of every contradiction it reports, so there is nothing for a higher setting to buy. Grounding and executability at **medium** — both are mechanical checks (open the file, run the grep, match two lists against each other) whose accuracy holds there. The dry-run prober runs at the session's effort and is never lowered: it is the only agent in this step that writes and compiles code, which is the work this step actually pays for. **This is the dial to turn if the step feels expensive — never the auditor count.** Dropping an auditor deletes an axis no other agent covers; lowering its effort deletes nothing.

   **This step's four briefs are 1-4 in the Plan audit briefs section below — pass them verbatim.** Three read-only auditors on disjoint axes (grounding, executability, coherence), plus the dry-run prober on its trigger. (Brief 5 in that section belongs elsewhere and is NOT part of this dispatch: it audits a diff at the Phase Completion Cycle's step 1c. Brief 6 is the comparison prober step 4 runs BEFORE the plan exists — dispatching 6 here is the one thing its own brief bans by name; by step 7 the fork it settles is already written into the plan.) Every one opens with the `Plan audit: <slug>.` line — drop it in a paste and any registered gate stops seeing this step.

   **Plus, when the plan modifies code that already exists** (same observable trigger as Team A — read the Work list, don't self-certify): **4. Dry-run prober.** This one is NOT read-only and NOT an auditor — it is the only cheap way to learn whether a Work list is complete, because that question cannot be answered by reading. Dispatch it with `isolation: "worktree"` so its edits are thrown away. **Probe phase 1 at plan time; probe the phase with the LARGEST `## Work` list at the moment its dependencies are real.** When the largest-Work phase IS phase 1, that is one prober. When it is a LATER phase, do NOT probe it now — a plan-time probe of a late phase implements against stubs of the phases beneath it, and its result expires the moment those phases ship real semantics. Instead the verification record's dry-run line says `probe of p<N> deferred to phase start`, and Mode 2 step 2 dispatches it against the phase's REAL dependencies when the phase comes up — the same prober, paid at the moment it is maximally informative. Phase 1 keeps its plan-time probe: its dependencies exist now, it ships first, and its work-shape sketch has to compile before anything else can. The prober's stated job is file-list completeness, which scales with the size of the Work list, so a twenty-file phase going unprobed is still a mismatch by construction — deferral moves its probe, never waives it. **Two more targeting rules.** When a Team-B structural proposal shaped the phase map, one prober targets the structural phase AT PLAN TIME regardless of its position — approval needs its verdict, because step 4's bake-in rule names that probe's APPROACH-failure verdict as the one citable blocker its talk-down accepts; a structural LATE phase is the one deliberate double-probe (plan-time for the approval decision, phase-start when the staleness trigger fires). And a phase whose Work cannot execute in a throwaway worktree at all (external runtime, live credentials, hardware) is substituted by the earliest phase that modifies existing code, and the verification record names the substitution.

   **Paste the chosen shard's text (single-phase plan: the whole plan file) into the prompt verbatim — NEVER tell the prober to read the plan file.** A worktree is a fresh checkout branched from the repo's *default branch*: uncommitted and untracked files are absent, so the plan files just written in step 6 do not exist there, and a prober told to open them probes nothing. The same fact means the prober implements against default-branch code — if the plan targets work sitting on an unmerged branch, land or merge that first, or expect the probe to report noise.

   **A clean pass still reports counts.** If an auditor cannot name what it checked, it did not run the pass — re-dispatch it. That is re-running a pass that never happened, not a second round on findings, and it is **the only re-dispatch this step permits — apart from the ONE scoped re-probe a new-mechanism fix earns (below).** Carry its count sentence into the record verbatim; never restate it from memory.

   **Findings are BLOCKING, not advisory — and you get ONE pass.** Dispatch the auditors once, fix what comes back once, then proceed. There is no re-dispatch and no second FINDINGS round — the re-read/edit/re-read cycle was ruled out on cost; the two named carve-outs (the count re-dispatch above, the new-mechanism re-probe below) are passes over work that never ran or text no agent saw, not second rounds. Do NOT hand the operator the plan plus a findings list to triage — that is the inversion this step exists to stop.

   **A MISSING file from the dry-run prober is not a finding to weigh — it is a Work-list edit.** Add it, and add it to the master's `## Files touched (overview)` too. The prober attempted the change and you did not; when its file list and your Work list disagree, the Work list is what's wrong. The only thing to judge is *which phase* owns each missing file. **When the prober reports something that did not survive contact, the response depends on which kind it labelled.** A SKETCH defect is fixed in place — a sketch that omits a required conformance, or prose and code block prescribing different field names, is a drafting error, and correcting it is exit (a). An APPROACH failure is a step 4 problem wearing a step 7 costume: go back to EXPLORE with what it hit as the sharper question, do not patch the plan around it — with ONE exception: when the failed probe TARGETED A STRUCTURAL PHASE, the verdict is the bake-in rule's evidentiary exit (step 4), so collapse the structure, cite the probe report, and surface the collapse at approval — no fresh EXPLORE; the probe already answered the question EXPLORE would re-ask. Do not escalate the first kind into the second; a missing `Identifiable` is not a reason to re-run EXPLORE. A MEASURED mismatch — the prober produced an observable a Done criterion names, measured it, and the measurement misses the criterion — is the plan's OWN claim falsified: route it like SKETCH when the criterion misdescribes the intent (fix the criterion), and like APPROACH when the design cannot produce the criterion (back to EXPLORE). An OBSERVABLE-UNAVAILABLE report is neither — the named observable needs a runtime the worktree could not reach, so the criterion is unmeasured, not met; carry that into the record rather than reading the green build as coverage.

   **A reported WORKAROUND is a design fork, and it is the one prober output that arrives disguised as good news.** The brief requires the prober to answer "did you work around any constraint to reach green?", because a prober that routes around an obstacle still compiles, still passes, and still hands back a file list that is honestly complete — *for the design it happened to build*. Nothing else in this step can see that: the auditors read the plan, not the worktree, and a green build is exactly what makes the workaround invisible. So when the answer is yes, do NOT treat the probe as clean. Compare the two shapes the prober describes, and apply the step-4 bake-in rule to the comparison: **if the workaround-free design is the better shape, take it and add ITS files to the Work list**, which is usually strictly larger — that is the whole reason the fork matters. Only when the two are genuinely defensible does it go to the operator at step 8 as a forced binary decision, drafted with the workaround-free version as the default. What you must never do is inherit the workaround silently because it built.

   **Two more report items route like findings, not like notes.** **Item (vi), the three behaviors the OLD code provided** — read each "re-established at X" as a claim and check it, because the ones that are wrong read exactly like the ones that are right. A behavior the prober names as covered by a DIFFERENT mechanism than the one it replaced is a SKETCH defect at minimum: same outcome by another route is fine, same outcome at another TIME is a behavior change wearing a re-establishment's clothing (a synchronous write moved into an async task is the archetype). **A behavior it cannot place is NOT a Work-list edit** — unlike a MISSING file it names no path to add, so it routes by what it costs instead: a behavior a user would notice losing becomes a Done criterion on this phase naming the observable it protects, and one that is internal becomes a line in the shard's Decisions & findings. Deciding which is the whole judgment, and "the user would not notice" is a claim, not a default. **Item (vii), the bigger-size run** — anything it reports is a finding against a phase whose own gate cannot see it, so it never rides as an aside: route it by the same three labels, and where the phase text FORBIDS the bigger size, the finding belongs on the LATER phase's Done criteria — the one that runs the mechanism at that size — rather than on this one's. ⚠️ Write it there directly and name it in the verification record: step 5(c)'s promotion rule is the same move but it runs at a phase commit and lands in a sweep record, and neither exists yet at plan time, so borrowing its machinery here would put a criterion into a later shard with no artifact saying it was added or why. A phase shipping a mechanism only exercisable later is the case this item exists for; `no larger size applies` is a valid answer and its ABSENCE is what must never be.

   **The same routing covers the prober's confessed DESIGN DECISIONS — report item (viii).** The workaround question catches the conscious fork, where the prober knows it dodged something; item (viii) reaches for the unconscious ones — every point where two implementations both satisfy the phase text as written and the prober picked one. Compare each confessed decision against the plan's stated design: the plan already settles it → nothing to do; the plan is silent and the choice is visible to a user or durable in data → it is an undecided user-facing decision, promoted at step 8 like any other; silent and internal → write the chosen reading into the shard, so the worker builds the same interpretation the probe validated instead of rolling its own. Self-report has a ceiling — a prober cannot confess a choice it never noticed making — but the workaround question has the same ceiling and is the one prober output that has paid for itself since it was added.

   **This channel is where the prober usually earns its keep, not the file list.** Expect runs where the file list comes back complete and the sketch does not compile — the prober is the only agent in this step that executes rather than reads, so it is the only one that can find that. Its stop condition is conditional for the same reason, and it has TWO independent exceptions — expect a report that runs past green under either, and do not read the overrun as a prober exceeding its brief. **The first is observables:** green-build-and-stop holds only when no Done criterion names an observable output — when one does, the brief has the prober produce the observable and measure it against the criterion before stopping, and a mismatch comes back labelled MEASURED. **The second is scale**, and its trigger is the phase's own shape rather than its criteria: when the phase ships a mechanism at a size a later phase runs bigger, the brief has the prober widen it in scratch past green, report what changed, and revert. A phase with all-grep criteria and a one-at-a-time mechanism hits the second and not the first.

   Four exits per finding, all recorded: (a) **fixed** in the plan file — **and the record must NAME the fix, not just count it**; (b) **promoted** — it is a genuine operator decision, so it becomes a forced binary decision surfaced at step 8, and the plan is drafted with the auditor's reading as the default (same bake-in rule as the reuse and exclusion specialists); (c) **refuted** — you checked the source yourself and the auditor was wrong, with a `file:line` / URL citation in the record; (d) **uncheckable** — the grounding auditor could not reach the source at all, which per the verify-or-block rule is never a pass: close it yourself, pull the claim from scope, or promote it via (b). "The agent was probably wrong" without a citation is not an exit, and neither is silence.

   **Every exit-(a) fix is classified before the record is written, because the record cannot tell these apart for you.** A **correction** writes down a value, name, or citation the finding itself supplied — apply it and name it. A fix that introduces a construct appearing in neither the pre-audit plan text nor the finding is a **new mechanism** — untested design minted during the fix pass — and it earns ONE scoped re-probe: re-dispatch a dry-run prober over the affected shard section (verbatim paste, same worktree isolation) before the record is written. Like the step-9 restructure re-dispatch, this is a first pass over text no agent has seen, not a second findings round. A re-probe that faults the fix → correct with the probe's citation or take exit (b).

   **Because nothing re-checks your fixes, anything you cannot close cleanly in the single pass takes exit (b)** and goes to the operator as an open question. Reaching for (b) when a fix is uncertain is the correct move, not a cop-out — it is cheaper than a wrong fix nobody re-read. What (b) is NOT for is "I didn't want to look it up"; that is exit (a) with the lookup done. Refutations and uncheckables are counted separately in the record precisely because an all-refuted pass and a clean pass must not look alike.

   **Record the outcome** in the plan's Status (single-phase `## Status`; master `## Status & cold-start`), in the VERIFICATION RECORD shape carried by the Plan templates section below, written from the agents' reported counts and never from intention. The record carries all four lines when all four ran — claims, done criteria, coherence, and the dry run's LISTED/MISSING split — because a pass with no dry-run line and a pass whose dry run found nothing must not look alike. The record describes the plan **as written at approval.** On a multi-phase plan, later execution edits announce themselves — each sweep record lands in the same Status and visibly post-dates it. A single-phase plan has no sweep, so when the Scope Check adds a file mid-work, note it in Status alongside the record; otherwise the record silently claims coverage of text it never saw.

8. **Show the plan to the user, surfacing any forced binary decisions explicitly:**
   > Here's the plan. Read it over and approve / tweak / reject. I won't write any code until you say it's live.
   >
   > [If reuse specialist surfaced a decision]
   > **Reuse decision baked in:** plan adopts Phase 0 refactor of `<duplicated surface>` based on `<file:line>` evidence. If you'd prefer copy-and-defer (ship the duplication, file the dedupe as follow-up), say so and I'll restructure.
   >
   > [If exclusion specialist surfaced a decision]
   > **Exclusion decision baked in:** plan folds `<excluded items>` into scope based on `<file:line>` dependency on `<included items>`. If you'd prefer to keep the exclusion, give me the one-sentence invariant that makes the asymmetry safe.
   >
   > [If step 7 promoted a finding — exit (b)]
   > **Verification finding needs your call:** `<what the auditor found>`. Plan is drafted with `<the auditor's reading>` as the default. If you want it the other way, say so and I'll restructure.

9. **Block on user response.** Do not touch any code files, do not run any builds or tests, until the user explicitly approves. If the user picks copy-and-defer for a reuse decision, append the deferred refactor to the plan's Parking lot in writing before code starts. **If their response restructures the plan** — overriding a baked-in decision, adding or cutting a phase, changing Work or Done criteria — the verification record now describes a plan that no longer exists: re-dispatch the affected auditor over the rewritten sections — AND a dry-run prober when the restructure touched a Work list or a work-shape sketch, because a rewritten sketch is the one channel no reader can check (the incident behind this clause was caught by exactly such a prober, not by an auditor) — and refresh the record before flipping the marker. **This is not a second findings round** — it is a first pass over text no auditor has ever seen, so it does not contradict the one-pass rule above. If the restructure is small enough to self-check, note in Status that the record post-dates it and say what changed. On approval, flip the plan's marker to `**Approval: APPROVED <date>**` — the file itself must record the approval, because a future session can't see this conversation.

10. **Once approved, enter "working the plan" mode.** You orchestrate rather than type: dispatch each phase's worker, read its report, review and gate its diff, commit, seal the shard, sweep downstream. Interrupt scope creep, and append to the parking lot when the user drops a shiny idea mid-work. The plan is referenced on every dispatch and on every report you read back — not on every file touch, because the file touches are not yours.

### Mode 2: Resume an existing plan (`/plan <slug>` — file exists)

1. **Read in this fixed order — do NOT skip a step.** (a) The master `plans/<slug>.md`: Phase map (the arc + gates) and Status & cold-start (which phase is NEXT). (b) The **NEXT phase's shard** `plans/<slug>-<phase>.md` in full — Locked decisions, Work, Decisions & findings. (c) The master's Background findings, plus any earlier shard whose Decisions & findings the NEXT phase's gate references. (d) **Any spec / sub-document the plan references** (lines like "Full spec: <path>", divergent-design outputs, design docs, ADRs). A shard that defers detail to another file is incomplete without that file; resuming from a summary alone forces re-deriving a dependency map the shard already has — the failure mode is a fresh session "discovering" entanglements (call sites, threading requirements, focus contracts) the shard recorded at plan time. **You read the NEXT shard, not every shard** — shipped phases are reference-on-demand; reading all of them back is the context-clouding this sharded layout exists to prevent. The re-anchor pass in step 2 *confirms* the recorded map against current code; it never re-derives one from scratch. If the code read surfaces a dependency the shard doesn't record, that's a finding to write back into the shard, not silent context. (e) **Check the Approval marker** in the Status section: if it still reads DRAFT, this plan was never approved — take it through Mode 1 steps 7-9 (verify, then present, then block) instead of resuming work on it. A DRAFT plan from an earlier session carries no verification record, or a record predating every commit since; it gets the read-back before it gets an approval.

2. **Validate the plan against current reality before working it (mandatory — do NOT skip to working a stale plan).** A resumed plan was authored in a prior session; resume mode deliberately skips Mode 1's EXPLORE, which is only safe *if the plan still matches the code*. **First, check the sweep record**: the master's Status must carry a `Downstream sweep at <phase>` line for the most recently shipped phase, naming every unshipped shard AND closing with a `code:` segment (Phase Completion Cycle step 5(c)). If it is missing, the shards you are about to work from may carry instructions that phase falsified — run that sub-step retroactively for the phase BEFORE reading the NEXT shard as truth, because a false instruction is worse than a stale note and you are about to follow it. If the line is present but has no `code:` segment, run only the fourth question retroactively: what did that phase pin or constrain, and which guard in an earlier phase's shipped source was built for the freedom it removed. That half is the one that leaves a live defect in the app rather than a stale note in a file. Then start mechanical: list the commits that touched the plan's files since it was authored or last refreshed — `git log --oneline <that commit>.. -- <every file in the NEXT shard's Work>`, where `<that commit>` is the Status section's `Authored at` line (a plan predating that line: fall back to `git log` on the plan file itself) — those diffs are exactly where drift lives; read them before any judgment-based check. Union the paths across the master's Files touched (overview) and every shard's Work, and read the path accounting honestly: a Work bullet that yields no path, or a named path git cannot resolve, produces an empty diff that reads as "no drift". **Re-run the write step's mechanical self-check at the same time** — shard files present, Phase-map sub-fields, stray research file, deferral tokens, stale or deferred probe records, interface-line presence once any phase has adopted them, dash-bullet path carriers, task disjointness. (Where the full /plan skill is installed, its checker runs the diff and the mechanical set for you — see Optional enforcement; it never returns green, and its judgment obligations stay yours, including the sweep record above.) **Check the spec-check record the same way you check the sweep record**: a shipped phase with no `**Spec check at <id>**` line in Status is the same drift alarm as a missing sweep line — run step 1c retroactively against that phase's commit before working the NEXT one, rather than assuming it happened and went unwritten. Then spot-check the plan's load-bearing claims **in this session**: the NEXT shard's **Work** (does the real change touch what it lists, or materially more? — cross-check the master's **Files touched (overview)**), the **Diagnosis** (does the stated symptom/root cause match what the code actually shows?), and the **approach** (are you about to do what it says, or something else?).

   **Re-probe the NEXT phase when its probe went stale or was deferred — and ONLY then.** Two observable triggers, both read off the verification record: (i) the record's dry-run entry names a since-shipped phase alongside a still-pending one — the probe implemented against stubs of code that now exists for real; (ii) the record says the NEXT phase's probe was `deferred to phase start` (Mode 1 step 7's late-phase targeting rule — write that phrase literally at plan time; this trigger keys on it). On either, dispatch ONE worktree-isolated dry-run prober over the NEXT shard — same brief from the Plan audit briefs section below, same verbatim shard paste, same `isolation: "worktree"` — BEFORE dispatching the phase's worker. This is the plan-time probe re-paid at the moment it is maximally informative: it implements against the dependencies the shipped phases actually built, not the ones the drafting session stubbed. Route its report exactly as step 7 routes a plan-time probe's — **every channel, including the WORKAROUND fork, the three OLD behaviors of item (vi), the bigger-size run of item (vii), and the confessed design decisions of item (viii)** — with ONE difference: an APPROACH failure fires the re-plan trigger below, interrupting the phase before a worker builds on a dead contract. **That interruption is the mechanism working, not the skill misbehaving**: it is the same question the review would have asked, arriving one phase earlier and before the rework existed. Neither trigger fired → no re-probe; a per-phase probe on a plan with no drift is spend without information.

   **Re-plan trigger — if ANY is true, the plan is stale: STOP, escalate to a fresh Mode 1 step 4 EXPLORE + rewrite, re-confirm before code. Do NOT patch the plan turn-by-turn while coding:**
   - **Scope undercount** — the real change hits materially more files / call-sites / surfaces than the NEXT shard's **Work** lists (rule of thumb: >~1.5× the listed count, or any unlisted file in another module).
   - **Diagnosis mismatch** — the symptom or root cause is a different file, mechanism, or layer than the plan states.
   - **Approach switch** — you are considering a different implementation approach than the one the plan names, **especially a smaller-diff alternative.** You may NOT pitch a smaller-diff alternative to the operator as "lower risk" until you have shown the plan's named approach is *infeasible* — cite the blocker (path:line / doc URL). "It's bigger / fewer files / less code" is not infeasibility and is not a reason to switch. The smaller-diff alternative is exactly where workarounds hide.
   - **Unanswered fork** — a forced binary decision surfaces that the plan didn't pre-answer.

   Re-planning means: run Mode 1 step 4 (the three-team EXPLORE) on the **actual** scope, rewrite the plan to match, run Mode 1 step 7's verification pass on the rewrite (a rewritten plan is a written plan — it gets the same read-back), and re-confirm with the operator before code. Inline-mutating a materially-wrong plan during the Phase Completion Cycle is the failure this gate exists to stop — it lets a stale design (and the workarounds that grow on it) ship without ever getting the EXPLORE that grounds design decisions.

   **Legacy-shape plans reshape here, not mid-work.** If the file predates the current template — a multi-phase plan in a single file, or section headers that don't match the names the Scope Check and this mode key on (e.g. `## Files to touch` instead of `## Work`) — bring it to the current shape during this validation pass, before any code: shard a multi-phase single file into master + per-phase shards, rename sections to the template's names, carry the content over verbatim. **Reshaping also MINTS the interface lines a legacy plan has no way to carry.** Every reshaped phase's `## Work` gains its `- Consumes:` / `- Produces:` bullets: read them off the work-shape sketch's binding lines where the phase has a sketch (the signatures and types are already there — the sketch's interface half is contract, so this is transcription, not design), and write the literal `none` where it has none. Do not invent an interface to fill the line; `none` is a valid, meaningful answer and a guessed signature is a fabricated contract the next phase will be audited against. A reshaped phase whose Work splits into obviously separate jobs may gain the task tier at the same time, under the same disjointness rule — but that is optional, and leaving it untiered is a correct outcome. This is mechanical re-filing, not a re-plan — it needs no fresh EXPLORE unless one of the triggers above also fired. **It does need Mode 1 step 7**, and the minted interface lines are part of what that pass audits: reshaping mints shard files that have never been read for self-sufficiency or coverage, which is exactly the executability auditor's axis — run the pass on the reshaped files and write the record before working the NEXT phase.

3. **Summarize the state** to the user: goal, done criteria, what's in the parking lot, how much is done vs remaining — and surface any re-plan triggers found in step 2.
4. **Ask what the user wants**: continue working it, **re-plan it** (if step 2 flagged staleness), update the plan, ship it (Mode 3), or abandon it (Mode 5).

### Mode 3: Ship a finished plan (`/plan ship <slug>` or user says "ship the plan")

1. **Verify done criteria are actually met.** Walk BOTH levels: every shard's Done criteria AND the master's plan-level Done criteria — ask the user to confirm any ambiguous ones. If any shard's criteria OR any master-level criterion is unmet, refuse and say what's still outstanding. (Single-phase plan: the one file's Done criteria.)
2. **Create `plans/shipped/`** if it doesn't exist.
3. **Disposition the Parking lot.** Every parked item gets an explicit exit before the plan archives: file it as a follow-up (the project's issue tracker if it has one, otherwise surface the list to the user) or drop it with a one-line reason. Record each disposition in the master. Silently archiving unread parked items is how deferred work disappears.
4. **Move the master AND every shard.** `plans/<slug>.md` → `plans/shipped/<slug>.md`, and each `plans/<slug>-<phase>.md` → `plans/shipped/<slug>-<phase>.md`, using `git mv` for tracked files, plain `mv` otherwise. **Never archive the master and leave shards orphaned in `plans/`** — they move together as one unit; the shards carry the decisions-and-findings record that makes the archive worth keeping.
5. **Sweep handoff and resume leftovers.** Delete any `plans/handoffs/<slug>-*.md` still present — handoff files are transient by contract (Mode 4 step 4) and must not outlive the plan. Same for a `plans/RESUME-<slug>.md` briefing if one exists (some projects keep per-plan resume prompts): delete it and remove its line from the `plans/RESUME.md` index — a resume prompt for a shipped plan is stale instructions waiting for a fresh session to execute them.
6. **Re-check the moves.** Steps 4 and 5 are the ones that fail by omission — an orphaned shard or a surviving handoff leaves no error, just a file, and the session that made the move is the least likely to notice. Verify mechanically against the new path: master and every shard out of `plans/`, no surviving `plans/handoffs/<slug>-*.md`, no `plans/RESUME-<slug>.md`, no `plans/RESUME.md` index line naming the slug. When globbing for slug-matching files, **read the names, not the counts**: /plan slugs are not namespaced, so a sibling plan whose name starts with this slug matches a prefix glob too. (Where the full /plan skill is installed, its checker runs exactly this sweep via `--archive-move` — see Optional enforcement.)
7. **Confirm to the user** with the new paths and a one-line summary of what shipped.

### Mode 4: Refresh the next phase's shard and hand it off (`/plan handoff <slug>`, or the user asks for a handoff prompt, says they're pausing for a context clear, or wants to brief another session)

Applies to ANY plan with remaining phases. There is no "multi-session plan" designation and no plan-type precondition — any phased plan becomes multi-session the moment the operator clears, and that decision arrives mid-flight. Never decline or skip steps because the plan "wasn't meant to span sessions."

1. **Read in the Mode 2 step 1 order** — master, then the NEXT phase's shard in full, then referenced specs. The shard IS the recorded map; the handoff's job is to confirm and extend it, not re-derive it, and not to restate it (the shard already holds it).
2. **Gap-check the next phase's recorded map against the CURRENT code.** Confirm each symbol anchor exists (re-tag line hints with the current commit id), then enumerate as explicit addenda everything the code shows that the plan + spec do NOT record — especially drift introduced by phases shipped after the spec was written (a helper that gained side effects, a signature that changed, an adjacent modifier that looks like part of the region being extracted but isn't). The gap-check is the point of this mode; a handoff that only reformats the spec reproduces the lossy-compression failure this mode exists to prevent. Run Mode 2 step 2's mechanical checks as part of it: a stale probe record, or a `deferred to phase start` dry-run record for the NEXT phase, is a gap-check finding — write it into the shard as an addendum so the resuming session runs Mode 2 step 2's re-probe before it dispatches the phase's worker.
3. **Run the reuse check for any new file/helper the phase creates**: does an equivalent already exist anywhere in the codebase? What naming convention does the target directory use?
4. **Write the gap-check findings back into the shard, then hand off the shard pointer.** The NEXT phase's shard is already the self-sufficient packet — do NOT restate it into a separate handoff document; that re-creates the lossy-compression failure this mode exists to prevent. Instead: write every addendum from step 2 (drift, new call sites, signature changes) directly into the shard's Work / Decisions & findings, so the shard stays the single source of truth. The line the operator sends to the next session is **`/plan <slug>`** — nothing else.

**It MUST route through this skill, and "read the shard and execute it" does NOT.** A bare pointer never enters Mode 2, so the next session skips the staleness validation AND never reaches the Phase Completion Cycle — whose step 1 is "dispatch a fresh subagent; **it implements, you do not**." The session then implements inline, and three things collapse at once:
- **The Scope Check becomes a no-op.** It is written as a worker rule that ESCALATES ("the worker STOPS and reports it… the dispatching session asks the operator"). One entity playing both parts is just "continue".
- **Independent review is lost.** The dispatching session is supposed to gate a diff it did not write — including catching errors in the PLAN, which the plan's own author is the least likely party to challenge.
- **Step 5(a)'s findings transcription has no source.** "The worker held that context and no longer exists — transcribe from the report, not from your impression of the diff." No worker, no report, so findings get written from impression, which is the exact failure that rule names.

One phase per session survives this; from the second phase onward it compounds, because phase N's implementation detail is sitting in the context that scopes phase N+1.

**If the previous session has NOT stopped, say so in the handoff.** Both sessions share one working tree, and two of them writing it is how one silently clobbers the other. Name which files are in flight and which session owns them. Only when there's briefing that genuinely doesn't belong in the durable shard (operator-specific context, a one-time instruction) do you write a separate `plans/handoffs/<slug>-<phase>.md` with a leading "read the shard FIRST" instruction — and that file is deleted once the phase commits. Writing back into the shard IS a plan-file edit, governed by step 5.
5. **Plan-file edits depend on who's running this mode.** The distinction is about ownership of the plan, not about who typed the code — phases are implemented by dispatched workers either way, and a worker never edits plan files. If you are NOT the owning session (briefing a plan another session is running), never edit the plan file — concurrent edits confuse the owner; stale-plan findings ride in the prompt's write-back instruction instead. If you ARE the owning session (generating your own handoff before a context clear), bring the plan file current FIRST — run the Phase Completion Cycle step-5 refresh if the just-finished phase hasn't had one — then emit the prompt. The file and the prompt must agree; the prompt is for the next session's chat, the file is for every session after that.

### Mode 5: Abandon a superseded plan (`/plan abandon <slug>`, or the user says a plan is dead, superseded, or won't ship)

Shipped is not the only exit. Without this mode, dead plans linger in `plans/` where Mode 2 will happily resume them.

1. **Confirm with the user** which plan dies and why — one sentence.
2. **Prepend a banner** to the master (single-phase plan: the one file): `> **ABANDONED <date>:** <reason>. <Superseded by plans/<other>.md | No successor.>`
3. **Walk the Parking lot** the same way Mode 3 does — parked items in a dead plan are still ideas the user chose to keep. Disposition each one (file as follow-up / drop with reason) before archiving.
4. **Move the master AND every shard** to `plans/archive/` (create it if needed), `git mv` for tracked files, plain `mv` otherwise. Same move-together rule as Mode 3 — never leave orphaned shards in `plans/`. Sweep `plans/handoffs/<slug>-*.md` and any `plans/RESUME-<slug>.md` briefing (+ its `plans/RESUME.md` index line) the same way Mode 3 step 5 does — transient briefings die with the plan.
5. **Re-check the moves the same way Mode 3 step 6 does** — leftovers fail by omission, and here the stakes are higher: a resume briefing that outlives an ABANDONED plan is instructions to work a plan the operator killed, sitting where Mode 2 and a fresh session will find them. Run the same mechanical sweep against `plans/archive/` after the move.

`plans/archive/` holds dead plans; `plans/shipped/` holds completed ones. Mode 2 never resumes from either.

---

## Plan Template

The shape is a **mechanical rule, not a judgment call** — count the phases: **exactly one phase → the single-file plan. More than one phase → master + one shard per phase** (the no-exceptions wording lives in Rules: "Multi-phase plans MUST shard"). A master with more than one Phase-map block and no shard files on disk is malformed — the write step's mechanical self-check is where that gets caught.

**Both shapes, and the section-by-section guidance for filling them in, live in the Plan templates section below.** Read it before writing any plan file. The Scope Check, Phase Completion Cycle, Mode 2/3/4/5, and Rules all refer to those section headers by name — don't add, remove, or rename a section without updating every one of those references.

---

## Scope Check Behavior (while working the plan)

Once a plan is approved, these behaviors kick in for the rest of the session.

**Two parties do this work.** The **executing worker** is a fresh subagent that implements one phase; the **dispatching session** is you — you scope, dispatch, review, gate, commit, and talk to the operator. Every rule below says which one it addresses, because the worker cannot reach the operator and you are not the one holding the diff.

- **Before touching a file** *(worker rule, escalating to the dispatching session)*: compare the file path to the current phase's shard `## Work` (single-phase plan: the one `## Work` section). If it's not listed:
  - **The worker STOPS and reports it** — it does not edit the file, does not widen scope, and cannot ask the operator itself.
  - **The dispatching session asks the operator**: "This file wasn't in the phase — add it, park it, or skip it?"
  - **🛑 If the file DRAWS UI, the question is not just about the file — say what will look different, in the same message, BEFORE they answer.** Which screen, which state, and what a user would notice: a value that appears where nothing was, copy that changed, a section that now renders on days it used to hide, a colour or density shift. "Add it" for a view file silently means "yes, change that screen", and an operator answering a question about a *file path* has not agreed to a *visual change* they were never shown. Observable trigger, same as the UI-capture rule's: does the file draw UI. Not "is this change visual enough".
  - If they say add, edit the shard's Work to include it (explicit mutation), and the master's Files touched (overview) if it's a new file for the plan, then re-dispatch or continue accordingly. **The added file joins a TASK, not just a Work list** — it goes into the file set of the task the worker names as in flight (a phase with no task tier has exactly one in-flight task: its whole Work section). If no task's scope honestly covers it, it becomes a NEW task with its own one-line scope rather than being wedged into a task it does not belong to. Either way, **re-check disjointness at the mutation**: the file must end up under exactly one task, and an add that would list it twice is the signal the task boundaries were drawn wrong. This is the same re-fire pattern as the UI-capture rule below — the tier is checked every time `## Work` changes, not only when the plan is drafted, because the phase's spec check will attribute the diff by task and an unattributed file has nowhere to land. **When the added file draws UI, the phase also gains a capture Done criterion naming that surface** — the UI-capture rule below is written as a drafting-time check and has already fired by now, so nothing else will add it. A phase that acquires a UI file mid-flight and keeps its original Done criteria is a phase whose exit conditions describe a smaller change than the one it ships.
  - **🛑 A REVIEW FINDING on code already in flight is NOT this question — apply it, and do not ask.** *(Dispatching session only; the worker's STOP above is unchanged and stays correct.)* Observable trigger: a gate you ran — the project's review pass, a type checker, a linter, a failing test, the spec check — flagged something in the diff this phase already produced, and fixing it correctly requires editing a file the `## Work` list does not name. Fix it, note the file in the shard's Work and the master's overview, and say what you did in the checkpoint. This is not a scope decision the operator owns: the standing apply-don't-park rule names exactly three reasons to park — a cited technical constraint, a genuinely cross-cutting fix needing its own design pass, or the operator saying so — and "the file was not on the list" is not among them, so asking re-opens a decision the rules already made. **The two rules genuinely contradict each other and this clause is the resolution; do not re-derive it per phase.**
    **🛑 The UI rider above SURVIVES the dropped ask, and it is the one thing this carve-out must not take with it.** The bullet two above is written as a rider on the question — say what will look different BEFORE they answer — so removing the question would silently remove the operator's sight of a visual change too. It doesn't. When a review finding lands in a file that DRAWS UI, apply the fix and say what a user would notice, in the checkpoint, in their terms: which screen, which state, what changed. Same observable trigger (does the file draw UI), same content, later moment — the operator is being told rather than asked, and telling them nothing is not what "do not ask" licenses. If the fix changes a rendered surface in a way you would not have chosen without the finding, that is the case to raise on its own rather than fold into a checkpoint line.
    **Which findings this covers, precisely, because one gate sits on both sides.** It is about a NEW edit a fix requires, in a file the Work list does not name. It is NOT about a hunk the worker already wrote in an unlisted file — the spec check's backward axis calls that UNCLAIMED, and that genuinely is the add/park/skip question arriving late, because nobody authorised the edit that already happened. Fix-needs-a-file → apply. Worker-already-edited-a-file → ask. (And note the gate that reports the finding may be one you asked the operator to run rather than one you invoked — some review skills are operator-only; the carve-out keys on the finding existing, not on who pressed the button.)
    **And treat the out-of-scope file as a PLANNING defect, not a scope surprise.** A file the fix was always going to need, absent from the Work list, means the plan mis-scoped the phase — record it in the shard's Decisions & findings, in one line, naming what the plan should have seen. The Work list that named a policy on its `Consumes:` line while omitting the file whose only caller the phase replaces is the shape to look for.
  - If they say park, append to the master's parking lot with a one-line note
  - If they say skip, move on without touching it
  - **A worker that silently edited an unlisted file is the overshoot failure** — it poisons the next phase, which was scoped against a world where that file was untouched. Read the worker's report for this specifically; "I also cleaned up X" is the tell, and it is not helpfulness.

- **Checkpoint at meaningful boundaries** *(dispatching session)* — when a done criterion is met, when a phase's worker returns, and at every phase boundary: briefly state where we are in the plan. Example: "Done criterion 2 of 4 met. Files touched: install.sh, test_install_sh.py. Still in scope." (Not per-tool-call — a phase often runs dozens of calls, and per-call status is noise.)

  **If the phase touched a file that draws UI, the checkpoint states the VISUAL DELTA in the operator's terms** — what a person looking at that screen would notice, not which files changed. "The card's empty state now says X instead of Y, and it renders on days it used to hide" is the checkpoint; "`DashboardSectionView` modified" is not. A file list is not a description of a change to something a person looks at, and the operator reads these checkpoints instead of the diff.

- **When the user suggests something new mid-work** *(dispatching session)*: ask whether it replaces current scope, extends it (update plan), or parks it (parking lot). Default to parking lot unless they explicitly want to expand.

- **Before asking the operator ANY mid-phase question** *(dispatching session)*: state which case applies, and route accordingly — the stop conditions' "blocker requires a decision" is a wide door, and this test is its gatekeeper. **(a)** The project's own written rules — its CLAUDE.md, the plan's Locked decisions, a standing skill rule — already settle it: decide, name the rule in the checkpoint, proceed. Asking the operator to re-make a decision their own rules made is the interruption this exists to stop. **(b)** It changes what the user sees, or writes a durable value: it IS the operator's, but BATCH it — collect case-(b) questions and ask them once at the phase's review gate rather than as each arises. One exception: a worker STOP is never batched, because the phase is stalled behind it and holding a blocked worker trades wall-clock for politeness — resolve it now. **(c)** Neither: decide it and record it in the shard's Decisions & findings; the record is the accountability, not the interruption.

- **Commit per phase**: each phase ends with the Phase Completion Cycle below — the commit happens at step 4 of that cycle, not as a separate decision. Don't batch phases unless they're trivially small (e.g. two constant changes).

- **When done criteria are met**: the cycle's stop condition kicks in — stop, commit final state, offer to ship the plan (`/plan ship <slug>`). Do not start adjacent work without a new plan.

---

## Phase Completion Cycle

Once the plan is approved, every phase ends by running this cycle in order. **The cycle is the default behavior — do not skip steps and do not wait for the user to prompt the next one.** The user has explicitly authorized this loop by approving the plan.

1. **Dispatch** *(dispatching session)* — hand the phase's shard to a fresh subagent using the Execution brief section below. **It implements; you do not.** You have been in the conversation that scoped this plan, and you are the party most likely to soften a hard phase into a smaller one on contact — that is precisely what the fresh worker is for. It has no memory of anyone proposing something smaller, because nobody proposed anything to it.

   `subagent_type: "general-purpose"`, **no `isolation`** — the worker edits the main checkout so the next phase builds on its work.

1b. **Read the worker's report** *(dispatching session)* — not just its diff. If it stopped on a scope gap, a bigger-than-described fix, or a workaround it declined to build, resolve that before proceeding; the scope-drift stop condition governs. **A phase whose worker needed context the shard didn't carry is a defect in the plan, not a hiccup** — the report is the only place that surfaces, and skimming it is how an underspecified shard passes for a clean phase.

   **A MID-PHASE STOP leaves a partial diff, and it is yours to disposition before step 1c runs.** The worker names the task it stopped in; tasks before it are complete, tasks after it are untouched, and its edits sit uncommitted in the checkout — never reverted, never partially committed. Decide one of three, and say which in the checkpoint: resolve the blocker and re-dispatch a worker briefed with which tasks already landed; cut the remaining tasks into a new phase and let the spec check see a phase whose Work now matches what shipped; or abandon the partial work explicitly. **Do NOT fall through to step 1c with a partial diff and an unchanged Work list** — the spec check compares the diff against every Work item, so it will report each unstarted task as UNBUILT and the record line will book a phase with N uncovered items, which is indistinguishable afterwards from a worker that silently skipped them.

1c. **Spec check** *(dispatching session, or a dispatched reviewer — see below)* — **does the diff implement THIS phase's Work, and only it?** This runs BEFORE step 2, and the order is the point: a quality pass over code that implements the wrong spec is a pass over work that is going to be rewritten, and a review pass has nothing to say about a Work item nobody built.

   **Two axes, and only two.** (i) **Work coverage, in BOTH directions.** Forward: every Work item of the phase is evidenced somewhere in the diff — attributed per task where the phase has a task tier, which is exactly what disjoint task file sets buy you (a changed file belongs to one task, so "which task does this hunk discharge?" has one answer). Backward: every changed file is claimed by some Work item, and a diff hunk in a file no Work item names is a finding. That second direction is what closes the silent-unlisted-edit hole the Scope Check's "a worker that silently edited an unlisted file is the overshoot failure" bullet names but cannot detect — that bullet tells you to read the report for it, and a worker that did not mention the edit leaves nothing to read. (ii) **Interface conformance** — the shipped signatures and types match the phase's approved `Consumes:` / `Produces:` lines.

   **NOT Done criteria.** Mode 3's ship walk owns those, and it walks both levels. Two records claiming the same ground is how they come to disagree; this one is scoped to the spec.

   **Who runs it is an observable trigger, not a judgment call — count the phase's tasks.** **Exactly one task → the dispatching session runs it itself**, the same self-performed duty as the downstream sweep, and for the same reason it is legitimate here: the session reviews a diff it did NOT write, which Mode 4 already names as one of the three things lost when a session implements inline. **More than one task → dispatch an independent spec reviewer** — brief 5 in the Plan audit briefs section below, `subagent_type: "general-purpose"`, read-only, and its inputs are PASTED text (the shard, the worker's report, the exact diff), never a path to read.

   **It ends with a record line in the master's Status** (single-phase plan: `## Status`), bold-led, carrying counts, in the shape the Status-section commentary of both templates in the Plan templates section below specifies:

   ```
   **Spec check at p4** — tasks 3/3 evidenced · interfaces conform · none uncovered
   ```

   Counts are mandatory for the same reason they are mandatory on an auditor's report: if the pass cannot name what it checked, it did not run.

   **🛑 A step-2 review fix that ADDS a file invalidates this record, and a stale record is worse than a missing one** — the missing one alarms, the stale one certifies. The 1c-before-2 order is deliberate and stays, so the repair is at the other end: when a review finding adds a file to `## Work` under the Scope Check's carve-out, append it to this phase's record line rather than leaving it claiming `none uncovered` over a diff it never saw. `· +1 file added at review (<path>), re-evidenced` is enough. Cheap, because you know exactly what you added and why; the alternative is a record that reads as coverage of the final diff and is not.

   **A shipped phase with no spec-check record is the same resume-time alarm as a missing sweep record** — run it retroactively against the phase's commit before working the next phase, don't assume it happened and went unwritten.

   **An interface mismatch found here is a re-plan trigger, not a fix.** The approved `Consumes:` / `Produces:` lines are contract; a shipped interface that differs from them fires Mode 2's approach-switch trigger at this phase. Do NOT edit the interface lines to match what shipped — that is the bookkeeping-instead-of-escalating failure, and it silently re-ratifies a design change the operator never saw. A Work-coverage gap routes normally: an unbuilt item is unfinished work, an unclaimed hunk is the Scope Check's add/park/skip question arriving late.

2. **Simplify** *(dispatching session)* — get the project's review pass run on the changed code (`/code-review` or an equivalent, if your setup has one). If the review skill is operator-only — not invocable by the model — use a model-invocable equivalent where one exists; otherwise STOP and ask the operator to run it. Do not skip the gate because you can't press the button.

   **Two escape hatches, both narrow:**
   - **No source files in the diff** → skip the review pass regardless of size or file count, and run a **reference check** instead (not step 5(c)'s downstream sweep — different target, no record line): does every pointer still resolve, did this change invalidate vocabulary used elsewhere, is any instruction now addressed to the wrong party or contradicting a neighbour. A bug hunt has nothing to say about prose — this is the check that does, and skipping BOTH is not the carve-out. Where the project's commit gate already defines which extensions count as code, use its definition rather than inventing a second one.
   - **Trivial code diff** → single-file AND single-logical-change AND no behavior change (typo fix, version bump, comment rewording). When in doubt, run it.

   A MIXED diff — any source file at all — gets the full review. The doc hatch is for diffs with zero.

   Then run any additional review gates the project's own instructions (CLAUDE.md or equivalent) mandate for this diff type — UI review passes, screenshot evidence, lint gates. Project gates compose with the review pass; they don't replace it, and neither hatch waives them.
3. **Test** *(dispatching session)* — run the project's canonical pre-commit test gate: the full suite, unless the project's own instructions define the gate for this diff type. A green subset the project's gate doesn't sanction is not green. If tests fail, fix before proceeding. Never commit red.
4. **Commit** *(dispatching session — the worker never commits)* — one commit per phase with a descriptive message that ties back to the plan / done criterion. Review and gate come first, and both are yours; a worker committing before anything checked its work is the ordering this step exists to prevent. Use `Fixes #N` if the phase closes an issue. If a handoff file `plans/handoffs/<slug>-<phase>.md` exists for the just-committed phase, delete it now — its lifetime ends at this commit (Mode 4 step 4 states the contract; this step is its enforcer).
5. **Write findings into the shard, then refresh the map** — on any multi-phase plan, before advancing, do BOTH, in this order, and do NOT skip either:
   - **(a) Seal the just-finished phase's shard** *(dispatching session, sourced from the worker's report)*. Write its `## Decisions & findings` to final state: every non-local decision it settled (Decision / Rationale / Alternatives / Evidence) and **every empirical finding the phase actually discovered** — spike results, gotchas, anything the next phase or a cold session would otherwise rediscover.

     **The worker held that context and no longer exists.** Its report section 2 is the entire record; the moment you move on without transcribing it, those findings are gone — not at the next context clear, but immediately. Transcribe from the report, not from your impression of the diff. A finding you can see in the diff was never the risk; the risk is the one the worker hit, understood, and mentioned once.
   - **(b) Advance the master** *(dispatching session)*. Mark the just-shipped phase in the Phase map and Status (commit id), promote the next phase to NEXT, lead Status with the "read `plans/<slug>-<phase>.md` FIRST" pointer, and pull that next shard's 2-3 binding decisions inline. Confirm the next shard still passes the self-sufficiency test against current code; update any line-number hints to the just-committed state and tag them with the commit. **Then write the sweep record from (c) into Status — (b) is NOT complete without that line.**

   - **(c) Sweep the DOWNSTREAM shards for drift this phase caused** *(dispatching session — this is judgment over the whole arc, which is exactly what a fresh worker does not have)*. Open every shard that has NOT yet shipped and answer the first three questions below against each one, then the fourth against the SOURCE the earlier phases shipped. This is not optional and it is not "if something feels off" — a phase changes the world the later shards were written about, and the world the earlier ones built in, and nothing else in this cycle checks either.
     - **What is now DONE?** A later shard's work item the just-finished phase shipped early, or that its approach made moot. Mark it done (with the commit) or delete it — don't leave a phase instructed to build something that exists.
     - **What has DRIFTED?** Any claim or instruction that the phase just falsified. Hunt the *vocabulary* of what changed: grep the unshipped shards for the symbols, file paths, feature names and UI affordances this phase touched, plus temporal phrasing that goes stale by construction — "not yet", "until p<N>", "currently", "has no", "there is no way to". **A false instruction is worse than a stale note**, because a later phase will follow it: a shard that says "verify this by reading the code, since the UI is unreachable" sends that phase down a wrong path once the UI is reachable.
     - **What was CUT or ADDED?** Scope that moved between phases, got dropped, or got absorbed. Push it to the shard that now owns it, and reflect it in the master's `## Files touched (overview)` and `## Non-goals` if the boundary moved.

     Shards that have ALREADY shipped are history — when their text is falsified by later work, mark it historical ("...when this shard was written") rather than rewriting it, so the record of why the phase did what it did survives.

     Cheap and mechanical, so there is no excuse to skip it: `rg -n "<symbol>|<affordance>|until p|not yet|currently|has no" plans/<slug>-p*.md`.

     - **What did this phase OBSOLETE in the code EARLIER PHASES SHIPPED?** The three questions above search plan files. This one searches source, and it is the only sub-step that does. **Trigger (observable):** the phase PINNED, constrained, centralized, or narrowed something — a domain, a range, a schema, a route, an invariant that used to be free. Ask: what did an earlier phase build *because* that thing was unconstrained? A guard, a threshold, a fallback, a clamp, a defensive branch — and the comment justifying it, which is now arguing for a world that no longer exists.

       Grep the earlier phases' shipped files for the guards, not for the new names: the obsolete thing does not mention what replaced it. Every hit gets one question — *would anyone write this line against the code as it stands now?* If not, it is dead weight at best and a false constraint at worst, and it is THIS phase's to remove, because this phase is what killed it. A stale guard is worse than a stale comment: the comment misleads a reader, the guard changes what the user sees.

     **Grep the vocabulary the phase INVALIDATED, not only what it added.** A sweep that searches for the new names finds the shards that need to learn them, and misses the ones still confidently repeating the old ones. Both lists, every time: what this phase introduced, and what it made false. The second list is where false instructions hide, and a false instruction is worse than a stale note because the next phase will follow it.

     **A constraint this sweep carries INTO an unshipped shard lands in that shard's `## Done criteria`, or it does not carry.** Prose in a carry-in note has no exit condition: a constraint can be discovered by research, delivered by two sweeps, paraphrased by the worker in its own doc comment — and still be wired backwards, because nothing in the phase's exit checked it. So every carried constraint is promoted to a Done criterion on the receiving shard, phrased as the OBSERVABLE it protects — "a partially-loaded day renders its fillable fields un-redacted; the loading shimmer only on the not-fetched state" — never as relocated prose; "handle partial days correctly" is the same sentence that already failed, moved down the page. The sweep record names the criterion it added, so the promotion is visible in the same line that already claims the shard was opened.

     - **Fifth question — did this phase's review or gates find a defect of the same CLASS as a prior phase's?** Read the earlier phases' recorded findings for the class, not the instance: "tests that cannot fail", "only the named states handled", "built on a stale contract". A class that recurs is not a phase finding — it is a plan-level one, and it becomes a Done criterion on EVERY unshipped shard. The criterion carries the CHECK, not the intent — "every new or changed test is mutation-checked: state the mutation and both pass counts", never "tests must be meaningful" — and it must be one the phase's worker satisfies inline; a criterion that needs a new agent per phase is cost creep this cycle does not authorize. **That ban governs sweep-escalated CRITERIA only** — it does not reach step 1c's reviewer, which is a cycle step chosen on an observable trigger, not a criterion this sweep minted. Name the escalated class in the sweep record.

     **(c) ends by writing a SWEEP RECORD into the master's Status — one line, naming EVERY unshipped shard and what happened to it, and closing with the code sweep:**

     ```
     Downstream sweep at p4 — p5 banner added · p6 banner added · p7 2 items corrected · p8 clean · p9 2 items moot · p10 clean · p11 1 item extended · code: pinned the x domain, checked p1's coverage gate + p2's clamp, gate obsoleted and removed
     ```

     The `code:` segment carries the same burden as the shard names: it says what this phase constrained and which earlier-phase guards you opened because of it. `code: nothing pinned` is a valid segment when the trigger didn't fire — its ABSENCE is what must never be.

     **This line is the enforcement, and it is why (c) cannot be skipped quietly.** (a) leaves a findings section behind and (b) leaves a status edit; a sweep that finds no drift leaves NOTHING, which makes "swept, all clean" and "never swept" indistinguishable at the next cold start — so the sub-step with no artifact is the one that silently rots. Naming every shard, including the clean ones, is the point: `p8 clean` is a claim you had to open p8 to make, and its absence from the line is visible forever. A sweep record that lists fewer shards than exist unshipped is an incomplete sweep, not a tidy one.

     **Verify before writing it, and never write it from memory or intention.** The record is a factual claim about files you opened this session. If you cannot name what you checked in each shard, you have not run (c) — run it, then write the line.

     A resuming session treats a missing sweep record for the last shipped phase as a **drift alarm**: the downstream shards may carry instructions that phase falsified, so run (c) retroactively for that phase before working the NEXT one. Do not assume it was done and left unrecorded. **A record present but carrying no `code:` segment is the same alarm, narrowed** — the shard half ran and the source half did not, so re-run only that half.

   This runs at EVERY phase commit, not only when a session handoff is known to be coming — the operator's decision to clear the session arrives after the commit, not before, so findings written only when a handoff is foreseen are missing exactly when they're needed. Skip only when the phase just committed was the plan's last (the plan ships instead).
6. **Advance** *(dispatching session)* — if any done criteria are still unmet, dispatch the next phase's worker. State a one-line status update ("Phase 2/4 done, starting phase 3") — this is a *status*, not a question. Never ask "should I continue?" — the approved plan is the standing authorization.

   **One check runs between the commit and that dispatch: the next phase's probe triggers.** Mode 2 step 2's re-probe rule fires on resume, but a plan worked start-to-finish in one session never enters Mode 2 — this advance is where a continuous session sails past it, and the incident behind the rule ran exactly that way. So before dispatching, apply the same two triggers to the phase you are about to start: does the verification record defer its probe (`deferred to phase start`), and did the just-committed phase expire a pending probe record (its dry-run entry now names a since-shipped phase beside a still-pending one) — the phase just committed may itself be what expired the record. Either fires → run Mode 2 step 2's re-probe (same brief, worktree isolation, same routing) before the worker. Neither fires → dispatch immediately as ever.

### Stop conditions (override step 6)

The cycle stops — and you wait for the user — only when one of these is true:

- **All done criteria met** (every shard's AND the master's plan-level) → commit the final state, then offer `/plan ship <slug>`.
- **Blocker requires a decision** → ambiguous spec, broken external dep, conflict with a non-goal, or a question the plan didn't pre-answer. Surface the specific question; don't keep advancing.
- **Scope drift detected** → a file outside the current shard's `## Work` needs editing, or the work has expanded past the plan's bounds. Use the "Before touching a file" rule (add / park / skip). **🛑 EXCEPT a review finding on code this phase already produced — that is NOT this stop condition**, it is the carve-out in the Scope Check, and it is applied rather than asked about. This list is what a session consults when deciding whether to halt, so without this sentence the carve-out gets re-derived every phase and loses to the halt list.
- **The worker reported a gap, a bigger-than-described fix, or a workaround it declined to build** → stop and resolve it. All three mean the shard was underspecified or the design was wrong, and all three are cheap now and expensive two phases later. Do not wave through a "STOP and report" the worker was explicitly instructed to raise.
- **Tests stay red after a reasonable fix attempt** → don't loop indefinitely; surface the failure and ask.
- **User interrupts** → defer to user input, then resume from wherever the cycle was.

If none of the stop conditions apply, the next phase starts automatically.

---

## Rules

- **Multi-phase plans MUST shard — this is mechanical, not a judgment call.** More than one phase → a master `plans/<slug>.md` PLUS one shard `plans/<slug>-<phase>.md` per phase; the single-phase plan is the ONLY non-sharded shape. There is NO "small multi-phase" exception, NO "it reads cleaner as one document," NO "fewer files is simpler" — two phases shard, six phases shard. The instant you notice you're about to put a second phase's work in the first phase's file, STOP and shard; simplicity is conceptual load (one phase = one self-contained shard), never file count. (The monolith failure this stops is described at the multi-phase template intro.)
- **A phase's research and decisions live INSIDE that phase's shard — never elsewhere.** Never a separate `<slug>-research.md` or `<slug>-<phase>-research.md`; never only in the master. The shard's `## Work` is the execution brief, its `## Decisions & findings` is the durable record (Decision / Rationale / Alternatives / Evidence, with `superseded by phase-<id>` markers). A separate research file is the monolith by another name and breaks the shard's self-containment — the one property the whole layout buys. The ONLY research in the master is cross-phase Background findings that belong to no single phase. **This rule is about PLAN files, not about this skill's own machinery** — the briefs and templates in the trailing sections here are prompt templates and document shapes, not the separate-research-file this bans. A live plan's research is the thing a future phase has to execute from, and that lives in that phase's shard, always.
- **The Phase map is mandatory for every multi-phase plan and carries EDGES, not nodes.** Every phase listed with enter-gate, done-signal, branch-on-failure, and shard path. It is NOT where work is detailed — that's each shard's Work. A multi-phase master with no Phase map is incomplete and MUST NOT be presented for approval; a Phase map that restates shard Work detail is the duplication it exists to prevent.
- **A decision earns a `## Decisions & findings` entry only when its consequences are NON-LOCAL** — scattered across the code, not contained in one spot. Local one-liners stay in the master's Non-goals. This threshold is what keeps "robust" from meaning "everything written twice"; apply it, don't record every passing choice. This threshold governs multi-phase shards; in a single-phase plan the bar is lower — record any choice a reviewer would ask "why this way?" about, because a single-phase plan is the only record of its own reasoning and has no shards to hold it.
- **/plan adopts clu-plan's master+shard STRUCTURE, never its dispatch machinery.** /plan is project-agnostic. Do NOT write a machine-parsed `## Sessions index` table, an `Effort`/lease column, attestation steps, or `clu complete` callbacks into a /plan plan — those are clu-specific. The Phase map is the generic phase index. If you catch yourself writing a Sessions-index table into a /plan plan, you've leaked clu specifics into the generic skill — stop and use the Phase map.
- **Never write code before the plan is approved — and dispatching a worker IS writing code.** Not even "just to set up scaffolding." The plan is the scaffolding. Writing it by proxy is still writing it; an unapproved plan does not become approved by being handed to someone else.
- **One active plan per conversation.** If the user wants to work on two things, they get two plans, and we tackle them sequentially.
- **The template is the source of truth, and it lives in the Plan templates section below** — there are two shapes (single-file plan; multi-phase master + per-phase shard), and the Scope Check, Phase Completion Cycle, Mode 2/3/4/5, and Rules all refer to their section names. Don't add, remove, or rename a section in either shape without updating every one of those references.
- **Be ruthless about non-goals.** If you're unsure whether to list something as a non-goal, list it. Easier to remove than to add mid-work.
- **Archive, don't delete.** Shipped plans move to `plans/shipped/` — they're a record of what got done, not garbage to collect.
- **Anchor on symbols, not line numbers.** Plans and specs with more than one phase must use symbol names (functions, types, properties) and distinctive code snippets as their primary anchors — every committed phase shifts the line numbers the next phase's notes cite, so raw `:NNN` references rot by design. Line numbers are allowed only as secondary hints tagged with the commit they were measured at, and the cold-start refresh (Phase Completion Cycle step 5) restates them when they drift. A fresh session re-anchors by grepping the symbol, never by trusting a stale line.
- **A resumed plan is a hypothesis, not a license.** Validate per Mode 2 step 2 before working any pre-existing plan; if scope or approach has materially diverged, re-run Mode 1 step 4 EXPLORE and rewrite — never patch a stale plan turn-by-turn while coding. The re-plan triggers (including the smaller-diff approach switch) are enumerated in Mode 2 step 2 — that list is the rule.
- **EPCC: Explore is unconditional.** EPCC = Explore → Plan → Code → Commit. The Explore step (Mode 1 step 4) runs every time, before a single line of plan text gets drafted. All three teams — change impact (A), adversarial code read (B), implementation specialists (C) — run regardless of plan size, file count, or category. There is no "small plan" exception, no "I already read the files" escape, no `--no-research` opt-out, no "pure docs/config" carve-out. **The one conditional part is Team A's skip, and its trigger is observable, not self-certified:** does this plan modify code that already exists? Read the Work list. "It's basically greenfield" is not the test. If a task is genuinely too trivial to warrant exploration, it's too trivial to warrant `/plan` in the first place — do it directly. This rule governs whether the teams RUN, never their headcount — presence is unconditional; scale is step 4's agent-count table plus its single-phase floor.
- **Every written plan gets an adversarial read-back before the operator sees it — ONE dispatch, ONE fix pass.** Mode 1 step 7 — three read-only agents on disjoint axes (grounding, executability, coherence), briefed verbatim to search as though the plan is wrong, and required to verify by opening sources rather than by reasoning. **The coherence axis exists because grounding and executability both pass a plan that contradicts itself:** grounding checks claims against the code, executability checks shards against each other, and neither reads a stated rule against the mechanism the same plan specifies. The same axis catches the characterization whose citation resolves but whose description is false — a named row described as living on a card it never appears on, correct `file:line` and all. No "small plan" exception, no "I verified as I drafted" escape (the drafting session grading its own grounding is the thing being checked). Findings BLOCK. There is no second round; what you cannot close cleanly is promoted to the operator instead. The record names every fix, not just a count — that naming is what replaces the re-read. Fires wherever plan text is minted or materially rewritten — Mode 1, the Mode 2 re-plan and legacy-reshape paths, an approval-time restructure, and (as one scoped re-probe rather than a full pass) a fix-pass NEW MECHANISM — never only on the first draft.
- **No research deferrals — verify or block.** Every cited file path, API name, metadata key, version number, framework behavior, or external system claim is verified this session and stated as fact with a file:line or URL+section citation — or the plan isn't drafted. There is no `TODO: verify` channel, no "confirm during implementation," no placeholder, no flag opt-out, no carve-out (same unconditional standard as EPCC). If a claim can't be closed by research, STOP and resolve it with the operator before drafting (provide access, run it, or pull it from scope). The guess/fact line is enforced by absence: every claim in a drafted plan is verified — not "unmarked," verified. Empirical unknowns that genuinely need runtime are not deferrals — but **needing runtime says what closes a question, never when.** This skill dispatches worktree agents that build and run code, so an unknown whose answer would change the phase map is settled at PLAN time by a comparison probe (Mode 1 step 4), and only what survives that becomes the Diagnosis falsifiable test or the algorithmic load-test, each of which keeps its OWN placement rule — the Diagnosis test runs before any shard's Work is scoped, the load-test lands at the earliest practical phase — and neither is a fork this step settles. **A phase whose done signal is "a decision is recorded" rather than "something works" is the tell that this was skipped** — every phase after it was drafted against an assumed answer, and the research that was supposed to find what the choice breaks ran before the choice existed.
- **Generic-skill discipline.** This skill is global — it ships across every project regardless of language or framework. Skill text MUST NOT hardcode paths, language conventions, or specific framework names beyond illustrative examples. Each agent's brief describes the *shape* of what to look for; the agent figures out where this project's equivalent lives (e.g., `node_modules/<dep>` for JS, `~/.cargo/registry/src/` for Rust, `site-packages` for Python, vendored docs folders for any project, the vendor's official docs site via WebFetch for any language). If you find yourself writing a project-specific path or framework name in the skill body, replace it with the generic shape and an illustrative example list.
- **Mid-implementation pivot rule.** If the first diagnostic experiment under an approved plan disproves the hypothesis (e.g. "I disabled X and the symptom didn't change"), STOP. Don't try a second guess. Return to Mode 1 step 4 (Explore) with the new evidence as a sharper question — the plan was scoped at the wrong target and patching it forward will compound the error. Two failed disable-experiments back-to-back is a hard signal to re-explore; if the symptom is genuinely opaque after that, hand off to a dedicated diagnosis pass (e.g. a `/diagnose` skill if your setup has one).
- **New SOURCE file mirrors an existing SOURCE file? Refactor first by default. Code only — this rule does NOT apply to markdown, docs, skill definitions, prompt templates, or config.** When the plan adds a new source file the description says "mirrors" / "like" / "similar to" / "same family as" an existing one — OR a sibling file with the same suffix already exists in the target directory — the reuse-specialist agent is mandatory during Explore and its Phase-0-refactor recommendation is presumed correct unless the user explicitly overrides at plan approval. The refactor becomes phase 0 of the plan; the new feature is phase 1+. Copy-and-defer requires an explicit user override at approval, recorded in the Parking lot in writing — not a passive default that quietly leaves duplication for a review pass to surface after the duplicate ships.
- **Algorithmic plans: land the research load-test at the earliest practical phase, not "whenever it's convenient."** The minimum executable test that would catch a naive implementation (research's question 3) is the falsifiable claim that proves the research is grounded. The default placement is phase 1's first commit, *before* the rest of phase 1 — the test runs against the simplest possible implementation and gates further work. If the test genuinely cannot be run until phase 2 (e.g. it needs integration plumbing that doesn't exist yet, or the LLM pipeline only behaves under realistic load), that's allowed, but the plan must explicitly call out the gap and the test still becomes the *first thing* in phase 2, not buried mid-phase. If the test fails when it lands, the research was incomplete — return to Mode 1 step 4 (Explore) with the specific failure mode as a sharper question, don't paper over it with tuning. This catches "research was insufficient" at phase 1-2 instead of phase 3+.
- **A Work list is proven by attempting it, never by reading.** When the plan modifies existing code, Mode 1 step 7 dispatches worktree-isolated dry-run prober(s) alongside the auditors — aimed at phase 1 (and the structural phase, when one shaped the map) at plan time, with the LARGEST-Work phase probed at its OWN start via Mode 2 step 2 when it is a later phase, because a plan-time probe of a late phase implements against stubs and expires when the phases beneath it ship — and every file one reports MISSING is added to the Work list, not weighed, added. The probers are also the only agents in that step that EXECUTE rather than read, so they are the only ones that can catch a work-shape sketch which does not compile as written; expect that channel to pay out more often than the file list does. Their stop condition is conditional to match: a prober stops at green only when no Done criterion names an observable output — when one does, it produces the observable, measures it against the criterion, and reports any mismatch as MEASURED, the plan's own claim falsified. No auditor can find a file that neither the master nor any shard names: the set-mismatch check compares the two lists against each other, so a file absent from both is invisible to it.

- **UI plans: a visual capture is a MANDATORY Done criterion on every phase that changes what renders.** The trigger is observable — the phase's `## Work` names a file that draws UI — not a judgment about whether the change is "visual enough". The criterion names the state to capture, not just "screenshot it": which screen, which data condition, and what the capture is supposed to prove.

  **This rule re-fires every time `## Work` CHANGES, not only when the plan is drafted.** A phase acquires UI files mid-flight — through the Scope Check's add path, through a dry-run prober's MISSING list, through a review finding that lands in a view. Each of those re-runs the trigger against the new Work list, and each can add a capture criterion to a phase already in progress. Read as a drafting-time check only, this rule passes a phase whose Done criteria describe the change it was scoped for rather than the change it is shipping.

  **A capture is evidence only if it POST-DATES the last edit to the surface it shows.** Before presenting any capture, check it against the commits and edits that landed after it was taken; if the surface moved, the image is a picture of something that no longer exists and presenting it is worse than presenting nothing, because it reads as verification. Re-capture, don't caption around it.

  **When the change alters how LONG a rendered string can get, the criterion must also name a stressed text size** — the largest ordinary size and one accessibility size, captured, not reasoned about. Text that grew is text that can now wrap, truncate, or push its neighbours out of a row that fitted before, and no test in any suite sees it. Trigger is observable: a value's format changed (a count became a duration, a number gained a unit, an abbreviation became a word), or a label was added to a fixed-width row.

  **A phase that changes what a user sees, and whose Done criteria can all be satisfied by a green test suite, is incomplete — send it back before presenting the plan.** Where the project's own instructions already demand screenshot evidence, this rule makes the plan carry it as an exit condition rather than leaving it to the commit gate to remember.

- **NO phase's Done criteria — UI or not — may be FULLY satisfiable by a passing suite plus greps. Every phase names at least one observable.** A rendered state, a measured value, a produced artifact, a behavior under a named degraded condition — something the phase must PRODUCE and check, not only keep green. The UI-capture rule above is the screen-shaped case of this; the rule itself has no UI trigger. It is also the precondition for every probe in this skill meaning anything: the prober's MEASURED channel and Mode 2's phase-start re-probe both key on Done criteria that name observables, and a prober aimed at all-grep criteria stops at green by instruction — the probe is starved, not stupid. The incident behind it went 4,795 tests green with 25 verified defects present, several user-visible.

- **Perf/bug plans: run the Diagnosis falsifiable test BEFORE scoping any shard's Work.** Protocol per the Diagnosis commentary in the Plan templates section below (confirmed → scope normally; disproved → back to Mode 1 step 4 with the negative result as the sharper question). Files-read alone doesn't ground a diagnosis; "I commented out X and the symptom didn't change" does. Escalation after repeated failed experiments is the Mid-implementation pivot rule above.
- **Justify non-goal exclusions across peer sets.** Every peer-set exclusion needs the one-sentence "why this asymmetry is safe" rationale per the Non-goals commentary in the Plan templates section below — if you can't write it, fold the excluded items in. The exclusion-safety specialist (Mode 1 step 4) surfaces this as a forced binary decision at approval; trust its default-include recommendation unless you have an iron-clad invariant.
- **User-facing decisions need a recorded sign-off — never inherit them as "locked."** A decision that changes what the user sees or how the feature behaves (show vs hide a value, a default, a copy change, a state that disappears) does NOT become a `## Locked decisions` entry just because a divergent-design master, a spec, or a prior session wrote it down. Unless the plan can cite an explicit operator sign-off — a "chosen: X" record carried from a divergent-design pass, or an approval in the plan's Status — surface it at plan approval (step 8) as a forced binary decision the user must make, same treatment as the reuse and exclusion specialists; don't soften it. Inheriting a user-facing call as settled is how a hidden behavior flip ships without the operator ever choosing it.

  **Shipped code is an inheritance channel too, and it is the one that hides best.** A threshold, default, or cut-off the plan CARRIES FORWARD is a user-facing decision on exactly the same terms as one the plan invents — the operator never chose it either. The tell is the reassuring phrasing: "preserves current behavior", "keeps the existing threshold", "translates the old gate to the new units". That sentence describes *fidelity to the old value*, which is not an argument that the value is right, and it is the form under which an undefended number gets silently re-ratified by an approval.

  So whenever a plan restates a magic number, a visibility gate, or a cut-off from existing code, it states in one line **who chose it and on what evidence.** No answer → it is unowned, and it goes to the operator at step 8 as a forced binary decision like any other. Untouched code needs no such audit; this fires only on values the plan is actively rewriting, where the cost of asking is one sentence.

- **Every plan is drafted as though a DIFFERENT session executes it. Unconditional, both shapes.** Not "when the plan looks long", not "for multi-phase plans", not "if a clear is expected" — the assumption is standing, and it is not a prediction about session boundaries. It is a fact about who does the work: every phase is implemented by a fresh worker holding one pasted shard and none of the conversation that produced it, and that is as true of a single-phase plan written and executed inside one hour as of a six-phase plan spanning a week. So a fact settled in conversation is written into the plan or **it does not exist** — there is no "we discussed it" channel. The test is the same for both shapes and is applied at drafting and at every refresh: *could a fresh session execute this from this text alone?* In a multi-phase plan the unit under test is the shard; in a single-phase plan it is `## Status` plus `## Work`. A rule gated on "will this span sessions?" is a rule a session can self-certify out of right up until the moment it matters, which is why this one has no gate.
- **Approved interface lines are CONTRACT, and a shipped interface that differs from them is a re-plan trigger.** Every phase's `## Work` carries `- Consumes:` / `- Produces:` bullets — mandatory with a literal `none` as a valid value, absence the only invalid state — per the Interface-lines commentary in the Plan templates section below. What the operator approved was those signatures and types, so shipping different ones is an approach switch, and it fires the Mode 2 re-plan trigger at that phase exactly like any other. The Phase Completion Cycle's step-1c spec check is what detects it; the downstream sweep may notice it too. **Neither one resolves it by editing the lines.** Rewriting the approved interface to match what shipped converts an escalation into bookkeeping and silently re-ratifies a design decision the operator never saw — the same failure shape as inheriting a user-facing value because the code already had it.
- **The task tier is `### Task N` headings inside `## Work`, with DISJOINT file sets.** A phase whose Work splits into more than one job names each with a `### Task N — <one-line scope>` heading inside its `## Work` section. Three rules, none negotiable: **(1)** each task's file set is disjoint from every other's — a file appears under exactly one task, and a phase whose work cannot be split that way is formally unsplittable, so leave it untiered rather than listing a file twice; **(2)** file-path-leading dash bullets stay the ONLY path carriers — a numbered list is invisible to a path extractor, so files listed that way disappear from every drift check that reads the Work list; **(3)** no per-task Done criteria — criteria stay phase-level, because a task is a unit of scope attribution and not a unit of exit. Tasks are consumed in order by ONE worker and are not commit units: the phase is still one commit. The tier has THREE mechanical consequences, not one. It routes the spec check — more than one task dispatches an independent reviewer, exactly one runs in the dispatching session. It is audited at plan time — the executability auditor names every file appearing under two or more tasks of a phase and reports the task count per tiered phase. And it is re-checked at the write step's mechanical self-check — per-shard task disjointness is on that list. So a tier is structure that three separate gates read, never a presentational device: adding one to a phase that does not need it manufactures work for all three, which is why a lone `### Task 1` is banned rather than merely discouraged.

---

## Optional enforcement

Everything above is stated as the session's OWN obligation, because a public clu install ships this file alone and enforces none of it mechanically. Where the operator's full /plan skill is installed, three checks pick up part of the load: `~/.claude/skills/plan/scripts/plan-check.sh` runs the mechanical artifact-shape set (the write-step self-check, Mode 2's staleness diff, the Mode 3/5 `--archive-move` sweep) — it has no PASS state, always exits 0, and ends by naming the judgment obligations that stay yours; a dispatch-gate hook denies plan-marked agent dispatches that use `Explore`/`Plan`, and denies research briefs missing the three verbatim boilerplate fragments; and a draft-gate hook freezes source edits while a plan in the repo reads `**Approval: DRAFT**`. This fork keeps every marker and fragment those checks key on — the `Approval:` markers, the `Plan slug:` / `Plan audit:` / `Plan work:` brief openers, the verbatim fragments — inert without the hooks, recognized wherever they exist.

---

## Research team briefs

Loaded by Mode 1 step 4. **Pass these verbatim.** Paraphrasing them into one generic "research this area" is the degradation the team split exists to prevent — a generalist mentions the load-bearing detail in passing, consolidation buries it, and the bug surfaces three phases later.

Every agent is `subagent_type: "general-purpose"`. Never `Explore` — it omits CLAUDE.md and there is no setting that opts it back in, so an Explore agent researches without the rules that make its research correct.

**One narrow exception.** Mode 1 step 4's agent-type rule permits `Explore` for a pure locate-this-symbol sweep where losing the standing rules costs nothing. None of the briefs below is such a sweep, so none of them may use it. A dispatch that genuinely is one carries this sentence verbatim at the start of a line, and the sentence is an on-record claim about that dispatch:

```
Explore carve-out: this agent only locates symbols. It judges nothing, audits nothing, and grounds no claim.
```

### Boilerplate — append to EVERY brief below

```
Plan slug: {slug}. Goal: {one-line goal}.
You are NOT to invoke the /plan skill. Research only. Report in under 400 words.
Cite file:line for local sources, URL+section for fetched ones. A claim you did
not open a source for is reported as unverified, not as a finding.
Diff size, file count, and implementation effort are not your inputs. Recommend
what is correct.
```

### Team A — CHANGE IMPACT

**Fires when the plan modifies code that already exists.** Skipped only for plans that create new code and modify none. Read the Work list to decide; "it's basically greenfield" is not the test.

**Neutral brief rule applies.** A-team agents get the operator's goal in the operator's words and the files in play — **never the approach under consideration.** "We're adding a helper to X" hands the agent your assumption and it will come back agreeing with you.

#### A1 — fan-in and observable behavior

```
Map who depends on {files/symbols in play}, and how far the dependency reaches.

- Direct callers, then callers of those callers, out to the point where a
  difference would become visible to a user or to another system. Stop there
  and say where you stopped.
- For each call site: what does it assume about this code that isn't in the
  signature? Return-value shape, nullability, whether it can throw, whether
  it's safe to call twice.
- Which call sites would keep compiling but start behaving differently if the
  change goes in? Those are the dangerous ones — a compiler error is a fixed
  bug, a silent behavior change is a shipped one.

Do NOT report a file map or a directory structure. If your report reads like an
inventory of what exists, you have answered the wrong question.
```

#### A2 — incidental behavior

```
Everything being changed or deleted here does something BEYOND its stated job.
Find it.

- What does this code do incidentally? Timing, ordering, caching, a retry that
  is also acting as a debounce, a log line something greps for, a lock held
  slightly longer than needed that another path relies on.
- For anything being DELETED: what was it doing that nobody documented? Name
  the invariant it enforced and then search for where that invariant would be
  re-established afterwards. If you can't find one, say so — that is the
  finding.
- What would still pass every existing test and still be broken?

Quote file:line. Speculation is fine if labelled, but label it.
```

#### A3 — shared state and ordering contracts

```
Map what else touches the same state as {files/symbols in play}.

- Every other reader and writer of the same state, queue, cache, file, or
  external resource.
- Ordering contracts: what breaks if this runs earlier, later, twice, not at
  all, or concurrently with its neighbours? Walk realistic sequences, not just
  the happy path.
- Where does this code's correctness depend on something else having already
  run? Is that dependency enforced, or is it just true today?

Cite file:line for each coupling. Rank by how silent the failure would be.
```

### Team B — ADVERSARIAL CODE READ

**1 agent; 2 when the change spans modules.** Neutral brief rule applies — this agent especially must not be told the intended approach.

#### B1 — attack the existing design

```
Read {files in play}. Your job is not to review a proposed change — it is to
find what is wrong with the code as it stands today.

- What undocumented invariant does this code depend on? What would a new
  contributor break within a week because nothing states it?
- Where does the naming lie? Functions whose names describe less (or more)
  than they do, types whose names describe a role they no longer play.
- What do you have to know that isn't in this file to change it safely?
- What is here only because of how it was built, rather than what it needs
  to do?

Then the question this brief exists for:

  **If you were writing this from scratch today, knowing what it must do,
  what shape would it be?** Describe that shape concretely. Do not soften it
  toward the current design and do not weigh how much work the difference
  would be — that is explicitly not your input. If the honest answer is
  "roughly what's there," say that plainly; a clean bill of health from this
  brief is a real result.
```

### Team C — IMPLEMENTATION SPECIALISTS

**2 agents + conditionals.** Unlike A and B, these agents MAY be told the intended approach — their job is to check it against how the thing is actually supposed to be used.

#### C1 — project-local API documentation and canonical samples

```
For the dependencies this plan touches, surface the framework's official
guidance and working code patterns. Find where this project's docs live —
vendored docs folders, build-output docs, library README and examples under
node_modules / ~/.cargo/registry/src/ / site-packages / Pods, framework
headers, generated .d.ts files — and fetch from the vendor's official docs
site when no local copy exists.

- What does the framework's canonical pattern for this problem look like?
- Where are working examples, in this project's dependencies or in vendor
  sample repos?
- What footguns does the documentation itself call out?

Cite file:line for local sources, URL+section for fetched docs.
```

#### C2 — web prior art and community evidence

```
How are others in this language / framework / domain solving this problem?
Stack Overflow threads, GitHub issues on the relevant libraries, recent blog
posts, conference talks.

Vendor docs are routinely incomplete, or describe an intended contract that
doesn't match shipped reality — independent corroboration is the entire point
of this agent. Bring back canonical patterns, recent gotchas, and links.

Gotchas and performance cliffs are usually the UNDOCUMENTED part, which is
exactly why a doc quote alone cannot close every question.

Cite a URL for every finding. "I found nothing credible" is a valid and
useful answer; padding is not.
```

### Conditional specialists

These attach to Team C on their own triggers. Check all three against every plan. Each carries a trigger definition AND a forced-binary-decision contract — they must be read together, which is why the trigger text travels with the brief.

**For algorithmic plans specifically** (signals: plan cites a paper, GDC talk, engine docs, or third-party library's primitive), one of the additional agents MUST be the implementation-details specialist briefed explicitly:
> "The math is someone else's job. Your job is the loop structure and the parameters that aren't on the equation page — iteration counts, warm-start handling, regularization parameters, accumulator resets, internal stabilization passes, default thresholds. Read the engine source. What does the per-tick / per-step inner loop ACTUALLY do, beyond the formula? What load-bearing details exist that aren't in the API documentation?"
This separation prevents iteration count from being buried under formula overview.

**For plans involving algorithms, numerical methods, physics, control loops, constraint solvers, integrators, or any code where correctness depends on more than the formula** (signals: the plan cites a paper, a GDC talk, an engine docs page, or a third-party library's primitive), the brief MUST also require the agent to answer these four questions:

1. **"What does the canonical implementation do INSIDE the per-tick / per-step inner loop, beyond the formula on the page?"** Iteration counts, regularization parameters, stabilization terms, accumulator resets, warm-start clamps, convergence tolerance — the things that aren't in the math but are load-bearing for correctness.
2. **"What fails if we ship just the formula without the surrounding solver structure?"** Specifically: under sustained external load (gravity, friction, persistent input, accumulated error), does the system drift? Diverge? Oscillate? Quote the failure mode in concrete terms (e.g. "body drifts 3 px/tick downward forever").
3. **"What's the minimum executable test that would catch a naive implementation?"** Describe the exact scenario — initial state, applied forces, time horizon, expected vs. failing behavior. This becomes the first thing to validate in phase 1.
4. **"What load-bearing details exist in the engine source that are absent from the API documentation?"** Default iteration counts, hardcoded thresholds, internal stabilization passes, etc. These are the gotchas that paper-style references won't surface.

These four questions exist because of a real failure: a constraint-solver rewrite shipped phases 1-2 with a one-iteration solver, and the bug (body drifts under gravity) only surfaced in phase 3 when external forces were added. The research had named the formula correctly but treated iteration count as a minor implementation detail. Don't let that recur — *flag* the inner-loop specifics, don't bury them.

**For plans that add a new SOURCE file mirroring an existing SOURCE file** (signals: the request is to add a NEW file whose description uses words like "mirror", "like X", "similar to", "same look-and-feel as", "same family as", "matches the X style"; OR the new file's name has an obvious sibling already in the same directory — e.g. `news_window.py` next to `chat_window.py`, `foo_backend.py` next to `bar_backend.py`), one of the agents MUST be the **reuse / refactor specialist** briefed explicitly:

**Scope — code only. Do NOT dispatch this specialist for markdown, docs, skill definitions, prompt templates, or config.** Prose that reads alike often *should* read alike: parallel structure across two briefs is a feature, not duplication to factor out. And the thresholds that make this specialist useful for code — blocks ≥30 lines, ≥3 near-verbatim methods — mean nothing for instructions, where the same 30 lines may be the whole document. Extraction is frequently impossible anyway: skill directories are linked as units, so a shared file outside one is unreachable at runtime.
> "The plan describes a NEW file as mirroring an EXISTING file. Read both (and any other obvious siblings in the same directory). List concrete duplication: blocks ≥30 lines, methods ≥3 that would be copied verbatim or near-verbatim, shared widget chrome, shared style/setter surface. For each duplicated block, cite file:line. Then propose ONE of two paths and recommend which:
> (a) **Phase 0 refactor** — extract a shared base class / helper module / mixin FIRST, land that as its own commit, then build the new file on top in phase 1+.
> (b) **Copy and defer** — write the duplicate now, file the dedupe as a follow-up.
> Default to recommending (a) unless the existing file is unstable, about to be rewritten, or the duplication is <30 lines of trivial boilerplate. Your recommendation gets surfaced as a forced binary decision the user must make at plan approval — don't soften it."

This separation prevents the failure mode that motivated this rule: a plan describes a new window as "mirroring" an existing one, the layout agent confirms "yes the styling matches," code gets written as a parallel implementation, and the base-class extraction lands as a parking-lot follow-up *after* the duplication ships and after a review pass surfaces it. The right move is refactor-then-extend, not extend-then-refactor.

**For plans whose Non-goals will exclude some members of a peer set** (signals: the user's framing names a subset like "not touching Class B", "logSet only, not the other ops", "endpoint A but not B which feeds it"; OR the in-scope items and excluded items share a queue, cache, FIFO contract, applied-token set, ordering relationship, or other coupling), one of the agents MUST be the **exclusion-safety specialist** briefed explicitly:
> "The plan will exclude [the excluded items] from a change being applied to [the included items]. Read both groups. List every dependency between them — shared state, shared queues, shared caches, ordering / FIFO contracts, applied-token sets. Walk through whether the asymmetric mix opens a race, ordering, or stale-state hazard under realistic call sequences (rapid-fire taps, dial-then-tap, A-before-B-but-B-arrives-first, etc.). Cite file:line. Recommend ONE of two paths:
> (a) **Fold excluded into scope** — apply the change uniformly across the peer set
> (b) **Keep exclusion + document explicit invariant** — the invariant must make the asymmetry provably safe, not just plausible
> Default to recommending (a) unless the operator has explicitly rejected it OR the invariant is iron-clad and short enough to fit in one sentence. Your recommendation gets surfaced as a forced binary decision the user must make at plan approval — don't soften it."

This separation prevents the failure mode that motivated this rule: a plan applies a new transport / mechanism to some ops in a peer set but excludes others as a "non-goal," and the excluded ops' slower delivery races the included ops' faster delivery, committing dependent state in the wrong order.

---

## Plan audit briefs

Briefs 1-4 are loaded by Mode 1 step 7 (the adversarial read-back of a written plan); brief 5 is loaded by the Phase Completion Cycle's step 1c, at a phase gate, and audits a DIFF rather than a plan; brief 6 is loaded by Mode 1 step 4, BEFORE the plan is drafted, and settles a design fork by experiment rather than letting it become a phase. **Pass these verbatim.** Paraphrasing them into one generic "review this" is the degradation their specificity exists to prevent — the axes are disjoint and none can answer another's question.

Every agent is `subagent_type: "general-purpose"`. **Never `Explore` here** — the grounding auditor's entire job is checking claims against sources under the verify-or-block rule, and Explore omits CLAUDE.md, so an Explore grounding auditor audits without the rule it is auditing against.

**Each brief opens with `Plan audit: <slug>.` on its own line, and that line is load-bearing.** Where the full /plan skill's dispatch gate is registered, it keys on that line to apply the `Explore`/`Plan` ban to audit dispatches; without it the gate cannot see this step at all. It is a *separate* marker from step 4's `Plan slug:` on purpose — an audit dispatch is held to the agent-type rule and nothing else, because the three research invariants are research-shaped (an auditor recommends nothing, and the dry-run prober is not read-only).

### 1. Grounding auditor

Brief: "Plan audit: <slug>.
Read `plans/<slug>.md` and every `plans/<slug>-<phase>.md`. Extract every EXISTENCE or BEHAVIOR claim about the codebase or an external system — file paths, symbol names, signatures, API names, metadata keys, version numbers, quoted behavior, `file:line` citations — and check each against the actual source this session. (Goals and non-goals state intent, not fact — skip them.) **Interface lines carry one more exemption, on exactly the same footing.** A symbol named on a `- Produces:` line — this shard's or an earlier phase's — is a DECLARED FUTURE symbol: the plan is saying it does not exist yet, so its absence from the code is not a finding and you do not check it; what grounds a `Produces:` line is the work-shape sketch beside it, not the codebase. A `- Consumes:` line is the opposite claim — that the symbol exists NOW — so check every one against current source. A `Consumes:` symbol you cannot find in current code AND cannot match to a `- Produces:` line of an earlier task in the same phase or an earlier phase IS a finding: it is a call into something nothing in this plan builds, and it reads as grounded precisely because the line beside it looks the same. **Search as though at least one claim does not resolve — reporting zero unresolved is a valid result, but only alongside the evidence below.** Report a table: claim · where in the plan · resolves? (yes / no / partially — and what the source actually says). Quote the CURRENT source text verbatim at three or more cited locations, so the report proves you opened the files rather than echoing the plan back. Separately list (i) every claim you could NOT check and why — external system you cannot reach, doc not present locally; those are unverified, not verified, and they are findings, not footnotes. **A claim whose source you did not actually open goes here regardless of how plausible it reads — plausibility is not a resolution, and quoting the plan's own citation back at me is not opening anything;** and (ii) every existence-or-behavior claim carrying no citation at all. Also flag any `TODO: verify` / 'confirm during implementation' / hedged phrasing ('should be', 'presumably', 'I believe'), any work-shape sketch using a symbol you cannot find — EXCEPT one the plan declares on a `- Produces:` line, which the exemption above covers and which is exactly where a declared-future symbol appears, so do not re-flag it here — and — in the Failure modes section specifically — TWO things. First, any entry whose *antecedent* is statically checkable ('if this function branches on that flag…', 'if that endpoint doesn't return X…'), which is an unlooked-up fact wearing risk's clothing rather than a real risk. Second, and do not skip this because the sentence is phrased as a warning: **any flat ASSERTION about how a framework or external API behaves — 'passing X causes Y', 'this modifier re-bins/discards/overwrites Z' — is a behavior claim and gets the same doc-quote-or-probe treatment as a claim anywhere else in the plan.** A failure-mode bullet reads as hypothetical and therefore slips past unchallenged, but the plan's Work is often scoped AROUND it, so a wrong one steers the whole phase. If the project's own docs or the repo's own working usage does not confirm it, report it — being wrong in the opposite direction to the truth is the case that costs most. You are NOT to invoke the `/plan` skill and NOT to edit any file. End with counts — e.g. 'checked 14 claims, 12 resolve, 2 do not, 1 uncheckable, 1 uncited.' Keep prose under 400 words; the claims table and source quotes do not count toward that."

### 2. Executability auditor

Brief: "Plan audit: <slug>.
Read the master `plans/<slug>.md` and every shard. (If the plan is a single file with no shards, read it alone and answer (a), (e), (f), (g), (h) only — a single-phase plan still carries interface lines and a task tier.) **Search as though at least one done criterion is covered by no phase and at least one shard cannot be executed standalone — reporting zero is valid only with the per-item accounting below.** Answer each as a list, not prose: (a) COVERAGE — for every master-level and per-shard Done criterion, name which shard's Work satisfies it. Name any criterion nothing covers, and any Work item no criterion justifies. (b) SET MISMATCH — files in the master's `## Files touched (overview)` appearing in no shard's Work, and files in a shard's Work missing from that overview. (c) ORDERING — does any phase's enter-gate depend on an output a LATER phase produces? Does any shard reference a decision or artifact from a phase that runs after it? (d) SELF-SUFFICIENCY — for each shard, using ONLY that shard's own text, list every referent it needs in order to EXECUTE but neither defines nor names a source for: inputs, outputs, call sites, delegated behavior, symbols. A pointer that names where the thing lives ('see the master', 'settled in phase 2') is not a gap — an unsourced referent is. Name the items. (e) EXCLUSIONS — does every Non-goal excluding some members of a peer set carry its one-sentence rationale for why the asymmetry is safe? (f) INHERITED DECISIONS — does the plan record as already-settled (in `## Locked decisions`, or anywhere it treats a choice as made) any decision that changes what the user sees or how the feature behaves — a default, a shown/hidden value, copy, an observable state — WITHOUT citing an explicit operator sign-off? Name each. (g) INTERFACES — the shards' contracts read against each other, in both directions. Every phase's `## Work` carries `- Consumes:` and `- Produces:` dash bullets (per task where the phase has a `### Task` tier); a literal `none` is a valid value and pairs with nothing. FORWARD: for every `- Consumes:` entry, find the `- Produces:` entry it pairs with — an EARLIER task of the same phase, or an EARLIER phase's shard. Quote both lines when it matches. An entry matching neither is UNPAIRED: report it as such and stop there — **do NOT open source to decide whether it exists in current code.** That is the grounding auditor's axis, it is checking exactly this line under its own carve-out, and two agents reporting one symbol is how a duplicate finding gets counted as two. BACKWARD: for every `- Produces:` entry, name each LATER shard whose `- Consumes:` claims that product and quote the two lines side by side. That repetition is deliberate — a worker's whole world is one pasted shard — and this pairing is the only thing checking it, so a drifted copy (renamed type, changed argument, dropped return, different arity) is a finding and you report it by quoting both, never by deciding which one is right. (h) TASK DISJOINTNESS — for every phase whose `## Work` carries more than one `### Task`, the file set under each task must be disjoint from every sibling task's. Name every file appearing under two or more tasks of the same phase, and name the tasks. Report the number of tasks you checked per tiered phase. You are NOT to invoke the `/plan` skill and NOT to edit any file. End with counts — e.g. 'checked 6 done criteria across 4 shards, 9 interface entries, 7 tasks in 3 tiered phases.' Report in under 400 words."

### 3. Coherence auditor

Brief: "Plan audit: <slug>.
Read the master `plans/<slug>.md` and every shard. **You are NOT checking the plan against the codebase — another auditor does that, and you should not open a source file at all.** You are checking the plan against ITSELF. Your question is: which two parts of this document set cannot both be true? **Search as though the plan contradicts itself at least once — reporting zero is valid only with the accounting below.** Report as a list, each entry naming BOTH locations and quoting both: (a) SUMMARY VS MECHANISM — a `## Locked decisions` entry, a Status sign-off item, a Goal line, or any prose statement of a rule, whose scope is wider or narrower than the behaviour the Work section's own steps or work-shape sketch actually produce. Walk each stated rule against the sketch step by step; a rule that says 'X always resolves to Y' against a sketch whose branch order reaches Y only sometimes is the archetype. (b) UNREACHABLE OUTCOME — a Done criterion or Goal the Work as written cannot produce. (c) SELF-VIOLATING SCOPE — a Work item that does the thing a Non-goal excludes. (d) SPLIT FACT — the same fact stated in two places with different content (master vs shard, or shard vs shard), including line hints and counts. (e) UNVERIFIED CHARACTERIZATION — any sentence describing what the code or product DOES, as opposed to where something lives: 'the X card shows Y', 'this only fires when Z', 'users see this on the W screen'. Flag EVERY one, **even when its citation resolves perfectly** — a correct `file:line` proves a symbol exists, never that a description of behaviour is accurate, and that gap is why this item exists. You are NOT to invoke the `/plan` skill and NOT to edit any file. End with counts — e.g. 'checked 11 stated rules against their mechanisms, 4 characterizations, 6 cross-file restatements.' Report in under 400 words; quoted pairs do not count toward that."

### 4. Dry-run prober

Dispatched only when the plan modifies code that already exists. **NOT read-only and NOT an auditor** — it is the only agent in step 7 that executes rather than reads. Dispatch with `isolation: "worktree"`. Trigger, target selection and the paste rule live in Mode 1 step 7; the brief is here. Its did-not-survive-contact taxonomy is three channels — SKETCH / APPROACH / MEASURED — and the response routing for each, MEASURED's included, sits beside the target-selection rules in Mode 1 step 7; a MEASURED mismatch is the plan's own claim falsified, which is why it gets its own label instead of riding SKETCH or APPROACH.

Brief: "Plan audit: <slug>.
You are in a throwaway git worktree. Below is one phase of a plan; START IMPLEMENTING it here. **You are not delivering the phase and your diff will be discarded** — implement only as far as you need to discharge THIS BRIEF'S duties, then stop. The first duty is always: which files did you have to OPEN AND EDIT that the Work section does not name? The second exists only when the phase's Done criteria name an observable output, and the third only when the phase ships a mechanism at a size a later phase runs bigger; both are stated with the stop condition below — reaching a file-list answer does NOT discharge either. Go far enough to hit the real call sites — change the signature, follow the compiler, run a build if the project has one, from this worktree's own path (re-point any build tooling whose session defaults target the main checkout). **A green build is the floor, not a preference — reaching one is what gives your file list its authority, because following the compiler is how the real call sites surface at all.** If it does not compile, fix it and build again, and keep going until it does. Only once you are green does the second stop apply: stop at the first green build, or past it at the point where the remaining work is more of the same (in a project with no build system, that second point IS the stop). **Never hand back a file list from a tree that never compiled as though it were complete** — if you stopped short of green, say so in the first line of your report and mark the list provisional. If you genuinely cannot reach green after real attempts, that IS a finding rather than a failure to report one: label it APPROACH (or SKETCH, when the plan's own sketch is what will not compile), quote the actual compiler error, and say what you tried. A quiet stop short of green is the single outcome this brief does not accept. **TWO things suspend the stop, and each is checked independently — a phase can trigger the second without the first.** (1) The phase's Done criteria name an observable output (a rendered surface, a numeric result, a formatted artifact): then neither stop applies until you have also produced that observable and measured it against the criterion, reporting any mismatch as MEASURED. (2) The phase ships a mechanism at a size a LATER phase runs bigger — one page, one item, one worker, one connection: then neither stop applies until you have discharged item (vii) below by actually RUNNING it at the bigger size. Item (vii) is a THIRD duty alongside the file list and the observable, not a question to answer from reading; answering it without running it is the one way to fail this brief while looking complete. If a named observable needs an external runtime this worktree cannot re-point safely (a booted simulator, a live service, hardware), report OBSERVABLE-UNAVAILABLE with the reason instead of silently stopping at green. Report: (i) **anything in the phase text that did not survive contact — quote it and say what you hit.** Three kinds, and label which: a SKETCH defect (a work-shape sketch that does not compile as written, a signature or conformance that is wrong or missing, two parts of the phase text prescribing different things), an APPROACH failure (the design itself does not work against the real code), or a MEASURED mismatch (an observable a Done criterion names, produced and measured, missing the criterion — quote the criterion, the measurement, and the delta). Answer explicitly: **did the work-shape sketch compile as written, or did you have to change it?** (ii) every file you edited, marked LISTED or MISSING against the Work section; (iii) for each MISSING file, the one-line reason it was unavoidable; (iv) anything in the Work section you did NOT need to touch; (v) **did you work around any constraint to reach green?** If you hit a restriction and routed around it — a wrapper, a holder, a shim, a lazy indirection, anything whose job is to dodge rather than to do — name the constraint, name the workaround, and say what the design would look like WITHOUT it, including which files THAT version would touch. Answer this even when the workaround was reasonable and even when it built cleanly. A green build is not evidence you found the right shape, only that you found a working one, and a workaround is the tell that a different design has a different — usually larger — footprint. Your file list describes the design you happened to build, and this is the only place the alternative gets recorded. (vi) **name three behaviors the OLD code provided that yours does not, and where each is re-established.** Not three defects — three behaviors, whether or not you think they are covered; saying 'this one is re-established at X' is the useful half. You just made the change and you still have the tree, which makes you the cheapest place in the whole process to ask, and the only one that can answer before the code exists. Look hardest at what a deleted line did BEYOND its stated job: what it kept alive, what ordering it guaranteed, what it happened to run first. A handler you removed because it 'can no longer fire' was also doing something synchronously that something else now does later, or not at all. If you genuinely cannot name three, say so explicitly and name the ones you can — an honest two beats a padded three. (vii) **if the phase ships a mechanism at a size a LATER phase runs bigger — one page, one item, one worker, one connection — run it bigger in scratch and report what changes.** Do this even when the phase text forbids the bigger size, and especially then: the phase is shipping code whose failure mode is invisible at the size it ships. Revert the scratch widening before you report, and say what the widened run did. A mechanism that cannot fail at the size it ships has no gate anywhere in this process, and you are the last agent positioned to give it one. (viii) **list every design decision you made that the phase text did not make for you** — every point where two reasonable implementations would BOTH satisfy the text as written and you picked one: an ordering, a default, a state-to-rendering mapping, which component owns a value, what happens on the path the text does not mention. For each, one line on what the other choice would have been and which files it would touch. Do not filter to the ones that felt important — a choice you made without noticing is exactly the kind that ships wrong, and the un-felt ones are the point of the question. Do NOT commit and do NOT report on code quality. The phase text follows: {shard_text}"

### 5. Spec reviewer

Dispatched by the Phase Completion Cycle's **step 1c**, not by step 7, and only when the phase's `## Work` carries MORE THAN ONE `### Task` (exactly one task → the dispatching session runs the pass itself). Read-only; no `isolation`. It compares a diff against a spec, which is mechanical work — **a cheaper or faster model is a legitimate choice here**, and it is the fix for the documented failure mode of spec-compliance reviewers being slow and over-broad.

**Every input is PASTED into the prompt: the shard's text, the worker's report, and the exact diff.** Never a file path and never "read the plan" — the plan files may be untracked, the diff is already in the dispatching session's hands, and an unscoped reviewer told to go find the change searches a whole codebase for half an hour to reconstruct what a paste would have handed it.

Brief: "Plan audit: <slug>.
You are checking ONE thing: does the diff below implement the phase's Work section, and only it? Everything you need is pasted into this prompt — the phase text, the worker's report, and the complete diff. **Do NOT open, read, search, or list any file, and do not run any command.** If something is not in this prompt, it is out of scope for you; say so rather than going to look for it. Answer on exactly two axes, each as a list, not prose. **(1) WORK COVERAGE, in both directions.** FORWARD: for every item in the phase's `## Work` — every file bullet under every `### Task` — name the diff hunk that evidences it, quoting a line or two of the diff. An item you cannot evidence is UNBUILT; name it. Where the phase has more than one task, attribute each changed file to the ONE task whose file set contains it — the task file sets are disjoint by construction, so a file you cannot attribute to exactly one task is itself a finding (name it as UNATTRIBUTED, and say whether it is claimed by zero tasks or by two). BACKWARD: for every file the diff changes, name the Work item that claims it. **A changed file no Work item names is UNCLAIMED — report every one, this is the direction that catches the edit nobody declared,** and do not excuse it because the change looks small, tidy, or obviously correct. **(2) INTERFACE CONFORMANCE.** For each task's `- Consumes:` and `- Produces:` lines, compare the signatures and types named there against what the diff actually ships. Report each as CONFORMS or MISMATCH, and for a MISMATCH quote the approved line and the shipped signature side by side. A `none` line means the task claims to create/call nothing new — a diff that ships a new public symbol against a `Produces: none` is a MISMATCH. **You do NOT report on any of the following, and a report containing them is wrong regardless of how good the observation is:** code quality, style, naming, performance, test coverage, bugs, or anything you would raise in a code review — a separate gate owns all of that; and whether the phase's Done criteria are met — a different pass owns those, and two records claiming that ground is how they come to disagree. Do not edit anything and do not invoke the `/plan` skill. **End with counts, in this exact line, and nothing else on the line:** `**Spec check at <phase id>** — tasks N/N evidenced · interfaces conform|N mismatch · none uncovered|N uncovered`. (Single-phase plan: `**Spec check** — …`, with no phase id.) A pass that cannot name what it checked did not run. Report in under 400 words; quoted diff and interface pairs do not count toward that. The phase text follows, then the worker's report, then the diff: {shard_text} / {worker_report} / {diff}"

### 6. Comparison prober

Dispatched by **Mode 1 step 4**, before the phase map is drafted — not at step 7 with the auditors, and not as a phase. Trigger: EXPLORE surfaced a fork between two or more candidate designs, and a different outcome would change the phase map. **NOT read-only.** Dispatch with `isolation: "worktree"`, at the session's effort.

It exists because "this needs a running app" describes what closes a question, not when. A fork resolved during execution means every phase after it was drafted against an assumed answer — and, worse, that the design did not exist when the change-impact research ran, so nothing could analyse what the choice implies.

**Paste the candidates and the discriminating question into the prompt.** Never a path to a plan file: the worktree branches from the default branch and the plan may be untracked there.

Brief: "Plan audit: <slug>.
You are in a throwaway git worktree. Your diff will be discarded and you are not delivering a feature. **You are running an EXPERIMENT to settle one question, and your report is a verdict with evidence.** The question and the candidate designs are pasted below.

Build the MINIMUM of each candidate that can answer the question — the smallest thing that exercises the discriminating property, not a finished implementation. **Hold constant everything the fork does not force to differ.** Structural forks propagate by construction, so 'identical apart from one line' is usually impossible and is not the standard: the standard is that every difference is one the candidate REQUIRES. Where you had a free choice, make it the same on both sides; where the candidate forced your hand, say so under (iii). A difference you did not have to make is how an experiment returns the wrong answer confidently.

🛑 **Build and run from THIS WORKTREE'S own path. Re-point any build tooling whose session defaults target the main checkout, and verify it took** — grep the build log for this tree's path before trusting a single number. Tooling pointed at the main checkout measures the UNMODIFIED tree for both candidates, which yields two indistinguishable results or one arbitrary winner, and every later question in this brief will read as discharged: you did hold everything constant, including the candidate. A verdict from the wrong tree is worse than no verdict, because the plan gets locked to it.

Then MEASURE. Run it, drive it, record numbers or observed states — do not reason from documentation about what should happen, because if docs settled this you would not have been dispatched. Where the candidates' docs disagree with what you measure, report both and trust the measurement.

🛑 **A VERDICT REQUIRES BOTH CANDIDATES BUILT AND MEASURED. That is the floor, not a preference.** One side measured and the other abandoned is not a comparison, and reporting it as a verdict with the difficulty logged as a caveat is the single outcome this brief does not accept — the plan is drafted on your answer either way, and nothing downstream re-opens it. If you cannot stand a candidate up after real attempts, that IS the finding: report **UNRESOLVED**, say which candidate and what stopped you, and give whatever partial measurement you have. Note also how many trials each side got: one trial is one trial, and a verdict resting on it says so in the verdict line.

Report: (i) the VERDICT — which candidate, and the single property that decided it; (ii) the MEASUREMENT behind it, as raw as you can give it: what you ran, how many trials, what each returned. A verdict from one trial is a verdict from one trial — say so; (iii) what you held constant, and anything you could not; (iv) **what surprised you about EITHER candidate** — behaviour neither the docs nor the question anticipated, whether or not it bears on the verdict, because the plan is about to be written against the winner and this is the only moment anyone looks at the loser; (v) **the SHAPE the winner forces** — the composition, structure, types or call pattern the plan must now be written against, quoted as concretely as you can, ideally as the actual code you built. **This is the highest-value line in your report and it is not a summary.** The change-impact research runs NEXT and is briefed from it; a vague shape means that research analyses a design nobody has written down, which is the exact failure your dispatch exists to prevent; (vi) anything that would make you refuse to call it: a candidate you could not build, a property you could not reach, an environment the worktree cannot provide. **A verdict you cannot support is worse than no verdict** — the plan will be drafted on it either way. Say UNRESOLVED and why.

**You are NOT to invoke the `/plan` skill** — you are settling one question for a plan someone else is drafting, and a prober that starts planning is the failure this line exists to stop. Do NOT commit. Do NOT report on code quality, and do not extend either candidate beyond what the question needs. The question and candidates follow: {question_and_candidates}"

⚠️ **Brief 6 is a step-4 dispatch that deliberately does NOT carry step 4's three research invariants** — the citation requirement, the effort-objection ban, and the 400-word cap. It recommends nothing, so the effort-objection ban is meaningless to it; it measures rather than cites; and a word cap fights item (ii)'s demand for raw measurement. It carries the `/plan`-invocation ban, which is the one that applies. Do not paste the research boilerplate onto it: a registered dispatch gate reads its `Plan audit:` opener as an audit and skips the invariant table, so nothing will catch the mistake.

---

## Plan templates

Loaded by Mode 1 step 6 (writing the files) and referenced by Mode 2 when reshaping a legacy-shape plan. The section names below are load-bearing: the Scope Check, the Phase Completion Cycle, and the Rules above all key on them — do not rename a section here without updating every one of those references.

### Single-phase plan — the ONLY non-sharded shape

One file, `plans/<slug>.md`:

```markdown
# <feature name>

## Status  *(approval marker + mid-work cold-start; refresh whenever work pauses)*
**Approval: DRAFT**  *(flip to `APPROVED <date>` at approval — code never starts while this reads DRAFT)*
**Authored at: <commit id>**  *(HEAD when the plan was written — Mode 2's staleness check diffs from this commit; update it on every revalidation/refresh)*
<VERIFICATION RECORD — written at Mode 1 step 7, before the plan is ever shown,
from the auditors' reported counts; refuted and uncheckable counted separately.
Mid-work Scope Check additions get noted here too — the record covers only the
text the auditors saw:
  Verification pass <date> — 9/9 claims resolve · 4/4 done criteria covered ·
  coherence clean across 7 stated rules and 3 characterizations · dry run 5
  files LISTED, 1 MISSING (added) · 1 fixed (Work cited `oldName` → corrected
  to `newName`) · 1 refuted (cited)>
<All four lines appear whenever all four agents ran. A missing dry-run line and
a dry run that found nothing must not read alike.>
<NAME every fix — "1 finding fixed" is unfalsifiable; "fixed WHAT, to WHAT" is
checkable against the file. With one pass and no re-dispatch, this naming is
the only thing that makes a bad fix visible.>
<One SPEC CHECK record when the phase ships (Phase Completion Cycle step 1c).
It is BOLD-LED and never contains the words "probe" or "dry run", and neither
constraint is stylistic: the Status region may be machine-read line by line,
bold and bullet lines are recognized shapes, an unrecognized line is glued onto
whatever record is open above it, and the probe/dry-run vocabulary OPENS a
record. Counts are mandatory — an auditor that cannot name what it checked did
not run the pass. A single-phase plan has no phase id, so it drops the
`at p<id>` suffix the master shape carries:
  **Spec check** — tasks 3/3 evidenced · interfaces conform · none uncovered>

<Once work starts: what's done, what's in flight, what's next — plus any
"verified this session" facts and line-hint staleness notes a resuming session
needs. A single-phase plan interrupted mid-work re-enters through this section.>

## Goal
<1-2 sentences. Concrete, not aspirational.>

## Diagnosis  *(required for perf/bug/regression plans; omit for greenfield)*
- **Hypothesis:** <the suspected cause, named concretely>
- **Falsifiable test:** <one-line experiment that CONFIRMS or DISPROVES before scoping Work>
- **Test result:** <run it; paste the observed output VERBATIM — actual numbers/ids/errors, not a summary; if disproved, STOP and return to Mode 1 step 4>

## Non-goals
- <explicit thing we're NOT doing — prevents scope creep>

## Work
<NO task heading for a single-job phase — bullets sit directly under `## Work`,
as below. ONLY when the phase splits into more than one job, precede each job's
bullets with `### Task N — <one-line scope>`: file sets disjoint (a file appears
under exactly one task), one worker consuming them in order, tasks are not commit
units. A lone `### Task 1` is noise — see the task-tier guidance below.>
- path/to/file.ext — <what changes here>
  <For any non-obvious item, add a 2-6 line work-shape sketch: signatures, data
  shapes, or pseudo-code — whichever shows the design. Cite the EXPLORE source
  that grounds any API the sketch uses.>
- Consumes: <exact signatures/types this task calls that EXIST already — signatures only; the EXPLORE citation that grounds them goes on the file bullet or its sketch, never on this line. Or `none`>
- Produces: <exact signatures/types this task creates that later tasks/phases consume; or `none`>

## Decisions & findings  *(record any choice a reviewer would plausibly ask "why this way?" about — chosen approach, rejected alternative, why; entry shape per the shard template below)*

## Failure modes to anticipate
- <thing that could break, unfamiliar territory, known gotcha>

## Done criteria
- <concrete exit condition>

## Parking lot
(empty)
```

### Multi-phase plan — master + shards, MANDATORY for >1 phase

A multi-phase plan is **NEVER one file.** It is a **master** (`plans/<slug>.md`) plus **one shard per phase** (`plans/<slug>-<phase>.md`). Writing a >1-phase plan as a single file — or letting per-phase research and decisions pile into one document — is the exact failure this shape exists to stop: a monolith no cold session can ingest selectively, where each phase's decisions are buried under every other phase's.

**Master** `plans/<slug>.md`:

```markdown
# <feature name>

## Phase map  *(every phase, one block — the ARC and the GATES, never the work detail)*
**Phase <id> — <one-line scope>**  *(tag gate/branch phases, e.g. (kill-switch gate))*
- Enters when: <gate/dependency to start; "start here" for the first phase>
- Done signal: <the single thing that ends the phase — points at the shard, does NOT restate it>
- If it fails: <where the plan stops or branches; "no gate — fix-forward" if none>
- Shard: `plans/<slug>-<phase>.md`

## Status & cold-start  *(which phase is NEXT)*
**Approval: DRAFT**  *(flip to `APPROVED <date>` at approval — code never starts while this reads DRAFT)*
**Authored at: <commit id>**  *(HEAD when the plan was written — Mode 2's staleness check diffs from this commit; update it on every revalidation/refresh)*
<VERIFICATION RECORD — written at Mode 1 step 7, before the plan is ever shown,
from the auditors' reported counts; refuted and uncheckable counted separately:
  Verification pass <date> — 14/14 claims resolve · 6/6 done criteria covered ·
  4/4 shards self-sufficient · coherence 1 finding (p1 locked decision stated a
  rule wider than its own work-shape sketch produced → scoped) · dry run 4
  files LISTED, 2 MISSING (added to p1 Work + overview) · 2 fixed (p2 Work
  cited a renamed symbol → corrected to the current name; p4 done criterion had
  no covering Work item → added) · 1 promoted to approval · 1 refuted (cited) ·
  1 uncheckable (vendor API, promoted)>
<NAME every fix — "2 findings fixed" is unfalsifiable; "fixed WHAT, to WHAT" is
checkable against the file. With one pass and no re-dispatch, this naming is
the only thing that makes a bad fix visible.>
<Which phases are SHIPPED (commit ids), which is NEXT. The NEXT phase's shard
IS the self-sufficient packet — name it with a LEADING "read
`plans/<slug>-<phase>.md` FIRST" instruction, not a trailing citation. Then pull
that phase's 2-3 binding decisions inline here, so a compaction that drops the
shard from context still leaves the decisions visible.>

<One SWEEP RECORD line per shipped phase (Phase Completion Cycle step 5c),
naming every unshipped shard and what happened to it — including the clean ones —
and closing with the `code:` segment for what the phase obsoleted in EARLIER
PHASES' shipped source. A shipped phase with no sweep record is a drift alarm,
not an omission; so is a record with no `code:` segment:
  Downstream sweep at p4 — p5 banner added · p6 banner added · p7 2 items
  corrected · p8 clean · p9 2 items moot · p10 clean · p11 1 item extended ·
  code: pinned the x domain, checked p1's coverage gate + p2's clamp, gate
  obsoleted and removed>

<One SPEC CHECK record per shipped phase (Phase Completion Cycle step 1c), in
this same Status region. It is BOLD-LED and never contains the words "probe" or
"dry run", and neither constraint is stylistic: the Status region may be machine-read
line by line, bold and bullet lines are recognized shapes, an unrecognized line
is glued onto whatever record is open above it, and the probe/dry-run vocabulary
OPENS a record. Counts are mandatory — an auditor that cannot name what it
checked did not run the pass. A shipped phase with no spec-check record is the
same resume-time alarm as a missing sweep record:
  **Spec check at p4** — tasks 3/3 evidenced · interfaces conform · none uncovered>

## Goal
<1-2 sentences. Concrete.>

## Diagnosis  *(required for perf/bug/regression plans; omit for greenfield)*
- **Hypothesis / Falsifiable test / Test result** — run the test before scoping any shard; record the observed output verbatim.

## Non-goals
- <explicit boundary; one-sentence safety rationale for any peer-set exclusion>

## Files touched (overview)
<The cross-phase conflict-spotting view: every file the plan creates/modifies,
tagged by phase. The per-file WORK detail lives in each shard — this is the map,
not the detail.>
- path/to/file.ext — <P1 | P1,P3> — <one-line note>

## Background findings  *(cross-phase research ONLY)*
<Consolidated EXPLORE findings that span phases and belong to no single one.
Per-phase findings live in that phase's shard, NOT here.>

## Done criteria  *(plan-level — the whole feature's exit, NOT a copy of per-phase criteria)*
<The cross-cutting exit conditions that mark the PLAN complete: outcomes that span
phases or aren't owned by any single one — whole-feature suite green, docs/skill
updated, deployed/pushed, the end-to-end user-facing result. Each phase's own
commit-level exit lives in that phase's shard's Done criteria; do NOT restate those
here. The plan is done when every shard's Done criteria AND these are met.>
- <plan-level exit condition>

## Parking lot
(empty)
```

**Shard** `plans/<slug>-<phase>.md` — one per phase, self-contained:

```markdown
# <slug>-<phase> — <one-line scope>

You are phase `<phase>` of the `<slug>` plan. <1-2 sentences: what this phase delivers as one commit.>

## Locked decisions (do NOT re-litigate)
See the master `plans/<slug>.md`. The decisions binding this phase:
- <decision settled at plan time>
<A user-facing decision (show/hide, default, copy, observable behavior) belongs
here ONLY if it cites an explicit operator sign-off — a "chosen: X" from a
divergent-design pass or an approval in Status. Without that it is an open
approval item surfaced at plan approval, NOT a locked decision (Rules).>

## Work
<NO task heading for a single-job phase — bullets sit directly under `## Work`,
as below. ONLY when the phase splits into more than one job, precede each job's
bullets with `### Task N — <one-line scope>`: file sets disjoint (a file appears
under exactly one task), one worker consuming them in order, tasks are not commit
units. A lone `### Task 1` is noise — see the task-tier guidance below.>
- path/to/file.ext — <what changes here, this phase>
  <For any non-obvious item, add a 2-6 line work-shape sketch: signatures, data
  shapes, or pseudo-code — whichever shows the design. Cite the EXPLORE source
  that grounds any API the sketch uses.>
- Consumes: <exact signatures/types this task calls that EXIST already — signatures only; the EXPLORE citation that grounds them goes on the file bullet or its sketch, never on this line. Or `none`>
- Produces: <exact signatures/types this task creates that later tasks/phases consume; or `none`>

## Decisions & findings
<The durable record for this phase. One entry per NON-LOCAL decision — a decision
whose consequences are scattered across the code rather than contained in one spot.
Local one-liners do NOT earn an entry; they stay in the master's Non-goals.>
### Decision: <short title>  *(status: active | superseded by phase-<id>)*
- **Rationale:** <why this choice>
- **Alternatives considered:** <what else was weighed, why rejected>
- **Evidence:** <file:line / URL+section that grounds it>

<Append empirical findings here AS THE PHASE RUNS — spike results, gotchas found
mid-implementation. Writing them here is what stops the next phase or the next
session from rediscovering them after a clear.>

## Failure modes to anticipate
- <runtime/integration risk>

## Done criteria
- <concrete exit condition>
```

### Filling in the template from conversation context

- **Phase map**: MANDATORY for every multi-phase plan; a multi-phase master without one is incomplete and MUST NOT be presented for approval. It carries the **edges** between phases — enter-gate, done-signal, branch-on-failure, and shard path — one block per phase. It is NOT a place to detail the work; the work lives in each shard's `## Work`. A Phase map that restates shard Work detail is the duplication this section exists to prevent — keep it coarse. Tag every phase that gates or branches (e.g. `(kill-switch gate)`) so the risky phases announce themselves on a skim.
- **Status / Status & cold-start**: Both shapes carry one — the single-phase `## Status` holds the Approval marker plus mid-work progress (it's the cold-start anchor when a single-phase plan pauses mid-work); the master's `## Status & cold-start` adds the phase bookkeeping below. Required for any plan with more than one phase. There is no "designated multi-session plan" — whether a session gets cleared mid-plan is the operator's call, made when context gets hot, *after* a phase commit, never at plan approval. A rule gated on "will this span sessions?" is a rule a session can self-certify out of right up until the clear happens — so every multi-phase plan is treated as multi-session by default. With sharding, **the NEXT phase's shard IS the self-sufficient packet** — the master's Status section names which phase is NEXT, leads with a "read `plans/<slug>-<phase>.md` FIRST" instruction, and pulls that phase's 2-3 binding decisions inline so a compaction dropping the shard still leaves them visible.

  **Every plan is drafted as if a DIFFERENT session executes it — unconditional, both shapes.** Not "if this might span sessions", not "for multi-phase plans": the assumption is standing, because the party who will execute a phase is a fresh worker with none of the drafting conversation in context, and that is true of a single-phase plan on the day it is written. So apply the same test at every refresh, to the shard in a multi-phase plan and to `## Status` + `## Work` in a single-phase one: **"could a fresh session execute this from this text alone — every input, output, call site, and delegated behavior?"** Anything settled in conversation is written into the plan or it does not exist. Summarizing lossily is the bug this prevents — restate the inventory, don't compress it by vibes.
- **Goal**: What has the user stated as the objective? Don't editorialize or expand scope.
- **Diagnosis**: Required when the plan exists to *change* something already running — performance regressions, bug fixes, "make X faster/smaller/cheaper", "stop Y from happening", "investigate Z". Skip for greenfield features (new code where there's no existing behavior to diagnose). The hypothesis names the suspected cause concretely (a function, a flag, a code path), not vaguely ("something is slow"). The falsifiable test is a one-line experiment runnable in seconds — comment out a call, set an env var, add a log. **Run it before scoping any shard's Work.** If the test disproves the hypothesis, the rest of the plan is built on sand — return to Mode 1 step 4 (Explore) with the negative result as a sharper question, don't ship the wrong fix. The cost of running a 30-second diagnostic test is far less than the cost of implementing, reviewing, testing, and committing a plan against the wrong target. Record the observed output verbatim in the Test result line — the raw numbers/ids/errors are what make the diagnosis trustworthy at review and re-checkable after a context clear.
- **Non-goals**: Things the user has explicitly said to NOT do, OR things that are natural adjacent work that we're deliberately deferring. This is the most important section for ADHD — aggressive non-goals prevent drift. **But: when a non-goal excludes some members of a peer set (some op types, some endpoints, some entities, some files in a related family), include a one-sentence justification of why the exclusion is safe.** If you can't write that justification, the exclusion is probably the bug — promote the excluded items into scope or restructure the plan to avoid the asymmetry. Asymmetric changes across peers in the same dependency graph open race, ordering, or stale-state hazards that don't appear in unit tests but bite in production. Default to *including*; require concrete justification to exclude.
- **Work / Files touched**: Always concrete, full stop. Naming every file is Team A's job (change impact) plus Team B's read of the existing code — keep exploring until you can list them; never list "every file that might be relevant" and never leave a `TODO: investigate` placeholder. If you genuinely can't determine the list without operator input (external access, a running app), that's a block — STOP and resolve it before drafting, don't draft around a hole. **In a multi-phase plan the per-file work detail lives in each shard's `## Work`; the master's `## Files touched (overview)` is the coarse, phase-tagged conflict-spotting map only — never duplicate the per-file detail up into it.** In a single-phase plan the one `## Work` section carries it. This is *plan-time* completeness; discovering an additional file *while implementing* is a different thing, governed by the Scope Check "Before touching a file" add/park/skip rule.
- **Work-shape sketches (in Work, both shapes)**: Any work item whose change isn't obvious from one line gets a 2-6 line sketch under it — function signatures, data shapes, or pseudo-code, whichever shows the design. The sketch exists for the approval gate: a reviewer can catch a design disagreement in a signature; they can't in "fix search". Two rules keep sketches honest. (1) **Docs-grounded**: a sketch may only use symbols and call shapes verified during EXPLORE — cite the doc or source next to the sketch, same verify-or-block standard as any other plan claim. Needing an unverified symbol means EXPLORE isn't done; go back and verify, don't sketch from memory. (2) **Interface binding, body illustrative**: the signatures, data shapes, and error behavior the user approved are contract — changing them mid-work is an approach switch (Mode 2 step 2 re-plan trigger). The body mechanics are illustrative and expected to flex on contact with reality; divergence is a one-line note in Decisions & findings, not a re-plan. Skip the sketch for obvious items — a sketch on "bump the version constant" is noise.
- **Interface lines (in Work, both shapes)**: every phase's `## Work` carries a `- Consumes:` and a `- Produces:` dash bullet — per task when the phase has a task tier, once for the whole Work section when it does not. **They are mandatory with `none` as a valid value; absence is the only invalid state** (the same model as the sweep record's `code: nothing pinned` — a written `none` is a claim someone made, an omission is indistinguishable from forgetting). `Consumes:` names the exact signatures and types this work CALLS that already exist — signatures only, with the EXPLORE citation that grounds them sitting on the file bullet or its sketch. `Produces:` names the exact signatures and types this work CREATES for later tasks or phases to call — those symbols do not exist yet, by design, and the grounding rule does not apply to them; what grounds a `Produces:` line is the work-shape sketch beside it. **A later phase that consumes an earlier phase's product REPEATS it as its own `Consumes:` line.** That duplication is deliberate: the worker's entire world is one pasted shard, so a `Consumes:` line pointing at another file is a referent the worker cannot resolve. It is the verifiable kind of duplication — the executability auditor reads the two lines against each other — not the kind DRY bans. **Interface lines carry NO file paths — not as a citation, not in passing, in no form.** Paths live only on file bullets, and the reason is mechanical: a path extractor that builds the staleness diff reads paths off every Work bullet without caring which kind it is, so a `file:line` citation on a `Consumes:` line silently joins the phase's drift set as though the phase modified that file. The grounding still happens; it just happens on the file bullet, where the path belongs and where drift detection is supposed to see it. And a shipped interface that differs from the approved one is not a detail — it fires the approach-switch re-plan trigger at that phase (the rule is in Rules above).
- **The task tier (in Work, both shapes)**: when a phase's Work splits into more than one job, each gets a `### Task N — <one-line scope>` heading INSIDE `## Work` — not a separate section, not a numbered list. Three rules make the tier work and none of them is negotiable. (1) **Each task's file set is DISJOINT from every other task's**: a file appears under exactly one task, always. A phase whose work genuinely cannot be split that way is formally unsplittable — leave it untiered rather than listing a file twice. (2) **Dash bullets remain the only path carriers.** A numbered list is silently invisible to a path extractor, so files listed that way vanish from every drift check that reads the Work list — the bullet leader is machine-read where the full /plan skill's checker runs, not cosmetic. (3) **No per-task Done criteria.** Criteria stay phase-level; a task is a unit of *scope attribution*, which is what lets the spec check say which task a diff hunk belongs to. Tasks are consumed IN ORDER by ONE worker and are not commit units — the phase is still one commit. Skip the tier entirely for a single-job phase; a `### Task 1` with nothing beside it is noise. The tier is also an observable trigger: a phase with more than one task routes its spec check to an independently dispatched reviewer instead of the dispatching session (Phase Completion Cycle step 1c).
- **Decisions & findings (in the shard)**: One entry per **non-local** decision (the threshold rule in Rules); each entry is Decision / Rationale / Alternatives considered / Evidence (file:line or URL+section). Mark a decision a later phase invalidates as `superseded by phase-<id>` — don't silently rewrite it. Append empirical findings (spike results, mid-implementation gotchas) here as the phase runs; this is what stops the next session rediscovering them after a clear. (Where research lives — inside the shard, never a separate file — is its own rule in Rules.)
- **Background findings (master only)**: ONLY cross-phase research that belongs to no single phase. Anything scoped to one phase belongs in that phase's shard, not here. This is the one research home in the master, and it never grows per-phase detail.
- **Failure modes**: Aim for 5+. If you have fewer than 3, you don't understand the problem yet. Draw from: similar past failures, platform quirks, unfamiliar dependencies, integration boundaries, untested paths. **But this section is not a sink for unresolved verifications.** A conditional whose antecedent is statically checkable ("*if* warmups can't be marked complete…"; "*if* this endpoint doesn't return X…") is a fact you didn't look up wearing risk's clothing: resolve the antecedent during EXPLORE, then either delete the entry or restate it as the verified fact — the verify-or-block rule bites on the antecedent, not just on declarative claims. A legitimate failure mode is one whose outcome remains uncertain *after* you've verified everything statically knowable about it.
- **Done criteria**: These are the exit conditions. When met, STOP — no polish, no adjacent improvements. Each criterion must be concrete and verifiable. In a multi-phase plan there are **two levels**: each shard's Done criteria are that phase's commit-level exit, and the master's Done criteria are the plan-level, cross-cutting exits (whole-feature suite green, deployed/pushed, end-to-end result) that belong to no single phase. The master's level is NOT a copy of the per-phase criteria — restating them there is the duplication to avoid; it holds only what spans phases. The plan is done when every shard's criteria AND the master's are met.

  **Any phase whose `## Work` names a file that draws UI carries a visual-capture criterion, and that is not optional** (the rule is in Rules above). Write it as the state to capture, not as "screenshot it": *which* screen, under *which* data condition, and what the capture must show — "the summary card at zero logged entries with a known total behind it: the drained ring, no stale figure, the nudge copy below". A criterion the implementer can satisfy with any screenshot of that screen is not one. Note where the state is unreachable in the project's fixture/simulator setup and say what covers it instead; that is a real answer, and it belongs in the plan rather than being discovered at capture time. A UI phase whose criteria are all satisfiable by a green test suite is incomplete — type checking proves the code compiles, never that the feature looks right.

  **When the phase's Work CONSUMES a type with enumerable states it does not itself define** — an enum, a status field, a state machine — **the Done criteria enumerate ALL of its states and say what each one produces.** The trigger is observable: does the phase read a value of an enum (or equivalent) type defined elsewhere. Count the cases in the source, not from memory — and if the count in the shard and the count in the source differ, the shard is what's wrong. Named states get built; unnamed states get whatever the default branch does. In the incident behind this rule the two states the shard named were built correctly, and the three it did not name shipped as a permanent placeholder drawn over real data — enumeration, not competence, was the variable.

  **And no phase's Done criteria — UI or not — may be fully satisfiable by a green suite plus greps** (the standing rule lives in Rules above): at least one criterion names an observable the phase must produce and measure. This is also what arms the dry-run prober's MEASURED channel — a prober aimed at all-grep criteria stops at green by instruction. A constraint a downstream sweep carries into this phase arrives as one of these criteria, phrased as the observable it protects, never as relocated prose.
- **Parking lot**: Always start empty. The skill never pre-populates it.

---

## Execution brief

Loaded by the Phase Completion Cycle step 1. Substitute the `{...}` placeholders and send the rendered text as the `prompt` on an `Agent` call.

- `subagent_type: "general-purpose"` — never `Explore`, which omits CLAUDE.md. Where the full /plan skill's dispatch gate is registered, it enforces this, keyed on the `Plan work:` line that opens the rendered brief below; keep it at the start of a line or the gate stops seeing worker dispatches. Like the audit marker it carries the agent-type rule ONLY — a worker owes none of the research boilerplate.
- **No `isolation`.** The worker runs in the main checkout so the next phase builds on its edits. Worktree agents branch from the repo's *default branch* rather than the parent's HEAD and hand back only a text report with no merge — that solves concurrency conflicts, which sequential phases do not have, while breaking the one property they need.

Placeholders: `{phase}`, `{slug}`, `{shard_text}`, `{repo_path}`.

**No project-rules placeholder, deliberately.** A `general-purpose` subagent already receives every level of the CLAUDE.md hierarchy the main conversation loads — that is the same fact that forces `general-purpose` over `Explore` everywhere in this skill. Pasting project rules into the prompt would duplicate what the worker already has, and would drift from the source the moment CLAUDE.md changes. If a worker turns out to be missing a project rule, the fix is in that project's CLAUDE.md, not in this template.

```
Plan work: {slug}.
You are implementing phase `{phase}` of the `{slug}` plan — a plan you did not write.

You have no history with this work, and that is deliberate. You are not carrying anyone's opinion about how big this change ought to be.

## The phase

{shard_text}

## Repo

`{repo_path}`

## Rules

- **Implement what the phase's `## Work` section describes.** If a file you need to touch is not listed there, STOP and report it. Do not edit it, and do not quietly widen scope to cover it.
- **If the Work section has `### Task` headings, consume them IN ORDER — never reorder them, never interleave them.** Finish a task before opening the next one. This is not a style preference: the phase's spec check evidences the diff task by task, and per-task evidence only exists if the work happened task by task. A file belongs to exactly one task, so an interleaved worker leaves hunks nothing can attribute.
- **If the correct fix is BIGGER than the phase describes, do it bigger and say so in your report.** Diff size, file count, and implementation effort are not your inputs. The phase was scoped by someone reading, not building; you are the first to see the real shape.
- **If you catch yourself designing a smaller way around the problem, STOP and report that instead of building it.** A workaround you ship is worse than a blocker you name, because the workaround is invisible afterwards and the blocker is not.
- **If you need information the phase does not give you, STOP and report the gap.** Do not guess, and do not invent context to fill it. A phase that cannot be executed from its own text is a defect in the plan, and your report is the only thing that surfaces it.
- Follow the phase's TDD instruction if it has one: failing test first, minimum code to pass, then refactor.
- **Do NOT commit.** Do NOT run a review skill (`/code-review` or equivalent, if the project has one). The dispatching session reviews your work, runs the project's test gate, and commits.
- Never `git add -A` or `git add .` — the dispatching session stages explicit paths.

## Report back

Three sections, in this order:

1. **What you changed, by task** — for each `### Task` in the phase's Work, in order: the task heading, then the files you touched under it and what changed in each. A phase with no task tier has exactly one task — its whole Work section — and reports as a plain file list. If you STOPPED mid-phase, still list every task you completed here.
2. **Every empirical finding** — gotchas, surprises, anything the phase got wrong about the code, anything the next phase would otherwise rediscover. **This is the only record of them.** You hold context that disappears when you return; nothing else is holding it, and the dispatching session writes your report into the plan's durable record.
3. **Anything you stopped on** — scope gaps, missing information, a bigger-than-described fix, a workaround you declined to build. If you stopped mid-phase, **NAME the task you were executing when you stopped**: the tasks before it are complete, the tasks after it are untouched, and your uncommitted edits stay in the checkout for the dispatching session to disposition. Do not revert them and do not commit them — that session resumes from where you stopped, and it can only do that if it knows which task you were in.

Section 3 empty is a normal result. Section 2 empty almost never is — if you genuinely found nothing surprising, say that explicitly rather than omitting the section.
```
