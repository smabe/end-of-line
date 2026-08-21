---
name: clu-monitor
description: |
  Use proactively when the user is starting autonomous plan execution
  with clu (after `clu queue add` or `clu init`) and the monitor hook
  is not installed on this machine. Also use when the user says "monitor clu", "notify me when X
  completes", or describes walking away. Idempotent — checks the marker
  first and short-circuits if the hook is already installed.
user_invocable: true
---

## You are the clu monitoring setup skill

This skill installs the `SessionStart` hook that arms the operator
dashboard — a persistent Monitor on `clu watch --all --operator` that
streams wedge events (stuck tools, blockers, refused gates, stalled
claims) into the session live, across every registered plan on the
host. After running this once per machine, the operator can queue plans
and keep working; wedges surface as they happen rather than on the next
prompt.

The `UserPromptSubmit` inbox surface is **retired** and no longer
installs. Live events reach the session through this dashboard Monitor
and away events through the notify channels, which leaves the inbox no
gap to cover. Its code is still on disk and `clu install-hook --inbox`
wires it back up if a gap ever reappears.

The marker rows in clu's host database (`~/.config/clu/clu.db`) are the
source of truth for "is the hook already installed." `clu install-hook`
writes the marker; `clu uninstall-hook` clears it. A leftover
`~/.config/clu/monitor.json` file from an older clu is inert — nothing
reads it, in either schema — so ignore one if you see it.

## Workflow

### 1. Check whether the hook is already installed

```bash
python3 -c "from end_of_line import monitor; print(monitor.load_marker())"
```

Read the printed dict:

- **A marker** (`schema_version: 2`, `session_start_hook_path`,
  `session_start_installed_at`): the hook is installed. Print:

  > Hook already installed at `<session_start_hook_path>` (installed
  > `<session_start_installed_at>`). Settings: `<settings_json_path>`.
  > To reinstall, run `clu uninstall-hook` then re-run `/clu-monitor`.

  Exit. Do NOT touch settings.json.

- **`None`**: no marker — either a clean machine, or one whose only
  monitoring was the legacy `/schedule` install (which has not been
  functional for a long time). Either way this is a clean slate:
  proceed to step 2. If the operator previously scheduled a routine by
  hand, tell them to delete it via `/schedule delete <id>`.

### 2. Install the hook

Run via Bash:

<!-- skilltest -->
```bash
clu install-hook                       # SessionStart (#70 operator dashboard)
clu install-hook --inbox               # also wires the retired inbox surface
```

Plain `clu install-hook` is what you want. Only pass `--inbox` if the
operator explicitly asks for the retired UserPromptSubmit surface back.

This is the canonical install path:

- Adds a `SessionStart` entry to `~/.claude/settings.json` pointing at
  the bundled `clu_session_start.py` script, preserving any existing
  hooks and matching the operator's nested-vs-flat array style.
- With `--inbox`, ALSO adds a `UserPromptSubmit` entry for the retired
  inbox surface. Both entries are idempotent on absolute hook path;
  re-runs are no-ops.
- Refuses to run in non-TTY contexts (workers shouldn't install
  user-level hooks).
- Refuses on malformed settings.json rather than guessing how to
  repair — surfaces a clear error.
- Writes the marker rows on success (with `hook_path` populated only
  when `--inbox` was used).

Capture the output. If `clu install-hook` exits non-zero, report the
error verbatim to the user with one-line diagnosis (most common: the
operator's settings.json has a syntax error and needs hand-fixing
before retry). Do NOT manually edit settings.json from this skill.

### 3. Confirm to the user

On success:

> Background monitoring active. Every fresh Claude Code session now
> arms a persistent Monitor on `clu watch --all --operator`, so wedges
> (stuck tools, blockers, refused gates, stalled claims) stream into
> the session as they happen across every registered plan. Anything
> that fires while you're away reaches you through your notify
> channels. To remove: `clu uninstall-hook`.

## How the surfacing works (for your future self)

Each tick of the supervisor that produces an operator-relevant event
(halt, blocker, plan completion, queue skip, stuck blocker re-ping,
stalled claim transition) appends to the plan's event log and fires a
notification. Two surfaces read those:

- **The dashboard Monitor** — `clu watch --all --operator` tails every
  registered plan's event log and prints one line per wedge. It streams
  forward only: it sets its cursor at the current end of the log when
  it starts, so it shows what happens *while it runs*, never history.
- **The notify channels** — Discord and/or iMessage, fired at the
  moment the event happens.

Supervisor ticks still insert rows into the host database's inbox
table, and the writes are guarded everywhere (the plan's event log is
the source of truth; the inbox is a parallel surface). Nothing reads
those rows while the surface is retired.

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

`clu install-hook` wires this by default, so every fresh Claude Code
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
`--operator` filter narrows the default visible set), each carrying the
investigate-then-recommend contract.

Pre-`/clear` / pre-`/compact` Monitors survive both reset commands per
the research note at `docs/research/monitor-lifecycle.md`, so the
SessionStart hook only matters for genuinely fresh conversations.

## Live in-session feed (`clu watch`)

`clu watch` inside Claude's Monitor tool is the live channel, and with
the inbox surface retired it is the only in-session one:

```
# Single-plan task-list mode (what /clu-plan auto-arms):
Monitor(command="clu watch --project . --plan my-feature --task-list", persistent=True)

# Operator dashboard — cross-plan wedge events only:
Monitor(command="clu watch --all --operator", persistent=True,
        description="clu operator dashboard")
```

Each state transition emits one stdout line, surfaced as a
notification. The two modes are complementary: per-plan task-list for
active plan execution, `--operator` for the cross-plan dashboard.
Neither replays history — arm them before walking away, and rely on the
notify channels for anything that fires while no session is alive.

`--task-list` mode needs `TaskCreate` / `TaskUpdate`, which are not in
the default toolset on Opus 4.8, Sonnet 5, Fable 5, Mythos 5, and newer
models (Claude Code 2.1.233). If they're missing, tell the operator to
set `CLAUDE_CODE_ENABLE_TODO_TOOLS=1` in `~/.claude/settings.json` and
restart; until then the stream is still correct, so read the protocol
lines as plain text rather than ignoring them.

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
- **Leftover `~/.config/clu/monitor.json`.** Inert in either schema —
  nothing reads it. `clu install-hook` writes the marker rows and carries
  no field out of the file. No operator action needed; the quarantine
  recipe in `docs/operations.md` moves it aside.
