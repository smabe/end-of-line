# sqlite-migration-p3 — plan-state engine swap under the existing API

You are phase `p3` of the `sqlite-migration` plan. This phase delivers, as one commit, per-plan state living in the project DB (`plans/.orchestrator/clu.db`) while every consumer keeps calling the existing `st.mutate` / `st.load` API — a strangler seam (`plan_store.mutate_compat`) translates dict mutations to table writes. Native per-operation writes come in p4; this phase's job is a green suite and a working demo fleet with the engine swapped.

## Locked decisions (do NOT re-litigate)
See the master `plans/sqlite-migration.md`. The decisions binding this phase:
- Data model — LOCKED by the operator at approval 2026-08-19 ("no blobs"): normalized tables, never one JSON document per plan. Normalized core — `plans` row + `claims` / `blockers` / `spawned_tasks` / `events` tables — with JSON-valued columns for closed-shape sub-objects (`config`, `phases`, `worktree`, claim `attestations`/`cpu_samples`/`flags`, blocker `options`/`notify_metadata`). Tables per p1's DDL.
- The strangler seam is deliberate and temporary: `mutate_compat` lands here, carries p3-p6, and is DELETED in p7 (master Files-touched deletion note). Do not "improve" callers onto native ops in this phase — that is p4's scoped job.
- No JSON import (upstream #6). Existing `*.state.json` files are ignored from this commit on; p8 quarantines them. The zombie sweep must therefore stop globbing files THIS phase (it would otherwise chew legacy files as unregistered zombies — exclusion-specialist finding, master Background findings).
- **`Path.exists()` on a state path is a SECOND engine seam, and converting it is not optional.** 21 sites gate on `state_path.exists()` before ever reaching `st.load` (enumerated in Work below). Leave one unconverted and it silently reports "no such plan" the moment state stops being written — the supervisor would idle every plan and the fleet would go dark with a green test suite. Every one becomes `plan_store.exists`.
- `st.load(path)` / `st.mutate(path)` keep their signatures; the `Path` argument becomes a KEY: `<orch_dir>/<slug>.state.json` maps to (project DB at `<orch_dir>/clu.db`, plan `<slug>`). `config.state_path()` is untouched — its slug validation and traversal guard (`config.py:164-175`) still gate every external slug.
- Event ordering/identity: events carry the table's autoincrement `id` and the dict snapshot presents them as today's ordered list, each entry carrying its `id`. `clu watch`'s cursor switches from list length to max-seen event id IN THIS PHASE (it is the same file this phase already edits) — a length cursor cannot survive p4's archival moving rows out of the hot table, so the two must not be separated.

## Work

### Task 1 — the store and the engine swap
- end_of_line/plan_store.py — NEW. Work-shape sketch (interface half is contract):
  ```python
  def key_for_state_path(state_path: Path) -> tuple[Path, str]
      # (<orch_dir>/clu.db, slug) from <orch_dir>/<slug>.state.json; slug re-validated
  def create(orch_dir: Path, data: dict) -> None            # empty_state dict -> rows; IntegrityError -> exists
  def exists(orch_dir: Path, slug: str) -> bool
  def snapshot(orch_dir: Path, slug: str) -> dict           # rows -> the exact dict shape st.load returns today
  @contextmanager
  def mutate_compat(orch_dir: Path, slug: str, *, timeout_s: float | None = None) -> Iterator[dict]
      # ONE db.write_txn: build snapshot dict, yield it, then write back:
      #   plans head fields + claims row (delete when current_claim is None) +
      #   blockers/spawned_tasks upsert + events INSERT for entries beyond the
      #   original length + plans.version += 1
  def plan_slugs(orch_dir: Path) -> list[str]               # replaces *.state.json globs
  def dump_json(orch_dir: Path, slug: str) -> str           # `clu state dump` body
  ```
  Grounding: dict shape = `state.empty_state` (`state.py:483-503`) plus the claim/blocker/task field inventory enumerated in the master's `## Background findings` and the p1 DDL (claims columns, blockers columns, spawned_tasks columns); `db.*` from p1. The write-back is total-state (same information a JSON rewrite carries today) — correctness-equivalent, and the whole point of deferring per-op writes to p4.
- end_of_line/state.py — `load` and `mutate` route: a path ending `.state.json` goes to `plan_store` (snapshot / mutate_compat); every OTHER path keeps the file engine (queue.json, quota.json still live on files until p5). `stamp_activity_marker` routes its 2.0s bounded window to `mutate_compat(timeout_s=...)` → `LockTimeout` raised from `db.DbBusy` so the hook's drop-on-contention contract (`state.py:1090-1122`, `activity_hook.py`) is preserved unchanged. `SchemaVersionMismatch` is raised for a too-new DB (wrapping `db.SchemaTooNew`) so every tolerant reader's except-clause keeps firing.
- Consumes: `db.connect(path, *, readonly=False, timeout_s=5.0)`, `db.write_txn(conn, *, timeout_s=None)`, `db.read_txn(conn)`, `db.project_db_path(orchestrator_dir) -> Path`, `db.ensure_project_schema(conn)`, `db.DbBusy`, `db.SchemaTooNew` (p1); `state.empty_state(plan_slug, plan_dir) -> dict` (`state.py:483`)
- Produces: `plan_store.key_for_state_path(Path) -> tuple[Path, str]`; `plan_store.create(orch_dir, data)`; `plan_store.exists(orch_dir, slug) -> bool`; `plan_store.snapshot(orch_dir, slug) -> dict`; `plan_store.mutate_compat(orch_dir, slug, *, timeout_s=None)` ctx manager; `plan_store.plan_slugs(orch_dir) -> list[str]`; `plan_store.dump_json(orch_dir, slug) -> str`

### Task 2 — consumers that touch state files by hand
- end_of_line/cli.py — `cmd_init`'s create-under-lock block (`cli.py:1985-2028`: state-create + worktree rollback) moves to `plan_store.create` inside one txn with the same rollback shape; NEW `clu state dump [--plan <slug>]` subcommand printing `plan_store.dump_json` (all plans of the project when `--plan` omitted) — the replacement for the documented "open the state file" affordance (upstream #5).
- end_of_line/supervisor.py — `sweep_zombie_states` (`supervisor.py:977-1011`): enumerate `plan_store.plan_slugs(orch_dir)` instead of globbing `*.state.json`; re-check-under-lock becomes re-check inside `mutate_compat`.
- end_of_line/cross_plan_rules.py — the queue-pop's state-create branch (`cross_plan_rules.py:204-217`): `st.locked(state_path)` + `save_atomic` → `plan_store.create` (queue.json itself stays a file until p5; the flock-outer/DB-inner nesting is safe — no DB lock is held while waiting on the flock).
- end_of_line/dispatch.py — the raw `json.loads(state_file.read_text())` at `dispatch.py:612` (`_maybe_write_attempt_context`) → `plan_store.snapshot`.
- end_of_line/watch.py — the bootstrap raw read at `watch.py:409` → `st.load` (which now routes); the per-plan cursor changes from `len(events)` (`watch.py:486,521,536`) to the max event `id` seen, so a later archival cannot shrink the list under a live cursor; `_state_path_to_project` (`watch.py:388-390`) still works — paths remain the keys. Line formats and the TASK protocol are untouched.
- **Every `state_path.exists()` gate → `plan_store.exists` (21 sites, all verified present this session; this is the bullet that keeps the fleet alive):**
  - `end_of_line/supervisor.py:601` — `tick()`'s first line. Unconverted, EVERY tick returns `[idle] no state at …` and nothing ever dispatches again.
  - `end_of_line/registry.py:102` — inside `load_entry_state`, the seam feeding fleet / top / serve / locator / the SessionStart hook. Unconverted, every dashboard reads empty. (p7 re-points the same function's load; the exists-gate moves HERE because it fails closed sooner.)
  - `end_of_line/cross_plan_rules.py:64` (`load_plans_for_project` skip), `:149` (queue-head status probe), `:205` (the create branch, already named above).
  - `end_of_line/watch.py:403` — the TASK bootstrap's per-plan skip.
  - `end_of_line/notify_discord.py:100` — `_persist_metadata`'s silent no-op when the plan is absent; keep the path construction (it is a key now) so the no-op contract survives.
  - `end_of_line/cli.py` — 14 gates: `:1987` and `:2080` (init create + follow-up), `:3243` (queue-add source plan), `:3700`, `:3769`, `:3869` (worktree attach / gc / tick paths), `:4214`, `:4233` (prior-blocker + logs), `:4476` (status), `:5221`, `:5635`, `:5936` (validate / ship / archive), `:6506`, `:6531` (blockers list + show). Each is a "does this plan exist" question, and each currently answers it by asking the filesystem.
  - Mechanical check for the phase's own gate: after this phase, `grep -rn "state_path.exists()\|state_file.exists()" end_of_line/` returns nothing.
- end_of_line/demo.py — state seeding via `st.mutate` already routes; verify the demo project's `.orchestrator/` gets its DB created by `plan_store.create`.
- Consumes: `plan_store.create`, `plan_store.snapshot`, `plan_store.exists`, `plan_store.plan_slugs`, `plan_store.dump_json` (Task 1); `db.write_txn` (p1)
- Produces: `clu state dump` CLI subcommand (operator surface)

### Task 3 — test seam
- tests/__init__.py — `GitProjectTestCase.state_path` stays (it is a key); `_claim`/`_read` (`tests/__init__.py:272,286-291`) route through `st.mutate`/`st.load` instead of raw file IO — one seam carrying the 105 `_read` call sites across 18 files unchanged (blast-radius report).
- tests/ — the plan-state offenders from the blast-radius report: raw `read_text()` sites in `test_dispatch.py` (10), `test_supervisor.py` (4), `test_zombie_sweep.py` (3) and the remaining state-file raw-readers → `st.load`; file-existence assertions on state files → `plan_store.exists`. Queue/quota/registry test families are NOT touched here (p2 did registry; p5 does queue/quota). The three flock-mechanism tests (`test_state.py:552-573,55-59,305-313`; `test_activity_callback.py:166-186`) are rewritten against the DB equivalents: bounded-wait drop via a held `write_txn`, snapshot consistency via a concurrent writer.
- Consumes: `plan_store.exists`, `plan_store.snapshot` (Task 1)
- Produces: none

## Decisions & findings

### Decision: strangler facade (`mutate_compat`) instead of a one-phase native port  *(status: active)*
- **Rationale:** every plan-state consumer must switch backends in ONE commit (two backends for one store is split brain), but ~45 cli.py call sites + supervisor + dispatch + dashboards cannot all move to native ops in one reviewable phase. The facade makes the backend switch atomic while keeping the call-site migration incremental (p4, p7). Conceptual load is lower, not higher: one seam, unchanged callers.
- **Alternatives considered:** big-bang native port (unreviewable single phase); dual-backend flag (split brain, banned).
- **Evidence:** call-site census in the master's Files-touched and Background findings (A1: ~19 modules + 67 test files funnel through the primitives).

### Decision: the path stays the API key  *(status: active)*
- **Rationale:** `state_path()` carries the slug validation + traversal guard (`config.py:164-175`), tests build paths everywhere, and `watch`/`top` derive project roots from path shape (`watch.py:388-390`). Re-keying every signature to (project, slug) tuples is p7-scope churn with no correctness gain in this phase.
- **Alternatives considered:** store-handle objects threaded through all callers (the eventual p7 shape for dashboards; premature here).
- **Evidence:** `config.py:164-175`; `watch.py:388-390`; tests/__init__.py:272.

## Failure modes to anticipate
- **Dict-identity assumptions:** callers mutate nested structures they got from the yielded dict (`claim = data["current_claim"]; claim["pid"] = …`). `mutate_compat` must yield ONE dict and serialize THAT object on exit — never a copy taken before the yield returns.
- **Events written outside append:** `attempts_for_phase` and friends only read events, but `queue.validate_repair`-style code that REPLACES arrays would corrupt the write-back diff; the write-back appends `events[original_len:]` and asserts the prefix is unchanged (raise loudly if a caller mutated history — that is a bug today too).
- **`release_if_expired` + `claim_phase` in one mutate window** (`state.py:728-763`) — the claims-row delete-then-insert inside one txn must not trip the PK.
- **Blocker ids are positional** (`q-{len+1}`, `state.py:1133`) — snapshot must return blockers in insertion order (ORDER BY rowid).
- **Two attempt counters** (stored claim `attempts` vs `attempts_for_phase` projection) — both preserved bit-for-bit by the facade; do NOT unify (master Parking lot).
- **Test pollution:** any test that misses `isolate_registry`/XDG isolation now writes a real project DB — `assert_xdg_safe` guards host-side; project-side DBs land in tmp_path by construction (paths are keys).
- **`st.load` FileNotFoundError contract:** `plan_store.snapshot` on a missing plan row must raise `FileNotFoundError`, because callers catch it by name. Note what the current code does NOT do, so nobody preserves a distinction that was never there: `registry.load_entry_state` (`registry.py:100-107`) pre-checks `exists()` and then catches `OSError`, so missing and corrupt already collapse to the same `None`. Preserve the exception TYPES, not an imagined missing-vs-corrupt split.

## Done criteria
- Full suite green; basedpyright clean via `clu verify`.
- Observable: the scripted demo sequence (p1's `tests/demo_script.py`) runs end-to-end (dispatch, heartbeats, blocked row, completion) and `plans/.orchestrator/` afterwards contains ZERO `*.state.json` and ZERO `*.state.json.lock` files, with `clu.db` present (find-based assertion, output pasted into Status). `queue.json` and `quota.json` may still be present and are explicitly NOT asserted against here — they stay on files until p5, which owns their zero-file criterion.
- Observable: `clu state dump --plan <demo-slug>` prints JSON that `json.loads` accepts and that carries status, current_claim, blockers, and events for the demo plan.
- Observable: `clu watch` against the scripted demo sequence emits output byte-identical to `tests/goldens/watch-demo.txt` (p1's pre-migration capture) — the engine swap is invisible at the stream. Capture through `demo_script.capture_watch_lines`, which pins the projection: the golden is `clu watch`'s DEFAULT TEXT output, and `--json` is a different projection that will never match it (p1 decision).
- The zombie sweep, pointed at a project dir containing legacy `*.state.json` files plus a DB, touches only the DB (test seeds both; legacy files' bytes are byte-identical after the sweep).
