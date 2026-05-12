# bundle-plan-skill-layout — package layout for multi-skill bundling

You are phase `layout` of the `bundle-plan-skill` plan. Your job is
purely structural: rename the current singular skill directory to
plural, add a second skill, and update the lone path reference in
`cli.py` so existing behavior keeps working. No install-skill logic
changes in this phase — that's phase `install`.

## Locked decisions (do NOT re-litigate)

See `plans/bundle-plan-skill.md`. Summary:

- Final layout: `end_of_line/skills/clu-phase/SKILL.md` and
  `end_of_line/skills/plan/SKILL.md` (plural `skills/`, one subdir
  per skill).
- `/plan` source: `~/projects/abe-skills/skills/plan/SKILL.md`
  (operator's private repo). Frozen clone — drift from upstream is
  acceptable and documented in a header note.

## Read first

- `end_of_line/skill/SKILL.md` — the current `/clu-phase` worker
  skill. After this phase, this file moves to
  `end_of_line/skills/clu-phase/SKILL.md` (intermediate `skill/`
  directory is removed).
- `end_of_line/cli.py:350` — the only `files(...).joinpath(...)`
  reference to the bundled skill. Update to the new path.
- `tests/test_install_skill.py` — has TWO references to the singular
  path (lines around 31 and any other `joinpath("skill/SKILL.md")`
  uses). `grep -n "skill/SKILL.md" tests/test_install_skill.py` to
  enumerate. Update each to `skills/clu-phase/SKILL.md`.
- `pyproject.toml` — verify it includes `skills/**` (or the equivalent)
  in `package_data` / `tool.setuptools.package-data`. If it currently
  lists `skill/SKILL.md` explicitly, update to a glob that covers the
  new layout. Run `python3 -c "from importlib.resources import files;
  print(files('end_of_line').joinpath('skills/clu-phase/SKILL.md').read_text()[:80])"`
  after the move to confirm packaging picks it up.
- `~/projects/abe-skills/skills/plan/SKILL.md` — the source for the
  bundled `/plan`. If this file does NOT exist on the worker's
  machine, BLOCK with a clear question (`clu block`). Do not commit a
  partial layout.

## Produce

1. **Pre-flight: confirm `/plan` source exists.**
   ```bash
   test -f ~/projects/abe-skills/skills/plan/SKILL.md \
       && echo OK \
       || (echo MISSING; exit 1)
   ```
   If MISSING, call `clu block` with the question:
   > "Phase `layout` can't proceed — the bundled `/plan` source at
   > `~/projects/abe-skills/skills/plan/SKILL.md` is missing on this
   > host. Operator: confirm the path, or paste the SKILL.md content
   > inline as a reply."
   Then exit without further edits.

2. **Move the existing skill.**
   ```bash
   git mv end_of_line/skill end_of_line/skills
   git mv end_of_line/skills/SKILL.md end_of_line/skills/clu-phase-SKILL.md.tmp  # workaround if needed
   mkdir -p end_of_line/skills/clu-phase
   git mv end_of_line/skills/clu-phase-SKILL.md.tmp end_of_line/skills/clu-phase/SKILL.md
   ```
   Use the simplest two-step rename your shell allows. The end state
   is what matters: `end_of_line/skills/clu-phase/SKILL.md` tracked
   in git, `end_of_line/skill/` gone.

3. **Copy `/plan` from abe-skills.**
   ```bash
   mkdir -p end_of_line/skills/plan
   cp ~/projects/abe-skills/skills/plan/SKILL.md end_of_line/skills/plan/SKILL.md
   ```
   Then prepend a header note (insert BEFORE any existing content,
   keep an empty line between the note and the original first line):

   ```markdown
   <!--
   This is a frozen clone of the operator's `/plan` skill, bundled
   with clu so installs are self-contained. The canonical version
   may drift in the operator's private skills repo. To replace this
   bundled copy with a symlink to your own version, run
   `clu install-skill --only plan --force` after putting your
   SKILL.md at ~/.claude/skills/plan/SKILL.md.
   -->
   ```

4. **Update `cli.py:350`.** Change:
   ```python
   bundled = files("end_of_line").joinpath("skill/SKILL.md")
   ```
   to:
   ```python
   bundled = files("end_of_line").joinpath("skills/clu-phase/SKILL.md")
   ```
   Do NOT add `/plan` handling here — that's phase `install`'s job.
   This phase keeps the existing single-skill behavior intact at the
   new path.

5. **Update `tests/test_install_skill.py` path references.**
   ```bash
   grep -n "skill/SKILL.md" tests/test_install_skill.py
   ```
   Replace each hit with the new path. The tests should still pass
   unchanged in semantics — they're just reading from the new
   location.

6. **Verify packaging still finds the skill.** Run:
   ```bash
   python3 -c "from importlib.resources import files; print(len(files('end_of_line').joinpath('skills/clu-phase/SKILL.md').read_bytes()))"
   ```
   Expect a non-zero byte count. If `FileNotFoundError`, the
   `pyproject.toml` package_data needs updating — add or extend the
   glob to cover `skills/**/SKILL.md`. If you edit `pyproject.toml`,
   reinstall the package: `pipx install -e . --force` OR
   `pip install -e .` depending on environment. Document any
   `pyproject.toml` change in the commit message.

7. **Run the full test suite.**
   ```bash
   python3 -m unittest discover -s tests
   ```
   All existing tests (≥229 after worker-path-config docs phase)
   pass at the new path. No new tests in this phase — phase `install`
   adds them.

8. **`/simplify`** — likely a single-file substantive change in
   `cli.py` plus mechanical test path updates. Trivial-diff escape
   hatch applies if total diff is mechanical. Skip unless the diff
   ballooned.

9. **Commit** with this structure:
   - Title: `bundle-plan-skill phase 1: skills/ plural layout`
   - Why: package now ships two skills (`/clu-phase` worker + `/plan`
     authorship); flat `skill/` directory becomes
     `skills/<name>/SKILL.md` to accommodate both. Layout-only —
     install-skill behavior unchanged in this phase.
   - What's new:
     - `end_of_line/skills/clu-phase/SKILL.md` (renamed from
       `end_of_line/skill/SKILL.md`)
     - `end_of_line/skills/plan/SKILL.md` (frozen clone from
       operator's abe-skills, with drift-note header)
     - `cli.py:350` updated to new path
     - `tests/test_install_skill.py` path references updated
     - `pyproject.toml` package_data updated IF needed (note in
       message whether this was required)
   - Tests: full suite green (≥229).
   - Co-Authored-By trailer (use the model name from your skill's
     mandate 3 template).

10. **Run the suite once more from a clean process** per mandate #9,
    then call `clu complete` with the worker token. Summary should
    include the commit SHA, test count, and confirmation that
    `pyproject.toml` was/wasn't touched.

## Failure modes to watch for

- **`pyproject.toml` doesn't auto-include the new dir.** Setuptools
  typically picks up `package_data` via globs in `[tool.setuptools.package-data]`.
  If the existing entry is `"end_of_line" = ["skill/SKILL.md"]` (an
  explicit path), you MUST update it. If it's a glob like `["**/*.md"]`,
  you're fine. Verify via the importlib.resources check in step 6.
- **Reinstall required.** After moving package_data, the existing
  `pipx install -e .` symlinks may not pick up the new files
  immediately. Run `pipx reinstall end-of-line` or
  `pipx install -e . --force` if step 6's check fails.
- **`git mv` of a directory.** Some git versions don't `mv` directories
  cleanly. If `git mv end_of_line/skill end_of_line/skills` fails,
  use plain `mv` and then `git add -A` (BUT — per CLAUDE.md, "No
  `git add -A` — stage explicit paths." So use `git rm` on the old
  path and `git add` on the new path explicitly).
- **Worker reads from wrong abe-skills user.** The path is `~/projects/abe-skills/...`,
  expanding `~` to the worker's `$HOME`. If clu's LaunchAgent sets
  HOME unexpectedly, the path resolves wrong. Test: `echo $HOME &&
  ls -la $HOME/projects/abe-skills/skills/plan/SKILL.md` before
  attempting the copy.

## Done criteria for this phase

- `end_of_line/skills/clu-phase/SKILL.md` exists, tracked, contents
  identical to the previous `end_of_line/skill/SKILL.md`.
- `end_of_line/skills/plan/SKILL.md` exists, tracked, has the
  drift-note header followed by the original content from abe-skills.
- `end_of_line/skill/` directory is gone.
- `cli.py` references the new path.
- Tests pass full suite from a clean process.
- One commit, structured message, no `Fixes` trailer.
- `clu complete` called with token + SHA + count summary.
