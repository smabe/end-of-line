# install-skill-list-impl — `--list` flag for clu install-skill

You are the only phase of the `install-skill-list` plan. Closes
GitHub issue [#13](https://github.com/smabe/end-of-line/issues/13).

Read the master plan for context, then do exactly what's below.

## Locked decisions (do NOT re-litigate)

- Source of truth: `BUNDLED_SKILLS` at `end_of_line/cli.py:506`.
- Target path formula: `Path.home() / ".claude" / "skills" / name /
  "SKILL.md"` (matches the existing `cmd_install_skill` body).
- Output: header + one aligned row per skill. `str.ljust` is fine.
- Exits `ExitCode.OK`. No writes.
- Short-circuit at top of `cmd_install_skill` when `args.list` is set.
  Don't bother with argparse mutex groups; the early return is
  simpler and easier to test.

## Read first

- `end_of_line/cli.py:506` — `BUNDLED_SKILLS` tuple.
- `end_of_line/cli.py:566-621` — `cmd_install_skill` body. Your
  early-return goes at the top.
- `end_of_line/cli.py` — find the `install-skill` subparser (search
  for `install-skill` or `install_skill`). Add `--list` there.
- `tests/test_install_skill.py` — existing test patterns, especially
  HOME monkeypatching.

## Produce

1. **TDD: failing test first.** Add to `tests/test_install_skill.py`:

   - `test_list_prints_bundled_skills_with_target_paths` — invoke
     `main(["install-skill", "--list"])` (or whatever the existing
     tests use for entry). Capture stdout. Assert:
     - Header line containing "Bundled skills" appears.
     - Each name in `BUNDLED_SKILLS` appears in the output.
     - For each name, the expected target path
       (`Path.home() / ".claude" / "skills" / name / "SKILL.md"`)
       appears on the same line as the name.
     - Exit code is `ExitCode.OK`.
     - No filesystem writes — assert `~/.claude/skills/` (or the
       tmp-HOME equivalent) is unchanged after the call.

   Run suite — new test must FAIL.

2. **Add `--list` to the subparser** alongside `--only`, `--force`,
   `--dry-run`:

   ```python
   p_install.add_argument(
       "--list", action="store_true",
       help="List bundled skills and their install targets, then exit.",
   )
   ```

3. **Early-return branch** at the top of `cmd_install_skill`:

   ```python
   if args.list:
       targets = [
           (name, Path.home() / ".claude" / "skills" / name / "SKILL.md")
           for name in BUNDLED_SKILLS
       ]
       width = max(len(name) for name, _ in targets)
       print("Bundled skills available via clu install-skill:")
       for name, target in targets:
           print(f"  {name.ljust(width)}  {target}")
       return ExitCode.OK
   ```

4. **Run the suite — all green.**

5. **`/simplify`** — optional for ~10 LOC, but if you touched anything
   else, run it.

6. **Commit.** Title: `install-skill: add --list to enumerate bundled
   skills`. Body references `closes #13`.

## Verification before `clu complete`

Per CLAUDE.md mandate #9: re-run `python3 -m unittest discover -s tests`
right before `clu complete`. Report pass/fail and final test count
in the summary.

## Acceptance

- [ ] `clu install-skill --list` prints each name + target path
- [ ] Exits `ExitCode.OK`, no filesystem writes
- [ ] New test passes; full suite green
- [ ] One commit with `closes #13` in body
