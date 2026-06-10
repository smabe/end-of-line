# harden-worker-dispatch-hb-daemon — `clu heartbeat-daemon` + SKILL.md step-2 rewrite

You are phase `hb-daemon` of the `harden-worker-dispatch` plan. You deliver, as
one commit: a new `clu heartbeat-daemon` subcommand that replaces the bash
heartbeat while-loop in the clu-phase skill, plus the SKILL.md rewrite that
uses it. This is the prerequisite for scoped-permission dispatch: the current
compound bash loop is empirically DENIED under `dontAsk` + allowlist (spike
Test B, 2026-06-10, claude 2.1.170) even when every inner command is
allowlisted, because subshell/trap/while constructs don't survive permission
decomposition.

## Locked decisions (do NOT re-litigate)

See `plans/harden-worker-dispatch.md`. Summary:

- New module `end_of_line/heartbeat_daemon.py`; paced-loop shape mirrors
  `demo_worker.py:213-258` (`run_worker`).
- Self-daemonizes via **double-fork + setsid** — the worker's Bash call must be
  a single flat command with NO shell `&`, no subshell, no trap. Parent returns
  immediately (exit 0) after the child detaches.
- Loop contract, every 120s tick:
  1. If worker PID dead (`os.kill(pid, 0)` raises) → clean exit.
  2. Ping heartbeat **in-process** (call the same code path `cmd_heartbeat`
     uses — import, don't shell out to `clu`).
  3. Token rejected / claim gone (the stale-claim error path) → clean exit.
     This is the post-`clu complete` shutdown path: complete releases the
     claim, next tick's ping is rejected, daemon exits ≤120s later. It is NOT
     a strike.
  4. Transient failure (exception, lock contention, transport) → strike. On
     the 3rd consecutive strike, call the `notify-heartbeat-failure` path
     once, then keep looping (preserves current SKILL.md 3-strike semantics).
  5. stderr of each tick appended to the sidecar log
     `<project>/plans/.orchestrator/logs/<phase>.<token>.hb.log` (same role as
     today's `tee -a` sidecar).
- **claim.pid semantics unchanged** — daemon never becomes the claim PID
  (supervisor-lifecycle PTY constraint memory). It records nothing in state
  beyond the heartbeats themselves.
- **Reaper interaction (accepted + documented):** setsid puts the daemon in its
  own process group, so `reap_orphan_pgroup`'s killpg won't kill it. That is
  fine because exits (1) and (3) above are independent backstops — a reaped
  worker is a dead PID and a released claim. Document this in the module
  docstring; do not add the daemon to any reaper.
- CLI: `clu heartbeat-daemon --project <root> --plan <slug> --phase <id>
  --token <T> --worker-pid <N>`. Token validated against the live claim like
  every worker callback (CLAUDE.md mandate). `ExitCode` IntEnum, `_die` on
  bad args, `state.validate_slug` on plan/phase.

## Read first

- `plans/harden-worker-dispatch.md` `## Findings log` — empty if you're first.
- `end_of_line/demo_worker.py:213-258` — the paced-loop + lifecycle pattern to
  mirror.
- `end_of_line/skills/clu-phase/SKILL.md` lines ~103-126 (step 2: the bash
  heartbeat loop you are replacing) and ~128-145 (step 2b env exports — keep),
  plus the step-2 references at lines ~244 (failure modes) — every mention of
  the bash loop/`WORKER_PID` ticker must be updated coherently.
- `end_of_line/cli.py` — find `cmd_heartbeat` and the argparse wiring of an
  existing worker callback (e.g. `heartbeat`) to mirror flag style and token
  validation; find `notify-heartbeat-failure`'s implementation for the strike
  call.
- `tests/__init__.py` — `CluTestCase`, `isolate_registry`, factory helpers.

## Produce

1. **Failing tests first** — `tests/test_heartbeat_daemon.py`:
   - Extract the per-tick decision as a pure function (e.g.
     `tick_once(...) -> action`) so the loop is testable without forking.
     Tests: worker-dead → exit action; stale-token/claim-gone → exit action,
     and it must NOT count as a strike; transient error → strike increments;
     3rd consecutive strike → notify fired exactly once, counter resets per
     current SKILL.md semantics; successful ping resets strikes.
   - CLI surface test: bad slug rejected, missing token rejected (mirror an
     existing callback's test file for the pattern).
   - Do NOT unit-test the actual double-fork; cover the fork path with the
     thinnest possible seam (e.g. `daemonize=False` flag used by tests, real
     forking exercised in phase `migrate-dogfood`'s live smoke).

2. **Implementation.**
   - `end_of_line/heartbeat_daemon.py`: `run(...)` (daemonize → loop) +
     `tick_once(...)` pure core. Module docstring covers the reaper-interaction
     note from Locked decisions.
   - `end_of_line/cli.py`: argparse subcommand + dispatch to the module.
   - `end_of_line/skills/clu-phase/SKILL.md`: step 2 becomes one flat command:
     `clu heartbeat-daemon --project "$PROJECT_ROOT" --plan "$PLAN" --phase "$PHASE" --token "$TOKEN" --worker-pid <same PID source the current loop uses for WORKER_PID>`
     (preserve the existing WORKER_PID determination — read the current step 2
     to see how it's derived, keep that). Remove the EXIT trap and the bash
     loop; update the failure-modes section and any other line that references
     the old ticker so no stale vocabulary remains (grep SKILL.md for
     `kill -0`, `EXIT trap`, `tee`, `sleep 120`).
   - `docs/reference.md`: add a `heartbeat_daemon.py` module entry in the
     established format.

3. **Acceptance.**
   - All new tests green; full suite green (`python3 -m unittest discover -s tests`).
   - `grep -n "while kill -0" end_of_line/skills/clu-phase/SKILL.md` → no hits.
   - `clu heartbeat-daemon --help` exits 0 and shows all five flags.
   - Manual one-shot: in a throwaway project (use the test factory pattern, or
     a /tmp git repo + `clu init`), arm a real claim, run the daemon with
     `--worker-pid $$` from a shell, observe one heartbeat land in state.json,
     then `kill` the daemon. (Document the observed behavior in the commit
     body's Tests section.)

4. **Commit + attest + complete.**
   - Findings: if the fork/daemonize path or SKILL.md rewrite surfaced anything
     later phases need (e.g. an unexpected permission interaction), append a
     dated bullet to `## Findings log` in `plans/harden-worker-dispatch.md`
     before committing.
   - Structured commit: `harden-worker-dispatch: phase hb-daemon — clu
     heartbeat-daemon replaces bash ticker (#90)`.
   - Stage explicit paths: `end_of_line/heartbeat_daemon.py`,
     `end_of_line/cli.py`, `end_of_line/skills/clu-phase/SKILL.md`,
     `docs/reference.md`, `tests/test_heartbeat_daemon.py` (+ master if
     findings logged).
   - After the commit:
     - `clu verify --plan harden-worker-dispatch --phase hb-daemon --token <T>`
     - `clu attest --simplify --plan harden-worker-dispatch --phase hb-daemon --token <T>`
   - `clu complete --plan harden-worker-dispatch --phase hb-daemon --token <T>`.

## Failure modes to watch

- **You are running under the OLD installed skill while editing the bundled
  copy.** Do not run `clu install-skill` mid-phase; the doctor drift guard
  flagging the diff afterward is expected and correct.
- **Double-fork on macOS under a sandboxed future dispatch:** fork/setsid is a
  process op, not a file write — fine under Seatbelt — but keep the daemon
  free of writes outside the sidecar log path (which lives under the canonical
  project root; the daemon runs as `clu`, which the hardened settings exempt
  via `excludedCommands`).
- **In-process heartbeat vs CLI:** importing the heartbeat code path means the
  daemon holds no subprocess PATH assumptions; do not shell out to `clu` from
  inside the daemon (worker env PATH gotchas, SKILL.md line ~220).
- **Stale-token detection must distinguish rejection from transport error** —
  rejection is a clean exit, anything else is a strike. Getting this backwards
  either orphans the daemon (never exits) or kills the 3-strike alert.
