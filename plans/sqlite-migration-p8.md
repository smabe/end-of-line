# sqlite-migration-p8 — docs, skills, cutover checklist, legacy quarantine

You are phase `p8` of the `sqlite-migration` plan. This phase delivers, as one commit, every document, skill, and comment brought truthful about the new storage layer, the legacy JSON stores quarantined on this host, and the cutover checklist executed (LaunchAgents re-loaded). The invariant this phase enforces: **no documented escape hatch or instruction names a raw file that no longer exists** (upstream #5).

## Locked decisions (do NOT re-litigate)
See the master `plans/sqlite-migration.md`. The decisions binding this phase:
- Legacy stores are quarantined, never imported (upstream #6): live-dir `*.state.json`, `*.state.json.lock`, `queue.json`, `quota.json` move to `plans/.orchestrator/legacy/`; `~/.config/clu/`'s `registry.json`, `monitor.json`, `inbound_state.json`, `outbound_pending.json`, `discord_state.json`, `discord_cursor.json` and every `*.lock` move to `~/.config/clu/legacy/`. Frozen `plans/archive/` content is untouched.
- Bundled skills get reference updates only — no behavioral redesign (master Non-goals). The clu-owned skills are edited HERE at their source (`end_of_line/skills/`), per the abe-skills ownership rule.
- The quarantine is a documented operator one-liner in operations.md, executed once on this host as part of shipping — NOT a clu subcommand (single-operator install; a migration command for a machine fleet of one is speculative generality).

## Work

- docs/contract.md — rewrite the storage half: state schema → the p1 table DDL (plans/claims/blockers/spawned_tasks/events/events_archive), "state file location" → project DB path + `clu state dump`, lock contract → `BEGIN IMMEDIATE` + busy_timeout semantics, queue schema (:235) → queue tables, quota file schema (:288) → quota row, auto-repair contract (:311) → deleted with a pointer to the p5 rationale. Event types and callback shapes are unchanged — say so explicitly.
- docs/architecture.md — process model: ":19 reads the state file under a flock" → snapshot/CAS description; the tick's snapshot→detect→apply shape (p6); the two-DB topology and the two-txn pop; crash-recovery section re-checked against the new self-heals.
- docs/reference.md — `state.py` surface (:20-76) split: what remains in state.py (domain/projection/liveness per p7), new `db.py` and `plan_store.py` surfaces and invariants; ":136 don't read state outside the lock" → "readers use snapshots; writers use ops; never hold a read txn across polls".
- docs/operations.md — troubleshooting rewrites: "queue.json corrupt" (:2232) deleted; "stuck claim" (:2279) re-commanded; `rm quota.json` (:1484, :2219) → `clu quota clear`; NEW sections: the quarantine one-liner, WAL notes (db+`-wal`+`-shm` siblings are normal; never on a network filesystem; back up by `sqlite3 .backup` or quiescent copy), `clu state dump` for inspection.
- docs/conventions.md + CLAUDE.md — the "`with st.mutate(path) as data:`" convention bullet → the new convention pair: "reads via `plan_store.snapshot`, writes via one `plan_store.op_*` / `db.write_txn`"; the "state.validate_slug on every external plan/phase_id" and token-validation bullets survive verbatim (still true).
- CONTEXT.md — vocabulary check only (Supervisor/Worker/Operator/Plan/Phase unchanged; fix any "state file" phrasing).
- end_of_line/skills/clu-phase/SKILL.md — nine sites, counted by grep this session, in two groups. **Prose references to the state file (:9, :22, :108)** — the `<state_file>` dispatch argument, "use this to inspect prior history", and "flood state.json writes" — get rewritten toward `clu state dump --plan <slug>`. **Six lines carrying the literal `$STATE` variable (:63, :71, :97, :107, :112, :120)**: `:63` ("Read `$STATE` directly") and `:112` (`inspect $STATE` for an answered blocker) become the dump command, while **`:71`, `:97`, `:107` and `:120` derive PROJECT_ROOT and the log/context directories from `dirname "$STATE"` and must KEEP working — the argument stays a path-shaped key even with no file behind it. Say that explicitly in the skill rather than leaving a worker to wonder why it is deriving a directory from a file that does not exist.** The dispatch template that supplies the argument is unchanged; `:302` (the PreToolUse `"Bash"` matcher) is unaffected.
- end_of_line/skills/clu-plan/SKILL.md — the two state-file references (:1152, :1240) updated.
- README.md + examples/*.orchestrator.json — grep for state-file/queue-file mentions; `repair_command` — verified this session that NO example JSON carries it, so there is nothing to annotate in `examples/`; the deprecation note lives only in `docs/contract.md`'s config section and the stderr message p5 adds.
- Vocabulary purge (the deleted-concepts rule): `rg -n "state\.json|\.lock|flock|save_atomic|locked_json|LockTimeout|outbound_pending|registry\.json|monitor\.json|quota\.json|queue\.json" docs/ end_of_line/ README.md CLAUDE.md CONTEXT.md` — every hit is either updated, historical-marked (frozen docs/history/ and plans/archive/ are exempt), or justified in the commit message.
- Cutover checklist (executed, results recorded in master Status): run the quarantine mv on this repo and `~/.config/clu`; `launchctl bootstrap` the tick + inbound LaunchAgents back; run one real `clu tick-all` and confirm `[idle]`/no-op output against an empty registry; re-register or `clu init` nothing — the operator queues the next real plan when ready.
- Consumes: `clu state dump` (p3), `clu quota clear` (p5) — the affordances every doc rewrite points at
- Produces: none (docs and operational state)

## Decisions & findings

### Decision: quarantine is a documented mv, not a clu subcommand  *(status: active)*
- **Rationale:** one host, one operator, one execution; a subcommand would be speculative generality with no second caller (project KISS rule).
- **Alternatives considered:** `clu migrate quarantine` (rejected as above); deleting legacy files (rejected — they are the only record of pre-migration history; archived plans remain readable JSON by decision).
- **Evidence:** upstream #6; master Non-goals.

## Failure modes to anticipate
- A doc hit that is load-bearing for a NON-migrated surface (e.g. worker log paths, `.orchestrator.json` docs) getting over-zealously rewritten — the greps target store vocabulary, not every JSON mention.
- The clu-phase SKILL.md is executed by cold workers verbatim — a wrong command in it strands every future phase; the `clu state dump` invocation must be tested by actually running it as written.
- `docs/history/` and `plans/archive/` are frozen (read-only by project convention) — mark exempt, do not edit.
- Re-loading LaunchAgents before the pipx environment reflects the final commit — with an editable install the working tree IS the install, so re-load only after the p8 commit is made on the branch the checkout sits on.
- Memory files (`~/.claude/projects/.../memory/`) mention state.json mechanics in shipped records — out of this plan's scope (they are historical records); do not edit them here.

## Done criteria
- Observable: the vocabulary grep from Work returns zero unjustified hits outside exempt dirs (paste the final grep output into Status).
- Observable: `plans/.orchestrator/` on this repo contains exactly `clu.db` (+`-wal`/`-shm`), `logs/`, `legacy/`; `~/.config/clu/` contains no `*.json` store files or `*.lock` outside `legacy/` (config.json stays — it is config, not a store).
- Observable: post-cutover `clu tick-all` runs clean against the live host (output recorded); `clu doctor` reports healthy.
- Every command named in a doc this phase touched has been executed once as written (state dump, quota clear on a seeded pause in a temp project, the quarantine mv).
- Full suite green; basedpyright clean — and this is the plan's final phase, so the master's plan-level Done criteria are checked and recorded now.
