# clu — orphaned heartbeat/worker processes + zombie `running` state files

> **Research-only handoff.** Authored 2026-05-31 from a live incident in a *consumer*
> repo (HealthData) where clu had been used. All diagnosis below was **run, not guessed** —
> evidence is real `ps`/state-file output captured on the host. Pick this up in
> `~/projects/end-of-line`; the fix is **not** started. Tracks **smabe/end-of-line#75**.
>
> **Citation honesty:** `SKILL.md` line numbers (installed vs repo) were verified by direct
> `grep` this session. `cli.py` / `registry.py` / `supervisor.py` / `state.py` / `dispatch.py`
> line numbers came from a code-reading research agent over `~/projects/end-of-line` this
> session — they're grounded but **`TODO: reconfirm line numbers on pickup`** since the tree moves.

## Goal
Make a *terminalized or unregistered* plan reliably (a) reap its live worker + heartbeat
subprocesses and (b) leave no state file stuck at `status=running`. Today both leak: a
finished plan can orphan a `clu heartbeat` loop for hours, and an unregistered plan can sit
at `status=running` on disk forever, invisible to every reaper.

## Diagnosis

Three distinct, independently-real defects. Two symptoms observed live; root causes traced to source.

### Defect A — orphaned heartbeat/worker processes outlive a finished plan
- **Hypothesis:** the worker's background `clu heartbeat` loop is not reaped when the worker
  exits abnormally (SIGKILL/crash/orphan), so it loops forever.
- **Falsifiable test (RUN):** `ps -eo pid,etime,command | grep "clu heartbeat"` on the host
  ~4.6h after a capture run that had already reached `status=done` + unregistered + archived.
- **Result — CONFIRMED:** 6 live processes for `penpot-cap-w1/w2/w4`, etime **04:38:xx**:
  2 worker `claude --print … /clu-phase … capture` (w1, w2) + 4 `while :; do clu heartbeat …; sleep 120; done` loops.
- **Root cause:** the **installed** skill is the *pre-#72* form. Verified:
  - Installed `~/.claude/skills/clu-phase/SKILL.md:105` → `( while :; do` (bare loop) + `:111` `trap "kill $HEARTBEAT_PID" EXIT` **only**.
  - Repo `end_of_line/skills/clu-phase/SKILL.md:109` → `( while kill -0 $WORKER_PID 2>/dev/null; do` (the #72 PID-tied guard) + `:124` EXIT trap.
  - The EXIT trap **does not fire on SIGKILL/OOM/abnormal exit** (uncatchable). With no `kill -0 $WORKER_PID` loop condition, the installed heartbeat had no second terminator → it ran forever after its worker died unclean.
- **So Defect A is partly deployment drift:** #72's fix exists in the repo but was never
  re-installed (`clu install-skill clu-phase`). clu has **no skill-version check** to warn the
  operator the installed copy is behind the bundle.
- **But even with #72 installed, a gap remains:** the supervisor-side backstop
  `_detect_dead_pid` (supervisor.py:628 — `TODO: reconfirm`) only runs **inside a tick**, and
  `tick-all` walks the **registry only** (cli.py:3496 / architecture.md:184 — `TODO: reconfirm`).
  An **unregistered** plan never ticks → its orphans are never reaped server-side. The worker's
  own `kill -0` loop becomes the *only* protection, so a stale skill = nothing reaps them.

### Defect B — `unregister`/`archive` leave a zombie `status=running` state file
- **Hypothesis:** a plan can be removed from the registry while its state file still says
  `running`, and nothing ever flips it terminal.
- **Falsifiable test (RUN):** inspect a week-old plan the operator believed they'd cleaned.
- **Result — CONFIRMED (`fm-docs-sweep`):** state file `status=running`, but `token` /
  `heartbeat_at` / `updated_at` / `current_claim` all **null**, last event
  `blocker_consumed` at **2026-05-23T23:54:50Z**, file mtime frozen at **May 23 19:54**
  (untouched since), **not in the registry**, no live process, no worktree on disk. A pure
  zombie — it never re-ran; it just never left `running`. (The operator "cleaned it
  yesterday"; the file proves nothing touched it — any state-file-scanning view re-surfaced
  the stale status.)
- **Root cause:**
  - `running` is **non-terminal** (state.py:115-120 — `TERMINAL_STATUSES = {paused, halted, halted_for_replan, done}`; `TODO: reconfirm`). Docs are explicit that `running` is strictly transient (contract.md:139, architecture.md:83-88, 132-134).
  - `unregister` (registry.py) is **status-blind** — removes the registry row, touches neither status nor the state file (cli.py:1864 — `TODO: reconfirm`).
  - Operator `clu archive` **refuses on RUNNING** (cli.py:5214) and doesn't terminalize; `--force` (added in #31) opts past the refusal but the *terminalize-on-cleanup* gap remains.
  - The **one documented self-heal** for "unregistered + running" is a crash-recovery window the architecture expects the **next tick to repair** (architecture.md:229-236). It **cannot fire**: tick-all walks the registry, and the plan is unregistered → never visited. The design relies on a repair path that is unreachable for this exact state.

### Defect C — no registry-independent reaper (the structural gap behind A+B)
- There is no lease-TTL sweep that reaps "`status=running` + heartbeat stale beyond TTL"
  **independent of registry membership**. `_detect_dead_pid` is tick/registry-bound. Both A's
  orphan-for-unregistered-plan and B's zombie escape it for the same reason: invisibility to
  the registry walk. A sweep over `.orchestrator/*.state.json` (TTL ≈ 3× the 120s heartbeat,
  per standard lease-reaper design) would catch both.

## Non-goals
- **Not** changing the worker-side heartbeat skill logic — #72 already fixed it in the repo;
  this plan only *redeploys* it + guards against drift. (Excluded safely: the repo skill is
  already correct; the bug is the *installed copy* + *server-side* reaping, a different layer.)
- **Not** re-litigating `clu archive` file-moving / `--drop-state` / `--force` — shipped in #31.
  This plan *uses* those, doesn't redesign them.
- **Not** touching the iMessage inbound poller, lease retry, or cross-plan rules.
- **Not** auto-killing processes the operator didn't authorize — reaping is scoped to a plan
  the operator is explicitly terminalizing/unregistering, or to a sweep gated behind a clear
  TTL + (optionally) a `--dry-run` default.

## Files to touch  *(line numbers `TODO: reconfirm on pickup` except SKILL.md, grep-verified)*
- `end_of_line/registry.py` — `unregister()`: terminalize status (e.g. → `halted`/new `abandoned`) and/or reap the plan's live processes before/after removing the row. (cli.py:1864 call site.)
- `end_of_line/cli.py` — `cmd_archive` (≈5210-5259), `cmd_unregister` (≈1864), `cmd_complete` (≈4264), `cmd_force_complete` (≈4344): add a best-effort **process reap** step (none reap today). `cmd_tick_all` (≈3495) or `cmd_doctor`: host the registry-independent zombie sweep.
- `end_of_line/dispatch.py` — worker spawned `start_new_session=True` (≈219): the child is its own process group → record the PGID on the claim so reapers can `os.killpg(pgid, SIGTERM→SIGKILL)`. (cmd template ≈170-176.)
- `end_of_line/supervisor.py` — `_detect_dead_pid` (≈628-670) / `_detect_stalled` (≈219-252): factor the liveness/TTL check into a form the new registry-independent sweep can call.
- `end_of_line/state.py` — `TERMINAL_STATUSES` (≈115-120): if adding an `abandoned` terminal status, define it here; add a helper to terminalize + stamp an `EVENT_PLAN_ABANDONED`.
- `~/.claude/skills/clu-phase/SKILL.md` (installed) — **redeploy** from bundle via `clu install-skill clu-phase`; this is the proximate fix for the live orphans.
- `end_of_line/cli.py` `cmd_doctor` (+ `install-skill`) — add an **installed-vs-bundled skill version check** so drift warns instead of silently shipping a pre-#72 heartbeat.
- `tests/` — see Done criteria.

## Failure modes to anticipate
- **`os.killpg` on the wrong group kills the CLI itself.** The worker must be in its *own*
  session (it is — `start_new_session=True`); target *that* PGID, never `getpgid(0)`.
- **PID reuse.** A recorded PID/PGID may belong to an unrelated process after a crash. Pair
  the PGID with a cmdline-marker check (`/clu-phase <plan> <phase> <token>`) before killing —
  `claim_worker_alive(cmdline_match=…)` already does this; reuse it.
- **`pkill -f` over-matching.** If reaping by marker instead of PGID, scope to the unique
  `token`, never a bare `clu`/`clu-phase` substring (would kill siblings + the grep).
- **TTL false-trips.** Heartbeat is 120s; a sweep TTL below ~3× (=6 min) will reap a merely-slow
  phase. Use the existing derived stalled threshold (`min(25, max(15, lease_ttl//2))`), not a new constant.
- **Terminalizing a genuinely-live plan.** The sweep must require BOTH `status=running` AND
  stale-heartbeat AND (PID dead OR unregistered) — not status alone (the fm-docs case had null
  heartbeat, which is the tell).
- **Idempotency / races with a concurrent tick.** A reap during archive can race the 30s
  `com.clu.tick`. Guard with the existing state lock; make the sweep a no-op on already-terminal plans.
- **Worktree-ahead retention is correct, don't "fix" it.** `archive` retaining a worktree with
  unpushed commits is by-design (don't lose work); terminalizing status must not imply deleting that worktree.

## Done criteria
- [ ] `clu unregister` (and `clu archive --force`) flips a `running` plan to a terminal status
  and emits an audit event — no state file left at `running` after either command.
- [ ] `clu archive` / `unregister` / `complete` / `force-complete` best-effort **reap** the
  plan's live worker + heartbeat process group (verified by a test that spawns a fake
  PGID-tagged sleeper and asserts it's gone post-command).
- [ ] A registry-independent sweep (in `tick-all` and/or `clu doctor`) detects a
  `status=running` + stale-heartbeat + unregistered state file, terminalizes it, and reaps any
  orphan — covering the exact `fm-docs-sweep` shape. Default `--dry-run` or clearly logged.
- [ ] `clu doctor` warns when the installed `clu-phase` skill is behind the bundled version
  (catches the pre-#72 drift that caused this incident).
- [ ] Tests: unregister-on-running terminalizes; archive reaps a tagged sleeper; sweep
  terminalizes+reaps a synthetic zombie; sweep is a no-op on a live/registered plan; doctor
  flags a stale installed skill.
- [ ] Docs: `architecture.md` note that the registry-independent sweep is the backstop for the
  "unregistered + running" window that tick-all can't reach (closes the gap named at architecture.md:229-236).

## Parking lot
(empty)

---

### Appendix — raw incident evidence (host: HealthData, 2026-05-31)
```
# orphans, 4h38m after plan done+unregistered+archived:
25798  04:38:44  claude --print … /clu-phase penpot-cap-w1 capture session-e4dc40… …w1.state.json
25938  04:38:43  claude --print … /clu-phase penpot-cap-w2 capture session-9ace41… …w2.state.json
27425  04:38:25  zsh -c … while :; do clu heartbeat --plan penpot-cap-w1 …; sleep 120; done
27594/28327      penpot-cap-w2 heartbeat wrapper + inner loop
27761  04:38:20  penpot-cap-w4 heartbeat loop
# all 6 killed by hand (SIGTERM sufficient).

# zombie state file:
fm-docs-sweep.state.json  status=running  token/heartbeat_at/updated_at/current_claim = null
  last event blocker_consumed 2026-05-23T23:54:50Z ; mtime May 23 19:54 ; not registered ; no worktree
```
Related: **#31** (archive file-moving + `--drop-state`/`--force`, shipped) · **#72**
(worker-PID-tied heartbeat, in repo, *not* in the installed skill at incident time).
