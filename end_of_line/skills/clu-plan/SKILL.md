---
name: clu-plan
description: Author a clu-format plan (master + sub-plan files) ready for `clu init` + `clu queue add` dispatch. Use when the user wants to scope a feature for clu-orchestrated execution, mentions queueing plans, or says "plan this for clu". For non-clu projects, falls back to /plan with a pointer.
user_invocable: true
---

<!--
Bundled with clu so /clu-plan installs are self-contained. The canonical
copy is end_of_line/skills/clu-plan/SKILL.md in the clu repo. To replace
this bundled copy with a symlink to your own version, run
`clu install-skill --only clu-plan --force` after putting your SKILL.md
at ~/.claude/skills/clu-plan/SKILL.md.
-->

## You are the clu-plan authoring skill

clu's dispatch contract requires a master plan file with a `## Sessions
index` markdown table whose rows declare each phase, PLUS one sub-plan
file per phase. The sub-plan is the worker's brief — what to read, what
to build, when to call `clu complete`. A `/plan`-style single file
yields no phases, and the supervisor errors `no Sessions index in
plans/<slug>.md` at dispatch.

This skill produces both: the master AND every sub-plan, in the format
that workers dispatched via `claude --print '/clu-phase ...'` can act on.

## When to use vs. when to refuse

**Use** when:
- User invokes `/clu-plan` directly.
- User asks to "scope this for clu", "plan a clu drain", "queue this
  up", or similar.
- User describes a multi-issue batch they want clu to drain
  autonomously.

**Refuse** (and point at `/plan`) when:
- The project doesn't have `.orchestrator.json` at its root. clu plans
  only make sense in clu-managed projects.
- The user's intent is a single solo human-authored plan with no
  intention to dispatch via clu (e.g. "make me a plan I'll work on
  manually"). The clu format has extra overhead; `/plan` is lighter.

Detection: `test -f .orchestrator.json && echo OK`. If absent, say:
> This project doesn't have `.orchestrator.json` — clu isn't managing
> it. Use `/plan` instead (the generic, non-clu plan skill).
> If you want to make this a clu project first, run `clu init --project
> . --plan <slug>` to bootstrap, then re-invoke `/clu-plan`.

## Workflow

### Step 1: Gather inputs

Ask only what you can't infer:

- **Plan slug** — kebab-case, matches `^[a-z0-9][a-z0-9_-]{0,63}$`. If
  the user gave one, validate it; if not, propose based on the goal
  ("scope a queue + worktree refactor" → `queue-worktree-refactor`).
- **Goal / scope** — what is this plan trying to accomplish? Pull from
  conversation context if it's been discussed; otherwise ask in one
  sentence.
- **GitHub issues to close** — list, optional. Worker uses these in
  commit messages (`closes #N`).
- **Phase breakdown** — how should the work split? If the user has a
  proposed split, use it. Otherwise propose one: smallest-first,
  each phase = one issue OR one cohesive commit, each phase has
  TDD-able acceptance criteria. Single-phase plans are fine when the
  scope is small — clu still requires the Sessions index with one row.

#### Phase granularity

Each phase has ~30–60s of overhead (cold-context worker ramp +
subprocess startup; push-dispatch since #52 closed the cron-tick gap
between phases) before any real work happens. Don't phase finer than
the work justifies.

Rules of thumb:

- **Collapse one-function helpers into their first caller.** If the
  helper is <50 LOC and only used by the next phase, it belongs in
  that phase.
- **A "meaningful commit" isn't a single function.** It's a
  minimum-viable slice that's TDD-able and reviewable on its own —
  a function plus its first caller usually qualifies.
- **Target 3–6 phases for typical features.** 7+ is fine when each
  phase is a genuinely independent slice (notify-multi-channel was 7,
  clu-ship was 8); treat it as a smell to re-check, not a hard cap.
- **Phase when there's a forcing function:** schema bumps, API surface
  changes that other plans queue against, config fields workers need
  to read in earlier phases.

Receipt: auto-archive-on-merge (2026-05-18) was 3 phases; the first
phase added a single ~15-line helper plus 5 tests and could have
shipped inside the next phase's commit without losing TDD-ability.
Each saved phase is ~30–60s of dead time off the plan's wall clock.

### Step 2: Pre-author research (mandatory)

This is the E in EPCC (explore → plan → code → commit) — the same
unconditional pre-draft exploration gate `/plan` enforces before any
plan text is written — but for clu-plans it runs BEFORE drafting (not
after a first approval), because the master's Locked-decisions and
Files-touched sections commit to specific file paths, function
signatures, and behaviors the moment they're written. A worker
dispatched off those paths inherits whatever the research got wrong.
There is no "small plan" exception and no opt-out: research grounds
the master, and the master is the contract the cold-context worker
can't push back on.

**Hand off opaque diagnostic cases to `/diagnose` first.** If the
symptom is genuinely opaque (no obvious hypothesis, multiple plausible
causes, intermittent reproduction), run `/diagnose` to find the root
cause BEFORE scoping phases here. /diagnose finds the cause; clu-plan
scopes the fix. A cold worker can't run a disciplined diagnosis loop
mid-phase — don't ship it a master built on a guessed root cause.

#### Stage zero — settle design forks first

Before any team below is briefed, read the operator's goal and the
code long enough to answer one question: **is there a fork between two
or more candidate designs whose outcomes would produce DIFFERENT
Sessions-index row sets** — different row count, ordering, scope, or
effort? If yes, that fork is settled NOW, by experiment, before Team A
or B is briefed. Its verdict lands in the master's Locked design
decisions, and the teams are briefed against a design that exists —
research run against a fork answers about a shape that may be
discarded.

The clu-specific stake: a cold-context worker inherits an unsettled
fork with no operator to ask. The anti-pattern is a Sessions-index row
whose scope is "decide X" — every row after it was drafted against an
assumed answer, and if the answer goes the other way, the plan gets
rewritten mid-dispatch, which is the outcome planning exists to
prevent.

**The instrument is a comparison probe:** ONE worktree-isolated agent
(`isolation: "worktree"`) that builds the smallest version of BOTH
candidates, measures the discriminating property, recommends nothing,
and reports the measurement. Paste the candidate descriptions verbatim
into its prompt — an agent worktree branches from the default branch
and carries only tracked files (code.claude.com/docs/en/worktrees), so
it cannot read unpushed or in-memory plan files. Its brief:

```
You are in a throwaway git worktree, settling ONE design question for
a clu plan someone else is drafting. Your diff will be discarded and
you are not delivering a feature. You are running an EXPERIMENT, and
your report is a verdict with evidence. The question and the candidate
designs are pasted in full below.

Build the MINIMUM of each candidate that can answer the question — the
smallest thing that exercises the discriminating property, not a
finished implementation. Hold constant everything the fork does not
force to differ: where you had a free choice, make it the same on both
sides; where a candidate forced your hand, say so. Build and run from
THIS worktree's own path — re-point any tooling whose defaults target
the main checkout, and verify it took before trusting a number.

Then MEASURE. Run it, drive it, record numbers or observed states — do
NOT reason from documentation about what should happen; if docs
settled this question you would not have been dispatched. Where docs
disagree with what you measure, report both and trust the measurement.

A verdict requires BOTH candidates built and measured. If you cannot
stand a candidate up after real attempts, that IS the finding: report
UNRESOLVED, say which candidate and what stopped you, and give
whatever partial measurement you have.

Report: (i) the VERDICT — which candidate, and the single property
that decided it; (ii) the MEASUREMENT behind it, as raw as you can
give it: what you ran, how many trials, what each returned; (iii) what
you held constant, and anything you could not; (iv) what surprised you
about EITHER candidate, whether or not it bears on the verdict; (v)
the SHAPE the winner forces — the composition, types, or call pattern
the plan must now be written against, as concretely as you can give
it, ideally as the actual code you built. The research teams are
briefed from that line next, so a vague shape means they analyse a
design nobody has written down.

You recommend nothing and you judge no code quality. You are NOT to
invoke `/clu-plan` or `/plan`. Do NOT commit. The question and the
candidate designs follow: {question_and_candidates}
```

Do NOT append the research boilerplate (further down) to this probe:
the probe recommends nothing and measures instead of citing, so the
effort-objection ban is meaningless to it and the 400-word cap fights
its duty to report raw measurement.

**Forks surfaced later by research are probed retroactively**, and the
affected teams are then RE-DISPATCHED against the winner — their first
pass answered about a shape you have now discarded, so its findings
describe code that won't be written. **What does NOT trigger a probe:**
a question whose answer changes only a sub-plan's internals — an
implementation detail inside a phase — and leaves the Sessions-index
row set alone. The trigger is "would the row set be different", not
"could this go wrong later".

#### Three research teams

Why teams and not topics: the old shape asked three topic questions
that were all versions of *what exists and what's canonical*. None
asked **what breaks when I touch this** — which is where
implementation surprises on refactors actually come from. Knowing a
function has four callers says nothing about whether one of them
depends on it being slow, on it running before something else, or on a
side effect nobody wrote down. Team A exists for that question.

Dispatch every agent in a single message so they run in parallel.
Pass the briefs below as written (placeholders filled) — paraphrasing
them into one generic "go research this" is the degradation the team
split exists to prevent.

**All research agents are `subagent_type: "general-purpose"` — never
`Explore`.** Per Claude Code's subagent docs: "Explore and Plan are
the only subagents that omit CLAUDE.md and git status. There is no
frontmatter field or per-agent setting to change which agents skip
them" (code.claude.com/docs/en/sub-agents, "What loads at startup").
An Explore-typed researcher silently loses every standing project rule
— the verify-before-stating gate, any project-specific API gate —
which is exactly what research correctness depends on.

**NEUTRAL BRIEFS FOR TEAMS A AND B.** They receive the operator's goal
in the operator's words plus the files in play — **never the approach
you have in mind.** A brief that says "we're adding a helper to X" has
already handed the agent your assumption, and what comes back will
agree with it. If you cannot describe the territory without naming
your solution, that is the signal your solution is already doing
load-bearing work before any research ran.

**Team A — CHANGE IMPACT (3 agents).** *Skipped only when the plan
creates new code and modifies none.* That trigger is observable — read
the draft Files-touched list to decide — not a judgment call about
whether something "counts as a refactor."

A1 — fan-in and observable behavior:

```
Map who depends on {files/symbols in play}, and how far the dependency
reaches.

- Direct callers, then callers of those callers, out to the point
  where a difference would become visible to a user or to another
  system. Stop there and say where you stopped.
- For each call site: what does it assume about this code that isn't
  in the signature? Return-value shape, nullability, whether it can
  throw, whether it's safe to call twice.
- Which call sites would keep compiling but start behaving differently
  if the change goes in? Those are the dangerous ones — a compiler
  error is a fixed bug, a silent behavior change is a shipped one.

Do NOT report a file map or a directory structure. If your report
reads like an inventory of what exists, you have answered the wrong
question.
```

A2 — incidental behavior:

```
Everything being changed or deleted here does something BEYOND its
stated job. Find it.

- What does this code do incidentally? Timing, ordering, caching, a
  retry that is also acting as a debounce, a log line something greps
  for, a lock held slightly longer than needed that another path
  relies on.
- For anything being DELETED: what was it doing that nobody
  documented? Name the invariant it enforced, then search for where
  that invariant would be re-established afterwards. If you can't
  find one, say so — that is the finding.
- What would still pass every existing test and still be broken?

Quote file:line. Speculation is fine if labelled, but label it.
```

A3 — shared state and ordering contracts:

```
Map what else touches the same state as {files/symbols in play}.

- Every other reader and writer of the same state, queue, cache,
  file, or external resource.
- Ordering contracts: what breaks if this runs earlier, later, twice,
  not at all, or concurrently with its neighbours? Walk realistic
  sequences, not just the happy path.
- Where does this code's correctness depend on something else having
  already run? Is that dependency enforced, or is it just true today?

Cite file:line for each coupling. Rank by how silent the failure
would be.
```

**Team B — ADVERSARIAL CODE READ (1 agent; 2 when the change spans
modules).** Attacks the *existing* design — the neutral-brief rule
applies to this team especially.

B1 — attack the existing design:

```
Read {files in play}. Your job is not to review a proposed change —
it is to find what is wrong with the code as it stands today.

- What undocumented invariant does this code depend on? What would a
  new contributor break within a week because nothing states it?
- Where does the naming lie? Functions whose names describe less (or
  more) than they do, types whose names describe a role they no
  longer play.
- What do you have to know that isn't in this file to change it
  safely?
- What is here only because of how it was built, rather than what it
  needs to do?

Then the question this brief exists for: if you were writing this
from scratch today, knowing what it must do, what shape would it be?
Describe that shape concretely. Do not soften it toward the current
design and do not weigh how much work the difference would be — that
is explicitly not your input. If the honest answer is "roughly what's
there," say that plainly; a clean bill of health from this brief is a
real result.
```

**Team C — IMPLEMENTATION SPECIALISTS (2 agents + the conditional
specialists below).** Unlike A and B, these MAY be told the intended
approach — their job is to check it against how the thing is meant to
be used.

C1 — project-local API documentation and canonical samples:

```
For the dependencies this plan touches, surface the framework's
official guidance and working code patterns. Find where this
project's docs live — vendored docs folders, library README and
examples under site-packages / node_modules / Pods, framework
headers, generated type stubs — and fetch from the vendor's official
docs site when no local copy exists.

- What does the framework's canonical pattern for this problem look
  like?
- Where are working examples, in this project's dependencies or in
  vendor sample repos?
- What footguns does the documentation itself call out?

Cite file:line for local sources, URL+section for fetched docs.
```

(For clu itself, stdlib-only — this agent is light, but still run it
to confirm no new third-party dep is implied.)

C2 — web prior art and community evidence (WebSearch + WebFetch):

```
How are others in this language / framework / domain solving this
problem? Stack Overflow threads, GitHub issues on the relevant
libraries, recent blog posts, conference talks.

Vendor docs are routinely incomplete, or describe an intended
contract that doesn't match shipped reality — independent
corroboration is the entire point of this agent. Gotchas and
performance cliffs are usually the UNDOCUMENTED part, which is
exactly why a doc quote alone cannot close every question.

Cite a URL for every finding. "I found nothing credible" is a valid
and useful answer; padding is not.
```

**Boilerplate — append to EVERY team and specialist brief** (never to
the stage-zero probe):

```
Researching for clu plan {slug}. Goal: {one-line goal}.
You are NOT to invoke `/clu-plan` or `/plan`. Research only. Report
in under 400 words.
Cite file:line for local sources, URL+section for fetched ones. A
claim you did not open a source for is reported as unverified, not as
a finding.
Diff size, file count, and implementation effort are not your inputs.
Recommend what is correct.
```

**Three framing questions to hold while designing the dispatch.**
Everything below is scaffolding for these; answer them well and the
rest mostly takes care of itself:

1. **What's the shape of failure if the research is wrong?** Frame it
   as a concrete falsifiable scenario (initial state, applied
   conditions, expected vs. failing behavior). That scenario is the
   load-test that lands at phase 1.
2. **What finding would a generalist agent bury?** Whatever it is,
   that's the brief for one specialist whose ONLY job is to surface it
   as a primary finding — not a footnote under broader coverage.
3. **How many genuinely distinct dimensions am I researching?** That's
   your agent count beyond the three teams.

**Baseline agent count, then scale with research surface area — not
plan size, not phase count:**

- **4 agents** for a plan that only creates new code: B1 + C1 + C2 +
  any triggered conditional specialist. Team A is skipped because
  there is no existing behavior to break.
- **7 agents** for a plan that modifies existing code: A1–A3 + B1 +
  C1 + C2, plus conditionals.
- **+1–2** when the plan spans extra dimensions beyond those — e.g. a
  schema bump wants migration + caller-impact specialists; a
  notify-channel change wants delivery-semantics + config-merge. The
  extra slots are for *role specialization*, not chasing the same
  question harder.
- **Stop adding agents** when the marginal one would re-cover another's
  ground. Consolidation overhead grows with agent count; budget it.

**The scale has a floor, and its trigger is observable — count the
Sessions-index rows.** A single-phase plan (Sessions index with ONE
row) collapses Team A to ONE agent carrying the A1+A2+A3 briefs
concatenated, verbatim — 4 agents total. The floor compresses Team A
only: no question is cut, and Team B's second agent plus the
conditional specialists keep their own triggers.

**Effort is the cost control, not headcount.** Dispatch the teams at
the session's effort; dispatch the conditional specialists at **low**
— each answers one narrow forced-binary question against a trigger
that already fired, and quality holds there. Trimming an agent cuts a
question; lowering its effort does not.

**Additional role splits when the plan's shape demands them**
(illustrative, not prescriptive — these compose on top of the three
teams, one sharp job per agent):

- **Algorithmic / numerical:** math-and-formulas · per-tick inner-loop
  specialist · integration-with-existing-system.
- **LLM orchestration:** prompt-design and structured-output ·
  caching-and-token-budget · evals-and-regression-fixtures.
- **Backend:** schema-and-migration · API-contract-and-versioning ·
  caching-and-invalidation · error-and-retry semantics.
- **Cross-cutting refactor:** callers-and-impact · test-coverage ·
  deprecation-path · integration-test-strategy.

**Three conditional specialists force a binary decision at approval.**
Check all three triggers against every plan; each is mandatory when
its trigger fires:

- **Reuse / refactor specialist** — MANDATORY when the plan adds a
  NEW SOURCE file described as "mirrors / like / similar to / same
  family as" an existing source file, OR a sibling with the same
  suffix already exists in the target dir. Scope is SOURCE files only
  — never markdown, docs, skill definitions, prompt templates, or
  config: parallel structure in prose is a feature, and the code
  thresholds below mean nothing for instructions. Brief it to read
  both, list concrete duplication (blocks ≥30 lines, ≥3 near-verbatim
  methods, shared chrome) with file:line, and recommend (a)
  **refactor-first** (extract the shared base/helper as its OWN
  phase, then build the new file on top in a later phase) or (b)
  copy-and-defer. The policy default and override mechanics live
  under Critical rules: "New file mirrors an existing file? Refactor
  first by default" — in short, (a) wins and becomes the first row of
  the Sessions index.

- **Exclusion-safety specialist** — MANDATORY when a Non-goal will
  exclude some members of a peer set (some op types, endpoints,
  entities, files in a related family) that share state, a queue, a
  cache, an ordering/FIFO contract, or an applied-token set with the
  in-scope items. Brief it to list every dependency between the two
  groups, walk realistic call sequences for race/ordering/stale-state
  hazards (file:line), and recommend (a) **fold excluded into scope**
  or (b) keep the exclusion with a one-sentence iron-clad invariant.
  The policy default lives under Critical rules: "Justify Non-goal
  exclusions across peer sets" — in short, (a) wins.

- **Algorithmic / inner-loop specialist** — MANDATORY for plans that
  cite a paper, GDC talk, engine docs, or a third-party primitive, or
  implement physics / integrators / control loops / constraint
  solvers. The four required questions are inlined under "Critical
  rules" below — concentrate them in this agent's brief.

**Skip-condition (narrow):** the teams still run even for a single
small phase — Team A's new-code-only skip and the single-phase floor
above are the only sanctioned reductions, and both compress headcount
without cutting a question. The only files you may skip re-reading
are ones you've *already read in this conversation* — cite them from
that read instead of re-dispatching. You may not skip C1 or C2 on a
"pure docs/config" basis.

**Consolidate as ground truth.** Walk away with: the corrected
understanding of the touched area — including what the change will
BREAK (Team A), not only what exists; Team B's from-scratch shape,
recorded even when the plan doesn't adopt it, so the approval
conversation can see what was on the table; any forced binary
decisions from the reuse / exclusion specialists, with the recommended
option (baked into the draft as the default — see Step 3); and **no
unverified claims** (full rule under Critical rules: "No research
deferrals — verify or block", including the only two legitimate
carve-outs and the membership test for what counts as empirical). An
empirical unknown whose answer would change the Sessions-index row
set is not a legitimate carve-out — that fork belonged to stage zero,
and if research surfaced it late, probe it now and re-dispatch the
affected teams. If research couldn't close a question, that's the
signal Step 2 isn't done — finish it, or STOP and resolve it with the
operator before drafting.

### Step 3: Draft all files in memory

**Do not write to disk yet.** Draft the master file AND every sub-plan
in memory — every file must be ready to write the moment the operator
says `ship`. The operator-approval mandate from the user's CLAUDE.md
applies: novel plan files require `ship` from the operator before they
land on disk.

Drafting all sub-plans up-front is mandatory even though only the
master is shown in Step 4. The worker dispatched after `clu init` will
read a sub-plan that exists or fail; you can't lazily author them on
ship.

**Three drafting rules (carried from `/plan`):**

- **Every factual claim is backed by Step 2 findings.** Cite file:line
  / URL+section in the master and sub-plans wherever a claim depends on
  a verified source.
- **Verify or block — no deferral channel** (full rule under Critical
  rules: "No research deferrals — verify or block"). Every claim in
  the master or a sub-plan is verified in Step 2 and cited with
  file:line / URL+section, or the plan isn't drafted.
- **Bake forced binary decisions in as the recommended option.** If
  the reuse specialist recommended a refactor-first split, draft the
  Sessions index with that refactor as the first row. If the exclusion
  specialist recommended folding excluded items in, draft them in
  scope. The decision is still surfaced at approval (Step 4) so the
  operator can override.

#### Master template

```markdown
# <slug> — <one-line tagline>

<2-3 paragraph intro: what the plan does, why it matters, what's the
ordering of phases. Reference any GitHub issues it closes. If the plan
is a follow-up to a recent incident, name the incident.>

## Diagnosis  *(required for perf/bug/regression plans; omit for greenfield)*

- **Hypothesis:** <the suspected cause, named concretely — a function,
  a flag, a code path; not "something is slow">
- **Falsifiable test:** <a one-line experiment runnable in seconds that
  CONFIRMS or DISPROVES the hypothesis before "Files touched" is scoped>
- **Test result:** <run it during Step 2. Record what you observed. If
  it confirms the hypothesis, scope phases normally. If it disproves
  it, STOP — return to Step 2 research with the negative result as a
  sharper question; do NOT draft phases yet. The master commits paths
  workers can't second-guess, so a wrong target here ships as wrong
  worker dispatch.>

## Locked design decisions

<One subsection per phase OR per logically distinct decision area.
Each subsection: bullet list of concrete decisions, with the WHY
attached when non-obvious. The worker reads these to ground itself
on what's already settled vs. what they get to decide.>

### Phase 1 — <phase name> (#issue if applicable)
- **<decision>:** <details>
- **<another decision>:** <details>

### Phase 2 — ...

## Non-goals

- <explicit boundary>
- <natural adjacent work being deferred>
- <scope creep risk>

## Files touched

List every file the plan creates or modifies, plus API hotspots
(public function signatures, schema fields, config keys) downstream
plans might rely on. The operator scans this at queue time to spot
overlaps when scheduling parallel batches — overlapping `## Files
touched` sections mean serialize, not parallelize. Unchecked semantic
conflicts across worktrees were the canonical failure (clu #50;
`cmd_answer` argparse drift, merge SHA `1816c0f`).

- `<path/to/file>` — <P1 NEW | P1, P3 modified> — <one-line note; flag API hotspots>
- `<another path>` — <phase tags> — <note>

## Per-phase done checklist

- TDD: failing tests first.
- `/code-review` after if diff >1 file or ~30 lines — plus any review
  gates the project's own CLAUDE.md mandates for this diff type (UI
  review passes, screenshot evidence, lint gates). Project gates
  compose with `/code-review`; they don't replace it.
- Full suite green: `python3 -m unittest discover -s tests` (or this
  project's canonical pre-commit gate — a green subset the gate
  doesn't sanction is not green).
- Structured commit format (Title / Why / What's new / Under the hood /
  Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- **Stamp attestations AFTER the commit.** The gate compares stamp SHA
  against HEAD; pre-commit stamps go stale the moment you commit.
  - `clu verify --plan <slug> --phase <id> --token <T>` runs the
    project verify command and stamps `attestations.verify`.
  - `clu attest --simplify --plan <slug> --phase <id> --token <T>`
    stamps `attestations.simplify` (required when phase diff exceeds
    `simplify_threshold`; auto-passes below it).
- Call `clu complete --plan <slug> --phase <id> --token <T>` with the
  worker token on success. The completion gate refuses with
  `EVENT_ATTESTATION_REFUSED` + an inbox surface if stamps are missing
  or stale.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| <phase-id> | `<slug>-<phase-id>.md` | <one-line scope> | <Nh> |
| <next-phase-id> | `<slug>-<next-phase-id>.md` | ... | ... |

## Findings log

_Empty at plan time. As phases run, the worker appends one dated bullet
per cross-phase finding — a gotcha, a spike result, an API surprise, an
assumption that turned out wrong — so a later phase doesn't rediscover
it. Cite file:line. Plan-time decisions stay in Locked design decisions;
this section is runtime-only: written by workers, read by every phase._
```

The `## Sessions index` heading must be byte-exact — case-sensitive,
single space. The machine-wide plan-draft gate
(`~/.claude/hooks/plan_draft_gate.py`) matches that exact spelling to
exempt clu masters from its write-freeze. clu's own parser is
case-insensitive, so a variant spelling parses fine for clu yet
silently loses the exemption.

The Sessions index is load-bearing. `parse_sessions_index()` derives
phase IDs from the sub-plan filename: if the filename is
`<slug>-<phase>.md`, the phase ID is `<phase>`. Both must be valid
slugs per `st.validate_slug` regex `^[a-z0-9][a-z0-9_-]{0,63}$`.

**The `Effort` column is mechanically load-bearing, not decorative.**
`parse_effort_minutes()` reads it at `clu init` time to scale each
phase's lease TTL (default 60min × `lease_ttl_scale`, capped by
`lease_ttl_minutes`). Formats accepted: `Nh` or `Nmin`, decimals ok,
case-insensitive — e.g. `1h`, `2.5h`, `90min` — plus ranges `N-Mh` /
`N-Mmin`, which resolve to the UPPER bound (`1-2h` → 120 minutes).
Anything else — notably `45m` or a bare `90` — parses as no estimate
and silently falls back to the default lease TTL, with no error.
Undersize → lease expires
mid-phase and the worker halts; oversize is fine. Estimate honestly;
a 4-hour phase tagged `1h` is a footgun. Shipped in lease-reliability
(#57/#58).

#### Sub-plan template (one per phase)

```markdown
# <slug>-<phase-id> — <one-line tagline>

You are phase `<phase-id>` of the `<slug>` plan. <1-2 sentences
describing this phase's job in concrete terms — what the worker
delivers as one commit.>

## Locked decisions (do NOT re-litigate)

See `plans/<slug>.md`. Summary:

- <bullet of phase-specific locked decisions, pulled from master>
- <another>

## Read first

- `plans/<slug>.md` `## Findings log` — prior phases' runtime findings (gotchas, spikes, wrong assumptions); empty if you're the first phase.
- `<file:line>` — <why the worker needs this context>
- `<file:line>` — <another>
- `<existing test file>` — <pattern to mirror>

## Produce

1. **Failing tests first.** <Specific test file + test names.
   Describe the assertions concretely.>

2. **Implementation.**
   - `<file>`: <code shape — function signature, key logic, what
     existing patterns to mirror. Include code snippets when the
     shape is non-obvious.>
   - `<another file>`: <...>

3. **Acceptance.**
   - <Concrete check 1 — e.g. all N new tests green>
   - <Concrete check 2 — e.g. manual smoke command + expected output>
   - <Concrete check 3 — e.g. grep confirms no regressions>

4. **Commit + attest + complete.**
   - **Record cross-phase findings (if any).** If this phase surfaced
     something a later phase would otherwise rediscover — a gotcha, a
     spike result, an API surprise, an assumption that proved wrong —
     append one dated bullet to `## Findings log` in `plans/<slug>.md`
     (create the heading if the master predates this convention).
     Nothing surfaced? Skip it; don't manufacture noise.
   - Structured commit: `<slug>: phase <phase-id> — <scope> (#issue
     if applicable)`.
   - Stage explicit paths: `<file1>`, `<file2>`, `<test file>` (add
     `plans/<slug>.md` if you logged a finding before committing; a
     finding surfaced after the commit rides a follow-up commit —
     re-stamp).
   - **After the commit** (HEAD must be the SHA being attested):
     - `clu verify --plan <slug> --phase <phase-id> --token <T>`
     - `clu attest --simplify --plan <slug> --phase <phase-id> --token <T>`
   - `clu complete --plan <slug> --phase <phase-id> --token <T>`.

## Failure modes to watch

- **<phase-specific gotcha>** — <explanation + mitigation>
- **<another>** — <...>
```

### Step 4: Present the master only and await `ship`

After drafting all files in memory, present **only the master file**
to the operator with this exact framing:

> Here's the master — N sub-plan files drafted alongside it in memory.
> Read the master (locked decisions, non-goals, Sessions index) and
> say `ship` to write + queue, or tell me what to change. If you want
> to see a specific sub-plan before shipping, name it and I'll expand
> it inline.
>
> [If the reuse specialist surfaced a decision]
> **Reuse decision baked in:** plan adopts a refactor-first split of
> `<duplicated surface>` (now the first row of the Sessions index)
> based on `<file:line>` evidence. Say so if you'd prefer
> copy-and-defer.
>
> [If the exclusion specialist surfaced a decision]
> **Exclusion decision baked in:** plan folds `<excluded items>` into
> scope based on `<file:line>` dependency on `<included items>`. To
> keep the exclusion, give me the one-sentence invariant that makes it
> safe.

Then **wait**. Do not write to disk. Silence is not approval. If the
operator picks copy-and-defer for a reuse decision, record the
deferred refactor in the master's Non-goals (with the follow-up) before
shipping.

Sub-plans are intentionally NOT dumped in chat by default. The design
judgment lives in the master (locked decisions, non-goals, Sessions
index); sub-plans are derivative worker-facing detail bounded by those
decisions and are rarely the thing that flips an approval. Pre-rendering
a 7-sub-plan dump is the slowest part of a clu-plan conversation and
mostly doesn't change the operator's decision.

If the operator asks to see a specific sub-plan, expand THAT one inline
— don't volunteer the others. If the operator returns with edits to
the master, apply them to the in-memory draft (including propagating
any locked-decision changes into the affected sub-plans) and re-show
the master.

### Step 5: On `ship`, write files + optionally init/queue

When the operator says `ship` (or equivalent):

1. **Author the plan files in a single tight pipeline.** Write all
   master + sub-plan files via `Write` tool calls in one assistant
   turn. Don't pause between them — the queue-pop-mid-author feedback
   memory documents the failure mode where `clu` cron pops position 1
   before the operator finishes adding positions 2-N.

2. **Plan files MUST land on the OPERATOR's main checkout.** When
   `clu init --worktree --branch` runs, it branches off current HEAD
   — if the plan files are on a different branch, the worker worktree
   won't see them. Commit + push to main BEFORE `clu init`.

3. **Re-validate before a delayed `clu init`.** If queueing happens
   later than authoring — the plan files were written in a prior
   session, or main has advanced since Step 2 verified the plan's
   claims (other plans merged, manual commits landed) — run the
   mechanical drift sweep first: `git log --oneline <commit that
   authored the plan>.. -- <every path in ## Files touched>`. Any
   commit touching those paths is exactly where drift lives; read
   those diffs and re-verify the master's affected claims (or re-run
   Step 2 on the drifted area) before dispatch. A cold worker reads
   Locked-decisions paths as settled and can't detect that main moved
   under them. Authoring and queueing in the same session with no
   intervening merges: skip, nothing can have drifted.

4. **Run `clu init` per plan (if the operator wants queueing now):**
   ```bash
   clu init --project . --plan <slug> --worktree --no-claude-md
   ```
   `--worktree` isolates each plan's worker on its own branch. Use
   `--no-claude-md` if the project's CLAUDE.md is already set up to
   avoid the prompt (most operators).

5. **Run `clu queue add` in ONE call** (atomic per the queue-ux-hardening
   ship):
   ```bash
   clu queue add --project . <slug-1> <slug-2> <slug-3>
   ```

6. **Confirm to the operator** with the dispatched state. Both
   `clu init` and `clu queue add` print a one-line resolved-model
   summary (worker-model-line #51) — surface it to the operator if
   they're choosing between sonnet/opus for this run:
   ```bash
   clu queue list --project .
   clu list                              # fleet view (snapshot)
   clu watch --all --task-list           # fleet stream (alt to list)
   ```

7. **Arm live progress monitoring** via the Monitor tool — only when
   the SessionStart hook hasn't already done it. The hook
   (`end_of_line/hooks/clu_session_start.py`) auto-arms one
   `--task-list` Monitor per active plan on every fresh session in a
   clu-managed cwd, and the hook docstring guarantees idempotency
   (won't double-arm if one is already in flight). So the manual
   block below is the fallback for the "just queued this in the
   current session" case — the hook hasn't fired yet because no new
   session has opened. After `/clear` or a fresh session, the hook
   does it for you.
   ```
   Monitor(
       description="clu <slug> phase progress",
       persistent=True,
       timeout_ms=3600000,
       command="clu watch --project . --plan <slug> --task-list"
   )
   ```
   Each state transition (phase started/completed/blocked/halted)
   arrives as a notification, so you see what clu is doing without
   polling. The operator's UserPromptSubmit hook handles AFK surfacing
   separately; this is the at-desk live feed.

   **Cross-plan wedge events** (`tool_stuck`, `phase_blocked`,
   `attestation_refused`, `stalled_claim_notified`) stream on a
   different filter — `clu watch --all --operator` — armed once per
   session by the user-CLAUDE.md SessionStart instruction (operator
   dashboard, #70). It's complementary to per-plan `--task-list`,
   not redundant: `--operator` is host-wide wedge surfacing,
   `--task-list` is per-plan execution progress.

8. **Tear down the Monitor when the plan completes.** The single
   teardown trigger is `TASK_UPDATE task=<slug> status=completed`
   with NO `parent=` field — that's the whole-plan completion event
   (emitted on `EVENT_PLAN_COMPLETED`), not a phase event. When that
   line arrives, call `TaskStop` on the Monitor's task_id. Without
   teardown the watch is a zombie stream that survives `/clear`
   (because of `persistent: True`) and pollutes future sessions
   with leftover monitors. Don't tear down on `status=in_progress
   msg="paused"` — paused plans can be resumed, and you'd lose the
   live feed for the rest of the run. Defensive: `clu watch`
   processes already running at session start are NOT all leftovers
   — concurrent sessions arm their own watches, and killing theirs
   severs a live feed they won't know to re-arm (observed
   2026-06-12: a second session's startup killed a healthy per-plan
   watch mid-plan). Decide by process-tree ownership before
   touching anything:
   - Ancestor `claude` process alive and YOURS (walk up from your
     shell's `$PPID` — your Bash calls are children of your own
     `claude` pid) → your own post-`/clear` zombie. You can't
     `TaskStop` it (task_ids don't survive `/clear`), so kill the
     PID.
   - Ancestor `claude` process alive but NOT yours → a concurrent
     session owns it. Leave it running.
   - Orphaned (PPID 1 / parent chain dead) → crash leftover; kill
     the PID. Normal session end reaps its own monitors, so
     orphans only come from harness crashes.

### Reacting to task-list protocol notifications

With `--task-list`, the Monitor stream emits two line shapes:

- `TASK_CREATE task=<id> [parent=<slug>] status=pending` —
  bootstrap lines, one per plan + phase, arrive together within
  ~200ms at startup. The parent line (plan itself, `task=<slug>`,
  no `/phase`) has NO `parent=` field. Child lines
  (`task=<slug>/<phase>`) always carry `parent=<slug>`.
- `TASK_UPDATE task=<id> [parent=<slug>] status=<state> msg="<one-liner>"` —
  fired as state transitions happen. `<state>` is one of
  `pending` / `in_progress` / `completed`. `parent=` is present
  on phase-scoped events and absent on plan-scoped events
  (`plan_completed`, `paused`, `resumed`).

**On the bootstrap batch (TASK_CREATE lines):** call `TaskCreate`
once with all matching tasks, ALL `status=pending`. The TaskCreate
API is flat — there's no `parent_id` field — so signal the
hierarchy visually in each child's `subject`:

- Parent (`task=<slug>`, no `parent=`): `subject = <slug>`.
- Child (`task=<slug>/<phase>`, with `parent=<slug>`):
  `subject = "└ <phase>"` — that's a U+2514 box-drawing
  character, single ASCII space, then the phase id. Lock this
  glyph verbatim so every plan renders the same tree.

**On each TASK_UPDATE:** call `TaskUpdate` matching by `task=`
(the task_id). **Do NOT re-set `subject`** — only update
`status` and `description` / `activeForm` to the `msg` content.
Re-setting the subject would either churn the row text or strip
the `└ ` glyph mid-stream and visually un-nest the tree. The
`msg` field carries the human-readable transition reason
(e.g. `"BLOCKED b-12 — should I proceed with X?"` or
`"HALTED (max attempts on foundation)"`) — surface significant
msgs to the operator via PushNotification when the user would
want to act now (halts, blockers).

**Out-of-order arrivals:** if a `TASK_UPDATE` arrives for a
task_id you haven't seen a `TASK_CREATE` for (race condition,
rare), buffer it ~1s and retry. If still no matching task,
create it on-the-fly with the update's status — apply the
same nesting convention: if the line carries `parent=<slug>`,
prefix the subject with `└ ` and the phase id.

**Non-`TASK_*` lines:** the snapshot baseline (`[snapshot] slug:
status, active=...`) and any text-mode lines that leak through
are operator-context only — ignore in the TaskCreate flow.

If the operator hasn't installed the new skill content yet
(`clu install-skill --force --only clu-plan`), the auto-arm
reverts to text mode and notifications won't have the protocol
prefix — fall back to free-text interpretation.

If the operator only wants the files authored (not queued yet), stop
after step 1. Don't run `clu init` without explicit operator intent.

An authored-but-never-init'ed plan is invisible to clu's archive
machinery — nothing will ever clean it up. If it's later superseded or
the operator says it's dead, prepend a one-line `> **ABANDONED
<date>:** <reason>` banner to the master and move the master + every
sub-plan to `plans/archive/<slug>/` yourself. Dead plan files left in
`plans/` are a queue accident waiting for a future session. (When in
doubt whether a lingering plan was ever init'ed, check for
`plans/.orchestrator/<slug>.state.json`.)

## Critical rules

- **Every sub-plan ends with `clu complete --plan ... --phase ...
  --token <T>`.** That's the worker's exit contract (per `/clu-phase`
  SKILL.md and the project CLAUDE.md mandate `--token on every worker
  callback`). Omitting it = lease-expiry = halt.
- **Attestation gate (#55) must be satisfied BEFORE `clu complete`.**
  Sub-plans must include, AFTER the commit and BEFORE complete, both
  `clu verify --plan ... --phase ... --token <T>` (runs project
  verify command, stamps `attestations.verify@HEAD`) and
  `clu attest --simplify --plan ... --phase ... --token <T>` (stamps
  `attestations.simplify@HEAD`). The gate compares stamp SHA against
  HEAD; stale or missing stamps refuse completion with
  `EVENT_ATTESTATION_REFUSED` + an inbox surface. Skip flags exist
  (`--skip-verify`, `--skip-simplify`) but emit audit events — use
  only with operator approval.
- **Phase IDs and plan slugs must match `^[a-z0-9][a-z0-9_-]{0,63}$`.**
  `st.validate_slug` enforces this. Sub-plan filenames derive phase
  IDs by stripping `<plan-slug>-` from the basename.
- **Each phase = one commit, one `/code-review`, one suite-green run, one
  `clu complete`.** Don't batch phases. The cadence is the contract.
- **Operator-approval mandate (user CLAUDE.md) applies.** Novel plan
  files require `ship` from the operator before they land on disk.
  Silence is not approval.
- **Master plans MUST declare `## Files touched`.** List every
  created + modified path with the phase tag, plus API hotspots
  (function signatures, schema fields, config keys). The operator
  uses this at queue time to spot overlaps and serialize conflicting
  plans before they ship — unchecked semantic conflicts across
  worktrees were the canonical failure (clu #50; `cmd_answer`
  argparse drift across plan-locator + blocker-lifecycle, merge SHA
  `1816c0f`). The dry-merge gate (#50) is the safety net; this
  section is the prevention.
- **Plan files commit + push to main BEFORE `clu init --worktree`.**
  Otherwise the worker worktree branches off a HEAD that doesn't have
  the plan files. (Real friction documented in commit `0d8e6d0` —
  cost a worktree round-trip to fix mid-pipeline.)
- **Per-project queue is concurrent, not sequential.** Cron pops one
  head per tick (~30s), but a popped plan dispatches on its own
  worktree and runs alongside any prior plans still in flight. Three
  queued plans = three concurrent workers ~60s apart, NOT one-after-
  another. See "Sequential queue execution requires waiting" below
  before queueing plans that touch overlapping files.
- **New file mirrors an existing file? Refactor first by default.**
  When the plan adds a file described as "mirrors / like / similar to
  / same family as" an existing one — or a same-suffix sibling already
  exists in the target dir — the reuse specialist (Step 2) is
  mandatory and its refactor-first recommendation is presumed
  correct unless the operator overrides at approval. The refactor
  becomes the first row of the Sessions index; the new file is a
  later row.
  Copy-and-defer requires an explicit operator override, recorded in
  the master's Non-goals — not a passive default that leaves
  duplication for `/code-review` after parallel worktrees have already
  forked it.

- **Justify Non-goal exclusions across peer sets.** When a Non-goal
  excludes some members of a peer set and includes others, each
  exclusion needs a one-sentence "why this asymmetry is safe" rationale
  in the Non-goals section. If you can't write it, fold the excluded
  items into scope or restructure to avoid the asymmetry. The
  exclusion-safety specialist (Step 2) surfaces this as a forced binary
  decision at approval; trust its default-include recommendation absent
  an iron-clad invariant. Across worktrees the asymmetry auto-merges
  silently (project CLAUDE.md: "Non-goals are claims that need proof").

- **Cross-phase findings flow through the master's `## Findings log`.**
  A worker that discovers a gotcha, spike result, or wrong assumption a
  later phase would otherwise rediscover appends a dated bullet there
  before its phase commit; every worker reads it at dispatch (clu-phase
  step 4). It's the runtime counterpart to plan-time Locked design
  decisions — same master, different author and time. Deliberately NOT a
  separate research file (clu workers already read the master every
  phase, so a section costs no extra read and `/plan` bans split research
  files) and NOT `state.json` (findings are worker-readable prose, not
  machine coordination). One worktree per plan + sequential phases means
  no intra-plan write contention on the master.

- **No research deferrals — verify or block.** Every cited path,
  function name, schema field, config key, version, or external
  behavior in the master or a sub-plan is verified in Step 2 and
  stated as fact with a file:line / URL+section citation — or the plan
  isn't drafted. There is no `TODO: verify` channel, no placeholder,
  no carve-out. If a claim can't be closed by research, STOP before
  drafting and resolve it with the operator (provide access, run it,
  or pull it from scope). This is *stricter* than `/plan`: a
  cold-context worker reads Locked-decisions paths as settled and has
  no operator to ask — an unverified path ships as wrong dispatch. The
  only things research legitimately can't close are (a) genuine
  operator decisions (surfaced at approval, Step 4) and (b) empirical/
  runtime unknowns that truly need a running app or live system (the
  master's Diagnosis falsifiable test or the algorithmic load-test),
  never Locked-decisions facts. **(b) has a membership test — apply
  it, don't self-certify into it:** a question is empirical ONLY if a
  Read / grep / doc-fetch *this session* genuinely can't close it. If
  reading the code or docs would settle it, it is NOT empirical —
  verify it now. The tell that this is failing is a sub-plan whose
  acceptance says "verify X" where X is statically checkable (does
  this function branch on that flag? does this type have that
  field?) — that's the Step 2 work you skipped, not a deferral.

- **Sub-plan failure modes are not a sink for unresolved
  verifications.** Each `## Failure modes to watch` entry must be a
  genuine runtime/integration risk, not a fact you didn't look up
  wearing risk's clothing. A conditional whose antecedent is
  statically checkable ("*if* this endpoint doesn't return X…") must
  be resolved during Step 2 — phrasing an unverified fact as an "if"
  does not exempt it from verify-or-block; the rule bites on the
  antecedent. A legitimate failure mode is one whose outcome stays
  uncertain *after* everything statically knowable is verified.

- **Perf/bug plans: run the Diagnosis falsifiable test in Step 2,
  BEFORE drafting the Sessions index.** Protocol per the master
  template's Diagnosis commentary (confirmed → scope normally;
  disproved → back to Step 2 with the negative result as the sharper
  question). Files-read alone doesn't ground a diagnosis; "I disabled
  X and the symptom didn't change" does.

- **For ALGORITHMIC plans** (signals: cites a paper, uses a constraint
  solver, implements physics/integrator/control loop), the
  inner-loop specialist (Step 2) is mandatory BEFORE drafting, and its
  brief MUST require these four questions answered:
  1. **What does the canonical implementation do INSIDE the per-tick /
     per-step inner loop, beyond the formula on the page?** Iteration
     counts, regularization, stabilization terms, accumulator resets,
     warm-start clamps, convergence tolerance.
  2. **What fails if we ship just the formula without the surrounding
     solver structure?** Under sustained load (gravity, friction,
     accumulated error) does it drift / diverge / oscillate? Quote the
     failure mode concretely.
  3. **What's the minimum executable test that catches a naive
     implementation?** Exact scenario — initial state, applied forces,
     horizon, expected vs. failing behavior. This becomes the first
     thing validated in phase 1 (land it as phase 1's first commit; if
     it can only run in phase 2, say so and make it phase 2's first
     step). If it fails when it lands, the research was incomplete —
     return to Step 2, don't tune past it.
  4. **What load-bearing details exist in the engine source that are
     absent from the API docs?** Default iteration counts, hardcoded
     thresholds, internal stabilization passes.

### Sequential queue execution requires waiting

`clu queue add` schedules a plan to dispatch on the next cron tick —
typically ~30s later. If a prior plan is still running on a different
worktree, both run **concurrently**. This is safe in isolation but
fails when both plans touch the same file:

- Plan A modifies `foo.py` on branch `clu/plan-a`.
- Plan B (queued before A finished) modifies `foo.py` on branch
  `clu/plan-b`, branched off pre-A-merge main.
- When A merges first, B's diff still doesn't include A's changes →
  merge conflict at integration time, or worse, silent semantic drift
  (the `cmd_answer` argparse incident, merge SHA `1816c0f`).

The dry-merge gate (#50) catches conflicts before B lands but doesn't
recover the wasted worker time. Best to serialize at queue time when
overlap is foreseeable.

**If you want sequential execution** (B starts off post-A-merge main):

1. Author + commit + push plan files for both A and B.
2. `clu init` only A; when the worker reaches DONE, the operator runs
   `clu ship --plan A --yes` (or just `clu ship --plan A` to preview
   first). Mode comes from `.orchestrator.json`'s `dispatch.ship_mode`
   (default `direct`; `as_pr` opens a GitHub PR instead).
3. After `clu ship` lands A on origin/main, `auto_archive_rule` cleans
   up A's worktree on the next tick; `clu init` B off post-merge main.
4. (Optional) `clu queue add` B at step 3 if you want supervisor
   dispatch instead of running it immediately.

**If you want concurrent execution** (default `clu queue add` of both):

- Verify both masters' `## Files touched` sections are disjoint —
  including indirect touches like shared helpers, schema fields, and
  config keys.
- If they overlap, fall back to the serial flow above.

The 2026-05-19 `watch.py` incident (#62 salvage) is the canonical
failure: two plans queued back-to-back, both modified
`end_of_line/watch.py`, second worker had to be paused and its work
salvaged into a one-phase recovery plan.

### Post-worker integration: `clu ship`

Once a plan reaches `STATUS_DONE`, the operator lands it on main
with **`clu ship`** — one verb, one approval. Two modes; the
project's `.orchestrator.json` `dispatch.ship_mode` picks the default:

- **`ship_mode: "direct"`** (default): `clu ship --plan X --yes`
  validates (dry-merge + suite), checks out main, merges (FF-first
  then merge-commit fallback), pushes origin main, and triggers an
  immediate tick so `auto_archive_rule` cleans up the worktree
  without waiting for cron. The feature branch is NOT pushed to
  origin (main carries the work; archive drops the local branch
  shortly after). `keep_remote_branches: true` in
  `.orchestrator.json` makes ship push the branch alongside main AND
  stops archive from deleting any remote `clu/<plan>` ref — relevant
  mainly to `as_pr` runs, where the PR branch lives on origin and
  archive otherwise runs `git push origin --delete` once it merges.
- **`ship_mode: "as_pr"`**: `clu ship --plan X --yes` opens a
  GitHub PR (via `gh pr create`) with the plan body as the PR body,
  stamps `state.ship_pending`, and exits. The operator clicks
  merge on GitHub; `auto_archive_rule` picks up cleanup when
  origin/main advances. Use when CI != local suite (iOS,
  heavyweight CI) or when the operator wants async approval.

Batch form: `clu ship --all-done --yes` ships every DONE plan with
an unmerged branch in one invocation. Per-plan failures are logged
and don't halt the batch.

Preview form: drop `--yes` to see the action list without applying.
Validate-only form: `--check`.

Flag overrides config: `clu ship --plan X --as-pr --yes` (or
`--direct --yes`) forces a mode for one-off ships.

When a plan hits DONE, the supervisor emits `KIND_READY_TO_SHIP`
to the inbox with the exact copy-paste command — operators get a
one-line surface in the channel they already watch (iMessage,
Discord, clu-watch).

**Do NOT use `clu integrate`** — it's now a stderr-warning
deprecation alias for `clu validate` (which is the dry-validate
path `clu ship --check` uses). The verb 'integrate' never updated
main; the rename was the canonical clu-ship.md cleanup.

## Worked example

A 2-issue batch where issues #100 and #101 both touch `auth.py`:

**Master** (`plans/auth-cleanup.md`):
```markdown
# auth-cleanup — close #100 + #101 (smaller diffs, same surface)

Two issues that batch cleanly because both touch
`server/auth.py` and neither introduces new module-level deps.
Smallest-first.

## Locked design decisions

### Phase 1 — #100 (session timeout)
- **Default timeout:** 30 min, configurable via `AUTH_SESSION_TIMEOUT`
  env var.
- **Refresh path:** `/auth/refresh` returns 401 if session age >
  timeout; client retries with re-login.

### Phase 2 — #101 (token rotation)
- **Rotation interval:** every 24h.
- **Old token grace period:** 5 min after rotation before invalidation.

## Non-goals
- Don't migrate the bcrypt → argon2 hash (filed as #102).
- Don't add admin override for the timeout (per security review).

## Files touched
- `server/auth.py` — P1, P2 modified — adds timeout + rotation. API hotspot: `validate_session` signature, `Session` dataclass.
- `tests/test_auth.py` — P1, P2 modified — new tests for both phases.

## Per-phase done checklist
- TDD: failing tests first.
- `/code-review` after if diff >1 file or ~30 lines.
- Full suite green.
- Structured commit format; stage explicit paths.
- **Post-commit attestations:** `clu verify` then `clu attest --simplify`
  (each with `--plan auth-cleanup --phase <id> --token <T>`).
- Call `clu complete --plan auth-cleanup --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| timeout | `auth-cleanup-timeout.md` | Session timeout config + 401-on-expire (#100) | 1h |
| rotation | `auth-cleanup-rotation.md` | 24h token rotation + 5min grace (closes #100 #101) | 2h |

## Findings log

_(empty at plan time — workers append cross-phase findings as phases run)_
```

Why two phases and not one combined commit? Each phase closes an
independent GitHub issue with its own acceptance criteria and its own
deployment risk (timeout misconfig vs. rotation race) — the forcing
function for phasing is "reviewable + revertable on its own", which
each issue satisfies. By contrast, a hypothetical helper `_clear_session(token)`
used only by phase 1 would NOT warrant its own phase: it would collapse
into the timeout phase's commit.

Both sub-plan files (`plans/auth-cleanup-timeout.md` and
`plans/auth-cleanup-rotation.md`) are drafted in memory alongside the
master — each following the sub-plan template above (Locked decisions /
Read first / Produce / Failure modes / `clu complete` exit) — but are
NOT shown in chat. The master's Sessions index names them; the operator
can ask to expand either inline before shipping.

Operator says `ship`. Both sub-plans get written from in-memory drafts
in the same write pipeline as the master:

```bash
git add plans/auth-cleanup*.md
git commit -m "plans: author auth-cleanup batch (closes #100 #101)"
git push origin main
clu init --project . --plan auth-cleanup --worktree --no-claude-md
clu queue add --project . auth-cleanup
clu queue list --project .
```

Then arm live monitoring:
```
Monitor(
    description="clu auth-cleanup phase progress",
    persistent=True,
    timeout_ms=3600000,
    command="clu watch --project . --plan auth-cleanup --task-list"
)
```

## Notes on integrations with other skills

- **`/plan`** is the project-agnostic generic version. Don't replace
  it — it remains the right tool for solo human-authored plans in
  any project.
- **`/clu-phase`** is the worker skill that reads each sub-plan and
  executes it. The sub-plan format you produce here is what
  `/clu-phase` consumes.
- **`/clu-monitor`** installs the in-session inbox hook. After
  queueing plans with this skill, remind the operator to run
  `/clu-monitor` if `~/.config/clu/monitor.json` is missing or v1.
- **`/brainstorm`** is for divergent design exploration BEFORE
  scoping. If the operator hasn't committed to an approach yet,
  suggest `/brainstorm` first, then `/clu-plan` once decisions land.
