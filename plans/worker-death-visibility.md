# worker-death-visibility — stop workers dying at the gate, and make the death loud when it happens

On 2026-08-19 a HealthData worker (`verify-registry-356`) finished ~640 lines
of work, ran the project test gate in the background, armed a Monitor to wait
for the gate's terminal event, and ended its turn. Under `claude --print` there
is no next turn, so the process exited with the work staged and uncommitted.
The per-worker heartbeat daemon noticed within 120 seconds and wrote one line
to a sidecar log nobody reads. The operator found out 9.5 hours later, by
hand. Issues #106 (the idiom that kills the worker) and #104 (the detection
that goes nowhere) are the two halves of that morning.

This plan closes both. Phase 1 removes the reason a worker reaches for the
Monitor idiom at all — the Bash tool's ten-minute ceiling makes a long test
gate look un-runnable in the foreground — and then forbids the idiom in the
worker skill. Phase 2 gives the heartbeat daemon a voice: a token-validated
callback that writes the death into the state file, the inbox, and the notify
pipeline, so `clu watch` and the operator's channels see it inside two
minutes. Phase 3 lets the daemon release the claim it just reported dead, so
the phase is redispatchable by whichever supervisor tick runs next instead of
waiting for one to re-derive a death that was already established.

Ordering is forced: phase 1's ceiling must exist before the skill can tell a
worker to run a 30-minute gate in the foreground, and phase 2's dedup marker
must exist before phase 3 can release a claim without double-reporting.

## Diagnosis

- **Hypothesis:** the worker did not crash, run out of budget, or hit an API
  error. It ended its turn deliberately, to wait on an armed Monitor, and
  `claude --print` exits at end of turn.
- **Falsifiable test:** the dead attempt's own worker log is one line long. If
  the hypothesis holds, that line is the worker announcing the wait — not an
  error, not a truncation. And Claude Code's headless documentation must state
  that a `--print` run terminates after its final result regardless of pending
  background work.
- **Test result: CONFIRMED, both halves.** The attempt log
  (`plans/.orchestrator/logs/impl.session-7644e9a17b314137.log`) contains
  exactly `Monitor armed for scheme results. Waiting on the gate before
  committing.` — a deliberate wait, not a failure. And
  code.claude.com/docs/en/headless.md § "Background tasks at exit" states that
  a background shell started during a `claude -p` run "is terminated about five
  seconds after Claude has returned its final result and stdin has closed."
  code.claude.com/docs/en/tools-reference.md § "Background commands" adds: "In
  non-interactive mode with the `-p` flag, background tasks end shortly after
  the run's final result." No documented mechanism re-invokes a `--print`
  session after its turn ends.

## Assumptions — closed before drafting

Phase 1's correctness depends on how Claude Code behaves at a Bash timeout, in
a call pattern no existing clu code exercises. Both lines were closed by probe
this session, not by inference.

- **`BASH_MAX_TIMEOUT_MS` is read from the dispatched subprocess's inherited
  environment.** PROBE: a `claude --print` subprocess launched with
  `BASH_DEFAULT_TIMEOUT_MS=3000 BASH_MAX_TIMEOUT_MS=5000` in its environment,
  asked to run a 25-second `python3` command with `timeout=30000`, returned
  `Command did not complete within its 5s timeout`. The 5-second ceiling from
  the inherited env was applied to the request. A first probe setting only
  `BASH_MAX_TIMEOUT_MS=5000` did NOT discriminate — the command completed —
  because the effective ceiling is the *larger* of the two variables and the
  untouched 2-minute default still allowed the call; both must be set to move
  the ceiling down, and only `BASH_MAX_TIMEOUT_MS` needs setting to move it up.
- **A command that exceeds the ceiling is MOVED TO THE BACKGROUND, not
  killed.** PROBE, same run: `Command did not complete within its 5s timeout
  and was moved to the background (ID: bxcd92dz5). Output is being written
  to: ... You will be notified when it completes.` This is the single most
  important finding in the plan. The doc agrees
  (code.claude.com/docs/en/tools-reference.md § "Background commands": "When a
  command reaches its timeout without finishing, Claude Code moves it to the
  background instead of stopping it," with only `sleep`-prefixed,
  `git`-containing, and unparseable compound commands stopped instead) — and a
  project test gate is none of those three, so it backgrounds.

The consequence drives phase 1's design: a gate that overruns the ceiling does
not fail loudly. It hands the worker a background task ID and a promise of
notification — the exact shape that killed the incident worker, reachable
without anyone reading the word "Monitor".


## Locked design decisions

### Phase 1 — foreground-gates (#106)

- **The ceiling is the real cause, not the idiom.** Claude Code's Bash tool
  caps command timeouts at ten minutes out of the box
  (`BASH_MAX_TIMEOUT_MS`, code.claude.com/docs/en/tools-reference.md §
  "Timeout and output limits": the effective ceiling is the larger of
  `BASH_DEFAULT_TIMEOUT_MS` and `BASH_MAX_TIMEOUT_MS`, "ten minutes out of
  the box"). A worker facing a 20-minute suite cannot run it in the
  foreground, so forbidding the Monitor idiom without raising the ceiling
  just moves the failure. The ceiling comes first.
- **Set it in the worker subprocess env, not in worker-settings.json.**
  `build_worker_env` (`end_of_line/dispatch.py:120`) already merges
  `os.environ` and injects the `CLU_*` claim variables, and that route is
  proven in production by #91. Whether an `env` block inside a `--settings`
  file reaches the session is an open, unprobed question — it is the entire
  subject of issue #102 — so this plan does not depend on it.
- **New config field `dispatch.bash_max_timeout_ms`, default 1_800_000 (30
  minutes).** Added to `DispatchSpec` (`end_of_line/config.py:37-49`) and
  parsed at the `DispatchSpec(...)` construction site
  (`end_of_line/config.py:397-403`). The default is chosen against a two-sided
  invariant: **gate duration < ceiling < lease TTL.** The lower bound is what
  matters most — a gate that overruns the ceiling is backgrounded, not killed
  (see Assumptions), which is the failure this plan exists to prevent, so the
  ceiling must comfortably exceed the real gate. MEASURED: clu's own canonical
  gate (`python3 -m unittest discover -s tests`) runs in 90 seconds wall clock
  on this machine, so 30 minutes is ~20x headroom here; a project with a
  heavier gate raises the field. The upper bound keeps the ceiling under the
  60-minute default lease TTL (`state.py:107`) so the Bash timeout is reached
  before the lease expires, leaving the worker alive to report rather than
  reaped after the fact.
- **An operator who already exports `BASH_MAX_TIMEOUT_MS` wins**, via
  `setdefault` rather than assignment. This is a deliberate departure, not a
  local convention: `build_worker_env` assigns `PATH` and every `CLU_*`
  variable unconditionally (`dispatch.py:148-155`), and there is no existing
  `setdefault` precedent in the function. The departure is justified because
  `PATH` and the `CLU_*` values are clu's own claim identity, which the
  operator has no business overriding, whereas the Bash ceiling is a host
  tuning knob an operator may legitimately have set for reasons clu cannot
  see.
- **The `setdefault` goes INSIDE the existing `inject` branch**, never on the
  unconditional path. `build_worker_env` returning `None` versus a dict is
  load-bearing in two opposite directions: `cmd_doctor` renders `None` as its
  "(source: inherited)" display (`cli.py:2656`), and `dispatch_repair_worker`
  (`dispatch.py:444`) uses `None` to let the repair subprocess inherit rather
  than receive a frozen copy of `os.environ`. Setting the ceiling on the
  unconditional path would silently flip both for every project without a
  `dispatch.path` override, and neither would fail a test. A consequence
  accepted deliberately: repair workers, which pass no claim kwargs, get no
  ceiling.
- **The skill forbids the whole family, not just Monitor.** The prohibition
  covers every "start it, end the turn, get woken up" shape — Monitor waits,
  `run_in_background` Bash followed by turn end, and any scheduled wakeup —
  because they share one failure: `--print` has no next turn. The positive
  instruction that replaces it is a foreground Bash call with an explicit
  `timeout` argument.
- **The auto-background result is named explicitly as a trap.** A worker can
  reach the fatal shape without ever choosing it: overrun the ceiling, get
  back "moved to the background ... You will be notified when it completes",
  believe it, and end the turn. The skill must state that a backgrounded
  result is NOT a result, and that the worker re-runs with a longer explicit
  timeout or calls `clu block` — never waits.
- **The prohibition lands in `## Common pitfalls`, beside the existing
  hardened-dispatch denial list** (`end_of_line/skills/clu-phase/SKILL.md`,
  the "Command shapes that get DENIED under hardened dispatch" bullet at the
  section head), and is cross-referenced from the full-suite mandate at line
  204. Those are the two places a worker is already reading when it decides
  how to run the gate.
- **Doc-level, because the incident host is not allowlist-protected.**
  HealthData dispatches with `--permission-mode bypassPermissions` and no
  `--allowedTools` at all, so every tool including Monitor is available;
  clu's own hardened example allowlists a fixed tool set that happens to
  exclude Monitor (`examples/hardened.orchestrator.json:5`, the `command`
  field). A config-level fix protects only the hardened hosts, which is not
  where the incident happened. Note that nothing in clu copies that example —
  `clu init` writes `~/.config/clu/worker-settings.json` from a bundled
  template and prints a recipe; the `.orchestrator.json` example is
  hand-copied prose (`README.md:134`, `docs/operations.md:681`). A fresh
  install therefore gets the code default, which is why the default matters
  more than the example.

### Phase 2 — death-report (#104, surfacing half)

- **The daemon reports through a token-validated CLI callback, never by
  writing the event itself.** New `clu notify-worker-dead`, modelled on
  `cmd_notify_heartbeat_failure` (`end_of_line/cli.py:5991-6033`) — the same
  `@_translate_claim_mismatch` → `st.mutate` → `st.assert_claim_match` →
  idempotency marker → `append_event` → best-effort `inbox.write_event` →
  `notify.notify` shape. `append_event` does no claim validation on its own,
  so a direct daemon write would be the project's first unvalidated state
  mutation from a worker-side process.
- **The daemon calls it in-process via `cli.main`**, exactly as the existing
  strike path already calls `notify-heartbeat-failure`
  (`end_of_line/heartbeat_daemon.py:66-88`). No PATH dependency, no new
  allowlist entry.
- **The daemon's lock acquisition must be bounded.** `st.mutate` →
  `locked_json` (`state.py:553-568`) calls `locked(path)` with no timeout, and
  `locked` blocks indefinitely when `timeout_seconds is None`
  (`state.py:515-535`). The daemon is `setsid`-detached and deliberately
  outside every reaper (`heartbeat_daemon.py:17-23`), so a blocking write on
  its exit path creates an unkillable process waiting forever on a lock. This
  phase threads `timeout_seconds` through `locked_json` and `mutate` — the
  parameter already exists on `locked` — and the death callback passes a
  bounded budget, degrading to a log line on `LockTimeout` rather than
  hanging.
- **The daemon's liveness probe becomes cmdline-anchored.** Today it is a
  bare `os.kill(pid, 0)` (`heartbeat_daemon.py:46-54`) while the supervisor
  requires the plan slug as a whole token in the process cmdline
  (`state.claim_worker_alive`, `state.py:312-351`, the #76 fix). Under PID
  reuse the bare probe reports a live worker that is somebody else's process
  — harmless when the only consequence was a silent exit, wrong the moment it
  sends the operator a death notification. The daemon reuses
  `claim_worker_alive` with `cmdline_match=<plan slug>`.
- **New event `EVENT_PHASE_WORKER_DEAD_REPORTED`, distinct from the
  supervisor's `EVENT_PHASE_WORKER_DEAD`.** They are different observations
  by different processes with different evidence, and collapsing them would
  make the state file lie about who saw what. The new event carries the
  reporter, the phase, the pid, and the worker log path.
- **A dedup marker on the claim is mandatory, not optional.**
  `EVENT_PHASE_WORKER_DEAD` has no marker today because its idempotency is
  structural — one writer, which releases the claim in the same lock window
  (`supervisor.py:727-737`). Adding a second writer destroys that guarantee:
  without a marker the operator gets two notifications, two inbox files
  (`inbox.write_event` has no dedup — one file per call), two `clu watch`
  lines, and two task-list updates. `mark_worker_death_reported` /
  `worker_death_already_reported` follow the `mark_worker_idle_emitted`
  pattern (`state.py:984-987`), and the supervisor's worker-dead branch
  (`supervisor.py:695-760`) checks it before notifying.
- **The event is DEFAULT-visible in `clu watch`, not verbose-only.** Its
  closest sibling `EVENT_HEARTBEAT_LOOP_FAILING` sits in `_VERBOSE_ONLY`
  (`watch.py:55-69`), so this is a deliberate departure: #104's complaint is
  precisely that two live watch streams received zero lines, and an event only
  a `--verbose` reader sees would reproduce that.
- **Watch registration is three places or the event is invisible** — even in
  `--json` mode, because `watch.project_event` returns None for unmapped
  types and the projection gates the JSON emit (`watch.py:504-512, 546`). The
  event goes into the default visible set, `_OPERATOR_VISIBLE`, and
  `_FORMATTERS`; `tests/test_watch_operator_filter.py:253-262` already fails
  on an operator-visible event with no formatter, so the guard exists.
- **Quiet hours: no bypass.** `QUIET_HOURS_BYPASS_KINDS` is a four-member set
  reserved for halt-equivalent states with no self-healing path
  (`notify.py:81-88`). Once phase 3 lands a dead worker self-heals, so it does
  not meet that bar. Between phase 2 shipping the kind and phase 3 shipping
  the recovery the justification is briefly ahead of the code — the phases run
  sequentially in one worktree, so that window is minutes, not a release. The
  overnight surface is the inbox entry, which reaches the operator on their
  next Claude turn. **This is the plan's one operator decision — see the
  approval note.**
- **The inbox entry gets a wedge-instruction tuple** in
  `hooks/clu_inbox_surface.py:117-123`, joining `tool_stuck`,
  `attestation_refused`, and `stalled_claim`, so the surfaced entry carries
  what to do rather than just what happened.

### Phase 3 — death-recovery (#104, recovery half)

- **The daemon releases the claim it just reported dead.** Detection,
  surfacing, and recovery all sit with the supervisor tick today; this plan
  moves the first two to the daemon in phase 2. Leaving recovery behind would
  mean the operator learns of the death in two minutes and then watches an
  unreleased claim sit until a tick arrives — the same tick that was absent
  for eight hours in the incident. The manual step the operator actually
  performed at 11:42 was `clu release-claim`, after which redispatch happened
  within 40 seconds; this phase performs that step automatically.
- **Release is token-validated.** `release_claim(data, expected_token,
  expected_phase)` (`state.py:794-813`) — the unvalidated two-argument-free
  form is documented as supervisor-only. The daemon holds the token, so it
  proves ownership.
- **Quota classification must happen BEFORE release, in the same lock
  window.** `quota.classify_log_tail` reads `claim["log_path"]`, and release
  clears the claim that carries it (`supervisor.py:701-706`). A daemon that
  released first would silently kill the #94 quota pause and the
  `EVENT_QUOTA_DEATH` attempt forgiveness, so the phase burns an attempt
  toward a max-attempts halt. The daemon runs the same classification the
  supervisor runs, in the same order.
- **Coolant goes through `release_claim_and_emit`**
  (`state.py:815-838`), which snapshots `phase_id` and `claimed_by` before
  the release wipes them — matching the supervisor's own call at
  `supervisor.py:733-737`.
- **The daemon does not reap the process group.** Its own `os.setsid` puts it
  outside the group it would be killing, the worker PID is already gone by
  definition, and reaping is documented as best-effort even in the supervisor
  (`supervisor.py:723-731`). The supervisor's existing reap remains the
  backstop for the orphaned heartbeat-keeper case.
- **This does not fix "no tick ran for eight hours."** Releasing the claim
  makes the phase *redispatchable*; something still has to dispatch it, and
  that is a tick. Issue #103 owns why ticks were absent. Stated plainly here
  so the plan is not read as closing a hole it only narrows.

## Non-goals

- **Issue #103 (why no supervisor tick reaped the expired lease for eight
  hours) is out of scope.** The exclusion is safe because this plan removes
  nothing #103 depends on and adds a strictly independent detection path: the
  daemon's report is defense in depth alongside the tick, not a replacement
  for it, and the supervisor's own worker-dead and lease-expiry branches are
  left intact and reachable. #103's investigation needs the incident host's
  cron and LaunchAgent logs, which are not in this repository. Note that
  #103's premise needs correcting first — `clu serve` is read-only
  (`cmd_serve`, `cli.py:4209-4234`, and `webserver.py` references neither the
  supervisor nor any tick), so serve never reaping a lease is by design, not
  the bug.
- **Issue #105 is out of scope.** Read its correcting comment, not its
  title: the title says worker transcripts are "not retained" and the body
  asks dispatch to start capturing stdout, but the author's own follow-up
  comment retracts that — the per-attempt logs already exist at
  `plans/.orchestrator/logs/<phase>.<session>.log` (verified on disk,
  including the incident's own 73-byte file), and the real bug is that
  `clu logs` surfaces only the heartbeat sidecar. The exclusion is safe
  because what remains is a read-side presentation fix over files this plan
  neither writes nor relocates, so nothing here changes what a later
  `clu logs` fix would find. Phase 2 does make one of those files more
  useful by carrying its path into the death event.
- **Issue #102 (`CLAUDE_CODE_ENABLE_TODO_TOOLS` in the worker-settings
  template) is out of scope**, and deliberately so: phase 1 routes its env
  var through `build_worker_env` precisely to avoid depending on #102's
  unprobed question about whether a `--settings` env block reaches the
  session. The two are independent by construction.
- **No deferred-notification queue.** A push notification suppressed by quiet
  hours is dropped, not replayed (`notify.py:156`). The exclusion is safe
  because the information is not lost with it: `notify.notify` writes the
  inbox event BEFORE any suppression check, and its docstring states the
  reason outright — "also drops an inbox event so the next Claude turn sees
  the same signal — independent of quiet hours, since the inbox is for
  in-session pickup, not waking the operator" (`notify.py:129-133`). An
  overnight death therefore reaches the operator at their next session rather
  than never. Building store-and-forward push is a new mechanism with its own
  delivery semantics and is not what either issue asks for.
- **No extraction of a shared worker-callback helper.** An earlier reading of
  this codebase called the shape `token assert → dedup marker → append_event
  → inbox.write_event → notify` a six-site rule-of-three violation. Checked
  against source, that is wrong: only `cmd_notify_heartbeat_failure`
  (`cli.py:6004-6032`) carries the whole shape. `cli.py:4686-4720` sits in
  `_write_attestation_refused_inbox` (def `:4678`), an inbox-only helper with
  no token assert, no dedup marker, no `append_event` and no notify; the four
  `supervisor.py` sites (`:291, :353, :444, :582`) are tick-internal
  emissions with no token to assert. The exclusion is therefore safe on the
  project's own terms: phase 2 adds the SECOND instance of the full shape,
  not a seventh, which is below the rule of three, and the two families
  change for different reasons — one is a token-validated worker callback,
  the other is supervisor tick logic — so merging them would need a mode flag,
  which the project bans. What IS uniformly wrong across all six is the raw
  string literal passed as the inbox `type=`, against the EVENT-constants
  rule. That narrow cleanup is the follow-up worth filing.
- **Not migrating HealthData to hardened dispatch.** Its
  `bypassPermissions` template is another project's configuration and belongs
  to the #90 family. The exclusion is safe because phase 1 is deliberately
  doc-level and env-level, both of which apply identically under
  `bypassPermissions` and under a hardened allowlist — so the un-migrated
  host gets exactly the same protection as a migrated one, and no asymmetry
  is created by leaving it alone.

## Files touched

- `end_of_line/config.py` — P1 modified — `DispatchSpec.bash_max_timeout_ms` field + parse + a shared non-negative-int validator. **API hotspot:** new `DispatchSpec` field.
- `end_of_line/dispatch.py` — P1, P3 modified — P1: `build_worker_env` sets `BASH_MAX_TIMEOUT_MS` inside the inject branch. P3: a `_TERMINATION_REASONS` entry for the new event so the redispatched attempt is told why the last one died. **API hotspot:** the `None`-versus-dict return contract, relied on by `cmd_doctor` and `dispatch_repair_worker`.
- `end_of_line/cli.py` — P1 modified (`cmd_doctor` docstring), P2, P3 modified — new `notify-worker-dead` subcommand in P2, extended in P3. **API hotspot:** new subcommand name + `--token` contract; parser block and `dispatchers` dict.
- `end_of_line/skills/clu-phase/SKILL.md` — P1 modified — end-of-turn-wait prohibition, the auto-background trap, foreground-gate recipe.
- `end_of_line/state.py` — P2 modified — new event constant, `mark_worker_death_reported` / `worker_death_already_reported`, `timeout_seconds` threaded through `locked_json` + `mutate`. **API hotspot:** `state.mutate` gains an optional keyword; all existing callers unchanged.
- `end_of_line/heartbeat_daemon.py` — P2 modified — cmdline-anchored liveness, death callback on the worker-dead exit.
- `end_of_line/notify.py` — P2 modified — `KIND_WORKER_DEAD_REPORTED` + `render_worker_dead_reported`. **API hotspot:** notify kind set.
- `end_of_line/watch.py` — P2 modified — default-visible set, `_OPERATOR_VISIBLE`, `_FORMATTERS`, `_TASK_STATUS_MAP`.
- `end_of_line/supervisor.py` — P2 modified — worker-dead branch consults the dedup marker before notifying.
- `end_of_line/hooks/clu_inbox_surface.py` — P2 modified — wedge-instruction tuple for the new inbox type.
- `docs/contract.md` — P2, P3 modified — events-table row + semantics paragraph; P3 updates it to say the reporter releases the claim.
- `docs/operations.md` — P1, P2, P3 modified — the timeout field and its two-sided invariant, the new death surface, the recovery behavior and its limit.
- `docs/reference.md` — P1, P2, P3 modified — module surfaces for the changed functions.
- `tests/test_doctor.py` — P1 modified — the only current importer of `build_worker_env`; already pins the "(source: inherited)" display at `:140-145`, which the injection must not break.
- `tests/test_dispatch.py` — P1 modified — home of the existing `CLU_*` env-injection tests; the ceiling tests belong beside them.
- `tests/test_config.py` — P1 modified — validation of the new field.
- `tests/test_heartbeat_daemon.py` — P2 modified — death callback fires once, lock timeout degrades cleanly, cmdline probe.
- `tests/test_notify_worker_dead.py` — P2 NEW, P3 modified — the callback's token, idempotency, event, inbox, notify; P3 adds release + quota ordering.
- `tests/test_supervisor_worker_dead_dedup.py` — P2 NEW, P3 modified — supervisor honors the marker; P3 adds the claimless-tick no-op.
- `tests/test_watch_operator_filter.py` — P2 modified — new event registered.
- `tests/test_state.py` — P2 modified — marker helpers + `mutate` timeout.
- `examples/hardened.orchestrator.json` — **NOT touched.** Pinning the default into the example would freeze it for every install that hand-copies the file, and the example already omits fields that have useful code defaults. The field is documented in `docs/operations.md` instead.

## Per-phase done checklist

- TDD: failing tests first.
- `/code-review` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood /
  Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- **Stamp attestations AFTER the commit.**
  - `clu verify --plan worker-death-visibility --phase <id> --token <T>`
  - `clu attest --simplify --plan worker-death-visibility --phase <id> --token <T>`
- Call `clu complete --plan worker-death-visibility --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| foreground-gates | `worker-death-visibility-foreground-gates.md` | Raise the worker Bash-timeout ceiling, then forbid the end-of-turn wait idiom in `/clu-phase` (closes #106) | 2h |
| death-report | `worker-death-visibility-death-report.md` | Heartbeat daemon reports worker death through a token-validated callback — event, inbox, notify, watch | 3h |
| death-recovery | `worker-death-visibility-death-recovery.md` | Daemon releases the claim it reported dead, quota-classifying first, so the phase is redispatchable (closes #104) | 2h |

## Verification record

- grounding: 64 claims checked, 16 fixed (4 wrong, 12 imprecise citations), 0 promoted, 1 refuted; 2 uncheckable claims closed by probe, 2 stand as the operator's account of the incident
- executability: 14 acceptance items across 3 sub-plans, 32 Read-first pointers, 21 Files-touched entries and 6 Non-goals checked; 9 fixed (7 phase-tag or test-path errors, 2 missing exclusion rationales), 0 promoted
- coherence: 11 stated rules, 7 characterizations and 6 cross-file restatements checked; 1 contradiction fixed, 1 weakened rationale rewritten after checking the source
- prober (foreground-gates): files LISTED 8 / MISSING 0; no workarounds; suite green at 1975 tests (+7); 3 SKETCH-class corrections and 3 old-behavior losses folded back into the drafts
- assumptions: 2 external-behavior lines closed by PROBE with observed output (see `## Assumptions`), 0 left `[unverified]`

## Findings log

- **2026-08-19 (foreground-gates):** running `python3 -m unittest discover -s
  tests` directly from a worker's sandboxed Bash tool leaves ~45 tests red that
  are purely environmental, NOT regressions: all of `test_webserver` (~36, fail
  in `server_bind()` — the Seatbelt sandbox blocks socket binding) plus
  `test_reap_orphan_pgroup` / `test_terminalize` / `test_zombie_sweep` (~9, fail
  because the sandbox blocks cross-process-group signals, so `result.signaled`
  is `None`). `clu verify` is authoritative — it runs the gate sandbox-exempt
  (the #90 `clu *` exemption) where these pass. Phase 2 touches the heartbeat
  daemon and supervisor tests; if you see this exact red cluster, don't chase
  it — confirm the failing modules are only those four and trust `clu verify`.
