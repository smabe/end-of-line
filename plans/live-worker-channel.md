# live-worker-channel — operator↔worker live channel (issue #100)

## Status & cold-start

**Approval: APPROVED 2026-08-10**
**Authored at: 65294f5**

**Operator sign-off (2026-08-10):**
- **Security posture AUTHORIZED** — `crossSessionInbound:"accept"` +
  `SendMessage`/`ListAgents` allowlist on hardened workers (bounded: same-uid,
  advice-into-context). Do NOT re-surface this at execution.
- **Stop semantics CONFIRMED** — "stop now" = worker commits WIP +
  `clu block --operator-stop` (block-and-wait for operator direction). NOT the
  release-and-auto-respawn alternative.

<!-- Drafting session line intentionally omitted: only one uuid source visible
     this session (scratchpad path component); per /plan step 6 single-source
     doubt → omit, DRAFT freeze stays repo-wide. -->

NEXT: **p1** — read `plans/live-worker-channel-p1.md` FIRST.

Binding decisions carried inline for p1: workers **whose dispatch command opts
in via a `{worker_name}` placeholder** render a deterministic name
`clu-<plan_slug>-<phase_id>` (the same opt-in model as the existing
`{session_id}` placeholder — a worker with no `{worker_name}` in its template
gets no name and the console cannot address it) so the console resolves them by
name via `ListAgents` (no socket registry — SendMessage routes by name, not pid);
`SendMessage`/`ListAgents` join the worker `--allowedTools` so a worker can
reply; `crossSessionInbound: "accept"` goes into `worker-settings.template.json`
so a headless `--print` worker never hangs on a held message.
**⚠️ Security posture — surfaced for explicit sign-off at approval (below):**
`crossSessionInbound:"accept"` + allowlisting `SendMessage`/`ListAgents` opens an
auto-accepted inbound channel into workers that already hold broad
Bash/git/gh/Edit/Write tools. Bounded to same-uid + advice-into-context (a
message can't approve, change config, or run commands), but it is a real
attack-surface widening the operator must authorize, not a silent default.

### Verification record
Read-back audit ran 2026-08-10 (4 agents; one fix pass; describes the plan as
written at approval).
- **Claims (grounding auditor):** checked 23 cited claims — 20 resolve, 1 hard
  error, 2 partial. FIXED: normal-block type value is `blocked_input` not `input`,
  and the new type follows the `blocked_` convention → `blocked_operator_stop`
  (was `operator_stop`) [p2/p3/p4]; citation drift `_maybe_write_attempt_context`
  is `dispatch.py:580` / `_last_termination_reason` `dispatch.py:498` [p2];
  version attributed — official-doc 2.1.224+ minimum, probe ran at 2.1.226
  [master]. The 4 external Claude-Code behavior claims are grounded in the
  stage-zero probe + the step-4 C2/adversarial doc fetches (cited in Background),
  not open.
- **Done criteria (executability auditor):** 5 master + 14 shard criteria, 13
  interface entries (all forward/backward paired), 0 tiered phases, ordering &
  self-sufficiency clean. FIXED: p1's doc-prose edit had no covering criterion and
  duplicated p5 → moved to p5 [p1/p5]; overview mis-attributed `dispatch.py` to p1
  only → now p1+p2 [master]. PROMOTED: `crossSessionInbound:"accept"` is a
  user-visible security posture → surfaced at approval (below). FIXED: p3
  status-reply copy reworded from locked to illustrative.
- **Coherence (coherence auditor):** ~11 rules, ~13 characterizations, ~6
  restatements. FIXED 2 contradictions: `--name` stated unconditional but the
  mechanism is opt-in via `{worker_name}` → stated as opt-in [master/p1]; "only
  Python change" contradicted p1's dispatch.py edit → "no new Python module;
  small additions to dispatch.py (p1) + cli.py/state.py (p2)" [master].
- **Dry run (p1, worktree prober):** green — 1970/1970 unittest, basedpyright 0
  on changed code. LISTED all edited files; MISSING: none. Live cross-session
  round-trip = OBSERVABLE-UNAVAILABLE in a worktree (needs a running worker + 2nd
  session) — the risky observable was already proven in stage-zero. FIXED SKETCH
  defects: the example must use `--name {worker_name}` (route i), not
  `--name clu-{plan}-{phase}` (`{plan}`/`{phase}` aren't valid placeholders);
  dropped the misleading "mirror the `{session_id}` opt-in / detection function"
  framing (`{worker_name}` has no side effect, needs no detection) and fixed the
  test-name reference [p1]. Item (vii): same plan+phase re-dispatch renders an
  IDENTICAL name — guaranteed, not edge — routed to p4's Done as live-worker
  targeting. Item (viii): internal design decisions (derive name in
  `render_command`, no claim-stamp, shlex-quote for parity) baked into p1.
- All fixes were corrections (values/wording the findings supplied) or the
  prober's own validated build written back — no new mechanism introduced, so no
  re-probe. One item PROMOTED to the operator (security posture); none refuted;
  none left uncheckable.

---

## Goal

Give the operator a **live, optional channel to a running clu worker** — ask it
for status, and tell it to stop — over Claude Code's cross-session
`SendMessage`/`ListAgents` (official doc states 2.1.224+ minimum; stage-zero
probe ran at 2.1.226). The channel is driven by a
**resident "monitor console" Claude session** the operator runs; clu-Python is
never in the message path — it only makes workers addressable and inbound-capable.

**This redefines issue #100's headline acceptance criterion.** The issue asked
for *warm blocker answers* — messaging a still-running worker its blocker answer
instead of cold-re-dispatching. Research (10 agents + a live probe) established
that is **against clu's grain and was declined** (see Background findings →
"Why not warm blockers"). The channel is confirmed real and delivered; what
ships is status + stop, not warm-resume. The issue is resolved with that writeup
(p5).

## Background findings (cross-phase — belong to no single phase)

**The stage-zero probe (2026-08-10, Claude Code 2.1.226).** Both mechanics
forks were settled by a live probe, not by doc-reading:
- A headless `claude --print --session-id <uuid>` worker **binds an inbox
  socket** (`/tmp/cc-socks/<pid>.sock`, dir 0700 / socket 0600 → same-uid only),
  **appears in `ListAgents`**, and **acts on a delivered message at its next
  tool-call boundary** (drain ≈ remaining in-flight tool time + one round;
  ~26s observed behind two 15s sleeps). NOTE: the official doc claims bare
  `--print` does not bind; the probe CONTRADICTS that for 2.1.226 — trust the
  probe, and re-probe on Claude Code upgrades (the feature is ~days old, format
  undocumented).
- A plain Python process **can** write directly to the socket
  (`{"type":"user","message":{"content":"..."}}\n`, `verifiedPeerPid` via
  SO_PEERCRED). **Unused by this plan** — the chosen design routes every message
  through the `SendMessage` *tool* between two Claude sessions (console↔worker),
  so clu-Python needs no socket-writer. Banked in the issue writeup only.

**Addressing is by NAME, not socket.** `SendMessage` resolves targets through
`ListAgents`, which reads Claude Code's own registration files. clu never manages
a socket path. Deterministic `--name clu-<plan>-<phase>` at dispatch is the whole
addressing mechanism; pid reuse and `claim.pid`-is-the-shim
(`dispatch.py:805-809`) are irrelevant because nothing routes by pid.
(Adversarial re-scope agent, verified against the cross-session-messaging doc.)

**`crossSessionInbound: accept` is safe, bounded to advice-into-context.** A
cross-session message can't approve permissions, can't change config, and any
command in it arrives as inert text (official doc, *How a session treats an
incoming message*). The one real, bounded risk is **prompt injection**: any
same-uid process can post and steer a worker within its *existing*
`--allowedTools` + Seatbelt + `CLU_TOKEN` (e.g. coax a premature `clu complete`).
That is not a sandbox/token escape — it is the same-uid trust model clu already
runs under. Documented in p5, mitigated in the worker contract (p3: treat inbound
as advice; never `clu complete` on a message's say-so alone). No sandbox
unix-socket change is needed — the worker's Claude *process* binds its own socket
and replies via the tool; Bash never touches it.

**Why not warm blockers (the declined path).** `clu block` releases the claim
and the worker **exits** (`cli.py:6069` → `release_claim_and_emit`,
`state.py:815`); at answer time there is no running worker to message. Making a
worker linger to receive a live answer fights every watchdog (all key off
`current_claim`), the 60-min lease vs. hours-long blocker SLA (would reap it
mid-wait), the tick chain (would double-dispatch), and it sacrifices what cold
respawn does incidentally: bounding infinite blocker loops at 3 attempts
(`state.py:109`, `supervisor.py:844-865`), honoring the quota-pause gate
(`supervisor.py:873-877`), and re-reading a possibly-edited sub-plan with a fresh
context window. Warm-resume also **pollutes** the worker's context — the exact
coupling clu's cold-context model was built to avoid; clu externalizes progress
to git + state (attempt-context sidecar, `dispatch.py:580-605`) precisely so
live worker context is disposable. (Teams A1/A2/A3, B1, exclusion specialist —
unanimous.)

**Stop semantics.** An operator "stop now" resolves to: the worker commits WIP
and calls `clu block` with an **operator-stop** blocker type, so the plan pauses
and the operator's redirect is delivered as the blocker answer into the fresh
cold worker (`clu prior-blocker`, clu-phase SKILL.md:85). `clu complete` with no
commits is the wrong verb — it still enforces the verify/simplify gates
(`cli.py:4736-4823`). A "release-and-auto-respawn" alternative was rejected: it
races the operator's edits against the cron tick and changes nothing unless the
operator edits in the gap. (Change-impact re-scope agent.) **This is a
user-facing decision — surfaced at approval; default is block-and-wait.**

**Reuse (moot).** The reuse specialist recommended a standalone module over
extending the `notify_*` family, but the chosen design adds **no new Python
module** at all (clu-Python isn't in the message path). The Python changes are
small additions to existing files: a `{worker_name}` placeholder in
`dispatch.py`'s `render_command` (p1) and a new blocker *type* in
`cli.py`/`state.py` (p2). Nothing mirrors `notify_*`; the fork does not arise.

## Phase map

**Phase p1 — Addressing + inbound plumbing**  *(gate: stage-zero probe must pass against the real dispatch path)*
- Enters when: approved plan (start here).
- Done signal: a dispatched worker registers in `ListAgents` as
  `clu-<plan>-<phase>` and both receives a message and replies to it (probe).
  Detail in the shard.
- If it fails: `--name` doesn't register or reply fails → back to Mode 1
  step 4 with the failing probe as the sharper question.
- Shard: `plans/live-worker-channel-p1.md`

**Phase p2 — Operator-stop blocker type**
- Enters when: p1 shipped.
- Done signal: a worker that blocks with `--operator-stop` records the type in
  state; `clu blockers show` displays it; TDD green.
- If it fails: shipped interface differs from p2's `Produces:` lines →
  re-plan trigger at this phase (Mode 2 approach-switch).
- Shard: `plans/live-worker-channel-p2.md`

**Phase p3 — Worker contract (clu-phase skill)**
- Enters when: p1 + p2 shipped.
- Done signal: a live worker sent "status" replies with a status line; sent
  "stop" commits + blocks with the operator-stop type + exits.
- If it fails: worker cannot reply or cannot reach the operator-stop callback
  → the plumbing (p1) or callback (p2) is wrong, not the prose; re-open that phase.
- Shard: `plans/live-worker-channel-p3.md`

**Phase p4 — Resident monitor console (clu-monitor skill)**
- Enters when: p1–p3 shipped.
- Done signal: the console drives a real worker through a status round-trip and
  a stop, end-to-end.
- If it fails: name discovery unreliable in `ListAgents` → back to p1's
  addressing decision.
- Shard: `plans/live-worker-channel-p4.md`

**Phase p5 — Docs + issue #100 writeup**
- Enters when: p1–p4 shipped.
- Done signal: every acceptance-criterion line in #100 is addressed (satisfied
  or explicitly declined with reasoning); reference check clean.
- If it fails: a shipped behavior contradicts what a doc would claim → fix the
  behavior, not the doc (do not paper over).
- Shard: `plans/live-worker-channel-p5.md`

## Files touched (overview)

- `end_of_line/dispatch.py` — `{worker_name}` placeholder in `render_command`
  (p1); operator-stop case in the prior-attempt sidecar
  (`_last_termination_reason`, if touched) (p2).
- `end_of_line/worker-settings.template.json` — `crossSessionInbound:"accept"` (p1).
- `examples/hardened.orchestrator.json` — `--name` + allowedTools example (p1).
- `end_of_line/cli.py` — `--operator-stop` on `clu block`; `blockers show`
  rendering (p2); worker `--allowedTools` guidance if clu-owned (p1).
- `end_of_line/state.py` — `BLOCKER_OPERATOR_STOP` type + threading (p2).
- `end_of_line/skills/clu-phase/SKILL.md` — status/stop worker contract (p3).
- `end_of_line/skills/clu-monitor/SKILL.md` — the console (p4).
- `docs/contract.md`, `docs/architecture.md`, `docs/operations.md` — (p5).
- `tests/` — addressing/dispatch (p1), operator-stop type (p2).

## Non-goals

- **Warm blocker answers** (issue #100's headline) — declined; see Background.
  Rationale for excluding this peer of "status/stop": warm-resume mutates a cold
  worker's context, which the other two uses (status = read-only; stop = worker
  exits normally) never do. The asymmetry is the whole point.
- **Worker↔worker messaging** — stays file-state-mediated (issue non-goal);
  direct chatter would route around the dry-merge gate.
- **Replacing the inbox / notifications** — the inbox is durable and survives
  worker death; SendMessage is live-only. Status-over-SendMessage is always **in
  addition to** state events, never instead — state stays the source of truth.
- **clu-Python sending on the socket** — the direct-Python-write path is proven
  but unused; every message routes through the SendMessage tool between sessions.
- **Supervisor→worker course-correction / dashboard nudge** (issue candidates
  #2/#4) — pure additional call sites of the same name-addressing once it exists;
  excluded now, safe because no shipped path reads their absence. Parkable follow-up.

## Parking lot

- Proactive worker→console status **push** (worker emits status at phase
  boundaries unprompted, in addition to state events). Defaulted OUT: pull-only
  (console asks) is simpler and adds no worker-context noise. Revisit if the
  console feels blind between queries.
- Candidates #2 (dashboard nudge) and #4 (supervisor course-correction) as
  additional call sites of the `clu-<plan>-<phase>` addressing.
