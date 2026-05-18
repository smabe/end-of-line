# Operations

The setup-and-fix-it manual for running clu on a Mac. Pairs with the
quickstart in `README.md` — the README is a 90-second tour; this doc is
the deeper reference an operator opens when something is wrong. Design
discussion belongs in `architecture.md`, not here.

## Prerequisites

- macOS. The notification adapter shells out to `osascript`; the inbound
  poller reads Apple's `chat.db`. Linux will need a different outbound
  channel and a different inbound source — neither is shipped.
- Python 3.11 or newer (uses `datetime.fromisoformat` Z-suffix support,
  `IntEnum`, dataclasses with `kw_only`).
- `claude` CLI on `$PATH` for worker dispatch. `which claude` should
  resolve before you call `clu init`. The dispatch command runs from
  launchd's environment, which does not inherit your shell PATH — use
  an absolute path in `.orchestrator.json` if `claude` lives outside
  `/usr/local/bin`.

## Install

```bash
git clone https://github.com/smabe/end-of-line.git
cd end-of-line
pipx install -e .
clu --help
```

`pip install` on a stock macOS Python is blocked by PEP 668; `pipx` is
the path that works without `--break-system-packages`.

Once `clu` is on `$PATH`, install the bundled skills:

```bash
clu install-skill                       # interactive — installs five
                                        # skills + prompts about CLAUDE.md
clu install-skill --add-claude-md-note  # non-interactive, accept the note
clu install-skill --no-claude-md-note   # non-interactive, skip the note
clu install-skill --only clu-phase      # one skill only
```

The CLAUDE.md note appends an idempotent clu-managed section to
`~/.claude/CLAUDE.md` telling Claude to use `ScheduleWakeup` when an
operator delegates an autonomous multi-step task. Markers
(`<!-- clu:start autonomous-loop-pacing -->` / `<!-- clu:end ... -->`)
make repeat installs replace-in-place rather than duplicate. Skip with
`--no-claude-md-note` for non-interactive CI flows, or pass nothing on
a non-TTY context and the prompt skips silently.

Find the pipx venv python — you need it for both LaunchAgents:

```bash
ls -l ~/.local/pipx/venvs/end-of-line/bin/python3
```

The typical absolute path is
`/Users/<you>/.local/pipx/venvs/end-of-line/bin/python3`. Pin this
string; every LaunchAgent that runs clu code uses it.

## Full Disk Access for the inbound poller

The inbound LaunchAgent reads `~/Library/Messages/chat.db`. That file is
behind macOS's TCC (Transparency, Consent, Control) gate, and
LaunchAgents do **not** inherit Terminal's grant. The python interpreter
that runs the poller needs its own FDA grant.

1. System Settings → Privacy & Security → Full Disk Access
2. Click `+`, then Cmd+Shift+G to paste an exact path
3. Paste `/Users/<you>/.local/pipx/venvs/end-of-line/bin/python3`
4. Toggle it on

If this is missing, the poller crash-loops on
`sqlite3.OperationalError: unable to open database file` and the
LaunchAgent's `ThrottleInterval` keeps respawning it. See troubleshooting
below for how to confirm.

## Install both LaunchAgents

clu needs two daemons: a long-lived inbound poller and a 5-minute tick
driver. Templates live under `examples/`.

### Inbound poller — `com.clu.inbound`

```bash
cp examples/clu.inbound.plist ~/Library/LaunchAgents/com.clu.inbound.plist
```

Edit `~/Library/LaunchAgents/com.clu.inbound.plist`:

- Replace `<string>/usr/bin/python3</string>` in `ProgramArguments` with
  the absolute path to your pipx venv python (above). LaunchAgents do
  not see `$PATH`.

Load it:

```bash
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.clu.inbound.plist
launchctl list | grep com.clu.inbound          # should print a PID
tail -f /tmp/clu-inbound.err                   # should be quiet
```

Reload after a plist edit:

```bash
launchctl bootout gui/$UID/com.clu.inbound
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.clu.inbound.plist
```

The poller writes its high-water rowid to `~/.clu/seen_msg_rowid` so a
restart doesn't replay old replies.

### Tick driver — `com.clu.tick`

```bash
cp examples/clu.tick.plist ~/Library/LaunchAgents/com.clu.tick.plist
```

Edit `~/Library/LaunchAgents/com.clu.tick.plist`:

- Replace the path in `ProgramArguments[0]` with the absolute path to
  `examples/clu-tick-all.sh` in your clone.

Edit `examples/clu-tick-all.sh`:

- Set `CLU=` to the absolute path of the `clu` shim that pipx wrote
  (usually `/Users/<you>/.local/bin/clu`).

Load it:

```bash
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.clu.tick.plist
launchctl list | grep com.clu.tick
tail -f /tmp/clu-tick.out                      # one block per 5-min tick
```

The tick driver calls `clu list`, then `clu tick --project P --plan S`
for each registered plan. One worker per active plan per tick, capped
by `--max-budget-usd` in your dispatch command. Pass `--dry-tick` to
mutate state without spawning a worker — debug only.

### Log locations

| File | Source |
|---|---|
| `/tmp/clu-inbound.out`, `/tmp/clu-inbound.err` | Inbound poller stdout/stderr |
| `/tmp/clu-tick.out`, `/tmp/clu-tick.err` | Tick driver stdout/stderr |
| `<project>/plans/.orchestrator/logs/<phase>.<token>.log` | Per-worker stderr |
| `<project>/plans/.orchestrator/<plan>.state.json` | Plan state (single source of truth) |

## First plan walkthrough

A clean end-to-end against a real project.

1. **Write the master plan.** Under `<project>/plans/<slug>.md`, include
   a `## Sessions index` table per the `/plan` convention. Each row's
   `Plan file` cell points to a sub-plan markdown beside it.

   ```markdown
   # demo-feature

   ## Sessions index

   | Session | Plan file | Scope | Effort |
   |---|---|---|---|
   | Design | `demo-feature-design.md` | Decide approach | 30m |
   | Implement | `demo-feature-impl.md` | Write the thing | 1h |
   ```

2. **Write each sub-plan.** A sub-plan tells one worker exactly what to
   produce. Keep it narrow — a phase that ships in 30 minutes is the
   target. Reference the project's CLAUDE.md so the worker inherits
   house style.

3. **Init + register.**

   ```bash
   clu init --project ~/projects/demo --plan demo-feature
   clu list                                  # confirms registration
   ```

   `clu init` creates `plans/.orchestrator/demo-feature.state.json` and
   adds the plan to `~/.config/clu/registry.json` so the tick driver
   picks it up.

   Three optional knobs let you override the per-plan defaults at init time:

   | Flag | Default | What it controls |
   |---|---|---|
   | `--lease-ttl-minutes N` | 30 | How long a worker claim is valid before `lease_expired` fires |
   | `--stalled-heartbeat-minutes N` | 10 | Threshold for `phase_stalled` (suppressed when no heartbeat received yet — see stall guard below) |
   | `--max-attempts-per-phase N` | 3 | How many times a phase may retry before the plan halts on `max_attempts_exhausted` |

   All three accept positive integers only; `≤0` is rejected at parse time.

4. **First tick.** Either wait 5 minutes for launchd, or fire it
   manually:

   ```bash
   clu tick --project ~/projects/demo --plan demo-feature
   ```

   The supervisor claims the first phase, writes `phase_started`, and
   `dispatch.dispatch_for_tick` Popens the worker. Watch:

   ```bash
   tail -f ~/projects/demo/plans/.orchestrator/logs/*.log
   clu status --project ~/projects/demo --plan demo-feature
   ```

5. **First blocker round-trip.** When a worker calls `clu block`, the
   supervisor sends an iMessage with `❓ <slug>/q-1 …` plus numbered
   options. Reply on your phone:

   - `2` — bare digit, accepted when this plan has the only open
     blocker on the host.
   - `demo-feature 2` — slug-prefixed, required when multiple plans
     have open blockers.

   The inbound poller routes the reply through `clu answer`, the next
   tick consumes the blocker, the tick after that re-dispatches the
   phase with the answer in state.

## Plan queue

Once a project has at least one registered plan, the operator can queue
follow-up plans for clu to `init` automatically as the chain drains.
Storage lives in `<project>/<plan_dir>/.orchestrator/queue.json`; one
queue per project (not per plan).

```bash
clu queue add my-next-plan          # append at tail
clu queue add fix-bug-7 --front     # insert at head
clu queue add a b c                 # multi-arg: all-or-nothing batch
clu queue                           # bare → list (default subcommand)
clu queue list                      # same
clu queue remove old-plan           # drop a pending slug (→ history)
```

`clu queue add` accepts one or more slugs. Multi-arg adds are atomic from
cron's POV — clu validates every slug (regex, within-batch dupes,
plan-file existence, pre-existing pending duplicates) before mutating;
any failure rejects the whole batch with the queue unchanged. Output
prints one `queued at position N` line per slug in argument order,
followed by `queued <N> plans` when N > 1. `--front` with multi-arg
inserts in argument order at the head (`a b c --front` →
`[a, b, c, ...existing]`, NOT reversed).

`clu queue list` shows pending entries as a table; when any plan
registered to this project has an active claim (i.e. was popped and is
currently running or stalled), a one-line footer surfaces it after the
table:

```
$ clu queue list
POS  SLUG  STATUS  NOTE
1    bar   queued  plans/bar.md

In flight: foo (dispatched 14:32:05 UTC, lease until 15:02:05 UTC)
```

Sorted by `started_at` ascending if multiple. Omitted cleanly when no
in-flight plans. The footer is independent of `queue.history` (which
records only failures — see `docs/contract.md`).

The supervisor's post-loop step in `clu tick-all` walks every distinct
project_root and pops at most one entry per tick into a fresh `clu
init`-equivalent. Bare `clu` (the fleet view) prints a one-line footer
when any project has pending queue work; hidden when every queue is
empty.

### Bootstrap

`clu queue add` requires the project to be known to the host registry.
Run `clu init --project P --plan <something>` at least once for the
project before queuing. Without it, `queue add` refuses:

```
error: project /Users/.../foo has no registered plans;
run `clu init --project /Users/.../foo --plan <slug>` first
```

The bootstrap rule exists so an operator who points `clu queue add` at
a stray directory can't silently create a queue file in an unintended
project.

### Multi-host queues

The queue is **per project, per host**. If you run clu against the same
git-synced project from two Macs, each Mac has its own `.orchestrator/
queue.json` and its own `repair-attempts` counter. clu does **not**
attempt any cross-host merge.

**Recommendation: pick one Mac as the cron host and only enqueue from
that one.** Other Macs can still run `clu status` / `clu queue list`
read-only against the local copy, but `queue add` and `queue remove`
should be limited to the cron host to avoid two queues that diverge.

This is a deliberate design choice — the queue file is a small,
operator-facing list of intentions, not a synced data structure. If the
cron host's queue is the source of truth, the worst-case multi-machine
failure mode is "the other Mac's queue is stale," not "two ticks
dispatched the same plan twice."

### Enabling auto-repair

If `queue.json` ever fails to load (catastrophic JSON / schema
corruption), clu can dispatch a headless Claude worker to repair it.
This is opt-in: set `dispatch.repair_command` in `.orchestrator.json`.
Without it, clu falls back to a halt-bypass `KIND_QUEUE_CORRUPT`
notification — operator repairs by hand.

Recommended template:

```jsonc
{
  "dispatch": {
    "command": "...",
    "repair_command": "claude --print 'queue.json at {corrupt_path} is corrupt: {diagnosis}. Backup at {backup_path}. Read both files, diagnose, repair in place using atomic write (tmp + fsync + os.replace). HARD RULES (clu validates and reverts on violation): 1. The queue array MUST contain at least every slug from the original. 2. Do NOT write an empty queue array unless the original was provably empty. 3. The history array is forensic — do not remove entries; you may append. 4. If you cannot repair without violating rules 1-3, exit 9 (REPAIR_DECLINED). Log to {log_path}. Expected schema: {schema_json}.'"
  }
}
```

Template variables: `{corrupt_path}`, `{backup_path}`, `{diagnosis}`,
`{schema_json}`, `{log_path}` — all shlex-quoted before substitution.

What's worth knowing about the pipeline:

- **clu's validation is the safety boundary, not the prompt.** The
  prompt is advisory; clu's `queue.validate_repair` re-loads the file,
  checks every backup slug against the repaired output, and reverts from
  the backup on any rule violation. A worker that ignores its prompt
  cannot drop slugs past us.
- **Backups are kept.** Every corruption produces a
  `queue.json.corrupt-<UTCstamp>` sibling whether the repair succeeds or
  not. Diff old vs new after a `KIND_QUEUE_REPAIRED` ping to see what
  the worker rewrote; the backup is also what clu reverts from on
  validation failure.
- **Throttle.** After 3 failed attempts on the same diagnosis-hash, the
  4th corruption skips dispatch entirely and goes straight to
  `KIND_QUEUE_CORRUPT`. The counter is per-diagnosis (different
  corruption errors get their own three attempts) and resets on a
  successful repair.
- **Synchronous.** `dispatch_repair_worker` blocks the cron tick for up
  to 60s. The next tick won't move on the queue until this one decides
  repaired-or-reverted. If you set a faster cron cadence than 60s, the
  next tick will wait for the queue lock the previous one holds.

The operator CLI (`clu queue add/list/remove`) does **not** trigger
auto-repair — it refuses loudly on a corrupt queue and prints a paste-
into-Claude diagnosis. Auto-repair only runs from `tick-all`.

## Per-plan worktrees

By default, every plan in a project runs against the project's main
working tree — concurrent plans edit the same files. Pass `--worktree`
at init to put a plan in its own git worktree on its own branch, so
two plans in the same project can advance in parallel without
stomping each other's diffs.

### Init walkthrough

```bash
# Default: worktree at <project-parent>/<basename>-<slug>,
# branch clu/<slug>, forked from HEAD.
clu init --plan rearchitect-workouts --worktree

# Custom path and branch:
clu init --plan rearchitect-workouts \
    --worktree ~/scratch/wo-rearch \
    --branch abe/wo-rearch \
    --base-ref feature/wo-base
```

`clu init` prints the resolved fork SHA + symbolic ref to stderr so
you can confirm what you got. The persisted `state.worktree.base_ref`
is the **resolved SHA**, not the symbolic ref — freezes the fork
point unambiguously.

Refusals exit `WORKTREE_SETUP_FAILED` (rc 10):

- Project isn't a git repo (`--worktree` on a non-git project).
- Branch `clu/<slug>` (or `--branch`) already exists.
- Target path already exists.
- `--base-ref` isn't a resolvable commit.
- `git worktree add` succeeded but the state save failed — the
  worktree + branch are torn back down before clu reports failure.
  No orphan state.

### Conflict warning

Running `clu init --plan b` (no `--worktree`) in a project where
plan `a` is already active without a worktree prints a stderr hint
suggesting `--worktree`. Ignoring the hint is supported; clu's
`tick-all` will then emit `EVENT_WORKTREE_CONFLICT_WARNING` + a halt-
bypass iMessage naming the pair on the next tick after both plans
are active. The iMessage fires once per (project, pair) onset and
auto-clears when one side pauses, halts, finishes, or gets a
worktree.

### Recovery when the worktree dir is missing

If you `git worktree remove` a worktree (or run `git worktree prune`)
while the plan is paused or halted, the next dispatch detects the
missing dir and pauses the plan with `EVENT_WORKTREE_MISSING`. The
iMessage names the orphan path. Recovery:

```bash
# Option 1: restore the original worktree on disk
git worktree add /path/from/iMessage clu/<slug>
clu resume --plan <slug>

# Option 2: re-create the worktree using the state's recorded path/branch
clu worktree reattach --project P --plan <slug>
clu resume --plan <slug>

# Option 3: retrofit a worktree onto a plan that wasn't init'd with one
clu worktree attach --project P --plan <slug> [PATH] [--branch B] [--base-ref REF]
clu resume --plan <slug>
```

`reattach` re-runs `git worktree add` against the path + branch already
persisted in `state.worktree`; use it when the dir was deleted but
state still points at the right destination. `attach` is for the
inverse case — a plan that was init'd without `--worktree` and now
needs one (e.g. another plan is starting in the same project and you
want to avoid the conflict warning); both flow through the same
`_setup_worktree` helper as `clu init --worktree`, so the same
refusal/rollback rules apply.

### Archiving a plan

`clu archive --project P --plan S` is the standard post-ship step. It does
two things in one command:

1. **Worktree cleanup** — removes the clu-managed worktree + branch if the
   branch is fully reachable from origin; retains with a warning when ahead.
2. **Plan-file move** — `git mv plans/<slug>.md plans/shipped/<slug>.md`
   (staging the rename; commit separately). Creates `plans/shipped/` if it
   doesn't exist. Skips silently when the plan file is already gone (e.g.
   manually moved in a prior run). Surfaces as `WORKTREE_SETUP_FAILED` if
   the file exists but `git mv` fails (e.g. not tracked, conflicts).

After archiving, run `clu unregister --all-archived` to prune the host
registry entry.

### Cleanup with `clu worktree gc`

```bash
# Dry-run list of done/halted plans with worktrees:
clu worktree gc --project /path/to/project

# Actually remove the worktree dirs (keeps the clu/<slug> branches):
clu worktree gc --project /path/to/project --confirm

# Also drop the clu/<slug> branches:
clu worktree gc --project /path/to/project --confirm --delete-branch

# Include archived plans (master plan file moved out of plan_dir):
clu worktree gc --project /path/to/project --include-archived --confirm
```

Each `git worktree remove --force` and `git branch -D` runs with a
30-second timeout — a hung git invocation can't block your terminal
indefinitely. Action-time re-checks each candidate's status, so a
`clu retry` that landed between the list pass and `--confirm` doesn't
lose its worktree.

`clu unregister --all-archived` also emits a stderr warning per ghost
entry whose state file still has a `worktree` record, naming the
orphan path. Recovery: `clu worktree gc --include-archived --confirm`.

## Setup: iMessage (macOS only)

Configure during `clu init` (interactive prompt on macOS) or directly in `.orchestrator.json`:

```json
"notify": {
  "channels": [
    {"kind": "imessage", "to": "you@example.com", "enabled": true}
  ],
  "quiet_hours": ["22:00", "08:00"]
}
```

`to` must be your iMessage self-chat handle (your own number or Apple ID email). clu
sends from your Mac to yourself; you answer from your phone.

Grant Full Disk Access to the pipx venv python so the inbound poller can open `chat.db`
(System Settings → Privacy & Security → Full Disk Access → add
`~/.local/pipx/venvs/end-of-line/bin/python3`).

Install the inbound LaunchAgent:

```bash
cp examples/clu.inbound.plist ~/Library/LaunchAgents/com.clu.inbound.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.clu.inbound.plist
```

Verify with `clu notify-test` after setup.

## Setup: Discord (any OS)

Discord works on macOS, Linux, and Windows — the only backend that doesn't require
platform-specific privileges.

**One-time Discord app setup:**

1. Go to `https://discord.com/developers/applications` → "New Application" → name it
   (e.g. "clu").
2. Under "Bot": enable the bot, copy the **Bot Token**.
3. Enable **Message Content Intent** under "Privileged Gateway Intents".
4. Under "OAuth2 → URL Generator": scope = `bot`, permissions = "Send Messages" +
   "Read Messages/View Channels". Copy the generated URL and open it to invite the bot
   to a personal server (create one if needed).
5. In your server settings → "Privacy Settings": enable "Allow direct messages from
   server members."
6. Get your **user ID**: Settings → Advanced → enable Developer Mode, then right-click
   your own username → "Copy User ID."

Add to `.orchestrator.json`:

```json
"notify": {
  "channels": [
    {"kind": "discord", "bot_token": "Bot.Token.Here", "user_id": "123456789"}
  ],
  "quiet_hours": ["22:00", "08:00"]
}
```

Install the inbound poller:

```bash
# macOS
cp examples/clu.discord_inbound.plist ~/Library/LaunchAgents/com.clu.discord_inbound.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.clu.discord_inbound.plist

# Linux (systemd)
cp examples/clu-discord-inbound.service ~/.config/systemd/user/
systemctl --user enable --now clu-discord-inbound
```

Verify with `clu notify-test --channel discord`.

## Setup: clu-watch only (zero external transport)

Skip outbound transport entirely — clu's inbox hook surfaces events into the active
Claude Code session on your next message. No iMessage handle, no bot token needed.

1. `channels: []` (empty or omit `notify.channels`) in `.orchestrator.json`.
2. Run `/clu-monitor` once in Claude Code to install the inbox hook.

All notification events still appear in the Claude Code session when you're at your
desk. You won't get phone pings when you're AFK — that's the tradeoff.

## Suppressing notifications

Four levers, from narrowest to broadest:

| Lever | Scope | Preserves credentials |
|---|---|---|
| Per-kind `kinds` filter | Channel only fires for listed notification kinds | Yes |
| `"enabled": false` on a channel | Channel silenced, config kept | Yes |
| `clu --no-notify <cmd>` | Single CLI invocation | N/A |
| `channels: []` | Permanent silence; inbox writes still happen | Yes |

**Per-kind filter** — fire only on halts and blockers:
```json
{"kind": "discord", "bot_token": "...", "user_id": "...", "kinds": ["halted", "blocker"]}
```

**Disable without deleting** — useful when a bot token is temporarily revoked:
```json
{"kind": "discord", "bot_token": "...", "user_id": "...", "enabled": false}
```

**Single-invocation suppress** — debug or dry-run a command without real DMs:
```bash
clu --no-notify tick --project . --plan my-feature
```

**Permanent silence** — inbox hook still works; just no outbound sends:
```json
"notify": {"channels": []}
```

## Notification model

Outbound — fired during supervisor ticks. Kinds:

| Kind | When | Quiet hours |
|---|---|---|
| `blocker` | Worker called `clu block` | Gated |
| `blocker_sla` | Open blocker older than `blocked_question_sla_hours` (default 24h) | Gated, re-checked next loud tick |
| `stalled` | Live claim with no heartbeat for `stalled_heartbeat_minutes` (default 10m) | Gated |
| `plan_completed` | All phases done | Gated |
| `halted` | Plan halted (max attempts, lease expired too many times, etc.) | **Bypasses quiet hours** |
| `queue_skipped` | Queue head abandoned (plan file missing) | Gated |
| `queue_repaired` | Auto-repair fixed a corrupt `queue.json` | Gated |
| `queue_repair_failed` | Auto-repair failed validation — file reverted from backup | **Bypasses quiet hours** |
| `queue_corrupt` | `queue.json` corrupt and auto-repair disabled OR throttle exhausted | **Bypasses quiet hours** |
| `stuck_blocker` | Open blocker un-consumed for >30 min; re-pings every 30 min | Gated (inbox always writes) |
| `stalled_claim` | Live claim's lease expired with plan status still `running`; one-shot per claim | Gated (inbox always writes) |

Quiet hours default to `["22:00", "08:00"]` local time and wrap
overnight. Configure per project under `notify.quiet_hours` in
`.orchestrator.json`. The bypass set lives in
`notify.QUIET_HOURS_BYPASS_KINDS` — halts fire at any hour because a
halted plan won't progress until you intervene.

Inbound reply grammar — locked at `^\s*(<plan-slug>\s+)?[0-9]\s*$`:

- Single digit (`1`, `2`, …) — picks the option at that index for the
  one open blocker on the host. Refused when more than one plan has an
  open blocker (force disambiguation rather than guess).
- `<plan-slug> <digit>` — picks the option for the named plan's first
  open blocker. Always honored when the slug matches.

## Background monitoring

clu sends notifications on halts, blockers, plan completions, and queue
events through configured channels. That covers the
"operator on their phone, away from the keyboard" case.

The remaining gap is in-session signaling: when you walk back to an
active Claude Code session AFTER clu has changed state, Claude has no
idea what happened unless you summarize for it. **The `/clu-monitor`
skill closes that gap** by installing a `UserPromptSubmit` hook that
surfaces clu's events into Claude's context automatically on your next
message.

### How it works

1. clu writes each notification event as a JSON file to
   `~/.config/clu/inbox/` alongside sending the iMessage. Inbox writes
   are unconditional — quiet hours don't gate them (Claude needs the
   context even when you're asleep).
2. The bundled `end_of_line/hooks/clu_inbox_surface.py` hook script
   reads that directory on every user message in Claude Code.
3. Events tagged with the current `project_root` (derived from
   `git rev-parse --show-toplevel`, falling back to `os.getcwd()`) get
   surfaced as a system reminder in the same turn as your message,
   capped at 20 events / 9500 chars.
4. Surfaced events are moved to `~/.config/clu/inbox/processed/` so
   you never see the same event twice.

Walk back to Claude after a notification, type literally anything
("ok", "next", "/post-ship"), and Claude reacts with full context.

### Setup

```bash
$ clu install-skill --force      # one-time; installs /clu-phase + /plan
                                 # + /clu-plan + /brainstorm + /clu-monitor
$ # then, in a Claude Code session opened in any project:
$ /clu-monitor
Installed UserPromptSubmit hook → /Users/you/.../end_of_line/hooks/clu_inbox_surface.py
Settings updated: /Users/you/.claude/settings.json
```

Account-wide, not per-project — one hook covers every clu-managed plan
on the host. The marker at `~/.config/clu/monitor.json` (v2) records
the install so re-running `/clu-monitor` is idempotent.

Under the hood, `/clu-monitor` shells out to `clu install-hook`. You
can run that directly from a TTY if you want to skip the skill (the
CLI refuses non-TTY contexts to prevent worker subprocesses from
silently modifying the user's settings.json).

### Status, reset, uninstall

```bash
# Check installed
$ cat ~/.config/clu/monitor.json
{
  "schema_version": 2,
  "hook_installed_at": "2026-05-12T19:00:00Z",
  "hook_path": "/Users/.../end_of_line/hooks/clu_inbox_surface.py",
  "settings_json_path": "/Users/you/.claude/settings.json"
}

# Inspect pending events (debug)
$ ls ~/.config/clu/inbox/

# Full uninstall
$ clu uninstall-hook            # removes hook entry from settings.json
$ rm ~/.config/clu/monitor.json # forget the install
$ rm -rf ~/.config/clu/inbox    # discard pending events (optional)
```

### What gets surfaced

Every event clu sends an iMessage for, plus two escalation kinds
shipped with the inbox in #20:

- `halted` — plan transitioned to HALTED or HALTED_REPLAN
- `blocked` — worker called `clu block` (first ping)
- `plan_completed` — plan finished cleanly
- `queue_*` — queue lifecycle (skipped, corrupt, repaired, repair_failed)
- `stuck_blocker` — blocker open >30min and not consumed; re-pings every 30min
- `stalled_claim` — claim's lease expired with plan status still RUNNING

iMessages and inbox writes are independent: quiet hours
(`notify.quiet_hours` in `.orchestrator.json`) suppress iMessages but
NOT inbox writes.

### Migration from pre-#20 install

If `~/.config/clu/monitor.json` exists with `schema_version: 1` (the
broken `/schedule`-based install from #19), `is_scheduled()` now
returns False so the CLI tip fires and `/clu-monitor` re-runs cleanly
— the v1 marker is overwritten in place with v2 on the next install.
No data migrated; the v1 `schedule_id` was never used by anything
beyond the routine creation. If you previously scheduled a routine
manually, delete it via `/schedule delete <id>` first.

### Smoke test (run once after install)

After `clu install-skill --force` and `/clu-monitor`, verify the chain
works end-to-end:

```bash
# 1. Drop a one-off event into the inbox.
$ python3 -c "from end_of_line import inbox; inbox.write_event(
    type='smoke', plan_slug='smoke-test', project_root='$(pwd)',
    summary='smoke test event', details={'test': True})"

# 2. Open Claude Code in this directory, type anything (e.g. 'hi').
# 3. Claude should respond aware of the smoke-test event.
# 4. Verify the event moved:
$ ls ~/.config/clu/inbox/processed/    # smoke event should be here
```

If Claude didn't see the event, check:

- `cat ~/.claude/settings.json | jq '.hooks.UserPromptSubmit'` — entry
  present with absolute path to `clu_inbox_surface.py`?
- `cat ~/.config/clu/inbox_hook.log` — the hook logs exceptions here
  before exiting 0; a non-empty log usually points at the cause.

### CLI tips

`clu init` and `clu queue add` print a one-line tip recommending
`/clu-monitor` when the marker is absent. The tip is suppressed when:

- Monitoring is already installed (v2 marker present), OR
- Output is not a TTY (workers see no tip — keeps log files clean)

### Project CLAUDE.md integration

On the first `clu init` in a project, clu offers to append a `## clu`
section to the project's `CLAUDE.md` (mechanism shipped in #19,
unchanged in #20). The section helps future Claude Code sessions orient
on the project's clu workflow across `/clear` boundaries.

The prompt fires once per project. Decline once, and a marker at
`<plan_dir>/.orchestrator/.no-claude-md` suppresses future prompts.
Flag overrides:

- `clu init --inject-claude-md ...` — force inject, no prompt.
- `clu init --no-claude-md ...` — write the decline marker, no prompt.

The injected section is appended verbatim (never overwrites existing
content) and matches:

```markdown
## clu

This project uses clu for autonomous plan execution.

- `clu queue add <slug>` to enqueue a plan; cron dispatches on each tick.
- `clu queue list` for pending; `clu list` for fleet status.
- Run `/clu-monitor` once per machine for background notifications on
  halts and blockers (status: `~/.config/clu/monitor.json`).
- The `/plan`, `/clu-plan`, and `/brainstorm` skills (bundled via
  `clu install-skill`) are the canonical authoring + pre-planning entry
  points. `/clu-plan` produces the clu-format master + sub-plan files;
  `/plan` is the generic single-file fallback.
```

### Live in-session feed (`clu watch`)

The inbox hook is the **AFK channel**: events accumulate while you're
away and surface into Claude on your next message. `clu watch` is the
**at-desk channel**: a live stream of state transitions emitted to
stdout as they happen, one line per event.

```bash
# Watch a single plan (default 1s poll):
clu watch --project . --plan my-feature

# Watch every plan in the current project:
clu watch --project .

# Watch every plan on the host (5s poll by default):
clu watch --all

# JSON lines for jq downstream:
clu watch --project . --plan my-feature --json | jq '.event.type'

# Include verbose bookkeeping events (lease expiry, force-releases, etc.):
clu watch --project . --verbose
```

On startup, `clu watch` prints a `[snapshot]` baseline per plan
(current status + active phase), then streams any new events as the
state files change. SIGINT exits cleanly.

**Pairing with Claude's Monitor tool** — when a Claude Code session
starts a clu plan, arm `Monitor` immediately after `clu queue add`:

```python
Monitor(command="clu watch --project . --plan my-feature", persistent=True)
```

Each stdout line becomes a notification; Claude acts on BLOCKED events
(surfaces the question), PLAN DONE events (kicks off `/post-ship`),
and dispatch failures (surfaces the error). The `/clu-plan` skill
arms this automatically.

Inbox (`/clu-monitor`) and `clu watch` are complementary: both run in
the same session without conflict. The inbox surfaces events from
between sessions (across `/clear` or restart boundaries); `clu watch`
covers the current session live.

#### Task-list mode (`--task-list`)

`--task-list` switches the output to a deterministic protocol that Claude
can parse to populate the native TaskCreate / TaskUpdate UI. Each Monitor
notification is one structured line; Claude calls `TaskCreate` /
`TaskUpdate` based on the prefix.

```bash
clu watch --project . --plan my-feature --task-list
```

**Line shapes**

```
TASK_CREATE task=<slug> status=pending
TASK_CREATE task=<slug>/<phase_id> parent=<slug> status=pending
TASK_UPDATE task=<slug>/<phase_id> parent=<slug> status=<status> msg="<message>"
TASK_UPDATE task=<slug> status=completed
```

- `task=<slug>` (no `/phase`) is the parent task for the plan itself —
  the `parent=` field is **absent** on these plan-scoped lines.
- `task=<slug>/<phase_id>` (with `/phase`) is a child task — the
  `parent=<slug>` field is **always present** so the agent can render
  visual nesting. Claude Code's TaskCreate UI is flat (no `parent_id`
  field), so `/clu-plan` SKILL.md instructs the agent to prefix child
  subjects with `└ ` (U+2514 + space) to render the tree.
- `msg` is double-quote-delimited; inner `"` and `\` are escaped. Max
  100 chars (truncated with ellipsis).

**Status mapping**

| Event | Status | msg |
|---|---|---|
| `phase_started` | `in_progress` | `"attempt N"` |
| `phase_completed` | `completed` | `""` |
| `phase_blocked` | `in_progress` | `"BLOCKED: <question> [<id>]"` |
| `phase_max_attempts` | `in_progress` | `"HALTED (max attempts)"` |
| `systemic_failure` | `in_progress` | `"SYSTEMIC FAILURE: <sig>"` |
| `plan_completed` | `completed` | parent task update |
| `event_paused` | `in_progress` | `"paused"` |
| `event_resumed` | `in_progress` | `"resumed"` |
| `phase_stalled` | `in_progress` | `"stalled"` |
| other default-visible events | (skipped) | — |

Verbose-only events are still gated by `--verbose`; when both `--verbose`
and `--task-list` are active they emit as `in_progress` updates with a
relevant `msg`.

**Bootstrap-then-stream ordering**

On startup, before the snapshot baseline, `--task-list` emits a
`TASK_CREATE` batch — one parent line for the plan, then one per phase in
the master plan's `## Sessions index`. Claude should batch all
`TASK_CREATE` lines arriving within ~200ms as a single `TaskCreate`
invocation. After bootstrap, event-driven `TASK_UPDATE` lines stream
as transitions happen.

If the master plan file is missing, the command exits with `UNKNOWN_TASK`
(6): `no master plan at <path>`. An empty Sessions index is not an error
— only the parent TASK_CREATE is emitted and TASK_UPDATE lines populate
phases ad-hoc.

**Exclusions**

- `--task-list` and `--json` are mutually exclusive (`--task-list and
  --json are mutually exclusive`).
- `--task-list` and `--all` are mutually exclusive in v1 (`--task-list
  requires --plan or single-project (mutually exclusive with --all)`).
  Multi-plan task trees are deferred.

**Claude usage via `/clu-plan`**

The `/clu-plan` skill arms `Monitor` automatically after `clu queue add`:

```python
Monitor(
    command="clu watch --project . --plan my-feature --task-list",
    persistent=True,
)
```

When a `TASK_CREATE` batch arrives, call `TaskCreate` with one task per
line. When a `TASK_UPDATE` arrives, call `TaskUpdate` matching by
`task_id`. If a `TASK_UPDATE` arrives for an unknown task (race), buffer
1 s and retry; if still unmatched, create the task on-the-fly with the
update's status.

## Troubleshooting

### Inbound poller crash-loops

Symptom: `launchctl list | grep com.clu.inbound` shows the PID rolling
every ~10 seconds; `/tmp/clu-inbound.err` repeats one of:

- `notify_inbound: chat.db not found at ...` → check the path in the
  error; rare unless `~/Library/Messages/` has been moved.
- `sqlite3.OperationalError: unable to open database file` → Full Disk
  Access is missing on the python interpreter the LaunchAgent runs.
  Re-grant FDA on **exactly** the path in `ProgramArguments[0]` of the
  plist, then `launchctl bootout` + `bootstrap` to re-pick-up.

### `phase_stalled` not firing for a worker I think is stuck

**Expected behavior since #27:** `phase_stalled` is suppressed when a
worker has never sent a heartbeat (i.e., `last_heartbeat_at ==
started_at`). This is the canonical `claude --print` case: stdout is
buffered until the process exits, so the bundled `/clu-phase` skill
never calls `clu heartbeat` between tool calls.

For these workers, watch `lease_expires` in `clu status` instead. When
the lease expires, the supervisor fires `lease_expired`, releases the
claim, and retries — the plan advances (or halts) exactly as before.
`phase_stalled` is still emitted for workers that called `clu heartbeat`
at least once and then went quiet.

### Worker dispatches but never completes

Symptom: `clu status` shows a live claim that ages past
`stalled_heartbeat_minutes` and eventually past the 30-minute lease.

Check, in order:

1. The per-worker log at `<project>/plans/.orchestrator/logs/<phase>.<token>.log`.
   The worker writes stderr there. Crash on import = look for traceback.
2. Whether the `/clu-phase` skill is installed for the worker. Run
   `clu install-skill --only clu-phase` to drop the bundled skill into
   `~/.claude/skills/clu-phase/SKILL.md` (or copy
   `examples/clu-phase-skill.md` by hand if you want the legacy
   single-file form). Without it, the worker has no contract to follow
   and exits without calling `clu complete` — you'll see the 30-minute
   lease eventually expire and the attempts counter tick up.
3. The dispatch command in `.orchestrator.json`. The template variables
   are `{plan_slug}`, `{phase_id}`, `{token}`, `{state_file}`,
   `{project}`. Typos in those names are silent — `claude` just sees a
   literal `{phase_id}` in its prompt.
4. The 0.5-second fast-fail. If the spawned process exits within
   500 ms, the supervisor logs `dispatch_failed` to the state event
   stream — `clu status` shows it and `cat state.json | jq .events[-5:]`
   has the stderr capture.

#### Manual force-complete after operator rescue

When the worker wrote all the code + tests but died before calling
`clu complete` (typically because the model exited mid-tool-call after
the final `git commit`), the lease eventually expires and the queue
shows the phase `stalled`. `clu retry` would re-dispatch a cold worker
that doesn't know about the on-disk work; what you want is to commit
the partial work yourself and mark the phase done.

```bash
git -C <project> add ... && git -C <project> commit -m "..."
clu force-complete --project <project> --plan <slug> --phase <id> \
    --commit <sha> --reason "worker died after writing files"
```

Behavior:

- Releases any active claim on the phase (no token required — operator
  override).
- Validates commit SHAs against git (same path as `clu complete`).
- Emits `EVENT_OPERATOR_FORCE_COMPLETE` (with `reason`) followed by
  `EVENT_PHASE_COMPLETED` so the supervisor's plan_done detection
  fires on the next `clu tick` exactly as if a real worker had called
  `clu complete` — no special notification path.

Refusal cases:

- Phase already completed → use `clu status` to confirm; nothing to do.
- Phase id not in the plan's `## Sessions index` → typo or wrong plan.
- Phase has no `EVENT_PHASE_STARTED` and no active claim → suspicious
  (phase never ran); pass `--really` if you're certain on-disk work
  exists anyway.

### Worker log shows `<tool>: command not found`

Symptom: per-worker log at
`<project>/plans/.orchestrator/logs/<phase>.<token>.log` contains a line
like `gh: command not found`, `pipx: command not found`, or any
user-installed tool reported missing. Typical victims are anything
under `~/.local/bin` (pipx shims) or Homebrew on Apple Silicon
(`/opt/homebrew/bin`).

Cause: the worker subprocess inherits a sparse PATH from the
LaunchAgent that dispatched it — `claude --print` doesn't get the
operator's interactive shell PATH.

Fix: set `dispatch.path` in `.orchestrator.json` to an absolute,
colon-separated PATH covering every tool the worker needs:

```json
"dispatch": {
  "command": "...",
  "path": "/opt/homebrew/bin:/usr/local/bin:/Users/<you>/.local/bin:/usr/bin:/bin"
}
```

When `path` is non-empty, clu passes `env={**os.environ, "PATH": ...}`
to the worker's `subprocess.Popen` — your absolute PATH overrides the
inherited one, and the rest of the env (`HOME`, `USER`, etc.) is left
intact. Empty or absent = inherit the parent env (the historical
behavior).

Constraints:

- Tilde (`~`) is expanded per segment at config load, so
  `~/.local/bin:/usr/bin` works fine.
- The fix is per-plan. Each `.orchestrator.json` that needs a custom
  PATH sets its own; there's no host-level default.

### iMessage notifications not arriving

1. Open `Messages.app`. Confirm it's signed in and you can iMessage
   yourself. clu does not bootstrap the conversation — the first
   message must already exist.
2. Check `.orchestrator.json` for `notify.imessage.to`. It must be your
   self-chat handle (the same one you'd type into a "To:" field).
3. Check quiet hours. If the event fired at 23:30 local and you have
   the default 22:00–08:00 window, only `halted` is loud. Other kinds
   are deferred until the next tick after 08:00 (and `blocker_sla`
   re-checks freshness on that loud tick before escalating).
4. Force one to confirm the channel works:

   ```bash
   clu notify-test --project P --channel imessage
   ```

   Failure here is an `osascript` / iMessage problem, not a clu
   problem.
5. **Check the osascript stderr log** at
   `$XDG_CONFIG_HOME/clu/imessage.log` (default
   `~/.config/clu/imessage.log`). AppleScript runtime errors are
   appended here. Empty file = osascript never even ran (check #4).
   Non-empty file = AppleScript failed; the message text names the
   specific failure (Automation permission denied, buddy lookup
   failed, Messages.app not running). Tail it while triggering a
   notification to catch the failure mode live:

   ```bash
   tail -f ~/.config/clu/imessage.log
   ```

6. **Automation permission for LaunchAgent dispatches.** macOS
   requires the *parent process* of an AppleScript-driven Messages.app
   send to have "Automation" permission for Messages. When the cron
   tick dispatches from a LaunchAgent, the parent is the python
   interpreter named in the plist — and LaunchAgents *cannot* show
   the permission prompt, so a denial fails silently. Fix:
   - System Settings → Privacy & Security → Automation → find the
     python interpreter at the exact path in
     `ProgramArguments[0]` of `~/Library/LaunchAgents/com.clu.tick.plist`
     → enable "Messages" under it.
   - If the interpreter isn't listed: run `clu notify-test --channel
     imessage` once interactively from the same interpreter so macOS
     surfaces the prompt, then accept.
   - After granting, `launchctl bootout` + `bootstrap` the agent so
     the new permission applies on the next tick.

### Plan halted on max-attempts

```bash
clu status --project P --plan S
```

The `Reason:` line names the specific event that halted the plan
(`max_attempts_exhausted`, `blocker_sla_exceeded`, etc.) and the phase
it halted on. Fix the underlying issue (sub-plan is too ambitious,
worker doesn't have the skill installed, a dependency is missing),
then:

```bash
clu retry --project P --plan S --phase X     # clears the attempts cap
```

`retry` writes a `retry_requested` event that resets the per-phase
attempts floor — the supervisor will dispatch the phase again on its
next tick.

### Systemic failures clu detects

Some worker fast-fails aren't the phase's fault — a missing binary, a
rate-limited API, an expired token. When the post-spawn fast-fail
(0.5 s after dispatch) catches a worker exit, the dispatcher scans the
**last 50 lines** of `<project>/plans/.orchestrator/logs/<phase>.<token>.log`
against a hard-coded signature list. On match, the plan flips to
`paused`, an `EVENT_SYSTEMIC_FAILURE` event is appended (carrying the
matched signature, phase, token, and log path), the phase's attempt
budget is **not** burned, and an iMessage fires through the halt-bypass
gate so a 3am rate-limit doesn't sit silent until morning.

| Signature | Trigger | Operator action |
|---|---|---|
| `missing_binary` | rc == 127 AND log contains `command not found` | Set `dispatch.path` in `.orchestrator.json` to an absolute, colon-separated PATH (see "Worker log shows `<tool>: command not found`" above). Then `clu resume --plan S`. |
| `rate_limit` | log contains `rate limit` or `RateLimitError` (case-insensitive) | Wait for the window to refresh, or roll the key. Then `clu resume --plan S`. |
| `auth_failure` | log contains `401 Unauthorized`, `AuthenticationError`, or `invalid api key` | Fix the credential (re-export `ANTHROPIC_API_KEY` in the LaunchAgent plist, or refresh whatever auth backs the worker). Then `clu resume --plan S`. |

The signature list is hard-coded in `dispatch.py` — no
`.orchestrator.json` knob. A new failure mode lands via PR with a test
in `tests/test_systemic_failure.py`. Each plan observes systemic
failure independently; there's no cross-plan preemption in v1, so if
plan A flags `rate_limit`, plan B's next tick will hit the same
failure and ping you separately. That's accepted noise — the operator
sees the same fix-once action either way.

### `queue.json` corrupt

Symptom: `clu queue list` / `clu queue add` / `clu queue remove` exits
loud with a `queue.json corrupt at ...` message + a paste-into-Claude
diagnosis. The supervisor sends a `queue_corrupt` or
`queue_repair_failed` iMessage when it hits the same path in `tick-all`.

The operator has four paths:

1. **Wait for auto-repair.** If `dispatch.repair_command` is set in
   `.orchestrator.json`, the next `tick-all` will dispatch the repair
   worker (up to 3 attempts per diagnosis-hash). A `queue_repaired`
   iMessage means it's back; a `queue_repair_failed` means the worker's
   output didn't pass clu's slug-preservation rules and the file was
   reverted from backup — go to path 2 or 3.
2. **Inspect the backup.** Every corruption produces a
   `queue.json.corrupt-<UTCstamp>` sibling. Diff it against the current
   file to see what's missing; hand-edit the live file with the parts
   you want preserved.
3. **Start fresh.** `mv queue.json queue.json.bad` — clu treats a
   missing queue file as empty. Pending entries are lost; history is
   lost. Use this when the corruption is total and the backups don't
   help.
4. **Ask Claude in-project.** Open `claude` interactively in the
   project root and paste the diagnosis from the CLI's refusal message.
   The CLI surfaces backup paths in the same output, so Claude can
   read both files and propose a fix without clu's auto-repair gate.

The throttle file lives next to the queue at
`queue.json.repair-attempts`. If you want to retry auto-repair after
hitting the cap, delete it.

### Extending a live lease

If a worker is still running but has consumed most of its 30-minute
window (visible in `clu status` as a short `lease_expires`), extend
from the operator side without touching the worker:

```bash
clu extend-lease --project P --plan S 20   # add 20 more minutes
```

The new expiry is `max(now, current_lease_expires) + timedelta(minutes=N)`,
so it's safe to call on an already-past lease (stalled claim) — it
extends from now, never backwards. Positive integers only; `≤0`
rejected. Appends a `lease_extended` event to the audit log.

### Stuck claim that won't release

If the state file shows a live claim whose worker is definitely dead
(no process at the stamped PID, no log entries) and the 30-minute
lease hasn't expired yet:

```bash
clu release-claim --project P --plan S [--reason "worker OOM"]
```

This nulls `current_claim` and appends a `claim_force_released` event
so the audit log distinguishes operator recovery from automatic lease
expiry. The plan's status is unchanged — `release-claim` is a recovery
action, not a state transition.

The default refuses to release a fresh-heartbeat claim on a running
plan (the heuristic for a live worker). Pause first, or override with
`--force`:

```bash
clu pause --project P --plan S --reason "investigating stuck worker"
clu release-claim --project P --plan S
clu resume --project P --plan S
```

Most of the time, you don't need this — a stale lease releases on the
next tick after expiry, and the phase's attempts counter ticks up
exactly once. Reach for `release-claim` when 30 minutes is too long to
wait or when the worker's exit pattern wouldn't naturally release
(e.g., a Popen orphan whose lease is still in the future).

When you release a claim because the scope changed or you pulled the
worker for a non-worker-fault reason (config fix, mid-flight abort),
add `--reset-attempts` so the attempt counter doesn't penalize the next
dispatch:

```bash
clu release-claim --project P --plan S --reason "scope changed" --reset-attempts
```

This appends an `attempts_reset` event alongside `claim_force_released`,
and the next `phase_started` for the same phase starts fresh from zero.

## Day-to-day commands

| Command | Purpose |
|---|---|
| `clu` | Fleet view across every registered plan |
| `clu status --project P --plan S` | One plan's state, with `Reason:` line on paused/halted |
| `clu list` | Plans registered on this host |
| `clu pause --project P --plan S [--reason ...]` | Stop dispatching new phases |
| `clu resume --project P --plan S` | Un-pause |
| `clu retry --project P --plan S [--phase X]` | Clear max-attempts on a halted phase |
| `clu release-claim --project P --plan S [--force] [--reason ...] [--reset-attempts]` | Clear a stuck `current_claim`; `--reset-attempts` zeroes the attempt counter so the next dispatch starts fresh |
| `clu extend-lease --project P --plan S MINUTES` | Add N minutes to the live claim's lease (operator-only) |
| `clu archive --project P --plan S` | Clean up worktree + branch and move `plans/<slug>.md` to `plans/shipped/<slug>.md` via `git mv`. Idempotent — skips the file move if the plan file is already gone. |
| `clu unregister --project P --plan S` | Drop a plan from the host registry (state file untouched) |
| `clu unregister --all-archived [--dry-run]` | Prune every registry entry whose master plan file no longer exists. Use after archiving plans (e.g. `post-ship`). `--dry-run` previews. |
| `clu queue add <slug>... [--front] [--project P]` | Append (or `--front` prepend) one or more plan slugs to the project's queue. Multi-arg is atomic — any validation failure rejects the whole batch |
| `clu queue list [--project P]` (or bare `clu queue`) | Show pending queue + recent failures |
| `clu queue remove <slug> [--project P]` | Drop a pending slug (moves it to history) |
| `clu answer --project P --plan S <id> <text\|index>` | Resolve a blocker by hand (instead of via iMessage) |
| `clu blockers list --project P --plan S` | Read-only: list open blockers (id, phase, asked-at, question, numbered options) |
| `clu blockers show --project P --plan S <id>` | Read-only: full payload for one blocker (question, options, context, answer if set) + related events |
| `clu logs --project P --plan S [--follow]` | Tail the active worker's log (falls back to the newest log if idle) |
| `clu doctor --project P` | Smoke-test what a worker subprocess sees (PATH + resolved binary locations). No state writes |
| `clu worktree gc [--project P] [--confirm] [--delete-branch] [--include-archived]` | List or remove worktrees of done/halted plans (see "Per-plan worktrees") |
| `clu worktree attach --project P --plan S [PATH] [--branch B] [--base-ref REF]` | Retrofit a worktree onto a plan init'd without one |
| `clu worktree reattach --project P --plan S` | Re-create the worktree dir from the path/branch already in `state.worktree` (recovery for an externally-removed dir) |

The full CLI surface — including worker-side commands like `complete`,
`block`, `spawn`, `heartbeat`, `task-done` — lives under the `cli`
module section of `reference.md`.
