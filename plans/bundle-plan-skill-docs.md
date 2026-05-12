# bundle-plan-skill-docs — README "Working with clu" section

You are phase `docs` of the `bundle-plan-skill` plan. Phases `layout`
and `install` already shipped — clu now bundles two skills and
installs both by default. Your job is to tell users about it in the
README and update the `install-skill` help text if not already
covered.

## Locked decisions (do NOT re-litigate)

See `plans/bundle-plan-skill.md`. Summary:

- README section title: "Working with clu" (the exact phrasing the
  operator chose).
- Mention BOTH bundled skills: `/clu-phase` (worker, required by
  dispatch) and `/plan` (authorship, the canonical shape clu's
  parser expects).
- Link `/grill-me` to https://github.com/mattpocock/skills with
  attribution to Matt Pocock. Do NOT copy grill-me content; just
  link.
- Do NOT add a "this is different from Claude Code's built-in plan
  mode" disambiguation paragraph. Operator explicitly skipped that.

## Read first

- `README.md` — the current install/usage section. The new section
  slots in AFTER install/setup and BEFORE the deeper architecture
  references. Read the existing section ordering and TOC anchors
  (if any) before choosing the insertion point.
- `plans/bundle-plan-skill.md` — the master, for context.
- Run `clu install-skill --help` after phase 2 ships to confirm the
  help text already mentions both skills. If it does, no further
  cli.py edits needed. If not, fix it here.

## Produce

1. **Read the current README structure.** Identify:
   - Where the install command appears (e.g. `pipx install -e .`)
   - The current ordering of sections (Install → Usage → Architecture
     → etc., or similar)
   - Whether the README uses an explicit TOC at the top (if so,
     update it)

2. **Add the "Working with clu" section.** Slot it right after the
   install section. Suggested content (adapt voice to match existing
   prose):

   ```markdown
   ## Working with clu

   clu bundles two skills with `clu install-skill`:

   - **`/clu-phase`** — the worker skill clu's dispatch invokes for
     each phase. Required for clu to function; you don't run this
     skill directly.
   - **`/plan`** — the authorship skill for writing plans clu can
     orchestrate. Drops a file at `plans/<slug>.md` in your project
     with a `## Sessions index` table — that table is what clu's
     parser reads to know what phases to dispatch.

   Run `clu install-skill` to drop both into `~/.claude/skills/`.
   Pass `--only <name>` to install just one, or `--force` to
   overwrite existing user files at those paths.

   **Recommended companion: `/grill-me`** by Matt Pocock
   ([source](https://github.com/mattpocock/skills)) — interviews you
   relentlessly about a plan or design until shared understanding is
   reached. Pairs well with `/plan` when you're about to commit a
   non-trivial plan to clu; running `/grill-me` first surfaces
   underspecified branches before they become mid-phase blockers.
   `/grill-me` is installed separately; clu does not bundle it.

   ### Minimum plan shape clu can orchestrate

   clu's parser requires the master plan file (`plans/<slug>.md`) to
   contain a `## Sessions index` table:

   | Session | Plan file | Scope | Effort |
   |---|---|---|---|
   | phase-a | `<slug>-phase-a.md` | <one-line scope> | <time est> |
   | phase-b | `<slug>-phase-b.md` | <one-line scope> | <time est> |

   Each row points to a sub-plan file in the same `plans/` directory.
   The bundled `/plan` skill produces this shape by default.
   ```

   Adjust formatting to match the README's style. If the README uses
   level-2 headings for major sections, `## Working with clu` fits.
   If it's structured differently, fold the content into the existing
   shape.

3. **Update README TOC** if one exists. Add an anchor link to the
   new section in the right alphabetical/structural position.

4. **Verify `clu install-skill --help` text mentions both skills.**
   ```bash
   python3 -m end_of_line.cli install-skill --help
   ```
   If phase 2's text update didn't land or reads poorly, fix it
   here. Otherwise leave alone.

5. **Run the test suite** from a clean process per mandate #9. Docs
   edits should not affect tests, but verify count is unchanged from
   phase 2's result.

6. **Commit** with structure:
   - Title: `bundle-plan-skill phase 3: README "Working with clu"`
   - Why: clu now ships two skills; new users need a one-stop place
     in the README that says what they are and how to get started.
   - What's new:
     - "Working with clu" section after install/setup
     - Minimum plan shape (Sessions index table) callout for users
       who don't install the bundled `/plan`
     - `/grill-me` link + attribution
   - Tests: full suite green, count unchanged from phase 2.
   - Co-Authored-By trailer.
   - No `Fixes` trailer — this plan doesn't close an open issue.

7. **Call `clu complete`** with the token. Summary: commit SHA,
   test count (unchanged), one-line confirmation that the README
   section + `/grill-me` link landed.

## Failure modes to watch for

- **README anchors break existing links.** If you add a new section,
  any external link to a specific README anchor stays valid (you're
  inserting, not renaming). If you rename existing anchors as a
  side effect (e.g. to fit ordering), check for inbound links —
  `grep -rn "README.md#" docs/` and similar.
- **Stitch into the wrong section.** The "Working with clu" section
  is post-install (users have installed clu and want to know what
  to do next), not part of the pitch / overview at the top of the
  README. Don't put it before the install command.
- **Over-explaining `/plan`.** The bundled `/plan` is the spec — users
  who installed it have the doc. Don't duplicate its content in the
  README. Keep the README description to one line plus the Sessions
  index shape (which IS load-bearing for clu's parser and worth
  documenting in two places: the skill AND the README, because users
  may write plans without using the skill).
- **`/grill-me` link rot.** https://github.com/mattpocock/skills is
  the current path. If it 404s when a future user clicks it, that's
  on Matt Pocock — not your problem. Don't try to mirror the
  content as defense.

## Done criteria for this phase

- `README.md` has a "Working with clu" section after install/setup
  describing both bundled skills, the Sessions index requirement,
  and the `/grill-me` link.
- TOC (if present) updated.
- `clu install-skill --help` mentions both skills.
- Tests green, count unchanged from phase 2.
- One commit, structured message.
- `clu complete` called with summary.
