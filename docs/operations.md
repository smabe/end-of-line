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
clu install-skill                       # interactive — installs three
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
clu queue                           # bare → list (default subcommand)
clu queue list                      # same
clu queue remove old-plan           # drop a pending slug (→ history)
```

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

## iMessage notification model

Outbound — fired during supervisor ticks via `osascript`. Kinds:

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

The `notify.imessage.to` handle should be **your iMessage self-chat
handle** (your own number or Apple ID email). clu sends from your Mac to
yourself; you answer from your phone. Without an active iMessage
conversation to that handle, `osascript` will fail silently.

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

### Worker dispatches but never completes

Symptom: `clu status` shows a live claim that ages past
`stalled_heartbeat_minutes` and eventually past the 30-minute lease.

Check, in order:

1. The per-worker log at `<project>/plans/.orchestrator/logs/<phase>.<token>.log`.
   The worker writes stderr there. Crash on import = look for traceback.
2. Whether the `/clu-phase` skill is installed for the worker. Copy
   `examples/clu-phase-skill.md` to `~/.claude/skills/clu-phase/SKILL.md`.
   Without it, the worker has no contract to follow and exits without
   calling `clu complete` — you'll see the 30-minute lease eventually
   expire and the attempts counter tick up.
3. The dispatch command in `.orchestrator.json`. The template variables
   are `{plan_slug}`, `{phase_id}`, `{token}`, `{state_file}`,
   `{project}`. Typos in those names are silent — `claude` just sees a
   literal `{phase_id}` in its prompt.
4. The 0.5-second fast-fail. If the spawned process exits within
   500 ms, the supervisor logs `dispatch_failed` to the state event
   stream — `clu status` shows it and `cat state.json | jq .events[-5:]`
   has the stderr capture.

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
   python3 -c "from end_of_line.notify import send_imessage; send_imessage('you@example.com', 'clu test')"
   ```

   Failure here is an `osascript` / iMessage problem, not a clu
   problem.

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

## Day-to-day commands

| Command | Purpose |
|---|---|
| `clu` | Fleet view across every registered plan |
| `clu status --project P --plan S` | One plan's state, with `Reason:` line on paused/halted |
| `clu list` | Plans registered on this host |
| `clu pause --project P --plan S [--reason ...]` | Stop dispatching new phases |
| `clu resume --project P --plan S` | Un-pause |
| `clu retry --project P --plan S [--phase X]` | Clear max-attempts on a halted phase |
| `clu release-claim --project P --plan S [--force] [--reason ...]` | Clear a stuck `current_claim` after a dead worker |
| `clu unregister --project P --plan S` | Drop a plan from the host registry (state file untouched) |
| `clu unregister --all-archived [--dry-run]` | Prune every registry entry whose master plan file no longer exists. Use after archiving plans (e.g. `post-ship`). `--dry-run` previews. |
| `clu queue add <slug> [--front] [--project P]` | Append (or `--front` prepend) a plan slug to the project's queue |
| `clu queue list [--project P]` (or bare `clu queue`) | Show pending queue + recent failures |
| `clu queue remove <slug> [--project P]` | Drop a pending slug (moves it to history) |
| `clu answer --project P --plan S <id> <text\|index>` | Resolve a blocker by hand (instead of via iMessage) |

The full CLI surface — including worker-side commands like `complete`,
`block`, `spawn`, `heartbeat`, `task-done` — lives under the `cli`
module section of `reference.md`.
