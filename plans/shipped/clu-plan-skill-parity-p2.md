# clu-plan-skill-parity-p2 — research-model rewrite: teams + stage-zero forks

You are phase `p2` of the `clu-plan-skill-parity` plan. Replace clu-plan's Step 2 pre-author research model (three topic dimensions, one of them an `Explore`-typed agent) with an adaptation of `/plan`'s team model, add stage-zero design-fork settlement, and delete every instruction that routes an author into `/plan`'s files. One commit, all in `end_of_line/skills/clu-plan/SKILL.md`.

## Locked decisions (do NOT re-litigate)

See the master `plans/clu-plan-skill-parity.md`. Binding here:
- **Self-contained:** every adapted brief lives INLINE in this SKILL.md. `clu install-skill` installs exactly one file per skill (cli.py:2354-2425 @3d51805), so a `references/` sibling would never reach an install. Pointing at `~/.claude/skills/plan/...` is banned for the same reason (absent on public installs).
- **No dispatch-gate markers:** the adapted briefs and boilerplate must NOT contain line-start `Plan slug:`, `Plan audit:`, or `Plan work:`. The machine-wide gate denies a `Plan slug:`-marked brief that lacks /plan's exact fragment "you are NOT to invoke the /plan skill" (plan-dispatch-gate.sh:198-208), and clu-plan's boilerplate deliberately says "/clu-plan or /plan" instead. Unmarked briefs are invisible to the gate, which is the intended state. `Plan work:` is additionally poisoned: it silently enters the gate's audit mode.
- **All research agents are `subagent_type: "general-purpose"`.** Ground the ban with the vendor quote: "Explore and Plan are the only subagents that omit CLAUDE.md and git status. There is no frontmatter field or per-agent setting to change which agents skip them" (code.claude.com/docs/en/sub-agents, "What loads at startup"). An Explore-typed researcher silently loses every standing project rule.
- The source material to adapt FROM is `~/.claude/skills/plan/SKILL.md` Mode 1 step 4 and `~/.claude/skills/plan/references/TEAM_BRIEFS.md` (available on this machine; read them, then write self-contained adaptations — never a pointer to them).

## Read first

- `end_of_line/skills/clu-plan/SKILL.md` :96-224 — the Step 2 being replaced, including the passages that SURVIVE (listed under Work).
- `~/.claude/skills/plan/SKILL.md` Mode 1 step 4 — stage zero, teams, agent-count table, effort dials, neutral-brief rule.
- `~/.claude/skills/plan/references/TEAM_BRIEFS.md` — the A1/A2/A3/B1/C1/C2 brief bodies and conditional-specialist briefs.
- Master `## Background findings` — gate discriminators and the preserved-passages list.

## Work

- `end_of_line/skills/clu-plan/SKILL.md` — rewrite Step 2 ("Pre-author research") as follows:
  1. **Stage zero — settle design forks first.** Before any team is briefed: if two or more candidate designs would produce a DIFFERENT Sessions-index row set (row count, ordering, scope, or effort), the fork is settled now by a comparison probe — one worktree-isolated agent (`isolation: "worktree"`) that builds the smallest version of BOTH candidates and measures the discriminating property, recommends nothing, and reports the measurement. Adapt /plan's comparison-probe contract (brief 6 in `~/.claude/skills/plan/references/AUDIT_BRIEFS.md`) inline: minimum build, everything else held constant, must not reason from docs alone, must not judge quality, must not invoke any plan skill; paste the candidate descriptions verbatim into its prompt (a worktree branches from the default branch and carries only tracked files — code.claude.com/docs/en/worktrees — so it cannot read unpushed plan files). The verdict lands in Locked design decisions. Forks surfaced later by research are probed retroactively and affected teams re-dispatched. A question that changes only a sub-plan's internals, not the row set, does not probe. State the clu-specific stake: a cold worker inherits an unsettled fork with no operator to ask — the anti-pattern is a Sessions-index row whose scope is "decide X".
  2. **Replace the three dimensions with three teams**, adapted inline with clu vocabulary, each brief in a fenced block ready to paste: Team A — change impact (A1 fan-in/observable behavior, A2 incidental behavior, A3 shared state/ordering), skipped only when the plan creates new code and modifies none (read the draft Files-touched list to decide); Team B — adversarial code read (B1, incl. the from-scratch-shape question; 2 agents when the change spans modules); Team C — implementation specialists (C1 project-local API docs + canonical samples, C2 web prior art). Carry the NEUTRAL-BRIEF rule for A and B verbatim in spirit: operator's goal in the operator's words, never the intended approach. Build a single fenced boilerplate block appended to every brief from clu-plan's EXISTING lines (slug + one-line goal; "You are NOT to invoke `/clu-plan` or `/plan`; research only, report under 400 words"; the citation request — all at :141-143 @3d51805) PLUS two lines clu-plan does not have today, adapted from /plan's boilerplate: the unopened-source clause ("a claim you did not open a source for is reported as unverified, not as a finding") and the effort-objection ban ("diff size, file count, and implementation effort are not your inputs; recommend what is correct"). Reworded freely EXCEPT the block must never gain the banned line-start markers.
  3. **Agent-count table + floor, adapted:** 4 agents for a plan that only creates new code (B1+C1+C2+triggered conditionals); 7 when it modifies existing code (A1-A3+B1+C1+C2); +1-2 for extra dimensions; stop when the marginal agent re-covers ground. Floor: a single-phase plan (Sessions index with ONE row) collapses Team A to one agent carrying A1+A2+A3 concatenated. Effort dials: teams at session effort, conditional specialists at low — headcount cuts a question, effort doesn't.
  4. **Keep the three conditional specialists** (:181-208) with their triggers and forced-binary contracts; align the reuse specialist's scope with /plan's current text: SOURCE files only, never markdown/docs/skills/prompt-templates/config.
  5. **Delete the `/plan` deference:** the sentence "The full role-split catalog lives in `/plan` Mode 1 step 4" (:177-178) is replaced by an inline 3-4 line role-split example list (algorithmic, LLM-orchestration, backend, cross-cutting refactor — one line each). After this phase, `grep -n "Mode 1 step 4" end_of_line/skills/clu-plan/SKILL.md` returns nothing.
  6. **Preserve untouched** (verbatim or lightly re-anchored): the phase-granularity section (:69-94), the /diagnose hand-off (:108-113), the three framing questions (:145-157), the skip-condition paragraph (:210-214), the consolidation contract + verify-or-block pointers (:216-224), and everything outside Step 2. The "same unconditional explore gate as /plan Mode 1 step 4" parity claim (:98-99) is reworded to describe the gate without citing /plan's internal step numbers.

- Consumes: none
- Produces: the adapted brief boilerplate block (fenced, marker-free) — p3's auditor step restates its citation and no-invoke lines

## Decisions & findings

**SHIPPED at 39264f8 (2026-08-10).** Worker findings, transcribed from its report:
- The dispatch gate NORMALIZES whitespace, wrapping, and case before fragment comparison (per its own deny text, observed live) — re-wrapped pastes of /plan boilerplate pass; only REWORDING fails. Directly relevant to p4's fork resync.
- The gate has a quoting escape hatch: a marker merely QUOTED off the start of a line (indented or inline) never fires it — which is why the rewritten Step 2 can safely DISCUSS the markers.
- `Mode 1` now has zero occurrences file-wide, and the old "three mandatory dimensions" vocabulary is fully purged (checked per the purge-deleted-vocabulary rule).
- The live control run's deny fired on exactly the predicted fragment mismatch ("/clu-plan or /plan" ≠ "the /plan skill").
- File grew 984 → 1255 lines; boilerplate block at :369, stage zero at :116, teams at :198 @39264f8.

### Decision: briefs stay invisible to the dispatch gate  *(status: active)*
- **Rationale:** adopting `Plan slug:` markers would subject clu briefs to fragment matching pinned to /plan's exact current wording; the fragments fail CLOSED, so the next /plan rewording would break clu authoring machine-wide.
- **Alternatives considered:** full marker + fragment adoption (gap-list item 5) — deferred by operator scoping, recorded in master Non-goals.
- **Evidence:** plan-dispatch-gate.sh:153-155,198-208,242-255 @abe-skills (fragments verified against the script this session).

## Failure modes to anticipate

- A brief block gains a line that begins with `Plan slug:` / `Plan audit:` / `Plan work:` (e.g. by copying /plan's boilerplate fence wholesale) — the gate starts seeing clu dispatches and denies them on the "/clu-plan or /plan" phrasing. The Done-criteria gate run exists to catch exactly this.
- The rewrite clobbers a load-bearing survivor: Findings-log rules, specialist back-references from Critical rules (:688-707, :761-782), or the verify-or-block pointer structure (single full statement :721-742, pointers elsewhere). Check each against the master's preserved-passages list before committing.
- Example dispatches in the new text that name `subagent_type: "Explore"` anywhere — grep for `Explore` after the rewrite; the only mentions left are the ban sentence itself.
- Renumbering Steps 3-5 — external references pin them (operator memory pins "Step 5"; hook comment cites the protocol section). Step 2's internal structure may change freely; the step numbers of 1-5 must not.

## Done criteria

- Produced observable: assemble one research brief exactly as the new Step 2 instructs (any team, boilerplate appended), wrap it as the gate's stdin payload — `{"tool_input": {"subagent_type": "general-purpose", "prompt": "<the assembled brief>"}}` (the gate reads `tool_input.prompt`; verified plan-dispatch-gate.sh:131-137 @abe-skills) — pipe it through `~/.claude/hooks/plan-dispatch-gate.sh`, and show the gate's output in the phase report: it must NOT deny (the gate never saw a marker). Then flip the same payload's prompt to start one line with `Plan slug:` and show the gate DOES deny it — proving the run exercised the gate rather than a broken harness.
- `grep -nE "^(Plan slug|Plan audit|Plan work):" end_of_line/skills/clu-plan/SKILL.md` inside fenced brief blocks returns nothing, and `grep -n "Mode 1 step 4" end_of_line/skills/clu-plan/SKILL.md` returns nothing.
- `subagent_type: "Explore"` appears zero times outside the sentence banning it.
- Full suite green, including `tests/test_task_list_skill_wire.py` and `tests/test_skill_wire.py` untouched-section pins.
