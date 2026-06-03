# clu demo mode — verify-the-install fleet

## Goal
A `clu demo` command (+ a dispatched-only `clu demo-worker`) that spins up a
self-contained, deterministic, **synthetic** demo fleet through clu's *real*
dispatch → claim → transcript → top/serve → state-machine pipeline, so the
operator can confirm the whole thing works end-to-end with one command — then
tears it down cleanly on Ctrl-C. No real LLM, no hand-rolled scratch projects,
no manual teardown. Replaces the manual scratch-project dance from the
2026-06-03 `clu serve` demo session.

## Non-goals
- **No real-LLM workers — synthetic only.** The 2026-06-03 haiku demo was
  flaky, cost tokens, and the workers kept dying/being reaped. Determinism +
  zero cost + reliability is the whole point of a *verify* tool.
- **No separate/isolated registry (decision A, operator-approved).** Demo plans
  live in the *real* `~/.config/clu/registry.json`, namespaced `demo-*`, with
  guaranteed teardown. *Why the asymmetry is safe:* the `demo-*` prefix makes
  them queryable + visually distinct; three teardown paths bound orphan risk
  (Ctrl-C signal trap, `clu demo down`, a `clu doctor` sweep); clu top/serve are
  tolerant readers (`registry.load_entry_state` returns None on any bad entry),
  so a stray demo entry is harmless, not corrupting.
- **Does not replace `examples/fake-worker.sh`** — it stays as the minimal
  state-only smoke fixture (no transcript). The reuse specialist's call: the
  demo worker needs transcript-writing + a longevity loop + clu's in-process
  Python helpers, all fragile in bash.
- **No notification exercise in the demo** (notify stays off). *Why safe:* the
  notify surface is already independently verifiable via the existing
  `clu notify-test`; folding it into the demo risks spamming the operator's
  phone for no added coverage.
- **No new third-party deps** (stdlib only — clu invariant).
- **No special cron/queue wiring** — the demo uses the normal `init` → `tick`
  dispatch so it verifies the real path, not a bypass.

## Verified contracts (research ground truth — for cold resume)
All confirmed this session by Read/grep; cite when implementing.
- **cwd encoder:** `top.encode_project_dir(cwd) -> str` (top.py:42) — non-alnum
  and non-`-` chars → `-` (lossy). Transcript path =
  `projects_root / encode_project_dir(cwd) / f"{session_id}.jsonl"`.
- **Projects root + test seam:** `top.PROJECTS_ROOT = ~/.claude/projects`
  (top.py:30). `gather_rows(*, projects_root=PROJECTS_ROOT, …)` (top.py:280) and
  `locate_transcript(cwd, *, projects_root=PROJECTS_ROOT, session_id=…)`
  (top.py:86) are **parameterizable** — tests pass a tmp `projects_root`. The
  demo-worker's transcript writer MUST accept a `projects_root` arg (default
  `PROJECTS_ROOT`) so tests never write to the real `~/.claude/projects`.
- **Transcript locator confirmation** (top.py:54-83): the file is accepted only
  if a record within the first ~200 lines carries `cwd` == the worker's cwd AND
  `isSidechain` is falsy. Synthetic records MUST include the real `cwd` and
  `isSidechain: false`, else the locator rejects the file → empty row.
- **Parser fields** (`top.extract_activity` top.py:174-223, `_content_blocks`
  top.py:164-171): switches on record `type` ∈ {`assistant`,`user`}; reads
  `message.content` (string OR list of block dicts); block `type` ∈
  {`text`,`tool_use`,`tool_result`}; `block.name == "Bash"` → `input.command` +
  `block.id`; `block.name ∈ _WRITE_TOOLS` (`{"Edit","Write","MultiEdit",
  "NotebookEdit"}`, top.py:33) → `input.file_path`; `tool_result.tool_use_id`;
  `message.usage` (dict, assistant); record `timestamp`. `command_running` =
  a Bash `block.id` with NO matching `tool_result.tool_use_id`.
- **Timestamps:** use UTC `…Z` strings; a naive timestamp parses but yields
  "unknown" age (top.py age helpers / `state.parse_iso`).
- **Dispatch templating** (dispatch.py:194-201): substitutes `{plan_slug}`,
  `{phase_id}`, `{token}`, `{project}`, `{state_file}`, and `{session_id}`
  (a fresh `uuid4`, but ONLY when the command template literally contains
  `{session_id}` — dispatch.py:193). Worker spawned `Popen(shell=True,
  cwd=<project_root|worktree>, start_new_session=True)`; `_stamp_pid` stamps
  pid/pgid/log_path/session_id on the claim.
- **cmdline-marker reaper (the #83 footgun):** the supervisor's dead-PID guard
  calls `state.claim_worker_alive(claim, cmdline_match=plan_slug)`, and
  `state._cmdline_marker_present` requires the slug as a **whole token bounded by
  non-slug chars**. The demo dispatch command MUST surface the bare slug
  space-bounded — `clu demo-worker {plan_slug} {phase_id} …` does this by
  construction (the slug sits between spaces). This dogfoods issue #83.
- **Worker callbacks** (with `--token`): `cmd_heartbeat` (cli.py:5596, stamps
  `last_heartbeat_at`), `cmd_block` (opens blocker + releases claim),
  `cmd_complete` (marks phase done). Heartbeat every ~2min is safe under the
  default 60-min lease (~25-min stalled threshold). A worker that EXITS without
  completing gets dead-PID-reaped → re-dispatched → attempts++ → halt; only the
  "dead" scenario should exit.
- **Registry** (registry.py): `register(project_root, plan_slug)`,
  `unregister(project_root, plan_slug)`, `entries()`. `cmd_init` auto-registers
  (cli.py ~1909).
- **CLI patterns:** `ExitCode` IntEnum + `_die(ExitCode.X, msg)`; subparser
  registration via `sub.add_parser(...)` + dispatch `if args.cmd == "…"`;
  `cmd_doctor` (cli.py:2516) uses `_print_*_health(cfg)` helpers. Tests use
  `CluTestCase` (XDG redirect + `CLU_TEST_MODE=1`).

## Files to touch
- **`end_of_line/demo_worker.py`** (NEW) — the synthetic worker core. Pure,
  testable functions: `transcript_path(cwd, session_id, projects_root) -> Path`
  (reuse `top.encode_project_dir`); `build_records(scenario, step, *, cwd,
  session_id, now) -> list[dict]` (the minimum-viable record set per the parser
  contract); `append_records(path, records)` (JSONL append, parent mkdir);
  `run_worker(plan, phase_id, token, state_file, session_id, scenario, *,
  projects_root=PROJECTS_ROOT, clock=…)` — the paced loop (write fresh records +
  `clu heartbeat` every ~Ns, lease-bounded) and the per-scenario behavior
  (`busy` stays green; `idle` stops emitting after a few steps so ACT climbs;
  `block` calls `clu block` then exits cleanly; `dead` exits without completing).
- **`end_of_line/demo.py`** (NEW) — orchestration. `DEMO_SLUG_PREFIX = "demo-"`;
  `demo_root()` (temp project tree under a tracked dir, e.g.
  `clu_config_dir()/demo/`); `scaffold(scenarios) -> list[ProjectPlan]` (write
  per-plan `.orchestrator.json` whose `dispatch.command` =
  `<py> -m end_of_line.cli demo-worker {plan_slug} {phase_id} {token}
  {state_file} {session_id} --scenario <s>`, git-init, master+sub-plan so
  `cmd_init` parses); `up()` (scaffold → register → init → tick each);
  `down()` (kill demo worker pgroups, `unregister` every `demo-*`, rm demo_root
  + synthetic transcripts); `sweep() -> list[str]` (stray `demo-*` for doctor).
- **`end_of_line/cli.py`** — `p_demo_worker` subparser + `cmd_demo_worker`
  (dispatched-only, thin wrapper over `demo_worker.run_worker`); `p_demo`
  subparser (`down` subcommand / `--serve` flag) + `cmd_demo` (foreground run
  with SIGINT/SIGTERM trap calling `demo.down()`; `--serve` also launches
  `webserver.serve`); dispatch wiring for both.
- **`end_of_line/cli.py` `cmd_doctor`** — add `_print_demo_sweep_health()`
  (reports/sweeps stray `demo-*` registrations), called from `cmd_doctor`.
- **`tests/test_demo_worker.py`** (NEW) — phase-1 load-test + scenarios.
- **`tests/test_demo.py`** (NEW) — scaffold/register/teardown + idempotency +
  `demo-*`-only teardown.
- **`docs/operations.md`** — "Verify your install — `clu demo`" section.
  **`docs/reference.md`** — `demo_worker.py` + `demo.py` module sections.
- **`end_of_line/state.py`** (added to scope during live verification) —
  `reap_orphan_pgroup`'s liveness poll caught only `ProcessLookupError`, not
  `PermissionError`; a transient EPERM while reaping a *live* worker group from
  `clu demo down`'s foreground teardown crashed the caller. The demo is the
  first caller to hit this asymmetry. Structural fix (the supervisor's own
  orphan-reaping path benefits too), not a demo-side try/except workaround.

## Phases
1. **Synthetic transcript writer** (`demo_worker.py` pure core).
   **LOAD-TEST, lands first** (proves the research): write a synthetic
   transcript via `build_records`/`append_records` into a tmp `projects_root`,
   then assert `top.gather_rows(projects_root=tmp, …)` reports the expected
   `last_command` + `command_running=True`, `last_write`, `last_text`, `tokens`,
   and a fresh `last_activity_seconds`. If it fails, the schema research was
   wrong — return to EXPLORE, don't tune. TDD the record builders.
2. **`clu demo-worker` subcommand + scenarios** — `run_worker` paced loop +
   heartbeat + lease-bounded duration; the 4 scenarios (busy/idle/block/dead);
   slug-bounded dispatched invocation; `cmd_demo_worker` wrapper. Test scenarios
   drive the right state transitions (use an injectable clock + small step caps,
   no real sleeps in tests).
3. **`clu demo` up/down orchestration** (`demo.py` + `cmd_demo`) — scaffold temp
   `demo-*` projects, register + init + tick, foreground run with a signal-trap
   that calls `down()`; `clu demo down` teardown by `demo-*` marker. Test that
   teardown removes exactly the `demo-*` entries + the demo_root, leaves real
   registry entries untouched.
4. **`clu doctor` demo sweep + docs** — `_print_demo_sweep_health`; operations.md
   verify-install section; reference.md module docs.

## Failure modes to anticipate
- **Locator rejects the synthetic file** if records omit the real `cwd` or set
  `isSidechain` truthy (top.py:54-83) → dashboard shows an empty/again-missing
  row. Every synthetic record carries `cwd`==worker cwd + `isSidechain:false`.
- **Tests writing to the real `~/.claude/projects`** — the writer must take
  `projects_root`; tests pass a tmp dir and call `gather_rows(projects_root=tmp)`.
  A miss here silently pollutes the dev machine's transcript dir.
- **cmdline-marker reap (#83)** — if the demo `.orchestrator.json` command
  doesn't surface the slug space-bounded, the busy/idle workers get killed mid-
  demo (exactly today's bug). `clu demo-worker {plan_slug} …` fixes it; a test
  should assert `_cmdline_marker_present(rendered_command, slug)`.
- **Longevity / re-dispatch storms** — a worker that exits is reaped + re-
  dispatched (attempts++ → halt). busy/idle must loop+heartbeat for the demo
  window; only `dead` exits. Teardown must handle a plan mid-re-dispatch.
- **Live cron interaction** — the `com.clu.tick` LaunchAgent ticks demo plans
  every ~30s (good: verifies cron). The `dead` scenario will trip dead-PID
  detection + re-dispatch on those ticks — intended (showcases it), but teardown
  must kill workers AND unregister so the next tick finds nothing.
- **Teardown orphans on crash** — a hard kill before the trap fires leaves
  `demo-*` registry entries + demo_root + synthetic transcript files. Mitigated
  by `clu demo down` + the doctor sweep; demo_root + the per-session transcript
  paths must be enumerable for removal (derive transcript path from each claim's
  stamped `session_id` + cwd).
- **Signal-trap cleanup** — like `clu serve`, run teardown from the handler
  safely (kill pgroups, unregister, rm); avoid re-entrancy if SIGINT fires twice.
- **`demo` while a real fleet is live** — demo rows mix into the operator's
  `clu top`. The `demo-*` prefix distinguishes them (accepted under decision A);
  note it in docs.
- **`cmd_init` needs a parseable plan** — the scaffolded demo project must have a
  master with a `## Sessions index` (1 phase) + a sub-plan file, or `cmd_init`
  errors. Generate minimal ones.

## Done criteria
- `clu demo-worker` writes a synthetic transcript that `top.gather_rows()`
  renders with the expected COMMAND (`*` running), WROTE, SAYING, tokens, and a
  fresh ACT — asserted against the real parser with a tmp `projects_root`.
- `clu demo` scaffolds + dispatches N `demo-*` plans through the real pipeline;
  `clu top` / `clu serve` show N live rows spanning **busy / idle / blocked /
  dead** — the blocked one answerable via `clu answer`, the dead one red via
  dead-PID detection. (Manual: run `clu demo --serve`, eyeball.)
- Foreground `clu demo` cleans up fully on Ctrl-C: no leftover worker processes,
  no `demo-*` registry entries, no demo_root, no synthetic transcript files —
  tested.
- `clu demo down` removes any orphaned `demo-*` state; `clu doctor` reports +
  sweeps stray demo state — tested, and teardown leaves non-demo registry
  entries untouched.
- The demo fleet is deterministic/seeded (same shape every run).
- No test writes to the real `~/.config/clu` or `~/.claude/projects`
  (CluTestCase + parameterized `projects_root`).
- `docs/operations.md` (verify-install section) + `docs/reference.md`
  (`demo_worker.py`, `demo.py`) updated. Full suite green (report count;
  ~1576 baseline).

## Parking lot
(empty at start)
