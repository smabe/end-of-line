---
name: plan
description: Create, resume, hand off, ship, or abandon a phased plan (single file for single-phase work; master + one shard per phase for multi-phase) in plans/ before starting multi-file work. Keeps ADHD scope creep in check by making scope explicit and approved upfront.
user_invocable: true
---

<!--
clu-adapted fork — sanitized for the clu package. This bundled copy is
intentionally diverged from any personal /plan: it has NO hard dependency
on skills a public clu install won't have. graphify / security-review
citations were removed; diagnose / code-review / brainstorm survive only
as clearly-optional examples ("if your setup has one"). Do NOT re-sync by
blind overwrite — re-apply this sanitization, or personal-skill
references leak back into the shipped package.
-->

## Plan Workflow

This skill enforces a "plan before code" discipline: for any non-trivial multi-file change, write a plan under `plans/` (a single file `plans/<slug>.md` for single-phase work; a master `plans/<slug>.md` plus one shard `plans/<slug>-<phase>.md` per phase for multi-phase work) and get user agreement before coding. The template and rules below are the authoritative source — this file is self-contained on purpose so it works in any project, including fresh clones with no extra project memory loaded. Why this discipline exists: without an explicit plan, work drifts ("while I'm here" fixes turn a 2-file change into a 7-file commit); the plan file is the anti-drift contract. Trivial changes skip the plan flow, gated on observable shape — never on a time estimate, which is self-certifiable: the change touches a single file AND is one logical change (typo fix, constant tweak, comment/doc edit, an obvious one-function bug fix). Multi-file, more than one logical change, or any new file/symbol → plan.

The skill has five modes, auto-detected from context. Bare `/plan` (no slug) is a status query, not an error: list every plan in `plans/` (excluding `shipped/` and `archive/`) with its Approval marker and NEXT phase, flag any legacy-shape files (see Mode 2's reshape rule), and ask which to resume — or whether to create a new one.

### Mode 1: Create a new plan (`/plan <slug>` — no existing file)

1. **Normalize the slug**: lowercase, hyphens for spaces, strip non-alphanumeric except hyphens. Example: "Pipeline Hardening!" → `pipeline-hardening`.
2. **Find the project's plans/ directory.** Start from the current working directory, walk up to the git root if needed. If `plans/` doesn't exist at the git root, create it.
3. **Check for an existing file at `plans/<slug>.md`.** If it exists, switch to Mode 2 (resume).
4. **EXPLORE — mandatory, unconditional** (the full no-exceptions wording lives in Rules: "EPCC: Explore is unconditional"). The plan is *not* drafted until exploration completes: codebase + project-local API docs + web prior art — all three dimensions, every time, before a single line of plan text gets written.

   **Hand hard diagnostic cases to a dedicated diagnosis pass first.** If the symptom is genuinely opaque (no obvious hypothesis, multiple plausible causes, intermittent reproduction), find the root cause BEFORE scoping the fix — with a disciplined diagnosis loop (e.g. a `/diagnose` skill if your setup has one). Diagnosis finds the cause; the plan scopes the fix. Don't try to do disciplined diagnosis inside the plan flow — they're sized for different jobs.

   **Three mandatory research dimensions.** Each gets its own dedicated agent. Dispatch all agents in a single message using the Agent tool so they run in parallel — put every agent call in one message and they execute concurrently.

   1. **Codebase / internal exploration** — `subagent_type: "Explore"` against the project files. Existing helpers, callers, conventions, file sizes, naming patterns, test coverage of the surface being changed. Brief: "Map the area this plan will touch. List existing helpers we should reuse instead of reimplementing. List callers of any function we're changing. Quote file:line for every claim."

   2. **Project-local API documentation + canonical samples** — `subagent_type: "general-purpose"`. For whatever dependencies this project uses, surface the framework's official guidance + working code patterns. The agent figures out where this project's docs live (vendored docs folders, build-output docs, library README + examples in `node_modules` / `~/.cargo/registry/src/` / `site-packages` / `Pods`, framework headers, generated `.d.ts` files, etc.) and fetches from the vendor's official docs site when no local copy exists. Brief: "What does the framework's canonical pattern for this problem look like? Where are working examples in the project's dependencies or in vendor sample repos? What footguns does the doc itself call out? Cite file:line for local sources or URL+section for fetched docs."

   3. **Web prior art / community evidence** — `subagent_type: "general-purpose"` with WebSearch + WebFetch. Brief: "How are others in this language / framework / domain solving this problem? Stack Overflow threads, GitHub issues on relevant libraries, recent blog posts, conference talks. Bring back canonical patterns, recent gotchas, and links. Vendor docs are routinely incomplete or describe an intended contract that doesn't match shipped reality — independent corroboration is the point of this dimension. Cite URLs for every finding."

   **These three are non-negotiable** for any plan that touches code, regardless of plan size, file count, or category. Each agent's brief describes the *shape* of what to look for; the agent figures out where this project's equivalent lives (Rules: "Generic-skill discipline").

   **Three framing questions to hold in mind while designing the dispatch.** Everything below is scaffolding for these; if you can answer them well, the rest mostly takes care of itself:

   1. **What's the shape of failure if the research is wrong?** Frame this as a concrete falsifiable test scenario (initial state, applied conditions, expected vs. failing behavior). That scenario is the load-test that lands at phase 1.
   2. **What finding would a generalist agent bury?** Whatever it is, that's the brief for one specialist whose ONLY job is to surface it as a primary finding — not as a footnote under broader topic coverage.
   3. **How many genuinely distinct dimensions am I researching?** That's your agent count beyond the three mandatory dimensions. Specialists compose on top per their trigger rules below.

   **Agent type choice.** The three mandatory dimensions pre-assign types (Explore for codebase, general-purpose for docs + web). Any additional specialist agents below also use `subagent_type: "general-purpose"` unless they're doing pure local-codebase mapping, in which case use `"Explore"`. Mix freely across the dispatch.

   **Beyond the three mandatory dimensions, agent count scales with research surface area.** Not with plan size, not with category — with how many distinct *additional* dimensions the plan actually has. Soft guidance for the extra slots:
   - **0 extra** when the three mandatory dimensions cover everything (single small change, contained area).
   - **1-2 extra** when the plan spans extra dimensions: e.g. an LLM orchestration change wants prompt-design + caching + integration + evals on top of the three mandatory; a UI change might want state-flow + animation + accessibility specialists added.
   - **3+ extra** when the plan is genuinely cross-cutting AND specialization buys clarity: complex algorithmic work, large-feature LLM systems, multi-service refactors. The extra slots are for *role specialization*, not for chasing the same question harder.
   - **Stop adding agents** when the marginal one would re-cover ground from another. The consolidation overhead grows with agent count; budget it intentionally.

   **Role specialization is the principle that funds high agent counts.** Each agent should have a single sharp job no other agent is doing. This prevents the failure mode that motivates this rule: a generalist agent mentions the load-bearing detail in passing, the consolidation report buries it under broader findings, and the bug surfaces in phase 3. A specialist whose *only* job is "the inner-loop / the prompt structure / the cache invalidation contract / the migration path" surfaces those findings as primary, not as asides.

   **Examples of additional role splits by domain** (illustrative, not prescriptive — these compose on top of the three mandatory dimensions):
   - **Algorithmic / numerical / physics**: math-and-formulas agent · per-tick-inner-loop specialist · integration-with-existing-system · (optional: failure-modes-under-load, platform-quirks).
   - **LLM orchestration**: prompt-design and structured-output agent · caching-and-token-budget agent · model-version-and-migration agent · evals-and-regression-fixtures agent · integration-with-existing-brain agent.
   - **UI feature**: component-and-layout agent · state-and-data-flow agent · animation-and-interaction agent · accessibility-and-test agent.
   - **Backend feature**: schema-and-migration agent · API-contract-and-versioning agent · caching-and-invalidation agent · error-and-retry semantics agent.
   - **Security review** (illustrative): threat-model-and-attack-surface agent · authn-and-authz agent · data-handling-and-privacy agent · dependency-and-supply-chain agent.
   - **Cross-cutting refactor**: callers-and-impact agent · test-coverage agent · deprecation-path agent · integration-test-strategy agent.

   **For algorithmic plans specifically** (signals: plan cites a paper, GDC talk, engine docs, or third-party library's primitive), one of the additional agents MUST be the implementation-details specialist briefed explicitly:
   > "The math is someone else's job. Your job is the loop structure and the parameters that aren't on the equation page — iteration counts, warm-start handling, regularization parameters, accumulator resets, internal stabilization passes, default thresholds. Read the engine source. What does the per-tick / per-step inner loop ACTUALLY do, beyond the formula? What load-bearing details exist that aren't in the API documentation?"
   This separation prevents iteration count from being buried under formula overview.

   **Each agent's brief includes:**
   - The slug + a one-line goal of the plan being scoped (so the agent knows what it's researching for)
   - Specific questions tailored to its role — sharp enough that a generalist wouldn't have written them
   - For algorithmic plans, the four required questions below concentrated in the implementation-details specialist's brief
   - An explicit instruction: "You are NOT to invoke the `/plan` skill. Your job is research only. Report in under 400 words."
   - A request for file-path-and-line-number citations (or URL + section) so findings can be verified quickly.

   **For plans involving algorithms, numerical methods, physics, control loops, constraint solvers, integrators, or any code where correctness depends on more than the formula** (signals: the plan cites a paper, a GDC talk, an engine docs page, or a third-party library's primitive), the brief MUST also require the agent to answer these four questions:

   1. **"What does the canonical implementation do INSIDE the per-tick / per-step inner loop, beyond the formula on the page?"** Iteration counts, regularization parameters, stabilization terms, accumulator resets, warm-start clamps, convergence tolerance — the things that aren't in the math but are load-bearing for correctness.
   2. **"What fails if we ship just the formula without the surrounding solver structure?"** Specifically: under sustained external load (gravity, friction, persistent input, accumulated error), does the system drift? Diverge? Oscillate? Quote the failure mode in concrete terms (e.g. "body drifts 3 px/tick downward forever").
   3. **"What's the minimum executable test that would catch a naive implementation?"** Describe the exact scenario — initial state, applied forces, time horizon, expected vs. failing behavior. This becomes the first thing to validate in phase 1.
   4. **"What load-bearing details exist in the engine source that are absent from the API documentation?"** Default iteration counts, hardcoded thresholds, internal stabilization passes, etc. These are the gotchas that paper-style references won't surface.

   These four questions exist because of a real failure: a constraint-solver rewrite shipped phases 1-2 with a one-iteration solver, and the bug (body drifts under gravity) only surfaced in phase 3 when external forces were added. The research had named the formula correctly but treated iteration count as a minor implementation detail. Don't let that recur — *flag* the inner-loop specifics, don't bury them.

   **For plans that add a new file mirroring an existing file** (signals: the request is to add a NEW file whose description uses words like "mirror", "like X", "similar to", "same look-and-feel as", "same family as", "matches the X style"; OR the new file's name has an obvious sibling already in the same directory — e.g. `news_window.py` next to `chat_window.py`, `foo_backend.py` next to `bar_backend.py`), one of the agents MUST be the **reuse / refactor specialist** briefed explicitly:
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

   **Divergent-design note**: if exploration surfaces significant scope questions the user can't decisively answer (e.g. "should we refactor this whole subsystem or just patch it?"), suggest a separate divergent-design pass (e.g. a `/brainstorm` skill if available) before re-running `/plan`. Do NOT run that exploration inline from within this skill — it's user-interactive and doesn't compose cleanly.

   **Consolidate findings as ground truth for the plan** (internal step; not surfaced to the user as a separate report). Walk away from exploration with:
   - The corrected understanding of the area being changed (codebase shape + canonical API patterns + community gotchas)
   - Any forced binary decisions surfaced by reuse / exclusion specialists, with the specialist's recommended option
   - Any load-bearing implementation details surfaced by the algorithmic specialist (for algorithmic plans)
   - **No unverified claims survive EXPLORE** (Rules: "No research deferrals — verify or block"). The only things research legitimately can't close are (a) genuine operator decisions (surfaced at approval) and (b) empirical/runtime unknowns (which become the Diagnosis falsifiable test or the algorithmic load-test, never plan-body facts). Anything else unresolved means EXPLORE isn't done — finish it, or STOP and resolve it with the operator before drafting.
     - **(b) has a membership test — apply it, don't self-certify into it.** A question qualifies as an empirical/runtime unknown ONLY if a Read / grep / doc-fetch *this session* genuinely cannot close it — i.e. it truly needs a running app, live external system, or real model output. If reading the code or docs would settle it, it is NOT empirical: verify it now. The tell that this rule is failing is a plan that says "Phase 1 must verify X" where X is statically checkable (does this function branch on that flag? does this type have that field?) — that "verification" is the EXPLORE work you skipped, not a legitimate deferral. Ask "does closing this need runtime, or just a Read?" before routing anything to (b).

5. **Draft the plan** using the template below, with research findings as ground truth. Three rules for drafting:
   - **Every factual claim in the plan must be supported by exploration findings.** Cite file:line / URL+section in the plan body where the claim depends on a specific verified source.
   - **Verify or block — no deferral channel** (full rule in Rules: "No research deferrals — verify or block"). Every claim in a drafted plan is verified this session and cited with file:line or URL+section, or the plan isn't written yet.
   - **Bake forced binary decisions into the plan as the recommended option.** If the reuse specialist recommended a Phase 0 refactor, draft the plan with a Phase 0 refactor included. If the exclusion specialist recommended folding excluded items into scope, draft the plan with them included. The decision still gets surfaced explicitly at approval (see step 7) so the user can override, but the plan reflects the recommendation by default.

6. **Write the file(s).** Single-phase plan → one file `plans/<slug>.md`. Multi-phase plan → the master `plans/<slug>.md` AND every shard `plans/<slug>-<phase>.md`, written in the same step. Every plan is written with `**Approval: DRAFT**` as the first line of its Status section (single-phase: `## Status`; master: `## Status & cold-start`) — the marker flips only at explicit approval in step 8. **Drafting the shards is not optional and not deferrable to "when the phase starts" — a multi-phase plan whose shards don't exist yet is not written.** Every shard a phase needs to be self-sufficient is authored now, at plan time, from the same research pass; a future session resuming the plan reads a shard that exists or it has nothing to resume from.

7. **Show the plan to the user, surfacing any forced binary decisions explicitly:**
   > Here's the plan. Read it over and approve / tweak / reject. I won't write any code until you say it's live.
   >
   > [If reuse specialist surfaced a decision]
   > **Reuse decision baked in:** plan adopts Phase 0 refactor of `<duplicated surface>` based on `<file:line>` evidence. If you'd prefer copy-and-defer (ship the duplication, file the dedupe as follow-up), say so and I'll restructure.
   >
   > [If exclusion specialist surfaced a decision]
   > **Exclusion decision baked in:** plan folds `<excluded items>` into scope based on `<file:line>` dependency on `<included items>`. If you'd prefer to keep the exclusion, give me the one-sentence invariant that makes the asymmetry safe.

8. **Block on user response.** Do not touch any code files, do not run any builds or tests, until the user explicitly approves. If the user picks copy-and-defer for a reuse decision, append the deferred refactor to the plan's Parking lot in writing before code starts. On approval, flip the plan's marker to `**Approval: APPROVED <date>**` — the file itself must record the approval, because a future session can't see this conversation.

9. **Once approved, enter "working the plan" mode**: reference the plan on every file touch, interrupt scope creep, append to the parking lot when the user drops a shiny idea mid-work.

### Mode 2: Resume an existing plan (`/plan <slug>` — file exists)

1. **Read in this fixed order — do NOT skip a step.** (a) The master `plans/<slug>.md`: Phase map (the arc + gates) and Status & cold-start (which phase is NEXT). (b) The **NEXT phase's shard** `plans/<slug>-<phase>.md` in full — Locked decisions, Work, Decisions & findings. (c) The master's Background findings, plus any earlier shard whose Decisions & findings the NEXT phase's gate references. (d) **Any spec / sub-document the plan references** (lines like "Full spec: <path>", brainstorm outputs, design docs, ADRs). A shard that defers detail to another file is incomplete without that file; resuming from a summary alone forces re-deriving a dependency map the shard already has — the failure mode is a fresh session "discovering" entanglements (call sites, threading requirements, focus contracts) the shard recorded at plan time. **You read the NEXT shard, not every shard** — shipped phases are reference-on-demand; reading all of them back is the context-clouding this sharded layout exists to prevent. The re-anchor pass in step 2 *confirms* the recorded map against current code; it never re-derives one from scratch. If the code read surfaces a dependency the shard doesn't record, that's a finding to write back into the shard, not silent context. (e) **Check the Approval marker** in the Status section: if it still reads DRAFT, this plan was never approved — present it for approval (Mode 1 steps 7-8) instead of resuming work on it.

2. **Validate the plan against current reality before working it (mandatory — do NOT skip to working a stale plan).** A resumed plan was authored in a prior session; resume mode deliberately skips Mode 1's EXPLORE, which is only safe *if the plan still matches the code*. Start mechanical: list the commits that touched the plan's files since it was authored or last refreshed — `git log --oneline <that commit>.. -- <every file in the NEXT shard's Work>` — those diffs are exactly where drift lives; read them before any judgment-based check. Then spot-check its load-bearing claims **in this session**: the NEXT shard's **Work** (does the real change touch what it lists, or materially more? — cross-check the master's **Files touched (overview)**), the **Diagnosis** (does the stated symptom/root cause match what the code actually shows?), and the **approach** (are you about to do what it says, or something else?).

   **Re-plan trigger — if ANY is true, the plan is stale: STOP, escalate to a fresh Mode 1 step 4 EXPLORE + rewrite, re-confirm before code. Do NOT patch the plan turn-by-turn while coding:**
   - **Scope undercount** — the real change hits materially more files / call-sites / surfaces than the NEXT shard's **Work** lists (rule of thumb: >~1.5× the listed count, or any unlisted file in another module).
   - **Diagnosis mismatch** — the symptom or root cause is a different file, mechanism, or layer than the plan states.
   - **Approach switch** — you are considering a different implementation approach than the one the plan names, **especially a smaller-diff alternative.** You may NOT pitch a smaller-diff alternative to the operator as "lower risk" until you have shown the plan's named approach is *infeasible* — cite the blocker (path:line / doc URL). "It's bigger / fewer files / less code" is not infeasibility and is not a reason to switch. The smaller-diff alternative is exactly where workarounds hide.
   - **Unanswered fork** — a forced binary decision surfaces that the plan didn't pre-answer.

   Re-planning means: run Mode 1 step 4 (the 3-dimension EXPLORE) on the **actual** scope, rewrite the plan to match, and re-confirm with the operator before code. Inline-mutating a materially-wrong plan during the Phase Completion Cycle is the failure this gate exists to stop — it lets a stale design (and the workarounds that grow on it) ship without ever getting the EXPLORE that grounds design decisions.

   **Legacy-shape plans reshape here, not mid-work.** If the file predates the current template — a multi-phase plan in a single file, or section headers that don't match the names the Scope Check and this mode key on (e.g. `## Files to touch` instead of `## Work`) — bring it to the current shape during this validation pass, before any code: shard a multi-phase single file into master + per-phase shards, rename sections to the template's names, carry the content over verbatim. This is mechanical re-filing, not a re-plan — it needs no fresh EXPLORE unless one of the triggers above also fired.

3. **Summarize the state** to the user: goal, done criteria, what's in the parking lot, how much is done vs remaining — and surface any re-plan triggers found in step 2.
4. **Ask what the user wants**: continue working it, **re-plan it** (if step 2 flagged staleness), update the plan, ship it (Mode 3), or abandon it (Mode 5).

### Mode 3: Ship a finished plan (`/plan ship <slug>` or user says "ship the plan")

1. **Verify done criteria are actually met.** Walk BOTH levels: every shard's Done criteria AND the master's plan-level Done criteria — ask the user to confirm any ambiguous ones. If any shard's criteria OR any master-level criterion is unmet, refuse and say what's still outstanding. (Single-phase plan: the one file's Done criteria.)
2. **Create `plans/shipped/`** if it doesn't exist.
3. **Disposition the Parking lot.** Every parked item gets an explicit exit before the plan archives: file it as a follow-up (the project's issue tracker if it has one, otherwise surface the list to the user) or drop it with a one-line reason. Record each disposition in the master. Silently archiving unread parked items is how deferred work disappears.
4. **Move the master AND every shard.** `plans/<slug>.md` → `plans/shipped/<slug>.md`, and each `plans/<slug>-<phase>.md` → `plans/shipped/<slug>-<phase>.md`, using `git mv` for tracked files, plain `mv` otherwise. **Never archive the master and leave shards orphaned in `plans/`** — they move together as one unit; the shards carry the decisions-and-findings record that makes the archive worth keeping.
5. **Sweep handoff and resume leftovers.** Delete any `plans/handoffs/<slug>-*.md` still present — handoff files are transient by contract (Mode 4 step 4) and must not outlive the plan. Same for a `plans/RESUME-<slug>.md` briefing if one exists (some projects keep per-plan resume prompts): delete it and remove its line from the `plans/RESUME.md` index — a resume prompt for a shipped plan is stale instructions waiting for a fresh session to execute them.
6. **Confirm to the user** with the new paths and a one-line summary of what shipped.

### Mode 4: Generate a phase handoff (`/plan handoff <slug>`, or the user asks for a handoff prompt, says they're pausing for a context clear, or wants to brief another session)

Applies to ANY plan with remaining phases. There is no "multi-session plan" designation and no plan-type precondition — any phased plan becomes multi-session the moment the operator clears, and that decision arrives mid-flight. Never decline or skip steps because the plan "wasn't meant to span sessions."

1. **Read in the Mode 2 step 1 order** — master, then the NEXT phase's shard in full, then referenced specs. The shard IS the recorded map; the handoff's job is to confirm and extend it, not re-derive it, and not to restate it (the shard already holds it).
2. **Gap-check the next phase's recorded map against the CURRENT code.** Confirm each symbol anchor exists (re-tag line hints with the current commit id), then enumerate as explicit addenda everything the code shows that the plan + spec do NOT record — especially drift introduced by phases shipped after the spec was written (a helper that gained side effects, a signature that changed, an adjacent modifier that looks like part of the region being extracted but isn't). The gap-check is the point of this mode; a handoff that only reformats the spec reproduces the lossy-compression failure this mode exists to prevent.
3. **Run the reuse check for any new file/helper the phase creates**: does an equivalent already exist anywhere in the codebase? What naming convention does the target directory use?
4. **Write the gap-check findings back into the shard, then hand off the shard pointer.** The NEXT phase's shard is already the self-sufficient packet — do NOT restate it into a separate handoff document; that re-creates the lossy-compression failure this mode exists to prevent. Instead: write every addendum from step 2 (drift, new call sites, signature changes) directly into the shard's Work / Decisions & findings, so the shard stays the single source of truth. The line the operator sends to the next session is just the pointer: `Read plans/<slug>-<phase>.md and execute it.` Only when there's briefing that genuinely doesn't belong in the durable shard (operator-specific context, a one-time instruction) do you write a separate `plans/handoffs/<slug>-<phase>.md` with a leading "read the shard FIRST" instruction — and that file is deleted once the phase commits. Writing back into the shard IS a plan-file edit, governed by step 5.
5. **Plan-file edits depend on who's running this mode.** If you are NOT the executing session (briefing a plan another session owns), never edit the plan file — concurrent edits confuse the owner; stale-plan findings ride in the prompt's write-back instruction instead. If you ARE the executing session (generating your own handoff before a context clear), bring the plan file current FIRST — run the Phase Completion Cycle step-5 refresh if the just-finished phase hasn't had one — then emit the prompt. The file and the prompt must agree; the prompt is for the next session's chat, the file is for every session after that.

### Mode 5: Abandon a superseded plan (`/plan abandon <slug>`, or the user says a plan is dead, superseded, or won't ship)

Shipped is not the only exit. Without this mode, dead plans linger in `plans/` where Mode 2 will happily resume them.

1. **Confirm with the user** which plan dies and why — one sentence.
2. **Prepend a banner** to the master (single-phase plan: the one file): `> **ABANDONED <date>:** <reason>. <Superseded by plans/<other>.md | No successor.>`
3. **Walk the Parking lot** the same way Mode 3 does — parked items in a dead plan are still ideas the user chose to keep. Disposition each one (file as follow-up / drop with reason) before archiving.
4. **Move the master AND every shard** to `plans/archive/` (create it if needed), `git mv` for tracked files, plain `mv` otherwise. Same move-together rule as Mode 3 — never leave orphaned shards in `plans/`. Sweep `plans/handoffs/<slug>-*.md` and any `plans/RESUME-<slug>.md` briefing (+ its `plans/RESUME.md` index line) the same way Mode 3 step 5 does — transient briefings die with the plan.

`plans/archive/` holds dead plans; `plans/shipped/` holds completed ones. Mode 2 never resumes from either.

---

## Plan Template

Two shapes, chosen by a **mechanical rule — NOT a judgment call.** Count the phases: **exactly one phase → the single-file plan. More than one phase → master + one shard per phase** (the no-exceptions wording lives in Rules: "Multi-phase plans MUST shard"). The Scope Check, Phase Completion Cycle, and Rules below refer to these section headers by name — don't rename them.

### Single-phase plan — the ONLY non-sharded shape

One file, `plans/<slug>.md`:

```markdown
# <feature name>

## Status  *(approval marker + mid-work cold-start; refresh whenever work pauses)*
**Approval: DRAFT**  *(flip to `APPROVED <date>` at approval — code never starts while this reads DRAFT)*
<Once work starts: what's done, what's in flight, what's next — plus any
"verified this session" facts and line-hint staleness notes a resuming session
needs. A single-phase plan interrupted mid-work re-enters through this section.>

## Goal
<1-2 sentences. Concrete, not aspirational.>

## Diagnosis  *(required for perf/bug/regression plans; omit for greenfield)*
- **Hypothesis:** <the suspected cause, named concretely>
- **Falsifiable test:** <one-line experiment that CONFIRMS or DISPROVES before scoping Work>
- **Test result:** <run it; record it; if disproved, STOP and return to Mode 1 step 4>

## Non-goals
- <explicit thing we're NOT doing — prevents scope creep>

## Work
- path/to/file.ext — <what changes here>

## Decisions & findings  *(only if the research pass settled a non-local decision — entry shape per the shard template below)*

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
<Which phases are SHIPPED (commit ids), which is NEXT. The NEXT phase's shard
IS the self-sufficient packet — name it with a LEADING "read
`plans/<slug>-<phase>.md` FIRST" instruction, not a trailing citation. Then pull
that phase's 2-3 binding decisions inline here, so a compaction that drops the
shard from context still leaves the decisions visible.>

## Goal
<1-2 sentences. Concrete.>

## Diagnosis  *(required for perf/bug/regression plans; omit for greenfield)*
- **Hypothesis / Falsifiable test / Test result** — run the test before scoping any shard.

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

## Work
- path/to/file.ext — <what changes here, this phase>

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
- **Status / Status & cold-start**: Both shapes carry one — the single-phase `## Status` holds the Approval marker plus mid-work progress (it's the cold-start anchor when a single-phase plan pauses mid-work); the master's `## Status & cold-start` adds the phase bookkeeping below. Required for any plan with more than one phase. There is no "designated multi-session plan" — whether a session gets cleared mid-plan is the operator's call, made when context gets hot, *after* a phase commit, never at plan approval. A rule gated on "will this span sessions?" is a rule a session can self-certify out of right up until the clear happens — so every multi-phase plan is treated as multi-session by default. With sharding, **the NEXT phase's shard IS the self-sufficient packet** — the master's Status section names which phase is NEXT, leads with a "read `plans/<slug>-<phase>.md` FIRST" instruction, and pulls that phase's 2-3 binding decisions inline so a compaction dropping the shard still leaves them visible. Apply the self-sufficiency test to the shard at every refresh: "could a fresh session list every input, output, call site, and delegated behavior of this phase from the shard alone?" Summarizing lossily is the bug this prevents — restate the inventory in the shard, don't compress it by vibes.
- **Goal**: What has the user stated as the objective? Don't editorialize or expand scope.
- **Diagnosis**: Required when the plan exists to *change* something already running — performance regressions, bug fixes, "make X faster/smaller/cheaper", "stop Y from happening", "investigate Z". Skip for greenfield features (new code where there's no existing behavior to diagnose). The hypothesis names the suspected cause concretely (a function, a flag, a code path), not vaguely ("something is slow"). The falsifiable test is a one-line experiment runnable in seconds — comment out a call, set an env var, add a log. **Run it before scoping any shard's Work.** If the test disproves the hypothesis, the rest of the plan is built on sand — return to Mode 1 step 4 (Explore) with the negative result as a sharper question, don't ship the wrong fix. The cost of running a 30-second diagnostic test is far less than the cost of implementing, reviewing, testing, and committing a plan against the wrong target.
- **Non-goals**: Things the user has explicitly said to NOT do, OR things that are natural adjacent work that we're deliberately deferring. This is the most important section for ADHD — aggressive non-goals prevent drift. **But: when a non-goal excludes some members of a peer set (some op types, some endpoints, some entities, some files in a related family), include a one-sentence justification of why the exclusion is safe.** If you can't write that justification, the exclusion is probably the bug — promote the excluded items into scope or restructure the plan to avoid the asymmetry. Asymmetric changes across peers in the same dependency graph open race, ordering, or stale-state hazards that don't appear in unit tests but bite in production. Default to *including*; require concrete justification to exclude.
- **Work / Files touched**: Always concrete, full stop. Naming every file is the codebase-EXPLORE dimension's job — keep exploring until you can list them; never list "every file that might be relevant" and never leave a `TODO: investigate` placeholder. If you genuinely can't determine the list without operator input (external access, a running app), that's a block — STOP and resolve it before drafting, don't draft around a hole. **In a multi-phase plan the per-file work detail lives in each shard's `## Work`; the master's `## Files touched (overview)` is the coarse, phase-tagged conflict-spotting map only — never duplicate the per-file detail up into it.** In a single-phase plan the one `## Work` section carries it. This is *plan-time* completeness; discovering an additional file *while implementing* is a different thing, governed by the Scope Check "Before touching a file" add/park/skip rule.
- **Decisions & findings (in the shard)**: One entry per **non-local** decision (the threshold rule in Rules); each entry is Decision / Rationale / Alternatives considered / Evidence (file:line or URL+section). Mark a decision a later phase invalidates as `superseded by phase-<id>` — don't silently rewrite it. Append empirical findings (spike results, mid-implementation gotchas) here as the phase runs; this is what stops the next session rediscovering them after a clear. (Where research lives — inside the shard, never a separate file — is its own rule in Rules.)
- **Background findings (master only)**: ONLY cross-phase research that belongs to no single phase. Anything scoped to one phase belongs in that phase's shard, not here. This is the one research home in the master, and it never grows per-phase detail.
- **Failure modes**: Aim for 5+. If you have fewer than 3, you don't understand the problem yet. Draw from: similar past failures, platform quirks, unfamiliar dependencies, integration boundaries, untested paths. **But this section is not a sink for unresolved verifications.** A conditional whose antecedent is statically checkable ("*if* warmups can't be marked complete…"; "*if* this endpoint doesn't return X…") is a fact you didn't look up wearing risk's clothing: resolve the antecedent during EXPLORE, then either delete the entry or restate it as the verified fact — the verify-or-block rule bites on the antecedent, not just on declarative claims. A legitimate failure mode is one whose outcome remains uncertain *after* you've verified everything statically knowable about it.
- **Done criteria**: These are the exit conditions. When met, STOP — no polish, no adjacent improvements. Each criterion must be concrete and verifiable. In a multi-phase plan there are **two levels**: each shard's Done criteria are that phase's commit-level exit, and the master's Done criteria are the plan-level, cross-cutting exits (whole-feature suite green, deployed/pushed, end-to-end result) that belong to no single phase. The master's level is NOT a copy of the per-phase criteria — restating them there is the duplication to avoid; it holds only what spans phases. The plan is done when every shard's criteria AND the master's are met.
- **Parking lot**: Always start empty. The skill never pre-populates it.

---

## Scope Check Behavior (while working the plan)

Once a plan is approved, these behaviors kick in for the rest of the session:

- **Before touching a file**: compare the file path to the current phase's shard `## Work` (single-phase plan: the one `## Work` section). If it's not listed:
  - STOP, ask the user: "This file wasn't in the phase — add it, park it, or skip it?"
  - If they say add, edit the shard's Work to include it (explicit mutation), and the master's Files touched (overview) if it's a new file for the plan
  - If they say park, append to the master's parking lot with a one-line note
  - If they say skip, move on without touching it

- **Checkpoint at meaningful boundaries** — when a done criterion is met, when a file's changes are complete, and at every phase boundary: briefly state where we are in the plan. Example: "Done criterion 2 of 4 met. Files touched: install.sh, test_install_sh.py. Still in scope." (Not per-tool-call — a phase often runs dozens of calls, and per-call status is noise.)

- **When the user suggests something new mid-work**: ask whether it replaces current scope, extends it (update plan), or parks it (parking lot). Default to parking lot unless they explicitly want to expand.

- **Commit per phase**: each phase ends with the Phase Completion Cycle below — the commit happens at step 4 of that cycle, not as a separate decision. Don't batch phases unless they're trivially small (e.g. two constant changes).

- **When done criteria are met**: the cycle's stop condition kicks in — stop, commit final state, offer to ship the plan (`/plan ship <slug>`). Do not start adjacent work without a new plan.

---

## Phase Completion Cycle

Once the plan is approved, every phase ends by running this cycle in order. **The cycle is the default behavior — do not skip steps and do not wait for the user to prompt the next one.** The user has explicitly authorized this loop by approving the plan.

1. **Code** — implement the phase against its shard's `## Work` entries (single-phase plan: the one `## Work`). Stay in scope; if a non-listed file needs editing, fall back to the "Before touching a file" rule.
2. **Simplify** — run your project's review pass (`/code-review` or an equivalent) on the changed code. **Trivial-diff escape hatch**: skip it only if the diff is single-file AND single-logical-change AND has no behavior change (typo fix, version bump, comment rewording, doc-only edit). When in doubt, run it. Then run any additional review gates the project's own instructions (CLAUDE.md or equivalent) mandate for this diff type — UI review passes, screenshot evidence, lint gates. Project gates compose with the review pass; they don't replace it, and the escape hatch above does not waive them.
3. **Test** — run the project's canonical pre-commit test gate: the full suite, unless the project's own instructions define the gate for this diff type. A green subset the project's gate doesn't sanction is not green. If tests fail, fix before proceeding. Never commit red.
4. **Commit** — one commit per phase with a descriptive message that ties back to the plan / done criterion. Use `Fixes #N` if the phase closes an issue. If a handoff file `plans/handoffs/<slug>-<phase>.md` exists for the just-committed phase, delete it now — its lifetime ends at this commit (Mode 4 step 4 states the contract; this step is its enforcer).
5. **Write findings into the shard, then refresh the map** — on any multi-phase plan, before advancing, do BOTH, in this order, and do NOT skip either:
   - **(a) Seal the just-finished phase's shard.** Write its `## Decisions & findings` to final state: every non-local decision it settled (Decision / Rationale / Alternatives / Evidence) and **every empirical finding the phase actually discovered** — spike results, gotchas, anything the next phase or a cold session would otherwise rediscover. This is the only moment the context that produced those findings still exists; the session that just finished the phase is the last one holding it. A finding left in your head and not in the shard is a finding lost at the next clear — that is the failure this step exists to stop.
   - **(b) Advance the master.** Mark the just-shipped phase in the Phase map and Status (commit id), promote the next phase to NEXT, lead Status with the "read `plans/<slug>-<phase>.md` FIRST" pointer, and pull that next shard's 2-3 binding decisions inline. Confirm the next shard still passes the self-sufficiency test against current code; update any line-number hints to the just-committed state and tag them with the commit.

   This runs at EVERY phase commit, not only when a session handoff is known to be coming — the operator's decision to clear the session arrives after the commit, not before, so findings written only when a handoff is foreseen are missing exactly when they're needed. Skip only when the phase just committed was the plan's last (the plan ships instead).
6. **Advance** — if any done criteria are still unmet, **immediately** start the next phase. State a one-line status update ("Phase 2/4 done, starting phase 3") — this is a *status*, not a question. Never ask "should I continue?" — the approved plan is the standing authorization.

### Stop conditions (override step 6)

The cycle stops — and you wait for the user — only when one of these is true:

- **All done criteria met** (every shard's AND the master's plan-level) → commit the final state, then offer `/plan ship <slug>`.
- **Blocker requires a decision** → ambiguous spec, broken external dep, conflict with a non-goal, or a question the plan didn't pre-answer. Surface the specific question; don't keep advancing.
- **Scope drift detected** → a file outside the current shard's `## Work` needs editing, or the work has expanded past the plan's bounds. Use the "Before touching a file" rule (add / park / skip).
- **Tests stay red after a reasonable fix attempt** → don't loop indefinitely; surface the failure and ask.
- **User interrupts** → defer to user input, then resume from wherever the cycle was.

If none of the stop conditions apply, the next phase starts automatically.

---

## Rules

- **Multi-phase plans MUST shard — this is mechanical, not a judgment call.** More than one phase → a master `plans/<slug>.md` PLUS one shard `plans/<slug>-<phase>.md` per phase; the single-phase plan is the ONLY non-sharded shape. There is NO "small multi-phase" exception, NO "it reads cleaner as one document," NO "fewer files is simpler" — two phases shard, six phases shard. The instant you notice you're about to put a second phase's work in the first phase's file, STOP and shard; simplicity is conceptual load (one phase = one self-contained shard), never file count. (The monolith failure this stops is described at the multi-phase template intro.)
- **A phase's research and decisions live INSIDE that phase's shard — never elsewhere.** Never a separate `<slug>-research.md` or `<slug>-<phase>-research.md`; never only in the master. The shard's `## Work` is the execution brief, its `## Decisions & findings` is the durable record (Decision / Rationale / Alternatives / Evidence, with `superseded by phase-<id>` markers). A separate research file is the monolith by another name and breaks the shard's self-containment — the one property the whole layout buys. The ONLY research in the master is cross-phase Background findings that belong to no single phase.
- **The Phase map is mandatory for every multi-phase plan and carries EDGES, not nodes.** Every phase listed with enter-gate, done-signal, branch-on-failure, and shard path. It is NOT where work is detailed — that's each shard's Work. A multi-phase master with no Phase map is incomplete and MUST NOT be presented for approval; a Phase map that restates shard Work detail is the duplication it exists to prevent.
- **A decision earns a `## Decisions & findings` entry only when its consequences are NON-LOCAL** — scattered across the code, not contained in one spot. Local one-liners stay in the master's Non-goals. This threshold is what keeps "robust" from meaning "everything written twice"; apply it, don't record every passing choice.
- **/plan adopts clu-plan's master+shard STRUCTURE, never its dispatch machinery.** /plan is project-agnostic. Do NOT write a machine-parsed `## Sessions index` table, an `Effort`/lease column, attestation steps, or `clu complete` callbacks into a /plan plan — those are clu-specific. The Phase map is the generic phase index. If you catch yourself writing a Sessions-index table into a /plan plan, you've leaked clu specifics into the generic skill — stop and use the Phase map.
- **Never write code before the plan is approved.** Not even "just to set up scaffolding." The plan is the scaffolding.
- **One active plan per conversation.** If the user wants to work on two things, they get two plans, and we tackle them sequentially.
- **The template is the source of truth** — there are two shapes (single-file plan; multi-phase master + per-phase shard), and the Scope Check, Phase Completion Cycle, Mode 2/3/4/5, and Rules all refer to their section names. Don't add, remove, or rename a section in either shape without updating every one of those references.
- **Be ruthless about non-goals.** If you're unsure whether to list something as a non-goal, list it. Easier to remove than to add mid-work.
- **Archive, don't delete.** Shipped plans move to `plans/shipped/` — they're a record of what got done, not garbage to collect.
- **Anchor on symbols, not line numbers.** Plans and specs with more than one phase must use symbol names (functions, types, properties) and distinctive code snippets as their primary anchors — every committed phase shifts the line numbers the next phase's notes cite, so raw `:NNN` references rot by design. Line numbers are allowed only as secondary hints tagged with the commit they were measured at, and the cold-start refresh (Phase Completion Cycle step 5) restates them when they drift. A fresh session re-anchors by grepping the symbol, never by trusting a stale line.
- **A resumed plan is a hypothesis, not a license.** Validate per Mode 2 step 2 before working any pre-existing plan; if scope or approach has materially diverged, re-run Mode 1 step 4 EXPLORE and rewrite — never patch a stale plan turn-by-turn while coding. The re-plan triggers (including the smaller-diff approach switch) are enumerated in Mode 2 step 2 — that list is the rule.
- **EPCC: Explore is unconditional.** EPCC = Explore → Plan → Code → Commit. The Explore step (Mode 1 step 4) runs every time, before a single line of plan text gets drafted. All three mandatory dimensions — codebase, project-local API docs + canonical samples, web prior art — run regardless of plan size, file count, or category. There is no "small plan" exception, no "I already read the files" escape, no `--no-research` opt-out, no "pure docs/config" carve-out. If a task is genuinely too trivial to warrant exploration, it's too trivial to warrant `/plan` in the first place — do it directly.
- **No research deferrals — verify or block.** Every cited file path, API name, metadata key, version number, framework behavior, or external system claim is verified this session and stated as fact with a file:line or URL+section citation — or the plan isn't drafted. There is no `TODO: verify` channel, no "confirm during implementation," no placeholder, no flag opt-out, no carve-out (same unconditional standard as EPCC). If a claim can't be closed by research, STOP and resolve it with the operator before drafting (provide access, run it, or pull it from scope). The guess/fact line is enforced by absence: every claim in a drafted plan is verified — not "unmarked," verified. Empirical unknowns that genuinely need runtime are not deferrals — they become the Diagnosis falsifiable test or the algorithmic load-test, which run as part of execution.
- **Generic-skill discipline.** This skill is global — it ships across every project regardless of language or framework. Skill text MUST NOT hardcode paths, language conventions, or specific framework names beyond illustrative examples. Each agent's brief describes the *shape* of what to look for; the agent figures out where this project's equivalent lives (e.g., `node_modules/<dep>` for JS, `~/.cargo/registry/src/` for Rust, `site-packages` for Python, vendored docs folders for any project, the vendor's official docs site via WebFetch for any language). If you find yourself writing a project-specific path or framework name in the skill body, replace it with the generic shape and an illustrative example list.
- **Mid-implementation pivot rule.** If the first diagnostic experiment under an approved plan disproves the hypothesis (e.g. "I disabled X and the symptom didn't change"), STOP. Don't try a second guess. Return to Mode 1 step 4 (Explore) with the new evidence as a sharper question — the plan was scoped at the wrong target and patching it forward will compound the error. Two failed disable-experiments back-to-back is a hard signal to re-explore; if the symptom is genuinely opaque after that, hand off to a dedicated diagnosis pass.
- **New file mirrors existing file? Refactor first by default.** When the plan adds a new file the description says "mirrors" / "like" / "similar to" / "same family as" an existing one — OR a sibling file with the same suffix already exists in the target directory — the reuse-specialist agent is mandatory during Explore and its Phase-0-refactor recommendation is presumed correct unless the user explicitly overrides at plan approval. The refactor becomes phase 0 of the plan; the new feature is phase 1+. Copy-and-defer requires an explicit user override at approval, recorded in the Parking lot in writing — not a passive default that quietly leaves duplication for a review pass to surface after the duplicate ships.
- **Algorithmic plans: land the research load-test at the earliest practical phase, not "whenever it's convenient."** The minimum executable test that would catch a naive implementation (research's question 3) is the falsifiable claim that proves the research is grounded. The default placement is phase 1's first commit, *before* the rest of phase 1 — the test runs against the simplest possible implementation and gates further work. If the test genuinely cannot be run until phase 2 (e.g. it needs integration plumbing that doesn't exist yet, or the LLM pipeline only behaves under realistic load), that's allowed, but the plan must explicitly call out the gap and the test still becomes the *first thing* in phase 2, not buried mid-phase. If the test fails when it lands, the research was incomplete — return to Mode 1 step 4 (Explore) with the specific failure mode as a sharper question, don't paper over it with tuning. This catches "research was insufficient" at phase 1-2 instead of phase 3+.
- **Perf/bug plans: run the Diagnosis falsifiable test BEFORE scoping any shard's Work.** Protocol per the Diagnosis template commentary (confirmed → scope normally; disproved → back to Mode 1 step 4 with the negative result as the sharper question). Files-read alone doesn't ground a diagnosis; "I commented out X and the symptom didn't change" does. Escalation after repeated failed experiments is the Mid-implementation pivot rule above.
- **Justify non-goal exclusions across peer sets.** Every peer-set exclusion needs the one-sentence "why this asymmetry is safe" rationale per the Non-goals template commentary — if you can't write it, fold the excluded items in. The exclusion-safety specialist (Mode 1 step 4) surfaces this as a forced binary decision at approval; trust its default-include recommendation unless you have an iron-clad invariant.
