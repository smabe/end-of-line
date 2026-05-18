# clu — End of Line

clu is a cron-driven plan orchestrator. The vocabulary below names the
moving parts an architecture review needs to talk about without falling
back into generic systems language ("service," "component," "API").

## Language

### Process roles

**Supervisor**:
The `clu tick` process that cron fires every 5 minutes. Reads the state file under a lock, picks one rule from the priority chain, writes one event, optionally spawns a worker, exits.
_Avoid_: scheduler, orchestrator (clu as a whole is the orchestrator; the supervisor is the tick process).

**Worker**:
A short-lived `claude --print` process spawned by the supervisor for exactly one phase. Reads its sub-plan, edits code, runs tests, and calls a worker callback before exiting.
_Avoid_: agent, child process, runner.

**Operator**:
The human at the keyboard. Talks to clu through `clu status`, iMessage replies, and lifecycle commands (`pause / resume / retry / extend-lease / release-claim / force-complete`).
_Avoid_: user, admin.

**Inbound poller**:
The long-lived LaunchAgent (`clu inbound`) that tails `chat.db` for iMessage replies and routes each one into `clu answer`.
_Avoid_: listener, watcher.

### Plan + execution units

**Plan**:
A markdown file (`plans/<slug>.md`) plus its state file (`plans/.orchestrator/<slug>.state.json`). The state file is the durable artifact; the markdown is the worker-facing spec.
_Avoid_: project, job.

**Master plan**:
The top-level markdown with a `## Sessions index` table listing phases. Each row points at a sub-plan file.

**Sub-plan**:
A markdown file for one phase. The worker reads it as its entire context for one run.

**Phase**:
One row in the `## Sessions index`. The unit the supervisor dispatches and the worker executes. Identified by a slug.

**Claim**:
The `current_claim` record on a state file. Marks a phase as in-flight, carries the worker's token, PID, lease expiry, and heartbeat timestamp. At most one claim per plan.
_Avoid_: lock, lease (the lease is a *field* on the claim).

**Token**:
A random opaque string minted at claim time, written into `claim_phase`, and required on every worker callback. The entire security boundary between a well-behaved worker and a misbehaving one.
_Avoid_: id, key, secret.

**Lease**:
The expiry timestamp on a claim. If it passes while the claim is still live, the supervisor releases the claim and the phase's attempt counter increments.

**Blocker**:
A question a worker raised via `clu block`. Carries an id (`q-1`, `q-2`, …), an options list, and (eventually) an answer index. Bridges the worker → operator → worker round-trip.
_Avoid_: question, prompt (those describe the *content*; "blocker" names the state-file record).

**Worktree**:
A per-plan git worktree at `~/.cache/clu/worktrees/<slug>/`. When present, workers run with `cwd=worktree.path` instead of the project root.
_Avoid_: branch, checkout.

### State machinery

**State file**:
The single durable artifact per plan. JSON, schema-versioned, mutated only inside `state.mutate`. Holds the event log, current claim, blockers, worktree record, status, and config.
_Avoid_: database, store.

**Event**:
An immutable append-only record on `data["events"]`. Tagged with an `EVENT_*` constant. The event log is the source of truth for every projection (`completed_phase_ids`, `latest_event`, etc.).
_Avoid_: log entry, message.

**Projection**:
A function over the event log that derives current truth (e.g. `state.completed_phase_ids(data)`). A typo'd event type silently breaks the projection — that's why `EVENT_*` constants exist.

**Tick**:
One invocation of `supervisor.tick`. Produces at most one action (the priority chain is first-match-wins). The `cmd_tick_all` cron entry runs one tick per registered plan plus per-project post-loop passes.

**Priority chain**:
The eight-rule list inside `supervisor.tick`. Order is load-bearing — every "why didn't this tick advance?" reduces to "which rule fired first?".

### Cross-plan machinery

**Queue**:
The per-project `queue.json`, the file that chains plans end-to-end. The queue advancement step in `cmd_tick_all` pops the head when the project's busy gate is clear.

**Registry**:
The host-level `~/.config/clu/registry.json`. Maps slug → plan directory so the fleet view (`clu`) can find every plan across every project.

**Worker callback**:
Any CLI command a worker invokes to report progress: `complete`, `block`, `spawn`, `task-done`, `heartbeat`, `queue add`. All take `--token` and validate it via `assert_claim_match`.

**Inbox**:
The per-event JSON store at `~/.config/clu/inbox/`. Surfaced into the next Claude Code session via the `UserPromptSubmit` hook. Quiet hours do NOT apply to the inbox.

**Quiet hours**:
The 22:00–08:00 window. Gates most iMessage sends; the `QUIET_HOURS_BYPASS_KINDS` set names the exceptions (currently `halted`).

**Notifier / InboundPoller**:
Pluggable protocols (post-#11). A Notifier sends one outbound message; an InboundPoller reads replies. iMessage and Discord are the two adapters today.

## Relationships

- A **Plan** has one **state file** and one **master plan** markdown.
- A **Master plan** has one or more **Phases**; each Phase has one **Sub-plan**.
- A **Plan** has at most one live **Claim**; the Claim carries one **Token** and one **Lease**.
- A **Worker** is spawned for exactly one **Phase** and calls **Worker callbacks** with the **Token**.
- A **Phase** can spawn one or more **Blockers**; each Blocker is answered by exactly one **Operator** reply.
- **Events** are the only source of truth on the **State file**; everything else is a **Projection**.
- A **Queue** belongs to one project and contains zero or more Plan slugs awaiting dispatch.
- A **Worktree**, when present, is owned by exactly one **Plan**.

## Example dialogue

> **New contributor:** "If a worker hangs, does the supervisor kill it?"
> **clu regular:** "No — the supervisor never touches workers. The **lease** on the **claim** expires, and the next **tick** releases the claim. The worker process is still running somewhere; we just stop trusting its **token**. Eventually launchd or the OS will reap it."
>
> **New contributor:** "So how does the operator answer a **blocker** if they're on the subway?"
> **clu regular:** "iMessage. The **inbound poller** reads `chat.db`, parses `<slug>? <digit>`, and shells out to `clu answer`. That writes the answer into the **state file** as an **event**. Two ticks later, the **phase** redispatches with the answer in hand."

## Flagged ambiguities

- "lease" was occasionally used to mean the whole claim. Resolved: **claim** is the record, **lease** is the expiry field on it.
- "agent" is avoided entirely — too overloaded (Claude agent, LaunchAgent, the operator-as-agent). Say **worker**, **LaunchAgent**, or **operator**.
- "task" appears in two senses: a **spawned task** (`spawn` / `task-done` worker callbacks) is a sub-unit of a phase; a **Claude TaskCreate task** is a UI affordance fed by `clu watch --task-list`. Disambiguate explicitly.
