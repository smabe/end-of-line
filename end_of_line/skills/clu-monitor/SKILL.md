---
name: clu-monitor
description: |
  Use proactively when the user is starting autonomous plan execution
  with clu (after `clu queue add` or `clu init`) and
  `~/.config/clu/monitor.json` is absent or carries the legacy v1
  schema. Also use when the user says "monitor clu", "notify me when X
  completes", or describes walking away. Idempotent — checks the marker
  first and short-circuits if the hook is already installed.
user_invocable: true
---

## You are the clu monitoring setup skill

This skill installs the `UserPromptSubmit` hook that surfaces
unprocessed clu events (halts, blockers, plan completions) into the
operator's next Claude turn. After running this once per machine, the
operator can queue plans and walk away — and when they come back and
type anything, Claude already knows what clu did while they were away.

The marker file at `~/.config/clu/monitor.json` is the source of truth
for "is the hook already installed." `clu install-hook` writes the
marker (schema v2) atomically; `clu uninstall-hook` removes it.

## Workflow

### 1. Check whether the hook is already installed

```bash
test -f ~/.config/clu/monitor.json && cat ~/.config/clu/monitor.json
```

Inspect the JSON:

- **v2 marker** (`schema_version: 2`, `hook_path`, `hook_installed_at`):
  the hook is installed. Print:

  > Hook already installed at `<hook_path>` (installed
  > `<hook_installed_at>`). Settings: `<settings_json_path>`. To
  > reinstall, run `clu uninstall-hook` then re-run `/clu-monitor`.

  Exit. Do NOT touch settings.json.

- **v1 marker** (`schema_version: 1`, `schedule_id`, `cadence`): legacy
  `/schedule`-based install from before `/clu-monitor` was rewritten.
  No longer functional. Print:

  > Migrating from legacy /schedule mechanism (no longer functional).
  > Installing the new UserPromptSubmit hook.

  Proceed to step 2.

- **No marker / corrupt**: clean slate. Proceed to step 2.

### 2. Install the hook

Run via Bash:

<!-- skilltest -->
```bash
clu install-hook                       # UserPromptSubmit (inbox surface) only
clu install-hook --session-start       # adds SessionStart hook too (#70 operator dashboard)
```

Pass `--session-start` if the operator wants the cross-plan dashboard
(live Monitor stream of wedges across every registered plan, armed
automatically on cold-start sessions). Without it, only the at-desk
inbox surface installs.

This is the canonical install path:

- Adds a `UserPromptSubmit` entry to `~/.claude/settings.json`,
  preserving any existing hooks and matching the operator's
  nested-vs-flat array style.
- With `--session-start`, ALSO adds a `SessionStart` entry pointing at
  the bundled `clu_session_start.py` script. Both entries are
  idempotent on absolute hook path; re-runs are no-ops.
- Refuses to run in non-TTY contexts (workers shouldn't install
  user-level hooks).
- Refuses on malformed settings.json rather than guessing how to
  repair — surfaces a clear error.
- Writes the v2 marker on success (with `session_start_hook_path` field
  populated when `--session-start` was used).

Capture the output. If `clu install-hook` exits non-zero, report the
error verbatim to the user with one-line diagnosis (most common: the
operator's settings.json has a syntax error and needs hand-fixing
before retry). Do NOT manually edit settings.json from this skill.

### 3. Confirm to the user

On success:

> Background monitoring active. clu writes events (halts, blockers,
> plan completions, stuck blockers, stalled claims) to
> `~/.config/clu/inbox/`. The UserPromptSubmit hook surfaces them into
> your next Claude turn as a system reminder — type anything when you
> return and Claude will see what happened while you were away. Status
> file: `~/.config/clu/monitor.json`. To remove: `clu uninstall-hook`.

## How the surfacing works (for your future self)

Each tick of the supervisor that produces an operator-relevant event
(halt, blocker iMessage, plan completion, queue skip/repair, stuck
blocker re-ping, stalled claim transition) writes a small JSON file
under `~/.config/clu/inbox/`. The hook script reads that directory at
the start of every UserPromptSubmit, filters to events whose
`project_root` matches the current Claude session's CWD, emits a
`hookSpecificOutput.additionalContext` payload (~10K cap, 20 most
recent events), and moves each surfaced event into
`~/.config/clu/inbox/processed/`. Mark-and-sweep dedup. Events older
than the 20-newest cap surface as a footer line.

iMessage notifications (the loud channel) still fire alongside inbox
writes — quiet hours gate them, but inbox writes happen
unconditionally because the inbox is for the operator's *next* turn,
not for waking them.

### Wedge event contracts (#67, #70)

Four event classes carry investigate-then-recommend instruction blocks
that the hook appends to the surfaced context whenever one fires. Each
follows the same shape: **investigate autonomously → recommend a
recovery path → wait for explicit operator approval before any
destructive action**. The receiving session must honor the
operator-approval checkpoint from user-level CLAUDE.md.

- **`tool_stuck` (#67)** — worker's Bash tool stuck at near-zero CPU for
  several minutes. Walk the process tree (`ps -p <worker_pid>` +
  `pgrep -P`), propose `kill` / `clu release-claim` /
  `clu force-complete`, wait for approval.
- **`attestation_refused` (#70 P1)** — worker hit the verify or simplify
  gate. Read the worker log, compare `stamped_at` to current HEAD,
  propose `clu verify` / `clu attest --simplify` /
  `clu complete --skip-verify` / `--skip-simplify`, wait for approval.
- **`stalled_claim` (#70 P4)** — claim lease expired without
  `clu complete`. Read the worker log, walk the pid tree, check
  `git status` for uncommitted work; propose `clu force-complete`
  (work on disk) / `clu release-claim` (worker dead) / `clu retry`
  (clean exit), wait for approval.
- **`phase_blocked`** — already handled by the existing blocker flow
  (`_build_blockers_section` shows the question + options and routes
  the operator's natural-language reply through `clu answer`).

Registry at `end_of_line/hooks/clu_inbox_surface.py::WEDGE_INSTRUCTION_BLOCKS`
— adding a new wedge class is one entry, not a four-step ritual.

### Operator dashboard (#70)

When `clu install-hook --session-start` is used, every fresh Claude Code
session sees an additionalContext block on `SessionStart` instructing
the session to arm:

```python
Monitor(
    command="clu watch --all --operator",
    persistent=True,
    description="clu operator dashboard",
)
```

The Monitor streams only the four wedge events listed above (the
`--operator` filter narrows the default visible set). Combined with the
inbox-hook contracts, the operator sees wedges live (Monitor) AND at
next-turn (inbox), with the same investigate-then-recommend handling
either way.

Pre-`/clear` / pre-`/compact` Monitors survive both reset commands per
the research note at `docs/research/monitor-lifecycle.md`, so the
SessionStart hook only matters for genuinely fresh conversations.

## Live in-session feed (`clu watch`)

The inbox hook is the *AFK* channel — it batches events into the next
user prompt. For *live* streaming while the operator is at-desk, use
`clu watch` inside Claude's Monitor tool:

```
# Single-plan task-list mode (what /clu-plan auto-arms):
Monitor(command="clu watch --project . --plan my-feature --task-list", persistent=True)

# Operator dashboard — cross-plan wedge events only:
Monitor(command="clu watch --all --operator", persistent=True,
        description="clu operator dashboard")
```

Each state transition emits one stdout line, surfaced as a
notification. The three channels are complementary: inbox for the
walk-away path, per-plan task-list for active plan execution,
`--operator` for the cross-plan dashboard.

## Failure modes

- **`clu install-hook` not on PATH.** The user's clu install is broken
  or they're on a fresh machine. Tell them to install clu first:
  `pipx install end-of-line` (or equivalent). Do NOT proceed.
- **settings.json malformed.** `clu install-hook` refuses with a clear
  message. Tell the operator to fix the JSON manually
  (`~/.claude/settings.json`) and re-run `/clu-monitor`.
- **No TTY (running in a worker subprocess).** `clu install-hook`
  refuses with "install-hook requires an interactive shell". This is
  intentional — workers must not install user-level hooks. If you see
  this in a worker log, route the message back to the operator
  explicitly via `clu block` or surface it in the completion summary.
- **Stale v1 marker after a manual reinstall.** `clu install-hook`
  atomically overwrites v1 → v2, no operator action needed.
