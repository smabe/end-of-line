# clu-queue-docs — update the docs library to reflect the queue feature

You are phase `docs` of the `clu-queue` plan. Phases primitive/add/
list/pop/repair/footer have shipped. The queue feature is operational.
Your job: update the docs library so contract/architecture/reference/
operations/CLAUDE.md reflect the implementation.

Design pass is done. Full context in
`.claude/plans/plan-queue-master.md` and `plans/clu-queue.md`. The
implementation is the ground truth; if the plan disagrees with the
code, the code wins — append the discrepancy to the parking lot of
`plans/clu-queue.md` (don't edit the plan in flight).

## Locked decisions (do NOT re-litigate)

- **Docs touched (5 files):**
  - `docs/contract.md` — schema, events, kinds, exit codes.
  - `docs/architecture.md` — post-loop step, freeze predicate,
    auto-repair sub-architecture.
  - `docs/reference.md` — `queue.py` public surface, new helpers,
    `dispatch.dispatch_repair_worker`.
  - `docs/operations.md` — bootstrap rule, multi-host caveat,
    `repair_command` opt-in instructions with recommended template.
  - `CLAUDE.md` — refresh the "Status" section to mention the queue
    is shipped + reference issue #17 as v2 deferred.
- **`docs/conventions.md` is NOT touched.** Slug-validation,
  EVENT_*, ExitCode rules already cover this work — no new
  conventions introduced.
- **`docs/_outline.md` IS touched** if the new queue subsections
  need to be listed in the structural contract (read it first to
  see).
- **Don't move `plan-queue-worker-callback.md`.** It stays in
  `.claude/plans/` as v2 reference material; not promoted to docs.

## Read first

- All five doc files above, in full.
- `docs/_outline.md` — the structural contract for the docs library.
- The implementation files: `queue.py`, `cli.py` (queue commands),
  `dispatch.py` (repair worker), `notify.py` (new kinds),
  `state.py` (new event + ExitCode), `config.py` (repair_command
  field). The docs MUST describe the actual shipped behavior, not
  an old version of the plan.
- `plans/clu-queue.md` — the master execution plan; reference it in
  CLAUDE.md's status section as the canonical history.
- `.claude/plans/plan-queue-master.md` — the design spec for any
  rationale or "why" the docs need.
- The recently shipped doc plans (`docs/history/plans/clu-docs/`)
  for tone, depth, formatting conventions.

## Produce

1. **No TDD for docs.** Tests for docs are limited to: every code
   identifier mentioned in the docs (function names, class names,
   constants) must resolve in the codebase. If there's a doc-link
   checker in the repo, run it; otherwise inspect manually.

2. **Update `docs/contract.md`:**
   - New "Queue schema" subsection: the full JSON shape from
     `queue._empty()`, field-by-field documentation.
   - Document `EVENT_QUEUE_POPPED` shape under the events section.
   - Document `KIND_QUEUE_SKIPPED`, `KIND_QUEUE_CORRUPT`,
     `KIND_QUEUE_REPAIRED`, `KIND_QUEUE_REPAIR_FAILED` under the
     notification kinds section, with which ones bypass quiet hours.
   - Document `ExitCode.REPAIR_DECLINED = 9`.
   - Document the auto-repair contract: the worker's responsibility
     (atomic write, preserve slugs, preserve history, exit 9 if
     can't repair safely), and clu's responsibility (backup,
     validate, revert on destructive output).
   - Document `ProjectConfig.dispatch.repair_command` (optional,
     None disables auto-repair).

3. **Update `docs/architecture.md`:**
   - Amend the "Tick priority chain" section to add rule 10 (the
     host-level per-project post-loop queue advancement). Be
     explicit that the in-tick() chain still has 8 rules and is
     unchanged.
   - New "Queue advancement" subsection covering:
     - Per-project busy gate (`current_claim is None` across that
       project's plans).
     - Head-only freeze predicate (HALTED/HALTED_REPLAN/PAUSED).
     - Absorb cases (DONE/RUNNING-with-state.json).
     - Abandon case (missing plan file).
     - Normal pop sequence (state → registry → queue-pop, single
       queue-lock window).
     - Crash recovery via existing idempotency.
   - New "Auto-repair worker" subsection covering:
     - Trigger (corruption in cmd_tick_all's queue load).
     - Backup-first, opt-in dispatch, throttle.
     - Validation pipeline (slug preservation, empty-queue check,
       history preservation, parse check).
     - Revert path.
     - Why clu's validation is the safety boundary, not the prompt.
   - Add a small diagram or text-art of the lock ordering rule:
     `queue → registry → state` (just the convention, no PNG).

4. **Update `docs/reference.md`:**
   - New `queue.py` section listing the public surface:
     `SCHEMA_VERSION`, `load`, `save_atomic`, `mutate`, `_empty`
     (mark private), plus the repair helpers
     (`validate_repair`, `best_effort_extract_slugs`,
     `best_effort_extract_history_slugs`, throttle helpers).
   - New `state.locked_json` entry under state.py's section.
   - New `dispatch.dispatch_repair_worker` entry under dispatch.py.
   - New `ProjectConfig.queue_path()` and
     `ProjectConfig.dispatch.repair_command` entries under config.py.
   - New CLI subcommands `clu queue add/list/remove` (and bare
     `clu queue` → list) under the CLI section.

5. **Update `docs/operations.md`:**
   - New "Multi-host queues" subsection: clu's queue is per-project,
     per-host. If the operator uses multiple Macs against the same
     project (via git-sync), each Mac has its own `.orchestrator/`
     and its own queue. **Recommendation: decide which Mac runs
     cron and only enqueue from that one.** No conflict-resolution
     in clu; the operator manages it.
   - New "Bootstrap" subsection: `clu queue add` requires the
     project to be known to the registry. Run `clu init` for at
     least one plan first.
   - New "Enabling auto-repair" subsection: how to set
     `dispatch.repair_command` in `.orchestrator.json` with a
     recommended template:
     ```jsonc
     {
       "dispatch": {
         "command": "...",
         "repair_command": "claude --print 'queue.json at {corrupt_path} is corrupt: {diagnosis}. Backup at {backup_path}. Read both files, diagnose, repair in place using atomic write (tmp + fsync + os.replace). HARD RULES (clu validates and reverts on violation): 1. The queue array MUST contain at least every slug from the original. 2. Do NOT write an empty queue array unless the original was provably empty. 3. The history array is forensic — do not remove entries; you may append. 4. If you cannot repair without violating rules 1-3, exit 9 (REPAIR_DECLINED). Log to {log_path}. Expected schema: {schema_json}.'"
       }
     }
     ```
     With a paragraph explaining that auto-repair is opt-in,
     clu's validation is the safety boundary regardless of the
     prompt, and the operator should review backup files
     (`queue.json.corrupt-*`) after a repair to see what changed.
   - New "Troubleshooting" entry: queue.json corrupt → operator's
     paths: (a) wait for auto-repair if enabled, (b) inspect
     backup at `queue.json.corrupt-*`, (c) `mv queue.json
     queue.json.bad` to start fresh (losing pending entries), or
     (d) open Claude in the project and ask it to repair.

6. **Update `CLAUDE.md`:**
   - Refresh the "Status" section: queue shipped at Day-N (this
     plan's session date), Sessions index summarized
     (primitive/add/list/pop/repair/footer/docs/smoke), test count
     after smoke phase. Reference `plans/clu-queue.md` as the
     canonical execution history.
   - Add the queue feature to the project's high-level summary
     paragraph.
   - Reference issue #17 as the v2 deferred work (worker-callback
     enqueue) with a one-line description.
   - **Don't add new conventions** — the existing ones (slug
     validation, EVENT_*, ExitCode, `with st.mutate`) cover this
     work without modification.

7. **Update `docs/_outline.md`** if any of the new doc sections
   need to be listed there. Check the existing entries; the
   outline is a structural index, not a verbose description.

8. **Run the full suite.** Docs don't have unit tests, but make
   sure the suite is still green (no implementation changed).

9. **Spot-check identifiers.** Grep the docs for every identifier
   you added (`queue.mutate`, `EVENT_QUEUE_POPPED`,
   `KIND_QUEUE_REPAIR_FAILED`, etc.) and confirm each resolves in
   the codebase.

10. **No `/simplify` for docs** unless you wrote so much new prose
    that the existing doc structure is straining. If you did,
    consider whether some of it should move to a sub-doc — but
    err on the side of leaving it inline; the docs library prizes
    consolidation over excessive splitting (see
    `docs/_outline.md`'s rules).

11. **Commit.** Structured:
    - Title: `clu-queue phase docs: update contract/architecture/reference/operations/CLAUDE.md`
    - Why: queue feature has shipped; the docs library is the
      authoritative spec and needs to match.
    - What's new: queue schema in contract.md; rule-10 + auto-repair
      sub-architecture in architecture.md; queue.py + repair helpers
      in reference.md; bootstrap + multi-host + auto-repair-setup
      in operations.md; CLAUDE.md status refresh + #17 reference.
    - Under the hood: docs reflect actual shipped behavior, not
      design intent; auto-repair section explains why clu's
      validation is the safety boundary, not the worker's prompt.
    - Tests: full suite still green; no tests added or changed
      (docs-only).
    - Co-Authored-By trailer.

12. **`clu complete --token <token>` with SHA + count summary.**

## Failure modes to watch for

- **Drift between docs and code.** If `queue.mutate`'s signature
  evolved during implementation, the reference.md entry must match
  the actual code, not the original plan's snippet. Re-read the
  code before writing.
- **Auto-repair template in operations.md is a long JSON string
  with embedded quotes.** Format it as a code block; if the
  operator copy-pastes it, the quoting must work in jsonc.
- **CLAUDE.md exceeds 200 lines.** Per project convention, the
  Status section refresh should be concise. If CLAUDE.md is getting
  long, consider running `/claude-md-optimizer` as a follow-up
  (parking lot it; don't run it in this phase).
- **`docs/_outline.md` says don't add sub-docs without updating
  it.** If you introduced new sub-sections, make sure the outline
  index reflects them.
- **Identifiers that don't exist yet.** If an earlier phase didn't
  ship a helper you assumed in the docs, fix the docs (don't add
  the helper here — that's scope creep).
- **Multi-host paragraph reading as a feature gap.** It's not — the
  per-host design is deliberate. Frame the paragraph as "here's how
  to operate this" not "here's a limitation we couldn't solve."

## Done criteria for this phase

- `docs/contract.md` documents queue schema, EVENT_QUEUE_POPPED,
  four new KIND_QUEUE_* constants, REPAIR_DECLINED exit code, and
  the auto-repair contract.
- `docs/architecture.md` describes rule-10 post-loop queue
  advancement (per-project), freeze-at-head predicate, absorb/
  abandon/normal-pop branches, lock ordering, and the auto-repair
  sub-architecture.
- `docs/reference.md` lists `queue.py` public surface,
  `state.locked_json`, `dispatch.dispatch_repair_worker`,
  `ProjectConfig.queue_path()`, `.dispatch.repair_command`, and
  the new CLI subcommands.
- `docs/operations.md` covers multi-host queues, bootstrap, and
  how to enable auto-repair with a recommended `repair_command`
  template.
- `CLAUDE.md` Status section refreshed; #17 referenced as v2
  deferred.
- Every identifier in the docs resolves in the codebase (spot-check).
- Full suite still green.
- One commit, structured message, no `Fixes` trailer.
- `clu complete` with token + SHA + count summary.
