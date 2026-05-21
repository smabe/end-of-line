# clu-docs outline

The structural contract for the rest of the `clu-docs` plan. Subsequent
phases (architecture, reference, operations, conventions, claude-md)
read this file and fill in the slots it defines. Don't change shape
without re-running the audit.

## Reference layout

**Single file**: `docs/reference.md`, one H2 per `end_of_line/*.py`
module.

Reasons:

- Modules share one audience (developer extending clu) and one shape
  (small, mostly-stateless utility around the state file + one
  supervisor entry). Splitting into `docs/reference/<module>.md` would
  fragment grep without adding navigation value.
- Size spread is one order of magnitude: 67–536 LOC. `cli.py` at 536
  and `state.py` at 481 are the largest, but neither dominates the
  whole file (~10 H2 sections, no section >200 doc lines once trimmed
  to public surface).
- Single file = single diff when an invariant changes that touches
  multiple modules (slug validation, token discipline, event-type
  constants).

Revisit if any single module crosses ~800 LOC of source — by then the
reference section will be unwieldy and a directory becomes justified.

## Module list (phase 3 turns each into an H2 under `## Modules`)

In load order from `cli.py`:

| Module | LOC | One-sentence responsibility |
|---|---:|---|
| `state.py` | 481 | Atomic state-file primitives: lock, mutate, append-only events, slug validation, claim lifecycle, projections (`completed_phase_ids`, `open_blockers`, `is_claim_stalled`). |
| `config.py` | 67 | Per-project `.orchestrator.json` loader → `ProjectConfig` with `state_path` path-traversal guard. |
| `plan_parser.py` | 84 | Parse the master plan's `## Sessions index` table into `Phase` records; phase id = plan-file stem minus master stem. |
| `supervisor.py` | 209 | One-tick decision logic; 8-priority chain returning `TickResult` (no I/O beyond state). |
| `dispatch.py` | 130 | Fire-and-forget worker spawn with 0.5s fast-fail, per-token stderr log, pid stamping on the live claim. |
| `notify.py` | 140 | Outbound iMessage via `osascript`; render functions per notification kind; quiet-hours gate (`_in_quiet_window`, `QUIET_HOURS_BYPASS_KINDS`). |
| `notify_inbound.py` | 190 | Long-lived poller over `~/Library/Messages/chat.db`; reply grammar `^\s*(<slug>\s+)?[0-9]\s*$`; routes to `clu answer`; seen-rowid checkpoint. |
| `registry.py` | 117 | Host-level index at `~/.config/clu/registry.json`; `register / unregister / list / load_entry_state`. |
| `queue.py` | 196 | Per-project plan queue (`<plan_dir>/.orchestrator/queue.json`); load/save/mutate via `state.locked_json`; bytes-mode regex slug extraction + `validate_repair` for the auto-repair safety boundary. |
| `monitor.py` | 72 | Account-wide background-monitoring marker at `$XDG_CONFIG_HOME/clu/monitor.json`; tolerant load/save/clear primitives used by the `/clu-monitor` skill and the CLI tip-suppression branch. |
| `inbox.py` | 138 | Per-event JSON inbox at `$XDG_CONFIG_HOME/clu/inbox/`; `write_event / read_unprocessed / mark_processed / list_for_project` — mark-and-sweep dedup, surfaced into Claude Code via the `UserPromptSubmit` hook. |
| `hooks/clu_inbox_surface.py` | 121 | `UserPromptSubmit` hook script: reads stdin, filters inbox to current project (`git rev-parse --show-toplevel` / `os.getcwd()`), emits `hookSpecificOutput.additionalContext` capped at 20 events / 9500 chars, marks events processed. Crash-safe (logs to `~/.config/clu/inbox_hook.log` and exits 0). |
| `fleet.py` | 103 | Pure projection of every registered plan into one-line `PlanSummary` for bare `clu`. |
| `cli.py` | 536 | argparse dispatch + `ExitCode` IntEnum + `_die` helper + `@_translate_claim_mismatch` decorator + every operator/worker subcommand. |

`__init__.py` (7 LOC) is not worth a section — mention in passing under
the package overview at the top of `reference.md` if at all.

## Cross-document boundaries

Each topic has exactly one owner. If a worker is tempted to cover
something that lives in another doc, they should cross-link instead of
duplicate.

| Topic | Owning doc |
|---|---|
| State schema (JSON shape, invariants, plan-markdown contract) | `contract.md` (keep as-is) |
| Worker callback contract (`complete / block / spawn / heartbeat / task-done`, token validation, exit codes) | `contract.md` |
| System diagram, tick lifecycle, dataflow, who-spawns-whom | `architecture.md` |
| Per-module public surface, key functions, invariants | `reference.md` |
| macOS install, FDA, LaunchAgent plists, log paths, troubleshooting | `operations.md` |
| TDD, `/code-review` discipline, structured commit format, slug regex, event-type constants, `--token` discipline, `_die` / `ExitCode` usage | `conventions.md` |
| Public-facing pitch, quickstart, repo map, naming | `README.md` (already rewritten) |
| Project-private status block, "read these before changing anything", sister-project pointer | `CLAUDE.md` (phase 6 rewrites) |

## Proposed additions

None required. The five-file layout covers everything in the codebase
once the existing `contract.md` is preserved. Two notes the later
phases should keep in mind rather than splitting into new docs:

- **Security model** (token validation on every worker callback, slug
  regex as path-traversal guard, lockfile `O_NOFOLLOW`, schema-version
  fail-loud) is small enough to live as a section inside
  `conventions.md` under "Load-bearing invariants". Don't spin a
  `security.md` — the surface is one paragraph plus a checklist.
- **Notification model** (kinds, quiet hours, bypass set, iMessage
  self-chat handle expectation, inbound grammar) belongs in
  `operations.md` next to the LaunchAgent setup; the `notify.py` and
  `notify_inbound.py` reference sections handle the code-level
  details.

## Done criteria for each downstream phase

Listed here so phase 2–6 workers can self-check before calling
`clu complete`:

- **architecture.md**: one page, has a system diagram (ASCII fine),
  describes the tick → dispatch → worker → callback loop, names the
  state file as the single durable artifact.
- **reference.md**: H1 + short package overview + one H2 per module
  from the list above, each with public functions/classes + invariants
  + cross-links to `contract.md` for schema details.
- **operations.md**: install, FDA, both LaunchAgent plists, log
  locations, `clu status` / `clu` fleet view as diagnostic tools,
  iMessage notification model + reply grammar.
- **conventions.md**: TDD with AAA + factory helpers, `/code-review`
  trigger, structured commit format, slug regex, event-type constants,
  `--token` discipline, `ExitCode` / `_die`, `tests.isolate_registry`
  requirement.
- **claude-md.md**: rewrite project `CLAUDE.md` to point at this docs
  library (kill the stale `brainstorm/` "read first" block), move
  `brainstorm/*.md` under `docs/history/`, leave a one-line breadcrumb
  in `CLAUDE.md`.
