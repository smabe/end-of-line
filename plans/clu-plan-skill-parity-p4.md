# clu-plan-skill-parity-p4 — bundled /plan fork resync

You are phase `p4` of the `clu-plan-skill-parity` plan. Rewrite `end_of_line/skills/plan/SKILL.md` — clu's bundled, sanitized fork of the personal `/plan` skill, frozen at the 2026-06-10 upstream — as a single-file, self-contained adaptation of the CURRENT upstream. One commit. This phase touches a different file than p1-p3 and depends on none of their output; it adapts directly from upstream sources.

## Locked decisions (do NOT re-litigate)

See the master `plans/clu-plan-skill-parity.md`. Binding here:
- **Single file, no siblings.** `clu install-skill` writes exactly `~/.claude/skills/<name>/SKILL.md` per skill (cli.py:2354-2425 @3d51805), so the upstream's `references/` and `scripts/` content either lands inline or is re-expressed — never referenced as a required file.
- **NEVER install this skill on this machine.** `~/.claude/skills/plan` is a symlink into the abe-skills repo; `clu install-skill --force` without `--only` (or with `--only plan`) writes clu's fork THROUGH the symlink, destroying the operator's /plan (cli.py:2394-2415). Ship-time reinstall in this plan is `--only clu-plan`, nothing else. `plan` is VENDORED — drift between the bundle and any installed copy is the designed steady state (cli.py:2285-2293).
- **Sanitization rules carry over from the existing fork's header** (end_of_line/skills/plan/SKILL.md:7-15 @3d51805) and MUST survive in the rewritten header: no hard dependency on skills a public clu install won't have; graphify / security-review references removed; diagnose / code-review / brainstorm survive only as clearly-optional examples ("if your setup has one"); "Do NOT re-sync by blind overwrite" warning retained.
- **Upstream sources to adapt FROM** (on this machine): `~/.claude/skills/plan/SKILL.md` (the 479-line spine) plus `references/TEAM_BRIEFS.md`, `references/AUDIT_BRIEFS.md` (briefs 1-6), `references/PLAN_TEMPLATES.md`, `references/EXECUTION_BRIEF.md`. `references/RATIONALE.md` is dropped: it holds incident stories behind rules; the fork keeps the rules and deletes the links to it.
- **Hook-dependent machinery is re-expressed as install-independent prose.** The upstream leans on three machine-state checks — two hooks (`plan-dispatch-gate.sh`, `plan-draft-gate.sh`) registered in `~/.claude/settings.json`, plus the `plan-check.sh` skill script its Modes invoke by path — all absent on a public clu install, and the upstream itself states that an unregistered checkout "silently reverts to prose" (upstream SKILL.md:465). So: keep the artifact conventions that are useful without enforcement (`**Approval: DRAFT**`/`APPROVED` markers, `Authored at:` line, brief boilerplate, `Plan slug:`/`Plan audit:` brief openers — inert without hooks, load-bearing with them); state the mechanical checks (shard files exist, Phase-map sub-fields, no stray research file, no deferral tokens, archive-move sweeps) as the session's OWN obligations, with one optional note that `~/.claude/skills/plan/scripts/plan-check.sh` runs them mechanically where the full /plan skill is installed; DROP the `Drafting session:` line mechanics and the hook-registration enforcement section (replace with a 3-4 line "optional enforcement" note).
- **Brief boilerplate stays verbatim from upstream** (unlike clu-plan's marker-free adaptation): the fork is /plan-shaped, so where the hooks ARE registered the gate must recognize its briefs — the three fail-closed fragments ("You are NOT to invoke the /plan skill…", the citation requirement, the effort-objection ban) and the `Plan slug:`/`Plan audit:` openers are copied exactly, never paraphrased.

## Read first

- `end_of_line/skills/plan/SKILL.md` — the current fork, whole file (400 lines): the header comment, the sanitization patterns in place (:12 and :30 "if your setup has one"; :102 "if available"), and the clu-boundary rule (:385 "/plan adopts clu-plan's master+shard STRUCTURE, never its dispatch machinery" — upstream carries the same rule; keep it).
- The upstream spine + four reference files named in Locked decisions, in full.
- Master `## Background findings` — install topology and gate facts.

## Work

- `end_of_line/skills/plan/SKILL.md` — full rewrite as the sanitized single-file adaptation:
  1. **Header comment**: updated fork notice — canonical upstream named, sanitization rules restated, blind-overwrite warning, and the new single-file constraint ("upstream is a directory; this fork inlines what it needs because install-skill ships one file").
  2. **Spine**: current upstream Mode 1 (stage-zero forks, three teams, agent-count table + floor, effort dials, draft rules, write + mechanical self-check, VERIFY read-back, forced-binary presentation, approval block), Modes 2-5, Scope Check, Phase Completion Cycle (fresh-worker dispatch, spec check, sweep records, stop conditions), and Rules — sanitized per Locked decisions.
  3. **Inlined reference content**: the Team A/B/C briefs + boilerplate + three conditional specialists; audit briefs 1-4 and 6 (brief 5, the spec reviewer, comes with the Phase Completion Cycle's step 1c) — verbatim where upstream says verbatim; the two plan templates with their section commentary; the execution brief. Trim commentary where it cites RATIONALE.md; keep every rule.
  4. **Kept from the current fork**: the clu-boundary rule; the generic-skill discipline; every "if your setup has one" softening for personal skills.
  5. **Length note**: the result will be large — the adaptation sources total ~1,030 lines (479 spine + 192 TEAM_BRIEFS + 69 AUDIT_BRIEFS + 243 PLAN_TEMPLATES + 46 EXECUTION_BRIEF; the directory's 2,264 total includes RATIONALE and the checker, both dropped). Trim by dropping rationale-links, duplicated origin stories, and hook-mechanics — never by dropping a rule, a brief, or a template section.

- Consumes: none (adapts from upstream sources; independent of p1-p3 outputs)
- Produces: none (no interface any later phase consumes; p4 is the last phase)

## Decisions & findings

**SHIPPED at 2e17c83 (2026-08-10).** Worker findings, transcribed from its report:
- The Done criterion's own sanitization grep initially failed against the HEADER: a fork notice saying "graphify / security-review citations were removed" itself matches the token grep — the old fork's header would have failed too. Fixed by naming the skills obliquely ("knowledge-graph ingestion, the personal security review pass"). Any future re-sync must keep the header free of the literal tokens.
- Upstream grows §-references that resolve only inside its own directory (step 9's "the §5 incident" → RATIONALE.md) — reworded to "the incident behind this clause"; check for new ones on every re-sync.
- The upstream checker-only caveat (probe-stale decided only for letter+digit phase ids) was DROPPED, not softened — the fork's trigger is "read the verification record", which has no id-shape limit. The deferral-token exception was kept in generalized form ("a repo whose plans are ABOUT planning machinery") because it applies to end-of-line itself.
- Result is 1,013 lines vs the probe's ~1,001 (delta: longer header + Optional enforcement section).

**Phase-start probe (2026-08-10, worktree, per the deferred-probe record): GREEN — no APPROACH/MEASURED failures, no MISSING files; gate fidelity proven both directions; ~1,001-line result; suite 1967/1967.** Probe-settled readings — build these, they are validated:
- Where "templates verbatim" collides with "drop the `Drafting session:` mechanics", the Locked decision WINS: the line and its Mode 2 re-stamp sentence are dropped from both template code blocks.
- Layout: spine first, then four trailing top-level sections mirroring upstream's references (team briefs · audit briefs 1-4 + 6 · both plan templates · execution brief), with spine pointers reworded to "the X section below". Brief 5 (spec reviewer) is inlined at the Phase Completion Cycle's step 1c.
- The checker note and hook note merge into ONE `## Optional enforcement` section (~7 lines); the optional note cites the install-target path `~/.claude/skills/plan/scripts/plan-check.sh` (valid on any full install); the header names upstream generically ("the operator's personal /plan skill, a directory").
- The Explore carve-out sentence is kept verbatim as a fourth fail-closed fragment; frontmatter keeps the shipped fork's `user_invocable: true` spelling.
- Absent-machinery assertions inside kept text are SOFTENED, not deleted ("is machine-read" → "may be machine-read"; checker-decided task disjointness → the session's own step-6 check).
- ONE sanitization edit inside a verbatim-marked brief body is authorized: brief 1's "the archive or the repo's own working usage" → "the project's own docs or the repo's own working usage" (personal docs-archive reference; gate reads only the opener, no exposure).
- Personal-project example vocabulary is genericized (SwiftUI view names, the TDEE-row incident); operator quotes and dating parentheticals are trimmed; the OpenSSL-panel and 4,795-tests incidents STAY (the surrounding rules argue from them).
- The gate probe's JSON wrapper needs no `tool_name` key; the worktree sandbox refuses compound redirect commands — assemble the file via Write + Edit appends, and run the gate probe from a scratchpad script.

### Decision: fork keeps upstream markers and verbatim fragments; clu-plan does not  *(status: active)*
- **Rationale:** the two skills sit on opposite sides of the dispatch gate by design — clu-plan briefs must stay invisible to it (its fragments demand /plan-specific wording), while the fork IS a /plan and must be recognized by the gate wherever the hooks exist. Symmetry here would break one side or the other.
- **Alternatives considered:** marker-free fork (uniform with clu-plan) — rejected: a fork user with the hooks registered would author /plan-style plans whose research dispatches bypass the gate entirely.
- **Evidence:** plan-dispatch-gate.sh:153-155,198-208 (markers + fail-closed fragments); master Background findings.

## Failure modes to anticipate

- **Blind overwrite** — copying upstream SKILL.md wholesale reintroduces personal-skill dependencies (the header comment's exact warning). In the current adaptation sources only `/security-review` actually occurs (upstream SKILL.md:91); the wider token sweep (`/graphify`, `claude-code-guide`, abe-skills paths — zero hits today, verified this session) is deliberately over-inclusive because upstream moves. Every `references/` / `scripts/` mention must land as inlined content or an optional note, never a required file.
- **Installing the fork on this machine** while testing — see Locked decisions; never run `clu install-skill` for `plan`.
- **Paraphrasing the fail-closed fragments** while inlining TEAM_BRIEFS — the gate denies briefs assembled from reworded text (upstream's own 2026-08-03 incident); copy the boilerplate block byte-for-byte. Observed live at p2: the gate normalizes whitespace, wrapping, and case before comparing, so re-WRAPPING for this file's line width is safe — only reWORDING fails. A marker merely quoted off the start of a line never fires the gate.
- **Dropping the VENDORED header semantics** — test_skill_drift.py expects `plan` in VENDORED_SKILLS with drift unflagged; nothing in this phase touches cli.py, so only the SKILL.md content changes.
- History docs under `docs/history/` mention the old fork's shape — they are frozen (read-only by project convention); do not "fix" them.

## Done criteria

- Produced observable: assemble a research brief exactly as the rewritten fork's inlined boilerplate instructs (with its `Plan slug:` opener and the three verbatim fragments), wrap it as `{"tool_input": {"subagent_type": "general-purpose", "prompt": "<brief>"}}`, pipe through `~/.claude/hooks/plan-dispatch-gate.sh`, and show the output: NOT denied (fragments recognized). Then a control run with the citation-requirement line reworded, showing the gate DOES deny — proving fragment fidelity end-to-end.
- `grep -in "graphify\|security-review" end_of_line/skills/plan/SKILL.md` returns nothing; `grep -n "references/\|scripts/plan-check" end_of_line/skills/plan/SKILL.md` returns only the optional-checker note and the header's upstream-layout explanation, never a required-file instruction.
- The rewritten fork contains the stage-zero fork rule, all three teams, the read-back step with briefs 1-4 and 6 inline, both templates, the execution brief, the Phase Completion Cycle's spec-check + sweep-record machinery, Modes 2-5, the Scope Check, and the Rules section (verified by section-heading inventory in the phase report), with the sanitization header's blind-overwrite warning, the clu-boundary rule, and the "if your setup has one" softenings retained (Work items 1 and 4).
- Full suite green: `python3 -m unittest discover -s tests`.
