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
- Upstream's hook-and-checker machinery (its dispatch gate and plan checker
  script) is re-expressed as the session's OWN obligations, with one Optional
  enforcement note at the end. The artifact conventions those checks key on
  (`Approval:` markers, the `Plan slug:` / `Plan audit:` / `Plan work:` brief
  openers, the verbatim brief fragments) are KEPT byte-for-byte — inert
  without the hooks, load-bearing wherever they exist.
- Upstream's `references/` briefs and templates land INLINE as the trailing
  sections of this file; nothing here requires a sibling file to exist.
  RATIONALE.md is deliberately NOT inlined — it is incident history for the
  operator's own shipped plans, not instruction — so its pointers are dropped
  rather than rewritten.
- App-specific illustrations (Swift type names, HealthData screens) are
  generalized; this ships to strangers' repos.
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

   **🛑 STAGE ZERO — surface and settle the design forks BEFORE dispatching the teams.** Read the operator's goal, the spec if there is one, and the code long enough to answer one question: **is there a fork between two or more candidate designs whose outcome would change the phase map** — different phases, different Work, different Done criteria, a different container / schema / protocol the later phases get written against? Every such fork is routed HERE, before Team A or B is briefed, by what settles it. A fork that reading code or docs this session can close is closed now and written into the plan as a locked decision. A fork that hinges on the operator's product intent is asked now, as a plain-English forced binary — what is being decided, what each answer commits the plan to, evidence beneath. A fork only the research can inform is NAMED now: the teams are briefed neutrally on the territory the candidates share (the neutral-brief rule stands — no agent is told the candidates), and after research returns YOU read its findings against each candidate and draft the plan on the one the evidence best supports, citing the findings that support the reading — then the fork goes to the operator at approval as a forced binary carrying the evidence for BOTH candidates, so your reading is theirs to overturn, not a silent default. If the operator picks the other candidate, that is a restructure under step 9 — re-run the affected research against the winner and step 7 on the rewrite.

   **The ordering is the whole point and it is not negotiable.** Team A asks what the change BREAKS and Team B asks what shape it should be — neither question has a sharp answer while the design is an unacknowledged fork. A fork named before dispatch shapes the briefs; a fork discovered after them means the research answered about a shape that may not survive.

   **When research surfaces a fork nobody knew about**, the same rule applies retroactively rather than being waived: settle or surface it the same way, and once a winner exists, **RE-DISPATCH Teams A and B against it.** That re-dispatch is not optional and is not a second opinion — the first pass answered about a shape you have now discarded, so its change-impact findings describe code you are not going to write. Cheaper than it sounds: it is two agents, and it is strictly cheaper than the phase that would otherwise exist to resolve the fork.

   **A re-dispatch does not count against the agent-count table below.** The table sizes *research breadth*; re-asking a question against the design that actually won is not new breadth, and cutting the re-dispatch to stay under a baseline briefs the plan against research about a discarded shape.

   **What does NOT count as a fork:** a question whose answer changes an implementation detail inside a phase but leaves the phase map alone; and the algorithmic load-test and the Diagnosis falsifiable test, which have their own placement rules and stay where those rules put them, even though a failure in either can send you back here. The trigger is "would the PLAN be different", not "could this go wrong later".

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


   **Each RESEARCH agent's brief includes** — Teams A, B, C and the conditional specialists:
   - The slug + a one-line goal of the plan being scoped (so the agent knows what it's researching for)
   - Specific questions tailored to its role — sharp enough that a generalist wouldn't have written them
   - For algorithmic plans, the four required questions below concentrated in the implementation-details specialist's brief
   - An explicit instruction: "You are NOT to invoke the `/plan` skill. Your job is research only. Report in under 400 words."
   - **The citation requirement, verbatim:** "Cite file:line for local sources, URL+section for fetched ones. A claim you did not open a source for is reported as unverified, not as a finding." *(This bullet used to paraphrase the rule instead of stating it. The dispatch gate matches the literal, so a brief assembled from this checklist was denied while reading as complete — fixed 2026-08-03.)*
   - **The comment ban, verbatim:** "A code comment is a claim by a past author, not evidence. Ground every finding in what the code does; cite a comment only as intent, and a comment the code contradicts is itself a finding." *(Why: comments rot without breaking anything, so they are exactly where stale claims survive — in the coverage-gate incident behind step 5(c)'s fourth question, three review angles and six verifiers read a comment arguing for a bug that no longer existed, and none noticed.)*
   - **The effort-objection ban, verbatim:** "Diff size, file count, and implementation effort are not your inputs. Recommend what is correct."


   **Divergent-design note**: if exploration surfaces significant scope questions the user can't decisively answer (e.g. "should we refactor this whole subsystem or just patch it?"), suggest a separate divergent-design pass (e.g. a `/brainstorm` skill if your setup has one) before re-running `/plan`. Do NOT run that pass inline from within this skill — it's user-interactive and doesn't compose cleanly.

   **Consolidate findings as ground truth for the plan** (internal step; not surfaced to the user as a separate report).

   **STRUCTURAL FINDINGS BAKE IN BY DEFAULT — you do not get to shrink them quietly.** When any agent recommends reworking a whole file or more, the plan is drafted WITH that rework. Talking it down to something smaller requires showing the larger approach is *infeasible* and citing the blocker (`path:line` or doc URL), and the downgrade is surfaced to the operator at approval as a forced binary decision — the same treatment the reuse and exclusion specialists get. **A talk-down that cannot cite a blocker is not yours to make at all:** the infeasibility bar is hard to clear against code that does not exist yet, and that is by design — when no citation exists, the structural shape stays in the draft and the disagreement goes to the operator at approval as a forced binary, with the structural shape as the default.

   **Three sharp edges on the bake-in rule, each from one shipped failure (2026-08-12, a six-phase retrofit approved against a "rebuild, not retrofit" arc decision).** **Partial adoption does not discharge it** — a structural proposal is adopted or declined AS A SHAPE, and adopting its cheap half while declining the rest IS the talk-down, owed the same infeasibility citation and the same approval surfacing as declining it outright. **Existence is not infeasibility** — "it already ships", "don't rebuild what exists", "the real delta is small" cite the current shape's existence, which proves nothing about the proposed shape's feasibility; they are banned as decline reasons on the same footing as the effort sentences below. And **a structural proposal that matches a recorded operator decision** — a transcribed upstream decision (step 6), or one recorded in this plan — **is not yours to decline at all**: declining or shrinking it un-makes a decision the operator made, so it goes to step 8 as a forced binary drafted with the operator's own decision as the default.

   **Diff size, file count, and implementation effort are not inputs to this decision, and that applies to YOU here, not only to the agents.** "It's a lot of work", "that's a big diff", "fewer files is lower risk", "let's keep this contained" are the sentences this rule exists to stop. You are the party who formed an opinion about the size of this change from a skim before any research ran; the agents are not. When their finding and your prior disagree, the prior is the thing without evidence.

   Walk away from exploration with:
   - The corrected understanding of the area being changed — including **what the change will break**, not only what exists (Team A) and what the canonical pattern is (Team C)
   - Team B's from-scratch shape, recorded even when the plan does not adopt it, so the approval conversation can see what was on the table
   - Any forced binary decisions surfaced by reuse / exclusion specialists, with the specialist's recommended option
   - Any load-bearing implementation details surfaced by the algorithmic specialist (for algorithmic plans)
   - **No unverified claims survive EXPLORE** (Rules: "No research deferrals — verify or block"). The only things research legitimately can't close are (a) genuine operator decisions (surfaced at approval) and (b) empirical/runtime unknowns (which become the Diagnosis falsifiable test or the algorithmic load-test, never plan-body facts) — **and (b) is narrower than it reads: an empirical unknown whose answer would change the phase map is a STAGE-ZERO fork — surfaced now and settled with the operator, never routed into execution.** Anything else unresolved means EXPLORE isn't done — finish it, or STOP and resolve it with the operator before drafting.
     - **(b) has a membership test — apply it, don't self-certify into it.** A question qualifies as an empirical/runtime unknown ONLY if a Read / grep / doc-fetch *this session* genuinely cannot close it — i.e. it truly needs a running app, live external system, or real model output. If reading the code or docs would settle it, it is NOT empirical: verify it now. The tell that this rule is failing is a plan that says "Phase 1 must verify X" where X is statically checkable (does this function branch on that flag? does this type have that field?) — that "verification" is the EXPLORE work you skipped, not a legitimate deferral. Ask "does closing this need runtime, or just a Read?" before routing anything to (b).
     - **🛑 (b) DOES NOT MEAN "resolve it during execution" when the answer changes the plan** — that fork was surfaced at STAGE ZERO above, before the teams were briefed, and if this pass surfaced a new one it gets settled or surfaced the same way and the affected teams re-dispatched rather than the question being carried forward. Needing runtime says what closes a question, never when. **The anti-pattern, and its tell: a phase whose done signal is "a decision is recorded" rather than "something works."** That phase is an unresolved fork wearing a gate's clothing — every phase after it was drafted against an assumed answer, and if the answer goes the other way the plan is rewritten, which is the outcome planning exists to prevent. The origin case deferred a container choice to phase 1; the composition it sealed carried a view-identity change that killed per-view state on every data change, and no gate here could see it because at research time there was no composition to see. *(Operator, 2026-08-07: "exploration phases during implementation time completely defeat the purpose of a planning stage. Planning is the time I set aside for figuring out if a proposed route will even work.")* **An unresolvable fork** — the answer needs hardware, a live service, or a real user, AND the operator, asked at plan time, chose to defer rather than decide — stays (b) and routes to execution. Both halves are required: the operator is always available at plan time, so "empirical" alone never carries the deferral — name which runtime the answer needs, and record the operator's deferral in the plan.

5. **Draft the plan** using the template in the Plan templates section below, with research findings as ground truth. Three rules for drafting:
   - **Every factual claim in the plan must be supported by exploration findings.** Cite file:line / URL+section in the plan body where the claim depends on a specific verified source.
   - **Verify or block — no deferral channel** (full rule in Rules: "No research deferrals — verify or block"). Every claim in a drafted plan is verified this session and cited with file:line or URL+section, or the plan isn't written yet.
   - **Bake forced binary decisions into the plan as the recommended option.** If the reuse specialist recommended a Phase 0 refactor, draft the plan with a Phase 0 refactor included. If the exclusion specialist recommended folding excluded items into scope, draft the plan with them included. The decision still gets surfaced explicitly at approval (see step 8) so the user can override, but the plan reflects the recommendation by default.

6. **Write the file(s).** Single-phase plan → one file `plans/<slug>.md`. Multi-phase plan → the master `plans/<slug>.md` AND every shard `plans/<slug>-<phase>.md`, written in the same step. Every plan is written with `**Approval: DRAFT**` as the first line of its Status section (single-phase: `## Status`; master: `## Status & cold-start`) — the marker flips only at explicit approval in step 9 — and an `**Authored at: <current HEAD commit>**` line beneath it, which Mode 2's staleness check diffs from. **Drafting the shards is not optional and not deferrable to "when the phase starts" — a multi-phase plan whose shards don't exist yet is not written.** Every shard a phase needs to be self-sufficient is authored now, at plan time, from the same research pass; a future session resuming the plan reads a shard that exists or it has nothing to resume from.

   **When the plan derives from an upstream document that carries operator decisions** — an arc index, a parent plan, an approved spec — write a `**Upstream: <path>**` line beneath the Status markers and transcribe that document's decisions into a `## Upstream decisions (transcribed)` section in the master, per the Plan templates section below: verbatim, each with `<path>:<line>` provenance, and including the document's OPERATIONAL rules (sentences bindable per file or artifact), not only its numbered decisions — the operational sentence is usually the detectable one and the slogan is not. This is transport, not summary: the coherence auditor reads only plan text, so an untranscribed upstream decision is invisible to every gate this skill runs — that is exactly how a six-phase retrofit shipped against an arc whose own index said "nothing is rewired on the way to being deleted" (2026-08-12). No upstream document → omit both halves; the two halves are paired, and the absence of both is a claim to check rather than a silent pass.

   **Then run the mechanical self-check over what you just wrote, before step 7 dispatches anything.** These checks are this session's OWN obligations: every Phase-map block names a shard file that exists on disk; every block carries its four sub-fields (Enters when / Done signal / If it fails / Shard); no stray `<slug>-research.md` exists; no literal deferral token survives (`TODO: verify`, "confirm during implementation", a placeholder); every phase's `## Work` carries its `- Consumes:` / `- Produces:` lines; file paths ride dash bullets, never numbered lists; an `**Upstream:` declaration is paired with a transcribed section and vice versa; every tiered phase's task file sets are disjoint; and no file in the master's `## Files touched` both receives work AND is deleted by this same plan — that last one is never silenced, it travels into the approval summary with the justification for why the plan does both. Settling the mechanical half here is what lets the step-7 auditors spend their one pass on claims instead. One trap worth naming: in a repo whose plans are ABOUT planning machinery, a prose sentence *mentioning* a deferral token is indistinguishable from one *deferring* — fence it in a code span or reword it. (Where the full /plan skill is installed, a checker script runs this set mechanically — see Optional enforcement below. It has no PASS state on purpose: it ends by naming the judgment obligations it could not check, and none of those are discharged by it running.)

7. **VERIFY — adversarial read-back of the written plan (mandatory, unconditional; runs BEFORE the user ever sees it).** This gate exists because the plan's grounding rules — verify-or-block, shard self-sufficiency, peer-set exclusion rationale — are all self-certified by the same session that just wrote the plan, and a self-certified rule is the one that rots (same enforcement logic as the sweep record, Phase Completion Cycle step 5(c)). Running the pass *after* presenting the plan inverts it into the failure it prevents: the operator becomes the reviewer. Applies to both shapes — a single-phase plan gets the same pass, scoped to its one file.

   **Dispatch all agents in a single message so they run in parallel. The three auditors are READ-ONLY — they never edit a plan file; you apply every fix.** Every agent here → `subagent_type: "general-purpose"`. **Never `Explore` here** — the grounding auditor's entire job is checking claims against sources under the verify-or-block rule, and Explore omits CLAUDE.md, so an Explore grounding auditor audits without the rule it is auditing against. **Three auditors, fixed** — the auditor axes are disjoint and none can answer another's question. This is not step 4's scaling dispatch; don't grow it by plan size. **Pass every brief verbatim** — paraphrasing them into one generic "review this plan" is the degradation this step's specificity exists to prevent.

   **Every brief in this step opens with `Plan audit: <slug>.` on its own line, and that line is load-bearing.** Where the full /plan skill's dispatch gate is registered, it keys on that line to apply the `Explore`/`Plan` ban to audit dispatches — without it the gate cannot see this step at all, and the one dispatch the paragraph above bans by name is the one nothing checks. It is a *separate* marker from step 4's `Plan slug:` on purpose: the research invariants that marker demands are research-shaped (an auditor recommends nothing, so the effort-objection ban is meaningless to it), so an audit dispatch is held to the agent-type rule and nothing else.

   **The auditors verify by EXECUTION, not by reasoning** — with one deliberate exception: the coherence auditor is told not to open a source file at all, because its evidence IS the plan's own text and it quotes both halves of every contradiction. For the other two, an auditor that reasons about whether a claim is plausible reproduces the drafting session's assumptions, and the failure mode is a whole panel confidently agreeing on a shared error — the documented case is 80+ agents, including a senior arbiter, unanimously confirming an OpenSSL vulnerability that did not exist, killed by one instance that compiled the code and ran three test cases. **The instruction is to RUN the check**: open the file, run the grep, fetch the URL, and quote what came back. A claim the auditor did not open a source for is reported as unchecked, never as resolved — plausibility is not a resolution. Do NOT substitute "use a different model" for this; the decisive variable in that case was empirical execution, not model choice.

   **Dispatch effort is fixed per auditor, and it is set low on purpose.** Coherence at **low** — its evidence is the plan's own text, it opens no source, and it quotes both halves of every contradiction it reports, so there is nothing for a higher setting to buy. Grounding and executability at **medium** — both are mechanical checks (open the file, run the grep, match two lists against each other) whose accuracy holds there. **Effort is the dial to turn if the step feels expensive — never the auditor count.** Dropping an auditor deletes an axis no other agent covers; lowering its effort deletes nothing.

   **This step's three briefs are 1-3 in the Plan audit briefs section below — pass them verbatim.** Three read-only auditors on disjoint axes (grounding, executability, coherence). (Brief 4 in that section belongs elsewhere and is NOT part of this dispatch: it audits a diff at the Phase Completion Cycle's step 1c.) Every one opens with the `Plan audit: <slug>.` line — drop it in a paste and any registered gate stops seeing this step.

   **A clean pass still reports counts.** If an auditor cannot name what it checked, it did not run the pass — re-dispatch it. That is re-running a pass that never happened, not a second round on findings, and it is **the only re-dispatch this step permits.** Carry its count sentence into the record verbatim; never restate it from memory.

   **Findings are BLOCKING, not advisory — and you get ONE pass.** Dispatch the auditors once, fix what comes back once, then proceed. There is no re-dispatch and no second FINDINGS round — the re-read/edit/re-read cycle was ruled out on cost; the one named carve-out (the count re-dispatch above) is a pass over work that never ran, not a second round. Do NOT hand the operator the plan plus a findings list to triage — that is the inversion this step exists to stop.

   Four exits per finding, all recorded: (a) **fixed** in the plan file — **and the record must NAME the fix, not just count it**; (b) **promoted** — it is a genuine operator decision, so it becomes a forced binary decision surfaced at step 8, and the plan is drafted with the auditor's reading as the default (same bake-in rule as the reuse and exclusion specialists); (c) **refuted** — you checked the source yourself and the auditor was wrong, with a `file:line` / URL citation in the record; (d) **uncheckable** — the grounding auditor could not reach the source at all, which per the verify-or-block rule is never a pass: close it yourself, pull the claim from scope, or promote it via (b). "The agent was probably wrong" without a citation is not an exit, and neither is silence.

   **Every exit-(a) fix is classified before the record is written, because the record cannot tell these apart for you.** A **correction** writes down a value, name, or citation the finding itself supplied — apply it and name it. A fix that introduces a construct appearing in neither the pre-audit plan text nor the finding is a **new mechanism** — untested design minted during the fix pass, which nothing in this step will ever re-read — and it does not ship on the drafting session's say-so: restate it as a correction the finding actually supplies, or take exit (b) and put it to the operator.

   **One finding class has narrower exits: a plan default that REVERSES a transcribed upstream operator decision never takes exit (a), and takes exit (c) only by citing a supersession recorded in the upstream document itself.** Anything else is exit (b) — the scope of an operator's decision is the operator's to read, not the drafting session's, and the one legitimate refutation is the upstream document recording that the operator already changed it.

   **Because nothing re-checks your fixes, anything you cannot close cleanly in the single pass takes exit (b)** and goes to the operator as an open question. Reaching for (b) when a fix is uncertain is the correct move, not a cop-out — it is cheaper than a wrong fix nobody re-read. What (b) is NOT for is "I didn't want to look it up"; that is exit (a) with the lookup done. Refutations and uncheckables are counted separately in the record precisely because an all-refuted pass and a clean pass must not look alike.

   **Record the outcome** in the plan's Status (single-phase `## Status`; master `## Status & cold-start`), in the VERIFICATION RECORD shape carried by the Plan templates section below, written from the agents' reported counts and never from intention. The record carries all three lines — claims, done criteria, coherence — because a pass that skipped an axis and a pass whose axis found nothing must not look alike. The record describes the plan **as written at approval.** On a multi-phase plan, later execution edits announce themselves — each sweep record lands in the same Status and visibly post-dates it. A single-phase plan has no sweep, so when the Scope Check adds a file mid-work, note it in Status alongside the record; otherwise the record silently claims coverage of text it never saw.

8. **Emit the APPROVAL SUMMARY, then ask for approval.** The summary comes FIRST and it is not optional — the format lives in the Approval summary section below and is followed exactly.

   **Why this step exists.** A multi-phase plan is tens of thousands of words across seven files. Handing the operator a set of paths and asking them to approve is asking them to approve on trust, because nobody reads or greps that in their head — and an approval given without sight of the contents is not review, it is a formality that the whole EXPLORE-and-verify apparatus upstream was built to earn and then throws away at the last step. *(Operator, 2026-08-12: "your plans are too huge for me to read and grep all of in my head so I need a COMPLETE bulleted summary of the master and each phase after they're all written.")*

   **The rule the format turns on: ENUMERATE, never précis.** Every locked decision, every non-goal, every file, every done criterion gets its own bullet, derived by READING the files you just wrote rather than by recalling what you meant them to say. Merging three decisions into one smooth sentence destroys exactly what the operator needs and reads better than the correct version, which is why it is the failure to guard against. **Length is expected**: do not offer a short version, do not stop early and ask whether to continue, and never trade completeness for brevity here. An empty section is reported as empty, never omitted.

   **A plan that deletes or replaces anything also lists its SURVIVORS** — everything on the touched surface that is NOT in the deletion list, with one line on why it stays. That list is the only part of the summary that is not a restatement, and it exists because a plan's deletions are derived from its REPLACEMENTS, so anything the new design has no successor for belongs to no phase, lands in no Work list, and is invisible to every downstream gate at once (the spec check reads the Work list; the sweeps hunt what a phase invalidated, not what nobody named). Full origin incident in the reference.

   Then:
   > That's everything in the plan. Approve / tweak / reject — I won't write any code until you say it's live.
   >
   > [If reuse specialist surfaced a decision]
   > **Reuse decision baked in:** plan adopts Phase 0 refactor of `<duplicated surface>` based on `<file:line>` evidence. If you'd prefer copy-and-defer (ship the duplication, file the dedupe as follow-up), say so and I'll restructure.
   >
   > [If exclusion specialist surfaced a decision]
   > **Exclusion decision baked in:** plan folds `<excluded items>` into scope based on `<file:line>` dependency on `<included items>`. If you'd prefer to keep the exclusion, give me the one-sentence invariant that makes the asymmetry safe.
   >
   > [If step 7 promoted a finding — exit (b)]
   > **Verification finding needs your call:** `<what the auditor found>`. Plan is drafted with `<the auditor's reading>` as the default. If you want it the other way, say so and I'll restructure.

9. **Block on user response.** Do not touch any code files, do not run any builds or tests, until the user explicitly approves. If the user picks copy-and-defer for a reuse decision, append the deferred refactor to the plan's Parking lot in writing before code starts. **If their response restructures the plan** — overriding a baked-in decision, adding or cutting a phase, changing Work or Done criteria — the verification record now describes a plan that no longer exists: re-dispatch the affected auditor over the rewritten sections and refresh the record before flipping the marker. **This is not a second findings round** — it is a first pass over text no auditor has ever seen, so it does not contradict the one-pass rule above. If the restructure is small enough to self-check, note in Status that the record post-dates it and say what changed. On approval, flip the plan's marker to `**Approval: APPROVED <date>**` — the file itself must record the approval, because a future session can't see this conversation.

10. **Once approved, enter "working the plan" mode.** You orchestrate rather than type: dispatch each phase's worker, read its report, review and gate its diff, commit, seal the shard, sweep downstream. Interrupt scope creep, and append to the parking lot when the user drops a shiny idea mid-work. The plan is referenced on every dispatch and on every report you read back — not on every file touch, because the file touches are not yours.

### Mode 2: Resume an existing plan (`/plan <slug>` — file exists)

1. **Read in this fixed order — do NOT skip a step.** (a) The master `plans/<slug>.md`: Phase map (the arc + gates) and Status & cold-start (which phase is NEXT). (b) The **NEXT phase's shard** `plans/<slug>-<phase>.md` in full — Locked decisions, Work, Decisions & findings. (c) The master's Background findings, plus any earlier shard whose Decisions & findings the NEXT phase's gate references. (d) **Any spec / sub-document the plan references** (lines like "Full spec: <path>", brainstorm outputs, design docs, ADRs). A shard that defers detail to another file is incomplete without that file; resuming from a summary alone forces re-deriving a dependency map the shard already has — the failure mode is a fresh session "discovering" entanglements (call sites, threading requirements, focus contracts) the shard recorded at plan time. **You read the NEXT shard, not every shard** — shipped phases are reference-on-demand; reading all of them back is the context-clouding this sharded layout exists to prevent. The re-anchor pass in step 2 *confirms* the recorded map against current code; it never re-derives one from scratch. If the code read surfaces a dependency the shard doesn't record, that's a finding to write back into the shard, not silent context. (e) **Check the Approval marker** in the Status section: if it still reads DRAFT, this plan was never approved — take it through Mode 1 steps 7-9 (verify, then present, then block) instead of resuming work on it. A DRAFT plan from an earlier session carries no verification record, or a record predating every commit since; it gets the read-back before it gets an approval. (A `Drafting session:` line in an older plan's Status block is a relic of the removed draft gate — ignore it; nothing reads it anymore.)

2. **Validate the plan against current reality before working it (mandatory — do NOT skip to working a stale plan).** A resumed plan was authored in a prior session; resume mode deliberately skips Mode 1's EXPLORE, which is only safe *if the plan still matches the code*. **First, check the sweep record**: the master's Status must carry a `Downstream sweep at <phase>` line for the most recently shipped phase, naming every unshipped shard AND closing with a `code:` segment (Phase Completion Cycle step 5(c)). If it is missing, the shards you are about to work from may carry instructions that phase falsified — run that sub-step retroactively for the phase BEFORE reading the NEXT shard as truth, because a false instruction is worse than a stale note and you are about to follow it. If the line is present but has no `code:` segment, run only the fourth question retroactively: what did that phase pin or constrain, and which guard in an earlier phase's shipped source was built for the freedom it removed. That half is the one that leaves a live defect in the app rather than a stale note in a file. Same pattern one more time: when the master declares an upstream document (`**Upstream:` in Status) and the record has no `upstream:` segment, re-read that document against the just-shipped and NEXT phases before working anything — the re-read is owed every phase, and a missing segment means it did not happen. Then start mechanical: list the commits that touched the plan's files since it was authored or last refreshed — `git log --oneline <that commit>.. -- <every file in the NEXT shard's Work>`, where `<that commit>` is the Status section's `Authored at` line (a plan predating that line: fall back to `git log` on the plan file itself) — those diffs are exactly where drift lives; read them before any judgment-based check. Union the paths across the master's Files touched (overview) and every shard's Work, and read the path accounting honestly: a Work bullet that yields no path, or a named path git cannot resolve, produces an empty diff that reads as "no drift". **Re-run the write step's mechanical self-check at the same time** — shard files present, Phase-map sub-fields, stray research file, deferral tokens, interface-line presence once any phase has adopted them, dash-bullet path carriers, `**Upstream:` paired with its transcribed section, task disjointness, and the doomed-file pairing. (Where the full /plan skill is installed, its checker runs the diff and the mechanical set for you — see Optional enforcement; it never returns green, and its judgment obligations stay yours, including the sweep record above.) **Check the spec-check record the same way you check the sweep record**: a shipped phase with no `**Spec check at <id>**` line in Status is the same drift alarm as a missing sweep line — run step 1c retroactively against that phase's commit before working the NEXT one, rather than assuming it happened and went unwritten. **A legacy Status record that mentions a probe — a dry-run line, a `deferred to phase start` note — is VOID, not an obligation**: the probe fleet was removed 2026-08-18 (see the tombstone under Optional enforcement), so do not dispatch anything for it; add one Status line retiring the record and move on. Then spot-check the plan's load-bearing claims **in this session**: the NEXT shard's **Work** (does the real change touch what it lists, or materially more? — cross-check the master's **Files touched (overview)**), the **Diagnosis** (does the stated symptom/root cause match what the code actually shows?), and the **approach** (are you about to do what it says, or something else?).

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
2. **Gap-check the next phase's recorded map against the CURRENT code.** Confirm each symbol anchor exists (re-tag line hints with the current commit id), then enumerate as explicit addenda everything the code shows that the plan + spec do NOT record — especially drift introduced by phases shipped after the spec was written (a helper that gained side effects, a signature that changed, an adjacent modifier that looks like part of the region being extracted but isn't). The gap-check is the point of this mode; a handoff that only reformats the spec reproduces the lossy-compression failure this mode exists to prevent. Run Mode 2 step 2's mechanical checks as part of it: anything they decide against the master or the NEXT shard is a gap-check finding — write it into the shard as an addendum so the resuming session sees it before dispatching the phase's worker.
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
  - **🛑 If the file is one the DESCRIBED WORK REQUIRES, this is not a question and it does not reach the operator.** The worker edits it and reports it (EXECUTION_BRIEF's rule); the dispatching session adds it to the shard's `## Work` and the master's Files touched, and records in `## Decisions & findings`, in one line, what the plan should have seen. **A Work list is a PREDICTION** — written by an agent reading code, guessing which files a change will touch — and it is least reliable exactly where it is enforced hardest: a phase creating a new surface has no call graph to derive the list from. Halting on a short prediction converts a planning miss into a stalled phase and an operator interruption, twice over. This is the same resolution the review-finding carve-out below already reaches, for the same reason; they are one rule arriving at two moments. *(Operator, 2026-08-07: "if the plan needs more files than in scope to be touched then fucking do it, but that also means the planning phase was weak.")*
  - **The worker STOPS and reports it, without editing, for exactly three things:** a change altering behaviour BEYOND the phase's description; a DELETION the phase does not name; a change to a user-visible surface the phase does not name.
  - **The dispatching session asks the operator** only in those three cases: "This file wasn't in the phase — add it, park it, or skip it?"
  - **🛑 If the file DRAWS UI, the question is not just about the file — say what will look different, in the same message, BEFORE they answer.** Which screen, which state, and what a user would notice: a value that appears where nothing was, copy that changed, a section that now renders on days it used to hide, a colour or density shift. "Add it" for a view file silently means "yes, change that screen", and an operator answering a question about a *file path* has not agreed to a *visual change* they were never shown. Observable trigger, same as the UI-capture rule's: does the file draw UI. Not "is this change visual enough".
  - If they say add, edit the shard's Work to include it (explicit mutation), and the master's Files touched (overview) if it's a new file for the plan, then re-dispatch or continue accordingly. **The added file joins a TASK, not just a Work list** — it goes into the file set of the task the worker names as in flight (a phase with no task tier has exactly one in-flight task: its whole Work section). If no task's scope honestly covers it, it becomes a NEW task with its own one-line scope rather than being wedged into a task it does not belong to. Either way, **re-check disjointness at the mutation**: the file must end up under exactly one task, and an add that would list it twice is the signal the task boundaries were drawn wrong. This is the same re-fire pattern as the UI-capture rule below — the tier is checked every time `## Work` changes, not only when the plan is drafted, because the phase's spec check will attribute the diff by task and an unattributed file has nowhere to land. **When the added file draws UI, the phase also gains a capture Done criterion naming that surface** — the UI-capture rule below is written as a drafting-time check and has already fired by now, so nothing else will add it. A phase that acquires a UI file mid-flight and keeps its original Done criteria is a phase whose exit conditions describe a smaller change than the one it ships.
  - **🛑 A REVIEW FINDING on code already in flight is NOT this question — apply it, and do not ask.** *(Dispatching session only; the worker's STOP above is unchanged and stays correct.)* Observable trigger: a gate you ran — the project's review pass, a type checker, a linter, a failing test, the spec check — flagged something in the diff this phase already produced, and fixing it correctly requires editing a file the `## Work` list does not name. Fix it, note the file in the shard's Work and the master's overview, and say what you did in the checkpoint. This is not a scope decision the operator owns: the standing apply-don't-park rule names exactly three reasons to park — a cited technical constraint, a genuinely cross-cutting fix needing its own design pass, or the operator saying so — and "the file was not on the list" is not among them, so asking re-opens a decision the rules already made. **The two rules genuinely contradict each other and this clause is the resolution; do not re-derive it per phase.**
    **🛑 The UI rider above SURVIVES the dropped ask, and it is the one thing this carve-out must not take with it.** The bullet two above is written as a rider on the question — say what will look different BEFORE they answer — so removing the question would silently remove the operator's sight of a visual change too. It doesn't. When a review finding lands in a file that DRAWS UI, apply the fix and say what a user would notice, in the checkpoint, in their terms: which screen, which state, what changed. Same observable trigger (does the file draw UI), same content, later moment — the operator is being told rather than asked, and telling them nothing is not what "do not ask" licenses. If the fix changes a rendered surface in a way you would not have chosen without the finding, that is the case to raise on its own rather than fold into a checkpoint line.
    **Which findings this covers, precisely, because one gate sits on both sides.** It is about a NEW edit a fix requires, in a file the Work list does not name. It is NOT about a hunk the worker wrote in an unlisted file and did NOT report — the spec check's backward axis calls that UNCLAIMED, and an unreported edit genuinely is the add/park/skip question arriving late, because nobody authorised what already happened. **A worker-edited unlisted file is a third case, and it routes on the REASON the worker gave, never on the fact that a report exists.** The brief authorises exactly one reason — the described work could not be completed without it — so that is the only one this clause absorbs. **Reporting truthfully is not the same as reporting as-required:** a worker can accurately report an unnamed deletion, a behaviour change beyond the description, a user-visible surface the phase does not name, or an incidental "I also cleaned up X", and every one of those is still the operator's, precisely because the brief told the worker to STOP rather than edit. So: fix-needs-a-file → apply. Worker-edited, reported as REQUIRED BY THE DESCRIBED WORK → apply, and record the planning defect. Worker-edited for any other stated reason, or silently → ask. **Read the reason. A report is evidence of honesty, not of authorisation.** (And note the gate that reports the finding may be one you asked the operator to run rather than one you invoked — see the Phase Completion Cycle's step 2 for who can press the button now; the carve-out keys on the finding existing, not on who pressed it.)
    **And treat the out-of-scope file as a PLANNING defect, not a scope surprise.** A file the fix was always going to need, absent from the Work list, means the plan mis-scoped the phase — record it in the shard's Decisions & findings, in one line, naming what the plan should have seen. The Work list that named a policy on its `Consumes:` line while omitting the file whose only caller the phase replaces is the shape to look for. *(Operator, 2026-08-07: "if the plan needs more files than in scope to be touched then fucking do it, but that also means the planning phase was weak.")*
  - If they say park, append to the master's parking lot with a one-line note
  - If they say skip, move on without touching it
  - **A worker that silently edited an unlisted file is the overshoot failure** — it poisons the next phase, which was scoped against a world where that file was untouched. Read the worker's report for this specifically; "I also cleaned up X" is the tell, and it is not helpfulness.

- **Checkpoint at meaningful boundaries** *(dispatching session)* — when a done criterion is met, when a phase's worker returns, and at every phase boundary: briefly state where we are in the plan. Example: "Done criterion 2 of 4 met. Files touched: install.sh, test_install_sh.py. Still in scope." (Not per-tool-call — a phase often runs dozens of calls, and per-call status is noise.)

  **If the phase touched a file that draws UI, the checkpoint states the VISUAL DELTA in the operator's terms** — what a person looking at that screen would notice, not which files changed. "The card's empty state now says X instead of Y, and it renders on days it used to hide" is the checkpoint; "CardiovascularSectionView.swift modified" is not. A file list is not a description of a change to something a person looks at, and the operator reads these checkpoints instead of the diff.

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

   **Who runs it is an observable trigger, not a judgment call — count the phase's tasks.** **Exactly one task → the dispatching session runs it itself**, the same self-performed duty as the downstream sweep, and for the same reason it is legitimate here: the session reviews a diff it did NOT write, which Mode 4 already names as one of the three things lost when a session implements inline. **More than one task → dispatch an independent spec reviewer** — brief 4 in the Plan audit briefs section below, `subagent_type: "general-purpose"`, read-only, and its inputs are PASTED text (the shard, the worker's report, the exact diff), never a path to read.

   **It ends with a record line in the master's Status** (single-phase plan: `## Status`), bold-led, carrying counts, in the shape the Status-section commentary of both templates in the Plan templates section below specifies:

   ```
   **Spec check at p4** — tasks 3/3 evidenced · interfaces conform · none uncovered
   ```

   Counts are mandatory for the same reason they are mandatory on an auditor's report: if the pass cannot name what it checked, it did not run.

   **🛑 A step-2 review fix that ADDS a file invalidates this record, and a stale record is worse than a missing one** — the missing one alarms, the stale one certifies. The 1c-before-2 order is deliberate and stays, so the repair is at the other end: when a review finding adds a file to `## Work` under the Scope Check's carve-out, append it to this phase's record line rather than leaving it claiming `none uncovered` over a diff it never saw. `· +1 file added at review (<path>), re-evidenced` is enough. Cheap, because you know exactly what you added and why; the alternative is a record that reads as coverage of the final diff and is not.

   **A shipped phase with no spec-check record is the same resume-time alarm as a missing sweep record** — run it retroactively against the phase's commit before working the next phase, don't assume it happened and went unwritten.

   **An interface mismatch found here is a re-plan trigger, not a fix.** The approved `Consumes:` / `Produces:` lines are contract; a shipped interface that differs from them fires Mode 2's approach-switch trigger at this phase. Do NOT edit the interface lines to match what shipped — that is the bookkeeping-instead-of-escalating failure, and it silently re-ratifies a design change the operator never saw. A Work-coverage gap routes normally: an unbuilt item is unfinished work, and an unclaimed hunk routes by the REASON the worker gave for it — one reported as required by the described work joins the Work list as a planning defect, while one reported for any other reason (a deletion, behaviour beyond the description, a user-visible surface, incidental cleanup) or reported not at all is the Scope Check's add/park/skip question arriving late. A report is evidence of honesty, not of authorisation.

2. **Simplify** *(dispatching session)* — get the project's review pass run on the changed code (`/code-review` or an equivalent, if your setup has one). Where it is model-invocable, invoke it yourself; where it is operator-only, use a model-invocable equivalent if one exists, otherwise STOP and ask the operator to run it. Do not skip the gate because you can't press the button. **And invoking it is only two thirds of the job** where the review forks into an agent with no findings-reporting tool: it hands findings back as text and nothing else, so apply the fixes and then file the findings record yourself — one entry per finding with an ABSOLUTE path and an outcome — from the repo you are committing in. That record, not the review's text, is what the project's commit gate reads.

   **Two escape hatches, both narrow:**
   - **No source files in the diff** → skip the review pass regardless of size or file count, and run a **reference check** instead (not step 5(c)'s downstream sweep — different target, no record line): does every pointer still resolve, did this change invalidate vocabulary used elsewhere, is any instruction now addressed to the wrong party or contradicting a neighbour. A bug hunt has nothing to say about prose — this is the check that does, and skipping BOTH is not the carve-out. Where the project already defines which extensions count as code, use its definition rather than inventing a second one.
   - **Trivial code diff** → single-file AND single-logical-change AND no behavior change (typo fix, version bump, comment rewording). When in doubt, run it.

   A MIXED diff — any source file at all — gets the full review. The doc hatch is for diffs with zero.

   Then run any additional review gates the project's own instructions (CLAUDE.md or equivalent) mandate for this diff type — UI review passes, screenshot evidence, lint gates. Project gates compose with the review pass; they don't replace it, and neither hatch waives them.
3. **Test** *(dispatching session)* — run the project's canonical pre-commit test gate: the full suite, unless the project's own instructions define the gate for this diff type. A green subset the project's gate doesn't sanction is not green. If tests fail, fix before proceeding. Never commit red.
4. **Commit** *(dispatching session — the worker never commits)* — **first, on a single-phase plan whose Status declares an upstream document (`**Upstream:`), the upstream re-read happens NOW, before the commit:** re-read the document, ask the sweep's two questions (changed since transcription? does this work contradict a transcribed decision?), record the result in `## Status`, and let that edit ride in this commit — a single-phase plan has no sweep to carry the re-read, and running it after the commit checks decisions the commit already built on. Then: one commit per phase with a descriptive message that ties back to the plan / done criterion. Review and gate come first, and both are yours; a worker committing before anything checked its work is the ordering this step exists to prevent. Use `Fixes #N` if the phase closes an issue. If a handoff file `plans/handoffs/<slug>-<phase>.md` exists for the just-committed phase, delete it now — its lifetime ends at this commit (Mode 4 step 4 states the contract; this step is its enforcer).
5. **Write findings into the shard, then refresh the map** — on any multi-phase plan, before advancing, do BOTH, in this order, and do NOT skip either. *(A single-phase plan skips this step entirely — it has no shards to seal and no downstream to sweep; its upstream re-read, when Status declares one, already ran at step 4, before the commit.)*
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

     - **When the master declares an upstream document (an `**Upstream:` line in Status), re-read that document — every phase, not once.** Two questions: has it changed since transcription (update the `## Upstream decisions (transcribed)` section and say so), and does anything the just-shipped phase did or the NEXT phase will do contradict a transcribed decision (that is the operator's question, surfaced now rather than discovered at the next approval). The record line below closes with an `upstream:` segment beside `code:` when a declaration exists — `upstream: re-read, no conflict` is a claim you had to open the document to make, and the segment's ABSENCE is the same alarm as a missing `code:` segment.

     **(c) ends by writing a SWEEP RECORD into the master's Status — one line, naming EVERY unshipped shard and what happened to it, and closing with the code sweep:**

     ```
     Downstream sweep at p4 — p5 banner added · p6 banner added · p7 2 items corrected · p8 clean · p9 2 items moot · p10 clean · p11 1 item extended · code: pinned the x domain, checked p1's coverage gate + p2's clamp, gate obsoleted and removed · upstream: re-read, no conflict
     ```

     *(The `upstream:` segment appears only when the master declares an upstream document; a plan with no `**Upstream:` line owes no segment.)*

     The `code:` segment carries the same burden as the shard names: it says what this phase constrained and which earlier-phase guards you opened because of it. `code: nothing pinned` is a valid segment when the trigger didn't fire — its ABSENCE is what must never be.

     **This line is the enforcement, and it is why (c) cannot be skipped quietly.** (a) leaves a findings section behind and (b) leaves a status edit; a sweep that finds no drift leaves NOTHING, which makes "swept, all clean" and "never swept" indistinguishable at the next cold start — so the sub-step with no artifact is the one that silently rots. Naming every shard, including the clean ones, is the point: `p8 clean` is a claim you had to open p8 to make, and its absence from the line is visible forever. A sweep record that lists fewer shards than exist unshipped is an incomplete sweep, not a tidy one.

     **Verify before writing it, and never write it from memory or intention.** The record is a factual claim about files you opened this session. If you cannot name what you checked in each shard, you have not run (c) — run it, then write the line.

     A resuming session treats a missing sweep record for the last shipped phase as a **drift alarm**: the downstream shards may carry instructions that phase falsified, so run (c) retroactively for that phase before working the NEXT one. Do not assume it was done and left unrecorded. **A record present but carrying no `code:` segment is the same alarm, narrowed** — the shard half ran and the source half did not, so re-run only that half.

   This runs at EVERY phase commit, not only when a session handoff is known to be coming — the operator's decision to clear the session arrives after the commit, not before, so findings written only when a handoff is foreseen are missing exactly when they're needed. Skip only when the phase just committed was the plan's last (the plan ships instead).
6. **Advance** *(dispatching session)* — if any done criteria are still unmet, dispatch the next phase's worker. State a one-line status update ("Phase 2/4 done, starting phase 3") — this is a *status*, not a question. Never ask "should I continue?" — the approved plan is the standing authorization.

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
- **Every question put to the operator is asked in plain English: WHAT is being decided, WHY it matters, and what each answer will do — before any evidence.** No internal vocabulary in the ask itself — exit letters, team letters, auditor names, checker labels, bare phase ids. The operator should never have to decode this skill's machinery to understand a question, and a question they interpret differently than intended produces an approval that authorizes something they never chose — that is how a six-phase retrofit was approved as "three structs stay" (2026-08-12). Verbatim quotes and citations attach BENEATH the ask as evidence, never as the ask. This governs every operator-facing question this skill produces: the approval summary's closing decisions, step 8's forced binaries, and mid-phase Scope Check questions alike.
- **Be ruthless about non-goals.** If you're unsure whether to list something as a non-goal, list it. Easier to remove than to add mid-work.
- **Archive, don't delete.** Shipped plans move to `plans/shipped/` — they're a record of what got done, not garbage to collect.
- **Anchor on symbols, not line numbers.** Plans and specs with more than one phase must use symbol names (functions, types, properties) and distinctive code snippets as their primary anchors — every committed phase shifts the line numbers the next phase's notes cite, so raw `:NNN` references rot by design. Line numbers are allowed only as secondary hints tagged with the commit they were measured at, and the cold-start refresh (Phase Completion Cycle step 5) restates them when they drift. A fresh session re-anchors by grepping the symbol, never by trusting a stale line.
- **A resumed plan is a hypothesis, not a license.** Validate per Mode 2 step 2 before working any pre-existing plan; if scope or approach has materially diverged, re-run Mode 1 step 4 EXPLORE and rewrite — never patch a stale plan turn-by-turn while coding. The re-plan triggers (including the smaller-diff approach switch) are enumerated in Mode 2 step 2 — that list is the rule.
- **EPCC: Explore is unconditional.** EPCC = Explore → Plan → Code → Commit. The Explore step (Mode 1 step 4) runs every time, before a single line of plan text gets drafted. All three teams — change impact (A), adversarial code read (B), implementation specialists (C) — run regardless of plan size, file count, or category. There is no "small plan" exception, no "I already read the files" escape, no `--no-research` opt-out, no "pure docs/config" carve-out. **The one conditional part is Team A's skip, and its trigger is observable, not self-certified:** does this plan modify code that already exists? Read the Work list. "It's basically greenfield" is not the test. If a task is genuinely too trivial to warrant exploration, it's too trivial to warrant `/plan` in the first place — do it directly. This rule governs whether the teams RUN, never their headcount — presence is unconditional; scale is step 4's agent-count table plus its single-phase floor.
- **No plan is presented for approval without its APPROVAL SUMMARY — the complete section-by-section enumeration of the master and every shard, in chat.** Format: the Approval summary section below. It fires on the same trigger as the read-back below and for the same reason — wherever plan text is minted or materially rewritten and an approval is then asked for: Mode 1 step 8, the Mode 2 re-plan and legacy-reshape paths, and an approval-time restructure large enough to re-open the ask. **Enumerate, never précis; length is expected; empty sections are reported as empty; and a plan that deletes or replaces anything also lists its SURVIVORS.** The failure this prevents is not a bad plan — it is a *good* plan approved unread, which converts every upstream gate into ceremony at the one step where a human was supposed to decide. Handing over file paths is not presenting a plan.
- **Every written plan gets an adversarial read-back before the operator sees it — ONE dispatch, ONE fix pass.** Mode 1 step 7 — three read-only agents on disjoint axes (grounding, executability, coherence), briefed verbatim to search as though the plan is wrong, and required to verify by opening sources rather than by reasoning. **The coherence axis exists because grounding and executability both pass a plan that contradicts itself:** grounding checks claims against the code, executability checks shards against each other, and neither reads a stated rule against the mechanism the same plan specifies. The same axis catches the characterization whose citation resolves but whose description is false — a named row described as living on a card it never appears on, correct `file:line` and all. No "small plan" exception, no "I verified as I drafted" escape (the drafting session grading its own grounding is the thing being checked). Findings BLOCK. There is no second round; what you cannot close cleanly is promoted to the operator instead. The record names every fix, not just a count — that naming is what replaces the re-read. Fires wherever plan text is minted or materially rewritten — Mode 1, the Mode 2 re-plan and legacy-reshape paths, and an approval-time restructure — never only on the first draft.
- **No research deferrals — verify or block.** Every cited file path, API name, metadata key, version number, framework behavior, or external system claim is verified this session and stated as fact with a file:line or URL+section citation — or the plan isn't drafted. There is no `TODO: verify` channel, no "confirm during implementation," no placeholder, no flag opt-out, no carve-out (same unconditional standard as EPCC). If a claim can't be closed by research, STOP and resolve it with the operator before drafting (provide access, run it, or pull it from scope). The guess/fact line is enforced by absence: every claim in a drafted plan is verified — not "unmarked," verified.

  **🛑 A NEGATIVE existence claim is a claim, and it is the one this rule leaks on, because it leaves nothing to cite.** "No shipped surface does X", "there is no existing Y", "nothing else calls Z", "this is the first place that does W" — every one of those is a fact, closable only by a SEARCH run this session, and it is stated with the search that closed it (the grep, and the project's own surface index where one exists) exactly as a positive claim is stated with its `file:line`. Two things make it leak. It usually appears in a **Non-goal or a Background note**, where it reads as scope rather than as an assertion — and the drafting session writes it from memory precisely because there was nothing to look at. **The exclusion rests on it**: a wrong negative deletes work the plan should have contained, or invents work that already exists. Where the project keeps an index of its own surfaces, a negative is closable as that index checked AND a grep run — never either alone. *(Origin, 2026-08-11: a plan's Non-goal asserted "no shipped surface in this app draws them" of a background treatment that had been shipping for months, and scoped a whole Work item around writing a second one. Every auditor passed over it — the grounding auditor because its brief told it to skip Non-goals, since those "state intent, not fact." The operator caught it. Both halves are now fixed: this rule, and the carve-out in the grounding auditor's brief below.)*

  Empirical unknowns that genuinely need runtime are not deferrals — but **needing runtime never licenses carrying a fork into execution.** An unknown whose answer would change the phase map is a stage-zero fork (Mode 1 step 4): closed by reading where reading closes it, put to the operator where it cannot be. Only what survives that becomes the Diagnosis falsifiable test or the algorithmic load-test, each of which keeps its OWN placement rule — the Diagnosis test runs before any shard's Work is scoped, the load-test lands at the earliest practical phase — and neither is a fork this step settles. **A phase whose done signal is "a decision is recorded" rather than "something works" is the tell that this was skipped** — every phase after it was drafted against an assumed answer, and the research that was supposed to find what the choice breaks ran before the choice existed.
- **Generic-skill discipline.** This skill is global — it ships across every project regardless of language or framework. Skill text MUST NOT hardcode paths, language conventions, or specific framework names beyond illustrative examples. Each agent's brief describes the *shape* of what to look for; the agent figures out where this project's equivalent lives (e.g., `node_modules/<dep>` for JS, `~/.cargo/registry/src/` for Rust, `site-packages` for Python, vendored docs folders for any project, the vendor's official docs site via WebFetch for any language). If you find yourself writing a project-specific path or framework name in the skill body, replace it with the generic shape and an illustrative example list.
- **Mid-implementation pivot rule.** If the first diagnostic experiment under an approved plan disproves the hypothesis (e.g. "I disabled X and the symptom didn't change"), STOP. Don't try a second guess. Return to Mode 1 step 4 (Explore) with the new evidence as a sharper question — the plan was scoped at the wrong target and patching it forward will compound the error. Two failed disable-experiments back-to-back is a hard signal to re-explore; if the symptom is genuinely opaque after that, hand off to a dedicated diagnosis pass (e.g. a `/diagnose` skill if your setup has one).
- **New SOURCE file mirrors an existing SOURCE file? Refactor first by default. Code only — this rule does NOT apply to markdown, docs, skill definitions, prompt templates, or config.** When the plan adds a new source file the description says "mirrors" / "like" / "similar to" / "same family as" an existing one — OR a sibling file with the same suffix already exists in the target directory — the reuse-specialist agent is mandatory during Explore and its Phase-0-refactor recommendation is presumed correct unless the user explicitly overrides at plan approval. The refactor becomes phase 0 of the plan; the new feature is phase 1+. Copy-and-defer requires an explicit user override at approval, recorded in the Parking lot in writing — not a passive default that quietly leaves duplication for a review pass to surface after the duplicate ships.
- **Algorithmic plans: land the research load-test at the earliest practical phase, not "whenever it's convenient."** The minimum executable test that would catch a naive implementation (research's question 3) is the falsifiable claim that proves the research is grounded. The default placement is phase 1's first commit, *before* the rest of phase 1 — the test runs against the simplest possible implementation and gates further work. If the test genuinely cannot be run until phase 2 (e.g. it needs integration plumbing that doesn't exist yet, or the LLM pipeline only behaves under realistic load), that's allowed, but the plan must explicitly call out the gap and the test still becomes the *first thing* in phase 2, not buried mid-phase. If the test fails when it lands, the research was incomplete — return to Mode 1 step 4 (Explore) with the specific failure mode as a sharper question, don't paper over it with tuning. This catches "research was insufficient" at phase 1-2 instead of phase 3+.

- **UI plans: a visual capture is a MANDATORY Done criterion on every phase that changes what renders.** The trigger is observable — the phase's `## Work` names a file that draws UI — not a judgment about whether the change is "visual enough". The criterion names the state to capture, not just "screenshot it": which screen, which data condition, and what the capture is supposed to prove.

  **This rule re-fires every time `## Work` CHANGES, not only when the plan is drafted.** A phase acquires UI files mid-flight — through the Scope Check's add path, through a review finding that lands in a view. Each of those re-runs the trigger against the new Work list, and each can add a capture criterion to a phase already in progress. Read as a drafting-time check only, this rule passes a phase whose Done criteria describe the change it was scoped for rather than the change it is shipping.

  **A capture is evidence only if it POST-DATES the last edit to the surface it shows.** Before presenting any capture, check it against the commits and edits that landed after it was taken; if the surface moved, the image is a picture of something that no longer exists and presenting it is worse than presenting nothing, because it reads as verification. Re-capture, don't caption around it.

**When the change alters how LONG a rendered string can get, the criterion must also name a stressed text size** — the largest ordinary size and one accessibility size, captured, not reasoned about. Text that grew is text that can now wrap, truncate, or push its neighbours out of a row that fitted before, and no test in any suite sees it. Trigger is observable: a value's format changed (a count became a duration, a number gained a unit, an abbreviation became a word), or a label was added to a fixed-width row.

**A phase that changes what a user sees, and whose Done criteria can all be satisfied by a green test suite, is incomplete — send it back before presenting the plan.** Where the project's own instructions already demand screenshot evidence, this rule makes the plan carry it as an exit condition rather than leaving it to be remembered at commit time.

- **NO phase's Done criteria — UI or not — may be FULLY satisfiable by a passing suite plus greps. Every phase names at least one observable.** A rendered state, a measured value, a produced artifact, a behavior under a named degraded condition — something the phase must PRODUCE and check, not only keep green. The UI-capture rule above is the screen-shaped case of this; the rule itself has no UI trigger. The incident behind it went 4,795 tests green with 25 verified defects present, several user-visible.

- **Perf/bug plans: run the Diagnosis falsifiable test BEFORE scoping any shard's Work.** Protocol per the Diagnosis commentary in the Plan templates section below (confirmed → scope normally; disproved → back to Mode 1 step 4 with the negative result as the sharper question). Files-read alone doesn't ground a diagnosis; "I commented out X and the symptom didn't change" does. Escalation after repeated failed experiments is the Mid-implementation pivot rule above.
- **Justify non-goal exclusions across peer sets.** Every peer-set exclusion needs the one-sentence "why this asymmetry is safe" rationale per the Non-goals commentary in the Plan templates section below — if you can't write it, fold the excluded items in. The exclusion-safety specialist (Mode 1 step 4) surfaces this as a forced binary decision at approval; trust its default-include recommendation unless you have an iron-clad invariant.
- **User-facing decisions need a recorded sign-off — never inherit them as "locked."** A decision that changes what the user sees or how the feature behaves (show vs hide a value, a default, a copy change, a state that disappears) does NOT become a `## Locked decisions` entry just because a divergent-design master, a spec, or a prior session wrote it down. Unless the plan can cite an explicit operator sign-off — a "chosen: X" record carried from a divergent-design pass, or an approval in the plan's Status — surface it at plan approval (step 8) as a forced binary decision the user must make, same treatment as the reuse and exclusion specialists; don't soften it. Inheriting a user-facing call as settled is how a hidden behavior flip ships without the operator ever choosing it.

  **Shipped code is an inheritance channel too, and it is the one that hides best.** A threshold, default, or cut-off the plan CARRIES FORWARD is a user-facing decision on exactly the same terms as one the plan invents — the operator never chose it either. The tell is the reassuring phrasing: "preserves current behavior", "keeps the existing threshold", "translates the old gate to the new units". That sentence describes *fidelity to the old value*, which is not an argument that the value is right, and it is the form under which an undefended number gets silently re-ratified by an approval.

  So whenever a plan restates a magic number, a visibility gate, or a cut-off from existing code, it states in one line **who chose it and on what evidence.** No answer → it is unowned, and it goes to the operator at step 8 as a forced binary decision like any other. Untouched code needs no such audit; this fires only on values the plan is actively rewriting, where the cost of asking is one sentence.

- **Every plan is drafted as though a DIFFERENT session executes it. Unconditional, both shapes.** Not "when the plan looks long", not "for multi-phase plans", not "if a clear is expected" — the assumption is standing, and it is not a prediction about session boundaries. It is a fact about who does the work: every phase is implemented by a fresh worker holding one pasted shard and none of the conversation that produced it, and that is as true of a single-phase plan written and executed inside one hour as of a six-phase plan spanning a week. So a fact settled in conversation is written into the plan or **it does not exist** — there is no "we discussed it" channel. The test is the same for both shapes and is applied at drafting and at every refresh: *could a fresh session execute this from this text alone?* In a multi-phase plan the unit under test is the shard; in a single-phase plan it is `## Status` plus `## Work`. A rule gated on "will this span sessions?" is a rule a session can self-certify out of right up until the moment it matters, which is why this one has no gate.
- **Approved interface lines are CONTRACT, and a shipped interface that differs from them is a re-plan trigger.** Every phase's `## Work` carries `- Consumes:` / `- Produces:` bullets — mandatory with a literal `none` as a valid value, absence the only invalid state — per the Interface-lines commentary in the Plan templates section below. What the operator approved was those signatures and types, so shipping different ones is an approach switch, and it fires the Mode 2 re-plan trigger at that phase exactly like any other. The Phase Completion Cycle's step-1c spec check is what detects it; the downstream sweep may notice it too. **Neither one resolves it by editing the lines.** Rewriting the approved interface to match what shipped converts an escalation into bookkeeping and silently re-ratifies a design decision the operator never saw — the same failure shape as inheriting a user-facing value because the code already had it.
- **The task tier is `### Task N` headings inside `## Work`, with DISJOINT file sets.** A phase whose Work splits into more than one job names each with a `### Task N — <one-line scope>` heading inside its `## Work` section. Three rules, none negotiable: **(1)** each task's file set is disjoint from every other's — a file appears under exactly one task, and a phase whose work cannot be split that way is formally unsplittable, so leave it untiered rather than listing a file twice; **(2)** file-path-leading dash bullets stay the ONLY path carriers — a numbered list is invisible to a path extractor, so files listed that way disappear from every drift check that reads the Work list; **(3)** no per-task Done criteria — criteria stay phase-level, because a task is a unit of scope attribution and not a unit of exit. Tasks are consumed in order by ONE worker and are not commit units: the phase is still one commit. The tier has THREE mechanical consequences, not one. It routes the spec check — more than one task dispatches an independent reviewer, exactly one runs in the dispatching session. It is audited at plan time — the executability auditor names every file appearing under two or more tasks of a phase and reports the task count per tiered phase. And it is re-checked at the write step's mechanical self-check — per-shard task disjointness is on that list. So a tier is structure that three separate gates read, never a presentational device: adding one to a phase that does not need it manufactures work for all three, which is why a lone `### Task 1` is banned rather than merely discouraged.

---

---

## Optional enforcement

Everything above is stated as the session's OWN obligation, because a public clu install ships this file alone and enforces none of it mechanically. Where the operator's full /plan skill is installed, two checks pick up part of the load: `~/.claude/skills/plan/scripts/plan-check.sh` runs the mechanical artifact-shape set (the write-step self-check, Mode 2's staleness diff, the Mode 3/5 `--archive-move` sweep) — it has no PASS state, always exits 0, and ends by naming the judgment obligations that stay yours; and a dispatch-gate hook denies plan-marked agent dispatches that use `Explore`/`Plan`, and denies research briefs missing the verbatim boilerplate fragments. This fork keeps every marker and fragment those checks key on — the `Approval:` markers, the `Plan slug:` / `Plan audit:` / `Plan work:` brief openers, the verbatim fragments — inert without the hooks, recognized wherever they exist.

🪦 The **probe fleet** — the stage-zero comparison prober, the step-7 dry-run prober, Mode 2's phase-start re-probe, and the new-mechanism scoped re-probe — was **REMOVED 2026-08-18 at the operator's direction**: what a rehearsal probe finds (missing Work files, sketches that don't compile), the phase's real worker finds and fixes in the same session, and the operator judged the spend not worth pre-discovering it. Do not resurrect probes from git history and do not re-add worktree probe dispatches to any step. Design forks that once went to a comparison probe now go to the operator (Mode 1 step 4); a structural talk-down with no citable blocker now goes to the operator at approval.

🪦 A **plan-draft gate** — a machine-wide write-freeze on any repo holding an unapproved plan — was **REMOVED 2026-08-13 at the operator's direction**: it kept blocking other sessions' work while one session drafted a plan, and the operator judged it to have no value. Do not resurrect it. The rule it enforced — no code before the operator approves the plan, "not even scaffolding" — is unchanged and holds on discipline like the rest of this skill.

**🛑 A checkout where the optional checks are not registered is not enforcing them, and nothing says so at the time.** They live in machine state rather than repo state, so a fresh clone silently reverts every one of them to prose the model has to remember. Assume that is the case here unless you have checked.

---

## Research team briefs

Loaded by `/plan` Mode 1 step 4. **Pass these verbatim.** Paraphrasing them into one generic "research this area" is the degradation the team split exists to prevent — a generalist mentions the load-bearing detail in passing, consolidation buries it, and the bug surfaces three phases later.

Every agent is `subagent_type: "general-purpose"`. Never `Explore` — it omits CLAUDE.md and there is no setting that opts it back in, so an Explore agent researches without the rules that make its research correct.

**One narrow exception, and where the full /plan skill's dispatch gate is registered it enforces the rule around it.** Mode 1 step 4 above permits `Explore` for a pure locate-this-symbol sweep where losing the standing rules costs nothing. None of the briefs below is such a sweep, so none of them may use it. A dispatch that genuinely is one carries this sentence verbatim at the start of a line, and the sentence is an on-record claim about that dispatch:

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
A code comment is a claim by a past author, not evidence. Ground every finding
in what the code does; cite a comment only as intent, and a comment the code
contradicts is itself a finding.
```

---

### Team A — CHANGE IMPACT

**Fires when the plan modifies code that already exists.** Skipped only for plans that create new code and modify none. Read the Work list to decide; "it's basically greenfield" is not the test.

**Neutral brief rule applies.** A-team agents get the operator's goal in the operator's words and the files in play — **never the approach under consideration.** "We're adding a helper to X" hands the agent your assumption and it will come back agreeing with you.

#### A1 — fan-in and observable behavior

```
Map who depends on {files/symbols in play}, and how far the dependency reaches.

If this session has code-graph tools (e.g. the GitNexus MCP tools — impact,
trace, query; load them if they are deferred), use them FIRST to enumerate the
callers and transitive dependents mechanically: a graph walk returns the full
fan-in where a grep walk samples it. Then open the code at every hit you
report — the graph is an INDEX, not evidence. A finding cites the file:line
you read, never the query result; and a symbol the graph does not know is
checked by grep before you report it as uncalled, because a miss in one index
is evidence about that index, nothing more. No graph tools or no index for
this repo → grep as ever.

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

If this session has code-graph tools (e.g. the GitNexus MCP tools — impact,
trace, query; load them if they are deferred), use them FIRST to enumerate the
readers and writers of the state in play before opening anything. Then open
every site you report — the graph is an INDEX, not evidence: each coupling
cites the file:line you read, never the query result, and a site the graph
misses is checked by grep before you call the list complete. No graph tools or
no index for this repo → grep as ever.

- Every other reader and writer of the same state, queue, cache, file, or
  external resource.
- Ordering contracts: what breaks if this runs earlier, later, twice, not at
  all, or concurrently with its neighbours? Walk realistic sequences, not just
  the happy path.
- Where does this code's correctness depend on something else having already
  run? Is that dependency enforced, or is it just true today?

Cite file:line for each coupling. Rank by how silent the failure would be.
```

---

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

---

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

---

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

Briefs 1-3 are loaded by `/plan` Mode 1 step 7 (the adversarial read-back of a written plan); brief 4 is loaded by the Phase Completion Cycle's step 1c, at a phase gate, and audits a DIFF rather than a plan. **Pass these verbatim.** Paraphrasing them into one generic "review this" is the degradation their specificity exists to prevent — the axes are disjoint and none can answer another's question.

Every agent is `subagent_type: "general-purpose"`. **Never `Explore` here** — the grounding auditor's entire job is checking claims against sources under the verify-or-block rule, and Explore omits CLAUDE.md, so an Explore grounding auditor audits without the rule it is auditing against.

**Each brief opens with `Plan audit: <slug>.` on its own line, and that line is load-bearing.** Where the full /plan skill's dispatch gate is registered, it keys on that line to apply the `Explore`/`Plan` ban to audit dispatches; without it the gate cannot see this step at all. It is a *separate* marker from step 4's `Plan slug:` on purpose — an audit dispatch is held to the agent-type rule and nothing else, because the research invariants are research-shaped (an auditor recommends nothing, so the effort-objection ban is meaningless to it).

---

### 1. Grounding auditor

Brief: "Plan audit: <slug>.
Read `plans/<slug>.md` and every `plans/<slug>-<phase>.md`. Extract every EXISTENCE or BEHAVIOR claim about the codebase or an external system — file paths, symbol names, signatures, API names, metadata keys, version numbers, quoted behavior, `file:line` citations — and check each against the actual source this session. (Goals and non-goals state intent, not fact — skip them. **ONE exception, and it is why that sentence is qualified: a NEGATIVE EXISTENCE claim is a fact wherever it sits, including inside a Non-goal. 'No shipped surface does X', 'there is no existing Y', 'nothing else calls Z', 'this is the first place that does W' — check every one by SEARCH, this session, and report an unsearched negative as a finding rather than reading it as scope. These are load-bearing precisely BECAUSE they appear in a Non-goal: the exclusion rests on them, so a wrong one deletes work the plan should have contained, and it is the one kind of claim that leaves no citation to check because there is nothing to cite. Where the project keeps an index of its own surfaces, a negative is closable only as that index checked AND a grep run — never from either alone, and never from the drafting session's memory.**) **Interface lines carry one more exemption, on exactly the same footing.** A symbol named on a `- Produces:` line — this shard's or an earlier phase's — is a DECLARED FUTURE symbol: the plan is saying it does not exist yet, so its absence from the code is not a finding and you do not check it; what grounds a `Produces:` line is the work-shape sketch beside it, not the codebase. A `- Consumes:` line is the opposite claim — that the symbol exists NOW — so check every one against current source. A `Consumes:` symbol you cannot find in current code AND cannot match to a `- Produces:` line of an earlier task in the same phase or an earlier phase IS a finding: it is a call into something nothing in this plan builds, and it reads as grounded precisely because the line beside it looks the same. **One more claim class, and it is the one a Work bullet hides best: PLACEMENT.** For every `## Work` bullet that puts a NEW view, card, renderer or section INTO a named existing file, open that file and verify it can actually reach the data the bullet says it renders. A bullet reads as grounded because the path resolves and the file exists — but a renderer placed where its inputs are unreachable is not buildable, and nothing else in this pass looks. Report the file the bullet named, the data the bullet requires, and the file that actually owns that data. *(Origin: a phase said "the card becomes the first section" of a file holding only a cache handle and a range, while every value the card renders lived on a view model owned two files away. The path was real, the file was real, the sentence was false, and it cost two worker dispatches to find out.)*

**Search as though at least one claim does not resolve — reporting zero unresolved is a valid result, but only alongside the evidence below.** Report a table: claim · where in the plan · resolves? (yes / no / partially — and what the source actually says). Quote the CURRENT source text verbatim at three or more cited locations, so the report proves you opened the files rather than echoing the plan back. Separately list (i) every claim you could NOT check and why — external system you cannot reach, doc not present locally; those are unverified, not verified, and they are findings, not footnotes. **A claim whose source you did not actually open goes here regardless of how plausible it reads — plausibility is not a resolution, and quoting the plan's own citation back at me is not opening anything;** and (ii) every existence-or-behavior claim carrying no citation at all. Also flag any `TODO: verify` / 'confirm during implementation' / hedged phrasing ('should be', 'presumably', 'I believe'), any work-shape sketch using a symbol you cannot find — EXCEPT one the plan declares on a `- Produces:` line, which the exemption above covers and which is exactly where a declared-future symbol appears, so do not re-flag it here — and — in the Failure modes section specifically — TWO things. First, any entry whose *antecedent* is statically checkable ('if this function branches on that flag…', 'if that endpoint doesn't return X…'), which is an unlooked-up fact wearing risk's clothing rather than a real risk. Second, and do not skip this because the sentence is phrased as a warning: **any flat ASSERTION about how a framework or external API behaves — 'passing X causes Y', 'this modifier re-bins/discards/overwrites Z' — is a behavior claim and gets the same doc-quote-or-source-check treatment as a claim anywhere else in the plan.** A failure-mode bullet reads as hypothetical and therefore slips past unchallenged, but the plan's Work is often scoped AROUND it, so a wrong one steers the whole phase. If the archive or the repo's own working usage does not confirm it, report it — being wrong in the opposite direction to the truth is the case that costs most. A code comment is a claim by a past author, not evidence: a plan claim is not resolved by a comment agreeing with it — resolve against what the code does, and report a comment the code contradicts as a finding in its own right. You are NOT to invoke the `/plan` skill and NOT to edit any file. End with counts — e.g. 'checked 14 claims, 12 resolve, 2 do not, 1 uncheckable, 1 uncited.' Keep prose under 400 words; the claims table and source quotes do not count toward that."

*(The incident behind that clause: [RATIONALE.md](RATIONALE.md#the-grounding-auditors-behaviour-claim-clause) — it sits OUTSIDE the quoted brief on purpose; inside, it ships to the agent.)*

### 2. Executability auditor

Brief: "Plan audit: <slug>.
Read the master `plans/<slug>.md` and every shard. (If the plan is a single file with no shards, read it alone and answer (a), (e), (f), (g), (h) only — a single-phase plan still carries interface lines and a task tier.) **Search as though at least one done criterion is covered by no phase and at least one shard cannot be executed standalone — reporting zero is valid only with the per-item accounting below.** Answer each as a list, not prose: (a) COVERAGE — for every master-level and per-shard Done criterion, name which shard's Work satisfies it. Name any criterion nothing covers, and any Work item no criterion justifies. **Plus one per-criterion check: any criterion asserting that two things AGREE — same value, same verdict, matching totals, "captured beside X and agreeing with it" — must ALSO assert that each side is PRESENT, or it passes vacuously when both are missing. Name every agreement criterion carrying no separate presence assertion for both operands.** A phase once ran its capture pass against "the header's stat, captured beside the card, agreeing with it" and passed while the card rendered no verdict and the header rendered no stat: nothing absent can disagree. (b) SET MISMATCH — files in the master's `## Files touched (overview)` appearing in no shard's Work, and files in a shard's Work missing from that overview. (c) ORDERING — does any phase's enter-gate depend on an output a LATER phase produces? Does any shard reference a decision or artifact from a phase that runs after it? (d) SELF-SUFFICIENCY — for each shard, using ONLY that shard's own text, list every referent it needs in order to EXECUTE but neither defines nor names a source for: inputs, outputs, call sites, delegated behavior, symbols. A pointer that names where the thing lives ('see the master', 'settled in phase 2') is not a gap — an unsourced referent is. Name the items. (e) EXCLUSIONS — does every Non-goal excluding some members of a peer set carry its one-sentence rationale for why the asymmetry is safe? (f) INHERITED DECISIONS — does the plan record as already-settled (in `## Locked decisions`, or anywhere it treats a choice as made) any decision that changes what the user sees or how the feature behaves — a default, a shown/hidden value, copy, an observable state — WITHOUT citing an explicit operator sign-off? Name each. (g) INTERFACES — the shards' contracts read against each other, in both directions. Every phase's `## Work` carries `- Consumes:` and `- Produces:` dash bullets (per task where the phase has a `### Task` tier); a literal `none` is a valid value and pairs with nothing. FORWARD: for every `- Consumes:` entry, find the `- Produces:` entry it pairs with — an EARLIER task of the same phase, or an EARLIER phase's shard. Quote both lines when it matches. An entry matching neither is UNPAIRED: report it as such and stop there — **do NOT open source to decide whether it exists in current code.** That is the grounding auditor's axis, it is checking exactly this line under its own carve-out, and two agents reporting one symbol is how a duplicate finding gets counted as two. BACKWARD: for every `- Produces:` entry, name each LATER shard whose `- Consumes:` claims that product and quote the two lines side by side. That repetition is deliberate — a worker's whole world is one pasted shard — and this pairing is the only thing checking it, so a drifted copy (renamed type, changed argument, dropped return, different arity) is a finding and you report it by quoting both, never by deciding which one is right. (h) TASK DISJOINTNESS — for every phase whose `## Work` carries more than one `### Task`, the file set under each task must be disjoint from every sibling task's. Name every file appearing under two or more tasks of the same phase, and name the tasks. Report the number of tasks you checked per tiered phase. You are NOT to invoke the `/plan` skill and NOT to edit any file. End with counts — e.g. 'checked 6 done criteria across 4 shards, 9 interface entries, 7 tasks in 3 tiered phases.' Report in under 400 words."

### 3. Coherence auditor

Brief: "Plan audit: <slug>.
Read the master `plans/<slug>.md` and every shard. **You are NOT checking the plan against the codebase — another auditor does that, and you should not open a source file at all.** You are checking the plan against ITSELF. Your question is: which two parts of this document set cannot both be true? **Search as though the plan contradicts itself at least once — reporting zero is valid only with the accounting below.** Report as a list, each entry naming BOTH locations and quoting both: (a) SUMMARY VS MECHANISM — a `## Locked decisions` entry, a Status sign-off item, a Goal line, or any prose statement of a rule, whose scope is wider or narrower than the behaviour the Work section's own steps or work-shape sketch actually produce. Walk each stated rule against the sketch step by step; a rule that says 'X always resolves to Y' against a sketch whose branch order reaches Y only sometimes is the archetype. (b) UNREACHABLE OUTCOME — a Done criterion or Goal the Work as written cannot produce. (c) SELF-VIOLATING SCOPE — a Work item that does the thing a Non-goal excludes. (d) SPLIT FACT — the same fact stated in two places with different content (master vs shard, or shard vs shard), including line hints and counts. (e) UNVERIFIED CHARACTERIZATION — any sentence describing what the code or product DOES, as opposed to where something lives: 'the X card shows Y', 'this only fires when Z', 'users see this on the W screen'. Flag EVERY one, **even when its citation resolves perfectly** — a correct `file:line` proves a symbol exists, never that a description of behaviour is accurate, and that gap is why this item exists. (f) UPSTREAM REVERSAL — when the master carries a `## Upstream decisions (transcribed)` section, walk every entry in it against the plan's Locked decisions, Work items, and Files-touched dispositions, and report anything that cannot be true beside it. Include the per-file test: a file that gains new capability in one phase and is deleted in a later one contradicts any transcribed rule of the shape 'nothing is rewired on the way to being deleted'. Quote both halves verbatim — the transcribed entry with its provenance, and the plan text that reverses it. The transcribed section IS plan text, so this stays inside your no-source-files contract; if the master has no such section, report 'no upstream section' and do NOT go looking for an upstream document. You are NOT to invoke the `/plan` skill and NOT to edit any file. End with counts — e.g. 'checked 11 stated rules against their mechanisms, 4 characterizations, 6 cross-file restatements, 3 upstream entries.' Report in under 400 words; quoted pairs do not count toward that."

### 4. Spec reviewer

Dispatched by the Phase Completion Cycle's **step 1c**, not by step 7, and only when the phase's `## Work` carries MORE THAN ONE `### Task` (exactly one task → the dispatching session runs the pass itself). Read-only; no `isolation`. It compares a diff against a spec, which is mechanical work — **a cheaper or faster model is a legitimate choice here**, and it is the fix for the documented failure mode of spec-compliance reviewers being slow and over-broad.

**Every input is PASTED into the prompt: the shard's text, the worker's report, and the exact diff.** Never a file path and never "read the plan" — the plan files may be untracked, the diff is already in the dispatching session's hands, and an unscoped reviewer told to go find the change searches a whole codebase for half an hour to reconstruct what a paste would have handed it.

Brief: "Plan audit: <slug>.
You are checking ONE thing: does the diff below implement the phase's Work section, and only it? Everything you need is pasted into this prompt — the phase text, the worker's report, and the complete diff. **Do NOT open, read, search, or list any file, and do not run any command.** If something is not in this prompt, it is out of scope for you; say so rather than going to look for it. Answer on exactly two axes, each as a list, not prose. **(1) WORK COVERAGE, in both directions.** FORWARD: for every item in the phase's `## Work` — every file bullet under every `### Task` — name the diff hunk that evidences it, quoting a line or two of the diff. An item you cannot evidence is UNBUILT; name it. Where the phase has more than one task, attribute each changed file to the ONE task whose file set contains it — the task file sets are disjoint by construction, so a file you cannot attribute to exactly one task is itself a finding (name it as UNATTRIBUTED, and say whether it is claimed by zero tasks or by two). BACKWARD: for every file the diff changes, name the Work item that claims it. **A changed file no Work item names is UNCLAIMED — report every one, this is the direction that catches the edit nobody declared,** and do not excuse it because the change looks small, tidy, or obviously correct. **(2) INTERFACE CONFORMANCE.** For each task's `- Consumes:` and `- Produces:` lines, compare the signatures and types named there against what the diff actually ships. Report each as CONFORMS or MISMATCH, and for a MISMATCH quote the approved line and the shipped signature side by side. A `none` line means the task claims to create/call nothing new — a diff that ships a new public symbol against a `Produces: none` is a MISMATCH. **You do NOT report on any of the following, and a report containing them is wrong regardless of how good the observation is:** code quality, style, naming, performance, test coverage, bugs, or anything you would raise in a code review — a separate gate owns all of that; and whether the phase's Done criteria are met — a different pass owns those, and two records claiming that ground is how they come to disagree. Do not edit anything and do not invoke the `/plan` skill. **End with counts, in this exact line, and nothing else on the line:** `**Spec check at <phase id>** — tasks N/N evidenced · interfaces conform|N mismatch · none uncovered|N uncovered`. (Single-phase plan: `**Spec check** — …`, with no phase id.) A pass that cannot name what it checked did not run. Report in under 400 words; quoted diff and interface pairs do not count toward that. The phase text follows, then the worker's report, then the diff: {shard_text} / {worker_report} / {diff}"

---

## Approval summary

Emitted by `/plan` Mode 1 step 8, in chat, AFTER every file is written and step 7's
verification has run — never before, because a summary of text that is still being
corrected describes a plan that does not exist yet.

**It is the operator's only realistic read of the plan.** A six-phase plan is tens of
thousands of words across seven files; nobody holds that in their head, and an operator
who cannot see what is going in approves on trust. That is not review. This artifact is
what makes step 9's approval a decision rather than a formality.

### The one rule

**ENUMERATE, never précis.** Every locked decision gets its own bullet. Every non-goal
gets its own bullet. Every file gets its own bullet. Every done criterion gets its own
bullet. A summary that merges three decisions into "the phase settles the rendering
approach" has destroyed exactly the information the operator needs, and it reads
*better* than the correct version, which is why this rule is stated first.

Compress the PROSE around each item to one line. Never compress the LIST.

**Length is expected and is not a defect.** Do not trade completeness for brevity here,
do not offer a "short version", and do not stop early and offer to continue — emit the
whole thing in one message. If it feels too long, that is the plan being large, and
the operator learning that at approval time is the entire point. (Operator, 2026-08-12:
*"your plans are too huge for me to read and grep all of in my head so I need a COMPLETE
bulleted summary of the master and each phase after they're all written."*)

### Derive it by READING, not by remembering

Open each file and walk its sections in order. You wrote these files, which is exactly
why you must not summarize them from memory: the drafting session's recollection of
what it intended is the thing least likely to notice what it actually wrote. An item
you cannot find in the file does not go in the summary; an item in the file that you
would rather not mention goes in anyway.

**An empty section is reported as empty, explicitly** — `Non-goals: none` — never
omitted. Absence is the hardest thing to see in a long document and the most expensive
thing to miss.

### Shape

Lead with the counts, so the operator knows the size before reading:

```
Plan: <slug> — N phases, M files touched (X new, Y modified, Z deleted), K done criteria.
```

#### Then the master, section by section

- **Goal** — one line, quoted from the file.
- **Phase map** — one bullet per phase: `p<id> — <title>. Ships: <what>. Deletes: <what, or "nothing">.`
- **Non-goals** — one bullet each, with its stated reason compressed to a clause. These
  are exclusions, and an exclusion the operator disagrees with is the cheapest possible
  thing to catch here and the most expensive anywhere later.
- **Files touched** — grouped `New` / `Modified` / `Deleted`, one bullet per file, each
  naming its owning phase. Deletions are listed LAST and never folded into "modified".
  **A file that appears in BOTH a work grouping and the deletion list gets the flag
  `⚠️ worked, then deleted` on its bullet, with the plan's one-line reason work lands in
  a file on its way out.** plan-check prints these as `doomed-file` lines and nothing
  silences them; the summary carries every one, because work-into-a-doomed-file is the
  signature of a retrofit wearing a staged migration's clothing, and the operator — not
  the drafting session — is the reader who decides which one this is.
- **Background findings** — one bullet each, the finding only.
- **Done criteria** (plan level) — one bullet each, verbatim or near.
- **Parking lot** — one bullet each.

#### Then each shard, in phase order, section by section

- **Locked decisions** — one bullet per decision, numbered as the shard numbers them.
  Never merge two. This is the densest real content in any plan and the operator has
  approved none of it before now.
- **Work** — per task where the phase is tiered: the task's one-line scope, then one
  bullet per file with what happens to it.
- **Interfaces** — the `Consumes:` / `Produces:` lines per task, as written.
- **Done criteria** — one bullet each.
- **Failure modes** — one bullet each.

#### Then, for any plan that DELETES or REPLACES anything, the survivors

**One bullet per thing that lives on the surface being changed and is NOT in the
deletion list.** Name it and say why it stays.

This section exists because of a specific shipped failure and it is the only part of
this summary that is not a restatement of the plan. Origin (2026-08-12): a six-phase
plan rebuilt a screen against an approved design, and three of the old screen's
sections had no successor in that design. Every deletion in the plan had been derived
from a REPLACEMENT — each phase deleted what its own new section superseded — so a
section with no successor belonged to no phase, appeared in no Work list, and was
therefore invisible to the spec check (which compares a diff against the Work list) and
to the downstream sweeps (which hunt what a phase *invalidated*, not what nobody ever
named). Nine research agents, three auditors, two probes and four code reviews passed
over it. The operator found it by looking at the screen, four phases in, and the plan's
own parking lot had asserted the opposite in a line written from assumption.

Deriving this list is a positive search — enumerate what the surface renders TODAY
(the project's own surface index where it has one, plus the file that composes the
screen), then subtract the deletion list. Do not derive it by asking "what did I forget",
which returns nothing by construction.

If the plan deletes nothing, write `Survivors: n/a — this plan deletes nothing.`

#### Then, for any plan with an `**Upstream:` declaration, the upstream section

One line when clean: `Upstream: <path> — N decisions transcribed, no conflicts.`

When the coherence auditor's upstream-reversal finding survived to approval, one 🛑
block per conflict, and the block's text is the AUDITOR'S, never the drafting session's
paraphrase — the incident behind this format is a reversal that WAS surfaced at
approval, in the drafting session's own framing, and approved: self-authored surfacing
launders rather than alarms. Each block: the plain-English ask (per the rule below),
then both verbatim quotes — the upstream decision with its provenance, and the plan
text that reverses it.

#### Close with the decisions that need an answer

Everything step 8 already surfaces — reuse, exclusion-safety, promoted verification
findings, inherited user-facing values, upstream conflicts, doomed-file hits — restated
as a numbered list at the END, where an operator who read the whole summary lands.

**Every entry is asked in plain English, and the ask comes before the evidence.** Three
parts, in order: **What you're deciding** — one sentence, no internal vocabulary (no
exit letters, team letters, auditor names, checker labels, or bare phase ids; name
things by what they are on screen or in the product). **Why it matters** — what happens
on each answer, concretely. **The evidence** — verbatim quotes and citations, beneath.
An operator who has to decode the machinery to understand the question will interpret
it differently than intended, and an approval of a misread question authorizes
something they never chose. (Operator, 2026-08-12: *"the questions I get asked are way
too full of jargon and I don't always understand what is being asked so I may interpret
them differently."*)

If there are none, say so: a plan with no open decisions is a real and reportable state.

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
**Upstream: <path> (sha <upstream doc's commit>)**  *(ONLY when the plan derives from a document carrying operator decisions — an arc index, a parent plan, an approved spec. Pairs with the `## Upstream decisions (transcribed)` section; plan-check flags either half alone. The sha is informational — the sweep re-reads the document itself every phase; a single-phase plan re-reads it once, at Phase Completion Cycle step 4, BEFORE its commit. Standalone plan → omit both halves.)*
<VERIFICATION RECORD — written at Mode 1 step 7, before the plan is ever shown,
from the auditors' reported counts; refuted and uncheckable counted separately.
Mid-work Scope Check additions get noted here too — the record covers only the
text the auditors saw:
  Verification pass <date> — 9/9 claims resolve · 4/4 done criteria covered ·
  coherence clean across 7 stated rules and 3 characterizations · 1 fixed (Work
  cited `oldName` → corrected to `newName`) · 1 refuted (cited)>
<All three axes appear whenever all three auditors ran. An axis that was skipped
and an axis that found nothing must not read alike.>
<NAME every fix — "1 finding fixed" is unfalsifiable; "fixed WHAT, to WHAT" is
checkable against the file. With one pass and no re-dispatch, this naming is
the only thing that makes a bad fix visible.>
<One SPEC CHECK record when the phase ships (Phase Completion Cycle step 1c).
It is BOLD-LED, and that is not stylistic: the Status region is machine-read
line by line, and the bold lead is what the checker's record count recognizes —
an unbolded line is not counted as the record. Counts are mandatory — an
auditor that cannot name what it checked did not run the pass. A single-phase
plan has no phase id, so it drops the `at p<id>` suffix the master shape
carries:
  **Spec check** — tasks 3/3 evidenced · interfaces conform · none uncovered>

<Once work starts: what's done, what's in flight, what's next — plus any
"verified this session" facts and line-hint staleness notes a resuming session
needs. A single-phase plan interrupted mid-work re-enters through this section.>

## Upstream decisions (transcribed)  *(ONLY when Status declares an `**Upstream:` document; omit otherwise)*
- "<the operator's decision, verbatim>" — <path>:<line>
- "<the document's operational rule, verbatim>" — <path>:<line>

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
**Upstream: <path> (sha <upstream doc's commit>)**  *(ONLY when the plan derives from a document carrying operator decisions — an arc index, a parent plan, an approved spec. Pairs with the `## Upstream decisions (transcribed)` section; plan-check flags either half alone. The sha is informational — the sweep re-reads the document itself every phase; a single-phase plan re-reads it once, at Phase Completion Cycle step 4, BEFORE its commit. Standalone plan → omit both halves.)*
<VERIFICATION RECORD — written at Mode 1 step 7, before the plan is ever shown,
from the auditors' reported counts; refuted and uncheckable counted separately:
  Verification pass <date> — 14/14 claims resolve · 6/6 done criteria covered ·
  4/4 shards self-sufficient · coherence 1 finding (p1 locked decision stated a
  rule wider than its own work-shape sketch produced → scoped) · 2 fixed (p2
  Work cited a renamed symbol → corrected to the current name; p4 done criterion
  had no covering Work item → added) · 1 promoted to approval · 1 refuted
  (cited) · 1 uncheckable (vendor API, promoted)>
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
this same Status region. It is BOLD-LED, and that is not stylistic: the Status
region is machine-read line by line, and the bold lead is what the checker's
record count recognizes — an unbolded line is not counted as the record.
Counts are mandatory — an auditor that cannot name what it checked did not
run the pass. A shipped phase with no spec-check record is the same resume-time
alarm as a missing sweep record:
  **Spec check at p4** — tasks 3/3 evidenced · interfaces conform · none uncovered>

## Upstream decisions (transcribed)  *(ONLY when Status declares an `**Upstream:` document; omit otherwise)*
- "<the operator's decision, verbatim>" — <path>:<line>
- "<the document's operational rule, verbatim>" — <path>:<line>

<Transcribe EVERY locked/settled operator decision AND every OPERATIONAL rule —
a sentence bindable per file or artifact ("every deletion sits in the plan that
ships its replacement; nothing is rewired on the way to being deleted") is the
detectable kind, and it is usually the one a slogan ("rebuild, not retrofit")
compresses away. Slogans are transcribed too, as context — but no gate can
adjudicate one at child-plan granularity, so never transcribe the slogan and
skip the operational sentence beside it. Verbatim with provenance, never
paraphrased: the coherence auditor audits THIS section against the plan (its
category (f)), and the sweep re-reads the SOURCE document every phase precisely
because this copy was chosen by the drafting session — the party whose framing
is the thing being checked.>

## Diagnosis  *(required for perf/bug/regression plans; omit for greenfield)*
- **Hypothesis / Falsifiable test / Test result** — run the test before scoping any shard; record the observed output verbatim.

## Non-goals
- <explicit boundary; one-sentence safety rationale for any peer-set exclusion>

## Files touched (overview)
<The cross-phase conflict-spotting view: every file the plan creates/modifies,
tagged by phase. The per-file WORK detail lives in each shard — this is the map,
not the detail.>
- path/to/file.ext — <P1 | P1,P3> — <one-line note>

<DELETIONS are machine-read and get exactly two spellings: a `Deleted:` grouping
header with its own bullets, or an inline `— deleted` annotation on the file's
bullet (`- path — P1 (adopt), P5 — deleted`). Never prose-only ("remove in P4"
is invisible to every mechanical check). Paths BEFORE a bullet's first `—` are
the files the bullet OWNS; paths after it are references ("replaced by `X`") and
carry no disposition. plan-check's doomed-file line — a file both worked on and
deleted by this same plan — keys on this grammar, and a file worked on its way
to deletion needs its one-line justification on this bullet. One honest limit:
the INLINE form's worked-then-deleted detection
is decided only for digit-bearing phase ids (`p1`, `W2`, `2b`) — a plan using
bare-letter or word ids (`A`, `logic`) spells its deletions via the `Deleted:`
grouping, whose detection is id-shape-independent.>

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
here ONLY if it cites an explicit operator sign-off — a "chosen: X" from
a divergent-design pass or an approval in Status. Without that it is an open approval
item surfaced at plan approval, NOT a locked decision (Rules).>

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
- **Work / Files touched**: Always concrete, full stop. Naming every file is Team A's job (change impact) plus Team B's read of the existing code — keep exploring until you can list them; never list "every file that might be relevant" and never leave a `TODO: investigate` placeholder. If you genuinely can't determine the list without operator input (external access, a running app), that's a block — STOP and resolve it before drafting, don't draft around a hole. **In a multi-phase plan the per-file work detail lives in each shard's `## Work`; the master's `## Files touched (overview)` is the coarse, phase-tagged conflict-spotting map only — never duplicate the per-file detail up into it.** In a single-phase plan the one `## Work` section carries it. This is *plan-time* completeness; discovering an additional file *while implementing* is a different thing, governed by the Scope Check "Before touching a file" rule — under which a file the described work REQUIRES is edited and recorded as a planning defect rather than halting the phase, and only a behaviour change beyond the description, an unnamed deletion, or an unnamed user-visible surface stops a worker. **A `## Work` bullet placing a new view or renderer INTO an existing file carries one extra obligation: the named file must be able to REACH the data that renderer consumes.** Naming a plausible-looking file whose scope cannot see those values is the placement failure the grounding auditor now checks for, and it is invisible on a skim because the path resolves and the file exists. **And every per-item note you attach to a file bullet — "already guarded", "nil-safe already", "doc-only", "no change needed" — is an assertion a worker will act on without auditing, so it is held to the same same-subject test as a Done criterion, in the post-phase tense** (the rule and the incident live with the Done-criteria commentary below).
- **Work-shape sketches (in Work, both shapes)**: Any work item whose change isn't obvious from one line gets a 2-6 line sketch under it — function signatures, data shapes, or pseudo-code, whichever shows the design. The sketch exists for the approval gate: a reviewer can catch a design disagreement in a signature; they can't in "fix search". Two rules keep sketches honest. (1) **Docs-grounded**: a sketch may only use symbols and call shapes verified during EXPLORE — cite the doc or source next to the sketch, same verify-or-block standard as any other plan claim. Needing an unverified symbol means EXPLORE isn't done; go back and verify, don't sketch from memory. (2) **Interface binding, body illustrative**: the signatures, data shapes, and error behavior the user approved are contract — changing them mid-work is an approach switch (Mode 2 step 2 re-plan trigger). The body mechanics are illustrative and expected to flex on contact with reality; divergence is a one-line note in Decisions & findings, not a re-plan. Skip the sketch for obvious items — a sketch on "bump the version constant" is noise.
- **Interface lines (in Work, both shapes)**: every phase's `## Work` carries a `- Consumes:` and a `- Produces:` dash bullet — per task when the phase has a task tier, once for the whole Work section when it does not. **They are mandatory with `none` as a valid value; absence is the only invalid state** (the same model as the sweep record's `code: nothing pinned` — a written `none` is a claim someone made, an omission is indistinguishable from forgetting). `Consumes:` names the exact signatures and types this work CALLS that already exist — signatures only, with the EXPLORE citation that grounds them sitting on the file bullet or its sketch. `Produces:` names the exact signatures and types this work CREATES for later tasks or phases to call — those symbols do not exist yet, by design, and the grounding rule does not apply to them; what grounds a `Produces:` line is the work-shape sketch beside it. **A later phase that consumes an earlier phase's product REPEATS it as its own `Consumes:` line.** That duplication is deliberate: the worker's entire world is one pasted shard, so a `Consumes:` line pointing at another file is a referent the worker cannot resolve. It is the verifiable kind of duplication — the executability auditor reads the two lines against each other — not the kind DRY bans. **Interface lines carry NO file paths — not as a citation, not in passing, in no form.** Paths live only on file bullets, and the reason is mechanical: the extractor that builds the staleness diff reads paths off every Work bullet without caring which kind it is, so a `file:line` citation on a `Consumes:` line silently joins the phase's drift set as though the phase modified that file. The grounding still happens; it just happens on the file bullet, where the path belongs and where drift detection is supposed to see it. And a shipped interface that differs from the approved one is not a detail — it fires the approach-switch re-plan trigger at that phase (the rule is in the Rules section above).
- **The task tier (in Work, both shapes)**: when a phase's Work splits into more than one job, each gets a `### Task N — <one-line scope>` heading INSIDE `## Work` — not a separate section, not a numbered list. Three rules make the tier work and none of them is negotiable. (1) **Each task's file set is DISJOINT from every other task's**: a file appears under exactly one task, always. A phase whose work genuinely cannot be split that way is formally unsplittable — leave it untiered rather than listing a file twice. (2) **Dash bullets remain the only path carriers.** A numbered list is silently invisible to the path extractor, so files listed that way vanish from every drift check that reads the Work list — the bullet leader is machine-read, not cosmetic. (3) **No per-task Done criteria.** Criteria stay phase-level; a task is a unit of *scope attribution*, which is what lets the spec check say which task a diff hunk belongs to. Tasks are consumed IN ORDER by ONE worker and are not commit units — the phase is still one commit. Skip the tier entirely for a single-job phase; a `### Task 1` with nothing beside it is noise. The tier is also an observable trigger: a phase with more than one task routes its spec check to an independently dispatched reviewer instead of the dispatching session (Phase Completion Cycle step 1c).
- **Decisions & findings (in the shard)**: One entry per **non-local** decision (the threshold rule in Rules); each entry is Decision / Rationale / Alternatives considered / Evidence (file:line or URL+section). Mark a decision a later phase invalidates as `superseded by phase-<id>` — don't silently rewrite it. Append empirical findings (spike results, mid-implementation gotchas) here as the phase runs; this is what stops the next session rediscovering them after a clear. (Where research lives — inside the shard, never a separate file — is its own rule in Rules.)
- **Background findings (master only)**: ONLY cross-phase research that belongs to no single phase. Anything scoped to one phase belongs in that phase's shard, not here. This is the one research home in the master, and it never grows per-phase detail.
- **Failure modes**: Aim for 5+. If you have fewer than 3, you don't understand the problem yet. Draw from: similar past failures, platform quirks, unfamiliar dependencies, integration boundaries, untested paths. **But this section is not a sink for unresolved verifications.** A conditional whose antecedent is statically checkable ("*if* warmups can't be marked complete…"; "*if* this endpoint doesn't return X…") is a fact you didn't look up wearing risk's clothing: resolve the antecedent during EXPLORE, then either delete the entry or restate it as the verified fact — the verify-or-block rule bites on the antecedent, not just on declarative claims. A legitimate failure mode is one whose outcome remains uncertain *after* you've verified everything statically knowable about it.
- **Done criteria**: These are the exit conditions. When met, STOP — no polish, no adjacent improvements. Each criterion must be concrete and verifiable. **🛑 A criterion asserting that two things AGREE must separately assert that each one is PRESENT.** "The header and the card show the same verdict", "the total matches the sum of the rows", "both screens report the same count" — every one of those is satisfied *vacuously* when both halves are missing, and a capture pass or a test run against it comes back green while the phase has shipped neither. Write the presence and the agreement as separate checks: *the card renders a verdict word; the header renders a stat; they are the same verdict word and the same numbers.* (Origin: a phase carried "the header's title, colour and stat, captured beside the card, agreeing with it", ran its capture pass, and passed — while the card rendered no verdict at all and the header rendered no stat at all. The code review found both; the phase's own gate could not, because nothing absent can disagree.) In a multi-phase plan there are **two levels**: each shard's Done criteria are that phase's commit-level exit, and the master's Done criteria are the plan-level, cross-cutting exits (whole-feature suite green, deployed/pushed, end-to-end result) that belong to no single phase. The master's level is NOT a copy of the per-phase criteria — restating them there is the duplication to avoid; it holds only what spans phases. The plan is done when every shard's criteria AND the master's are met.

  **Any phase whose `## Work` names a file that draws UI carries a visual-capture criterion, and that is not optional** (the rule is in the Rules section above). Write it as the state to capture, not as "screenshot it": *which* screen, under *which* data condition, and what the capture must show — "the card at zero logged intake with a known burn: the drained ring, no deficit figure, nudge below". A criterion the implementer can satisfy with any screenshot of that screen is not one. Note where the state is unreachable in the project's fixture/simulator setup and say what covers it instead; that is a real answer, and it belongs in the plan rather than being discovered at capture time. A UI phase whose criteria are all satisfiable by a green test suite is incomplete — type checking proves the code compiles, never that the feature looks right.

  **When the phase's Work CONSUMES a type with enumerable states it does not itself define** — an enum, a status field, a state machine — **the Done criteria enumerate ALL of its states and say what each one produces.** The trigger is observable: does the phase read a value of an enum (or equivalent) type defined elsewhere. Count the cases in the source, not from memory — and if the count in the shard and the count in the source differ, the shard is what's wrong. Named states get built; unnamed states get whatever the default branch does. In the incident behind this rule the two states the shard named were built correctly, and the three it did not name shipped as a permanent placeholder drawn over real data — enumeration, not competence, was the variable. *(Why: RATIONALE.md, "Enumerate every state of every type the phase puts on screen".)*

  **A criterion must test the property the GOAL names, not a property that travels with it.** Read the plan's goal sentence and the criterion side by side and check they have the same subject. When they don't, the criterion is testing a stand-in — something that happens to be true wherever the real property is true, and stops tracking it somewhere. That "somewhere" is the defect, and it will be at an edge of the range, because that is where two properties that usually coincide come apart. The incident: the goal said *neither end of the window may be a single session*, and the criterion that shipped said *the two halves must not overlap*. Identical claims for every window of five or more. At exactly three, the halves still do not overlap — and each one is a single session, which is the precise thing the plan existed to eliminate. The proxy passed, the plan wrote "this settles the question," and every gate downstream inherited a closed finding whose citation was correct.

  **This check runs on every per-item NOTE in `## Work`, not only on criteria — and that half is the one that bites, because the note is what a worker reads at the moment it decides whether a file needs touching.** An annotation like *"already guarded"*, *"nil-safe already"*, *"no change needed here"*, *"this one's fine"* is a criterion in disguise: it asserts a property, it is acted on, and nothing in the flow audits it. Hold it to the same test — does it name the property the TASK names, or one that merely travels with it? The sequel to the incident above, in the very phase written to fix it: the task said *"each of these eight sites must decide what absence means rather than inheriting a fabricated direction,"* and the note beside one site read *"already guards `direction != .flat`."* True — and the wrong property. That guard stops a *flat* direction being emitted; it says nothing about nil. Nil could not occur at that site before the floor moved, so the note was correct under the old conditions and stopped being correct under the ones the phase itself created. The worker read the note, reported "already guarded, no change needed," and shipped a tool where *too short to judge* and *measured flat* produce byte-identical output. **A note that says a site is already handled is a claim about the code AFTER this phase lands, not before it** — that is the tense the check has to be applied in, and it is the tense these notes are almost always written in wrongly.

  **And a criterion that fixes a size, count, or length has to say why THAT one** — because the number it names is usually the number where the thing works. Same incident, the sharper half: the criterion read *"a **five-point** series where the middle value alone would flip the direction."* Five is where the property holds. The one test standing guard was specified, at plan time, at the value that cannot fail. So when a criterion or a Work item names a concrete size, name the smallest size the code accepts as well, and say what happens there — **the guard is where the interesting behaviour is, not the typical case.** When the phase's Work quotes a floor (`count >= 3`, a minimum window, a required length), the criteria state what the code does AT that floor, not near it. This is the same rule as the fixture-monoculture check that plans already escalate for TESTS, aimed one level up: a criterion that names one value is as blind as a fixture that varies one dimension, and nothing else in the flow is looking at it. *(Why: RATIONALE.md, "A criterion that names a size names the size that passes".)*

  **And no phase's Done criteria — UI or not — may be fully satisfiable by a green suite plus greps** (the standing rule lives in the Rules section above): at least one criterion names an observable the phase must produce and measure. A constraint a downstream sweep carries into this phase arrives as one of these criteria, phrased as the observable it protects, never as relocated prose.
- **Parking lot**: Always start empty. The skill never pre-populates it.

---

## Execution brief

Loaded by `/plan`'s Phase Completion Cycle step 1. Substitute the `{...}` placeholders and send the rendered text as the `prompt` on an `Agent` call.

- `subagent_type: "general-purpose"` — never `Explore`, which omits CLAUDE.md. **Where the full /plan skill's dispatch gate is registered it enforces this**, keyed on the `Plan work:` line that opens the rendered brief below; keep it at the start of a line or the gate stops seeing worker dispatches. Like the step-7 audit marker it carries the agent-type rule ONLY — a worker owes none of the research boilerplate.
- **No `isolation`.** The worker runs in the main checkout so the next phase builds on its edits. Worktree agents branch from the repo's *default branch* rather than the parent's HEAD and hand back only a text report with no merge — that solves concurrency conflicts, which sequential phases do not have, while breaking the one property they need.

Placeholders: `{phase}`, `{slug}`, `{shard_text}`, `{repo_path}`.

**No project-rules placeholder, deliberately.** A `general-purpose` subagent already receives every level of the CLAUDE.md hierarchy the main conversation loads — that is the same fact that forces `general-purpose` over `Explore` everywhere in this skill. Pasting project rules into the prompt would duplicate what the worker already has, and would drift from the source the moment CLAUDE.md changes. If a worker turns out to be missing a project rule, the fix is in that project's CLAUDE.md, not in this template.

---

Plan work: {slug}.
You are implementing phase `{phase}` of the `{slug}` plan — a plan you did not write.

You have no history with this work, and that is deliberate. You are not carrying anyone's opinion about how big this change ought to be.

## The phase

{shard_text}

## Repo

`{repo_path}`

## Rules

- **Implement what the phase's `## Work` section describes.** A file you must edit **in order to complete the described work**, which the Work list does not name, is a DEFECT IN THE PLAN, not a scope question: edit it, and name it in your report under a heading the dispatching session cannot miss, with one line on why it was unavoidable. Do not stop for it. The Work list is a prediction written by someone reading rather than building; you are the first to see the real shape, and a phase that halts every time that prediction is short converts a planning miss into a stalled phase.
- **STOP and report, without editing, for exactly three things:** a change that would alter behaviour BEYOND what the phase describes; a DELETION the phase does not name; and any change to a user-visible surface the phase does not name. Those are the cases where a wrong guess by you is expensive and a question is cheap — the rest is bookkeeping the dispatching session does after the fact. Do NOT use this as a licence to widen scope generally: every unlisted file you touch is reported, and a reviewer checks the diff against the Work list in both directions.
- **If the Work section has `### Task` headings, consume them IN ORDER — never reorder them, never interleave them.** Finish a task before opening the next one. This is not a style preference: the phase's spec check evidences the diff task by task, and per-task evidence only exists if the work happened task by task. A file belongs to exactly one task, so an interleaved worker leaves hunks nothing can attribute.
- **If the correct fix is BIGGER than the phase describes, do it bigger and say so in your report.** Diff size, file count, and implementation effort are not your inputs. The phase was scoped by someone reading, not building; you are the first to see the real shape. **"Bigger" means more files, more effort, more depth IN SERVICE OF the behaviour the phase describes** — that is the axis the plan is bad at predicting and you are good at seeing. It does not mean broader BEHAVIOUR: a change doing something the phase does not describe takes the stop path above, however obviously correct it looks from here. The two rules divide cleanly on that one question, and it is the question to ask before widening anything.
- **If you catch yourself designing a smaller way around the problem, STOP and report that instead of building it.** A workaround you ship is worse than a blocker you name, because the workaround is invisible afterwards and the blocker is not.
- **If you need information the phase does not give you, STOP and report the gap.** Do not guess, and do not invent context to fill it. A phase that cannot be executed from its own text is a defect in the plan, and your report is the only thing that surfaces it.
- **A code comment is a claim by a past author, not evidence.** Decide what a call site needs by reading what the code does, never by trusting a comment that says it is already handled — comments rot without breaking anything, so a stale one reads exactly like a true one. A comment the code contradicts goes in your report's findings section.
- Follow the phase's TDD instruction if it has one: failing test first, minimum code to pass, then refactor.
- **Do NOT commit.** Do NOT run the project's review pass — not because you can't, but because reviewing your own diff is not the check the gate is asking for. Nor would it satisfy the gate: a review run from in here reports back as text and stamps nothing, so the dispatching session would have to review again anyway. The dispatching session reviews your work, runs the project's test gate, and commits.
- Never `git add -A` or `git add .` — the dispatching session stages explicit paths.

## Report back

Three sections, in this order:

1. **What you changed, by task** — for each `### Task` in the phase's Work, in order: the task heading, then the files you touched under it and what changed in each. A phase with no task tier has exactly one task — its whole Work section — and reports as a plain file list. If you STOPPED mid-phase, still list every task you completed here.
2. **Every empirical finding** — gotchas, surprises, anything the phase got wrong about the code, anything the next phase would otherwise rediscover. **This is the only record of them.** You hold context that disappears when you return; nothing else is holding it, and the dispatching session writes your report into the plan's durable record.
3. **Anything you stopped on** — scope gaps, missing information, a bigger-than-described fix, a workaround you declined to build. If you stopped mid-phase, **NAME the task you were executing when you stopped**: the tasks before it are complete, the tasks after it are untouched, and your uncommitted edits stay in the checkout for the dispatching session to disposition. Do not revert them and do not commit them — that session resumes from where you stopped, and it can only do that if it knows which task you were in.

Section 3 empty is a normal result. Section 2 empty almost never is — if you genuinely found nothing surprising, say that explicitly rather than omitting the section.
