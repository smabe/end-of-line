---
name: plan
description: Create, resume, or ship a one-screen plan file in plans/ before starting multi-file work. Keeps ADHD scope creep in check by making scope explicit and approved upfront.
user_invocable: true
---

## Plan Workflow

This skill enforces a "plan before code" discipline: for any non-trivial multi-file change, write a one-screen plan to `plans/<slug>.md` and get user agreement before coding. The template and rules below are the authoritative source — this file is self-contained on purpose so it works in any project, including fresh clones that don't have your auto-memory loaded. Why this discipline exists: without an explicit plan, work drifts ("while I'm here" fixes turn a 2-file change into a 7-file commit); the plan file is the anti-drift contract. Trivial changes (typo fixes, single-file tweaks, obvious bug fixes that commit in under 5 minutes) skip the plan flow.

The skill has three modes, auto-detected from context:

### Mode 1: Create a new plan (`/plan <slug>` — no existing file)

1. **Normalize the slug**: lowercase, hyphens for spaces, strip non-alphanumeric except hyphens. Example: "Pipeline Hardening!" → `pipeline-hardening`.
2. **Find the project's plans/ directory.** Start from the current working directory, walk up to the git root if needed. If `plans/` doesn't exist at the git root, create it.
3. **Check for an existing file at `plans/<slug>.md`.** If it exists, switch to Mode 2 (resume).
4. **EXPLORE — mandatory, no skip conditions, no flag opt-out.** This is the E in EPCC (Explore → Plan → Code → Commit). The plan is *not* drafted until exploration completes. There is no "small plan" exception, no "I already read the files" escape, no `--no-research` opt-out, no "pure docs/config" carve-out. Codebase + project-local API docs + web prior art — all three dimensions, every time, before a single line of plan text gets written.

   **Hand-off to `/diagnose` for hard diagnostic cases.** If the symptom is genuinely opaque (no obvious hypothesis, multiple plausible causes, intermittent reproduction), the plan should defer to `/diagnose` for the diagnosis phase. /plan + /diagnose compose: run /diagnose to find the cause, then /plan to scope the fix. Don't try to do disciplined diagnosis inside a /plan flow — the skills are sized for different jobs.

   **Three mandatory research dimensions.** Each gets its own dedicated agent. Dispatch all agents in a single message using the Agent tool so they run in parallel — see graphify's SKILL.md step B2 for the pattern.

   1. **Codebase / internal exploration** — `subagent_type: "Explore"` against the project files. Existing helpers, callers, conventions, file sizes, naming patterns, test coverage of the surface being changed. Brief: "Map the area this plan will touch. List existing helpers we should reuse instead of reimplementing. List callers of any function we're changing. Quote file:line for every claim."

   2. **Project-local API documentation + canonical samples** — `subagent_type: "general-purpose"`. For whatever dependencies this project uses, surface the framework's official guidance + working code patterns. The agent figures out where this project's docs live (vendored docs folders, build-output docs, library README + examples in `node_modules` / `~/.cargo/registry/src/` / `site-packages` / `Pods`, framework headers, generated `.d.ts` files, etc.) and fetches from the vendor's official docs site when no local copy exists. Brief: "What does the framework's canonical pattern for this problem look like? Where are working examples in the project's dependencies or in vendor sample repos? What footguns does the doc itself call out? Cite file:line for local sources or URL+section for fetched docs."

   3. **Web prior art / community evidence** — `subagent_type: "general-purpose"` with WebSearch + WebFetch. Brief: "How are others in this language / framework / domain solving this problem? Stack Overflow threads, GitHub issues on relevant libraries, recent blog posts, conference talks. Bring back canonical patterns, recent gotchas, and links. Vendor docs are routinely incomplete or describe an intended contract that doesn't match shipped reality — independent corroboration is the point of this dimension. Cite URLs for every finding."

   **These three are non-negotiable** for any plan that touches code, regardless of plan size, file count, or category. The skill is global — it ships across every project and every language. Skill text MUST NOT hardcode paths, language conventions, or specific framework names beyond illustrative examples. Each agent's brief describes the *shape* of what to look for; the agent figures out where this project's equivalent lives.

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
   - **Security review** (handled by /security-review skill, but as illustration): threat-model-and-attack-surface agent · authn-and-authz agent · data-handling-and-privacy agent · dependency-and-supply-chain agent.
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

   This separation prevents the failure mode that motivated this rule: a plan describes a new window as "mirroring" an existing one, the layout agent confirms "yes the styling matches," code gets written as a parallel implementation, and the base-class extraction lands as a parking-lot follow-up *after* the duplication ships and after a `/code-review` pass surfaces it. The right move is refactor-then-extend, not extend-then-refactor.

   **For plans whose Non-goals will exclude some members of a peer set** (signals: the user's framing names a subset like "not touching Class B", "logSet only, not the other ops", "endpoint A but not B which feeds it"; OR the in-scope items and excluded items share a queue, cache, FIFO contract, applied-token set, ordering relationship, or other coupling), one of the agents MUST be the **exclusion-safety specialist** briefed explicitly:
   > "The plan will exclude [the excluded items] from a change being applied to [the included items]. Read both groups. List every dependency between them — shared state, shared queues, shared caches, ordering / FIFO contracts, applied-token sets. Walk through whether the asymmetric mix opens a race, ordering, or stale-state hazard under realistic call sequences (rapid-fire taps, dial-then-tap, A-before-B-but-B-arrives-first, etc.). Cite file:line. Recommend ONE of two paths:
   > (a) **Fold excluded into scope** — apply the change uniformly across the peer set
   > (b) **Keep exclusion + document explicit invariant** — the invariant must make the asymmetry provably safe, not just plausible
   > Default to recommending (a) unless the operator has explicitly rejected it OR the invariant is iron-clad and short enough to fit in one sentence. Your recommendation gets surfaced as a forced binary decision the user must make at plan approval — don't soften it."

   This separation prevents the failure mode that motivated this rule: a plan applies a new transport / mechanism to some ops in a peer set but excludes others as a "non-goal," and the excluded ops' slower delivery races the included ops' faster delivery, committing dependent state in the wrong order.

   **Brainstorm skill note**: if exploration surfaces significant scope questions the user can't decisively answer (e.g. "should we refactor this whole subsystem or just patch it?"), suggest the user invoke `/brainstorm` as a separate step before re-running `/plan`. Do NOT invoke brainstorm inline from within this skill — it's user-interactive and doesn't compose cleanly.

   **Consolidate findings as ground truth for the plan** (internal step; not surfaced to the user as a separate report). Walk away from exploration with:
   - The corrected understanding of the area being changed (codebase shape + canonical API patterns + community gotchas)
   - Any forced binary decisions surfaced by reuse / exclusion specialists, with the specialist's recommended option
   - Any load-bearing implementation details surfaced by the algorithmic specialist (for algorithmic plans)
   - A list of claims the research could NOT verify — these become `TODO: verify <claim>` markers in the plan draft, never written as fact.

5. **Draft the plan** using the template below, with research findings as ground truth. Three rules for drafting:
   - **Every factual claim in the plan must be supported by exploration findings.** Cite file:line / URL+section in the plan body where the claim depends on a specific verified source.
   - **TODO markers are mandatory for unverified specifics.** Any cited file path, API name, metadata key, version number, framework behavior, or external system claim must either be verified during exploration (then stated as fact) or written as `TODO: verify <specific thing>` (never as fact). The line between "extrapolation from training data" and "verified from this session's research" must always be visible in the plan file.
   - **Bake forced binary decisions into the plan as the recommended option.** If the reuse specialist recommended a Phase 0 refactor, draft the plan with a Phase 0 refactor included. If the exclusion specialist recommended folding excluded items into scope, draft the plan with them included. The decision still gets surfaced explicitly at approval (see step 7) so the user can override, but the plan reflects the recommendation by default.

6. **Write the file** to `plans/<slug>.md`.

7. **Show the plan to the user, surfacing any forced binary decisions explicitly:**
   > Here's the plan. Read it over and approve / tweak / reject. I won't write any code until you say it's live.
   >
   > [If reuse specialist surfaced a decision]
   > **Reuse decision baked in:** plan adopts Phase 0 refactor of `<duplicated surface>` based on `<file:line>` evidence. If you'd prefer copy-and-defer (ship the duplication, file the dedupe as follow-up), say so and I'll restructure.
   >
   > [If exclusion specialist surfaced a decision]
   > **Exclusion decision baked in:** plan folds `<excluded items>` into scope based on `<file:line>` dependency on `<included items>`. If you'd prefer to keep the exclusion, give me the one-sentence invariant that makes the asymmetry safe.
   >
   > [If any TODO markers remain in the plan]
   > **Unverified claims still in the plan:** `<list of TODO markers>`. I couldn't verify these during exploration — flagging so you can sanity-check before approving.

8. **Block on user response.** Do not touch any code files, do not run any builds or tests, until the user explicitly approves. If the user picks copy-and-defer for a reuse decision, append the deferred refactor to the plan's Parking lot in writing before code starts.

9. **Once approved, enter "working the plan" mode**: reference the plan on every file touch, interrupt scope creep, append to the parking lot when the user drops a shiny idea mid-work.

### Mode 2: Resume an existing plan (`/plan <slug>` — file exists)

1. **Read the plan file** at `plans/<slug>.md`.
2. **Summarize the state** to the user: goal, done criteria, what's in the parking lot, how much is done vs remaining.
3. **Ask what the user wants**: continue working it, update the plan, or ship it (archive to `plans/shipped/`).

### Mode 3: Ship a finished plan (`/plan ship <slug>` or user says "ship the plan")

1. **Verify done criteria are actually met.** Read the plan file and walk through each done criterion — ask the user to confirm any ambiguous ones. If criteria aren't met, refuse and say what's still outstanding.
2. **Create `plans/shipped/`** if it doesn't exist.
3. **Move** `plans/<slug>.md` → `plans/shipped/<slug>.md` using `git mv` if the file is tracked, plain `mv` otherwise.
4. **Confirm to the user** with the new path and a one-line summary of what shipped.

---

## Plan Template

Use this exact structure. The Scope Check and Phase Completion Cycle below refer to these section headers by name — don't rename them.

```markdown
# <feature name>

## Goal
<1-2 sentences stating what this plan is trying to accomplish. Concrete, not aspirational.>

## Diagnosis  *(required for perf/bug/regression plans; omit for greenfield features)*
- **Hypothesis:** <what specifically is causing the symptom — e.g. "X function is the hot path" / "Y handler swallows the error">
- **Falsifiable test:** <a one-line experiment that would CONFIRM or DISPROVE the hypothesis before we touch the file list — e.g. "comment out X.start() and observe Z drops to ≤K", "add a log at line N and confirm it fires before Y">
- **Test result:** <run it. Record what you observed. If the test disproves the hypothesis, STOP — return to Mode 1 step 4 (Explore) with a sharper question, do not draft "Files to touch" yet>

## Non-goals
- <explicit thing we're NOT doing — prevents scope creep>
- <another boundary>

## Files to touch
- path/to/file1.ext — <what changes here>
- path/to/file2.ext — <what changes here>

## Failure modes to anticipate
- <thing that could break, unfamiliar territory, known gotcha>
- <edge case>
- <integration risk>

## Done criteria
- <concrete exit condition — how we know we're actually finished>
- <another exit condition>

## Parking lot
(empty at start — append "ooh shiny" thoughts that surface mid-work for later)
```

### Filling in the template from conversation context

- **Goal**: What has the user stated as the objective? Don't editorialize or expand scope.
- **Diagnosis**: Required when the plan exists to *change* something already running — performance regressions, bug fixes, "make X faster/smaller/cheaper", "stop Y from happening", "investigate Z". Skip for greenfield features (new code where there's no existing behavior to diagnose). The hypothesis names the suspected cause concretely (a function, a flag, a code path), not vaguely ("something is slow"). The falsifiable test is a one-line experiment runnable in seconds — comment out a call, set an env var, add a log. **Run it before scoping `Files to touch`.** If the test disproves the hypothesis, the rest of the plan is built on sand — return to Mode 1 step 4 (Explore) with the negative result as a sharper question, don't ship the wrong fix. The cost of running a 30-second diagnostic test is far less than the cost of implementing, reviewing, testing, and committing a plan against the wrong target.
- **Non-goals**: Things the user has explicitly said to NOT do, OR things that are natural adjacent work that we're deliberately deferring. This is the most important section for ADHD — aggressive non-goals prevent drift. **But: when a non-goal excludes some members of a peer set (some op types, some endpoints, some entities, some files in a related family), include a one-sentence justification of why the exclusion is safe.** If you can't write that justification, the exclusion is probably the bug — promote the excluded items into scope or restructure the plan to avoid the asymmetry. Asymmetric changes across peers in the same dependency graph open race, ordering, or stale-state hazards that don't appear in unit tests but bite in production. Default to *including*; require concrete justification to exclude.
- **Files to touch**: Be specific. If you don't know, write `TODO: investigate` rather than listing every file that might be relevant.
- **Failure modes**: Aim for 5+. If you have fewer than 3, you don't understand the problem yet. Draw from: similar past failures, platform quirks, unfamiliar dependencies, integration boundaries, untested paths.
- **Done criteria**: These are the exit conditions. When met, STOP — no polish, no adjacent improvements. Each criterion must be concrete and verifiable.
- **Parking lot**: Always start empty. The skill never pre-populates it.

---

## Scope Check Behavior (while working the plan)

Once a plan is approved, these behaviors kick in for the rest of the session:

- **Before touching a file**: compare the file path to "Files to touch" in the plan. If it's not listed:
  - STOP, ask the user: "This file wasn't in the plan — add it, park it, or skip it?"
  - If they say add, edit the plan to include it (explicit mutation)
  - If they say park, append to the parking lot with a one-line note
  - If they say skip, move on without touching it

- **Checkpoint every 3-5 tool calls**: briefly state where we are in the plan. Example: "We're on done criterion 2 of 4. Files touched: install.sh, test_install_sh.py. Still in scope."

- **When the user suggests something new mid-work**: ask whether it replaces current scope, extends it (update plan), or parks it (parking lot). Default to parking lot unless they explicitly want to expand.

- **Commit per phase**: each phase ends with the Phase Completion Cycle below — the commit happens at step 4 of that cycle, not as a separate decision. Don't batch phases unless they're trivially small (e.g. two constant changes).

- **When done criteria are met**: the cycle's stop condition kicks in — stop, commit final state, offer to ship the plan (`/plan ship <slug>`). Do not start adjacent work without a new plan.

---

## Phase Completion Cycle

Once the plan is approved, every phase ends by running this cycle in order. **The cycle is the default behavior — do not skip steps and do not wait for the user to prompt the next one.** The user has explicitly authorized this loop by approving the plan.

1. **Code** — implement the phase against the plan's "Files to touch" entries. Stay in scope; if a non-listed file needs editing, fall back to the "Before touching a file" rule.
2. **Simplify** — run `/code-review` on the changed code. **Trivial-diff escape hatch**: skip `/code-review` only if the diff is single-file AND single-logical-change AND has no behavior change (typo fix, version bump, comment rewording, doc-only edit). When in doubt, run it.
3. **Test** — run the project's test suite (or the relevant subset for the changed area). If tests fail, fix before proceeding. Never commit red.
4. **Commit** — one commit per phase with a descriptive message that ties back to the plan / done criterion. Use `Fixes #N` if the phase closes an issue.
5. **Advance** — if any done criteria are still unmet, **immediately** start the next phase. State a one-line status update ("Phase 2/4 done, starting phase 3") — this is a *status*, not a question. Never ask "should I continue?" — the approved plan is the standing authorization.

### Stop conditions (override step 5)

The cycle stops — and you wait for the user — only when one of these is true:

- **All done criteria met** → commit the final state, then offer `/plan ship <slug>`.
- **Blocker requires a decision** → ambiguous spec, broken external dep, conflict with a non-goal, or a question the plan didn't pre-answer. Surface the specific question; don't keep advancing.
- **Scope drift detected** → a file outside "Files to touch" needs editing, or the work has expanded past the plan's bounds. Use the "Before touching a file" rule (add / park / skip).
- **Tests stay red after a reasonable fix attempt** → don't loop indefinitely; surface the failure and ask.
- **User interrupts** → defer to user input, then resume from wherever the cycle was.

If none of the stop conditions apply, the next phase starts automatically.

---

## Rules

- **Never write code before the plan is approved.** Not even "just to set up scaffolding." The plan is the scaffolding.
- **One active plan per conversation.** If the user wants to work on two things, they get two plans, and we tackle them sequentially.
- **The template is the source of truth** — don't add or remove sections in the Plan Template above without also updating the Scope Check and Phase Completion Cycle rules, since they reference the section names.
- **Be ruthless about non-goals.** If you're unsure whether to list something as a non-goal, list it. Easier to remove than to add mid-work.
- **Archive, don't delete.** Shipped plans move to `plans/shipped/` — they're a record of what got done, not garbage to collect.
- **EPCC: Explore is unconditional.** EPCC = Explore → Plan → Code → Commit. The Explore step (Mode 1 step 4) runs every time, before a single line of plan text gets drafted. All three mandatory dimensions — codebase, project-local API docs + canonical samples, web prior art — run regardless of plan size, file count, or category. There is no "small plan" exception, no "I already read the files" escape, no `--no-research` opt-out, no "pure docs/config" carve-out. If a task is genuinely too trivial to warrant exploration, it's too trivial to warrant `/plan` in the first place — do it directly.
- **TODO markers are mandatory for unverified specifics.** Any cited file path, API name, metadata key, version number, framework behavior, or external system claim must either be verified during exploration (then stated as fact with file:line or URL+section citation) or written as `TODO: verify <specific thing>` (never as fact). The line between "extrapolation from training data" and "verified from this session's research" must always be visible in the plan file. Absence of TODO marker = positive verification, not absence of doubt.
- **Generic-skill discipline.** This skill is global — it ships across every project regardless of language or framework. Skill text MUST NOT hardcode paths, language conventions, or specific framework names beyond illustrative examples. Each agent's brief describes the *shape* of what to look for; the agent figures out where this project's equivalent lives (e.g., `node_modules/<dep>` for JS, `~/.cargo/registry/src/` for Rust, `site-packages` for Python, vendored docs folders for any project, the vendor's official docs site via WebFetch for any language). If you find yourself writing a project-specific path or framework name in the skill body, replace it with the generic shape and an illustrative example list.
- **Mid-implementation pivot rule.** If the first diagnostic experiment under an approved plan disproves the hypothesis (e.g. "I disabled X and the symptom didn't change"), STOP. Don't try a second guess. Return to Mode 1 step 4 (Explore) with the new evidence as a sharper question — the plan was scoped at the wrong target and patching it forward will compound the error. Two failed disable-experiments back-to-back is a hard signal to re-explore; if the symptom is genuinely opaque after that, hand off to `/diagnose`.
- **New file mirrors existing file? Refactor first by default.** When the plan adds a new file the description says "mirrors" / "like" / "similar to" / "same family as" an existing one — OR a sibling file with the same suffix already exists in the target directory — the reuse-specialist agent is mandatory during Explore and its Phase-0-refactor recommendation is presumed correct unless the user explicitly overrides at plan approval. The refactor becomes phase 0 of the plan; the new feature is phase 1+. Copy-and-defer requires an explicit user override at approval, recorded in the Parking lot in writing — not a passive default that quietly leaves duplication for `/code-review` to surface after the duplicate ships.
- **Algorithmic plans: land the research load-test at the earliest practical phase, not "whenever it's convenient."** The minimum executable test that would catch a naive implementation (research's question 3) is the falsifiable claim that proves the research is grounded. The default placement is phase 1's first commit, *before* the rest of phase 1 — the test runs against the simplest possible implementation and gates further work. If the test genuinely cannot be run until phase 2 (e.g. it needs integration plumbing that doesn't exist yet, or the LLM pipeline only behaves under realistic load), that's allowed, but the plan must explicitly call out the gap and the test still becomes the *first thing* in phase 2, not buried mid-phase. If the test fails when it lands, the research was incomplete — return to Mode 1 step 4 (Explore) with the specific failure mode as a sharper question, don't paper over it with tuning. This catches "research was insufficient" at phase 1-2 instead of phase 3+.
- **Perf/bug plans: run the Diagnosis falsifiable test BEFORE drafting "Files to touch."** A plan whose goal is to change something that's already running (perf regression, bug fix, "stop X from doing Y", "make Z cheaper") needs ground truth on what's actually causing the symptom before we scope a fix. The Diagnosis section's hypothesis + falsifiable test exists for this. If the test confirms the hypothesis, scope normally. If it disproves the hypothesis, the rest of the plan is built on sand — don't write the file list, return to Mode 1 step 4 (Explore) with the negative result as the sharper question. Files-read alone doesn't ground the diagnosis; "I commented out X and the symptom didn't change" does. Two failed disable-experiments back-to-back means the hypothesis space is wrong — switch to /diagnose, don't keep guessing.
- **Justify non-goal exclusions across peer sets.** When the plan applies a change to some members of a peer set and excludes others, each exclusion needs a one-sentence "why this asymmetry is safe" rationale in the Non-goals section. If you can't write that rationale, restructure: either fold the excluded items in, or refactor the plan to avoid the asymmetric application entirely. Default to *including* — exclusions require concrete justification, not just absence of a complaint about the excluded item. The exclusion-safety specialist in Mode 1 step 4 surfaces this as a forced binary decision at approval; trust its default-include recommendation unless you have an iron-clad invariant.
