# clu-ship-ergonomics-quiet — --all-done filter + state_locator gate + worktree-drift docs (#4 #5 #6)

You are phase `quiet` of the `clu-ship-ergonomics` plan. Quiet three
operational annoyances: noisy `clu ship --all-done` enumeration that
walks dead plans, every `clu answer` printing `state_locator:
skipping ...` warnings for missing tmp paths, and the undocumented
worktree config-drift pattern that bit the operator during the field
session.

One commit; suite green; `clu complete`.

## Locked decisions (do NOT re-litigate)

See `plans/clu-ship-ergonomics.md`. Summary:

- `--all-done` pre-filter goes in BOTH `_cmd_ship_direct_all_done`
  AND `_cmd_ship_pr_all_done`. Use a local-branch existence check —
  if the branch on `wt["branch"]` no longer exists in the local repo,
  skip silently.
- `state_locator: skipping` ENOENT moves to DEBUG. Everything else
  stays at WARNING. Only the state-file-load except (the second one
  in `_load_open_blockers`) needs the split. The config-load except
  stays at WARNING — config drift is a real problem worth surfacing.
- Worktree config drift is DOCUMENTED in `docs/design-briefs/clu-ship.md`,
  not toolchain'd. No new `clu sync-config` command in this phase.

## Read first

- `end_of_line/cli.py:4290-4301` — `_cmd_ship_direct_all_done`
  eligibility loop.
- `end_of_line/cli.py:4592-4603` — `_cmd_ship_pr_all_done` same.
- `end_of_line/state_locator.py:86-105` — `_load_open_blockers`
  with the two WARNING log calls.
- `end_of_line/state_locator.py:20` — `log = logging.getLogger(__name__)`
  confirms it's stdlib `logging`.
- `end_of_line/state.py` — grep for `is_branch_merged_into` and any
  branch-existence helper; reuse if present, add `_local_branch_exists`
  alongside if not.
- `docs/design-briefs/clu-ship.md` — read the full file (~180 lines)
  to learn tone + section layout before adding the new section.
- `docs/design-briefs/clu-ship-field-feedback.md:223-244` — the
  Friction #6 scenario. Quote a short excerpt in the new section
  (don't paraphrase the operator's experience).
- `tests/` — grep for an existing `test_state_locator*.py` and
  `test_ship*.py`. Pick the right one for each new test.

## Produce

1. **Failing tests first.**

   - `tests/test_state_locator*.py` (verify path):
     - `test_load_open_blockers_silent_on_enoent`: use
       `self.assertNoLogs("end_of_line.state_locator", level="WARNING")`
       (or equivalent context manager) when the state file doesn't
       exist; with `assertLogs(level="DEBUG")` verify the DEBUG line
       is emitted. Current code emits at WARNING; this should fail
       until the split lands.
     - `test_load_open_blockers_warns_on_corrupt`: write a malformed
       JSON state file; assert WARNING fires. Guards the non-ENOENT
       path against accidentally getting silenced.

   - `tests/test_ship*.py` (verify path):
     - `test_all_done_skips_plans_with_deleted_branches`: create
       two STATUS_DONE plans, delete one's branch via
       `git branch -D`, run `cmd_ship --all-done --check`, assert
       only the surviving plan appears in the validation report (no
       noise for the deleted-branch plan).

   Run `python3 -m unittest <new tests>` and confirm RED before
   moving on.

2. **Implementation.**

   - `end_of_line/state_locator.py` — split the state-load except
     block in `_load_open_blockers`. Current shape (L102-105):
     ```python
     except (FileNotFoundError, st.SchemaVersionMismatch,
             json.JSONDecodeError, OSError) as exc:
         log.warning("state_locator: skipping %s — %s", entry.plan_slug, exc)
         return None
     ```
     Change to:
     ```python
     except FileNotFoundError:
         log.debug("state_locator: skipping %s — state file missing", entry.plan_slug)
         return None
     except (st.SchemaVersionMismatch, json.JSONDecodeError, OSError) as exc:
         log.warning("state_locator: skipping %s — %s", entry.plan_slug, exc)
         return None
     ```
     The earlier config-load except (L96-98 area) stays WARNING
     unchanged. Verify exact line numbers — the exploration may have
     drifted.

   - `end_of_line/state.py` — if no branch-existence helper exists,
     add one next to `is_branch_merged_into`:
     ```python
     def local_branch_exists(project_root: Path, branch: str) -> bool:
         rc = subprocess.run(
             ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
             cwd=project_root, capture_output=True,
         ).returncode
         return rc == 0
     ```
     Public name (no underscore) — sibling of `is_branch_merged_into`.
     If a helper with this behavior already exists under a different
     name, reuse it instead of adding.

   - `end_of_line/cli.py:4290-4301` (`_cmd_ship_direct_all_done`) —
     add a branch-exists pre-filter inside the eligibility loop:
     ```python
     for p in plans:
         if p.state.get("status") != st.STATUS_DONE:
             continue
         wt = st.get_worktree(p.state)
         if wt is None:
             continue
         branch = wt["branch"]
         if not st.local_branch_exists(project_root, branch):
             continue  # NEW: skip plans whose branch was manually deleted
         if st.is_branch_merged_into(project_root, branch, "origin/main"):
             continue
         eligible.append((p.slug, branch))
     ```

   - `end_of_line/cli.py:4592-4603` (`_cmd_ship_pr_all_done`) — same
     pre-filter, same shape.

   - `docs/design-briefs/clu-ship.md` — add a new "Worktree config
     management" section before "Open scoping questions". Cover:
     - **The drift scenario.** Short prose framing + a quoted block
       from `docs/design-briefs/clu-ship-field-feedback.md:223-244`
       (the 2026-05-23 stale-UUID example).
     - **Operator workaround.** Pre-emptive `Edit` of `.orchestrator.json`
       in each active worktree when patching the canonical config
       mid-batch. One concrete example.
     - **Future tool option.** A `clu sync-config --to-worktrees`
       command flagged as "deferred pending repeat field signal" with
       a one-line rationale (cost of building speculatively > cost of
       documenting the workaround).

3. **Acceptance.**

   - All new tests green; existing suite green.
   - Full suite: `python3 -m unittest discover -s tests`. Report
     pass count delta from end of phase `cli`.
   - Manual smoke (run from a project CWD):
     - `clu answer --plan some-plan 0 2>&1 | grep -c "state_locator: skipping"` —
       returns `0` even if the registry contains stale `/private/tmp`
       entries. (If no open blocker exists, the command may exit
       non-zero with `UNKNOWN_TASK`; only the stderr noise count
       matters.)
     - `clu ship --all-done --check 2>&1` — for a project with
       shipped plans whose branches have been deleted, those plans
       don't appear in the validation report.
   - `grep -A 3 'except FileNotFoundError' end_of_line/state_locator.py` —
     confirms the new DEBUG branch landed.

4. **`/code-review`** (mandatory; diff is >1 file and >30 LOC).
   Apply ≤5 LOC mechanical fixes in the same commit.

5. **Commit + complete.**
   - Structured commit:
     ```
     clu-ship-ergonomics: phase quiet — --all-done filter + state_locator ENOENT gate + worktree-drift docs

     ## Why
     ...

     ## What's new
     ...

     ## Under the hood
     ...

     ## Tests
     ...

     Co-Authored-By: ...
     ```
     Reference `docs/design-briefs/clu-ship-field-feedback.md`
     (frictions #4 #5 #6) in the body.
   - Stage explicit paths: `end_of_line/state_locator.py`,
     `end_of_line/cli.py`, `end_of_line/state.py` (if helper added),
     `docs/design-briefs/clu-ship.md`, plus each modified/new test
     file.
   - `clu complete --plan clu-ship-ergonomics --phase quiet --token <T>`.

## Failure modes to watch

- **`_load_open_blockers` has TWO except blocks.** Only the
  state-file-load path (the second one) gets the ENOENT split. The
  config-load FileNotFoundError still warrants WARNING — config
  drift is a real problem worth surfacing. Re-read state_locator.py
  carefully before editing.
- **`local_branch_exists` may already exist in `state.py`.** Grep
  before adding. If it exists under a different name (e.g.
  `branch_exists`, `has_local_branch`), reuse it.
- **`docs/design-briefs/clu-ship.md` may be cross-referenced from
  other docs.** Grep for `clu-ship.md` across the repo to verify;
  adding a new section is safe (no anchor name collisions) but
  moving existing anchors would break links.
- **Don't widen the ENOENT silencing.** Only `FileNotFoundError` on
  the state-file-load path goes to DEBUG. Other `OSError` variants
  (permission denied, I/O error) stay at WARNING because those
  ARE real operator problems.
- **`assertNoLogs` requires Python 3.10+.** This project's CLAUDE.md
  says 3.11+, so it's available — but if any older syntax is in use
  elsewhere in `tests/`, mirror the existing pattern.
- **`is_branch_merged_into` uses subprocess.run** — mirror its
  argument shape and error-handling for `local_branch_exists`.
