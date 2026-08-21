---
name: clu-monitor
description: |
  Use proactively when the user is starting autonomous plan execution
  with clu (after `clu queue add` or `clu init`) and the monitor hook
  is not installed on this machine. Also use when the user says "monitor clu", "notify me when X
  completes", or describes walking away. Idempotent — checks
  settings.json first and short-circuits if the hook is already installed.
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

The same hook also **surfaces open blockers for the current project** at
session start — each blocker's question, numbered options, and id, plus
the routing line `clu answer --project . --plan <slug> --blocker <q-N>
<answer>`. The Monitor stream is forward-only, so it never replays a
blocker raised before the session began; this state read covers that
gap. When more than one blocker is open the instruction tells the
session to ask the operator which one they mean and pass its
`--blocker <q-N>` rather than guess.

The `UserPromptSubmit` inbox surface is **retired** and no longer
installs. Live events reach the session through this dashboard Monitor
and away events through the notify channels, which leaves the inbox no
gap to cover. Its code is still on disk and `clu install-hook --inbox`
wires it back up if a gap ever reappears.

**`~/.claude/settings.json` is the source of truth for "is the hook
already installed."** It is the file Claude Code reads, so it is what
decides whether the hook fires. clu also keeps an install RECORD in its
host database (`~/.config/clu/clu.db`) — that is metadata: when the
install happened and which settings file it wrote into. It decides
nothing, and a machine where the two disagree is normal. Ask
`clu install-hook --check`, never the record. A leftover
`~/.config/clu/monitor.json` file from an older clu is inert — nothing
reads it, in either schema — so ignore one if you see it.

## Workflow

### 1. Check whether the hook is already installed

<!-- skilltest -->
```bash
clu install-hook --check
```

Read-only; it writes nothing. Output looks like:

```
Settings: /Users/you/.claude/settings.json
SessionStart: installed (recorded 2026-08-21T03:00:00Z)
UserPromptSubmit: not installed
```

The `SessionStart` line is the one that matters — that is the operator
dashboard. `UserPromptSubmit` is the retired inbox surface and is
expected to read `not installed`.

- **`SessionStart: installed`**: the hook is in settings.json. Print:

  > Hook already installed. Settings: `<Settings path>`. The install was
  > recorded `<recorded timestamp, if the line has one>`. To reinstall,
  > run `clu uninstall-hook` then re-run `/clu-monitor`.

  Exit. Do NOT touch settings.json. A missing `recorded …` is not a
  problem — it only means clu has no record of the install (a hook added
  by hand, or a record cleared since), and the entry in settings.json is
  what makes it fire.

- **`SessionStart: not installed`**: a clean slate — either a fresh
  machine, or one whose only monitoring was the legacy `/schedule`
  install (which has not been functional for a long time). Proceed to
  step 2. If the operator previously scheduled a routine by hand, tell
  them to delete it via `/schedule delete <id>`.

- **`SessionStart: unknown (settings.json could not be read)`**: clu
  could not parse the file — do NOT treat this as "not installed" and do
  NOT install over it. Tell the operator to fix
  `~/.claude/settings.json` (most often a syntax error mid-edit) and
  re-run `/clu-monitor`. Installing here would either duplicate a
  working hook or fail outright.

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
  inbox surface. Both entries are idempotent on the hook script's
  BASENAME, so a clu that moved (reinstall, new venv, second checkout)
  is recognised rather than duplicated; re-runs are no-ops, and any
  duplicates an older absolute-path install left are pruned.
- Runs fine without a TTY — that is what lets you invoke it from Bash
  here.
- Refuses on malformed settings.json rather than guessing how to
  repair — surfaces a clear error.
- Records the install on success (with `hook_path` populated only when
  `--inbox` was used). The record is metadata; what makes the hook fire
  is the entry in settings.json.

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
- **`phase_blocked`** — the live Monitor line names the blocker but
  carries no options. Its options and reply routing come from the
  SessionStart hook's open-blocker section (described above), which
  renders the question + numbered options and the
  `clu answer … --blocker <q-N>` line for every open blocker in the
  project. For a blocker raised mid-session, `clu blockers list` shows
  the options on demand.

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
- **`settings.json` unreadable.** `clu install-hook --check` reports
  `unknown (settings.json could not be read)` rather than "not
  installed" — three states, on purpose, so a locked or half-edited file
  never sends you to reinstall a working hook. Do not install over it;
  ask the operator to fix the file.
- **The install record disagrees with settings.json.** Normal, and not
  an error either way. The file decides; the record is only a date.
  Never report installed-ness from `monitor.load_marker()`.
- **Leftover `~/.config/clu/monitor.json`.** Inert in either schema —
  nothing reads it. No operator action needed; the quarantine
  recipe in `docs/operations.md` moves it aside.
