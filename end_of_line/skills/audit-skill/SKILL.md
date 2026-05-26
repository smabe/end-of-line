---
name: audit-skill
description: Audit a SKILL.md against the current code to find drift — stale numbers, missing references to shipped features, renamed commands, deprecated patterns. Use when the user wants to refresh a skill after a feature shipment, or proactively before depending on it. Advisory only — produces a punch list for the operator, never auto-edits.
user_invocable: true
---

<!--
Bundled with clu so /audit-skill installs are self-contained. The canonical
copy is end_of_line/skills/audit-skill/SKILL.md in the clu repo. To replace
this bundled copy with a symlink to your own version, run
`clu install-skill --only audit-skill --force` after putting your SKILL.md
at ~/.claude/skills/audit-skill/SKILL.md.
-->

## You are the skill-audit workflow

Skills go stale silently. The clu-plan SKILL.md drifted across a dozen
shipments — stale latency numbers, undocumented Effort column,
missing attestation-gate exit contract — and only got caught because
the operator manually asked "what's outdated here." This skill
systematizes that prompt.

Two mechanical layers (`tests/test_skill_lint.py` and
`tests/test_skill_fences.py`) catch verb renames and broken bash
examples. They don't catch **absence** — a feature shipped in
`cli.py` that the SKILL.md never tells workers about. That's the
class this skill addresses.

## Scope

**Use** when:
- The user invokes `/audit-skill <name>` directly.
- The user asks "is the X skill still accurate?" / "audit the Y SKILL.md".
- After a non-trivial feature shipment that touched surfaces a bundled
  skill references (cli.py, hooks/, state.py events, supervisor
  rules).

**Refuse / fall back** when:
- The skill name doesn't match a directory under
  `end_of_line/skills/`. Output the valid list.
- The user wants you to auto-apply fixes. This skill is advisory by
  design — produce findings, let the operator decide what to land.

## Workflow

### Step 1: Resolve the target

The user passes a skill name like `clu-plan` or `audit-skill`.

1. Check `end_of_line/skills/<name>/SKILL.md` exists. If not, list
   the valid names from `BUNDLED_SKILLS` in `end_of_line/cli.py` and
   stop.
2. Note the skill's last-modified commit:
   `git log -1 --format='%h %ci' end_of_line/skills/<name>/SKILL.md`.
   The audit window is "shipments since this commit."

### Step 2: Read the skill in full

Use the Read tool on the SKILL.md. Don't skim — drift hides in
specific clauses, not section headers. Pay attention to:

- **Numbers**: any concrete value (defaults, timeouts, thresholds,
  intervals, counts). These rot fastest.
- **Command examples**: every `clu <verb> ...` invocation.
- **Sequence claims**: "after X, do Y" — these tie to gate
  enforcement and can become stale when new gates ship.
- **Cross-references**: mentions of other commands, hooks, state
  fields, events.

### Step 3: Map the skill's claimed surfaces to current code

For each substantive claim, locate where the code enforces or
implements it. Common pairs:

| Skill claim type | Where to look |
|---|---|
| `clu <verb>` invocation | `end_of_line/cli.py` argparse + `cmd_<verb>` function |
| Default values (TTLs, intervals, thresholds) | `ProjectConfig` / `parse_*` helpers in `cli.py` + `state.py` |
| Gate / refusal behavior | `cmd_complete` / `cmd_verify` / `cmd_attest` enforcement blocks |
| Event names referenced | `EVENT_*` constants in `state.py` |
| Hook behavior | `end_of_line/hooks/*.py` |
| Supervisor rules / tick priority | `supervisor.py` priority chain |
| Worker callback contracts | `/clu-phase` SKILL.md + token-validating wrappers in cli.py |

For each claim, ask: *does the code still match what the skill says,
or has the code moved on?*

### Step 4: Identify what's shipped that the skill doesn't mention

This is the hard part — the class mechanical lints can't catch.

Read the project memory index for shipped features since the skill's
last edit:

```
~/.claude/projects/-Users-smabe-projects-end-of-line/memory/MEMORY.md
```

Walk every entry dated after the skill's last commit. For each:

- Did this shipment touch a surface the skill describes?
- If yes, does the skill still reflect post-shipment reality?
- Common drift triggers: new commands, new gates, new events,
  default-value changes, hooks getting auto-invoked, schema fields,
  config knobs.

Also useful: `git log --since="<skill-edit-date>" --oneline -- end_of_line/cli.py end_of_line/state.py end_of_line/supervisor.py end_of_line/hooks/` — names the commits that may have introduced drift.

### Step 5: Produce a punch list

Output a structured report:

```
## Audit of <skill-name>/SKILL.md
Last edited: <commit + date>
Shipments since: <N>

### Substantive gaps (correctness)
1. **<short title>** — <SKILL.md line range>
   <description: what's stale + what the code does now + cite file:line>
   Suggested fix: <concrete edit>

### Important gaps (clarity / completeness)
N. **<short title>** — <SKILL.md line range>
   ...

### Smaller improvements
N. **<short title>** — <SKILL.md line range>
   ...

### Suggested order
<which to land immediately vs. bundle later>
```

Each finding must include:
- A file:line citation from the live code that grounds the claim.
- An MEMORY.md / git-log reference for the shipment that introduced
  the drift (helps the operator confirm context).
- A specific suggested fix, not "consider updating this." Drafting
  the fix forces the audit to be concrete.

### Step 6: Don't auto-edit

This skill is advisory. Present the punch list and wait. The operator
decides which findings to land. If they say "apply 1 and 2", proceed
with explicit Edit calls; otherwise stay hands-off.

## Severity classification

Use three buckets:

- **Substantive gap (correctness)** — workers or operators following
  this skill will hit a wall the SKILL.md never warned them about.
  The attestation-gate omission in clu-plan was this class. Land
  immediately.
- **Important gap (clarity)** — the skill works but obscures
  load-bearing mechanics (e.g. the Effort column scaling lease TTL).
  Land soon; not urgent.
- **Smaller improvement** — new feature exists, skill doesn't mention
  it, no one's burned yet. Bundle with the next refresh.

## What NOT to flag

- **Style preferences.** Section ordering, header levels, bullet vs
  numbered list. Not drift, just taste.
- **Missing examples.** "This could use another example" is a feature
  request, not drift.
- **Pre-existing technical debt unrelated to recent shipments.** If
  the gap existed when the skill was last edited and nothing has
  shipped since to make it worse, it's not what this skill is for.
- **Improvements you'd make to a fresh-author skill.** Audit asks
  "did reality drift past what this says," not "could this be
  written better."

## Worked example (clu-plan, 2026-05-26)

A real audit ran against `clu-plan/SKILL.md` (last edited 2026-05-19,
6 shipments since) and surfaced three substantive gaps:

1. **Attestation gate missing from sub-plan exit contract.** SKILL.md
   said "structured commit → `clu complete`" but skipped
   `clu verify` + `clu attest --simplify`. `cli.py:4083-4146`
   refuses completion with `EVENT_ATTESTATION_REFUSED` when stamps
   are stale. Workers following the template would hit a wall.
   Shipped: attestation-gate (#55, 2026-05-18) per MEMORY.md.

2. **Effort column unexplained.** Sessions index template showed
   `| Effort |` with `<Nh>` placeholder; no mention that
   `parse_effort_minutes()` (cli.py:1743-1745) reads it to scale
   per-phase lease TTL. Plans with undersize Effort lease-expire
   mid-phase. Shipped: lease-reliability (#57/#58, 2026-05-19).

3. **Step 6 manual Monitor arming outdated.** SKILL.md instructed
   manual `Monitor(...)` arming after queueing. The SessionStart
   hook at `end_of_line/hooks/clu_session_start.py:104` now
   auto-arms per-plan `--task-list` Monitors. Shipped:
   session-arm-task-list (2026-05-24).

Plus three smaller improvements (fleet streaming alternative, worker
model line, operator dashboard reference). Operator landed all six
in commit `16cc211`.

This is the shape of a good audit report — concrete, cited,
actionable.

## Notes on integrations

- **`tests/test_skill_lint.py`** catches `clu <verb>` drift
  mechanically. Don't re-report what the lint already covers — the
  audit's job is the *absence* class.
- **`tests/test_skill_fences.py`** catches bash-flag drift inside
  tagged fences. Same rule — don't duplicate.
- **`/clu-plan`** is downstream — if the audit produces findings that
  warrant a code change (e.g. "the skill is right; the code needs to
  catch up"), scope that as a plan there.
- **`/post-ship`** is the natural trigger — after a non-trivial
  shipment, run `/audit-skill` against any skill the shipment
  touched. Not yet automated; suggest it manually until then.
