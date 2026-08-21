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
  (small, mostly-stateless utility around the store + one
  supervisor entry). Splitting into `docs/reference/<module>.md` would
  fragment grep without adding navigation value.
- Single file = single diff when an invariant changes that touches
  multiple modules (slug validation, token discipline, event-type
  constants).

**The original revisit trigger has been crossed and the decision has
not been revisited.** This layout was chosen when the spread was
67–536 LOC and the rule was "revisit if any single module crosses ~800
LOC of source." Several now do — `cli.py` is past 6,900 — so the
single-file choice stands on the shared-audience and single-diff
arguments alone, not on size. Splitting `reference.md` into a
directory is a live question, not a settled one.

## Module list (phase 3 turns each into an H2 under `## Modules`)

In load order from `cli.py`:

| Module | One-sentence responsibility |
|---|---|
| `db.py` | SQLite core: connection factories with clu's pragmas (WAL, `synchronous=FULL`, `busy_timeout`), `write_txn` / `read_txn`, both schemas, and the degradable-error vocabulary. |
| `plan_store.py` | The per-plan store: `snapshot` (one read transaction over five tables), one `op_*` per write purpose, and the supervisor tick's snapshot / preconditions / apply pair. |
| `state.py` | Plan-state DOMAIN layer: vocabulary, append-only events, slug validation, claim lifecycle, liveness probes, projections (`completed_phase_ids`, `open_blockers`, `is_claim_stalled`). No storage engine. |
| `config.py` | Per-project `.orchestrator.json` loader → `ProjectConfig` with `state_path` path-traversal guard. |
| `plan_parser.py` | Parse the master plan's `## Sessions index` table into `Phase` records; phase id = plan-file stem minus master stem. |
| `supervisor.py` | One-tick decision logic; first-match-wins priority chain (canonical list in the module docstring) returning `TickResult` (no I/O beyond state). |
| `dispatch.py` | Fire-and-forget worker spawn with 0.5s fast-fail, per-token stderr log, pid stamping on the live claim. |
| `notify.py` | Outbound routing across the configured channels (Discord REST, iMessage via `osascript`); render functions per notification kind; quiet-hours gate (`in_quiet_window`, `QUIET_HOURS_BYPASS_KINDS`) — unset `quiet_hours` means never gated, which is the shipped default. |
| `notify_inbound.py` | Long-lived poller over `~/Library/Messages/chat.db`; reply grammar `^\s*(<slug>\s+)?[0-9]\s*$`; routes to `clu answer`; seen-rowid checkpoint. |
| `registry.py` | Host-level index in the `registry` table of `~/.config/clu/clu.db`; `register / unregister / list / load_entry_state`. |
| `queue.py` | Per-project plan queue: the `queue` / `queue_history` tables in the project database, whole-operation reads/writes plus cursor-level halves for callers that must span two tables in one transaction. |
| `monitor.py` | Account-wide background-monitoring marker in the host database's `monitor` table; tolerant load/record/clear primitives used by the `/clu-monitor` skill and the CLI tip-suppression branch. |
| `inbox.py` | Per-event inbox in the host database's `inbox` table; `write_event / read_unprocessed / mark_processed / list_for_project / claim_for_project` — mark-and-sweep dedup. **Dormant**: supervisor ticks still write rows, but the reading surface is retired and nothing consumes them unless `clu install-hook --inbox` re-arms it. |
| `hooks/clu_inbox_surface.py` | Retired `UserPromptSubmit` hook script, kept on disk and opt-in via `clu install-hook --inbox`: reads stdin, filters inbox to current project (`git rev-parse --show-toplevel` / `os.getcwd()`), emits `hookSpecificOutput.additionalContext` capped at 20 events / 9500 chars, marks events processed. Crash-safe (logs to `~/.config/clu/inbox_hook.log` and exits 0). |
| `fleet.py` | Pure projection of every registered plan into one-line `PlanSummary` for bare `clu`. |
| `cli.py` | argparse dispatch + `ExitCode` IntEnum + `_die` helper + `@_translate_claim_mismatch` decorator + every operator/worker subcommand. |

`__init__.py` is a stub and not worth a section — mention in passing under
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
- **Notification model** (kinds, quiet-hours gate and why it ships off,
  bypass set, channel config, inbound grammar) belongs in
  `operations.md` next to the LaunchAgent setup; the `notify.py` and
  `notify_inbound.py` reference sections handle the code-level
  details.

## Done criteria for each downstream phase

Listed here so phase 2–6 workers can self-check before calling
`clu complete`:

- **architecture.md**: one page, has a system diagram (ASCII fine),
  describes the tick → dispatch → worker → callback loop, names the
  plan state as the single durable artifact.
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
