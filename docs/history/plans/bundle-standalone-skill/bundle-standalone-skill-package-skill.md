# bundle-standalone-skill-package-skill — clu install-skill subcommand

You are phase `package-skill` of the `bundle-standalone-skill` plan.
Make clu standalone: the worker skill ships inside the Python package,
and `clu install-skill` puts it in the right place on the user's
machine.

## Locked decisions (do NOT re-litigate)

See `plans/bundle-standalone-skill.md`. Summary:

- **Skill home:** `end_of_line/skill/SKILL.md` (inside the package).
- **Bundle via pyproject.toml** so it ships in both editable and wheel installs.
- **`clu install-skill`** writes to `~/.claude/skills/clu-phase/SKILL.md`.
- **Flags:** `--force` (overwrite existing), `--dry-run` (print plan, no writes).
- **Symlink-aware `--force`:** if target is a symlink, unlink before write.
- **Global only.** No `--project` flag.
- **Worker does NOT touch `~/.claude/skills/`.** That's the operator's manual step.

## Read first

- `examples/clu-phase-skill.md` — the current in-repo skill template.
  This is what you copy into `end_of_line/skill/SKILL.md` as the
  initial canonical content. Don't change its body in this phase —
  phase 2 owns the content edits.
- `pyproject.toml` — current packaging config. You'll add a
  `[tool.setuptools.package-data]` section (or equivalent for whichever
  build backend is in use) so the skill ships with the wheel.
- `end_of_line/cli.py` — subcommand wiring; mirror an existing
  read-only host-scoped command (`list`, `tick-all`) for the install
  subcommand's argparse style.
- `tests/__init__.py` — `isolate_registry` helper. Not strictly needed
  for install-skill tests (no registry touched), but check whether
  you need a similar tmp-dir helper or can use stock `tempfile`.
- `README.md` — current install section. You'll add the
  `clu install-skill` step.

## Produce

1. **Create the canonical skill file.** `end_of_line/skill/SKILL.md`.
   Initial content: **byte-identical copy** of `examples/clu-phase-skill.md`.
   Don't edit the body. Phase 2 will add the quality mandates section.

2. **Bundle in pyproject.toml.** Add the skill file to the package data
   so it ships in wheels. Verify with `pipx reinstall -e .` (or
   equivalent) that `importlib.resources.files("end_of_line").joinpath("skill/SKILL.md")`
   resolves to a real file at runtime.

3. **Failing tests first.** New file `tests/test_install_skill.py`.
   Cover at minimum:
   - **Fresh install** (target doesn't exist) → file created, contents
     match bundled skill byte-for-byte, exit 0.
   - **Existing target without `--force`** → refuse with non-zero exit
     (`ExitCode.STATUS_TRANSITION` is the right semantic — same as
     release-claim's refusal — but reuse `ExitCode.UNKNOWN_TASK` if
     STATUS_TRANSITION doesn't fit; document the choice in the commit).
     No file modified. Helpful stderr suggests `--force`.
   - **Existing target with `--force`** → overwritten, exit 0.
   - **Existing target is a symlink, no `--force`** → refuse with a
     specific message mentioning the symlink (so the operator knows
     the situation is different from a regular file).
   - **Existing target is a symlink, with `--force`** → symlink
     unlinked, real file written in its place, exit 0. The symlink
     target (e.g. abe-skills) is NOT touched.
   - **`--dry-run`** → exit 0, helpful stdout describing the action,
     no filesystem changes.
   - **`--dry-run` + `--force`** → exit 0, stdout describes overwrite,
     no filesystem changes.
   - **Parent dir doesn't exist** (`~/.claude/skills/` is fresh) →
     `mkdir -p` semantics: create parent dirs, write file, exit 0.

   Use `tempfile.TemporaryDirectory()` for the target dir; don't
   write to the real `~/.claude/skills/` from tests. The handler
   needs to accept a `target_path` override (or read from `HOME` env
   var) so tests can redirect. Pick whichever is cleaner; keep the
   public `--target` flag *out* of the CLI for v1 — global-only per
   the locked decisions.

4. **Implementation.**
   - **`end_of_line/cli.py`:** add an `install-skill` subparser. No
     `add_common(...)` — this is host-scoped, no `--project`/`--plan`.
     Flags: `--force` (store_true, default False), `--dry-run`
     (store_true, default False).
   - **Handler `cmd_install_skill(args)`:**
     - Resolve bundled skill via `importlib.resources.files("end_of_line").joinpath("skill/SKILL.md")`.
     - Resolve target as `Path.home() / ".claude" / "skills" / "clu-phase" / "SKILL.md"`.
     - Compute `(exists, is_symlink)` for the target with `target.is_symlink()` first (because `target.exists()` follows symlinks). Order matters.
     - If exists and not force: refuse with appropriate message
       (different stderr text for symlink vs regular file).
     - If `--dry-run`: print the planned action ("would write %s to
       %s"), exit 0.
     - Create parent dirs (`target.parent.mkdir(parents=True, exist_ok=True)`).
     - If symlink and force: `target.unlink()` first.
     - Copy bundled file content to target. Use `target.write_bytes(bundled.read_bytes())`
       not shutil.copy — avoids any platform-specific permission
       weirdness.
     - Print success.
     - Return `ExitCode.OK`.

5. **Update `examples/clu-phase-skill.md`** to a one-line stub:
   ```
   This file has moved. The canonical worker skill lives at
   `end_of_line/skill/SKILL.md` and ships with the `clu` package.
   Run `clu install-skill` to install it.
   ```
   Don't delete the file — historical plan references point at this
   path and a clean stub avoids 404s for readers of `docs/history/`.

6. **Update `README.md`** with a clear "Install" section:
   ```
   ## Install

   1. `pipx install -e .` — puts `clu` on your $PATH.
   2. `clu install-skill` — copies the `/clu-phase` worker skill to
      `~/.claude/skills/clu-phase/SKILL.md`, which Claude Code reads
      to drive worker phases.
   3. (Optional) Install the LaunchAgents from `examples/` for
      cron-driven dispatch. See `docs/operations.md`.
   ```
   Keep it short — 3-5 lines. The depth lives in `docs/operations.md`.

7. **`/simplify`** if the diff crosses ~30 lines or >1 module (it
   will — pyproject.toml + cli.py + new skill file + test file +
   README + examples stub).

8. **Full suite green:** `python3 -m unittest discover -s tests`.

9. **Commit** (structured format). Stage explicit paths:
   `git add end_of_line/cli.py end_of_line/skill/SKILL.md pyproject.toml tests/test_install_skill.py examples/clu-phase-skill.md README.md`.

## Constraints

- **The worker does NOT touch `~/.claude/skills/`.** That's the
  operator's manual step after the bundle lands.
- **No `--project` flag.** Global install only.
- **No skill content edits.** Phase 2 owns body changes; this phase
  copies the existing body byte-for-byte.
- **No new top-level skill location** like `skills/clu-phase/`. The
  skill goes INSIDE the package (`end_of_line/skill/`) so `importlib.resources`
  works for wheel installs.
- **Symlink handling is mandatory.** The operator's current setup
  has the target symlinked to abe-skills; tests must cover this
  case.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan bundle-standalone-skill --phase package-skill \
    --token <token> --commit <sha>
```

## Escape hatch

`clu block` if:
- `pyproject.toml`'s build backend isn't setuptools (might be poetry,
  flit, hatch — different package-data syntax). Surface what you find
  with options to add the correct setting.
- `importlib.resources.files("end_of_line").joinpath("skill/SKILL.md")`
  doesn't resolve after the package-data change. Possibly a
  pipx-editable-install quirk. Surface the failure mode.
- You discover the user's `~/.claude/skills/clu-phase/SKILL.md`
  symlinks somewhere unexpected (not abe-skills). Don't act on the
  surprise — surface so the operator can decide.
