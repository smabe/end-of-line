# bundle-brainstorm-skill — ship `/brainstorm` as a third bundled skill

Add `/brainstorm` to the set clu bundles, alongside `/clu-phase` and
`/plan` (which ship in `bundle-plan-skill`). The README's "Working
with clu" section gets rewritten to pitch the combo —
**brainstorm → grill-me → plan → clu** — as the recommended pre-clu
workflow for non-trivial work. `/grill-me` stays linked-out (Matt
Pocock's, https://github.com/mattpocock/skills); `/brainstorm` is
the operator's, cloned the same way `/plan` is.

## Goal

After this plan ships, `clu install-skill` (no args) installs three
skills: `/clu-phase`, `/plan`, `/brainstorm`. README mentions all
three and links the fourth (`/grill-me`) for users to install
separately.

## Locked design decisions (do NOT re-litigate)

- **Depends on `bundle-plan-skill` having shipped.** This plan
  extends the `BUNDLED_SKILLS` tuple from 2 to 3 entries; the
  refactor introducing that tuple must already exist. If
  `bundle-plan-skill` hasn't shipped when this kicks off, block.
- **`/brainstorm` is a frozen clone.** Source is
  `~/projects/abe-skills/skills/brainstorm/SKILL.md` (operator's
  private repo). Same drift-note header as `/plan`.
- **Default `clu install-skill` installs all three by default.**
  No prompt, no flag for the happy path.
- **`--only brainstorm`** works the same as `--only plan` and
  `--only clu-phase`.
- **README rewrite pitches the combo.** The existing "Working with
  clu" section (shipped by `bundle-plan-skill` phase docs) gets
  expanded to: brainstorm explores, grill-me stress-tests, plan
  commits the chosen approach. The pitch makes the combo
  RECOMMENDED but not mandatory — each skill is independent.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| impl | `bundle-brainstorm-skill-impl.md` | Copy `/brainstorm` from abe-skills; add to `BUNDLED_SKILLS`; tests for `--only brainstorm` and three-skill default install; rewrite README "Working with clu" section to pitch the combo. | 1h |

## Failure modes to anticipate

- **bundle-plan-skill not shipped.** If `BUNDLED_SKILLS` doesn't
  exist yet (or is hardcoded to a single skill), this phase must
  block. Verify by reading `end_of_line/cli.py` for the tuple before
  starting work.
- **abe-skills path missing on worker.** Same as `/plan`: if
  `~/projects/abe-skills/skills/brainstorm/SKILL.md` doesn't exist,
  block with a clear question. Do not invent content.
- **README section already mentions a combo.** Phase docs of
  bundle-plan-skill may have written something close to the final
  pitch already. Don't rewrite from scratch — extend what's there.
- **Test count drift.** Each `--only` test multiplies with skill
  count; expect ~3 new tests on top of bundle-plan-skill's additions.

## Done criteria

- `end_of_line/skills/brainstorm/SKILL.md` exists, tracked, with the
  drift-note header.
- `BUNDLED_SKILLS` tuple in `cli.py` is `("clu-phase", "plan", "brainstorm")`.
- `clu install-skill` (no args) installs all three.
- `clu install-skill --only brainstorm` installs only brainstorm.
- README "Working with clu" section pitches the
  brainstorm→grill-me→plan→clu combo and lists three bundled skills.
- Full suite green; new test count reflects the third skill.
- One commit, structured message, no `Fixes` trailer.

## Parking lot
(empty)
