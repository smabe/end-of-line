# auto-archive-on-merge-config-docs — `.orchestrator.json:auto_archive` field + full docs

You are phase `config-opt-out-docs` of the `auto-archive-on-merge`
plan. This phase ships the real `.orchestrator.json:auto_archive`
boolean config field (replacing the `getattr` forward-compat shim
from phase `auto-archive-rule`) and rounds out the documentation:
architecture, contract, operations.

## Locked decisions (do NOT re-litigate)

See `plans/auto-archive-on-merge.md`. Summary:

- **`ProjectConfig.auto_archive: bool = True`.** Default ON.
  Operators opt out by adding `"auto_archive": false` to
  `.orchestrator.json`.
- **Type-strict loading:** non-bool value → `ConfigError`. No
  truthy-coercion of strings, ints, etc. Use
  `isinstance(value, bool)` (NOT `bool(value)`) for validation.
- **Docs span 3 files:** architecture.md (cross-plan rule chain
  subsection), contract.md (config schema + notify-kind schema),
  operations.md (full operator workflow + opt-out example).

## Read first

- `end_of_line/config.py:68-150` — `ProjectConfig` dataclass +
  `load_project_config`. Note how `test_command` was added in the
  dry-merge-gate cli-docs phase — mirror that pattern.
- `tests/test_config.py` — find the `test_command` tests for the
  pattern (file write → load → assert field). Add `auto_archive`
  cases nearby.
- `tests/test_auto_archive_rule.py` — from phase
  `auto-archive-rule`. Re-enable / update the
  `test_disabled_by_auto_archive_false_via_getattr` test to use
  the real config field instead of monkey-patching.
- `docs/architecture.md` — find the cross-plan rule chain section.
  The dry-merge-gate phase added a "Multi-plan batch integration
  gate" subsection there; place the auto-archive subsection
  consistently.
- `docs/contract.md` — find the `.orchestrator.json` schema section
  + the notify-kind enumeration; add the new entries.
- `docs/operations.md` — find the plan-lifecycle / operator-workflow
  section. Phase `auto-archive-rule` landed a brief note; this
  phase expands it.
- `docs/_outline.md` — verify section ownership before placing
  doc additions.

## Produce

1. **Failing tests first.**
   - `tests/test_config.py` — add:
     - `test_auto_archive_defaults_to_true_when_absent` — minimal
       `.orchestrator.json` without `auto_archive` key →
       `cfg.auto_archive is True`.
     - `test_auto_archive_false_in_orchestrator_json` — file
       contains `"auto_archive": false` → `cfg.auto_archive is
       False`.
     - `test_auto_archive_true_explicit` — file contains
       `"auto_archive": true` → `cfg.auto_archive is True`.
     - `test_auto_archive_non_bool_raises_config_error` — file
       contains `"auto_archive": "yes"` → `ConfigError`. Same for
       integer `1`.
   - `tests/test_auto_archive_rule.py` — update the
     `test_disabled_by_auto_archive_false_via_getattr` test to
     drop the monkey-patch and use a real `.orchestrator.json`
     with `"auto_archive": false`. Rename to
     `test_disabled_by_auto_archive_false_config_field`.

2. **Implementation.**
   - **`end_of_line/config.py` `ProjectConfig`:**
     ```python
     @dataclass
     class ProjectConfig:
         ...
         test_command: str | None = None
         auto_archive: bool = True
     ```
   - **`load_project_config`** — add validation + pass-through:
     ```python
     auto_archive_raw = raw.get("auto_archive", True)
     if not isinstance(auto_archive_raw, bool):
         raise ConfigError(
             f"auto_archive: must be a boolean, got "
             f"{type(auto_archive_raw).__name__}"
         )
     # ...
     return ProjectConfig(..., auto_archive=auto_archive_raw)
     ```
   - **`docs/architecture.md`** — append subsection under the
     cross-plan rule chain (after "Multi-plan batch integration
     gate"):
     ```markdown
     ### Auto-archive on merge

     `auto_archive_rule` is the final priority in the cross-plan
     chain. Each cron tick, for every plan with `status ==
     STATUS_DONE` and a live worktree, the rule checks whether the
     worktree's branch is an ancestor of `origin/main` via
     `state.is_branch_merged_into`. On hit, it invokes
     `_perform_archive(cfg, slug, unregister=True)` and emits
     `KIND_PLAN_AUTO_ARCHIVED`. First-eligible-wins in registry
     order; one fire per tick per project per the ADR-0002 invariant.

     Disabled per-project via `.orchestrator.json:auto_archive:
     false`.
     ```
   - **`docs/contract.md`** — schema additions:
     - `.orchestrator.json`: `auto_archive: bool (default true)`
       with a one-line description.
     - Notify kinds enumeration: add `KIND_PLAN_AUTO_ARCHIVED`
       with description.
   - **`docs/operations.md`** — expand the auto-archive section
     left by phase `auto-archive-rule` into the full operator
     workflow:
     ```markdown
     ## Auto-archive on merge

     Once a plan ships and the operator merges its branch to
     `origin/main`, clu auto-cleans the worktree, branch, plan
     file, and registry entry on the next cron tick. The operator
     sees one `plan_auto_archived` notification per cleanup.

     **Flow:**
     1. Worker finishes; `clu complete` fires plan_done; plan
        reaches STATUS_DONE.
     2. (Multi-plan batches) `dry_merge_gate_rule` fires; clean
        result → proceed.
     3. Operator: `git merge --no-ff clu/<slug> && git push`.
     4. Next cron tick: `auto_archive_rule` detects merged branch,
        archives, emits notification.

     **Opt-out** — add to `.orchestrator.json`:
     ```json
     {
       "auto_archive": false
     }
     ```
     With `auto_archive: false`, operators must run `clu archive
     --plan <slug>` and `clu unregister --all-archived` manually,
     as before.
     ```

3. **Acceptance.**
   - All 5 new/updated tests green (4 in test_config.py, 1
     renamed in test_auto_archive_rule.py).
   - `python3 -m unittest discover -s tests` zero regressions.
   - `clu` CLI still works with a project lacking the
     `auto_archive` key (default-True path verified).
   - `grep -n "auto_archive" end_of_line/config.py` shows field +
     validation.
   - All 3 doc files contain the new content; markdown lint-clean
     (no broken code fences).

4. **Commit + complete.**
   - `auto-archive-on-merge: phase config-opt-out-docs —
     .orchestrator.json:auto_archive + full docs`
   - Stage: `end_of_line/config.py`, `docs/architecture.md`,
     `docs/contract.md`, `docs/operations.md`, `tests/test_config.py`,
     `tests/test_auto_archive_rule.py`.
   - `clu complete --plan auto-archive-on-merge --phase config-docs
     --token <T>`. **NOTE:** phase id is `config-docs`
     (filename-derived), NOT `config-opt-out-docs` — the master
     Sessions column label is just display.

## Failure modes to watch

- **`bool` is a subclass of `int` in Python.** A naive
  `isinstance(value, int)` check would accept `True`/`False`. We
  want the opposite: reject `int` and accept only `bool`. The
  correct check is `isinstance(value, bool)` which DOES reject
  `int(1)` — Python's bool isinstance is type-precise, not
  inclusive-of-int. Test
  `test_auto_archive_non_bool_raises_config_error` should explicitly
  pass `1` and `"true"` to lock this.
- **Forward-compat in rule.** Phase `auto-archive-rule` used
  `getattr(cfg, "auto_archive", True)`. After this phase, the
  attribute always exists — keep the `getattr` for safety, OR
  simplify to direct attribute access. Locked decision: keep
  `getattr` for one more ship cycle so an external config-reload
  path can't break the rule. Re-evaluate in a later cleanup.
- **Docs ownership.** `docs/_outline.md` is the structural contract.
  If a section already owns "cross-plan rules", EXTEND rather than
  create a sibling. Don't duplicate content across files.
- **Notification kind double-listed.** `KIND_PLAN_AUTO_ARCHIVED`
  exists in code from phase `auto-archive-rule`; contract.md may
  already reference it. Don't duplicate — extend the existing
  enumeration entry.
