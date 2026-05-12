# clu-monitor-cli-hints — CLI tip emission + CLAUDE.md injection

You are phase `cli-hints` of the `clu-monitor` plan. Second of three. Closes part of [#19](https://github.com/smabe/end-of-line/issues/19).

Phase 1 (`skill-marker`) shipped `monitor.py` and the bundled skill. This phase wires the discoverability hooks: tip lines after `clu init` / `clu queue add`, and the interactive CLAUDE.md injection prompt on first init.

Read the master plan first. Do not redesign.

## Locked decisions (do NOT re-litigate)

- **Tip conditions**: `not monitor.is_scheduled()` AND `sys.stdout.isatty()`. Both required. Suppress otherwise.
- **CLAUDE.md prompt conditions**: `sys.stdin.isatty()` AND `<project_root>/CLAUDE.md` exists AND no `## clu` heading AND no `.no-claude-md` decline marker AND no `--no-claude-md` / `--inject-claude-md` flag override.
- **Decline marker location**: `<plan_dir>/.orchestrator/.no-claude-md`. Empty file is fine — existence is the signal.
- **Section detection**: case-insensitive regex match for `^##\s+clu\s*$` anywhere in CLAUDE.md.
- **Append shape**: two blank lines + the canonical section verbatim. Never overwrites existing content.
- **Prompt response parsing**: lower-strip the input; `y` or `yes` = accept; anything else = decline.
- **Flag semantics**:
  - `--inject-claude-md` → force inject, no prompt, no decline-marker write. Skip injection silently if section already exists (idempotent).
  - `--no-claude-md` → write decline marker, skip injection, no prompt.
  - Neither → run the interactive flow when conditions allow.

## Read first

- Phase 1 output: `end_of_line/monitor.py` (especially `is_scheduled`, `load_marker`).
- `end_of_line/cli.py:429-441` — current `cmd_init`. Tip + CLAUDE.md logic appends after line 440 (`print(f"Initialized {state_path}")`).
- `end_of_line/cli.py:847-916` (post-refactor location of `cmd_queue_add`) — tip appends after the success print loop, before `return ExitCode.OK`.
- `end_of_line/cli.py:100-115` — subparser declarations for `init`. Add the `--inject-claude-md` / `--no-claude-md` flags here (or extend `add_common` — careful, `add_common` is shared by `init`/`register`/`unregister`; flags should be `init`-only).
- `end_of_line/cli.py:82-87` — `add_common`. Do NOT add the new flags here; declare directly on `p_init`.
- `tests/test_init.py` (or wherever cmd_init tests live — `grep -rn "def cmd_init\|test_init\|test_cmd_init" tests/`).
- `tests/test_queue_add.py` — extend with hint-suppression cases.

## Produce

### 1. TDD: failing tests first

**`tests/test_cli_hints.py`** (or add to existing test files — group by command if cleaner):

`cmd_init` tip tests:
- `test_init_prints_monitor_tip_when_marker_absent_and_tty` — marker absent + stdout is TTY → output ends with `Tip: run /clu-monitor for background notifications...`
- `test_init_suppresses_tip_when_marker_present` — write marker via `monitor.record_scheduled` → no tip in output.
- `test_init_suppresses_tip_when_stdout_not_tty` — monkeypatch `sys.stdout.isatty` to return False → no tip.

`cmd_queue_add` tip tests:
- `test_queue_add_prints_monitor_tip_when_marker_absent_and_tty` — same shape, after the position line.
- `test_queue_add_suppresses_tip_when_marker_present`
- `test_queue_add_suppresses_tip_when_stdout_not_tty`
- `test_queue_add_multi_arg_prints_tip_once` — `clu queue add a b c` with marker absent + TTY → tip appears exactly once (not per slug).

CLAUDE.md injection tests (all use stdin monkeypatch to simulate input):
- `test_init_prompts_for_claude_md_inject_when_file_exists_no_section_no_marker` — create CLAUDE.md without `## clu` section, stdin returns "y" → CLAUDE.md gets the canonical section appended.
- `test_init_skips_prompt_when_no_claude_md_file` — no CLAUDE.md at project_root → no prompt, no error.
- `test_init_skips_prompt_when_section_already_present` — CLAUDE.md has `## clu` heading → no prompt.
- `test_init_skips_prompt_when_decline_marker_present` — `.orchestrator/.no-claude-md` exists → no prompt.
- `test_init_decline_writes_marker` — prompt fires, stdin returns "n" → no append, decline marker exists at `.orchestrator/.no-claude-md`.
- `test_init_decline_via_empty_input_writes_marker` — stdin returns "" → treated as decline.
- `test_init_inject_flag_forces_inject_without_prompt` — `--inject-claude-md`, no stdin interaction → CLAUDE.md gets the section appended, no decline marker written.
- `test_init_no_claude_md_flag_writes_decline_marker_without_prompt` — `--no-claude-md`, no stdin interaction → decline marker present, CLAUDE.md unchanged.
- `test_init_no_claude_md_flag_idempotent_with_existing_marker` — decline marker already present, `--no-claude-md` again → no error, marker unchanged.
- `test_init_inject_flag_idempotent_with_existing_section` — CLAUDE.md already has `## clu` section, `--inject-claude-md` → no double-append, no error.
- `test_init_skips_prompt_when_stdin_not_tty` — monkeypatch `sys.stdin.isatty()` to return False → no prompt fires even without flags. Decline marker NOT written (operator hasn't actually declined).
- `test_init_appends_canonical_section_verbatim` — accept path → assert the appended bytes match the canonical template exactly (two leading blank lines + the spec'd content). Snapshot-style assertion.

Use `tests.isolate_registry` + `isolate_monitor_marker` (added in phase 1) in setUp. Mock `sys.stdin` via `unittest.mock.patch` on `builtins.input` (cleaner than monkeypatching `sys.stdin`).

Run suite — all new tests must FAIL.

### 2. Implement hint helpers

Add to `end_of_line/cli.py` near the top of the file (after imports):

```python
_MONITOR_TIP = (
    "\n  Tip: run /clu-monitor for background notifications on "
    "halts and blockers.\n"
)


def _maybe_print_monitor_tip() -> None:
    """Print the /clu-monitor tip if monitoring isn't scheduled
    and stdout is a TTY. Silent otherwise."""
    if not sys.stdout.isatty():
        return
    if monitor.is_scheduled():
        return
    print(_MONITOR_TIP, end="")
```

Add `from . import monitor` to the imports if not already present.

### 3. Wire tip into `cmd_init` and `cmd_queue_add`

In `cmd_init` (cli.py:429-441), after the `print(f"Initialized {state_path}")` line:

```python
    print(f"Initialized {state_path}")
    _maybe_handle_claude_md_injection(cfg, args)  # see step 4
    _maybe_print_monitor_tip()
    return 0
```

In `cmd_queue_add` (post phase-1-of-#18 refactor), after the position-print loop and before `return ExitCode.OK`:

```python
    for pos in positions:
        print(f"queued at position {pos}")
    if len(slugs) > 1:
        print(f"queued {len(slugs)} plans")
    _maybe_print_monitor_tip()
    return ExitCode.OK
```

### 4. CLAUDE.md injection logic

```python
_CLU_SECTION_RE = re.compile(r"^##\s+clu\s*$", re.IGNORECASE | re.MULTILINE)

_CLU_SECTION_TEMPLATE = """

## clu

This project uses clu for autonomous plan execution.

- `clu queue add <slug>` to enqueue a plan; cron dispatches on each tick.
- `clu queue list` for pending; `clu list` for fleet status.
- Run `/clu-monitor` once per machine for background notifications on
  halts and blockers (status: `~/.config/clu/monitor.json`).
- The `/plan` and `/brainstorm` skills (bundled via `clu install-skill`)
  are the canonical authoring + pre-planning entry points.
"""


def _decline_marker_path(cfg: ProjectConfig) -> Path:
    return cfg.project_root / cfg.plan_dir / ".orchestrator" / ".no-claude-md"


def _claude_md_has_clu_section(claude_md: Path) -> bool:
    try:
        text = claude_md.read_text()
    except OSError:
        return False
    return bool(_CLU_SECTION_RE.search(text))


def _maybe_handle_claude_md_injection(
    cfg: ProjectConfig, args: argparse.Namespace,
) -> None:
    """CLAUDE.md injection flow. See master plan for locked semantics."""
    claude_md = cfg.project_root / "CLAUDE.md"
    decline_marker = _decline_marker_path(cfg)

    # Flag overrides take precedence over everything.
    if getattr(args, "inject_claude_md", False):
        if not claude_md.exists():
            return  # nothing to inject into
        if _claude_md_has_clu_section(claude_md):
            return  # idempotent
        with claude_md.open("a") as f:
            f.write(_CLU_SECTION_TEMPLATE)
        print(f"Added clu section to {claude_md}")
        return

    if getattr(args, "no_claude_md", False):
        decline_marker.parent.mkdir(parents=True, exist_ok=True)
        decline_marker.touch(exist_ok=True)
        return

    # Interactive flow conditions.
    if not sys.stdin.isatty():
        return
    if not claude_md.exists():
        return
    if _claude_md_has_clu_section(claude_md):
        return
    if decline_marker.exists():
        return

    print(
        f"\nThis project doesn't have a clu section in CLAUDE.md yet. "
        f"Adding one helps future Claude sessions orient on clu's "
        f"workflow. May I append a short section? [y/N]: ",
        end="",
    )
    response = input().strip().lower()
    if response in {"y", "yes"}:
        with claude_md.open("a") as f:
            f.write(_CLU_SECTION_TEMPLATE)
        print(f"Added clu section to {claude_md}")
    else:
        decline_marker.parent.mkdir(parents=True, exist_ok=True)
        decline_marker.touch(exist_ok=True)
        print("Skipped. Run `clu init --inject-claude-md` later if you change your mind.")
```

### 5. Argparse flags

In `cli.py` near line 100-115 (init subparser block), AFTER `add_common(p_init)`:

```python
inject_group = p_init.add_mutually_exclusive_group()
inject_group.add_argument(
    "--inject-claude-md", action="store_true",
    help="Force-append a clu section to project CLAUDE.md (no prompt). "
         "Idempotent if section already exists.",
)
inject_group.add_argument(
    "--no-claude-md", action="store_true",
    help="Skip the CLAUDE.md prompt and write a decline marker so "
         "future inits don't re-ask.",
)
```

Note: `dest` for `--inject-claude-md` is `inject_claude_md` (argparse converts hyphens). Same for `--no-claude-md` → `no_claude_md`. The helper above uses `getattr(args, ..., False)` to tolerate other commands' args namespaces that don't have these fields.

### 6. Run the suite

All new tests green; existing init / queue-add tests still pass.

### 7. `/simplify`

Multi-file diff with interactive prompt logic. Definitely run it. Watch for: dead branches in `_maybe_handle_claude_md_injection`, redundant `getattr` defaults, opportunities to extract the `decline_marker.parent.mkdir + touch` pair into a helper if it grows.

### 8. Commit

Title: `clu-monitor: CLI hints + CLAUDE.md injection prompt`.
Body references `closes #19 phase 2 of 3`.

## Verification before `clu complete`

Re-run the full suite. The new test count delta should match the count in the produce step (~14 new tests). Confirm all existing init tests stayed green — the `_maybe_handle_claude_md_injection` call adds a code path before `return 0`, so any test that asserts on the exact output of `cmd_init` might need updating.

## Acceptance

- [ ] `clu init` prints monitor tip when marker absent + TTY, suppresses otherwise
- [ ] `clu queue add` prints monitor tip (once for multi-arg) under the same conditions
- [ ] First `clu init` in a project with CLAUDE.md prompts for injection
- [ ] Decline writes `.no-claude-md` marker; no re-prompt on next init
- [ ] `--inject-claude-md` forces inject without prompt (idempotent on existing section)
- [ ] `--no-claude-md` writes decline marker without prompt
- [ ] Non-TTY stdin skips the prompt entirely (no decline marker written — operator hasn't declined)
- [ ] Section append uses canonical template verbatim
- [ ] All new tests pass; existing init / queue-add tests still pass
- [ ] One commit referencing `#19 phase 2 of 3`
