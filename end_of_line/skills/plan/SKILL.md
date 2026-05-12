<!--
This is a frozen clone of the operator's `/plan` skill, bundled
with clu so installs are self-contained. The canonical version
may drift in the operator's private skills repo. To replace this
bundled copy with a symlink to your own version, run
`clu install-skill --only plan --force` after putting your
SKILL.md at ~/.claude/skills/plan/SKILL.md.
-->

---
name: plan
description: Create, resume, or ship a one-screen plan file in plans/ before starting multi-file work. Keeps ADHD scope creep in check by making scope explicit and approved upfront.
user_invocable: true
---

## Plan Workflow

This skill enforces the "plan before code" discipline defined in the user's `feedback_plan_discipline.md` memory. Read that memory for the why and the template format — it is the single source of truth.

The skill has three modes, auto-detected from context:

### Mode 1: Create a new plan (`/plan <slug>` — no existing file)

1. **Normalize the slug**: lowercase, hyphens for spaces, strip non-alphanumeric except hyphens. Example: "Pipeline Hardening!" → `pipeline-hardening`. Also recognize an optional `--no-research` flag anywhere in the arguments — strip it from the slug and remember it for step 7.5.
2. **Find the project's plans/ directory.** Start from the current working directory, walk up to the git root if needed. If `plans/` doesn't exist at the git root, create it.
3. **Check for an existing file at `plans/<slug>.md`.** If it exists, switch to Mode 2 (resume).
4. **Draft the plan file** using the template below, filled in based on the current conversation's context (what the user has said they want, what files have been discussed, what failure modes have surfaced). If context is thin, include `TODO` markers for sections you can't fill in yet — but know that step 7.5 will actively remedy thin context.
5. **Write the file** to `plans/<slug>.md`.
6. **Show the plan to the user** and say:
   > Here's the plan. Read it over and approve / tweak / reject. I won't write any code until you say it's live.
7. **Block on user response.** Do not touch any code files, do not run any builds or tests, until the user explicitly approves.

7.5. **Post-approval research pass.** After the user approves the initial plan, but BEFORE touching any implementation file, remedy thin context. This is the difference between plan v1 ("draft from conversation, march forward") and plan v2 ("draft, approve, research, revise, re-approve, execute").

   **Skip this step entirely if ANY of the following are true:**
   - The user passed `--no-research` in step 1
   - "Files to touch" lists ≤ 3 files AND the assistant has already read each one in the current conversation AND (for perf/bug plans) the Diagnosis falsifiable test has been RUN and CONFIRMED the hypothesis. Files-read alone is not enough — having read the code without verifying the cause means we may be confidently wrong about the target.
   - The plan is pure docs/config (no code files listed)

   **Mid-implementation pivot rule.** If the first diagnostic experiment under the approved plan disproves the hypothesis (e.g. "I disabled X and the symptom didn't change"), STOP. Don't try a second guess. Fall back here, dispatch research agents with the new evidence as a sharper question. Two failed disable-experiments back-to-back is the signal that the plan was scoped at the wrong target — the cost of replanning is far less than the cost of three more wrong guesses.

   **Hand-off to `/diagnose` for hard cases.** If the symptom is genuinely opaque (no obvious hypothesis, multiple plausible causes, intermittent reproduction), the plan should defer to `/diagnose` for the diagnosis phase. /plan + /diagnose compose: run /diagnose to find the cause, then /plan to scope the fix. Don't try to do disciplined diagnosis inside a /plan flow — the skills are sized for different jobs.

   **Otherwise, run the pass in order:**

   **a. Dispatch parallel agents** using the Agent tool. **All agent calls go in a single message so they run in parallel** — see graphify's SKILL.md step B2 for the pattern.

   **Three questions to hold in mind while designing this dispatch.** Everything below is scaffolding for these; if you can answer them well, the rest mostly takes care of itself:

   1. **What's the shape of failure if the research is wrong?** Frame this as a concrete falsifiable test scenario (initial state, applied conditions, expected vs. failing behavior). That scenario is the load-test that lands at phase 1.
   2. **What finding would a generalist agent bury?** Whatever it is, that's the brief for one specialist whose ONLY job is to surface it as a primary finding — not as a footnote under broader topic coverage.
   3. **How many genuinely distinct dimensions am I researching?** That's your agent count. Not plan size, not category — distinct dimensions where each one rewards a focused brief.

   **Choose agent type per agent** based on what they're researching:
   - **`subagent_type: "Explore"`** for local codebase mapping: existing helpers, file sizes, naming conventions, callers of the to-be-changed functions, test layer coverage. Description match: "Fast agent specialized for exploring codebases."
   - **`subagent_type: "general-purpose"`** for everything else: external libraries / SDK source, third-party docs, papers, GDC talks, web search, vendor APIs, model documentation, prompt-engineering references, security CVEs, etc. Per the description: "researching complex questions … multi-step tasks."
   - Mix freely. A research pass typically dispatches both types in one parallel message.

   **Agent count: scale with research surface area.** Not with plan size, not with category — with how many distinct research dimensions the plan actually has. Soft guidance:
   - **1-2 agents** when the plan is contained to a small area (one module, one config, one workflow).
   - **3-4 agents** when the plan spans multiple dimensions: e.g. an LLM orchestration change might want prompt-design + caching + integration + evals; a UI change might want component + state + animation + tests; a backend feature might want schema + API + caching + error-handling.
   - **5-6 agents** when the plan is genuinely cross-cutting AND specialization buys clarity over breadth: complex algorithmic work, large-feature LLM systems, multi-service refactors. The extra slots are for *role specialization*, not for chasing the same question harder — if a 6th agent is just "another Explore on the same files," cut it.
   - **Stop adding agents** when the marginal one would re-cover ground from another. The consolidation overhead in step (b) grows with agent count; budget it intentionally.

   **Role specialization is the principle that funds high agent counts.** Each agent should have a single sharp job no other agent is doing. This prevents the failure mode that motivated this skill update: a generalist agent mentions the load-bearing detail in passing, the consolidation report buries it under broader findings, and the bug surfaces in phase 3. A specialist whose *only* job is "the inner-loop / the prompt structure / the cache invalidation contract / the migration path" surfaces those findings as primary, not as asides.

   **Examples of role splits by domain** (illustrative, not prescriptive):
   - **Algorithmic / numerical / physics**: math-and-formulas agent · per-tick-inner-loop specialist · integration-with-existing-system · (optional: failure-modes-under-load, platform-quirks).
   - **LLM orchestration**: prompt-design and structured-output agent · caching-and-token-budget agent · model-version-and-migration agent · evals-and-regression-fixtures agent · integration-with-existing-brain agent.
   - **UI feature**: component-and-layout agent · state-and-data-flow agent · animation-and-interaction agent · accessibility-and-test agent.
   - **Backend feature**: schema-and-migration agent · API-contract-and-versioning agent · caching-and-invalidation agent · error-and-retry semantics agent.
   - **Security review** (handled by /security-review skill, but as illustration): threat-model-and-attack-surface agent · authn-and-authz agent · data-handling-and-privacy agent · dependency-and-supply-chain agent.
   - **Cross-cutting refactor**: callers-and-impact agent · test-coverage agent · deprecation-path agent · integration-test-strategy agent.

   **For algorithmic plans specifically** (signals: plan cites a paper, GDC talk, engine docs, or third-party library's primitive), one of the agents MUST be the implementation-details specialist briefed explicitly:
   > "The math is someone else's job. Your job is the loop structure and the parameters that aren't on the equation page — iteration counts, warm-start handling, regularization parameters, accumulator resets, internal stabilization passes, default thresholds. Read the engine source. What does the per-tick / per-step inner loop ACTUALLY do, beyond the formula? What load-bearing details exist that aren't in the API documentation?"
   This separation prevents iteration count from being buried under formula overview (real failure mode that motivated this update — see commit history of this skill).

   **Each agent's brief includes:**
   - The plan file path (for it to read)
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

   **For plans that add a new file mirroring an existing file** (signals: "Files to touch" includes a NEW file whose description uses words like "mirror", "like X", "similar to", "same look-and-feel as", "same family as", "matches the X style"; OR the new file's name has an obvious sibling already in the same directory — e.g. `news_window.py` next to `chat_window.py`, `foo_backend.py` next to `bar_backend.py`), one of the agents MUST be the **reuse / refactor specialist** briefed explicitly:
   > "The plan describes a NEW file as mirroring an EXISTING file. Read both (and any other obvious siblings in the same directory). List concrete duplication: blocks ≥30 lines, methods ≥3 that would be copied verbatim or near-verbatim, shared widget chrome, shared style/setter surface. For each duplicated block, cite file:line. Then propose ONE of two paths and recommend which:
   > (a) **Phase 0 refactor** — extract a shared base class / helper module / mixin FIRST, land that as its own commit, then build the new file on top in phase 1+.
   > (b) **Copy and defer** — write the duplicate now, file the dedupe as a follow-up.
   > Default to recommending (a) unless the existing file is unstable, about to be rewritten, or the duplication is <30 lines of trivial boilerplate. Your recommendation gets surfaced as a forced binary decision the user must make in the second-approval step — don't soften it."

   This separation prevents the failure mode that motivated this rule: a plan describes a new window as "mirroring" an existing one, the layout agent confirms "yes the styling matches," code gets written as a parallel implementation, and the base-class extraction lands as a parking-lot follow-up *after* the duplication ships and after a `/simplify` pass surfaces it. The right move is refactor-then-extend, not extend-then-refactor. See `feedback_reuse_first.md` for the originating incident (TokenPal news history window, April 2026).

   **b. Consolidate findings into a diff against the current plan.** Do NOT dump raw agent output to the user. Instead, compose a structured "what changed" report:
   - **Confirmed**: plan assumptions that the research validated
   - **Contradicted**: plan assumptions that turned out to be wrong, and the corrected understanding
   - **New failure modes surfaced**: things the research found that the plan didn't anticipate
   - **Load-bearing implementation details** (algorithmic plans only): inner-loop specifics, iteration counts, stabilization passes, default parameters that aren't in the formula but affect correctness. Each one paired with "what fails if we miss this." If this section is empty for a numerical/algorithmic plan, the research wasn't deep enough — re-dispatch with sharper questions before presenting the diff.
   - **Reuse opportunities surfaced** (mandatory whenever the reuse-specialist ran): existing files / classes / helpers in the repo that the new code should refactor-then-extend rather than parallel-implement. Each entry MUST include: (1) the duplicated surface with file:line citations, (2) the specialist's recommendation — Phase 0 refactor vs copy-and-defer — and (3) the **forced binary decision** the user must make in the next step. If the user picks copy-and-defer, the deferred refactor MUST be appended to the plan's Parking lot in writing before code starts.
   - **Scope questions**: unresolved tensions the user needs to decide (e.g. "this function is 400 lines — do we harden in place or refactor first?")
   - **Suggested plan edits**: specific section-level changes (e.g. "add X to 'Files to touch'", "remove Y from 'Failure modes' because it turned out to be a non-issue")

   **c. Present the diff for second approval** with this template:
   > Research pass complete. Here's what changed from the initial plan:
   >
   > **Confirmed**: <bullets>
   > **Contradicted**: <bullets, with corrected understanding>
   > **New failure modes**: <bullets>
   > **Reuse opportunities** (forced decision): <each entry = duplicated surface + file:line + recommendation + binary choice "Phase 0 refactor / copy-and-defer">
   > **Scope questions for you**: <bullets — these need decisions>
   > **Proposed plan edits**: <bullets>
   >
   > Approve / tweak / reject these changes. I won't touch implementation until you say.

   **d. Block on user response.** Same rule as step 7 — no code, no tests, no builds until second approval lands.

   **e. On approval**, apply the confirmed plan edits to the plan file (explicit `Edit` calls, preserving the template structure). On rejection of specific items, apply only what the user approved. On total rejection, proceed with the original plan unchanged.

   **f. Brainstorm skill note**: if the research pass surfaces significant scope questions the user can't decisively answer (e.g. "should we refactor this whole subsystem or just patch it?"), suggest the user invoke `/brainstorm` as a separate step. Do NOT invoke brainstorm inline from within this skill — it's user-interactive and doesn't compose cleanly.

8. **Once approved (or re-approved after step 7.5), enter "working the plan" mode**: reference the plan on every file touch, interrupt scope creep, append to the parking lot when the user drops a shiny idea mid-work.

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

Use this exact structure — the "working agreement" memory references these section headers. Don't rename them.

```markdown
# <feature name>

## Goal
<1-2 sentences stating what this plan is trying to accomplish. Concrete, not aspirational.>

## Diagnosis  *(required for perf/bug/regression plans; omit for greenfield features)*
- **Hypothesis:** <what specifically is causing the symptom — e.g. "X function is the hot path" / "Y handler swallows the error">
- **Falsifiable test:** <a one-line experiment that would CONFIRM or DISPROVE the hypothesis before we touch the file list — e.g. "comment out X.start() and observe Z drops to ≤K", "add a log at line N and confirm it fires before Y">
- **Test result:** <run it. Record what you observed. If the test disproves the hypothesis, STOP — return to step 7.5 with a sharper question, do not draft "Files to touch" yet>

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
- **Diagnosis**: Required when the plan exists to *change* something already running — performance regressions, bug fixes, "make X faster/smaller/cheaper", "stop Y from happening", "investigate Z". Skip for greenfield features (new code where there's no existing behavior to diagnose). The hypothesis names the suspected cause concretely (a function, a flag, a code path), not vaguely ("something is slow"). The falsifiable test is a one-line experiment runnable in seconds — comment out a call, set an env var, add a log. **Run it before scoping `Files to touch`.** If the test disproves the hypothesis, the rest of the plan is built on sand — return to research, don't ship the wrong fix. The cost of running a 30-second diagnostic test is far less than the cost of implementing, /simplify-ing, testing, and committing a plan against the wrong target.
- **Non-goals**: Things the user has explicitly said to NOT do, OR things that are natural adjacent work that we're deliberately deferring. This is the most important section for ADHD — aggressive non-goals prevent drift.
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
2. **Simplify** — run `/simplify` on the changed code. **Trivial-diff escape hatch**: skip `/simplify` only if the diff is single-file AND single-logical-change AND has no behavior change (typo fix, version bump, comment rewording, doc-only edit). When in doubt, run it.
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
- **The template is the source of truth** — don't add or remove sections without updating the `feedback_plan_discipline.md` memory to match.
- **Be ruthless about non-goals.** If you're unsure whether to list something as a non-goal, list it. Easier to remove than to add mid-work.
- **Archive, don't delete.** Shipped plans move to `plans/shipped/` — they're a record of what got done, not garbage to collect.
- **New file mirrors existing file? Refactor first by default.** When the plan adds a new file the description says "mirrors" / "like" / "similar to" / "same family as" an existing one — OR a sibling file with the same suffix already exists in the target directory — the reuse-specialist agent is mandatory and its Phase-0-refactor recommendation is presumed correct unless the user explicitly overrides. The refactor becomes phase 0 of the plan; the new feature is phase 1+. Copy-and-defer requires an explicit user decision in the second-approval step, recorded in the Parking lot in writing — not a passive default that quietly leaves duplication for `/simplify` to surface after the duplicate ships.
- **Algorithmic plans: land the research load-test at the earliest practical phase, not "whenever it's convenient."** The minimum executable test that would catch a naive implementation (research's question 3) is the falsifiable claim that proves the research is grounded. The default placement is phase 1's first commit, *before* the rest of phase 1 — the test runs against the simplest possible implementation and gates further work. If the test genuinely cannot be run until phase 2 (e.g. it needs integration plumbing that doesn't exist yet, or the LLM pipeline only behaves under realistic load), that's allowed, but the plan must explicitly call out the gap and the test still becomes the *first thing* in phase 2, not buried mid-phase. If the test fails when it lands, the research was incomplete — return to step 7.5 with the specific failure mode as a sharper question, don't paper over it with tuning. This catches "research was insufficient" at phase 1-2 instead of phase 3+.
- **Perf/bug plans: run the Diagnosis falsifiable test BEFORE drafting "Files to touch."** A plan whose goal is to change something that's already running (perf regression, bug fix, "stop X from doing Y", "make Z cheaper") needs ground truth on what's actually causing the symptom before we scope a fix. The Diagnosis section's hypothesis + falsifiable test exists for this. If the test confirms the hypothesis, scope normally. If it disproves the hypothesis, the rest of the plan is built on sand — don't write the file list, return to research with the negative result as the sharper question. Files-read alone doesn't ground the diagnosis; "I commented out X and the symptom didn't change" does. Two failed disable-experiments back-to-back means the hypothesis space is wrong — switch to /diagnose, don't keep guessing.
