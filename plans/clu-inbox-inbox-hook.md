# clu-inbox-inbox-hook — inbox primitive + bundled hook + CLI install + skill rewrite

You are phase `inbox-hook` of the `clu-inbox` plan. First of three. Closes part of [#20](https://github.com/smabe/end-of-line/issues/20).

Read the master plan first. This phase ships the foundational pieces. Subsequent phases consume the inbox API (`inbox.write_event` from `notify.py` integration) and rewrite docs.

## Locked decisions (do NOT re-litigate)

Full context in the master plan and [#20](https://github.com/smabe/end-of-line/issues/20). Summary:

- **Inbox path**: `~/.config/clu/inbox/` (unprocessed) + `~/.config/clu/inbox/processed/` (surfaced). XDG-respecting via same pattern as `monitor.marker_path()`.
- **Event filename**: `<utc_iso>-<kind>-<short_id>.json`. Short id is 8-char random hex (not monotonic) — race-free under concurrent writes.
- **Event JSON shape**: `{id, schema_version: 1, type, plan_slug, project_root, timestamp, summary, details}`.
- **Hook event**: `UserPromptSubmit`.
- **Hook output**: stdout JSON `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "..."}}`. Empty inbox → exit 0, no stdout (no-op).
- **Hook script location** (after install): the bundled package's resource. Install uses `importlib.resources.files("end_of_line").joinpath("hooks/clu_inbox_surface.py")` resolved at install time, baked as absolute path into settings.json.
- **Hook script behavior**: cap at 20 most recent events; if >20, summary line "+ N older events (see `clu inbox` for all)". additionalContext truncated to <10K chars with a similar footer.
- **CWD/project detection**: `git rev-parse --show-toplevel` first, fall back to `os.getcwd()`. Hook exits 0 with no stdout if no events match the resolved project_root.
- **Marker v2 schema**: `{schema_version: 2, hook_installed_at, hook_path, settings_json_path}`. v1 markers (with `schedule_id`) treated as "needs reinstall" by `is_scheduled`.
- **Settings.json format**: detect nested-array vs flat-array (existing entries indicate which) and preserve.
- **Skill rewrite**: workflow drops `/schedule create` entirely, replaces with `clu install-hook` via Bash.

## Read first

- `end_of_line/monitor.py` — marker primitive (v1). The migration path goes here.
- `end_of_line/registry.py:30-50` — XDG path pattern + `_mutate` shape. Mirror for inbox.
- `end_of_line/state.py` — `locked_json`, `utcnow`, `parse_iso`. Reuse.
- `end_of_line/notify.py:87-112` — `notify()` function. Phase 2 hooks the inbox here; phase 1 ships the API only (no wiring yet).
- `end_of_line/cli.py:626-749` — `BUNDLED_SKILLS` and `cmd_install_skill`. The install-hook command sits nearby in the dispatch table.
- `end_of_line/skills/clu-monitor/SKILL.md` — current (broken) workflow. Phase 1 rewrites this.
- `~/.claude/settings.json` reference structure (verified in conversation 2026-05-12): nested-array `hooks.<event>[].hooks[].{type,command,timeout?}`.
- Claude Code hooks docs (linked in #20): UserPromptSubmit semantics, hookSpecificOutput schema.

## Produce

### 1. TDD: failing tests first

New file `tests/test_inbox.py`:

- `test_inbox_path_respects_xdg_config_home` — same XDG pattern test as monitor.py.
- `test_write_event_creates_file_with_correct_shape` — call `write_event(type="halted", plan_slug="foo", project_root="/x", summary="...", details={"reason": "max_attempts"})`; assert file exists at `~/.config/clu/inbox/<ts>-halted-<id>.json`, JSON has all required fields, `schema_version: 1`, `id` matches filename portion.
- `test_write_event_race_free_filenames` — write 10 events with same kind in tight loop; assert 10 distinct filenames.
- `test_read_unprocessed_returns_all_in_inbox` — write 3 events, call `read_unprocessed()`, assert 3 returned, sorted by timestamp ascending.
- `test_read_unprocessed_excludes_processed` — write 3 events, mark 1 processed, `read_unprocessed()` returns 2.
- `test_mark_processed_moves_file_to_subdir` — write event, call `mark_processed(event_id)`, assert file no longer in `inbox/` but is in `inbox/processed/`.
- `test_mark_processed_idempotent_when_missing` — call `mark_processed("nonexistent")`; no error.
- `test_list_for_project_filters_by_root` — write events with different project_roots; `list_for_project("/x")` returns only those matching.
- `test_list_for_project_handles_missing_inbox` — no inbox dir at all → returns empty list, no error.

New file `tests/test_inbox_hook.py` (the hook script — runs the script as a subprocess for fidelity):

- `test_hook_empty_inbox_exits_zero_no_stdout` — empty inbox, run hook with mock stdin payload, exit code 0, stdout empty.
- `test_hook_surfaces_events_for_current_project` — write 2 events for `/proj-a`, 1 for `/proj-b`; invoke hook with `cwd=/proj-a`; stdout JSON has `additionalContext` mentioning the 2 events, NOT the 1 from /proj-b.
- `test_hook_marks_surfaced_events_processed` — write 2 events; invoke hook; subsequent `read_unprocessed()` returns 0.
- `test_hook_caps_at_20_events` — write 25 events for current project; hook output mentions 20 + footer "+ 5 older events".
- `test_hook_truncates_additional_context_at_10k_chars` — write events with very long summaries; hook output ≤10K chars with truncation footer.
- `test_hook_falls_back_to_cwd_when_no_git` — run from a non-repo dir; project_root resolves to CWD, not crash.
- `test_hook_exits_zero_on_crash` — corrupt one inbox JSON file; hook logs to its error log, exits 0 (graceful fail).
- `test_hook_runs_under_500ms_with_50_events` — write 50 events; measure subprocess wall time; assert <500ms (use generous CI buffer if needed — note brittleness).

New file `tests/test_install_hook.py`:

- `test_install_hook_creates_settings_json_when_absent` — no settings.json; `clu install-hook`; settings.json now has `hooks.UserPromptSubmit` with our entry.
- `test_install_hook_preserves_nested_array_format` — settings.json with existing nested-array hooks (SessionStart entry like the operator's real config); install adds UserPromptSubmit in same nested-array shape.
- `test_install_hook_preserves_flat_array_format` — settings.json with flat-array hooks; install matches that style.
- `test_install_hook_idempotent_by_path_match` — install twice; settings.json has exactly one entry for our hook path.
- `test_install_hook_does_not_clobber_other_user_hooks` — settings.json with user's own PreToolUse + SessionStart entries; after install, both untouched, new UserPromptSubmit added.
- `test_install_hook_refuses_in_non_tty` — monkeypatch `sys.stdout.isatty()` to False; `clu install-hook` exits non-zero with a clear message.
- `test_install_hook_writes_atomically` — verify temp-then-rename pattern (mock os.rename, assert called).
- `test_install_hook_refuses_on_malformed_settings_json` — write garbage to settings.json; install errors with helpful message, does not overwrite.
- `test_uninstall_hook_removes_only_our_entry` — install + uninstall round-trip; user's other hooks intact; UserPromptSubmit entry removed (or array empty/removed if it was the only one).
- `test_uninstall_hook_idempotent_when_absent` — uninstall when nothing is installed; no error.

New file `tests/test_monitor_migration.py`:

- `test_is_scheduled_returns_false_for_v1_marker` — write `{"schema_version": 1, "schedule_id": "x", ...}`; `is_scheduled()` returns False.
- `test_record_scheduled_v2_shape` — call new `record_hook_installed(hook_path, settings_json_path)`; marker has schema_version=2 with expected fields.
- `test_v1_marker_overwritten_by_install` — v1 marker present; run install; marker now v2 atomically (no transient state where both exist).

Update `tests/test_install_skill.py` only if BUNDLED_SKILLS or hook bundling needs new resource paths (no — skill list unchanged).

Run suite — all new tests must FAIL.

### 2. Implement `end_of_line/inbox.py`

```python
"""Per-event inbox surfaced to active Claude Code sessions via UserPromptSubmit hook.

Pattern: clu writes one JSON file per event into `~/.config/clu/inbox/`. The bundled
UserPromptSubmit hook script reads, filters by project_root, formats summary, marks
processed. Mark-and-sweep dedup via `processed/` subdirectory.
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from . import state as st

SCHEMA_VERSION = 1


def inbox_root() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "clu" / "inbox"


def _processed_root() -> Path:
    return inbox_root() / "processed"


def write_event(
    *, type: str, plan_slug: str, project_root: str,
    summary: str, details: dict | None = None,
    inbox: Path | None = None,
) -> str:
    """Write a single event file, returns the event id."""
    inbox_dir = inbox or inbox_root()
    inbox_dir.mkdir(parents=True, exist_ok=True)
    event_id = f"evt-{secrets.token_hex(4)}"
    ts = st.utcnow()
    # filename: <ts>-<type>-<short>.json — sortable + collision-free
    safe_ts = ts.replace(":", "").replace("-", "")
    filename = f"{safe_ts}-{type}-{event_id[-8:]}.json"
    payload = {
        "id": event_id, "schema_version": SCHEMA_VERSION, "type": type,
        "plan_slug": plan_slug, "project_root": project_root,
        "timestamp": ts, "summary": summary, "details": details or {},
    }
    target = inbox_dir / filename
    tmp = inbox_dir / f".{filename}.tmp"
    tmp.write_text(json.dumps(payload, indent=2))
    os.rename(tmp, target)
    return event_id


def read_unprocessed(inbox: Path | None = None) -> list[dict]:
    inbox_dir = inbox or inbox_root()
    if not inbox_dir.exists():
        return []
    out = []
    for p in sorted(inbox_dir.iterdir()):
        if p.is_dir() or p.name.startswith(".") or not p.name.endswith(".json"):
            continue
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue  # tolerant — corrupt events don't take down the surfacer
    return out


def mark_processed(event_id: str, inbox: Path | None = None) -> None:
    inbox_dir = inbox or inbox_root()
    if not inbox_dir.exists():
        return
    processed = _processed_root()
    processed.mkdir(parents=True, exist_ok=True)
    for p in inbox_dir.iterdir():
        if p.is_dir() or p.name.startswith(".") or not p.name.endswith(".json"):
            continue
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if data.get("id") == event_id:
            os.rename(p, processed / p.name)
            return  # idempotent: not-found is silent


def list_for_project(project_root: str, inbox: Path | None = None) -> list[dict]:
    target = str(Path(project_root).resolve())
    return [e for e in read_unprocessed(inbox) if e.get("project_root") == target]
```

### 3. Implement `end_of_line/hooks/clu_inbox_surface.py`

Standalone script (must run from `python3 -m end_of_line.hooks.clu_inbox_surface` to inherit installed package; OR ship as a `__main__` block in `end_of_line/hooks/__init__.py` and install with that invocation).

```python
"""UserPromptSubmit hook: surface unprocessed clu inbox events into Claude's context.

Reads stdin (JSON hook payload), filters inbox to events for current project,
emits hookSpecificOutput JSON, marks events processed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from .. import inbox

MAX_EVENTS = 20
MAX_CONTEXT_CHARS = 9500  # buffer under the 10K cap
_LOG_PATH = Path.home() / ".config" / "clu" / "inbox_hook.log"


def _resolve_project_root() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return os.getcwd()


def _format_event(e: dict) -> str:
    ts = e.get("timestamp", "?")
    slug = e.get("plan_slug", "?")
    kind = e.get("type", "?")
    summary = e.get("summary", "")
    return f"  [{ts}] {slug} / {kind}: {summary}"


def _build_context(events: Iterable[dict]) -> str:
    events = list(events)
    if not events:
        return ""
    capped = events[-MAX_EVENTS:]
    truncated_count = len(events) - len(capped)
    lines = ["clu inbox (unprocessed):"]
    lines.extend(_format_event(e) for e in capped)
    if truncated_count > 0:
        lines.append(f"  (+ {truncated_count} older events — run `clu inbox` to see all)")
    out = "\n".join(lines)
    if len(out) > MAX_CONTEXT_CHARS:
        out = out[:MAX_CONTEXT_CHARS] + "\n  (truncated)"
    return out


def main() -> int:
    try:
        # Read stdin (hook payload) but we don't actually need it for our logic.
        # Just consume to be a well-behaved hook.
        _ = sys.stdin.read()
        project_root = _resolve_project_root()
        events = inbox.list_for_project(project_root)
        context = _build_context(events)
        if not context:
            return 0  # no-op
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }
        sys.stdout.write(json.dumps(payload))
        for e in events[-MAX_EVENTS:]:
            inbox.mark_processed(e["id"])
        return 0
    except Exception as exc:  # graceful — never alarm the operator
        try:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _LOG_PATH.open("a") as f:
                f.write(f"{exc!r}\n")
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Add `end_of_line/hooks/__init__.py` (empty). Ensure pyproject.toml includes the new dir in package data (mirror the `skills/**` config).

### 4. CLI: `clu install-hook` and `clu uninstall-hook`

```python
def cmd_install_hook(args) -> int:
    if not sys.stdout.isatty():
        return _die(ExitCode.GENERIC, "install-hook requires an interactive shell")

    # Resolve absolute hook path at install time (the #9 dance).
    from importlib.resources import files
    hook_path = str(files("end_of_line").joinpath("hooks/clu_inbox_surface.py"))

    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{}\n")

    try:
        with settings_path.open() as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        return _die(ExitCode.GENERIC, f"settings.json is malformed: {exc}; refusing to edit")

    hooks = data.setdefault("hooks", {})
    ups = hooks.setdefault("UserPromptSubmit", [])

    # Detect existing format style by looking at any existing hook entries.
    nested_style = _detect_nested_style(hooks)

    # Idempotency: check for our path.
    our_cmd = f"python3 {hook_path}"
    if _hook_already_installed(ups, our_cmd):
        print(f"Hook already installed at {hook_path}")
        monitor.record_hook_installed(hook_path, str(settings_path))
        return ExitCode.OK

    entry = (
        {"hooks": [{"type": "command", "command": our_cmd, "timeout": 5}]}
        if nested_style
        else {"type": "command", "command": our_cmd}
    )
    ups.append(entry)

    # Atomic write.
    tmp = settings_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.rename(tmp, settings_path)

    monitor.record_hook_installed(hook_path, str(settings_path))
    print(f"Installed UserPromptSubmit hook → {hook_path}")
    print(f"Settings updated: {settings_path}")
    return ExitCode.OK


def cmd_uninstall_hook(args) -> int:
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return ExitCode.OK  # idempotent
    try:
        with settings_path.open() as f:
            data = json.load(f)
    except json.JSONDecodeError:
        return _die(ExitCode.GENERIC, "settings.json malformed; refusing to edit")

    ups = data.get("hooks", {}).get("UserPromptSubmit", [])
    filtered = [e for e in ups if not _is_our_hook_entry(e)]
    if len(filtered) == len(ups):
        print("clu inbox hook not present in settings.json")
    else:
        data["hooks"]["UserPromptSubmit"] = filtered
        tmp = settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.rename(tmp, settings_path)
        print("Uninstalled UserPromptSubmit hook")
    monitor.clear_marker()
    return ExitCode.OK
```

Helpers `_detect_nested_style`, `_hook_already_installed`, `_is_our_hook_entry` live alongside. Match against the absolute hook_path (idempotency contract).

Subparser registration goes near the other top-level commands. Both are no-arg.

### 5. Update `end_of_line/monitor.py`

Add `record_hook_installed(hook_path, settings_json_path)` writing schema v2.

Update `is_scheduled()` to return True ONLY for v2 markers:

```python
def is_scheduled(path: Path | None = None) -> bool:
    data = load_marker(path)
    return data is not None and data.get("schema_version") == 2
```

`load_marker` stays tolerant (returns None on v1; CLI hint emission keeps working — v1 marker now reads as "not scheduled" and tips fire).

### 6. Rewrite `end_of_line/skills/clu-monitor/SKILL.md`

Workflow becomes:

1. Check `~/.config/clu/monitor.json` for v2 marker. If present, print status and exit (idempotent).
2. If v1 marker present (legacy `/schedule` install): print "Migrating from legacy /schedule mechanism (no longer functional)" and proceed to install.
3. Run `clu install-hook` via Bash. Report success/failure.
4. Confirm: "Background monitoring active via UserPromptSubmit hook. Next time you queue plans and walk away, type anything when you return — Claude will see clu's events automatically."

Drop ALL /schedule references. Keep the proactive `description:` frontmatter (it's still right — invoke after walking-away contexts).

### 7. Run the suite — all green

Multi-file refactor + new modules. Existing skill-install tests stay green (skill list unchanged). New tests cover the new surface.

### 8. `/simplify` then commit

Title: `clu-inbox: hook+inbox in-session signaling, replaces broken /schedule`.
Body references `closes #20 phase 1 of 3`.

## Verification before `clu complete`

Per CLAUDE.md mandate #9: re-run full suite right before `clu complete`. Report final test count + explicit confirmation that existing monitor/notify/skill-install tests stayed green. Smoke-test the install once locally:

```bash
clu install-hook
cat ~/.claude/settings.json | jq '.hooks.UserPromptSubmit'
clu uninstall-hook
```

(Skip this step if running headless / no `jq` — the unit tests cover the same ground.)

## Acceptance

- [ ] `end_of_line/inbox.py` exposes write_event / read_unprocessed / mark_processed / list_for_project
- [ ] `end_of_line/hooks/clu_inbox_surface.py` is a working UserPromptSubmit hook
- [ ] `clu install-hook` idempotently adds the entry to `~/.claude/settings.json` preserving format
- [ ] `clu uninstall-hook` removes only our entry; other hooks untouched
- [ ] Marker schema v2; v1 markers treated as "needs reinstall"
- [ ] `/clu-monitor` skill rewritten — no `/schedule` references
- [ ] All new tests pass (~32 new); existing tests stay green
- [ ] One commit referencing `closes #20 phase 1 of 3`
