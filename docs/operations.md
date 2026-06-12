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

### Tick cadence

The cron `StartInterval` is the **fallback** cadence; in steady state,
state-changing CLI actions push a tick themselves and the next phase
dispatches within ~2-3 seconds of the previous one completing.

**Push-side (no waiting):** every state-changing command spawns a
detached `clu tick --project <P>` as its last act before exiting.
The spawned tick reads the just-written state and dispatches the next
phase. Sites:

- `clu complete` — worker finishes a phase
- `clu block` — worker reports a blocker
- `clu task-done` — spawned-subtask completion callback
- `clu force-complete` — operator rescue when a worker dies with
  work on disk
- `clu queue add` — operator queues a new plan (first phase
  dispatches within ~5s instead of one cron interval)

The spawn is fire-and-forget: `start_new_session=True` detaches from
the caller's process group so worker exit can't reap the tick, and
stdout/stderr go to `/dev/null`. The per-plan `st.mutate` lock
prevents the spawned tick from racing a coincidental cron tick.

**Pull-side (fallback):** the LaunchAgent cron fires `clu tick-all`
every `StartInterval` seconds (default **30** as of this feature, was
60 pre-tick-on-action). The cron path mainly catches plans where no
push happened: external state mutations, lease expirations, or
operator activity outside the CLI.

**Project-scoped tick (`clu tick --project P`).** Omitting `--plan`
ticks every plan registered to project P, then runs the cross-plan
rule chain (queue advance, auto-archive, worktree conflict scan).
This is what the push-side spawns invoke — covers phase chaining,
queue head dispatch, and cross-plan handoff in one go.

**Opt-out.** Set `"tick_on_action": false` in `.orchestrator.json`
to disable all five push sites for one project (the cron path still
runs). The default is `true`. Use this only if a project's push
ticks are demonstrably thrashing — the default is a near-free
latency win.

```json
{
  "plan_dir": "plans",
  "tick_on_action": false,
  ...
}
```

Existing installs are untouched by the 60 → 30 default change.
Re-bootstrap the LaunchAgent with the latest `examples/clu.tick.plist`
if you want the tighter cadence:

```bash
launchctl bootout gui/$UID ~/Library/LaunchAgents/com.clu.tick.plist
cp examples/clu.tick.plist ~/Library/LaunchAgents/com.clu.tick.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.clu.tick.plist
```

### Log locations

| File | Source |
|---|---|
| `/tmp/clu-inbound.out`, `/tmp/clu-inbound.err` | Inbound poller stdout/stderr |
| `/tmp/clu-tick.out`, `/tmp/clu-tick.err` | Tick driver stdout/stderr |
| `<project>/plans/.orchestrator/logs/<phase>.<token>.log` | Per-worker stderr |
| `<project>/plans/.orchestrator/<plan>.state.json` | Plan state (single source of truth) |

## Watching workers — `clu top`

A read-only `top`-like dashboard of every active worker on the host. Run it in
a side window and leave it up:

```bash
clu top                 # curses live view, all registered plans
clu top --project .     # scope to one project
clu top --once          # one plain snapshot (also the default when piped)
clu top --interval 3    # refresh seconds (default 1.5)
clu top --cols saying,cmd   # show only these metric columns (default: all)
```

`--cols` takes a comma-separated subset of the metric keys `name, ran, act, hb,
pid, cmd, wrote, saying` plus the modular extras `health, tokens, attempts,
lease, progress` (an unknown key is a clean usage error). With no `--cols`, the
table is the full 8 columns as before.

The extra metrics surface signals the flat table doesn't:

- **health** — a single fused glyph (`●` ok / `◐` warn / `✗` dead) folding PID
  liveness, ACT staleness (`> 60s`), heartbeat silence, and the stuck-tool
  marker into one read, so the dangerous *PID-alive-but-wedged* case can't hide
  behind four separate clocks. The `act > 60` threshold matches `clu serve`'s
  web view exactly.
- **tokens** — the last assistant turn's token total (summed from the raw usage
  dict, same math as the web dashboard); catches a runaway loop spending hard.
- **attempts** — `X/max` for the current phase; `2/3` means one retry left
  before the plan halts on max-attempts (invisible in the default table).
- **lease** — countdown to lease expiry (`12m00s` left, `exp` once past).
- **progress** — phase `X/N` from the plan's sessions index (`—` for a
  single-phase plan).

Each of these lives in `end_of_line/top_registry.py` alone — adding one is a
single-file change, no layout or render-loop edit.

In the live view: **`q`** quits, **`w`** toggles detail mode. Columns size to
the terminal — the text fields use all available width and truncate only when
content genuinely won't fit, so a wider window shows more. Detail mode stacks
each worker into a small block with full, word-wrapped COMMAND and SAYING (never
truncated, at the cost of vertical space).

One row per active claim:

```
PROJECT/PLAN·PHASE          RAN     ACT     HB  PID  COMMAND              WROTE          SAYING
HealthData/logging·impl  25m25s    15s    51s   ok  pytest -k logging    logging.py 4s  tests pass, wiring next
```

- **RAN** — elapsed since the current claim was dispatched (resets on re-dispatch).
- **ACT** — age of the most recent transcript entry (the finest "is it doing
  things" clock; far tighter than the heartbeat).
- **HB** — heartbeat age. **PID** — `ok`/`dead` (a live `kill -0` probe, so a
  dead worker is flagged, never shown as quietly idle).
- **COMMAND** — last Bash command (`*` prefix = still running).
  **WROTE** — last file edited + how long ago. **SAYING** — last assistant line.

The command/write/saying columns come from the worker's Claude Code transcript
(`~/.claude/projects/<enc>/<id>.jsonl`) — written by the harness as tools
actually run — and PID/heartbeat from the OS and state. None of it is the
worker LLM's self-report, so `clu top` is an independent check that a worker is
producing work, not just claiming to.

### Deterministic transcript lookup — the `{session_id}` placeholder

By default `clu top` finds a worker's transcript by encoding its working
directory to the `~/.claude/projects/` dir and confirming the match by the
in-file `cwd` field. That works, but the encoding is lossy and a dir can hold
many sessions. For an exact, unambiguous match, add `{session_id}` to your
`dispatch.command` so clu hands Claude Code a known id:

```json
"command": "claude --session-id {session_id} --print '/plan {plan_slug} (resume phase {phase_id}; state at {state_file})'"
```

clu then generates one uuid per dispatch, passes it to `--session-id`, and
stamps it on the claim; `clu top` reads the transcript file directly. Omit the
placeholder and clu stamps nothing — Claude Code picks its own id and `clu top`
falls back to cwd-matching.

## Serving the dashboard on the web — `clu serve`

`clu top` renders in a terminal; `clu serve` self-hosts the same worker data as
a web page, so you can watch the fleet from a browser — or, with `--lan`, from
your phone. Read-only, always: there are no kill / release / answer controls
over HTTP.

```bash
clu serve                       # localhost only: http://127.0.0.1:8787/
clu serve --port 9000           # pick a port
clu serve --project .           # scope to one project's plans
clu serve --no-transcript       # metrics only — omit command / SAYING / writes
```

`GET /` serves the dashboard (the same Tron view as `clu top`, with split /
strip / phone geometries; `↑↓`/`jk` select, `w` cycles geometry); the page polls
`GET /api/workers` every ~1.5s for the live rows. Ctrl-C stops it.

### Reaching it from your phone — `clu serve --lan`

One switch turns on the whole security layer at once — there is no half-secured
middle state:

```bash
clu serve --lan
# clu serve → https://192.168.1.50:8787/login?token=<auto-generated>
#   open that URL once; it sets a read-only session cookie.
#   ⚠ reachable on your LAN at 192.168.1.50 — anyone on this network with the token can view worker activity.
```

`--lan`:
- **binds one auto-detected LAN IP** (never `0.0.0.0`; `--host` overrides),
- **requires a token** — an auto-generated `secrets.token_urlsafe(32)`, cached
  `0600` at `~/.config/clu/serve_token` and reused across runs,
- **serves HTTPS** via a self-signed cert minted with the system `openssl` (SAN
  = bind IP + `localhost`, cached `0600`). Your browser warns that the CA is
  untrusted — expected for a self-signed cert; accept it once,
- **enforces a Host-header allowlist** — the primary defense against
  DNS-rebinding (a malicious page in your phone's browser pointing its hostname
  at the server's LAN IP). A cross-origin `Host` gets `421`.

Open the printed `…/login?token=…` URL once. It sets an `HttpOnly;
SameSite=Strict; Secure` cookie, after which the dashboard loads normally;
`Authorization: Bearer <token>` works too (for scripted clients). The token is
the only secret — anyone on your LAN who has it can view worker activity (never
control it).

TLS flags:
- `--cert FILE --key FILE` — use your own PEM pair instead of the auto
  self-signed cert.
- `--http` — serve plaintext on the LAN bind (loud warning; token + transcript
  travel unencrypted, sniffable on shared Wi-Fi). Conflicts with `--cert/--key`.

A non-loopback `--host` (e.g. a fixed LAN IP) is treated exactly like `--lan`:
it requires a token and defaults to HTTPS. clu refuses to expose a non-loopback
bind without a token.

> **Heads-up:** backgrounding `clu serve --lan > serve.log` writes the login URL
> — token included — into that log file. The token is long-lived; treat the log
> like the token file (or read the token from `~/.config/clu/serve_token`).

## Verify your install — `clu demo`

Before you point clu at a real plan, confirm the whole pipeline works end to
end with one command:

```bash
clu demo
```

This stands up a synthetic fleet — four throwaway `demo-*` plans, one per
worker personality — and runs each through clu's **real** init → tick → claim →
transcript pipeline. No real LLM, no token cost, fully deterministic. In a
second terminal, watch them light up:

```bash
clu top          # or: clu serve  (clu demo --serve does this for you)
```

You'll see four live rows:

| Plan | What it does |
|---|---|
| `demo-busy` | works continuously — a live `*` command, fresh ACT |
| `demo-idle` | works briefly, then goes quiet — ACT climbs while HB stays fresh |
| `demo-block` | opens a blocker (answerable with `clu answer`) and exits |
| `demo-dead` | exits mid-work — flagged **dead** by PID-liveness detection |

If those four render, your install is healthy: dispatch, claiming, transcript
location, the dashboard, heartbeats, blockers, and dead-worker detection all
work. (The blocked and dead rows are the point — they prove the failure paths
surface, not just the happy path.)

**Teardown is guaranteed.** Press Ctrl-C and the demo kills every worker,
unregisters every `demo-*` plan, and removes its scratch tree under
`~/.config/clu/demo`. The demo never notifies — it can't reach your phone even
though it exercises the `block` path.

If a `clu demo` was ever hard-killed before its teardown ran, leftovers are
caught two ways:

```bash
clu demo down    # remove any orphaned demo-* state and exit
clu doctor       # reports leftover demo plans (and points you at `clu demo down`)
```

`clu doctor` stays read-only: it reports leftover demo state but never
unregisters — `clu demo down` is the cleanup.

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
   | `--lease-ttl-minutes N` | 60 | How long a worker claim is valid before `lease_expired` fires |
   | `--stalled-heartbeat-minutes N` | derived: `max(15, lease_ttl//2)` (= 30 at the 60-min default) | Threshold for `phase_stalled` (suppressed when no heartbeat received yet — see stall guard below) |
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

## Hardened worker dispatch

clu workers are headless Claude Code sessions launched by
`dispatch.command`. The easy template — `--permission-mode
bypassPermissions` or `--dangerously-skip-permissions` — gives every
worker unrestricted host access. The hardened recipe replaces that with
a layered model (GH #90):

- **The OS sandbox is the boundary.** Claude Code's native Seatbelt
  sandbox confines worker subprocesses to the working tree + temp dirs
  and the allowed network domains. Permission allowlists alone are
  friction, not a boundary — field consensus (CVE-2025-66032 family,
  Flatt Security's bypass write-ups) is that prefix matching can be
  escaped by a determined payload; the sandbox can't.
- **The allowlist is friction.** `--permission-mode dontAsk` denies
  anything not explicitly allowed, which keeps an off-script worker
  from even reaching the sandbox wall in the common case.
- **`clu block` is the escape hatch.** A denied tool call returns a
  denial message to the worker and the session continues (verified
  empirically, claude 2.1.170, 2026-06-10). A worker that genuinely
  needs a denied tool raises a blocker instead of wedging — the
  operator answers by iMessage like any other blocker.

### The recipe

```
claude --print --model claude-fable-5 --permission-mode dontAsk \
  --settings /Users/<you>/.config/clu/worker-settings.json \
  --allowedTools "Bash(clu *),Bash(git *),Bash(python3 *),Bash(gh *),Bash(command -v *),Edit,Write,TodoWrite,Task,Skill" \
  --max-budget-usd 20.00 '/clu-phase {plan_slug} {phase_id} {token} {state_file}'
```

Three deployment requirements, all empirical (2026-06-10):

- **`--allowedTools` MUST be one comma-joined argument.** The flag is
  variadic — split across multiple arguments it eats the following
  prompt argument and the worker never receives `/clu-phase`.
- **The `--settings` path MUST be absolute.** `~` is not reliably
  expanded inside the `shell=True` dispatch line when quoted.
- **`dispatch.path` MUST include clu's bin dir** (see
  `examples/hardened.orchestrator.json`). Cron dispatch inherits the
  LaunchAgent's minimal PATH; without the override, the worker's `clu`
  calls exit 127 and the worker falls back to the absolute path —
  which silently defeats the `excludedCommands: ["clu *"]` sandbox
  exemption (the pattern prefix-matches the literal command text, so
  `/Users/<you>/.local/bin/clu …` runs *inside* the sandbox, and any
  callback that writes outside the working tree — canonical-root state
  for worktree plans, the `~/.config/clu` inbox — dies with
  `Operation not permitted`). Caught by the live denial smoke; the
  blocker landed only because that scratch project's state file sat
  under the sandbox-writable cwd.

**Version floor: claude ≥ 2.1.170.** Some 2.1.11x builds deny `$VAR`
expansion inside allowlisted Bash calls (anthropics/claude-code#51001),
which breaks the worker contract's env-var plumbing.

### What the worker settings carry

`clu init` writes `~/.config/clu/worker-settings.json` from a bundled
template when the file is absent (never overwrites — the file is yours
to tune). Content:

```json
{
  "sandbox": {
    "enabled": true,
    "failIfUnavailable": true,
    "allowUnsandboxedCommands": false,
    "excludedCommands": ["clu *"],
    "network": {
      "allowedDomains": ["github.com", "api.github.com"]
    }
  }
}
```

Fail-closed by design: if the sandbox can't start, the worker doesn't
run (`failIfUnavailable`), and nothing may opt out per-command
(`allowUnsandboxedCommands: false`). `clu` itself runs **outside** the
sandbox via `excludedCommands` — callbacks write state at the canonical
project root and `clu block` spawns `osascript`, both outside the
worktree+tmp cage. clu is the operator's own token-validated CLI;
exempting it keeps every callback working with zero clu changes and
means no `filesystem.allowWrite` entries are needed. Worktree workers'
writes to the canonical shared `.git` are auto-granted by the sandbox
(code.claude.com/docs/en/sandboxing), so per-plan worktrees need no
extra entries either.

**The activity hook rides in this file too.** For `tool_stuck` coverage
(#91), add the PreToolUse/PostToolUse block from the clu-phase SKILL.md
to `worker-settings.json` alongside `sandbox` — hooks load from any
settings source, including `--settings` (probe-verified on claude
2.1.170). Use the `clu activity --start-bash / --end-bash` command form
here, not `python3 -m end_of_line.activity_hook`: this file is
machine-global, and the module form only resolves when the worker's cwd
carries the package source, while `clu` rides `dispatch.path` in every
project. The dispatcher injects `CLU_PLAN/PHASE/TOKEN/PROJECT` into the
worker env at Popen time, so the hook's `[ -n "$CLU_TOKEN" ]` guard
opens exactly for clu-dispatched sessions and short-circuits everywhere
else.

### The allowlist, entry by entry

| Entry | Why the worker needs it |
|---|---|
| `Bash(clu *)` | The worker contract itself: `heartbeat-daemon`, `complete` / `block`, `verify` / `attest`, `prior-blocker`. |
| `Bash(git *)` | Phase commits in the worktree + SHA capture for `clu complete --commit`. |
| `Bash(python3 *)` | The test suite (`python3 -m unittest …`) and stdlib one-liners. |
| `Bash(gh *)` | Best-effort issue references; already optional per the `/clu-phase` skill. |
| `Bash(command -v *)` | Resolving absolute tool paths under the minimal worker PATH. |
| `Edit`, `Write` | File edits. Bare (unscoped) — see residual gaps below. |
| `TodoWrite` | Worker self-tracking across a long phase. |
| `Task` | Review/search subagents (`/code-review` fan-out). |
| `Skill` | `/clu-phase` itself, plus `/code-review` and friends. |

A per-project test command beyond `python3` needs its own entry — e.g.
a project verified by `xcodebuild` adds `Bash(xcodebuild *)` to its
copy of the dispatch command.

### Denials in practice

Under `dontAsk` in `--print` mode, a denied tool call does NOT wedge
the session: the worker sees the denial text, keeps its context, and
later allowed calls still run. The `/clu-phase` contract tells workers
to treat a denial that blocks the phase as a `clu block` trigger, so
the failure mode is a focused iMessage question, not a silent
lease-expiry.

Off-allowlist commands are not always refused outright: a command the
sandbox can contain may execute *inside* it and fail at the boundary
instead (live smoke: `curl https://example.com` ran and exited 56 on
the network block rather than being denied). Same net containment —
the allowlist gates what reaches the host unsandboxed; the sandbox is
the wall.

### Guard rails

- `clu doctor` warns when `dispatch.command` or
  `dispatch.repair_command` carries `bypassPermissions` or
  `--dangerously-skip-permissions`, and points back here. Quiet when
  clean.
- `{plan_slug}` in `dispatch.command` is load-bearing beyond the worker
  prompt: the supervisor's PID-reuse liveness checks
  (`claim_worker_alive`, the orphan reaper) match a claim's plan slug
  against the live process command line. `clu doctor` warns when the
  rendered command can't surface the slug as a bounded token — missing
  entirely, or embedded without delimiters (`x{plan_slug}y`).
- `clu init` materializes the settings template (above) and prints the
  hardened-command hint when the file is absent.
- **Migration ordering:** install the daemon-era `/clu-phase` skill
  (`clu install-skill`) BEFORE swapping `dispatch.command` to the
  hardened recipe. The pre-daemon skill arms heartbeats with a
  background bash compound that scoped permissions deny (#90 spike
  Test B), so an old-skill worker dispatched under the new command
  runs heartbeat-less until lease expiry. `clu doctor`'s skill-drift
  check flags the stale install.

### Residual gaps (v1, documented not fixed)

- **Bare `Edit` / `Write`.** Path-scoped `Write(...)` rules silently
  fail under `dontAsk` (anthropics/claude-code#52962), and the sandbox
  does not govern Claude's file tools — so file edits are allowed
  everywhere the process can write. The sandbox still confines what
  worker *subprocesses* can touch.
- **Per-project tools.** The recipe's Bash allowlist covers the clu
  contract; anything project-specific (simulators, builders) is the
  operator's addition, with its own sandbox implications unverified.

## Type-check gate — basedpyright (this repo)

Since #89 drained the repo to zero basedpyright errors, the type check
is a hard gate at three layers. Each layer pins or floats differently —
this is what runs where:

- **Clean-clone canary** (`scripts/canary.sh`, weekly LaunchAgent):
  builds a fresh venv from the `dev` extra, where basedpyright is an
  **exact pin** (`basedpyright==1.39.7` in `pyproject.toml`). The error
  set shifts across basedpyright releases, so a float would let an
  upstream release turn the canary red with no repo change. A non-zero
  exit fails the run outright (`fail basedpyright`), same as ruff and
  the suite.
- **Worker verify gate**: `.orchestrator.json` (local, untracked) sets
  `quality.verify_command` to
  `basedpyright && python3 -m unittest discover -s tests`, so every
  `clu verify` stamp — required before any `clu complete` — proves the
  tree type-checks AND the suite passes. The command runs with
  `shell=True` (same operator-trust model as `test_command`), so
  chained gates like this work. `clu verify` runs
  sandbox-exempt (via `sandbox.excludedCommands`), and the chained
  command runs as its child — outside the worker sandbox — so the
  gate holds inside hardened workers; `test_command` stays pure-suite
  for the merge gate. This uses whatever `basedpyright` is on the dispatch
  PATH — keep it matched to the pin (below).
- **Local development**: the recommended pipx install floats unless you
  match it by hand. After any pin bump:
  `pipx install basedpyright==1.39.7 --force`. Skew between local and
  pinned versions shows up as "passes locally, canary disagrees" —
  check `basedpyright --version` first when that happens.

Exit-code contract (pyright CLI): 0 = clean, 1 = type errors; warnings
alone exit 0. So bare `basedpyright` is a valid hard-gate command.

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
three things in one command:

1. **Worktree cleanup** — removes the clu-managed worktree + branch if the
   branch is fully reachable from origin; retains with a warning when ahead.
2. **Plan-file move** — `git mv plans/<slug>*.md plans/archive/<slug>/`
   (master + every sub-plan rename). Creates `plans/archive/<slug>/` if it
   doesn't exist. Skips silently when no plan files remain (e.g. manually
   moved in a prior run). Surfaces as `WORKTREE_SETUP_FAILED` if the file
   exists but `git mv` fails (e.g. not tracked, conflicts).
3. **Atomic commit** — commits the rename as `chore: archive <slug>` so
   `git status` is clean on exit. The operator never has to remember to
   commit the staged renames.

After archiving, run `clu unregister --all-archived` to prune the host
registry entry.

### Auto-archive on merge

When using per-plan worktrees, clu automates the post-ship cleanup step.
After you merge `clu/<slug>` to `main` and push, the next cron tick detects
that the branch is an ancestor of `origin/main` and automatically runs the
full archive sequence: worktree removal, branch deletion, plan-file move to
`plans/archive/<slug>/`, and registry entry pruned. The operator receives one
`plan_auto_archived` notification per cleanup.

**End-to-end flow:**

1. Worker finishes; `clu complete` fires `plan_done`; plan reaches `STATUS_DONE`.
2. (Multi-plan batches) `dry_merge_gate_rule` fires; clean result → proceed.
3. Operator: `git merge --no-ff clu/<slug> && git push`.
4. Next cron tick: `auto_archive_rule` detects merged branch, archives, emits
   `plan_auto_archived` notification.

No `clu archive` or `clu unregister` calls needed.

**Opt-out** — add to `.orchestrator.json`:

```json
{
  "auto_archive": false
}
```

With `auto_archive: false`, operators must run `clu archive --plan <slug>` and
`clu unregister --all-archived` manually, as before. Non-bool values (e.g.
`"yes"`, `1`) raise `ConfigError` at startup.

### Remote-branch cleanup (`keep_remote_branches`)

By default, both auto-archive and operator-driven `clu archive` also delete
`origin/<branch>` after the local worktree + branch are removed. GitHub's
"Automatically delete head branches" setting only fires on PR merges, so a
direct-mode ship leaves the feature branch stranded on the remote unless clu
deletes it. `clu ship --direct` skips the feature-branch push entirely for
the same reason — main carries the work, and the local branch is dropped by
archive a few seconds later.

Opt out (preserve worker branches on the remote, e.g. for external CI or
audit):

```json
{
  "keep_remote_branches": true
}
```

With `keep_remote_branches: true`, `clu ship --direct` pushes the feature
branch to origin, and archive cleanup leaves `origin/<branch>` in place.
The local cleanup (worktree dir + local branch ref) still fires either way.

Best-effort semantics: a remote delete that fails because the branch is
already gone (GitHub's auto-delete, another client, manual `git push --delete`)
is treated as success. Other failures (protected branch, hook rejection,
auth) are logged to stderr but never block the archive.

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

## Multi-plan batches

When two or more plans touch overlapping code and run in parallel worktrees,
textual auto-merge may succeed while semantic conflicts slip through silently.
The batch integration gate catches these before the operator merges to main.

### Operator workflow

1. `clu init --project P --plan plan-a --worktree`  
   `clu init --project P --plan plan-b --worktree`

2. Tag both as a batch when queueing:

   ```
   clu queue add --project P --batch my-batch plan-a plan-b
   ```

   `--batch` must be a valid slug. Omitting it → gate never fires for those plans.

3. Workers drain to `done` on their own `clu/<slug>` branches. The dry-merge
   gate fires automatically on the next `clu tick-all` after the second plan
   completes:
   - **Clean** → `KIND_GATE_CLEAN` notification; operator merges branches to
     main and runs `clu archive` on each.
   - **Dirty** → `KIND_GATE_DIRTY` notification (bypasses quiet hours); follow-up
     plan files written to `plans/merge-resolve-<batch>-<ts>.md`. Operator reviews
     the conflict report, fixes, and queues the follow-up:
     ```
     clu queue add --project P merge-resolve-<batch>-<ts>
     ```

4. After all branches are green and merged to main, archive each plan:
   ```
   clu archive --project P --plan plan-a
   clu archive --project P --plan plan-b
   ```

### Re-running the gate manually

After pushing fixes to one or more branches, replay the check without waiting
for the next cron tick:

```
clu validate --project P --batch my-batch
```

Or for ad-hoc cross-branch validation outside of clu plans:

```
clu validate --project P --branches clu/plan-a,clu/plan-b
```

Flags:

| Flag | Default | Meaning |
|---|---|---|
| `--batch B` | — | Resolve DONE members from registry |
| `--branches a,b` | — | Override batch resolution; use exact branch names |
| `--no-suite` | false | Textual-merge only; skip `test_command` |
| `--base-ref REF` | `main` | Base ref to merge off |

Exit 0 = clean; exit 1 = dirty. Stdout reports outcome + conflict files.

`clu validate` does **not** mutate plan state and does **not** write
follow-up plans — it is read-only from clu's perspective. The cross-plan
rule owns those side effects.

> `clu integrate` is a stderr-warning deprecation alias for
> `clu validate`. Existing scripts keep working; new scripts should
> call `clu validate` or `clu ship --check` (which internally
> delegates to `cmd_validate`).

### `clu ship` — post-worker integration

After a worker reaches `STATUS_DONE`, run `clu ship` to land the work
on main and clean up:

```
clu ship --project P --plan X            # preview only
clu ship --project P --plan X --check    # validate only, no destructive steps
clu ship --project P --plan X --yes      # apply (mode from .orchestrator.json)
clu ship --project P --all-done --yes    # ship every DONE plan with an unmerged branch
```

Mode comes from `dispatch.ship_mode` in `.orchestrator.json` (default
`"direct"`); `--direct` and `--as-pr` flags override per invocation.

| Mode | What happens on `--yes` |
|---|---|
| `direct` | validate → checkout main → FF-first merge (merge-commit fallback) → push origin main + branch → trigger immediate tick |
| `as_pr`  | validate → push branch with `--set-upstream` → `gh pr create` → stamp `state.ship_pending`; cleanup runs when GitHub merges the PR and the next fetch bumps `origin/main` |

The supervisor's `ready_to_ship_rule` emits `KIND_READY_TO_SHIP` into
the inbox when DONE plans exist with unmerged branches; the body
contains the exact copy-paste command.

### Configuring `test_command`

Add `test_command` to `.orchestrator.json` to run the suite inside the
scratch worktree after a successful textual merge:

```json
{
  "dispatch": { "command": "claude --print '{plan_slug}'" },
  "test_command": "python3 -m unittest discover -s tests"
}
```

`test_command` is run with `shell=True` inside the scratch worktree. It
inherits the subprocess environment — no venv activation, no PATH
manipulation. The operator owns the trust: whatever is in `test_command`
runs as the clu process user. Keep it to a single test-runner invocation.

Absent or `null` → textual-merge-only mode (still catches the literal
conflict class from the canonical 2026-05-18 incident). `--no-suite`
overrides it to textual-only even when `test_command` is set.

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

Replies in the self-chat have `is_from_me = 1` in `chat.db` because the operator IS
the sender. The poller scopes to one chat (your self-chat) and accepts both
`is_from_me` values; clu's own outbound rows are skipped via an outbound-floor
tracker (`outbound_pending.json` → `inbound_state.json.outbound_rowids[chat_id]`).

By default the inbound daemon auto-resolves your self-chat by joining
`chat → chat_handle_join → handle` for the unique iMessage chat where your handle is
the only participant. If you have multiple self-chat candidates (e.g. iCloud sync
resurrected a stale thread alongside the live one), the auto-resolver refuses with a
hint to set `self_chat_id` on the channel:

```json
{"kind": "imessage", "to": "you@example.com", "self_chat_id": "you@example.com"}
```

Run `clu doctor --project <path>` to see what gets resolved. The `Notify channels:`
section prints `self_chat=<id> (auto-resolved | override)` per iMessage channel, or
the `SelfChatLookupError` message when neither path succeeds.

State files (under `$XDG_CONFIG_HOME/clu/`, default `~/.config/clu/`):
- `inbound_state.json` — `{schema_version, last_inbound_rowid, outbound_rowids}`
- `outbound_pending.json` — pending marks waiting for the poller to drain

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

> Discord's official quick-start
> ([docs.discord.com/developers/getting-started](https://docs.discord.com/developers/getting-started))
> is written around slash commands and steers you toward the Installation page and the
> `applications.commands` scope. clu uses **bot DMs**, not slash commands — ignore that
> path and use the `bot`-scope OAuth2 flow below.

1. Go to `https://discord.com/developers/applications` → "New Application" → name it
   (e.g. "clu"). On the **General Information** page, copy the **Application ID** — this
   doubles as the bot's user ID (`bot_user_id`, needed by the inbound poller).
2. Under "Bot": click "Reset Token" and copy the **Bot Token**. You can't view it again
   without regenerating, so stash it in a password manager.
3. Enable **Message Content Intent** under "Privileged Gateway Intents". (Approval is
   only required once the bot is in 100+ servers; a personal one-server bot just toggles
   it on.) The inbound poller needs this to read your reply text.
4. Under "OAuth2 → URL Generator": scope = `bot` (only — **not** `applications.commands`),
   permissions = "Send Messages" (Text Permissions) + "View Channels" (General
   Permissions — this is Discord's renamed "Read Messages"). Nothing else is needed.
   Copy the generated URL and open it to invite the bot to a personal server (create one
   if needed).
5. In your server settings → "Privacy Settings": enable "Allow direct messages from
   server members." clu DMs you rather than posting in a channel, so this gate must be open.
6. Get your **user ID**: Settings → Advanced → enable Developer Mode, then right-click
   your own username → "Copy User ID."

Add to `.orchestrator.json` (`bot_user_id` is the Application ID from step 1; required
only for the inbound poller, but harmless to include always):

```json
"notify": {
  "channels": [
    {"kind": "discord", "bot_token": "Bot.Token.Here",
     "user_id": "123456789", "bot_user_id": "987654321"}
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

## Global notify config (all projects)

Define your notification channels **once** and have every clu project inherit them,
instead of pasting the same Discord/iMessage block into each project's
`.orchestrator.json`. Channels live in a machine-wide file:

```
~/.config/clu/config.json     ($XDG_CONFIG_HOME/clu/config.json if XDG is set)
```

```json
{
  "notify": {
    "channels": [
      {"kind": "discord", "bot_token": "Bot.Token.Here",
       "user_id": "123456789", "bot_user_id": "987654321"}
    ],
    "quiet_hours": ["22:00", "08:00"]
  }
}
```

**Lock down the permissions** — this file holds your bot token:

```bash
chmod 600 ~/.config/clu/config.json
```

Plaintext + `chmod 600` is the right baseline for a single-user host: `~/.config` is
**not** a git repo, so the token never enters a project's tracked tree (a strictly safer
home than the per-project `.orchestrator.json`). Keep credentials in this global file
only — projects reference a channel by `kind`; never re-embed the token per project, or
rotation becomes an N-file edit.

### How global and per-project config merge

A project's `.orchestrator.json` is layered **on top of** the global config, keyed by
channel `kind`:

| In the project's `.orchestrator.json` | Result |
|---|---|
| (no `notify.channels`, or `channels: []`) | inherits the global channels as-is |
| a channel of the **same kind** as a global one | the project channel **replaces** the global one |
| a channel of a **new kind** | **added** alongside the inherited global channels |
| `{"kind": "discord", "enabled": false}` (mask stub) | the inherited global discord is **disabled** for this project |
| legacy `notify.imessage.to` | normalized to an iMessage channel, then merged — so legacy projects still inherit global channels too |

`quiet_hours`: the project's value wins if set, otherwise the global value applies.

The global file is **optional and fail-open**: if it's missing, empty, or malformed, it's
ignored (a malformed file logs one line to stderr) — a typo in the shared config can never
break a project's load. With no global file present, every project behaves exactly as
before.

To silence one noisy project while keeping the global setup, mask each kind with a
`{"kind": "...", "enabled": false}` stub (or use `clu --no-notify <cmd>` for a single run).

## Setup: clu-watch only (zero external transport)

Skip outbound transport entirely — clu's inbox hook surfaces events into the active
Claude Code session on your next message. No iMessage handle, no bot token needed.

1. `channels: []` (empty or omit `notify.channels`) in `.orchestrator.json`, **and** no
   `~/.config/clu/config.json` (a global config would otherwise be inherited — see
   [Global notify config](#global-notify-config-all-projects)).
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
| `channels: []` | Silence — **unless** a global `~/.config/clu/config.json` is inherited; mask each kind with `{"kind": "...", "enabled": false}` to override that | Yes |

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
| `stalled` | Live claim with no heartbeat past the threshold (explicit `stalled_heartbeat_minutes`, else derived `min(25, max(15, lease_ttl//2))`; 25m at the default 60-min lease) | Gated |
| `plan_completed` | All phases done | Gated |
| `halted` | Plan halted (max attempts, lease expired too many times, etc.) | **Bypasses quiet hours** |
| `queue_skipped` | Queue head abandoned (plan file missing) | Gated |
| `queue_repaired` | Auto-repair fixed a corrupt `queue.json` | Gated |
| `queue_repair_failed` | Auto-repair failed validation — file reverted from backup | **Bypasses quiet hours** |
| `queue_corrupt` | `queue.json` corrupt and auto-repair disabled OR throttle exhausted | **Bypasses quiet hours** |
| `stuck_blocker` | Open blocker un-consumed for >30 min; re-pings every 30 min | Gated (inbox always writes) |
| `stalled_claim` | Live claim's lease expired with plan status still `running`; one-shot per claim | Gated (inbox always writes) |
| `quota_paused` | Worker killed by a quota limit with a parseable reset; project pauses, then auto-resumes (see "Recovering from a quota pause") | Gated |
| `quota_resumed` | Canary survived the reset; quota pause cleared | Gated |
| `quota_stuck` | Quota death whose reset didn't parse; no auto-resume — needs `rm quota.json` | **Bypasses quiet hours** |

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
                                 # + /clu-plan + /clu-reply
                                 # + /brainstorm + /clu-monitor
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

Monitor's lifecycle across `/clear`, `/compact`, and concurrency is
characterized empirically in [`docs/research/monitor-lifecycle.md`](research/monitor-lifecycle.md) —
armed Monitors survive both session-reset commands until their own
`timeout_ms` boundary.

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

#### Operator dashboard mode (`--operator`)

`--operator` narrows the watch stream to the cross-plan-worth-interrupting
event set — the four events the operator should react to in flight rather
than at next session boundary:

| Event (state name) | Surfaced when |
|---|---|
| `tool_stuck` | Worker's Bash tool has been near-zero CPU for ≥5 min (per #67 stuck-tool detection) |
| `phase_blocked` | Worker called `clu block` |
| `attestation_refused` | Worker hit the verify or simplify quality gate |
| `stalled_claim_notified` | Claim's lease expired with plan still `running` |

Note the name pairing: `clu watch --operator` streams `EVENT_*` names from
state.events, while the inbox-hook surface (next section) uses the shorter
`KIND_*` names — `stalled_claim_notified` (watch) corresponds to
`stalled_claim` (inbox). Both surfaces refer to the same logical event
class; only the wire name differs.

Default `clu watch` is too chatty for live dashboarding (phase_started,
queue_popped, lease_extended noise drowns the signal). `--operator`
inverts the default: narrow set, bypass the verbose gate, suppress the
per-plan snapshot baseline.

```bash
clu watch --all --operator             # cross-host dashboard
clu watch --project . --operator       # single-project dashboard
clu watch --all --operator --json      # for jq pipelines
```

Mutex with `--task-list` (the task-list maps don't cover the wedge set
yet — composition deferred). Composes with everything else.

##### SessionStart hook for cold-start arming

The Monitor a session arms before `/clear` or `/compact` keeps delivering
events into the new context (per
[`docs/research/monitor-lifecycle.md`](research/monitor-lifecycle.md)).
The remaining gap is **cold-start**: a brand-new conversation has no
prior Monitor. Close it with the SessionStart hook:

```bash
clu install-hook --session-start      # composes with the default UserPromptSubmit install
```

Adds a `SessionStart` entry to `~/.claude/settings.json` pointing at
`end_of_line/hooks/clu_session_start.py`. On every fresh session, the
hook emits `additionalContext` instructing the session to arm
`Monitor(command="clu watch --all --operator", persistent=True,
description="clu operator dashboard")` unless one is already in flight.

The marker records both fields when both hooks are installed:

```bash
$ cat ~/.config/clu/monitor.json
{
  "schema_version": 2,
  "hook_installed_at": "...",
  "hook_path": ".../clu_inbox_surface.py",
  "session_start_hook_path": ".../clu_session_start.py",
  "session_start_installed_at": "...",
  "settings_json_path": "/Users/.../.claude/settings.json"
}
```

`clu uninstall-hook` removes both entries.

##### Investigate-then-recommend contract

Each of the four wedge event classes carries an instruction block in the
inbox-hook surface telling the receiving session what to do. The contract
is uniform: **investigate autonomously → recommend a recovery path →
wait for explicit operator approval before any destructive action**.
This honors the operator-approval checkpoint in user-level CLAUDE.md.

| Event (inbox name) | Investigate by | Recommend (gated on approval) |
|---|---|---|
| `tool_stuck` | `ps -p <worker_pid>` + `pgrep -P <worker_pid>` | `kill <pid>` / `clu release-claim` / `clu force-complete` |
| `attestation_refused` | Read worker log, compare `stamped_at` to current HEAD | `clu verify` / `clu attest --simplify` / `clu complete --skip-verify` / `--skip-simplify` |
| `stalled_claim` | Read worker log, walk pid tree, check `git status` for uncommitted work | `clu force-complete --commit <sha>` (work on disk) / `clu release-claim` (worker dead) / `clu retry` (clean exit) |
| `phase_blocked` | (existing blocker flow — see `## Background monitoring`) | `clu answer --plan <slug> <blocker_id> <answer>` |

Adding a new wedge class is one entry in
`end_of_line/hooks/clu_inbox_surface.py::WEDGE_INSTRUCTION_BLOCKS` —
the predicate + composition happen automatically.

##### End-to-end verification

Smoke-test the full dashboard chain after `clu install-hook --session-start`:

```bash
# 1. Drop a synthetic attestation_refused into the inbox.
$ python3 -c "from end_of_line import inbox; inbox.write_event(
    type='attestation_refused', plan_slug='smoke', project_root='$(pwd)',
    summary='smoke test', details={'gate': 'verify', 'stamped_at': None,
    'head_sha': 'abc1234'})"

# 2. Open a fresh Claude Code session in this directory.
#    The SessionStart hook injects the Monitor-arming instruction.
#    Claude should arm Monitor(command="clu watch --all --operator", ...).
# 3. Type "hi" — UserPromptSubmit hook surfaces the inbox event PLUS
#    the ATTESTATION_REFUSED_INSTRUCTION block.
# 4. Claude should propose a recovery (clu verify / --skip-verify) and
#    wait for your approval rather than auto-running anything.

# Verify the event moved:
$ ls ~/.config/clu/inbox/processed/    # smoke event lands here
```

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

Symptom: `clu status` shows a live claim that ages past the heartbeat
threshold (`stalled_heartbeat_minutes` if set, else `min(25, max(15, lease_ttl//2))`)
and eventually past the 60-minute lease.

Check, in order:

1. The per-worker log at `<project>/plans/.orchestrator/logs/<phase>.<token>.log`.
   The worker writes stderr there. Crash on import = look for traceback.
2. Whether the `/clu-phase` skill is installed for the worker. Run
   `clu install-skill --only clu-phase` to drop the bundled skill into
   `~/.claude/skills/clu-phase/SKILL.md` (or copy
   `examples/clu-phase-skill.md` by hand if you want the legacy
   single-file form). Without it, the worker has no contract to follow
   and exits without calling `clu complete` — you'll see the 60-minute
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

### Recovering from a quota pause (#94)

The workers run on your Claude subscription, so they share its session /
weekly / model limits. When a worker is killed mid-phase by a quota
limit, it prints a line like `You've hit your session limit · resets
1:50am (America/New_York)` and exits.

**Before #94**, that death was indistinguishable from a real crash: it
burned the phase's attempt budget, and after three deaths the plan
halted on `max_attempts_exhausted` — so an overnight reset left a fleet
of plans frozen until you woke up and ran a manual `clu retry` sweep,
one plan at a time. (The motivating incident: 8 plans frozen ~5.5h past
a 01:50 reset.)

**Now** clu classifies the death from the worker log at all three death
sites and handles it automatically:

- The phase attempt is **forgiven** (a `quota_death` event, like
  `systemic_failure`) — three quota deaths in a row never advance the
  halt counter.
- The whole **project** pauses (not just the one plan) by writing
  `<project>/plans/.orchestrator/quota.json` with `paused_until = reset
  + ~2min`. Every plan's dispatch idles until then; in-flight workers
  and the watchdogs keep running.
- You get one `quota_paused` iMessage carrying the local resume time.
  It's **gated by quiet hours** — there's nothing to do, so it won't
  wake you; `clu watch` and the inbox show the event regardless.
- Past the reset, the first plan to tick dispatches as a **canary**
  while the rest idle. If it survives ~3 min, clu deletes `quota.json`,
  emits `quota_resumed`, and the fleet resumes on its own. If the
  account is still throttled, the canary re-dies and re-pauses with the
  new reset time — no attempts burned, no action from you.

**The stuck case.** If the reset time doesn't parse — a weekly limit
(`resets Mon 12:00am`), a date form, or future wording the table doesn't
know — clu can't schedule an auto-resume. It writes a **stuck pause**
(`paused_until: null`), which idles every plan indefinitely and sends a
`quota_stuck` iMessage that **bypasses quiet hours** (a fleet frozen
with no horizon is halt-equivalent). The escape hatch is one command,
once your quota is actually back:

```bash
rm <project>/plans/.orchestrator/quota.json   # clears the pause; plans dispatch next tick
```

That's the same file the auto-resume deletes — "file absent == not
paused" is the whole contract. Deleting it by hand is always safe; the
worst case is a still-throttled worker re-dies and re-pauses.

The signature table (what counts as a quota death) and the reset parser
are hard-coded in `quota.py`; a new wording lands via PR with a test in
`tests/test_quota.py`. See `architecture.md` § "Quota pause gate" for
the canary state machine and `contract.md` § "Quota pause file schema"
for the `quota.json` fields.

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

If a worker is still running but has consumed most of its 60-minute
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
(no process at the stamped PID, no log entries) and the 60-minute
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
exactly once. Reach for `release-claim` when 60 minutes is too long to
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
| `clu archive --project P --plan S` | Clean up worktree + branch and move `plans/<slug>*.md` (master + sub-plans) to `plans/archive/<slug>/` via `git mv`. Idempotent — skips the file move if the plan files are already gone. |
| `clu migrate-archive --project P [--dry-run]` | One-shot migration from the pre-#65 flat `plans/shipped/<file>.md` layout to the nested `plans/archive/<slug>/<file>.md` layout. Groups by longest-prefix master, `git mv`s each group, removes the empty `plans/shipped/` dir, and commits the renames. Idempotent (no-op when `plans/shipped/` is absent). |
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
| `clu validate --project P [--batch B \| --branches a,b,c] [--no-suite] [--base-ref REF]` | Dry-merge a batch's branches in a scratch worktree and run `test_command` if configured. Mode-agnostic validate path; does NOT mutate plan state or file follow-ups. Exit 0 = clean; exit 1 = dirty |
| `clu integrate ...` | **DEPRECATED** alias for `clu validate`. Prints a stderr warning; same args. New scripts should call `clu validate` directly. |
| `clu ship --project P --plan X [--direct \| --as-pr] [--check] [--yes]` | Single-plan post-worker integration. Validate → merge to main (or open PR) → push → trigger archive. Mode defaults from `dispatch.ship_mode` in `.orchestrator.json`. Without `--yes`, prints preview and exits OK. |
| `clu ship --project P --all-done [--direct \| --as-pr] [--check] [--yes]` | Batch post-worker integration — ships every DONE plan with an unmerged branch behind one `--yes`. Per-plan failures logged; batch continues. |

The full CLI surface — including worker-side commands like `complete`,
`block`, `spawn`, `heartbeat`, `task-done` — lives under the `cli`
module section of `reference.md`.
