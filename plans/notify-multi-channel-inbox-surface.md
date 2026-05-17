# notify-multi-channel-inbox-surface — active-blocker section in inbox hook

You are phase `inbox-surface` of the `notify-multi-channel` plan. Add an "Active blockers" section to the UserPromptSubmit inbox-hook output so Claude Code sessions see currently-BLOCKED plans with question, options, and a disambiguation instruction.

## Locked decisions (do NOT re-litigate)

See `plans/notify-multi-channel.md` §"Phase 3". Summary:
- Insertion: `clu_inbox_surface.py:_build_context()` after existing event list (after line 79).
- Section text format locked verbatim (see master § Phase 3).
- Disambiguation instruction: natural-language reply, Claude asks for clarification when ambiguous.
- Data: extend `open_blockers_for_host()` to include question + options OR add sibling function.
- Project scope: filter by current project.
- Empty case: omit section entirely.
- Cap at 10 blockers with `... +N more` footer.

## Read first

- `end_of_line/hooks/clu_inbox_surface.py` lines 67-89 (`_build_context()`), 92-124 (`main()`).
- `end_of_line/inbox.py` lines 133-141 (`list_for_project()`).
- `end_of_line/notify_base.py` (post-phase-1): `open_blockers_for_host()` — extend or add sibling.
- `end_of_line/state.py` lines 432-498 (blocker record + `open_blockers()`).
- `tests/test_inbox_hook.py` — patterns to mirror.

## Produce

1. **Failing tests first** in `tests/test_inbox_hook.py`:
   - `test_hook_surfaces_active_blocker` — fixture: one BLOCKED plan with question + 2 options; assert hook output contains the verbatim section format (heading, question, options, instruction).
   - `test_hook_omits_blockers_section_when_none_open` — no BLOCKED plans → no "## Active blockers" string in output.
   - `test_hook_surfaces_multiple_blockers_across_plans` — two BLOCKED plans → both blocks rendered, separated by blank line.
   - `test_hook_scopes_blockers_to_current_project` — two projects, BLOCKED plan in each; hook called for project A → only A's blocker shown.
   - `test_hook_caps_blockers_at_10` — fixture with 12 BLOCKED plans → output shows 10 + "... +2 more" footer.
   - `test_open_blockers_with_details_includes_question` — unit test on the extended query helper.

2. **Implementation.**
   - `end_of_line/notify_base.py` (or wherever cleanest): new `open_blockers_with_details(entries, project_root) -> list[BlockerDetail]`:
     ```python
     @dataclass
     class BlockerDetail:
         project_root: Path
         plan_slug: str
         phase_id: str
         blocker_id: str
         question: str
         options: tuple[str, ...]
     ```
     Reads each entry's state, walks `data["blockers"]`, filters to unanswered, joins with phase id.
   - `end_of_line/hooks/clu_inbox_surface.py`:
     - In `_build_context()` after current event list, call `open_blockers_with_details(registry_entries, current_project)`.
     - If non-empty, append section using the locked-verbatim format.
     - Cap at 10; append `... +N more open blockers — see \`clu list\` for the full set.` footer if `len > 10`.
   - Section format (lock verbatim):
     ```python
     SECTION_HEADER = "\n## Active blockers\n\n"
     BLOCKER_TEMPLATE = (
         "Plan `{slug}`, phase `{phase}`, blocker `{blocker_id}`:\n"
         "Question: {question}\n"
         "Options:\n"
         "{options_list}\n"
     )
     INSTRUCTION = (
         "\nIf the user's next message reads as a reply to one of these "
         "blockers (letter, number, or natural pick), call "
         "`clu answer --plan <slug> <blocker_id> <answer>` via Bash. "
         "If multiple blockers are open and the reply is ambiguous, ask "
         "the user which plan they mean — don't guess.\n"
     )
     ```

3. **Acceptance.**
   - 6 new tests green.
   - Existing `tests/test_inbox_hook.py` (truncation cap, mark-processed, corrupt-file handling) still green.
   - Manual smoke: in a project with a real blocker, `echo '' | python3 -m end_of_line.hooks.clu_inbox_surface | jq -r .hookSpecificOutput.additionalContext` shows the section.
   - No regression in `python3 -m end_of_line.cli list` blocker counts.

4. **Commit + complete.**
   - Title: `notify-multi-channel: phase inbox-surface — active-blocker section in hook`
   - Stage: `end_of_line/hooks/clu_inbox_surface.py`, `end_of_line/notify_base.py` (or wherever query landed), `tests/test_inbox_hook.py`.
   - `clu complete --plan notify-multi-channel --phase inbox-surface --token <T>`.

## Failure modes to watch

- **Context-size cap.** Hook truncates at 10K chars (existing test confirms). The 10-blocker cap is the safety belt; verify with a 12-blocker fixture.
- **Question with backticks/markdown.** Operator-supplied via `clu block --question`. Test: a question with backticks doesn't break formatting. If it does, escape rather than reject.
- **Test isolation.** Use `CluTestCase` + `isolate_registry` per project memory — otherwise tests pollute `~/.config/clu/`.
- **Race with answered blockers.** If a worker calls `clu answer` between query and output, blocker may close. Acceptable race; next hook fires clean.
- **Don't duplicate clu-watch surface.** `watch.py:_task_msg_for()` already emits BLOCKED events for `--task-list` consumers. This phase is the *inbox-hook* path for sessions without Monitor armed. Complementary, not duplicative.
