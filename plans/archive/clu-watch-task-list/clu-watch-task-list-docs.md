# clu-watch-task-list-docs — file issue + sweep + close (closes #N)

You are phase `docs` of `clu-watch-task-list`. File the GitHub issue
this plan implements (no pre-existing issue — operator approved scope
live in conversation). Update docs library. Close the issue via commit
message.

## Locked decisions (do NOT re-litigate)

See `plans/clu-watch-task-list.md` § Phase 6. Summary:
- Worker files the issue via `gh issue create` at the start of this
  phase.
- `(closes #N)` in commit title triggers GitHub auto-close on merge
  to main.
- Docs updates: `docs/reference.md`, `docs/operations.md`,
  `README.md`. NOT contract.md or architecture.md (no schema or
  flow changes).

## Read first

- `docs/_outline.md` — structural contract for the docs library.
- `docs/reference.md` — find existing `cmd_watch` entry from
  clu-watch ship; extend it.
- `docs/operations.md` — find "Live in-session feed (`clu watch`)"
  subsection from clu-watch ship; add the new "Task-list mode"
  sub-subsection adjacent.
- `README.md` — find the `clu watch` table row + the "Three
  observation surfaces" bullet; decide whether to extend in place
  or add a callout paragraph.

## Produce

1. **File the GitHub issue first** (no test for this step; gh is the
   action):
   ```bash
   gh issue create \
     --title "clu watch --task-list mode for Claude TaskCreate UI" \
     --body "$(cat <<'EOF'
   ## Summary

   `clu watch` emits text lines today; with the Monitor tool, each
   becomes a notification but the format is flat. This issue adds a
   `--task-list` mode that emits a deterministic protocol Claude can
   parse to call TaskCreate / TaskUpdate, mirroring plan execution
   into the native task-list UI.

   ## Design

   See \`plans/clu-watch-task-list.md\` for the locked design. 6 phases:
   - \`protocol\` — pure projector + status mapping
   - \`bootstrap\` — emit TASK_CREATE per phase on startup
   - \`projector\` — stream_loop wiring
   - \`cli\` — argparse flag + mutex with --json/--all
   - \`skill-wire\` — /clu-plan auto-arms with --task-list, parse rules
   - \`docs\` — this issue's closer

   ## Trigger

   Operator request 2026-05-17 after watching queue-worker-callback
   and clu-watch ship — wanted the same TaskCreate UI as other
   multi-phase work in Claude Code.
   EOF
   )"
   ```
   Capture the returned issue number; substitute into the final
   commit title.

2. **No code tests** (docs-only phase). Run full suite as
   regression guard at end.

3. **Documentation updates.**
   - `docs/reference.md`:
     - `cmd_watch` entry gains `--task-list` flag with line-shape
       summary.
     - New entries for `project_event_task` and `bootstrap_task_list`.
   - `docs/operations.md`:
     - Under "Live in-session feed (`clu watch`)", add a sub-subsection
       "Task-list mode (`--task-list`)" with:
       - Line-shape spec (`TASK_CREATE` + `TASK_UPDATE`)
       - Status mapping table (event → status + msg)
       - Bootstrap-then-stream ordering
       - `--all` / `--json` exclusions
       - Claude usage example via Monitor + `/clu-plan` auto-arm
   - `README.md`:
     - Extend the `clu watch` table row to mention `--task-list` (or
       add a short callout paragraph adjacent — phase worker decides
       which reads better given the table width).

4. **Acceptance.**
   - Issue filed and number captured.
   - All three docs files updated; no broken cross-references.
   - Full suite green: `python3 -m unittest discover -s tests`.
   - `grep -c "task-list\|TASK_CREATE\|TASK_UPDATE" docs/ README.md`
     confirms new content is in.

5. **Commit + complete.**
   - Title: `clu-watch-task-list: phase docs — reference + operations + README sweep (closes #N)`
   - Body should remind operator to run `clu install-skill --force
     --only clu-plan` to pick up the new auto-arm behavior on disk.
   - Stage: `docs/reference.md`, `docs/operations.md`, `README.md`.
   - The `(closes #N)` in the title triggers GitHub auto-close on
     merge.
   - `clu complete --plan clu-watch-task-list --phase docs --token <T>`

## Failure modes to watch

- **`gh issue create` fails** — auth / repo not resolved. Surface the
  error verbatim and `clu block` rather than proceeding without an
  issue number. The closing pattern depends on the issue existing.
- **Symbol drift since plan** — verify shipped function names match
  the docs entries before committing. `project_event_task` /
  `bootstrap_task_list` should be present in `end_of_line/watch.py`.
- **`/simplify`** — docs-only doesn't qualify; skip.
- **Operator-install reminder** — the commit body MUST include a
  reminder line like "After merge: `clu install-skill --force --only
  clu-plan`" so the new auto-arm reaches `~/.claude/skills/`.
- **Mutex docs phrasing** — when documenting `--task-list and --json
  are mutually exclusive`, mirror the wording of the runtime error
  so operators see consistent messaging across CLI + docs.
