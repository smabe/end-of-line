# install-skill-list — `clu install-skill --list` enumerates bundled skills

Closes [#13](https://github.com/smabe/end-of-line/issues/13).
Discoverability fix: today the only way to know what `clu
install-skill` will install is to grep `BUNDLED_SKILLS` in
`end_of_line/cli.py`. After this plan, `clu install-skill --list`
prints them.

## Goal

```
$ clu install-skill --list
Bundled skills available via clu install-skill:
  clu-phase   /Users/you/.claude/skills/clu-phase/SKILL.md
  plan        /Users/you/.claude/skills/plan/SKILL.md
  brainstorm  /Users/you/.claude/skills/brainstorm/SKILL.md
```

Exits `ExitCode.OK`. No filesystem writes. No prompts.

## Locked design (do NOT re-litigate)

- **Mutually exclusive with `--only`, `--force`, `--dry-run`.**
  Argparse mutex group is fine, but a simpler shape is: when `--list`
  is set, ignore the others and short-circuit at the top of
  `cmd_install_skill`. Pick whichever needs less boilerplate.
- **Output shape**: header line, then one row per skill with
  `name<padding>target_path`. Padding via `str.ljust(max_name_len)`.
- **Path resolution**: same `Path.home() / ".claude" / "skills" /
  name / "SKILL.md"` formula `cmd_install_skill` already uses. Reuse,
  don't duplicate.
- **Source**: iterate `BUNDLED_SKILLS` (the existing tuple at
  `cli.py:506`).

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| impl | `install-skill-list-impl.md` | Add `--list` to the `install-skill` subparser; early-return branch in `cmd_install_skill` that prints the table; new test in `tests/test_install_skill.py`. | 30m |

## Failure modes to anticipate

- **`Path.home()` in tests**. Existing install-skill tests already
  monkeypatch HOME or use `tmp_path`. Mirror the same pattern — don't
  introduce a new fixture style.
- **Mutex with `--force` etc.** If you go the mutex-group route,
  ensure tests don't break (e.g. `--list --force` should error or
  short-circuit cleanly, not crash).
