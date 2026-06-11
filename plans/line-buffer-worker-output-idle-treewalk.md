# line-buffer-worker-output-idle-treewalk — idle watchdog sums descendant CPU

You are phase `idle-treewalk` of the `line-buffer-worker-output` plan. You
deliver, as one commit: `_emit_worker_idle` sampling CPU across the whole
worker process tree instead of `claim.pid` alone. This is the prerequisite
for phase pty-shim (a shim at claim.pid would read ~0% CPU and false-fire
WORKER_IDLE), and it is independently correct today — a worker's children
(test runs, builds) currently contribute nothing to the idle check.

## Locked decisions (do NOT re-litigate)

See `plans/line-buffer-worker-output.md`. Summary:
- Pid source: `walk_worker_tree(claim_pid, ...)` (supervisor.py:135-167) —
  the same battle-tested walk `_emit_stuck_tool` uses (supervisor.py:403).
  Root pid + descendants together form the sample set.
- Sampling: ONE `ps -p <pid,pid,...> -o %cpu=` invocation, sum the values.
  Instantaneous %cpu remains the metric; `append_cpu_sample` and
  `worker_idle_window_satisfied` (state.py:989-1012) are NOT modified.
- Do NOT use the tree's `Descendant.cpu_seconds` for this — that's
  cumulative CPU time, a different quantity than the windowed %cpu samples.
- Keep the injectable seams: the existing `ps_output` test seam pattern must
  keep working; the descendant walk takes the same snapshot-injection shape
  `_emit_stuck_tool` tests use.

## Read first

- `plans/line-buffer-worker-output.md` `## Findings log` — empty if first.
- `end_of_line/supervisor.py:455-523` — `_emit_worker_idle` as shipped (the
  `ps -p <pid> -o %cpu=` call at :486 is what you replace).
- `end_of_line/supervisor.py:55-167` — `Descendant`, `_parse_ps_output`,
  `capture_ps_snapshot`, `walk_worker_tree`.
- `tests/test_supervisor_worker_idle.py` — 11 existing tests, ps/lsof
  seams; `tests/test_supervisor_stuck_tool.py` — tree-walk injection
  patterns to mirror.

## Produce

1. **Failing tests first** (`tests/test_supervisor_worker_idle.py`):
   - busy child, idle root → NOT idle (sum includes descendant; the
     pre-fix behavior would false-fire).
   - all tree members idle → idle window proceeds (existing semantics).
   - descendants disappear between walk and ps (race) → no crash; sample
     from whatever pids `ps` returns.
   - no descendants (today's common case) → behavior identical to current
     single-pid sampling (regression pin on existing tests staying green).

2. **Implementation** in `_emit_worker_idle` per Locked decisions; update
   `docs/reference.md`'s supervisor section line for the idle rule.

3. **Acceptance.**
   - All new + existing worker-idle tests green; full suite green.
   - `basedpyright` exit 0 (the gate is live — your verify runs it).

4. **Commit + attest + complete.**
   - Findings: note anything pty-shim must know (e.g. seam signature
     changes).
   - Structured commit: `line-buffer-worker-output: phase idle-treewalk —
     worker-idle watchdog sums descendant CPU`.
   - Stage explicit paths: `end_of_line/supervisor.py`,
     `tests/test_supervisor_worker_idle.py`, `docs/reference.md` (+ master
     if findings logged).
   - After the commit:
     - `clu verify --plan line-buffer-worker-output --phase idle-treewalk --token <T>`
     - `clu attest --simplify --plan line-buffer-worker-output --phase idle-treewalk --token <T>`
   - `clu complete --plan line-buffer-worker-output --phase idle-treewalk --token <T>`.

## Failure modes to watch

- **Empty pid list to `ps -p`** (root died mid-tick): `ps -p` with no/dead
  pids exits non-zero — keep the existing tolerant error handling shape
  around the ps call (the current code's failure path).
- **Don't widen the rule's trigger conditions** — the no-Bash-active and
  no-anthropic-socket conditions are unchanged; only the CPU source moves.
- **Sandbox suite caveat**: judge green by `clu verify` (~30 known
  in-sandbox environment failures are not yours).
