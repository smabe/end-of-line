# clu-watch-task-list-skill-wire — `/clu-plan` auto-arm + Claude-facing parse rules

You are phase `skill-wire` of `clu-watch-task-list`. Update the
bundled `/clu-plan` SKILL.md so the auto-arm step uses
`--task-list`, and add a new subsection teaching Claude how to react
to TASK_CREATE / TASK_UPDATE protocol lines. No Python code change.

## Locked decisions (do NOT re-litigate)

See `plans/clu-watch-task-list.md` § Phase 5. Summary:
- Edit bundled source at `end_of_line/skills/clu-plan/SKILL.md` (NOT
  `~/.claude/skills/clu-plan/SKILL.md` — that's installed copy).
- Two flag updates: step 6 block (line 239) + worked example (line ~382).
- New subsection teaches: TASK_CREATE → TaskCreate, TASK_UPDATE →
  TaskUpdate, match by task_id, buffer-and-retry on out-of-order.

## Read first

- `end_of_line/skills/clu-plan/SKILL.md:239-250` — current step 6
  Monitor-arming block.
- `end_of_line/skills/clu-plan/SKILL.md:380-390` — current worked
  example Monitor invocation.
- `end_of_line/skills/clu-plan/SKILL.md` full body — find the right
  insertion point for the new "Reacting to task-list protocol"
  subsection. Likely between current step 6 and "Critical rules" or
  just after step 6's code block.

## Produce

1. **Failing tests first**
   (`tests/test_task_list_skill_wire.py`, new — or extend
   `test_skill_wire.py` from clu-watch ship):
   - `test_clu_plan_skill_arm_uses_task_list_flag` — read
     `end_of_line/skills/clu-plan/SKILL.md`, assert it contains the
     substring `clu watch --project . --plan <slug> --task-list`.
   - `test_clu_plan_skill_has_task_protocol_reaction_section` — assert
     SKILL.md contains the substring "Reacting to task-list protocol"
     (the new subsection header).
   - `test_clu_plan_skill_mentions_task_create_handler` — SKILL.md
     contains "TASK_CREATE" + instruction to call TaskCreate.
   - `test_clu_plan_skill_mentions_task_update_handler` — SKILL.md
     contains "TASK_UPDATE" + instruction to call TaskUpdate.

2. **Implementation** (file content edits only):
   - `end_of_line/skills/clu-plan/SKILL.md` line 245 — change:
     ```
     command="clu watch --project . --plan <slug>"
     ```
     to:
     ```
     command="clu watch --project . --plan <slug> --task-list"
     ```
   - Same change in the worked-example block (search for the second
     occurrence of `clu watch --project . --plan auth-cleanup` and
     append ` --task-list`).
   - Insert new subsection after step 6's closing code block,
     BEFORE "## Critical rules" (find the exact line and insert):
     ```markdown
     ### Reacting to task-list protocol notifications

     With `--task-list`, the Monitor stream emits two line shapes:

     - `TASK_CREATE task=<id> status=pending` — bootstrap lines, one
       per plan + phase, arrive together within ~200ms at startup.
     - `TASK_UPDATE task=<id> status=<state> msg="<one-liner>"` —
       fired as state transitions happen. `<state>` is one of
       `pending` / `in_progress` / `completed`.

     **On the bootstrap batch (TASK_CREATE lines):** call `TaskCreate`
     once with all matching tasks. The parent task (`task=<slug>`,
     no `/phase`) is the top-level row; child tasks (`task=<slug>/<phase>`)
     are children of the parent. All start `pending`.

     **On each TASK_UPDATE:** call `TaskUpdate` matching by task_id.
     The `msg` field carries the human-readable transition reason
     (e.g. `"BLOCKED b-12 — should I proceed with X?"` or
     `"HALTED (max attempts on foundation)"`) — surface significant
     msgs to the operator via PushNotification when the user would
     want to act now (halts, blockers).

     **Out-of-order arrivals:** if a `TASK_UPDATE` arrives for a
     task_id you haven't seen a `TASK_CREATE` for (race condition,
     rare), buffer it ~1s and retry. If still no matching task,
     create it on-the-fly with the update's status.

     **Non-`TASK_*` lines:** the snapshot baseline (`[snapshot] slug:
     status, active=...`) and any text-mode lines that leak through
     are operator-context only — ignore in the TaskCreate flow.

     If the operator hasn't installed the new skill content yet
     (`clu install-skill --force --only clu-plan`), the auto-arm
     reverts to text mode and notifications won't have the protocol
     prefix — fall back to free-text interpretation.
     ```

3. **Acceptance.**
   - 4 new tests green.
   - All previous-phase tests still green.
   - Full suite green.
   - Manual smoke: `grep -c "TASK_CREATE\|TASK_UPDATE\|--task-list"
     end_of_line/skills/clu-plan/SKILL.md` returns ≥4.

4. **Commit + complete.**
   - Title: `clu-watch-task-list: phase skill-wire — /clu-plan auto-arm uses --task-list + parse rules`
   - Stage: `end_of_line/skills/clu-plan/SKILL.md`,
     `tests/test_task_list_skill_wire.py` (or extension).
   - `clu complete --plan clu-watch-task-list --phase skill-wire --token <T>`

## Failure modes to watch

- **Editing symlink target instead of source** — `~/.claude/skills/
  clu-plan/SKILL.md` may be a symlink or installed copy. Always
  edit `end_of_line/skills/clu-plan/SKILL.md` (the bundled source).
  Verify with `ls -la` on the user-level path before edit.
- **Note for operator** — the commit message should remind the
  operator to run `clu install-skill --force --only clu-plan` after
  merge to pick up the new content (symlinked copies don't auto-
  update from the bundled source — they're snapshot copies as of
  install time).
- **Step number drift** — if step 5/6 numbering has changed since
  the skill was last edited, find by content (search for "Arm live
  progress monitoring") rather than line number.
