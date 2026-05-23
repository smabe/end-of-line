---
name: clu-reply
description: Explicit blocker reply for clu plans. Use when natural-language reply via the inbox-surface isn't appropriate — multi-blocker disambiguation, scripted/non-interactive contexts. Args: `<plan-slug> <answer>` (answer is a 0-indexed option number or free text).
user_invocable: true
---

<!--
Bundled with clu so /clu-reply installs are self-contained. The canonical
copy is end_of_line/skills/clu-reply/SKILL.md in the clu repo.
-->

## You are the clu-reply skill

Explicit, unambiguous answer for an open clu blocker. Use when the
in-session inbox-surface natural-language path needs help — multiple
blockers open and ambiguous, you want to be precise, or you're in a
scripted context.

## When to refuse

- Not in a clu-managed project (no `.orchestrator.json` at repo root)
  → say so, suggest the operator `cd` into a clu project first.
- Plan slug not registered → list registered plans (`clu list`) and ask
  which plan they meant.
- No open blocker on the named plan → say so clearly; point at
  `clu blockers list --project . --plan <slug>` to confirm.

## Workflow

1. **Parse args.** `$1` = plan slug, `$2..` = answer text (rejoin
   multi-word args into a single answer string).

2. **List open blockers** for the plan:
   ```bash
   clu blockers list --project . --plan "$plan"
   ```
   Output format per blocker:
   ```
   <blocker-id> [<phase-id>] (asked <timestamp>)
     <question>
     Options:
       0. <option>
       1. <option>
   ```

3. **Decide:**
   - No open blockers → refuse with "No open blockers on plan `<slug>`."
   - One open blocker → capture its id (first token of the first output line).
   - Multiple open blockers → show them and ask the operator which blocker
     to answer. Do not guess.

4. **Fire the answer:**
   ```bash
   clu answer --project . --plan "$plan" "$answer"
   ```

5. **Report result.** On non-zero exit, surface clu's error verbatim.

## Examples

- `/clu-reply notify-multi-channel 1` — answer option 1 on the open blocker.
- `/clu-reply auth-cleanup "yes, with the bcrypt path"` — free-text answer.
- `/clu-reply my-plan B` — letter answers are accepted; clu resolves them.
