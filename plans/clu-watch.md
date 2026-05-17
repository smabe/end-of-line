# clu-watch — streaming state-machine event projection for AI agents

clu has two existing observability surfaces today:
- `clu status [--json]` — single snapshot of one plan.
- `clu logs --follow` — `tail -f`-style stream of the worker subprocess
  log (raw stdout chatter, no state-machine structure).

**Gap:** there's no streaming projection of the per-plan *events*
array — the structured `EVENT_PHASE_STARTED` / `EVENT_PHASE_COMPLETED`
/ `EVENT_PHASE_BLOCKED` / `EVENT_PLAN_COMPLETED` / etc. log that
records every state transition.

`clu watch` fills the gap. It emits one concise line per meaningful
transition, designed for AI-agent consumption via Claude Code's
`Monitor` tool: each stdout line becomes a notification with enough
context for an agent to decide whether to act. Polling loops written
in operator bash get replaced by a native streaming endpoint that
tails state files directly.

**Automatic-on-init story:** `clu init` and `clu queue add` print a
one-line tip ("Tip: `clu watch --project . --plan <slug>` to stream
state events") so operators not using `/clu-plan` discover it. The
`/clu-plan` skill itself arms `Monitor(command="clu watch ...")`
right after `clu queue add` so Claude-driven sessions get a live
feed hands-free.

GitHub issue gets filed by the worker on phase `docs` (no pre-existing
issue — scope was operator-approved live in conversation). The docs
commit closes it.

## Locked design decisions

### Phase 1 — `events` (projection module, pure function)

- **New module:** `end_of_line/watch.py`.
- **Public function:** `project_event(event: dict, plan_slug: str,
  *, verbose: bool = False) -> str | None`. Returns the rendered line
  or `None` if the event is filtered out (noisy/verbose-only).
- **Default-visible event set** (always emit):
  `EVENT_PHASE_STARTED`, `EVENT_PHASE_COMPLETED`,
  `EVENT_PHASE_BLOCKED`, `EVENT_BLOCKER_ANSWERED`,
  `EVENT_BLOCKER_CONSUMED`, `EVENT_BLOCKER_SLA_EXCEEDED`,
  `EVENT_PHASE_MAX_ATTEMPTS`, `EVENT_PHASE_STALLED`,
  `EVENT_TASK_SPAWNED`, `EVENT_TASK_COMPLETED`,
  `EVENT_PLAN_COMPLETED`, `EVENT_DISPATCH_FAILED`,
  `EVENT_SYSTEMIC_FAILURE`, `EVENT_PAUSED`, `EVENT_RESUMED`,
  `EVENT_RETRY_REQUESTED`, `EVENT_QUEUE_POPPED`,
  `EVENT_WORKTREE_MISSING`, `EVENT_WORKTREE_CONFLICT_WARNING`,
  `EVENT_QUEUE_APPENDED` (when v2 ships, otherwise N/A),
  `EVENT_QUEUE_REJECTED` (when v2 ships).
- **Verbose-only event set** (emit only with `--verbose`):
  `EVENT_LEASE_EXPIRED`, `EVENT_LEASE_EXTENDED`,
  `EVENT_CLAIM_FORCE_RELEASED`, `EVENT_ATTEMPTS_RESET`,
  `EVENT_STUCK_BLOCKER_REPINGED`, `EVENT_STALLED_CLAIM_NOTIFIED`,
  `EVENT_WORKTREE_ATTACHED`, `EVENT_WORKTREE_CLEANED`,
  `EVENT_WORKTREE_RETAINED_AHEAD`.
- **Line shape** (text mode): `<slug>/<phase>: <transition>` —
  drop the `/<phase>` segment for plan-scoped events
  (`EVENT_PLAN_COMPLETED`, `EVENT_PAUSED`, `EVENT_RESUMED`,
  `EVENT_QUEUE_POPPED`, etc.). Include actionable IDs when relevant
  — blocker prompts include the blocker id (`<slug>/<phase>:
  BLOCKED <blocker-id> — <question>`) so an agent can immediately
  `clu answer <blocker-id>`.
- **Caps:** truncate question / reason fields to 100 chars to keep
  notifications scannable. Full payload available via `--json` mode.

### Phase 2 — `stream` (polling loop + state-file tailer)

- **Public function:** `watch.stream_loop(state_paths: list[Path],
  *, json_mode: bool, verbose: bool, sink: TextIO = sys.stdout,
  poll_interval: float = 1.0) -> int`. Returns exit code on SIGINT.
- **Per-plan cursor:** `last_seen_event_index: dict[Path, int]`.
  On first iteration, emit the *current snapshot* baseline (one
  line per plan: status + active phase) then set the cursor to
  `len(events)` for each.
- **Polling cadence:** default 1s for explicit `--plan` or
  single-project mode; 5s for `--all` (registry-wide). Tunable
  via `--interval N`.
- **State-file watching:** poll modification time; on change, load
  events, slice from cursor, project each through phase 1's
  function, emit non-`None` lines, advance cursor. New plans added
  to the registry mid-watch are picked up by re-resolving the
  state-path list on each tick (only when in `--all` mode — the
  cheap-to-resolve case).
- **JSON mode:** emit `json.dumps({"ts": ISO, "slug": ..., "event":
  {...}})` per line, no rendering. Cursor and filtering still
  apply (verbose flag still gates).
- **Graceful exit:** SIGINT prints a final newline and returns
  `ExitCode.OK`. State files going missing (plan archived
  mid-watch) drop silently from the cursor map.

### Phase 3 — `cli` (subcommand + arg shape)

- **Subcommand:** `clu watch`.
- **Args:** `--project PATH` (optional in `--all` mode),
  `--plan SLUG` (with `--project` → single-plan mode), `--all`
  (mutually exclusive with `--plan`; watches every registered
  plan), `--json` (toggle output format), `--verbose` (include the
  noisy events), `--interval FLOAT` (poll interval seconds).
- **Default mode resolution:** bare `clu watch` (no args) implies
  CWD project + every registered plan in that project. The CWD
  inference is the convenience path; explicit `--project` still
  works.
- **Exit codes:** `OK` on SIGINT-exit; `UNKNOWN_TASK` if `--plan`
  doesn't match a registered plan; `GENERIC` on argparse-violation.

### Phase 4 — `tips` (init + queue add print the recipe)

- **`clu init` closing line:** after the existing "Initialized ..."
  line, print:
  `Tip: \`clu watch --project . --plan <slug>\` streams state events
  (use with Claude's Monitor tool).`
- **`clu queue add` closing line:** when the operator adds one or
  more plans, print:
  `Tip: \`clu watch --project . --all\` streams every queued plan
  (use with Claude's Monitor tool).`
- **Suppression:** `--quiet` flag on both commands suppresses the
  tip (for scripts). The existing `_maybe_print_monitor_tip` helper
  in `cli.py` is the natural sibling; add `_maybe_print_watch_tip`.

### Phase 5 — `skill-wire` (skill updates so Claude arms Monitor automatically)

- **`/clu-plan` SKILL.md** — in step 5 ("On `ship`, write files +
  optionally init/queue"), after the `clu queue list` confirmation,
  add a step: "Arm `Monitor(command='clu watch --project .
  --plan <slug>', persistent: true)` so the operator sees live
  phase transitions." The skill's worked example gains this line.
- **`/clu-monitor` SKILL.md** — in the "How surfacing works"
  section, add a paragraph: "For *live* in-session streaming (vs.
  the inbox hook's mark-and-sweep into the next user turn), use
  `clu watch` inside `Monitor`. Inbox = AFK channel; watch =
  at-desk channel. They're complementary."
- Skills bundled with clu live under
  `end_of_line/skills/<name>/SKILL.md`; the user-installed copy at
  `~/.claude/skills/<name>/SKILL.md` may be a symlink (per
  `clu install-skill`). Edit the bundled source. After phase ships,
  `clu install-skill --force --only clu-plan` re-installs.

### Phase 6 — `docs` (file + close issue + sweep)

- **GitHub issue:** the worker creates an issue from this plan at
  start of the phase (`gh issue create --title "clu watch:
  streaming state-event projection for AI agents" --body <body
  pointing at this plan file>`) so the final commit can
  `(closes #N)`. Rationale: scope was operator-approved live;
  no pre-existing issue.
- **`docs/reference.md`** — `cmd_watch` entry under the CLI
  command list; `watch.project_event` / `watch.stream_loop`
  entries under the module list.
- **`docs/operations.md`** — new subsection under "Background
  monitoring" titled "Live in-session feed (`clu watch`)";
  positions it next to the inbox-hook section.
- **`README.md`** — short paragraph under the "Observe what clu
  is doing" section (or equivalent) introducing `clu watch` as
  the live-feed sibling to the inbox.

## Non-goals

- **No event persistence beyond the per-plan state.json events
  array.** No new event log file, no archive, no rotation. The
  existing append-only events array is the source of truth.
- **No SSE / WebSocket / HTTP endpoint.** `clu watch` is a CLI
  stream; if a future use case needs HTTP, that's a separate plan.
- **No event-filter DSL.** `--verbose` is the only knob; richer
  filtering happens via JSON-mode + `jq` downstream.
- **No `clu init` *spawning* a background watch process.** Output
  would go nowhere (Monitor is Claude-session-scoped). The tip
  pattern is the right shape.
- **No `clu logs --follow` deprecation.** Different layer (worker
  stdout vs state events); both stay.
- **No backfill of historical events on `--from N` resume in v1.**
  `--from N` accepts a starting cursor but operates only forward.
  Backfill is a v2 if anyone asks.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the
  hood / Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- Call `clu complete --plan clu-watch --phase <id> --token <T>` on
  success.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| events | `clu-watch-events.md` | `watch.project_event` pure projector + per-EVENT_* tests | 2h |
| stream | `clu-watch-stream.md` | `watch.stream_loop` polling + cursor + snapshot baseline | 2h |
| cli | `clu-watch-cli.md` | `clu watch` subcommand wiring + arg resolution | 1h |
| tips | `clu-watch-tips.md` | `clu init` + `clu queue add` closing tips + `--quiet` | 30m |
| skill-wire | `clu-watch-skill-wire.md` | `/clu-plan` arms Monitor; `/clu-monitor` mentions watch | 30m |
| docs | `clu-watch-docs.md` | GH issue file + sweep + close (closes #N) | 1h |
