# clu-watch-task-list — `--task-list` protocol for Claude TaskCreate/TaskUpdate UI

`clu watch` today emits one-line text events that arrive as flat Monitor
notifications. The operator wants Claude's native TaskCreate/TaskUpdate
UI (parent task = plan, children = phases, statuses tick from pending →
in_progress → completed) to mirror what clu watch is reporting.

The clean split: clu emits a deterministic line-shape protocol, the
agent parses it and calls TaskCreate / TaskUpdate. clu stays unaware of
Claude internals; Claude stops parsing free-form text. Same model as
JSON mode but tailored for the TaskCreate workflow.

The `/clu-plan` skill already auto-arms `Monitor(command="clu watch …")`
after `clu queue add`; this plan adds `--task-list` to that invocation
and teaches the skill how to react to the protocol lines.

GH issue gets filed by the worker on phase `docs` (no pre-existing
issue — operator approved scope live).

## Locked design decisions

### Phase 1 — `protocol` (pure line-shape helpers + status mapping)

- **Line shapes** (single-line, parseable by `re.match`):
  - `TASK_CREATE task=<slug>/<phase> status=pending`
  - `TASK_UPDATE task=<slug>/<phase> status=<pending|in_progress|completed> msg="<one-liner, escaped>"`
  - Plan-scoped events use `task=<slug>` (no `/<phase>` segment).
- **Status mapping** (event_type → TaskCreate status):
  - `EVENT_PHASE_STARTED` → `in_progress`
  - `EVENT_PHASE_COMPLETED` → `completed`
  - `EVENT_PHASE_BLOCKED` → `in_progress` (msg carries question + blocker id)
  - `EVENT_PHASE_MAX_ATTEMPTS` → `in_progress` (msg flags HALTED)
  - `EVENT_SYSTEMIC_FAILURE` → `in_progress` (msg flags signature)
  - `EVENT_PLAN_COMPLETED` → TASK_UPDATE for parent (`task=<slug>`) as `completed`
  - `EVENT_PAUSED` → `in_progress` with msg "paused"
  - `EVENT_RESUMED` → `in_progress` with msg "resumed"
  - `EVENT_PHASE_STALLED` → `in_progress` with msg "stalled"
  - All other default-visible events → skipped (TaskCreate UI doesn't
    benefit from worktree-attached / task_spawned / etc. — those stay
    in text mode log only).
  - Verbose-only events still gated by `--verbose`; when verbose AND
    task-list, emitted as `in_progress` updates with relevant msg.
- **Msg escaping:** double-quote msg, escape inner `"` and `\` so a
  single line is always one TASK line. Keep msg ≤ 100 chars (truncate
  with ellipsis, mirroring existing `_trunc`).
- **Public surface in `end_of_line/watch.py`** (extend existing module
  — no new file): `project_event_task(event, plan_slug, *, verbose=False)
  -> str | None`, sibling to `project_event`. Internal status map +
  render helpers as `_TASK_STATUS_MAP` and `_fmt_task_*`.

### Phase 2 — `bootstrap` (emit TASK_CREATE per phase on startup)

- **Master plan lookup:** for each watched plan, resolve
  `cfg.project_root / cfg.plan_dir / f"{slug}.md"`. Call
  `parse_sessions_index(plan_path)` from `plan_parser.py`. Phase ids
  come from `Phase.id`.
- **Emission order:** TASK_CREATE lines emitted *before* the snapshot
  baseline, in the order phases appear in the Sessions index. Plus one
  TASK_CREATE for the parent (`task=<slug> status=pending`) at the
  very top.
- **Missing master file:** error `UNKNOWN_TASK` (6) with message `no
  master plan at <path>` — same shape as `cmd_status` missing-state
  failure.
- **Empty Sessions index** (single-phase plan with no index): degrade
  to "no phases to seed, just emit parent" and let TASK_UPDATE lines
  populate ad-hoc as events fire. Documented as a non-error edge.
- **New helper in `watch.py`**: `bootstrap_task_list(state_paths,
  cfg_loader, sink)` — pure function, takes a callable
  `cfg_loader(state_path) -> ProjectConfig` so tests can inject fake
  configs. Returns nothing; emits via sink.

### Phase 3 — `projector` (wire task-list mode into `stream_loop`)

- **New `stream_loop` arg:** `task_list_mode: bool = False`. Mutually
  exclusive with `json_mode` at call site (CLI gates this; stream_loop
  itself just routes).
- **Routing rule:** if `task_list_mode`, project events via
  `project_event_task` instead of `project_event`. The snapshot-baseline
  line is still emitted (text shape — operator sees "[snapshot] foo:
  running, active=none" prefixed lines for context); Claude's skill
  instructions tell it to ignore non-`TASK_*` lines.
- **Bootstrap integration:** when `task_list_mode`, call
  `bootstrap_task_list` after building `cursors` but before the first
  event-poll tick. Reuses the existing `_before_first_tick` test seam
  pattern — natural insertion point.

### Phase 4 — `cli` (argparse flag + mutual exclusion validation)

- **New flag:** `p_watch.add_argument("--task-list",
  action="store_true", default=False, dest="watch_task_list",
  help="Emit TASK_CREATE/TASK_UPDATE protocol lines for Claude's
  TaskCreate UI — see docs/operations.md § 'Task-list mode'.")`
- **Mutex with `--json`:** runtime check in `cmd_watch` (argparse
  groups don't compose with the existing `--all`/`--plan` group).
  `_die(GENERIC, "--task-list and --json are mutually exclusive")`.
- **Mutex with `--all`:** v1 limitation. Multiple plans → multiple
  TaskCreate trees would complicate the protocol; defer.
  `_die(GENERIC, "--task-list requires --plan or single-project
  (mutually exclusive with --all)")`.
- **CLI wiring:** `cmd_watch` reads `args.watch_task_list`, validates
  exclusions, passes through to `stream_loop(...,
  task_list_mode=True)`.

### Phase 5 — `skill-wire` (update `/clu-plan` to use the new flag)

- **`end_of_line/skills/clu-plan/SKILL.md` step 6 (line 239)**: change
  the auto-arm command from `clu watch --project . --plan <slug>` to
  `clu watch --project . --plan <slug> --task-list`.
- **Worked example update (line ~382)**: same flag change.
- **New "Reacting to task-list protocol" subsection** in the skill
  (after step 6): teach Claude:
  > When a Monitor notification matches `TASK_CREATE task=<id>
  > status=pending`, call TaskCreate with one task per matching line
  > (the bootstrap batch arrives together within 200ms; treat the
  > batch as one TaskCreate invocation). When a notification matches
  > `TASK_UPDATE task=<id> status=<state> msg="..."`, call TaskUpdate
  > matching by task_id. If a TASK_UPDATE arrives for a task without a
  > prior TASK_CREATE (race condition), buffer it 1s and retry; if
  > still no matching task, create it on-the-fly with status from the
  > update.
- **`/clu-monitor` no change** — that skill installs the inbox hook;
  orthogonal to watch task-list mode.
- **After this phase ships**, operator runs `clu install-skill --force
  --only clu-plan` to refresh the symlink-or-copy on disk.

### Phase 6 — `docs` (file issue + sweep + close)

- **GitHub issue:** worker files at start of phase via `gh issue
  create`. Title: "clu watch --task-list mode for Claude TaskCreate
  UI". Body: links to this plan, summarizes the protocol, names the
  trigger.
- **`docs/reference.md`** — `cmd_watch` entry gains `--task-list` flag
  with the protocol summary. New entries for `project_event_task` and
  `bootstrap_task_list`.
- **`docs/operations.md`** — new subsection "Task-list mode
  (`--task-list`)" under "Live in-session feed". Documents line shapes,
  status mapping, bootstrap-then-stream order, `--all`/`--json`
  exclusions, Claude usage example.
- **`README.md`** — one-line addition to the `clu watch` table row
  mentioning `--task-list` or a short callout paragraph; phase worker
  decides which reads better.
- **Closes #N** in commit title.

## Non-goals

- **No `--task-list --all` in v1.** Multi-plan TaskCreate trees defer.
- **No `--task-list --json` combination.** Two output formats serving
  different consumers; pick one.
- **No buffered TASK_CREATE batching server-side.** Monitor's 200ms-
  batching does the grouping for Claude. No Python-side debouncing.
- **No new ExitCode.** Reuses GENERIC for arg violations, UNKNOWN_TASK
  for missing master.
- **No protocol versioning.** v1 emits the locked shape; future changes
  flip via new flag (e.g. `--task-list-v2`) rather than version-
  stamping lines.
- **No status-mapping config.** Event→status mapping hard-coded —
  operator preference would create a contract Claude can't predict.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format.
- Stage explicit paths.
- Call `clu complete --plan clu-watch-task-list --phase <id> --token
  <T>` on success.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| protocol | `clu-watch-task-list-protocol.md` | `project_event_task` + status mapping (pure helpers) | 1.5h |
| bootstrap | `clu-watch-task-list-bootstrap.md` | `parse_sessions_index` integration + TASK_CREATE emission | 1.5h |
| projector | `clu-watch-task-list-projector.md` | `stream_loop` task_list_mode wiring + snapshot ordering | 1h |
| cli | `clu-watch-task-list-cli.md` | argparse `--task-list` + mutex with `--json`/`--all` | 45m |
| skill-wire | `clu-watch-task-list-skill-wire.md` | `/clu-plan` SKILL.md auto-arm + Claude-facing parse rules | 30m |
| docs | `clu-watch-task-list-docs.md` | GH issue file + reference/operations/README sweep + close | 1h |
