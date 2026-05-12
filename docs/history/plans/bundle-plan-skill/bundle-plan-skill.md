# bundle-plan-skill — ship clu with `/plan` alongside `/clu-phase`

Make clu self-contained for plan authorship: bundle the `/plan` skill
into the Python package, install both skills by default, and document
the recommended workflow in the README. Operator's call (confirmed):
default-install-both, refuse to clobber non-symlinks, link `/grill-me`
out to https://github.com/mattpocock/skills since the source repo for
`/plan` is private.

## Goal

After this plan ships, `pipx install -e .` followed by `clu install-skill`
gives a new user both the worker skill (`/clu-phase`, required by clu's
dispatch contract) and the authorship skill (`/plan`, the canonical
format clu's parser expects). The README's "Working with clu" section
points users at both, plus `/grill-me` for stress-testing plans.

## Locked design decisions (do NOT re-litigate)

- **Bundle layout: `end_of_line/skills/<name>/SKILL.md`** (plural
  `skills/`, one subdir per skill). Current `end_of_line/skill/`
  (singular) gets renamed and `clu-phase` moves under it.
- **Default `clu install-skill` installs BOTH skills.** No prompt, no
  flag required for the happy path.
- **`--only <name>` flag** installs just one skill. For users who
  already have their own `/plan` setup.
- **`--force` flag** stays as the explicit-overwrite escape hatch
  (already exists for `/clu-phase`; extend semantics to cover both).
- **Don't-clobber-non-symlinks safety.** If the target at
  `~/.claude/skills/<name>/SKILL.md` is a regular file (user-owned),
  refuse to overwrite without `--force`. Symlinks are fair game —
  clu owns the ones it wrote and overwriting a symlink can't destroy
  user data. The current behavior (`unlink before write`) stays for
  symlinks; only the non-symlink case becomes stricter.
- **`/plan` is a frozen clone.** Source is
  `~/projects/abe-skills/skills/plan/SKILL.md` (operator's private
  repo). The bundled version captures the shape clu's parser expects.
  Drift from upstream is acceptable and documented in a header note;
  users who want the bleeding edge can replace the symlink manually.
- **README link target for `/grill-me`:** https://github.com/mattpocock/skills
  (Matt Pocock's public skills repo). Attribution: "by Matt Pocock,
  bundled separately." Do NOT copy grill-me's contents into clu.
- **Plan-mode disambiguation is OUT OF SCOPE.** Operator chose to
  skip the "this is different from Claude Code's built-in plan mode"
  paragraph. The bundled `/plan` is the spec; users who install it
  understand what they get.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| layout | `bundle-plan-skill-layout.md` | Rename `end_of_line/skill/` → `end_of_line/skills/clu-phase/`; copy `/plan` from abe-skills to `end_of_line/skills/plan/SKILL.md` with a header drift note; update `cli.py:350` path; existing install-skill test paths updated. | 1h |
| install | `bundle-plan-skill-install.md` | Refactor `cmd_install_skill` to iterate over a list of bundled skills; add `--only <name>` flag; add don't-clobber-non-symlinks safety. New tests for multi-skill install + the no-clobber rule. | 1.5h |
| docs | `bundle-plan-skill-docs.md` | README "Working with clu" section recommending bundled `/plan` + linking `/grill-me`; update `install-skill` help text to mention both skills. | 30m |

## Failure modes to anticipate

- **abe-skills path not on the worker's machine.** Phase 1's worker
  reads from `~/projects/abe-skills/skills/plan/SKILL.md`. If that
  path doesn't exist (e.g. the worker runs on a fresh box), the phase
  must block, not commit half-done. Worker should check existence
  first and `clu block` with a clear blocker question if missing.
- **`cli.py` resource path change is load-bearing.** `files("end_of_line").joinpath("skill/SKILL.md")` at line 350 must update to
  the new path AND `pyproject.toml` must include the new directory in
  package data (verify the existing config covers `skills/**`).
- **Existing install-skill tests assume singular path.** Test fixtures
  read `files("end_of_line").joinpath("skill/SKILL.md")` (see
  `tests/test_install_skill.py:31`). All such references need updating
  in lockstep.
- **`--only` arg parsing.** Must validate against the known skill
  list; an unknown name should exit with a clear error, not silently
  install nothing.
- **Don't-clobber-non-symlinks: symlink to a regular file.** If the
  target IS a symlink but its dereferenced destination is a regular
  file the user wrote, is that "user data"? No — the symlink itself
  is what clu controls. Unlinking the symlink doesn't touch the
  destination. The rule is about the target path's *file type*, not
  what it resolves to.
- **`/plan` content drift in flight.** If the operator edits
  `~/projects/abe-skills/skills/plan/SKILL.md` between phase 1 and
  phase 3, the bundled copy and the upstream diverge silently. That's
  acceptable — see the drift note in the bundled SKILL.md header.
- **README section ordering.** "Working with clu" should slot after
  "Install" and before "Architecture" or wherever the operator finds
  natural. Phase 3's worker reads the existing README and chooses an
  insertion point that doesn't break the existing TOC anchors.
- **Frozen-clone licensing.** `/plan` is the operator's own skill, so
  copyright isn't an issue. `/grill-me` belongs to Matt Pocock; we're
  only LINKING (not copying), so no attribution surface here either.

## Done criteria (whole plan)

- `end_of_line/skills/clu-phase/SKILL.md` and
  `end_of_line/skills/plan/SKILL.md` both exist in the package.
- `clu install-skill` (no args) installs both skills, prints both
  destinations.
- `clu install-skill --only plan` installs only `/plan`. Same for
  `--only clu-phase`.
- `clu install-skill` refuses to overwrite a non-symlink target at
  either destination without `--force`. Existing symlink behavior
  preserved.
- README has a "Working with clu" section that:
  - Tells users `clu install-skill` ships `/plan` and `/clu-phase`
  - Recommends `/plan` for plan authorship (with a one-line shape
    pointer: "files in `plans/<slug>.md` with a `## Sessions index`
    table")
  - Links `/grill-me` to https://github.com/mattpocock/skills with
    Matt Pocock attribution and a note that it's installed separately
- Full suite green (≥229 after worker-path-config docs phase; expect
  ~232-235 after this plan adds new install-skill tests).
- Three commits, one per phase, structured message format, no
  `Fixes` trailer (this plan doesn't close an open issue).

## Parking lot
- Future: `clu install-skill --list` to enumerate what's bundled
  without installing. Discoverability nice-to-have.
- Future: `/plan` and `/clu-phase` versioning — print bundled vs
  installed version, warn on stale installs.
- Future: bundle `/grill-me` directly once Matt Pocock's skill repo
  has a stable license / vendoring policy. For now, link out.
