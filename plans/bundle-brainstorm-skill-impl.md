# bundle-brainstorm-skill-impl — copy + wire + tests + README

You are phase `impl` of the `bundle-brainstorm-skill` plan. Phase
sole — copy `/brainstorm` from abe-skills, add to `BUNDLED_SKILLS`,
extend tests, rewrite README. One commit at the end.

## Locked decisions (do NOT re-litigate)

See `plans/bundle-brainstorm-skill.md`. Summary:

- Depends on `bundle-plan-skill` having shipped. `BUNDLED_SKILLS`
  tuple must already exist in `end_of_line/cli.py`.
- `/brainstorm` source:
  `~/projects/abe-skills/skills/brainstorm/SKILL.md`. Frozen clone
  with drift-note header (same pattern as `/plan`).
- README pitch:
  **brainstorm → grill-me → plan → clu** as the recommended pre-clu
  workflow. Combo is recommended, not mandatory.

## Read first

- `end_of_line/cli.py` — locate `BUNDLED_SKILLS`. Confirm it exists
  and is `("clu-phase", "plan")`. If it's NOT in that shape (e.g.
  bundle-plan-skill shipped a different structure), block with a
  clear question.
- `end_of_line/skills/clu-phase/SKILL.md` and
  `end_of_line/skills/plan/SKILL.md` — verify both exist (they
  should after bundle-plan-skill ships).
- `tests/test_install_skill.py` — patterns for skill install tests,
  especially the parametric ones bundle-plan-skill phase 2 added
  for multi-skill behavior.
- `README.md` — find the "Working with clu" section bundle-plan-skill
  phase 3 added. You'll extend (not rewrite from scratch) to fold in
  brainstorm.
- `~/projects/abe-skills/skills/brainstorm/SKILL.md` — source for
  the bundled copy. Pre-flight check it exists; block if missing.

## Produce

1. **Pre-flight: confirm `/brainstorm` source.**
   ```bash
   test -f ~/projects/abe-skills/skills/brainstorm/SKILL.md \
       && echo OK \
       || (echo MISSING; exit 1)
   ```
   If MISSING, call `clu block` with the question:
   > "Phase `impl` can't proceed — bundled `/brainstorm` source at
   > `~/projects/abe-skills/skills/brainstorm/SKILL.md` is missing
   > on this host. Operator: confirm path or paste SKILL.md content
   > inline."
   Exit without further edits.

2. **Verify `BUNDLED_SKILLS` exists and is what we expect.**
   ```bash
   grep -n "BUNDLED_SKILLS" end_of_line/cli.py
   ```
   Expect a tuple like `("clu-phase", "plan")`. If absent, block
   with a clear question; bundle-plan-skill probably didn't ship.

3. **Copy `/brainstorm` from abe-skills.**
   ```bash
   mkdir -p end_of_line/skills/brainstorm
   cp ~/projects/abe-skills/skills/brainstorm/SKILL.md end_of_line/skills/brainstorm/SKILL.md
   ```
   Prepend the drift-note header (insert BEFORE existing content,
   blank line between):
   ```markdown
   <!--
   This is a frozen clone of the operator's `/brainstorm` skill,
   bundled with clu so installs are self-contained. The canonical
   version may drift in the operator's private skills repo. To
   replace this bundled copy with a symlink to your own version,
   run `clu install-skill --only brainstorm --force` after putting
   your SKILL.md at ~/.claude/skills/brainstorm/SKILL.md.
   -->
   ```

4. **Extend `BUNDLED_SKILLS` in `end_of_line/cli.py`.**
   ```python
   BUNDLED_SKILLS = ("clu-phase", "plan", "brainstorm")
   ```
   If the install-skill subcommand's `--only` arg uses `choices=`,
   that argparse choices list updates automatically (it iterates
   `BUNDLED_SKILLS`). If it has the choices hardcoded, update there
   too.

5. **Update the install-skill help text.** Should now mention three
   skills:
   ```python
   help="Copy bundled skills (/clu-phase worker, /plan authorship, "
        "/brainstorm pre-planning) into ~/.claude/skills/<name>/SKILL.md "
        "so Claude Code can find them. Default installs all three; "
        "use --only to install one.",
   ```

6. **TDD: failing test first.** Add to `tests/test_install_skill.py`:
   - **`test_default_installs_all_three`** — fresh tmp HOME,
     `clu install-skill` creates all three targets, bytes match
     bundled.
   - **`test_only_brainstorm`** — `--only brainstorm` creates ONLY
     brainstorm target; clu-phase and plan targets do not exist.
   - The existing `test_default_installs_both_skills` (or similar
     name from bundle-plan-skill phase 2) needs renaming or
     updating to reflect three skills. Don't delete — adapt.

7. **Implement.** With `BUNDLED_SKILLS` extended in step 4, the
   install-skill logic should already iterate correctly (assuming
   bundle-plan-skill phase 2 implemented it generically). Verify
   by running the new tests; if they fail because the iteration
   hardcoded two skills, that's a bundle-plan-skill regression to
   fix (one-line tuple update is the entire fix).

8. **Rewrite the README "Working with clu" section** to pitch the
   combo. Read what's there now; extend rather than replace. Target
   content (adapt voice):

   ```markdown
   ## Working with clu

   clu bundles three skills with `clu install-skill`:

   - **`/clu-phase`** — the worker skill clu's dispatch invokes for
     each phase. Required for clu to function; you don't run this
     skill directly.
   - **`/plan`** — the authorship skill for writing plans clu can
     orchestrate. Drops a file at `plans/<slug>.md` in your project
     with a `## Sessions index` table.
   - **`/brainstorm`** — multi-persona parallel exploration. Useful
     before `/plan` when the problem space is fuzzy and you want a
     master plan instead of a guess.

   Run `clu install-skill` to drop all three into `~/.claude/skills/`.
   Pass `--only <name>` to install just one, or `--force` to
   overwrite existing user files at those paths.

   ### Recommended workflow

   For non-trivial work, the combo is **brainstorm → grill-me →
   plan → clu**:

   1. `/brainstorm` — parallel personas explore the design space and
      consolidate into a master plan.
   2. `/grill-me` ([Matt Pocock's skill](https://github.com/mattpocock/skills),
      installed separately) — interview yourself until each
      decision branch is resolved.
   3. `/plan` — commit the agreed approach to `plans/<slug>.md`
      with the Sessions index clu's parser expects.
   4. `clu init` — hand it to clu, which dispatches each phase as a
      cold-context worker subprocess.

   Each skill is independent — use one, all four, or none. The combo
   just makes ambitious work less likely to drift mid-flight.

   ### Minimum plan shape clu can orchestrate

   clu's parser requires the master plan file (`plans/<slug>.md`) to
   contain a `## Sessions index` table:

   | Session | Plan file | Scope | Effort |
   |---|---|---|---|
   | phase-a | `<slug>-phase-a.md` | <one-line scope> | <time est> |
   | phase-b | `<slug>-phase-b.md` | <one-line scope> | <time est> |

   Each row points to a sub-plan file in the same `plans/`
   directory. The bundled `/plan` skill produces this shape by
   default.
   ```

   Preserve any existing structure / TOC anchors. Match the file's
   prose voice.

9. **Run the test suite from a clean process** per mandate #9.
   Confirm count delta matches expectations (≥2 new tests passing;
   any renamed tests pass).

10. **`/simplify`** — diff likely spans cli.py + tests + README.
    Substantive, not trivial. Run `/simplify`.

11. **Commit.** Title:
    `bundle-brainstorm-skill: third bundled skill + combo README`.
    Structured message (Title / Why / What's new / Under the hood /
    Tests / Co-Authored-By trailer). No `Fixes` trailer — no open
    issue to close.

12. **Re-run suite once more from a clean process** per mandate #9,
    then call `clu complete` with token + SHA + count.

## Failure modes to watch for

- **`BUNDLED_SKILLS` is a list, not a tuple.** Doesn't matter
  semantically but check the syntax matches existing style before
  editing.
- **README section drift.** If the operator hand-edited the
  "Working with clu" section between bundle-plan-skill shipping and
  this phase starting, your rewrite could blow away their edits.
  Read the file fresh; don't trust the brief above as a literal
  diff target.
- **Test renames break references.** If you rename
  `test_default_installs_both_skills`, search for any other test
  that imports/extends it. Unlikely but check.
- **`/brainstorm` SKILL.md uses YAML frontmatter that conflicts
  with the prepended HTML comment.** Read the source file first; if
  it starts with `---` frontmatter, the comment goes AFTER the
  frontmatter block, not before.

## Done criteria

- `end_of_line/skills/brainstorm/SKILL.md` exists with drift-note
  header.
- `BUNDLED_SKILLS` includes `"brainstorm"`.
- Tests cover three-skill default install and `--only brainstorm`.
- Full suite green from clean process.
- README "Working with clu" section pitches the combo and lists
  three bundled skills + linked `/grill-me`.
- One commit, structured message, no `Fixes` trailer.
- `clu complete` called with SHA + count summary.
