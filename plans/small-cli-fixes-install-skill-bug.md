# small-cli-fixes-install-skill-bug — install-skill directory-symlink fix (#23)

You are phase `install-skill-bug` of the `small-cli-fixes` plan. Fix the
install-skill command so it warns (not silently writes through) when the parent
directory of the target `SKILL.md` is a symlink. The operator's actual setup
deliberately symlinks `~/.claude/skills/<name>` → `~/projects/abe-skills/
skills/<name>`, so follow-into is the correct UX; the warning is the signal
that the symlink behavior triggered.

## Locked decisions (do NOT re-litigate)

See `plans/small-cli-fixes.md`. Summary:

- **Bug site:** `cmd_install_skill` at cli.py:1340-1405, specifically the
  `target.parent.mkdir(parents=True, exist_ok=True)` at line 1389 which
  follows directory symlinks.
- **Fix:** at the pre-flight validation block (around cli.py:1371-1380),
  additionally check `target.parent.is_symlink()`. If True, emit warning to
  stderr (specifically: `print(f"warning: {target.parent} is a symlink → "
  f"{target.parent.resolve()}; install-skill will write through",
  file=sys.stderr)`). Then proceed — the symlink-follow is intentional.

## Read first

- `end_of_line/cli.py:1340-1405` — `cmd_install_skill` full body. Understand
  the two-phase structure (pre-flight validation → write). Your touch is in
  pre-flight.
- `end_of_line/cli.py:1371-1380` — the existing symlink check (only
  inspects the SKILL.md file, not its parent).
- `tests/test_install_skill.py` (around line 189-239) —
  `SymlinkTargetTests` and `HardlinkTargetTests` show the existing
  fs-mocking pattern. Mirror their shape for your new directory-symlink
  test.

## Produce

1. **Failing test first.** Add to `tests/test_install_skill.py`:
   `test_install_skill_warns_on_directory_symlink`:
   ```python
   def test_install_skill_warns_on_directory_symlink(self):
       canonical = self.tmp_path / "canonical"
       canonical.mkdir()
       linked = self.tmp_path / "linked"
       linked.symlink_to(canonical)
       target = linked / "SKILL.md"
       # ... run install-skill with target.parent being `linked` ...
       # Assert: stderr contains "warning" and "symlink"
       # Assert: install completes successfully (canonical/SKILL.md exists)
   ```
   Use `unittest.mock.patch('sys.stderr', new_callable=io.StringIO)` to
   capture stderr. The test will fail because the warning doesn't exist yet
   (the install will silently succeed without warning).

2. **Implementation: pre-flight warning.**
   In `cmd_install_skill` at the pre-flight validation block (after the
   existing `is_symlink` check for SKILL.md itself, before the iteration
   ends), add:
   ```python
   if target.parent.is_symlink():
       print(
           f"warning: {target.parent} is a symlink → "
           f"{target.parent.resolve()}; install-skill will write through",
           file=sys.stderr,
       )
   ```
   Then proceed normally (no change to the write phase).

3. **Acceptance.**
   - New test passes; assert stderr captured contains "warning" and
     "symlink".
   - Existing `SymlinkTargetTests` and `HardlinkTargetTests` still pass
     (regression check — your change adds a warning but doesn't change
     control flow).
   - Full suite green.

4. **Commit + complete.**
   - Structured commit: `small-cli-fixes: phase install-skill-bug —
     directory-symlink follow-with-warning (#23)`.
   - Stage: `end_of_line/cli.py`, `tests/test_install_skill.py`.
   - `clu complete --plan small-cli-fixes --phase install-skill-bug --token
     <T>`.

## Failure modes to watch

- **Test pollution from symlinks** — `tmp_path` from `CluTestCase` should
  be cleaned automatically; verify the symlink is REMOVED after the test
  (Python's tempdir cleanup handles symlinks correctly, but if your test
  creates a circular symlink it could hang).
- **Existing tests using `cmd_install_skill` with non-symlinked parents** —
  must still pass without spurious warnings. Verify by running the full
  install-skill test file (`python3 -m unittest tests.test_install_skill`)
  and confirming no NEW stderr output appears (capture and assert empty
  in a regression test if any existing test is silent).
- **The warning should NOT change the exit code** — the install still
  succeeds. Verify exit code 0 (or `ExitCode.OK`) in the new test.
- **`sys.stderr` capture pattern** — `unittest.mock.patch('sys.stderr',
  new_callable=io.StringIO)` is one option; the existing test file may
  use a different idiom. Match what's already there.
