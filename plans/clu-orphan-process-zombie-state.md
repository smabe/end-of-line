# clu — orphaned heartbeat/worker processes + zombie `running` state files

## Goal
Make a terminalized or unregistered plan reliably (a) reap its live worker +
heartbeat process group and (b) leave no state file stuck at `status=running`.
Add a registry-independent sweep that backstops the unregistered-while-running
window the registry-walking tick can never reach, plus a `doctor` guard against
the installed-skill drift that caused the live incident. Tracks
**smabe/end-of-line#75**.

## Diagnosis
- **Hypothesis:** nothing reaps a plan that has left the registry or finished —
  neither its processes nor its `running` state file — because the only reaper
  (`_detect_dead_pid`) runs inside a tick and `tick-all` walks the **registry
  only**.
- **Falsifiable tests (RUN this session):**
  - Code anchors reconfirmed (handoff said "reconfirm on pickup"):
    `TERMINAL_STATUSES` excludes `running` (`state.py:120`); `unregister` is
    status-blind, pure row removal (`registry.py:113-125`); claim stores `pid`,
    no `pgid` (`dispatch.py:638`); worker spawned `start_new_session=True`
    (`dispatch.py:219`); `reap_orphan_pid` is single-PID via `os.kill`, no
    `killpg` (`state.py:315-350`); `install-skill` copies bundle→installed with
    **no version/hash compare** (`cli.py:2119-2181`).
  - **Two handoff claims DISPROVEN:** (1) `clu archive --force`/`--drop-state`
    do **not** exist — `cmd_archive` hard-refuses RUNNING at `cli.py:5227` and
    has only `plan`/`project` args (`cli.py:436-453`); the `--force` flags live
    on unregister/install-skill/release-claim. (2) `cmd_complete` never sets
    `status=done` — it releases the claim + emits `phase_completed`
    (`cli.py:4263-4287`); the **next tick** (supervisor priority #9) flips
    status. So a plan unregistered before that tick stays `running` forever.
- **Process-group result (verified):** with `start_new_session=True`, **PGID ==
  worker PID** (stdlib agent, doc-grounded); the group persists while any member
  is alive even after the leader dies; `getpgid()` must NOT be used to recover
  the pgid (raises `ProcessLookupError` the instant the leader exits). **Claude
  Code's Bash tool does not re-group its commands** (claude-code-guide,
  medium-high confidence from CC issues #43944/#25188/#16135 — *not* official
  docs): the backgrounded heartbeat subshell inherits the `claude` worker's
  PGID, and `$PPID` inside a Bash command is the `claude` PID itself (so
  `WORKER_PID=$PPID` == `claim["pid"]`). **Therefore `os.killpg(claim["pid"],
  SIG)` reaps worker + heartbeat atomically.**
- **Why `killpg`, not the existing single-PID `os.kill`:** `os.kill(worker_pid)`
  alone kills only `claude`; the heartbeat then **reparents to launchd and keeps
  running** — *exactly the 4h38m orphan in the incident*. `killpg` closes that
  reparent-and-linger window. CC #16135 notes `killpg` also kills `claude` —
  here that is **intended** (we reap a done/terminalized plan), and the
  `pgid != os.getpgid(0)` guard protects the `clu` reaper itself (different
  session).

## Non-goals
- **Not** changing worker-side heartbeat skill logic — #72 already fixed it in
  `end_of_line/skills/clu-phase/SKILL.md:109` (`while kill -0 $WORKER_PID`).
  *Safe to exclude:* the repo skill is already correct and the installed copy on
  this host was re-installed today (16:02, now byte-matches the bundle); the
  remaining bug is server-side reaping + drift detection, a different layer.
- **Not** adding terminalize to `cmd_archive`. *Safe to exclude:* archive
  deliberately refuses RUNNING and directs the operator to `halt`/`pause` first,
  so by the time archive runs the status is already terminal — adding a
  terminalizing `--force` would duplicate `halt`'s job and erode a correct
  safety guard. Terminalization routes through `unregister` / `force-complete` /
  the sweep instead.
- **Not** adding a new `abandoned` status. *Safe to exclude:* no consumer needs
  to distinguish operator-halt from reaped-zombie at the *status* level; the new
  `EVENT_PLAN_ABANDONED` audit event carries that distinction, so terminalize
  reuses the existing `halted` status and avoids touching every status switch in
  notify/watch/supervisor.
- **Not** auto-killing processes the operator didn't authorize — reaping is
  scoped to a plan the operator is explicitly terminalizing/unregistering, or to
  the sweep gated behind a stale-heartbeat TTL + dead-PID/unregistered check.
- **Not** deleting worktrees on terminalize — worktree-ahead retention (unpushed
  commits) is by-design; reaping kills processes, never worktrees.
- **Not** touching the iMessage poller, lease retry, or cross-plan rules.

## Files to touch
**Phase 1 — group-reap primitive**
- `end_of_line/state.py` — new `reap_plan_processes(claim, ...)` (or
  `reap_orphan_pgroup(pgid, cmdline_match)`): `os.killpg(pgid, SIGTERM)` →
  poll → `SIGKILL`, guarded `pgid > 0 and pgid != os.getpgid(0)`, cmdline-marker
  check before signaling (reuse the `ps -p` pattern from `reap_orphan_pid`
  `state.py:315-350`), `ProcessLookupError`→success / `PermissionError`→surface.
  Plus a token-scoped straggler sweep for any `clu heartbeat … <token>` loop —
  **backstop only** (verification shows the heartbeat shares the worker's pgroup,
  so `killpg` already gets it; the sweep covers the medium-confidence inference
  + any reparent-lingering edge). Unique token → no over-match.
- `end_of_line/dispatch.py` — `_stamp_pid` (`:632-642`): also record
  `claim["pgid"] = proc.pid` with a one-line comment citing the
  `start_new_session` pid==pgid invariant (cheap, removes the coupling to that
  invariant at every reap site).

**Phase 2 — terminalize + wire cleanup commands**
- `end_of_line/state.py` — new `EVENT_PLAN_ABANDONED` constant; new
  `terminalize(data, *, status=STATUS_HALTED, event=EVENT_PLAN_ABANDONED)`
  helper: CAS-gated (no-op if `data["status"]` already terminal), flips status +
  appends the audit event, under the caller's `mutate` lock.
- `end_of_line/cli.py` — `cmd_unregister_one` (`:1851`): if the plan's state
  file is `running`, terminalize + reap the claim's process group **before**
  removing the registry row. `cmd_force_complete` (`:4294`) and `cmd_complete`
  (`:4161`): add a best-effort process reap after releasing the claim.

**Phase 3 — registry-independent zombie sweep**
- `end_of_line/cli.py` — host the sweep in BOTH (decision locked):
  - `cmd_tick_all` (`:3495`, after the registry loop) — runs the sweep
    **automatically** so zombies self-heal unattended; terminalize + reap inline.
  - `cmd_doctor` (`:2403`) — **reports** the sweep with a `--dry-run` preview so
    the operator can see what would be reaped without acting.
  - Predicate (shared): `status == running` AND stale-heartbeat (null
    `heartbeat_at` OR older than the derived threshold) AND (claim PID dead OR
    plan not in registry). No-op on live/registered/already-terminal. Factor the
    sweep into one helper both call sites invoke (DRY — single source of truth).
- `end_of_line/state.py` — reuse `stalled_threshold_for_phase` (`:539-556`,
  `max(15, lease_ttl//2)` cap 25 min) for the TTL; factor a tiny
  `is_zombie_state(data, registered: bool)` predicate the sweep calls.

**Phase 4 — skill-drift guard**
- `end_of_line/cli.py` — `cmd_doctor` (`:2403`): SHA-256 compare each installed
  `~/.claude/skills/<name>/SKILL.md` against the bundled
  `end_of_line/skills/<name>/SKILL.md`; warn on mismatch with the remediation
  (`clu install-skill <name>`). Hash, not mtime (mtime false-positives on every
  reinstall).

**Phase 5 — docs**
- `docs/architecture.md` — extend the **"Crash recovery" paragraph (`:229-237`,
  reconfirmed)**: that self-heal relies on the queue head still being present so
  the next tick re-enters; it structurally cannot reach an unregistered zombie
  with no queue head (the `fm-docs-sweep` shape). Note the sweep as that
  backstop. Touch `docs/reference.md` / `contract.md` for the new event/helper.

**Tests (per phase)** — `tests/` on `GitProjectTestCase` (`tests/__init__.py:164`).

## Failure modes to anticipate
- **`killpg(getpgid(0))` kills `clu` itself.** Assert `pgid > 0 and pgid !=
  os.getpgid(0)` before every signal; never reconstruct pgid via `getpgid` (dies
  when leader exits) — use the stored `claim["pgid"]`.
- **PID/PGID reuse.** A recorded pgid can alias a recycled unrelated group.
  Cmdline-marker check (the unique `token` / `/clu-phase <plan> <phase>`) before
  `killpg`, mirroring `reap_orphan_pid`'s guard.
- **Heartbeat in a separate session.** Verified the heartbeat shares the
  worker's pgroup (CC Bash tool doesn't re-group; medium-high confidence from CC
  issues, not docs), so `killpg(worker_pgid)` gets it. If that inference is ever
  wrong, the token-scoped straggler sweep + #72's worker-side `kill -0` are the
  backstops. Confidence isn't doc-certain → keep the straggler sweep.
- **TTL false-trips.** Heartbeat is 120s; a sweep TTL below ~3× reaps a slow
  phase. Use the existing derived threshold, never a new constant; require
  stale-heartbeat AND dead-PID/unregistered, never status alone.
- **Sweep racing the 30s `com.clu.tick`.** Terminalize under the `mutate` lock;
  CAS no-op on already-terminal makes it idempotent.
- **`ProcessLookupError` mid-escalation** = group already drained → treat as
  success, not error.

## Done criteria
- [ ] `clu unregister` on a `running` plan flips it to a terminal status, emits
  `plan_abandoned`, and reaps its process group before removing the row — no
  state file left at `running`. (Test: synthetic running plan + tagged sleeper.)
- [ ] `clu complete` / `force-complete` best-effort reap the plan's worker +
  heartbeat group (test: PGID-tagged sleeper gone post-command).
- [ ] Registry-independent sweep terminalizes + reaps a synthetic `fm-docs-sweep`
  zombie (running + null heartbeat + unregistered); no-op on a live/registered
  plan and on an already-terminal plan.
- [ ] `clu doctor` warns when an installed skill's SHA-256 differs from the
  bundle; silent when in sync.
- [ ] Tests for each of the above, all green; full suite passes (report count).
- [ ] `docs/architecture.md` documents the sweep as the backstop for the
  unregistered-while-running window.

## Parking lot
(empty)

---

### Appendix — raw incident evidence (host: HealthData, 2026-05-31)
```
# orphans, 4h38m after plan done+unregistered+archived:
25798  04:38:44  claude --print … /clu-phase penpot-cap-w1 capture …w1.state.json
25938  04:38:43  claude --print … /clu-phase penpot-cap-w2 capture …w2.state.json
27425  04:38:25  zsh -c … while :; do clu heartbeat --plan penpot-cap-w1 …; sleep 120; done
27594/28327      penpot-cap-w2 heartbeat wrapper + inner loop
27761  04:38:20  penpot-cap-w4 heartbeat loop
# all 6 killed by hand (SIGTERM sufficient).

# zombie state file:
fm-docs-sweep.state.json  status=running  token/heartbeat_at/updated_at/current_claim = null
  last event blocker_consumed 2026-05-23T23:54:50Z ; mtime May 23 19:54 ; not registered ; no worktree
```
Related: **#31** (archive plan-move — note: did NOT add `archive --force`) ·
**#72** (worker-PID-tied heartbeat, in repo + now installed on this host).
