# bundle-standalone-skill — make clu clone-and-go

clu currently depends on the operator's private `abe-skills` repo for
the `/clu-phase` worker skill. That coupling means nobody else can
actually use clu out of the box. This plan severs the dependency:
the skill becomes part of the clu repo, a `clu install-skill`
subcommand puts it in the right place, and the skill itself learns
the universal quality mandates that produced this session's output.

## Locked design decisions

See memory `project_standalone_skill_plan.md` for full rationale.
Summary:

- **Skill canonical home:** `end_of_line/skill/SKILL.md` (inside the
  Python package). Bundle via `pyproject.toml` package-data so
  `importlib.resources` finds it across editable + wheel installs.
- **Install command:** `clu install-skill` writes to
  `~/.claude/skills/clu-phase/SKILL.md`. Flags: `--force` (overwrite
  existing), `--dry-run` (print plan, no writes).
- **Scope: global only.** No `--project` flag, no per-project skill
  installs. One install per user.
- **Symlink handling:** if the target is a symlink (existing operators
  whose path symlinks into a private skills repo), `--force` unlinks
  the symlink before writing the new file. Refuse without `--force` so
  the operator confirms.
- **Discoverability:** prominent README "Install" section. Two-line
  install: `pipx install -e . && clu install-skill`.
- **Migration:** worker does NOT touch `~/.claude/skills/`. Operator
  runs `clu install-skill --force` themselves after the bundle lands
  to swap from the abe-skills symlink to the in-repo skill.

## Phase ordering rationale

Phase 1 (**package-skill**) ships the canonical home + install
subcommand + README guide. After this lands, a fresh clone of clu is
clone-and-go for any new operator. The existing operator's setup is
unaffected (abe-skills symlink still active).

Phase 2 (**quality-mandates**) edits the canonical skill to add the
universal quality mandates we identified. This is content, not code.
After phase 2 lands, the operator re-runs `clu install-skill --force`
on their machine to pick up the new mandates.

Splitting this way means phase 1 is a working improvement on its own;
phase 2 is pure content polish layered on top.

## Per-phase done checklist

- TDD for phase 1 (it's code + tests); content-only for phase 2.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format.
- Stage explicit paths.
- `clu complete --commit <sha>` with the actual SHA.

No GitHub issues to close — this work isn't tracked in an issue. The
commit message is the only record.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| package-skill | `bundle-standalone-skill-package-skill.md` | Skill into package + `clu install-skill` + README | 1h |
| quality-mandates | `bundle-standalone-skill-quality-mandates.md` | Quality mandates section in the canonical skill | 30m |
