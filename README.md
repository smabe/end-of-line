<p align="center">
  <img src="docs/images/tron-tron-legacy.gif" alt="CLU: 'I created the perfect system.'" width="448">
</p>

# End of Line

> "End of line."
> — Master Control Program

A cron-driven plan orchestrator. You write a multi-phase plan as markdown; `clu` dispatches each phase to a fresh Claude session, tracks state in atomic JSON, and pings you on iMessage when it hits a question. Workers run cold (no carried-over context), report back via CLI callbacks, and the supervisor advances the plan one tick at a time.

The system runs itself: the [halt-bypass feature](https://github.com/smabe/end-of-line/commit/aef2b81) that decided whether halts should bypass quiet hours was shipped by clu — a worker opened the blocker, I answered via iMessage, the worker resumed, edited `notify.py`, wrote tests, and committed.

## How it works

- **State lives outside sessions.** Each plan owns `<project>/plans/.orchestrator/<slug>.state.json`. Workers don't carry context; they read state on startup.
- **Atomic writes under a lock.** Every mutation is `tmp + fsync + rename` under `flock`. Two ticks colliding is safe.
- **Append-only event log.** Phase claims, completions, lease expirations, blockers — all derivable from `events[]`. State corruption is recoverable by replaying.
- **`/plan` convention.** Phase declarations come from the master plan's `## Sessions index` markdown table. The parser is 80 lines.
- **System cron is the heartbeat.** No long-running orchestrator process. Each tick is ~50ms of Python; the supervisor itself burns zero LLM tokens. Workers are the only thing that costs API money.
- **Pluggable notification backends.** iMessage (macOS, via `osascript` + `chat.db` poll) and Discord (any OS, REST) ship out of the box; the protocol is open for more. Quiet hours (default 22:00–08:00) gate non-halt notifications. In-session-only mode (`channels: []`) skips outbound entirely — the inbox hook covers that case.
- **Three observation surfaces.** iMessage for halts and blockers (loud, your phone), the inbox hook for AFK pickup (quiet, Claude's next message), `clu watch` for live in-session streaming (Claude's `Monitor` tool, at-desk). Same event stream, three audiences.

## Install

```bash
git clone https://github.com/smabe/end-of-line.git
cd end-of-line
pipx install -e .          # puts `clu` on $PATH via its own venv
clu install-skill          # copies the 5 bundled skills (/clu-phase + /plan + /clu-plan + /brainstorm + /clu-monitor) into ~/.claude/skills/
```

On macOS, `pip install` is usually blocked by PEP 668 — `pipx` is the path that works without `--break-system-packages`.

`clu install-skill` writes five bundled skills into `~/.claude/skills/`, one subdirectory per skill. Pass `--force` to overwrite an existing regular file (symlinks are overwritten without it), `--dry-run` to preview, or `--only <name>` to install just one.

After installing the skills, run `/clu-monitor` once in Claude Code to install a `UserPromptSubmit` hook that surfaces clu's events into Claude's context on your next message — type "ok" after walking back and Claude already knows what halted, completed, or stuck. Idempotent — re-running prints the current install status. State file: `~/.config/clu/monitor.json`.

For a live in-session feed, `clu watch` streams state-machine events to stdout as they happen — one line per transition. It's the at-desk sibling to the inbox hook: the inbox catches events from between sessions; `clu watch` covers the current session live. The `/clu-plan` skill arms `Monitor(command="clu watch --project . --plan <slug> --task-list", persistent=True)` automatically after `clu queue add`, so Claude-driven sessions get a live feed that populates the native TaskCreate UI hands-free. Add `--task-list` to emit `TASK_CREATE`/`TASK_UPDATE` protocol lines instead of text; omit it for plain-text output (compatible with `--json` for jq pipelines).

(Optional) Install LaunchAgents / systemd units from `examples/` for cron-driven dispatch and inbound polling — see `docs/operations.md` for setup per backend (iMessage, Discord, or in-session-only).

(Optional, contributor-only) This repo uses [graphify](https://github.com/karpathy/graphify) to keep an up-to-date knowledge graph of the codebase at `graphify-out/` (god nodes, communities, surprising connections) so Claude Code can answer "where is X defined" without grepping the whole tree. The graph is regenerated on every code-touching commit by a local post-commit hook. Git doesn't track the hook itself or the local Claude settings, so each clone runs setup once:

```bash
pipx install graphifyy   # one-time, system-wide
graphify hook install    # post-commit + post-checkout regen (AST-only, no LLM)
graphify claude install  # CLAUDE.md section + PreToolUse hook to consult the graph
```

`graphify-out/` is gitignored — the graph is per-clone, not a committed artifact.

## Working with clu

`clu install-skill` ships five skills:

- **`/clu-phase`** — the worker skill clu's dispatch invokes for each phase. Required for clu to function; you don't run it directly. The dispatch command in `.orchestrator.json` (see [Configure a project](#configure-a-project)) launches Claude with this skill so each phase honors the worker callback contract.
- **`/plan`** — generic project-agnostic authorship skill. Drops a single file at `plans/<slug>.md` with Goal / Files-to-touch / Failure-modes / Done-criteria sections. Use this for solo human-authored plans in any project. Does NOT produce the Sessions-index format clu's supervisor needs — for clu-dispatched plans use `/clu-plan`.
- **`/clu-plan`** — clu-format authorship: produces a master with `## Sessions index` table PLUS one sub-plan file per phase (the worker brief). Use this whenever you intend to dispatch the plan via `clu queue add`. Refuses with a pointer to `/plan` in non-clu projects.
- **`/brainstorm`** — parallel-persona pre-planning. Launches 3-6 agents (UX, engineer, QA, …) in parallel to analyze a feature from different angles, then consolidates their outputs into a master plan. Useful before `/plan` or `/clu-plan` when the problem space is fuzzy and you'd rather explore than guess.
- **`/clu-monitor`** — one-shot setup skill that registers a `UserPromptSubmit` hook in `~/.claude/settings.json`. The hook surfaces clu's events (halts, blockers, plan completions, queue lifecycle, stuck-blocker re-pings, stalled claims) into Claude's context on every user message, so walking back to a session always has Claude already aware of what happened. Run once per machine; idempotent via the marker at `~/.config/clu/monitor.json`.

### Recommended workflow

For non-trivial work, the combo is **brainstorm → grill-me → clu-plan → clu**:

1. **`/brainstorm`** — parallel personas explore the design space and consolidate into a master plan.
2. **`/grill-me`** by Matt Pocock ([source](https://github.com/mattpocock/skills), installed separately) — interviews you relentlessly until each decision branch is resolved.
3. **`/clu-plan`** — commit the agreed approach to `plans/<slug>.md` PLUS one sub-plan file per phase, in the `## Sessions index` shape clu's parser expects. (Use `/plan` instead for solo human-driven work in a project that won't be clu-dispatched.)
4. **`clu init`** — hand it to clu, which dispatches each phase as a cold-context worker subprocess.

Each skill is independent — use one, all four, or none. The combo just makes ambitious work less likely to drift mid-flight. `/grill-me` is the only piece clu doesn't bundle; install it yourself when you want it.

### Minimum plan shape clu can orchestrate

If you skip the bundled `/clu-plan` and hand-roll a plan, clu's parser needs the master file (`plans/<slug>.md`) to contain a `## Sessions index` table:

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| phase-a | `<slug>-phase-a.md` | one-line scope | time est |
| phase-b | `<slug>-phase-b.md` | one-line scope | time est |

Each row points to a sub-plan file in the same `plans/` directory. The bundled `/clu-plan` produces this shape (master + one sub-plan per phase) by default. `/plan` does NOT — it writes a single file with no Sessions-index table and is the right choice only for solo human-driven projects you don't intend to dispatch through clu.

## Configure a project

Drop a `.orchestrator.json` at your project root. `clu init` prompts for notify config interactively; or write it by hand:

```json
{
  "plan_dir": "plans",
  "dispatch": {
    "kind": "shell",
    "command": "claude --print --permission-mode bypassPermissions --max-budget-usd 3.00 '/clu-phase {plan_slug} {phase_id} {token} {state_file}'"
  },
  "notify": {
    "channels": [
      {"kind": "imessage", "to": "you@example.com"}
    ],
    "quiet_hours": ["22:00", "08:00"]
  }
}
```

Three notification modes — pick one or combine:

- **iMessage (macOS):** `{"kind": "imessage", "to": "<your-handle>"}`. Requires Full Disk Access for the pipx venv python and the inbound LaunchAgent (`examples/clu.inbound.plist`).
- **Discord (any OS):** `{"kind": "discord", "bot_token": "...", "user_id": "..."}`. Bot DMs you directly; inbound poller in `examples/clu.discord_inbound.plist` / `examples/clu-discord-inbound.service`. See `docs/operations.md` for the Discord app setup walkthrough.
- **In-session only:** `"channels": []` — no phone pings, but the inbox hook surfaces events into Claude Code on your next message. Great for local-only work.

Other config fields:

- `dispatch.command` gets `{plan_slug}`, `{phase_id}`, `{token}`, `{state_file}`, `{project}` substituted (all shlex-quoted) before launching.
- `dispatch.path` (optional) — colon-separated PATH for the worker subprocess. `~` is expanded per segment. Empty/unset = inherit parent env.
- `quiet_hours` is `[start, end]` in local wall-clock time; wraps overnight. Halt notifications bypass it (see `notify.QUIET_HOURS_BYPASS_KINDS`).
- `clu --no-notify <cmd>` suppresses outbound sends for a single invocation (debug/dry-run). `clu notify-test` fires a test notification through all configured channels.

The dispatch command above launches Claude with the `/clu-phase` skill. Run `clu install-skill` to drop it into `~/.claude/skills/clu-phase/SKILL.md`, or write your own equivalent — anything that honors the worker callback contract (always call `clu complete` or `clu block` before exiting) will work.

**Coolant integration (optional).** If the [coolant](https://github.com/todd-w-shaffer/coolant) plugin is installed, clu auto-discovers its scripts under `~/.claude/plugins/cache/.../coolant/<version>/scripts/` and emits agent-start/agent-stop events on every worker dispatch + claim release. This makes clu workers visible to coolant's parallel-mode gating math (`gate.sh` caps `go test` / `vitest` etc. against active agent count). Override the discover path with `coolant.script_dir` in `.orchestrator.json`, or set `coolant.enabled: false` to opt out per-project. `clu doctor` reports the resolved state.

## Bootstrap a plan

Write a master plan with a `## Sessions index` table (this is the `/clu-plan` skill's convention):

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

Concurrent plans in the same project stomp each other's diffs by default. Add `--worktree` at init to isolate a plan in its own `git worktree` on branch `clu/<slug>`:

```bash
clu init --project ~/projects/my-repo --plan my-feature --worktree
# After it ships, clean up:
clu worktree gc --project ~/projects/my-repo --confirm --delete-branch
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

Workers can also chain a follow-up plan into the project queue mid-phase via `clu queue add <slug> --token <T> --plan <source-plan> --phase <source-phase>`. The new plan lands in the queue with lineage stamped (which plan, which phase, a fingerprinted token — the raw token is never persisted). Per-phase cap is 3 by default (`max_queue_adds_per_phase` in the plan's config block). See [`docs/contract.md`](docs/contract.md) for the full worker-enqueue contract.

## Operator commands

| Command | Purpose |
|---|---|
| `clu` | Fleet view across every registered plan |
| `clu init` | Create state.json for a new plan (auto-registers) |
| `clu list` | List plans on this host (name + project path) |
| `clu register` / `clu unregister` | Manual registry edits |
| `clu status` | Pretty-print one plan's current state, with a `Reason:` line for paused/halted plans |
| `clu logs [--follow]` | Tail the active worker's log (falls back to the newest log if idle) |
| `clu watch [--plan SLUG\|--all] [--json] [--verbose] [--interval N] [--task-list]` | Stream state-machine events live — one line per transition. Default: every plan in the CWD project. SIGINT exits cleanly. Pair with Claude's `Monitor` tool for an in-session live feed. `--task-list` emits `TASK_CREATE`/`TASK_UPDATE` protocol lines for Claude's native TaskCreate UI (mutually exclusive with `--json`/`--all`) |
| `clu tick` | One supervisor decision step on one plan; spawns a worker if a phase is ready. `--dry-tick` skips spawn (debug only) |
| `clu tick-all` | Tick every registered plan once (host-scoped; what cron runs) |
| `clu answer <id> <text\|index>` | Resolve a blocker by hand (instead of via iMessage) |
| `clu pause [--reason ...]` | Halt dispatching new phases |
| `clu resume` | Un-pause |
| `clu retry [--phase X]` | Clear max-attempts on a halted phase and resume |
| `clu release-claim [--force] [--reason ...] [--reset-attempts]` | Escape hatch when a worker dies holding the lease. `--reset-attempts` zeroes the attempt counter so the next dispatch starts fresh (use when the abort is operator-fault, not worker-fault) |
| `clu extend-lease --project P --plan S MINUTES` | Add N minutes to the live claim's lease without touching the worker. Anchors from `max(now, current_expires)` so it's safe to call on an already-expired claim |
| `clu task-done <task_id>` | Mark a spawned follow-up done |
| `clu blockers list \| show` | Read-only inspection: `list` shows every open blocker for a plan (id, phase, question, numbered options); `show <id>` prints the full payload plus related events |
| `clu archive --project P --plan S` | Post-ship cleanup: removes the clu-managed worktree + branch (when reachable from origin) AND `git mv plans/<slug>*.md plans/archive/<slug>/`. Idempotent on the file-move step |
| `clu install-skill [--force] [--dry-run] [--only <name>] [--list]` | (Re-)install the 5 bundled skills (`/clu-phase` + `/plan` + `/clu-plan` + `/brainstorm` + `/clu-monitor`) into `~/.claude/skills/`. `--only <name>` installs one; `--force` overwrites a regular file (symlinks are overwritten without it); `--list` enumerates bundled skills and exits |
| `clu install-hook` / `clu uninstall-hook` | Register or remove the `UserPromptSubmit` hook in `~/.claude/settings.json` that surfaces clu's inbox events into the active Claude session. `/clu-monitor` is the user-facing wrapper |
| `clu doctor --project P` | Smoke-test what a worker subprocess sees (PATH + resolved binary locations). No state writes |
| `clu unregister --all-archived [--dry-run]` | Batch-prune registry entries whose master plan file no longer exists. `--dry-run` previews without mutating |
| `clu worktree gc [--project P] [--confirm] [--delete-branch] [--include-archived]` | List or remove worktrees of done/halted plans. Default is dry-run; `--confirm` runs `git worktree remove --force` (and `--delete-branch` adds `git branch -D`) |
| `clu worktree attach --project P --plan S [PATH] [--branch B] [--base-ref REF]` | Retrofit a worktree onto a plan that was init'd without one |
| `clu worktree reattach --project P --plan S` | Recovery: re-create the worktree dir from the path + branch already recorded in `state.worktree` (use after an external `git worktree remove`) |

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
end_of_line/skills/   # bundled skills (/clu-phase worker, /plan + /clu-plan authorship, /brainstorm pre-planning, /clu-monitor in-session signaling) installed via `clu install-skill`
end_of_line/hooks/    # bundled UserPromptSubmit hook script that surfaces inbox events into Claude's context
tests/                # unittest suite
plans/                # active plan files (dogfooded — this repo uses clu on itself)
docs/                 # architecture, reference, operations, conventions, contract
docs/history/         # archived plans + pre-Day-1 brainstorms — receipts that the system shipped real features
examples/             # .orchestrator.json template, LaunchAgent plists, fake-worker.sh for smoke testing
```

## Naming

[Tron](https://en.wikipedia.org/wiki/Tron). The binary is `clu` after the supervisor program; "end of line" is what MCP says when terminating a process.
