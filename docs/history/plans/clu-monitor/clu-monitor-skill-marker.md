# clu-monitor-skill-marker — `/clu-monitor` skill + marker file primitives

You are phase `skill-marker` of the `clu-monitor` plan. First of three phases. Closes part of [#19](https://github.com/smabe/end-of-line/issues/19).

Read the master plan first. This phase ships the foundational pieces (skill markdown + monitor module + bundle registration). Subsequent phases consume `monitor.is_scheduled()` and reference the installed skill.

## Locked decisions (do NOT re-litigate)

- Marker location: `~/.config/clu/monitor.json` resolved via the same XDG pattern as `registry.registry_path()` (cli.py / registry.py:30-33). Reuse the resolution helper if practical; otherwise mirror it exactly.
- Marker schema: `{schema_version: 1, scheduled_at, schedule_id, cadence}`. Use `state.locked_json` for atomic mutations (matches registry/queue pattern).
- Skill description is the **proactive trigger** — see the master plan's locked phrasing. Do not deviate; that wording is what makes Claude auto-invoke.
- Skill workflow: read marker → if present, exit with status; if absent, invoke `/schedule create` via Skill tool → write marker on success.
- `BUNDLED_SKILLS` becomes `("clu-phase", "plan", "brainstorm", "clu-monitor")` at `cli.py:626`.

## Read first

- `end_of_line/registry.py:30-33` — `registry_path()` XDG pattern. Copy the structure.
- `end_of_line/registry.py:36-50` — `_empty()`, `_load()`, `_mutate()`. Marker primitives mirror this shape but with a one-row schema (no `plans: []` array).
- `end_of_line/state.py` — `locked_json`, `validate_slug`, `utcnow`, `parse_iso`. Reuse.
- `end_of_line/cli.py:626` — `BUNDLED_SKILLS` tuple.
- `end_of_line/cli.py:705-715` — `cmd_install_skill --list` output. Adds clu-monitor automatically once it's in the tuple.
- `end_of_line/skills/plan/SKILL.md` and `end_of_line/skills/brainstorm/SKILL.md` — frontmatter style for the new skill markdown. Match the format exactly.
- `tests/test_install_skill.py` — `BUNDLED_SKILLS` count assertions to update.
- `tests/test_registry.py` — registry-test patterns. Use `isolate_registry`-style isolation; you'll need a parallel `isolate_monitor_marker` test helper (XDG_CONFIG_HOME monkeypatch).

## Produce

### 1. TDD: failing tests first

New file `tests/test_monitor.py`. Required cases:

- `test_marker_path_respects_xdg_config_home` — `XDG_CONFIG_HOME=/tmp/xdg` → marker_path is `/tmp/xdg/clu/monitor.json`. Unset → `~/.config/clu/monitor.json`.
- `test_is_scheduled_returns_false_when_absent` — fresh tmp_path, no marker file.
- `test_is_scheduled_returns_true_when_present` — write a valid marker, assert True.
- `test_is_scheduled_returns_false_when_corrupt` — write garbage to marker, assert False. (Don't raise — tolerant like `registry.load_entry_state`.)
- `test_is_scheduled_returns_false_when_schema_mismatch` — write `{"schema_version": 999, ...}`, assert False.
- `test_record_scheduled_writes_marker` — call `record_scheduled("sch-123", "*/15 * * * *")`; assert marker has schema_version=1, scheduled_at populated (ISO ts), schedule_id, cadence.
- `test_record_scheduled_overwrites_existing` — write a marker, call `record_scheduled` again with different values, assert the new values won.
- `test_clear_marker_removes_file` — write marker, call `clear_marker()`, assert file absent.
- `test_clear_marker_idempotent_when_absent` — call `clear_marker()` on a path with no file; no exception, no error.
- `test_load_marker_returns_dict_when_present` — `load_marker()` returns the marker dict for callers that want to display fields (used in cli-hints phase).
- `test_load_marker_returns_none_when_absent` — None, not exception.

Add `tests/helpers.py` (or wherever `isolate_registry` lives) a parallel `isolate_monitor_marker(test, tmp_path)` that sets `XDG_CONFIG_HOME` to `tmp_path` for the duration of the test.

Update `tests/test_install_skill.py`:
- Existing tests that assert on `BUNDLED_SKILLS` count or membership need updating to 4 skills.
- Add a case: `test_install_skill_installs_clu_monitor` — verifies the new skill name installs to `~/.claude/skills/clu-monitor/SKILL.md`.
- Add a case: `test_list_includes_clu_monitor` — `clu install-skill --list` output contains `clu-monitor`.

Run suite — new tests must FAIL.

### 2. Implement `end_of_line/monitor.py`

```python
"""Background-monitoring marker file.

A successful `/clu-monitor` invocation writes a marker at
`~/.config/clu/monitor.json` so subsequent invocations are
idempotent and clu CLI hints can suppress themselves when
monitoring is already scheduled. Account-wide, not per-project.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import state as st

SCHEMA_VERSION = 1


def marker_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "clu" / "monitor.json"


def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION}


def load_marker(path: Path | None = None) -> dict | None:
    """Return the marker dict, or None on any failure mode.

    Tolerant by design: corrupt JSON, missing file, schema mismatch —
    all return None so callers can treat "marker missing" and
    "marker unparseable" the same way.
    """
    path = path or marker_path()
    if not path.exists():
        return None
    try:
        return st.load(path, expected_version=SCHEMA_VERSION)
    except (OSError, ValueError, st.SchemaVersionMismatch):
        return None


def is_scheduled(path: Path | None = None) -> bool:
    return load_marker(path) is not None


def record_scheduled(
    schedule_id: str, cadence: str, *, path: Path | None = None,
) -> None:
    path = path or marker_path()
    with st.locked_json(
        path, expected_version=SCHEMA_VERSION, empty=_empty,
    ) as data:
        data["scheduled_at"] = st.utcnow()
        data["schedule_id"] = schedule_id
        data["cadence"] = cadence


def clear_marker(path: Path | None = None) -> None:
    path = path or marker_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
```

Note: the `locked_json` primitive expects a callable `empty` returning the seed dict. Confirm signature matches — read `state.locked_json` to verify.

### 3. Write `end_of_line/skills/clu-monitor/SKILL.md`

Frontmatter must match the established pattern. Body content:

```markdown
---
name: clu-monitor
description: |
  Use proactively when the user is starting autonomous plan execution
  with clu (after `clu queue add` or `clu init`) and
  `~/.config/clu/monitor.json` is absent. Also use when the user says
  "monitor clu", "notify me when X completes", or describes walking
  away. Idempotent — checks for an existing schedule before creating
  one.
user_invocable: true
---

## You are the clu monitoring setup skill

This skill schedules a background routine that pings the operator on
iMessage when clu needs human attention — halted plans, unanswered
blockers, stalled claims. After running this once per machine, the
operator can queue plans and walk away.

## Workflow

### 1. Check if monitoring is already scheduled

Run:
```bash
test -f ~/.config/clu/monitor.json && cat ~/.config/clu/monitor.json
```

If the file exists and contains valid JSON with `schedule_id` and
`scheduled_at`, monitoring is already set up. Print:

> Monitoring already scheduled at <scheduled_at> (id: <schedule_id>,
> cadence: <cadence>). To reset, delete `~/.config/clu/monitor.json`
> and re-run `/clu-monitor`.

Exit. Do NOT create a duplicate schedule.

### 2. Compose the canonical monitoring prompt

The routine that `/schedule` runs each tick should execute this prompt
(verbatim — do not modify):

> Check clu state by running `clu list` and `clu queue list`. Send the
> user an iMessage if: (a) any plan has status HALTED or
> HALTED_REPLAN — include the slug + halt reason from the most recent
> event; (b) any plan has an open blocker (no `consumed: true`) for
> more than 30 minutes — include the question + option list; (c) any
> plan has a stalled claim (lease_expires past current time with
> status RUNNING). Otherwise: stay silent. Do NOT send "all clear"
> or heartbeat messages.

### 3. Invoke `/schedule` to create the routine

Default cadence: `*/15 8-21 * * *` (every 15 minutes during
08:00-22:00 local). Matches clu's existing quiet_hours convention.

Use the Skill tool to invoke `/schedule create` with the prompt and
cadence. Pass the full canonical prompt — the routine is a remote
agent and has no shared context with this session.

### 4. Record the marker

On successful schedule creation, capture the `schedule_id` from
`/schedule`'s response and write the marker:

```bash
python3 -c "from end_of_line import monitor; monitor.record_scheduled('<schedule_id>', '*/15 8-21 * * *')"
```

If `/schedule create` fails (auth, quota, etc.), do NOT write the
marker. Report the failure to the user with the next steps to
diagnose. The next `/clu-monitor` invocation will retry cleanly.

### 5. Confirm to the user

Print a one-screen summary:

> Background monitoring scheduled. clu will iMessage you on halts,
> stuck blockers, and stalled claims (silent otherwise). Status
> file: `~/.config/clu/monitor.json`. To pause: `/schedule pause
> <schedule_id>`. To remove: delete the status file and the
> /schedule routine.

## Failure modes

- **`/schedule` skill not available.** Some Claude Code installs may
  not have the schedule skill present. Detect by trying to invoke and
  catching the missing-skill error. Tell the user: "The /schedule
  skill is required but not available in this Claude Code install.
  See https://docs.claude.com/claude-code for setup."
- **User declines to authorize the schedule.** /schedule will prompt
  before creating the routine (it costs money on their account). If
  the user declines, do NOT write the marker. Exit cleanly with the
  message "Monitoring not scheduled (declined). Re-run /clu-monitor
  whenever you're ready."
- **Marker write fails.** Disk full / permissions issue. The schedule
  exists but the marker doesn't — next /clu-monitor invocation would
  create a duplicate. Tell the user explicitly: "Schedule created but
  marker file write failed at <path>. To prevent duplicates, manually
  create the file with `python3 -c \"from end_of_line import monitor;
  monitor.record_scheduled('<id>', '<cadence>')\"`."
```

### 4. Update `BUNDLED_SKILLS` at `cli.py:626`

```python
BUNDLED_SKILLS = ("clu-phase", "plan", "brainstorm", "clu-monitor")
```

The install-skill --list / --only paths handle this automatically once the tuple includes the name — no other changes in cli.py needed for this phase.

### 5. Verify package data includes the new skill directory

Check `pyproject.toml`'s `package-data` / `include` config. The existing pattern (from Day 5's bundle-plan-skill) should already cover `end_of_line/skills/**/*.md` — confirm by `pipx install -e .` and running `clu install-skill --list`, which should print all four skills.

### 6. Run the suite — all green

Multi-file diff. The install-skill test count delta is the regression gate (existing tests asserting 3 skills must be updated to 4).

### 7. `/simplify`

### 8. Commit

Title: `clu-monitor: bundled skill + marker file primitives`.
Body: lists the new module, the new bundled skill, BUNDLED_SKILLS change.
References `closes #19 phase 1 of 3` (don't close the issue yet — cli-hints + docs phases still pending).

## Verification before `clu complete`

Per CLAUDE.md mandate #9: re-run the full suite before `clu complete`. Confirm install-skill tests stayed green AND the new monitor tests all pass. Report final test count.

## Acceptance

- [ ] `end_of_line/monitor.py` exposes marker_path / is_scheduled / record_scheduled / clear_marker / load_marker
- [ ] `end_of_line/skills/clu-monitor/SKILL.md` exists with proactive frontmatter
- [ ] `BUNDLED_SKILLS` includes `"clu-monitor"` at cli.py:626
- [ ] `clu install-skill --list` prints four skills
- [ ] `clu install-skill --only clu-monitor` works
- [ ] All new tests pass; existing install-skill tests updated for count
- [ ] One commit referencing `#19 phase 1 of 3`
