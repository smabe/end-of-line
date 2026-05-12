# bundle-plan-skill-install — multi-skill install + --only + no-clobber

You are phase `install` of the `bundle-plan-skill` plan. Phase
`layout` (already done by the time you run) put both skills in the
package at `end_of_line/skills/{clu-phase,plan}/SKILL.md`. Your job:
refactor `cmd_install_skill` to handle both, add a `--only <name>`
flag, and add the don't-clobber-non-symlinks safety the operator
chose.

## Locked decisions (do NOT re-litigate)

See `plans/bundle-plan-skill.md`. Summary:

- Default `clu install-skill` installs **both** skills. No prompt,
  no required flag for the happy path.
- `--only <name>` installs just one skill. Validate against known
  skill names; unknown name → clear error exit.
- `--force` continues to mean "overwrite anything, including
  non-symlinks". Already exists; extend semantics to per-skill.
- **Don't-clobber-non-symlinks safety:**
  - If target is a symlink (clu owns symlinks it wrote, and other
    symlinks are user-owned but harmless to replace) → existing
    `unlink before write` behavior; overwrite freely.
  - If target is a regular file (user-owned content) → REFUSE
    without `--force`. Exit with `ExitCode.STATUS_TRANSITION` and a
    message naming the path + suggesting `--force`.
  - Broken symlink (points nowhere) → treat as symlink, overwrite.
- The existing `--dry-run` flag continues to work; should print the
  planned action for each skill being installed.

## Read first

- `end_of_line/cli.py`, `cmd_install_skill` (around lines 347-377
  pre-refactor). The current implementation handles ONE skill. You
  rewrite it to iterate over a list:
  ```python
  BUNDLED_SKILLS = ("clu-phase", "plan")  # source of truth
  ```
  or a dict mapping name → resource path if you want explicit
  structure. Choose what reads cleanest.
- `end_of_line/cli.py` argparse setup for `install-skill` (around
  lines 107-119). You'll add `--only` here.
- `end_of_line/state.py` — `ExitCode` IntEnum. Use named codes, not
  bare ints (per CLAUDE.md "ExitCode IntEnum, never bare ints").
  Look for an existing code that fits "user gave bad input" (likely
  `BAD_REQUEST` or similar) for the `--only unknown_name` case.
- `tests/test_install_skill.py` — the test patterns. The existing
  tests assume single-skill semantics. You'll extend them and add
  new tests for the new behavior.

## Produce

1. **TDD: failing tests first.** Add to `tests/test_install_skill.py`:

   - **`test_default_installs_both_skills`** — fresh tmp HOME,
     `clu install-skill` (no args) creates BOTH
     `~/.claude/skills/clu-phase/SKILL.md` and
     `~/.claude/skills/plan/SKILL.md`, both match bundled bytes.
   - **`test_only_clu_phase`** — `--only clu-phase` creates
     ONLY clu-phase target, plan target does not exist.
   - **`test_only_plan`** — `--only plan` creates ONLY plan target,
     clu-phase does not exist.
   - **`test_only_unknown_name_exits_clean`** — `--only banana`
     exits non-zero with a message listing valid skill names.
     Neither target gets created.
   - **`test_refuses_to_overwrite_non_symlink_without_force`** —
     pre-create `~/.claude/skills/plan/SKILL.md` as a regular file
     with sentinel content. `clu install-skill` exits non-zero, the
     sentinel content is preserved, and the OTHER skill (clu-phase)
     was NOT installed either (one-failure-aborts-all OR per-skill
     skip-and-continue — see semantic decision below).
   - **`test_overwrites_symlink_without_force`** — pre-create the
     target as a symlink to a tempfile. `clu install-skill` succeeds,
     target is a regular file with bundled bytes (or a new symlink —
     check current code behavior; should match).
   - **`test_force_overwrites_non_symlink`** — pre-create target as
     a regular file. `clu install-skill --force` succeeds, target
     now has bundled bytes.
   - **`test_dry_run_prints_both_destinations`** — `--dry-run` lists
     both intended writes, neither file is created.

   Run the suite. All seven new tests must FAIL before implementation
   (one or two existing tests may also need updating to match the
   new default-installs-both behavior — that's expected).

2. **Implementation decision: one-failure-aborts vs skip-and-continue.**
   When one skill's target is a regular file (refuse without
   `--force`), what happens to the OTHER skill in a `clu install-skill`
   no-args run? Two options:
   - **Abort-all**: first refusal stops the whole run, neither skill
     installs. Cleaner mental model ("install-skill is atomic").
   - **Skip-and-continue**: install the safe one, report which was
     skipped. More user-friendly but ambiguous exit code.

   **Default: abort-all.** It's atomic, the exit code is unambiguous,
   and the operator's stated rule was "no clobber" — which reads as
   "be strict." Document this in the message:
   > "Refusing to overwrite ~/.claude/skills/plan/SKILL.md (regular
   > file, not a symlink clu owns). Pass --force to overwrite, or
   > --only clu-phase to skip /plan. No skills were installed."

   The test `test_refuses_to_overwrite_non_symlink_without_force`
   above already encodes this (asserts NEITHER skill was installed).

3. **Refactor `cmd_install_skill`.** Skeleton:

   ```python
   BUNDLED_SKILLS = ("clu-phase", "plan")

   def cmd_install_skill(args) -> int:
       from importlib.resources import files

       skills_to_install = (
           (args.only,) if args.only else BUNDLED_SKILLS
       )
       if args.only and args.only not in BUNDLED_SKILLS:
           return _die(
               ExitCode.BAD_REQUEST,  # or whichever code is canonical
               f"unknown skill {args.only!r}; valid: {', '.join(BUNDLED_SKILLS)}",
           )

       # Pre-flight: check ALL targets for non-symlink collisions before
       # writing any. This enforces abort-all atomicity.
       plans = []
       for name in skills_to_install:
           bundled = files("end_of_line").joinpath(f"skills/{name}/SKILL.md")
           target = Path.home() / ".claude" / "skills" / name / "SKILL.md"
           is_symlink = target.is_symlink()
           exists = is_symlink or target.exists()
           if exists and not is_symlink and not args.force:
               return _die(
                   ExitCode.STATUS_TRANSITION,
                   f"refusing to overwrite {target} (regular file). "
                   f"Pass --force to overwrite, or --only <other> to skip.",
               )
           plans.append((name, bundled, target, exists))

       if args.dry_run:
           for name, bundled, target, exists in plans:
               verb = "Would overwrite" if exists else "Would write"
               print(f"{verb} {target} from bundled {bundled}")
           return ExitCode.OK

       for name, bundled, target, exists in plans:
           target.parent.mkdir(parents=True, exist_ok=True)
           if exists:
               target.unlink()
           target.write_bytes(bundled.read_bytes())
           print(f"Installed {name} skill to {target}")
       return ExitCode.OK
   ```

   Adjust to match the existing codebase style (the snippet is
   illustrative — check the actual `ExitCode` names and the
   `_die` signature).

4. **Argparse: add `--only`.** In the parser setup around line 107:
   ```python
   p_install_skill.add_argument(
       "--only",
       choices=BUNDLED_SKILLS,
       help="Install only the named skill (default: both).",
   )
   ```
   Using `choices=` gives argparse-level validation for free, but
   ALSO add the runtime check inside `cmd_install_skill` (defense
   in depth, and matches the test
   `test_only_unknown_name_exits_clean` which expects an `ExitCode`
   exit, not an argparse SystemExit). Pick one path — `choices=` is
   simpler if the test tolerates argparse's exit; otherwise drop
   `choices=` and do runtime validation only.

5. **Update the help text** for the install-skill subcommand
   (around line 109) to reflect both skills:
   ```python
   help="Copy bundled skills (/clu-phase worker + /plan authorship) "
        "into ~/.claude/skills/<name>/SKILL.md so Claude Code can "
        "find them. Default installs both; use --only to install one.",
   ```

6. **Run the full suite.** All seven new tests pass. Existing tests
   either pass unchanged or were updated to match the new
   default-installs-both behavior. Expect total count ~232-236.

7. **`/simplify`.** This phase has a real refactor — multi-step
   logic change across cli.py + tests. Run `/simplify` on the diff
   per CLAUDE.md ("/simplify after non-trivial work — diffs >1 file
   or ~30 lines").

8. **Commit** with structure:
   - Title: `bundle-plan-skill phase 2: install-skill handles both bundled skills`
   - Why: clu now ships two skills; install-skill must place both
     by default and protect user-owned files at the targets.
   - What's new:
     - `--only <name>` flag with validation
     - Default-install-both with atomic abort-all on non-symlink
       collision
     - Don't-clobber-non-symlinks safety
   - Under the hood: pre-flight pass checks all targets before any
     write so the operation is atomic; symlinks are still freely
     overwritten (clu owns the ones it wrote).
   - Tests: 7 new tests; full suite ≥232 green.
   - Co-Authored-By trailer.

9. **Mandate #9 re-verify.** Re-run the full suite from a clean
   process right before calling `clu complete`. Report count + delta.

## Failure modes to watch for

- **`choices=` vs runtime validation conflict.** If argparse exits
  via `SystemExit(2)` on an unknown `--only` value, the test that
  expects an `ExitCode` won't pass. Either:
  - Drop `choices=` and rely on the runtime check (simpler, matches
    the test's expectation)
  - Keep `choices=` and rewrite the test to assert SystemExit
  Operator-friendly default: drop `choices=`, runtime-check only.
- **`_die` signature variance.** Existing code uses
  `_die(ExitCode.X, msg)`. Make sure that's what you call. Check
  one existing usage in cli.py for the canonical pattern.
- **Pre-flight skip on existing-symlink targets.** A non-existent
  target should NOT trip the refusal — only an existing regular
  file does. Double-check the predicate:
  `exists AND NOT is_symlink AND NOT args.force`.
- **`Path.home() / ".claude"` in tests.** Tests patch `$HOME` per
  `InstallSkillTestBase.setUp` — make sure `Path.home()` honors the
  patch (it should, since Python's `Path.home()` reads `$HOME`).
- **Importlib resources on the new layout.** Verify
  `files("end_of_line").joinpath("skills/plan/SKILL.md")` actually
  resolves to the bundled file. Phase 1 should have already
  guaranteed this via its packaging check; if not, you'll get
  `FileNotFoundError` here.

## Done criteria for this phase

- `cmd_install_skill` handles both skills with the pre-flight
  atomic pattern.
- `--only <name>` flag works for both `clu-phase` and `plan`,
  rejects unknown names cleanly.
- Don't-clobber-non-symlinks safety in place; symlink overwrites
  still work; `--force` overrides.
- 7 new tests pass; full suite green from a clean process.
- One commit, structured message, no `Fixes` trailer.
- `clu complete` with token + SHA + count summary.
