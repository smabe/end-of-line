<!--
This is a frozen clone of the operator's `/brainstorm` skill, bundled
with clu so installs are self-contained. The canonical version
may drift in the operator's private skills repo. To replace this
bundled copy with a symlink to your own version, run
`clu install-skill --only brainstorm --force` after putting your
SKILL.md at ~/.claude/skills/brainstorm/SKILL.md.
-->

---
name: brainstorm
description: Launch parallel persona agents to analyze a feature from multiple angles, then consolidate into a master plan
user_invocable: true
---

## Brainstorm Workflow

Launch 3-6 parallel agents, each with a different persona, to analyze a proposed feature or design decision. Each agent writes their own analysis file, then consolidate into a master plan.

### Step 1: Identify Personas

Based on the feature being analyzed, select relevant personas. Common personas:

| Persona | Focus |
|---------|-------|
| **UX Designer** | Interaction flow, visual design, accessibility, consistency with existing patterns |
| **iOS Engineer** | Architecture, API constraints, performance, dependencies, code design |
| **Data Analyst** | What's valuable, what's misleading, what data needs context |
| **QA Tester** | Edge cases, failure modes, adversarial inputs, regression risks |
| **Privacy/Compliance** | App Store guidelines, regulatory, data handling, disclaimers |
| **End User** | What would a real user expect, get confused by, or love? |

Ask the user which personas to include. Default to the 5 most relevant. Don't exceed 6.

### Step 2: Launch Agents

Launch all persona agents **in parallel** using `run_in_background: true`. Each agent:

1. Receives a thorough brief with full context about the feature and existing codebase patterns
2. Writes their analysis to `.claude/plans/{feature}-{persona}.md`
3. Covers their domain thoroughly with specific, actionable recommendations

**Agent prompt template:**
- Start with "You are a {persona} reviewing a proposed {feature} for {app}."
- Include relevant context: tech stack, design language, existing patterns, known constraints
- List 8-12 specific topics to cover
- Tell them to write to a specific file path
- Ask for specific recommendations, not vague observations

### Step 3: Consolidate

After all agents complete, read every persona file and create a master plan at `.claude/plans/{feature}-master.md`:

**Master plan structure:**
```markdown
# {Feature} — Master Plan

## Context
Why we're building this, what problem it solves.

## Design Decisions
Key decisions with rationale, noting which persona raised the point.

## Architecture
Technical approach, informed by the iOS Engineer + QA analysis.

## UX Specification
Interaction flow, states, layout — from UX Designer.

## Safety & Compliance
Guardrails, disclaimers, refusal categories — from Privacy/Compliance.

## Suggested Scope (MVP)
What to build first, what to defer. Informed by all personas.

## Open Questions
Unresolved disagreements between personas, things that need user input.

## Test Plan
Key scenarios to verify, from QA + Engineer.
```

### Step 4: Present to User

Summarize the master plan concisely and ask for feedback before implementation.

### Rules
- Always ask the user which personas to use before launching
- Each persona brief must include enough context to be standalone — agents don't share context
- Persona files are working documents — the master plan is the deliverable
- Flag disagreements between personas as open questions rather than picking a winner
- The master plan should be concise enough to scan but detailed enough to implement from
