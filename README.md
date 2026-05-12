# End of Line

> "End of line."
> — Master Control Program

A cron-driven plan orchestrator. You write a multi-phase plan as markdown; `clu` dispatches each phase to a fresh Claude session, tracks state in atomic JSON, and pings you on iMessage when it hits a question. Workers run cold (no carried-over context), report back via CLI callbacks, and the supervisor advances the plan one tick at a time.

The system runs itself: the [halt-bypass feature](https://github.com/smabe/end-of-line/commit/aef2b81) that decided whether halts should bypass quiet hours was shipped by clu — a worker opened the blocker, I answered via iMessage, the worker resumed, edited `notify.py`, wrote tests, and committed.

## Status

v0.1, working. 221 tests pass (`python3 -m unittest discover -s tests`). Stdlib-only Python 3.11+. macOS-targeted today because the iMessage adapter uses `osascript` and the chat.db poller reads Apple's local SQLite — pluggable backends (Slack / stdout / etc.) are tracked in [#11](https://github.com/smabe/end-of-line/issues/11).

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
clu install-skill          # copies the /clu-phase worker skill into ~/.claude/skills/
```

On macOS, `pip install` is usually blocked by PEP 668 — `pipx` is the path that works without `--break-system-packages`.

`clu install-skill` writes the bundled worker skill to `~/.claude/skills/clu-phase/SKILL.md`, which Claude Code reads to drive each phase. Pass `--force` to overwrite an existing file or symlink, `--dry-run` to preview.

For the inbound iMessage poller, grant Full Disk Access to the pipx venv python (System Settings → Privacy & Security → Full Disk Access → add `~/.local/pipx/venvs/end-of-line/bin/python3`). Without it, the poller can't open `chat.db`.

(Optional) Install the LaunchAgents from `examples/` for cron-driven dispatch — see `docs/operations.md`.

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

The bundled skill also encodes **9 universal quality mandates** — TDD before logic changes, structured commit messages, `command -v` fallbacks for external tools, re-running the project's primary check from a fresh process before `clu complete`, and so on. See `end_of_line/skill/SKILL.md` for the full list. Each mandate earned its slot by capturing a witnessed failure mode from real worker sessions, not hypothetical good advice. Project-specific rules (test framework, naming conventions, files to avoid) layer on top via your project's `CLAUDE.md`.

## Operator commands

| Command | Purpose |
|---|---|
| `clu` | Fleet view across every registered plan |
| `clu init` | Create state.json for a new plan (auto-registers) |
| `clu list` | List plans on this host (name + project path) |
| `clu register` / `clu unregister` | Manual registry edits |
| `clu status` | Pretty-print one plan's current state, with a `Reason:` line for paused/halted plans |
| `clu logs [--follow]` | Tail the active worker's log (falls back to the newest log if idle) |
| `clu tick --dispatch` | One supervisor decision step on one plan; spawn a worker if a phase is ready |
| `clu tick-all` | Tick every registered plan once (host-scoped; what cron runs) |
| `clu answer <id> <text\|index>` | Resolve a blocker by hand (instead of via iMessage) |
| `clu pause [--reason ...]` | Halt dispatching new phases |
| `clu resume` | Un-pause |
| `clu retry [--phase X]` | Clear max-attempts on a halted phase and resume |
| `clu release-claim [--force] [--reason ...]` | Escape hatch when a worker dies holding the lease |
| `clu task-done <task_id>` | Mark a spawned follow-up done |
| `clu install-skill [--force] [--dry-run]` | (Re-)install the bundled `/clu-phase` worker skill |

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
end_of_line/skill/    # the bundled /clu-phase worker skill, installed via `clu install-skill`
tests/                # unittest suite
plans/                # active plan files (dogfooded — this repo uses clu on itself)
docs/                 # architecture, reference, operations, conventions, contract
docs/history/         # archived plans + pre-Day-1 brainstorms — receipts that the system shipped real features
examples/             # .orchestrator.json template, LaunchAgent plists, fake-worker.sh for smoke testing
```

## Naming

[Tron](https://en.wikipedia.org/wiki/Tron). The binary is `clu` after the supervisor program; "end of line" is what MCP says when terminating a process.
