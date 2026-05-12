# End of Line

> "End of line."
> — Master Control Program

A cron-driven plan orchestrator. You write a multi-phase plan as markdown; `clu` dispatches each phase to a fresh Claude session, tracks state in atomic JSON, and pings you on iMessage when it hits a question. Workers run cold (no carried-over context), report back via CLI callbacks, and the supervisor advances the plan one tick at a time.

The system runs itself: the [halt-bypass feature](https://github.com/smabe/end-of-line/commit/aef2b81) that decided whether halts should bypass quiet hours was shipped by clu — a worker opened the blocker, I answered via iMessage, the worker resumed, edited `notify.py`, wrote tests, and committed.

## Status

v0.1, working. 233 tests pass (`python3 -m unittest discover -s tests`). Stdlib-only Python 3.11+. macOS-targeted today because the iMessage adapter uses `osascript` and the chat.db poller reads Apple's local SQLite — pluggable backends (Slack / stdout / etc.) are tracked in [#11](https://github.com/smabe/end-of-line/issues/11).

Recent ships, all driven by clu on itself: configurable worker PATH ([`dispatch.path`](#configure-a-project), closes [#9](https://github.com/smabe/end-of-line/issues/9)), self-contained skill bundling (`clu install-skill` now ships `/clu-phase` + `/plan` + `/brainstorm` with a `--only` flag), and a Day-4 sweep that closed 6 backlog issues across 4 self-dispatched bundle plans.

## How it works

- **State lives outside sessions.** Each plan owns `<project>/plans/.orchestrator/<slug>.state.json`. Workers don't carry context; they read state on startup.
- **Atomic writes under a lock.** Every mutation is `tmp + fsync + rename` under `flock`. Two ticks colliding is safe.
- **Append-only event log.** Phase claims, completions, lease expirations, blockers — all derivable from `events[]`. State corruption is recoverable by replaying.
- **`/plan` convention.** Phase declarations come from the master plan's `## Sessions index` markdown table. The parser is 80 lines.
- **System cron is the heartbeat.** No long-running orchestrator process. Each tick is ~50ms of Python; the supervisor itself burns zero LLM tokens. Workers are the only thing that costs API money.
- **iMessage round-trips.** Outbound via `osascript`; inbound via a tiny LaunchAgent that polls `chat.db` and routes replies back into `clu answer`. Quiet hours (default 22:00–08:00) gate non-halt notifications.

## Install

```bash
git clone https://github.com/smabe/end-of-line.git
cd end-of-line
pipx install -e .          # puts `clu` on $PATH via its own venv
clu install-skill          # copies the bundled skills (/clu-phase + /plan + /brainstorm) into ~/.claude/skills/
```

On macOS, `pip install` is usually blocked by PEP 668 — `pipx` is the path that works without `--break-system-packages`.

`clu install-skill` writes three bundled skills into `~/.claude/skills/`, one subdirectory per skill. Pass `--force` to overwrite an existing regular file (symlinks are overwritten without it), `--dry-run` to preview, or `--only <name>` to install just one.

For the inbound iMessage poller, grant Full Disk Access to the pipx venv python (System Settings → Privacy & Security → Full Disk Access → add `~/.local/pipx/venvs/end-of-line/bin/python3`). Without it, the poller can't open `chat.db`.

(Optional) Install the LaunchAgents from `examples/` for cron-driven dispatch — see `docs/operations.md`.

## Working with clu

`clu install-skill` ships three skills:

- **`/clu-phase`** — the worker skill clu's dispatch invokes for each phase. Required for clu to function; you don't run it directly. The dispatch command in `.orchestrator.json` (see [Configure a project](#configure-a-project)) launches Claude with this skill so each phase honors the worker callback contract.
- **`/plan`** — authorship skill for writing plans clu can orchestrate. Drops a file at `plans/<slug>.md` in your project with a `## Sessions index` table — that table is what clu's parser reads to know which phases to dispatch.
- **`/brainstorm`** — parallel-persona pre-planning. Launches 3-6 agents (UX, engineer, QA, …) in parallel to analyze a feature from different angles, then consolidates their outputs into a master plan. Useful before `/plan` when the problem space is fuzzy and you'd rather explore than guess.

### Recommended workflow

For non-trivial work, the combo is **brainstorm → grill-me → plan → clu**:

1. **`/brainstorm`** — parallel personas explore the design space and consolidate into a master plan.
2. **`/grill-me`** by Matt Pocock ([source](https://github.com/mattpocock/skills), installed separately) — interviews you relentlessly until each decision branch is resolved.
3. **`/plan`** — commit the agreed approach to `plans/<slug>.md` with the `## Sessions index` table clu's parser expects.
4. **`clu init`** — hand it to clu, which dispatches each phase as a cold-context worker subprocess.

Each skill is independent — use one, all four, or none. The combo just makes ambitious work less likely to drift mid-flight. `/grill-me` is the only piece clu doesn't bundle; install it yourself when you want it.

### Minimum plan shape clu can orchestrate

If you skip the bundled `/plan` and hand-roll a plan, clu's parser needs the master file (`plans/<slug>.md`) to contain a `## Sessions index` table:

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| phase-a | `<slug>-phase-a.md` | one-line scope | time est |
| phase-b | `<slug>-phase-b.md` | one-line scope | time est |

Each row points to a sub-plan file in the same `plans/` directory. The bundled `/plan` produces this shape by default.

## Configure a project

Drop a `.orchestrator.json` at your project root (it's gitignored by example since it holds your iMessage handle):

```json
{
  "plan_dir": "plans",
  "dispatch": {
    "kind": "shell",
    "command": "claude --print --permission-mode bypassPermissions --max-budget-usd 3.00 '/clu-phase {plan_slug} {phase_id} {token} {state_file}'"
  },
  "notify": {
    "imessage": {"to": "you@example.com"},
    "quiet_hours": ["22:00", "08:00"]
  }
}
```

- `dispatch.command` gets `{plan_slug}`, `{phase_id}`, `{token}`, `{state_file}`, `{project}` substituted (all shlex-quoted) before launching.
- `dispatch.path` (optional) — colon-separated PATH passed to the worker subprocess as `env={**os.environ, "PATH": ...}`. Set this when workers need to resolve tools like `gh` or `pipx` from `~/.local/bin` or `/opt/homebrew/bin` that the LaunchAgent's default PATH doesn't include. Use absolute paths only. Empty/unset = inherit the parent env. Example: `"/opt/homebrew/bin:/usr/local/bin:/Users/me/.local/bin:/usr/bin:/bin"`.
- `notify.imessage.to` should be your iMessage self-chat handle (your own number or Apple ID email) — clu DMs you when a worker opens a blocker, when a phase stalls, when the plan halts, or when it completes.
- `quiet_hours` is `[start, end]` in local wall-clock time; wraps overnight. Halt notifications bypass it (see `notify.QUIET_HOURS_BYPASS_KINDS`).

The dispatch command above launches Claude with the `/clu-phase` skill. Run `clu install-skill` to drop it into `~/.claude/skills/clu-phase/SKILL.md`, or write your own equivalent — anything that honors the worker callback contract (always call `clu complete` or `clu block` before exiting) will work.

## Bootstrap a plan

Write a master plan with a `## Sessions index` table (this is the `/plan` skill's convention):

```markdown
# my-feature

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| Design | `my-feature-design-block.md` | Decide approach | 30m |
| Implement | `my-feature-impl.md` | Write the thing | 1h |
```

Then init and let cron drive:

```bash
clu init --project ~/projects/my-repo --plan my-feature
clu              # bare command = fleet view across every registered plan
```

## macOS LaunchAgents

Two daemons:

```bash
# Inbound iMessage poller — watches chat.db for your replies to blockers
cp examples/clu.inbound.plist ~/Library/LaunchAgents/com.clu.inbound.plist
# Edit ProgramArguments[0] to point at your pipx venv python
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.clu.inbound.plist

# Tick driver — fires every 60s, advances every registered plan via `clu tick-all`
cp examples/clu.tick.plist ~/Library/LaunchAgents/com.clu.tick.plist
# Verify ProgramArguments[0] matches `which clu` in your shell (default: ~/.local/bin/clu)
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.clu.tick.plist
```

Logs land at `/tmp/clu-inbound.{out,err}` and `/tmp/clu-tick.{out,err}`.

## Worker contract

A worker is a process clu spawns for one phase. The included `/clu-phase` skill (a Claude Code skill) handles the contract automatically; if you want to plug a different worker in, the rules are:

| Step | Call |
|---|---|
| Success | `clu complete --project P --plan S --phase X --token T [--commit SHA ...]` |
| Need user input | `clu block --project P --plan S --phase X --token T --question "..." --option A --option B [--context "..."]` |
| Spawn a follow-up | `clu spawn --project P --plan S --phase X --token T --source <kind> --title "..."` |
| Still alive (long phases) | `clu heartbeat --project P --plan S --phase X --token T` |

Every worker callback validates `--token` against the live claim — `clu` rejects forged tokens with exit code 4 (`CLAIM_MISMATCH`). Never exit without calling `complete` or `block`, or the lease expires after 30 min and your phase's attempts counter ticks toward the halt cap.

If a worker calls `clu block`, clu releases the claim and sends an iMessage. When you reply, the inbound poller routes the answer back, the supervisor consumes it on the next tick, and re-dispatches the phase — the resume-aware worker reads the answered blocker from state and continues with your choice.

The bundled skill also encodes **9 universal quality mandates** — TDD before logic changes, structured commit messages, `command -v` fallbacks for external tools, re-running the project's primary check from a fresh process before `clu complete`, and so on. See `end_of_line/skills/clu-phase/SKILL.md` for the full list. Each mandate earned its slot by capturing a witnessed failure mode from real worker sessions, not hypothetical good advice. Project-specific rules (test framework, naming conventions, files to avoid) layer on top via your project's `CLAUDE.md`.

## Operator commands

| Command | Purpose |
|---|---|
| `clu` | Fleet view across every registered plan |
| `clu init` | Create state.json for a new plan (auto-registers) |
| `clu list` | List plans on this host (name + project path) |
| `clu register` / `clu unregister` | Manual registry edits |
| `clu status` | Pretty-print one plan's current state, with a `Reason:` line for paused/halted plans |
| `clu logs [--follow]` | Tail the active worker's log (falls back to the newest log if idle) |
| `clu tick` | One supervisor decision step on one plan; spawns a worker if a phase is ready. `--dry-tick` skips spawn (debug only) |
| `clu tick-all` | Tick every registered plan once (host-scoped; what cron runs) |
| `clu answer <id> <text\|index>` | Resolve a blocker by hand (instead of via iMessage) |
| `clu pause [--reason ...]` | Halt dispatching new phases |
| `clu resume` | Un-pause |
| `clu retry [--phase X]` | Clear max-attempts on a halted phase and resume |
| `clu release-claim [--force] [--reason ...]` | Escape hatch when a worker dies holding the lease |
| `clu task-done <task_id>` | Mark a spawned follow-up done |
| `clu install-skill [--force] [--dry-run] [--only <name>]` | (Re-)install the bundled skills (`/clu-phase` + `/plan` + `/brainstorm`) into `~/.claude/skills/`. `--only <name>` installs one; `--force` overwrites a regular file (symlinks are overwritten without it) |

## State schema

Sketch — see `docs/contract.md` for the full schema:

```json
{
  "schema_version": 1,
  "plan_slug": "my-feature",
  "status": "running",
  "current_claim": {"phase_id": "design", "claimed_by": "session-...", "lease_expires": "..."},
  "blockers": [{"id": "q-1", "phase_id": "design", "question": "...", "answer": null}],
  "spawned_tasks": [{"id": "task-1", "source": "simplify", "title": "...", "status": "pending"}],
  "events": [{"ts": "...", "type": "phase_completed", "phase": "design"}],
  "config": {"lease_ttl_minutes": 30, "blocked_question_sla_hours": 24, "max_attempts_per_phase": 3, "stalled_heartbeat_minutes": 10}
}
```

## Repo layout

```
end_of_line/          # the package (cli, supervisor, state, notify, dispatch, …)
end_of_line/skills/   # bundled skills (/clu-phase worker, /plan authorship, /brainstorm pre-planning) installed via `clu install-skill`
tests/                # unittest suite
plans/                # active plan files (dogfooded — this repo uses clu on itself)
docs/                 # architecture, reference, operations, conventions, contract
docs/history/         # archived plans + pre-Day-1 brainstorms — receipts that the system shipped real features
examples/             # .orchestrator.json template, LaunchAgent plists, fake-worker.sh for smoke testing
```

## Naming

[Tron](https://en.wikipedia.org/wiki/Tron). The binary is `clu` after the supervisor program; "end of line" is what MCP says when terminating a process.
