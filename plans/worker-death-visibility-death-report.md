# worker-death-visibility-death-report — give the heartbeat daemon a voice

You are phase `death-report` of the `worker-death-visibility` plan. The
per-worker heartbeat daemon already detects a dead worker within 120 seconds
and throws the knowledge away. You turn that detection into an event in the
state file, an inbox entry, an operator notification, and a `clu watch` line —
through the project's existing token-validated worker-callback contract. One
commit. You do NOT release the claim; that is phase `death-recovery`.

## Locked decisions (do NOT re-litigate)

See `plans/worker-death-visibility.md`. Summary:

- The daemon reports through a NEW `clu notify-worker-dead` CLI callback,
  never by calling `append_event` itself — `append_event` does no claim
  validation, and the token is the project's entire security boundary.
- The daemon invokes it in-process via `cli.main`, exactly as the existing
  3-strike path invokes `notify-heartbeat-failure`.
- `st.mutate` must gain an optional bounded lock timeout, and the daemon must
  use it. The daemon is `setsid`-detached and outside every reaper, so an
  indefinite `flock` wait on its exit path creates an unkillable process.
- The daemon's liveness probe becomes cmdline-anchored via
  `state.claim_worker_alive`, because a bare `kill(0)` false-positives on PID
  reuse and this phase turns that into an operator notification.
- New event `EVENT_PHASE_WORKER_DEAD_REPORTED`, distinct from the supervisor's
  `EVENT_PHASE_WORKER_DEAD` — different processes, different evidence.
- A dedup marker on the claim is mandatory. Without it the operator gets two
  notifications, two inbox files, two watch lines and two task-list updates.
- Quiet hours: NO bypass. The four-member bypass set is for halt-equivalent
  states with no self-healing path.

## Read first

- `plans/worker-death-visibility.md` `## Findings log` — phase `foreground-gates` ran before you; read what it recorded.
- `end_of_line/heartbeat_daemon.py` — the whole module, it is short. The module docstring at lines 17-23 explains why the daemon is outside every reaper; that constraint drives the bounded-lock decision.
- `end_of_line/heartbeat_daemon.py:63-83` — `_notify_failure`, the in-process `cli.main` call. Your death report mirrors this exactly.
- `end_of_line/heartbeat_daemon.py:86-107` — `tick_once`. Its `pid_alive` / `ping` injection seams are declared in the signature at `:92-93`; they are how the tests drive it, so preserve them.
- `end_of_line/cli.py:5991-6032` — `cmd_notify_heartbeat_failure`. This is the canonical worker-callback shape: `@_translate_claim_mismatch`, `st.mutate`, `st.assert_claim_match`, `mark_*` early return, `append_event`, body built inside the lock window, best-effort `inbox.write_event` in `try/except OSError`, `notify.notify`, one-line receipt, `ExitCode.OK`.
- `end_of_line/cli.py:1101-1113` — the argparse block for `notify-heartbeat-failure`, including `add_common(p)` and `--token required=True`.
- `end_of_line/cli.py:1486-1508` — the `dispatchers` dict, indexed at `:1509`. A subcommand registered in the parser but missing here raises a `KeyError` traceback at that line — loud, but only once something actually invokes it.
- `end_of_line/state.py:497-547` — `LockTimeout` and `locked`. `locked` ALREADY takes `timeout_seconds`; `locked_json` (def `:553`, its `with locked(path):` at `:571`) and `mutate` (`:578-585`) do not forward it. That is the gap you close.
- `end_of_line/state.py:300-351` — `_cmdline_marker_present` (`:300-309`) and `claim_worker_alive` (`:312-351`). Note the deliberate fail-open behaviors: PID `None` → alive, EPERM → alive, `ps` failure → alive.
- `end_of_line/state.py:978-987` — `worker_idle_already_emitted` / `mark_worker_idle_emitted`. Copy this pattern for the death marker.
- `end_of_line/state.py:240-259` — the event-constant block, with the comment convention: emitter, dedup rule, and every field named above the constant.
- `end_of_line/supervisor.py:696-757` — the supervisor's own worker-dead branch. You add the marker check here.
- `end_of_line/notify.py:46-88` — the kind constants and the four-member `QUIET_HOURS_BYPASS_KINDS`. `render_worker_idle` at `:242-251` is the best `render_*` template. Also read `notify.notify` at `:128-160`: it writes an inbox event itself ONLY when both `plan_slug` and `project_root` are passed, and does so BEFORE the quiet-hours check. `cmd_notify_heartbeat_failure` deliberately writes its own inbox entry and then calls `notify.notify` WITHOUT those kwargs, so there is exactly one inbox write. Follow that; passing both would double-write.
- `end_of_line/watch.py:20-89` — the visible-event sets, including `_VERBOSE_ONLY` at `:55-69` (note `EVENT_HEARTBEAT_LOOP_FAILING` lives there and your event deliberately does not); `_FORMATTERS` at `:113` with conditional splices at `:205, :230, :250, :256`; `:261-288` for `_TASK_STATUS_MAP` and `_TASK_VERBOSE_STATUS_MAP`.
- `end_of_line/hooks/clu_inbox_surface.py:112-122` — `WEDGE_INSTRUCTION_BLOCKS`, a list of `(inbox_type, instruction)` tuples; the comment at `:112-117` states that adding a wedge class is one entry.
- `tests/test_watch_operator_filter.py:253-262` — asserts every `_OPERATOR_VISIBLE` event has a `_FORMATTERS` entry. It will fail until you register both.
- `tests/test_notify_heartbeat_failure.py` — the closest existing test module to the one you are writing.
- `tests/__init__.py` — `CluTestCase`, `capture_inbox_writer`, `must`, `write_config`, `isolate_registry`.

## Produce

1. **Failing tests first.**
   - `tests/test_notify_worker_dead.py` (new):
     - A wrong token is rejected with `ExitCode.CLAIM_MISMATCH`.
     - The first call appends `EVENT_PHASE_WORKER_DEAD_REPORTED` with the
       phase, pid, and the ATTEMPT log path — `claim["log_path"]`, the
       `<phase>.<session>.log` a post-mortem wants, NOT the daemon's own
       `.hb.log` sidecar that `run_loop` carries as `log_path`. Phase
       `death-recovery` needs that same field for quota classification, so
       getting it wrong here breaks that phase silently. Writes one inbox
       event and calls notify once.
     - A second call with the same claim is a no-op returning `ExitCode.OK` —
       no second event, no second inbox file, no second notify.
     - The notify kind is NOT in `QUIET_HOURS_BYPASS_KINDS`.
   - `tests/test_heartbeat_daemon.py` (extend):
     - `tick_once` with a live PID whose cmdline lacks the plan slug returns
       the worker-dead action (the cmdline-anchored probe), while a live PID
       whose cmdline carries it returns `ok`.
     - `run_loop` invokes the death report exactly once on the worker-dead
       exit, and NOT on the claim-gone exit — a claim released by
       `clu complete` is a normal finish, not a death.
     - A `LockTimeout` raised by the report is swallowed: the loop still
       returns 0 and prints a diagnostic, so the daemon can never hang.
   - `tests/test_state.py` (extend): `mutate(path, timeout_seconds=...)`
     forwards to `locked` and raises `LockTimeout` when the lock is held;
     `mutate(path)` with no timeout keeps blocking as before.
   - `tests/test_supervisor_worker_dead_dedup.py` (new): with the marker
     already stamped, the supervisor's worker-dead branch still releases and
     reaps but does NOT emit a second notification.
   - `tests/test_watch_operator_filter.py`: the new event is operator-visible
     and has a formatter.

2. **Implementation.**
   - `end_of_line/state.py`:
     - `EVENT_PHASE_WORKER_DEAD_REPORTED = "phase_worker_dead_reported"` in
       the event block, with the sibling comment convention above it — name
       the heartbeat daemon as the emitter, the claim marker as the dedup
       rule, and each field.
     - `worker_death_already_reported(claim)` / `mark_worker_death_reported(claim, now)`
       following `mark_worker_idle_emitted`.
     - Thread `timeout_seconds: float | None = None` through `locked_json`
       and `mutate`, forwarding to `locked`. Default `None` preserves every
       existing caller's behavior — do not change any call site.
   - `end_of_line/cli.py`: `cmd_notify_worker_dead`, argparse block, and
     `dispatchers` entry. Follow `cmd_notify_heartbeat_failure` line for line;
     `st.validate_slug` on the plan before any path join.
   - `end_of_line/notify.py`: `KIND_WORKER_DEAD_REPORTED` with a comment
     stating why it is NOT in the bypass set, and `render_worker_dead_reported`
     — emoji-led body naming the plan, phase, and the concrete next command
     the operator would run.
   - `end_of_line/heartbeat_daemon.py`:
     - Replace the bare `_pid_alive` probe with `st.claim_worker_alive` using
       the plan slug as `cmdline_match`. Keep the `pid_alive` injection seam
       so tests can still drive it.
     - On the worker-dead exit action, call the new command in-process via
       `cli.main` before returning, wrapped so no exception — `LockTimeout`
       included — can prevent the loop from returning 0.
   - `end_of_line/supervisor.py`: in the worker-dead branch, consult
     `worker_death_already_reported` and skip the notify body when the daemon
     already reported. Still append the supervisor's own event, still release,
     still reap — only the duplicate operator ping is suppressed.
   - `end_of_line/watch.py`: register the event in the default visible set,
     `_OPERATOR_VISIBLE`, `_FORMATTERS`, and `_TASK_STATUS_MAP`.
   - `end_of_line/hooks/clu_inbox_surface.py`: add the wedge-instruction tuple
     for the new inbox type.
   - `docs/contract.md`: events-table row plus the semantics paragraph.
   - `docs/operations.md`, `docs/reference.md`: the new surface.

3. **Acceptance.**
   - All new tests green; full suite green with count and delta recorded.
   - An actual invocation reaches the dispatcher — e.g. calling it with a
     deliberately wrong token exits `ExitCode.CLAIM_MISMATCH`. Do NOT use
     `--help` as the proof: argparse resolves `--help` at parse time, before
     the `dispatchers` dict is ever indexed, so it proves only the parser site.
   - The event appears in `_TASK_STATUS_MAP`, asserted by a test that drives
     `--task-list` output over a state file carrying it.
   - The inbox entry carries its wedge instruction, asserted against
     `WEDGE_INSTRUCTION_BLOCKS`.
   - A `clu watch --operator` run over a state file carrying the new event
     prints a formatted line for it, in both text and `--json` mode. The JSON
     check matters specifically: `watch.project_event` gates the JSON emit on
     the projection, so an unregistered event is invisible there too.
   - `python3 -m unittest tests.test_watch_operator_filter` green.
   - Grep confirms no existing `st.mutate(` call site was given a timeout it
     did not previously have.

4. **Commit + attest + complete.**
   - **Record cross-phase findings** in `## Findings log` — phase
     `death-recovery` builds directly on your marker and your lock-timeout
     plumbing, so anything surprising about either belongs there.
   - Commit: `worker-death-visibility: phase death-report — heartbeat daemon reports worker death (#104)`.
   - Stage explicit paths: the source files above, the doc files, and your
     test files.
   - After the commit:
     - `clu verify --plan worker-death-visibility --phase death-report --token <T>`
     - `clu attest --simplify --plan worker-death-visibility --phase death-report --token <T>`
   - `clu complete --plan worker-death-visibility --phase death-report --token <T>`

## Failure modes to watch

- **The daemon runs where you cannot debug it.** It is double-forked,
  `setsid`-detached, with stdio redirected to
  `<plans>/.orchestrator/logs/<phase>.<token>.hb.log`. Every diagnostic you
  add lands there and nowhere else. Test through the `detach=False` and
  `max_ticks` seams on `run` / `run_loop` rather than trying to observe a real
  daemon.
- **An unbounded lock on the daemon's exit path is the one unrecoverable
  bug in this phase.** Nothing kills that process — `reap_orphan_pgroup`'s
  `killpg` cannot reach it by design. If your report path can block
  indefinitely, a supervisor tick holding the lock strands a daemon forever.
  The bounded timeout is not a nicety.
- **`assert_claim_match` can legitimately fail here.** Between the daemon
  observing a dead PID and its report landing, a supervisor tick may already
  have released the claim. That is `ClaimMismatch`, it is expected, and it
  must exit cleanly — the supervisor already reported the death, so silence is
  correct. Do not treat it as an error.
- **Do not touch `last_heartbeat_at` on the death path.** The daemon probes
  liveness BEFORE pinging precisely so a dead worker stops refreshing that
  field and the lease ages honestly. Refreshing it during the death report
  re-creates the #72 zombie-lease bug.
- **The `exit_claim_gone` action is not a death.** It fires when
  `clu complete` or `clu block` released the claim — the normal, successful
  end of a phase. Reporting a death there would ping the operator on every
  successful phase.
- **A new event makes a dead plan look freshly active.**
  `fleet.summarize_plan` uses the last event's timestamp as last-activity, so
  `clu list` will show recent activity for a plan whose worker just died.
  That is arguably correct — something did just happen — but check the
  rendering and note it in the findings log if it reads as misleading.
