# p4 — Resident monitor console (clu-monitor skill)

The operator-facing half: a live Claude session that discovers running clu
workers and drives status/stop. Opt-in — nothing here runs unless the operator
starts the console. SKILL.md edit, no Python.

## Locked decisions

- **The console is a resident Claude session, not a hook.** Today
  `clu-monitor/SKILL.md` installs a `UserPromptSubmit` hook (SKILL.md:15) — that
  stays. This adds a distinct *console mode*: guidance for a live session that
  talks to workers. Keep the two clearly separated in the skill (hook install vs.
  live console); do not conflate — the hook is durable notification wiring, the
  console is live tooling that coexists with it.
- **Discovery is by name.** The console lists peers via `ListAgents` and matches
  `clu-<plan_slug>-<phase_id>` rows (p1). It does NOT parse sockets or pids.
- **Same-name collision is GUARANTEED, not an edge case (p1 plan-time probe,
  item vii).** Every re-dispatch of the same plan+phase (lease expiry, blocker
  resume) renders the identical name, and a dead worker's `ListAgents`
  registration can linger. The console MUST target the LIVE worker — prefer the
  most-recently-started row and cross-check liveness against clu state
  (`clu top` / the state file's `current_claim.pid`), never blind-send to a
  name that resolves to two rows. cwd does not disambiguate here (a re-dispatch
  runs in the same cwd).
- **State is still the source of truth.** The console reads live status via
  SendMessage for immediacy, but authoritative status is the state file / `clu
  top` / `clu serve`. A stop's durable ack is the `blocked_operator_stop` blocker in
  state (p2), not the message round-trip.

## Work

- `end_of_line/skills/clu-monitor/SKILL.md`: add a "Live console" section:
  - how to find clu workers: `ListAgents` → rows named `clu-<plan>-<phase>`;
    cwd disambiguation.
  - status: `SendMessage` "report status" to the worker's name; read the reply.
  - stop: `SendMessage` "stop now: commit WIP and block with --operator-stop" to
    the worker; then confirm via `clu blockers show` (the durable ack, p2) that
    the plan paused with a `blocked_operator_stop` blocker.
  - live-worker targeting: resolve a `clu-<plan_slug>-<phase_id>` name to the
    LIVE worker (most-recent row + liveness cross-check against
    `current_claim.pid`); refuse to send when a name resolves to two rows without
    a liveness tiebreak.
  - the trust framing: the console is an ordinary operator session (no sandbox
    exemption needed); it never sends anything that would ask a worker to bypass
    its own gates.
  - coexistence: the console does not replace the hook, `clu top`, or `clu serve`.
- Grep other skills/docs for `clu-monitor` references that this split makes stale
  (`rg -n "clu-monitor" end_of_line/skills docs`).
- No tests (skill prose); observable is a live end-to-end run.
- Consumes: worker addressing/reply (p1), operator-stop callback (p2), worker
  behavior (p3).
- Produces: the operator workflow; consumed conceptually by p5's docs.

## Done criteria

- **Observable (end-to-end):** from a console session, discover a live clu worker
  by its `clu-<plan>-<phase>` name, run a status round-trip (ask → reply), then
  stop it (message → worker commits + `clu block --operator-stop` → plan pauses),
  and confirm the `blocked_operator_stop` blocker via `clu blockers show`. One continuous
  demonstration, captured (transcript or a written run log naming each step's
  observed result).
- Reference check clean (doc-only diff).

## Decisions & findings
<!-- sealed at phase commit -->
_pending._
