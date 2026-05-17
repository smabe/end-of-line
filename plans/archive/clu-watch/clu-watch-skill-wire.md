# clu-watch-skill-wire — `/clu-plan` arms Monitor; `/clu-monitor` mentions watch

You are phase `skill-wire` of `clu-watch`. Update two bundled SKILL.md
files: `/clu-plan` gains a Monitor-arming step in its workflow;
`/clu-monitor` gains a "live channel" note pointing at `clu watch`.
No Python code change.

## Locked decisions (do NOT re-litigate)

See `plans/clu-watch.md` § Phase 5. Summary:
- Edit the bundled sources at `end_of_line/skills/clu-plan/SKILL.md`
  and `end_of_line/skills/clu-monitor/SKILL.md`, NOT the symlinked
  copies under `~/.claude/skills/`.
- After this phase ships, `clu install-skill --force` re-installs
  the user-level symlinks.

## Read first

- `end_of_line/skills/clu-plan/SKILL.md` — current step 5 ("On
  `ship`, write files + optionally init/queue") + worked example.
- `end_of_line/skills/clu-monitor/SKILL.md` — current "How the
  surfacing works" section.
- `end_of_line/cli.py:cmd_install_skill` — verify what files the
  installer copies (smoke check that the edit will reach users).

## Produce

1. **Failing tests first** (`tests/test_skill_wire.py`, new or
   extend `test_install_skill.py`):
   - `test_clu_plan_skill_mentions_monitor_watch` — read the
     bundled SKILL.md at `end_of_line/skills/clu-plan/SKILL.md`,
     assert it contains the substring `clu watch` AND `Monitor(`.
   - `test_clu_monitor_skill_mentions_watch_sibling` — read the
     bundled SKILL.md at `end_of_line/skills/clu-monitor/SKILL.md`,
     assert it contains `clu watch` AND a note about live vs
     inbox.
   - `test_install_skill_dry_run_shows_clu_plan_update_path` —
     call `main(["install-skill", "--only", "clu-plan",
     "--dry-run"])`, assert stdout names the target path.
     (Regression guard; install path didn't change.)
   - These are file-content assertions, not behavior tests. Cheap
     and durable.

2. **Implementation.**
   - `end_of_line/skills/clu-plan/SKILL.md` § Step 5:
     - After the existing `clu queue list --project .` confirmation
       step, insert:
       ```markdown
       6. **Arm live progress monitoring** via the Monitor tool:
          ```
          Monitor(
              description="clu <slug> phase progress",
              persistent=True,
              timeout_ms=3600000,
              command="clu watch --project . --plan <slug>"
          )
          ```
          Each state transition (phase started/completed/blocked/halted)
          arrives as a notification, so you see what clu is doing
          without polling. The operator's UserPromptSubmit hook
          handles AFK surfacing separately; this is the at-desk
          live feed.
       ```
     - Update the worked example's closing block to include the
       Monitor arming.
   - `end_of_line/skills/clu-monitor/SKILL.md` § "How the
     surfacing works":
     - Add a paragraph at the end:
       ```markdown
       ## Live in-session feed (`clu watch`)

       The inbox hook is the *AFK* channel — it batches events into
       the next user prompt. For *live* streaming while the
       operator is at-desk, use `clu watch` inside Claude's
       Monitor tool:
       ```
       Monitor(command="clu watch --project . --all", persistent=True)
       ```
       Each state transition emits one stdout line, surfaced as a
       notification. The two channels are complementary: inbox for
       the walk-away path, watch for the live-feed path.
       ```

3. **Acceptance.**
   - 3 new tests green.
   - Phases events / stream / cli / tips tests still green.
   - Full suite green.
   - Manual smoke: `clu install-skill --only clu-plan --dry-run`
     names the target path; `clu install-skill --only clu-plan
     --force` succeeds (operator runs this; tests don't).

4. **Commit + complete.**
   - Title: `clu-watch: phase skill-wire — /clu-plan arms Monitor,
     /clu-monitor mentions watch`
   - Stage: `end_of_line/skills/clu-plan/SKILL.md`,
     `end_of_line/skills/clu-monitor/SKILL.md`,
     `tests/test_skill_wire.py` (or extension).
   - `clu complete --plan clu-watch --phase skill-wire --token <T>`

## Failure modes to watch

- **Editing the symlink target instead of the source** — the
  user-level path at `~/.claude/skills/clu-plan/SKILL.md` is a
  symlink. Edit the bundled source under
  `end_of_line/skills/...` only. Verify with `ls -la` on the
  user-level path before edit.
- **Skill content drift** — `/clu-plan` already has substantive
  workflow steps; insertion should slot in cleanly without
  duplicating step numbers. Re-read step 5 first to find the
  right insertion point.
- **Operator must re-install** — note in the commit message that
  operators with existing `~/.claude/skills/` symlinks need
  `clu install-skill --force --only clu-plan` (and --only
  clu-monitor) to pick up the new SKILL.md content. Symlinks
  don't auto-update content; the install command rewrites them.
