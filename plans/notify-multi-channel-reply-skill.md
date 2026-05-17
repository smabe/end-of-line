# notify-multi-channel-reply-skill — /clu-reply bundled skill

You are phase `reply-skill` of the `notify-multi-channel` plan. Add a bundled `/clu-reply` skill — explicit escape hatch for blocker replies when natural-language disambiguation isn't appropriate (multi-blocker ambiguity, scripted contexts).

## Locked decisions (do NOT re-litigate)

See `plans/notify-multi-channel.md` §"Phase 4". Summary:
- Location: `end_of_line/skills/clu-reply/SKILL.md` (markdown-only; Claude executes via Bash).
- Args: `<plan-slug> <answer>` (answer = 0-indexed option number or free text).
- Behavior: look up open blocker for plan, shell `clu answer --project . --plan <slug> <blocker_id> <answer>`.
- Add `"clu-reply"` to `BUNDLED_SKILLS` tuple in `cli.py:1426`.

## Read first

- `end_of_line/skills/clu-plan/SKILL.md` — bundled-skill convention (frontmatter, workflow, refusal).
- `end_of_line/skills/clu-monitor/SKILL.md` — second example, lighter shape.
- `end_of_line/cli.py` line 1426 (BUNDLED_SKILLS), lines 1486-1555 (`cmd_install_skill`).
- `end_of_line/cli.py` ~line 3131 (`cmd_answer`) — what the skill invokes.
- `tests/test_install_skill.py` — install-flow test patterns.
- `pyproject.toml` — confirm `package_data` includes `skills/**/*.md`.

## Produce

1. **Failing tests first** in `tests/test_clu_reply_skill.py`:
   - `test_clu_reply_in_bundled_skills` — `"clu-reply"` in `BUNDLED_SKILLS` tuple.
   - `test_clu_reply_skill_file_ships_with_package` — `importlib.resources.files("end_of_line").joinpath("skills/clu-reply/SKILL.md").read_text()` non-empty.
   - `test_clu_reply_skill_frontmatter_valid` — parse YAML frontmatter, assert `name == "clu-reply"` and `description` present.
   - `test_install_skill_installs_clu_reply` — with `HOME` redirected to `tmp_path`, invoke `cmd_install_skill(["--only", "clu-reply"])`; assert `~/.claude/skills/clu-reply/SKILL.md` exists.
   - `test_install_skill_list_shows_clu_reply` — `cmd_install_skill(["--list"])` output contains `clu-reply`.

2. **Implementation.**
   - `end_of_line/skills/clu-reply/SKILL.md`:
     ```markdown
     ---
     name: clu-reply
     description: Explicit blocker reply for clu plans. Use when natural-language reply via the inbox-surface isn't appropriate — multi-blocker disambiguation, scripted/non-interactive contexts. Args: `<plan-slug> <answer>` (answer is a 0-indexed option number or free text).
     ---

     # /clu-reply

     Explicit, unambiguous answer for an open clu blocker. Use when the in-session inbox-surface path needs help (multiple blockers open and ambiguous, you want to be precise, etc.).

     ## When to refuse

     - Not in a clu-managed project (no `.orchestrator.json` at repo root) → suggest the operator cd into a clu project first.
     - Plan slug not registered → list registered plans (`clu list`) and ask which.
     - No open blocker on the plan → say so, point at `clu list --plan <slug>`.

     ## Workflow

     1. Parse args: `$1` = plan slug, `$2..` = answer (rejoin if multi-word).
     2. Locate blocker_id — use the clu CLI command that exposes blocker IDs as JSON (verify against current cli.py; likely `clu list --plan <slug> --json` or similar).
     3. Fire the answer:
        ```bash
        clu answer --project . --plan "$plan" "$blocker_id" "$answer"
        ```
     4. Report result. On non-zero exit, surface clu's error verbatim.

     ## Examples

     - `/clu-reply notify-multi-channel 1` — answer option 1 on the most-recent blocker.
     - `/clu-reply auth-cleanup "yes, with the bcrypt path"` — free-text answer.
     ```
   - `end_of_line/cli.py` line 1426: insert `"clu-reply"` in alphabetic order:
     ```python
     BUNDLED_SKILLS = ("brainstorm", "clu-monitor", "clu-phase", "clu-plan", "clu-reply", "plan")
     ```

3. **Acceptance.**
   - 5 new tests green.
   - `clu install-skill --list` shows `clu-reply`.
   - `clu install-skill --only clu-reply --dry-run` previews `~/.claude/skills/clu-reply/SKILL.md`.
   - Existing `tests/test_install_skill.py` tests green.

4. **Commit + complete.**
   - Title: `notify-multi-channel: phase reply-skill — bundled /clu-reply skill`
   - Stage: `end_of_line/skills/clu-reply/SKILL.md`, `end_of_line/cli.py`, `tests/test_clu_reply_skill.py`.
   - `clu complete --plan notify-multi-channel --phase reply-skill --token <T>`.

## Failure modes to watch

- **`package_data` exclusion.** New `SKILL.md` must be in the wheel. Check `pyproject.toml` for `skills/**/*.md` glob. The `importlib.resources` test catches this.
- **Hard-coded blocker-id lookup.** Use whichever clu CLI command actually exposes blocker_ids as JSON (verify in cli.py before writing the skill body). Don't invent a non-existent command.
- **Don't add a `--token` flow.** `clu answer` is operator-side; no worker token. Skill calls plainly.
- **Skill executes via Bash, not Python.** The SKILL.md is instructions for Claude; no Python module to import.
