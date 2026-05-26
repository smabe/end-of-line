# skill-drift-detection

## Goal

Build three layers of skill/code drift detection so the kind of SKILL.md
gap we just closed manually (clu-plan missing the attestation gate,
stale 100s overhead claim, undocumented Effort column) gets caught
mechanically or by an on-demand audit instead of by accident months
later. Layer A blocks renamed/removed verbs in CI. Layer B runs
tagged bash fences in skills as real tests. Layer C is an on-demand
`/audit-skill` slash command for the harder "absence" class of drift
the mechanical layers can't catch.

## Non-goals

- **No SkillScope-style static analyzer** — overkill for 6 bundled
  skills. The on-demand LLM audit (layer C) covers the same surface
  at a fraction of the implementation cost.
- **No pure generate-don't-lint of skill bodies.** Skills are
  prose-with-behavior, not command reference manuals. We can derive
  the verb list mechanically (A), but the surrounding prose has to
  stay prose.
- **Layer C is advisory only, never a CI gate.** "LLM passed" is not
  a signal that should fail builds. Punch list back to the operator,
  human reviews and applies.
- **No fence-tagging of every existing bash block.** Tag the ones that
  are runnable as-is; leave placeholder fences (`<slug>`, `<phase>`)
  alone. Layer B is opt-in per fence, not all-or-nothing.
- **No audit of the audit-skill itself in this plan.** Drift on
  layer C is acceptable on day one; we can add a layer-C self-audit
  later if it starts going stale.

## Files to touch

### Phase A — argparse-derived verb lint
- `tests/test_skill_lint.py` — NEW — extracts subparser verb list from
  `cli.build_parser()` (or equivalent), greps all bundled SKILL.md
  files for `clu <word>` patterns, asserts every matched verb exists.
  Mode: hard-fail with a punch list naming the SKILL.md + line + verb.
- `end_of_line/cli.py` — MAYBE — factor out parser construction into
  a callable function if it isn't already (so the test can import
  the parser without running `main`). Only if `build_parser()` doesn't
  already exist as a clean entry point.

### Phase B — executable bash fences
- `tests/test_skill_fences.py` — NEW — walks all
  `end_of_line/skills/*/SKILL.md`, finds fences tagged with our marker
  (proposed: `<!-- skilltest -->` HTML comment immediately before
  the fence), runs each in a tmpdir with `bash -e -o pipefail`, asserts
  exit 0. Includes a tiny scaffold (cd into a tmp project with a stub
  `.orchestrator.json`) so commands like `clu queue list` can run.
- `end_of_line/skills/*/SKILL.md` (selective) — modified — tag 3-5
  representative fences that ARE actually runnable as-is. The point
  is to prove the harness works and seed the pattern; full tagging
  is out of scope.
- `docs/conventions.md` — modified — add a 3-line "Skill fence tags"
  subsection documenting the marker convention.

### Phase C — `/audit-skill` slash command
- `end_of_line/skills/audit-skill/SKILL.md` — NEW — workflow prompt
  that takes a skill name, reads the SKILL.md, reads the relevant
  code surfaces (`cli.py`, `hooks/`, `state.py` events, recent
  MEMORY.md ship entries), produces a punch list against the
  template: stale numbers, missing references to shipped features,
  deprecated commands, mismatched defaults.
- `end_of_line/cli.py:1994` — modified — add `"audit-skill"` to
  `BUNDLED_SKILLS` tuple.
- `end_of_line/cli.py:593-605` — modified — update install-skill
  help text to list the new skill.
- `CLAUDE.md` — modified — add a one-line pointer to
  `/audit-skill` in the project-private brief.

## Failure modes to anticipate

- **Argparse introspection brittleness.** `parser._subparsers` is a
  private attribute; relying on it ties the test to argparse internals.
  Mitigation: prefer iterating `parser._actions` for `_SubParsersAction`
  instances, document the dependency, accept that argparse stdlib API
  is glacial enough that breakage is unlikely. Backup: shell out to
  `clu --help` and parse the verb list from output (less brittle to
  Python upgrades, more brittle to formatting changes — pick one
  failure mode).
- **`clu <verb>` false positives in skill prose.** A SKILL.md sentence
  like "we used to call this `clu integrate` before the rename" should
  not fail the lint when we deliberately reference the old name as
  history. Mitigation: scope the regex to fenced code blocks only,
  or add an opt-out marker like `clu <verb> [intentional]`.
- **Layer B's bash sandbox is a footgun.** Running `bash -e` from
  fences in a CI sandbox could exec `clu init`, `clu install`,
  etc. and pollute the test environment. Mitigation: every tagged
  fence runs in a `tempfile.TemporaryDirectory()` with `HOME` and
  `XDG_CONFIG_HOME` overridden to point inside the tmpdir. Use
  `tests.isolate_registry()` pattern from `tests/__init__.py`.
- **Skill fence tagging drift in Phase B.** If we tag fences as
  runnable and they later become non-runnable (e.g. example uses
  a flag that gets removed), the test fails. That's the *point* of
  the layer — but it can create commit-time friction when a clu
  refactor changes a flag and the skill update lands separately.
  Mitigation: the test failure message names the exact SKILL.md +
  fence so the fix is mechanical.
- **Layer C is just a prompt — drift on layer C itself is real.** The
  audit-skill's checklist of "what to look for" will go stale just
  like any other skill. Out-of-scope for this plan but worth flagging
  for the parking lot.
- **BUNDLED_SKILLS tuple has duplicate maintenance points.** The
  tuple at cli.py:1994, the help text at cli.py:593-605, the
  CLAUDE.md mention at line 120, and the install-skill description
  at line 595-605 all hardcode the skill name list. Phase C must
  update all four. Mitigation: grep for "brainstorm" before
  finishing phase C — it co-occurs in every site that lists
  bundled skills.

## Done criteria

- `python3 -m unittest discover -s tests` includes the new
  `test_skill_lint.py` and `test_skill_fences.py` and runs green.
- Test count moves from 1377 → ~1380+ (a handful of new tests).
- `clu install-skill --only audit-skill --force` installs the new
  skill to `~/.claude/skills/audit-skill/SKILL.md`.
- Manually deleting `clu integrate` and re-adding it to a SKILL.md
  fails `test_skill_lint.py` with a clear punch-list message.
- One representative bash fence in a SKILL.md (probably
  clu-plan's `clu init` invocation) is tagged with the
  `<!-- skilltest -->` marker and passes `test_skill_fences.py`.
- `/audit-skill clu-plan` invoked manually returns a punch list
  (proof-of-life — we don't need it to find anything new on this run).
- All four BUNDLED_SKILLS reference sites updated (tuple, help text,
  CLAUDE.md, install-skill description).
- One commit per phase, structured commit format, `/code-review`
  after each phase, push at the end.

## Parking lot

(empty at start)
