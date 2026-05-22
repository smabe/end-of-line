# tool-stuck-perf-followups

## Goal
Ship the two perf follow-ups from the tool-stuck-active-tool-scope ship
(`3344828`) that survived measurement: (1) a thin hook entry point so
PreToolUse/PostToolUse don't import clu's whole orchestrator tree, and
(2) hoist the `ps -eo` call out of the per-plan loop in
`_print_stuck_tool_health` so `clu doctor` doesn't fork N ps subprocesses.
Drop the sidecar marker idea — measured ROI doesn't justify the
structural cost.

## Diagnosis
- **Hypothesis 1 (thin entry):** `end_of_line/cli.py:36` imports the
  full orchestrator surface (`coolant`, `cross_plan_rules`, `dispatch`,
  `dry_merge`, `fleet`, `monitor`, `notify`, `queue`, `registry`,
  `state`, `state_blocker`, `state_locator`, `supervisor`, `watch`).
  Each PreToolUse/PostToolUse hook spawns `python3 -m end_of_line.cli
  activity ...`, paying that full import on every Bash call. The hook
  only needs `state` (lock, mutate, claim-match, mark/clear active).
- **Falsifiable test 1:** measure cold-start cost of `import
  end_of_line.cli` vs `from end_of_line import state` in separate
  subprocesses.
- **Test result 1:** confirmed. `import end_of_line.cli` = 41ms,
  `from end_of_line import state` = 15ms (in-process). End-to-end
  subprocess invocation: full cli = 63ms/call, state-only = 32ms/call.
  Delta is ~31ms × N hooks per phase ≈ 18s saved at 600 hooks.
- **Hypothesis 2 (hoist ps):** `cli.py:_print_stuck_tool_health` loops
  over plans and calls `walk_worker_tree(pid, ps_output=None)` for
  each → each iteration shells out `ps -eo pid,ppid,etime,time,command`
  inside `walk_worker_tree` (supervisor.py:124-134). With N active
  plans, N ps forks.
- **Falsifiable test 2:** read `_print_stuck_tool_health` (cli.py:~2013)
  and `walk_worker_tree` (supervisor.py:~111) — does the loop pass
  `ps_output`? It does not (cli.py:2058 calls
  `walk_worker_tree(pid, ps_output=ps_output)` where `ps_output` is
  the test-seam kwarg, defaulting to `None` in production).
- **Test result 2:** confirmed by reading the source. Each loop
  iteration in the production path forks a fresh `ps`. One ps call
  costs ~50ms on macOS; at 5 active plans, that's ~200ms wasted on
  every `clu doctor` invocation that could be a single shared snapshot.

## Non-goals
- Don't ship the sidecar marker file (third followup the agent
  suggested). Measured per-call cost was ~5ms which is dominated by
  Python startup, not the 80-byte vs 150kb write. Structural cost
  (new schema, supervisor read change, migration) doesn't pay back.
- Don't change the active-marker semantics or the `clu activity`
  CLI surface. The existing `clu activity --start-bash` / `--end-bash`
  subcommand stays working — operators with existing hooks shouldn't
  have to rewrite settings.json.
- Don't auto-install hooks. The clu-phase SKILL still tells operators
  to install once by hand; this plan just changes which entry point
  the snippet recommends.
- Don't change cache cadence on `ps` for the supervisor's
  `_emit_stuck_tool` — that's a single-claim call already, not in a
  loop. Hoisting only applies to `_print_stuck_tool_health`.
- Don't measure or refactor cold-import cost for other CLI
  subcommands. The PreToolUse hook is the only sub-second hot path.

## Files to touch
- `end_of_line/state.py` — extract `stamp_activity_marker(state_path,
  *, token, phase, action, timeout_seconds)` helper. Action is
  `"start"` or `"end"` (literal strings, no enum); the helper
  acquires the lock with the given timeout, validates the claim, and
  mutates `active_tool_started_at`. Returns `True` on stamp, `False`
  on `LockTimeout`. Raises `ClaimMismatch` on bad token/phase. Both
  the existing `cmd_activity` and the new thin entry point call this.
- `end_of_line/activity_hook.py` (new, ~30 LOC) — module with a
  `main(argv=None)` that parses argv (argparse, stdlib-only),
  resolves the state path via the minimum needed config surface, and
  calls `st.stamp_activity_marker`. Imports `argparse`, `sys`, `os`,
  `pathlib.Path`, and `end_of_line.state`. **Does NOT import
  `end_of_line.cli` or `end_of_line.config` or any other heavy
  module.** Adds an `if __name__ == "__main__":` so
  `python3 -m end_of_line.activity_hook` works.
- `end_of_line/cli.py` — `cmd_activity` collapses to a few lines that
  call `stamp_activity_marker` (removes the inline lock + load + write
  pattern). Old behavior preserved.
- `end_of_line/cli.py` — `_print_stuck_tool_health` (cli.py:~2013):
  hoist the `ps -eo ...` subprocess call out of the per-plan loop.
  Run it once into a string, pass `ps_output=` to every
  `walk_worker_tree` call inside the loop. The existing test seam
  (`_print_stuck_tool_health(cfg, ps_output=...)`) means tests don't
  notice the change.
- `end_of_line/skills/clu-phase/SKILL.md` — update the operator hook
  snippet to use `python3 -m end_of_line.activity_hook` (or
  equivalent) instead of `clu activity`. Document that the old `clu
  activity` subcommand still works for operators who haven't updated.
- `tests/test_activity_hook_module.py` (new) — exercises
  `activity_hook.main(...)` directly: start/end mutate, wrong token
  rejects, lock-contention silent drop. Mirrors the structure of
  `tests/test_activity_callback.py` but imports the thin module.
- `tests/test_state_stuck_tool.py` (or new
  `tests/test_state_activity_marker.py`) — direct unit tests for the
  new `stamp_activity_marker` helper. Cover: stamp start, stamp end,
  bad token → ClaimMismatch, lock timeout → False return, no claim →
  ClaimMismatch.

## Failure modes to anticipate
- **Drift between `cmd_activity` and `activity_hook.main`.** Two
  entry points calling the same logic invite divergence. Mitigation:
  both delegate to `state.stamp_activity_marker` — the helper IS the
  contract. If a future change needs to mutate the marker differently,
  it edits the helper.
- **`pyproject.toml` console script vs `-m` invocation.** A `clu-activity-hook`
  console script would be cleaner for operators, but adds an install-time
  registration. `python3 -m end_of_line.activity_hook` works without
  any pyproject change. Defaulting to `-m` for simplicity; if
  operators ask for a console script later, add it then.
- **Hook backward compatibility.** Operators who installed the
  original snippet (`clu activity --start-bash ...`) still get correct
  behavior — the `cmd_activity` path stays. Only NEW installs get the
  recommended thin entry point. Document both work; the SKILL example
  shows the fast path.
- **Import isolation regression.** A future contributor edits
  `state.py` to import a heavy module → thin entry point gets fat
  again silently. Mitigation: add a test that asserts
  `end_of_line.activity_hook` doesn't transitively import
  `end_of_line.cli` (introspect `sys.modules` after a fresh subprocess
  import).
- **Hoisted ps call timing.** `_print_stuck_tool_health` now runs `ps`
  once at function entry; all plan iterations use the same snapshot.
  In the pathological case where a plan's claim PID started milliseconds
  before the ps snapshot was taken, that claim's tree might be
  incomplete. Acceptable — `clu doctor` is a snapshot view, not a
  real-time monitor.
- **Test-seam compatibility.** `_print_stuck_tool_health(cfg,
  ps_output=...)` is the existing seam. Hoisting changes the internal
  flow but keeps the seam: the explicit `ps_output` arg bypasses the
  newly-added live call. Existing tests pass unchanged.

## Done criteria
- `state.stamp_activity_marker(state_path, *, token, phase, action,
  timeout_seconds)` exists, paired with focused unit tests.
- `end_of_line/activity_hook.py` exists; `python3 -m
  end_of_line.activity_hook --start-bash --project X --plan Y --phase
  Z --token T` stamps `active_tool_started_at`; `--end-bash` clears
  it; wrong token returns non-zero; lock contention silently returns 0.
- Per-call cold-start cost for the thin entry is at most half the
  current `cli activity` cost when measured the same way (subprocess
  invocation). Capture before/after numbers in the commit body.
- `_print_stuck_tool_health` calls `ps -eo ...` once per invocation,
  regardless of how many active plans live in the project. Verified
  by running `clu doctor` against a project with 2+ plans and counting
  ps invocations (could be by `strace`-equivalent or by trusting the
  diff).
- clu-phase SKILL.md updated: hook snippet uses the new entry point;
  gotcha line notes that the old `clu activity` subcommand still works
  for backward compat.
- An import-isolation test asserts that `end_of_line.activity_hook`
  doesn't drag in `end_of_line.cli` (or `dispatch` / `fleet` / etc).
- Full suite green; two commits (one per phase); commit bodies cite
  benchmark numbers.

## Parking lot
(empty)
