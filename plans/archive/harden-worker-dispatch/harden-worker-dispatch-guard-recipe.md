# harden-worker-dispatch-guard-recipe — doctor bypass warning + settings emission + recipe docs

You are phase `guard-recipe` of the `harden-worker-dispatch` plan. You deliver,
as one commit: a `clu doctor` warning when dispatch commands carry bypass
permissions, `clu init` emission of the worker sandbox/permissions settings
file, and the documented hardened-dispatch recipe (#90 acceptance criteria
1–3).

## Locked decisions (do NOT re-litigate)

See `plans/harden-worker-dispatch.md`. Summary:

- **Doctor printer** `_print_dispatch_permission_health(cfg)`: shlex-tokenize
  `cfg.dispatch.command` AND `cfg.dispatch.repair_command` (mirror
  `resolved_model()` at `dispatch.py:95-111`, including its return-None-on-
  shlex-ValueError tolerance). Warn when tokens contain `bypassPermissions`
  (as the value of `--permission-mode`/`--permission-mode=`) or
  `--dangerously-skip-permissions`. Quiet when clean — matches every other
  doctor printer. Warning text points at the operations.md recipe section.
- **Settings emission**: `cmd_init` calls a new `_ensure_worker_settings()`
  (mirror `_ensure_quality_stub`, `cli.py:1878-1899`): if
  `~/.config/clu/worker-settings.json` (via the existing `clu_config_dir()`
  in `_xdg_guard.py`) is absent, write it from the bundled template and print
  the path + a one-line hardened-command hint. NEVER overwrite an existing
  file. Bundled template is `end_of_line/worker-settings.template.json`,
  loaded via `importlib.resources` exactly like the skills bundle.
- **Template content** (locked by the master's permission model):
  ```json
  {
    "sandbox": {
      "enabled": true,
      "failIfUnavailable": true,
      "allowUnsandboxedCommands": false,
      "excludedCommands": ["clu *"],
      "network": {
        "allowedDomains": ["github.com", "api.github.com"]
      }
    }
  }
  ```
- **The documented recipe command** (operations.md; generic paths, absolute
  `--settings` path spelled out as a requirement — `~` is not reliably
  expanded inside the `shell=True` dispatch line when quoted):
  ```
  claude --print --model claude-fable-5 --permission-mode dontAsk \
    --settings /Users/<you>/.config/clu/worker-settings.json \
    --allowedTools "Bash(clu *),Bash(git *),Bash(python3 *),Bash(gh *),Bash(command -v *),Edit,Write,TodoWrite,Task,Skill" \
    --max-budget-usd 20.00 '/clu-phase {plan_slug} {phase_id} {token} {state_file}'
  ```
  Document: `--allowedTools` is variadic — MUST be one comma-joined argument
  (empirical, spike 2026-06-10); version floor claude ≥ 2.1.170 (verified; the
  `$VAR`-denial bug anthropics/claude-code#51001 affects some 2.1.11x builds);
  denied tool → worker raises `clu block` instead of wedging (spike Test A);
  worktree shared-`.git` writes auto-granted by the sandbox
  (code.claude.com/docs/en/sandboxing); residual v1 gaps: bare `Edit`/`Write`
  (path-scoped Write rules silently fail in dontAsk,
  anthropics/claude-code#52962), and per-project test commands beyond
  `python3 *` need their own allowlist entry (e.g. `Bash(xcodebuild *)` —
  HealthData migration is a non-goal here).

## Read first

- `plans/harden-worker-dispatch.md` `## Findings log` — phase hb-daemon may
  have logged permission surprises.
- `end_of_line/dispatch.py:95-111` — `resolved_model()` parsing to mirror.
- `end_of_line/cli.py:1878-1899` — `_ensure_quality_stub` emission precedent;
  `cli.py:2587-2631` — `cmd_doctor` printer chain and where to insert.
- `end_of_line/_xdg_guard.py` — `clu_config_dir()`.
- `tests/test_doctor.py:79` — `redirect_stdout` capture pattern;
  `tests/__init__.py` — `CluTestCase` XDG isolation (settings-emission tests
  must not touch the real `~/.config/clu/`).
- `docs/operations.md` `## Bootstrap` (~line 521), `docs/conventions.md`
  `## Worker callback contract` (line 115), `docs/reference.md` dispatch
  section (line 303), and `docs/_outline.md` for the docs structural contract.
- Packaging: confirm how `skills/` ships as package data (pyproject/setup
  config) and register the new template file the same way.

## Produce

1. **Failing tests first.**
   - Doctor printer tests (in the doctor tests file): warns on
     `--permission-mode bypassPermissions`, warns on
     `--permission-mode=bypassPermissions`, warns on
     `--dangerously-skip-permissions` in `repair_command`, quiet on the
     hardened recipe command, quiet on empty command, tolerant of unparseable
     command (no crash, no warning).
   - Init emission tests: file created from template when absent (assert JSON
     content parses and `sandbox.enabled` is true); existing file NOT
     overwritten; emission path printed.

2. **Implementation.**
   - `end_of_line/cli.py`: `_print_dispatch_permission_health` + insertion in
     `cmd_doctor`'s printer chain; `_ensure_worker_settings()` + call in
     `cmd_init`.
   - `end_of_line/worker-settings.template.json`: the locked template, plus
     packaging registration.
   - `docs/operations.md`: new `## Hardened worker dispatch` section after
     `## Bootstrap` carrying the recipe, the allowlist enumeration table with
     one-line WHY per entry (#90 acceptance criterion 1), the denial→`clu
     block` contract, version floor, and residual gaps.
   - `docs/conventions.md`: cross-link under Worker callback contract.
   - `docs/reference.md`: doctor printer + init emission lines in the cli/
     dispatch sections.

3. **Acceptance.**
   - All new tests green; full suite green.
   - `clu doctor` against this repo's CURRENT (still-bypass) config prints the
     warning; against a config carrying the recipe command prints nothing
     extra. (This repo's live config is migrated next phase — the warning
     firing now is correct and expected.)
   - Fresh `clu init` in a scratch project creates
     `~/.config/clu/worker-settings.json` under the test-isolated XDG dir.

4. **Commit + attest + complete.**
   - Findings: log anything the next phase's live smoke must know (e.g.
     packaging quirks for the template file).
   - Structured commit: `harden-worker-dispatch: phase guard-recipe — doctor
     bypass warning + worker-settings emission + recipe docs (#90)`.
   - Stage explicit paths: `end_of_line/cli.py`,
     `end_of_line/worker-settings.template.json`, packaging file(s),
     `docs/operations.md`, `docs/conventions.md`, `docs/reference.md`, the
     test files (+ master if findings logged).
   - After the commit:
     - `clu verify --plan harden-worker-dispatch --phase guard-recipe --token <T>`
     - `clu attest --simplify --plan harden-worker-dispatch --phase guard-recipe --token <T>`
   - `clu complete --plan harden-worker-dispatch --phase guard-recipe --token <T>`.

## Failure modes to watch

- **Doctor noise discipline**: every existing printer is quiet-when-clean; a
  printer that always prints (even "ok") breaks the doctor contract
  (`cli.py:2691-2693` rationale).
- **XDG leakage from tests**: emission tests that miss the `CluTestCase`
  isolation write a real `~/.config/clu/worker-settings.json` on the dev
  machine. Use the established isolation helpers.
- **shlex on empty/None**: `repair_command` is `str | None`
  (`config.py:45`); guard before tokenizing.
- **Template packaging**: a template registered in the package but missing
  from the wheel/sdist data only fails on a clean install — mirror exactly how
  `skills/` is registered, and note the clean-clone canary will catch drift.
