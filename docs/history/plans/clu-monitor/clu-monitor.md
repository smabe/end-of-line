# clu-monitor — bundled `/clu-monitor` skill + discoverability hardening

Closes [#19](https://github.com/smabe/end-of-line/issues/19). Ship a
bundled `/clu-monitor` skill that schedules background notifications
via Claude Code's `/schedule`, plus the four-layer discoverability
hardening (skill description, marker-suppressed CLI hints, project
CLAUDE.md injection, opt-in) that makes Claude actually invoke it at
the right moment.

## Goal

After this plan ships:

```
$ pipx install clu
$ clu install-skill
Installed clu-phase skill to ~/.claude/skills/clu-phase/SKILL.md
Installed plan skill to ~/.claude/skills/plan/SKILL.md
Installed brainstorm skill to ~/.claude/skills/brainstorm/SKILL.md
Installed clu-monitor skill to ~/.claude/skills/clu-monitor/SKILL.md

$ clu init --project . --plan my-feature
Initialized ./plans/.orchestrator/my-feature.state.json

  Tip: run /clu-monitor for background notifications on halts and blockers.

This project doesn't have a clu section in CLAUDE.md yet. Adding one
helps future Claude sessions orient on clu's workflow. May I append
a short section? [y/N]: y
Added clu section to ./CLAUDE.md
```

Now the operator opens Claude Code, says "build feature X with clu," and Claude — having seen the CLAUDE.md context AND the CLI tip — proactively invokes `/clu-monitor` before walking away. The operator never types `/clu-monitor` themselves.

## Locked design decisions (do NOT re-litigate)

Full design lives in [#19](https://github.com/smabe/end-of-line/issues/19). Summary:

### Marker file (shared primitive)
- Path: `~/.config/clu/monitor.json` (XDG-respecting, parallel to `registry.json` path resolution).
- Schema: `{schema_version: 1, scheduled_at, schedule_id, cadence}`. Account-wide, not per-project.
- Helpers in new `end_of_line/monitor.py`: `marker_path()`, `is_scheduled()`, `record_scheduled(schedule_id, cadence)`, `clear_marker()`, `load_marker()`.

### `/clu-monitor` skill
- Location: `end_of_line/skills/clu-monitor/SKILL.md`.
- Added to `BUNDLED_SKILLS` tuple at `cli.py:626`.
- `description:` frontmatter is the proactive trigger. Use phrasing like:
  > "Use proactively when the user is starting autonomous plan execution with clu (after `clu queue add` or `clu init`) and `~/.config/clu/monitor.json` is absent. Also use when the user says 'monitor clu', 'notify me when X completes', or describes walking away. Idempotent — checks for an existing schedule before creating one."
- Workflow:
  1. Check `~/.config/clu/monitor.json` via `monitor.is_scheduled()`. If present, print status (slug, cadence, scheduled_at) and exit.
  2. Compose the canonical monitoring prompt (see below).
  3. Invoke `/schedule create` via the Skill tool with the prompt + cadence `*/15 8-21 * * *` (every 15 min during 08:00-22:00 local).
  4. On success, call `monitor.record_scheduled(schedule_id, cadence)`.
  5. Print summary.
- **Canonical monitoring prompt** (verbatim, embedded in the skill):
  > Check clu state by running `clu list` and `clu queue list`. Send the user an iMessage if: (a) any plan has status HALTED or HALTED_REPLAN — include the slug + halt reason from the most recent event; (b) any plan has an open blocker (no `consumed: true`) for >30 minutes — include question + option list; (c) any plan has a stalled claim (lease_expires past current time with status RUNNING). Otherwise: stay silent. Do NOT send "all clear" / heartbeat messages.

### CLI hint emission
- After `clu init` and `clu queue add` succeed, conditional tip emission:
  ```
  Tip: run /clu-monitor for background notifications on halts and blockers.
  ```
- Conditions for printing: `monitor.is_scheduled()` returns False AND `sys.stdout.isatty()` is True.
- Why TTY check: worker subprocesses inherit clu commands' output via the dispatch logger; printing tips there is noise that future workers' summaries would echo back.

### CLAUDE.md injection (opt-in, prompted on first init)
- Trigger: `clu init` succeeds AND running on a TTY AND `<project_root>/CLAUDE.md` exists (don't create one — only edit existing) AND it has no `## clu` heading AND no decline marker at `<plan_dir>/.orchestrator/.no-claude-md`.
- Behavior: print the prompt, read one line of input, accept `y`/`Y` as yes, anything else as no. On no, write the decline marker (empty file is fine — existence is the signal).
- Non-interactive override: `--inject-claude-md` flag forces yes without prompt; `--no-claude-md` forces no without prompt and writes the decline marker.
- Canonical CLAUDE.md section (appended verbatim, two blank lines before for separation):
  ```markdown


  ## clu

  This project uses clu for autonomous plan execution.

  - `clu queue add <slug>` to enqueue a plan; cron dispatches on each tick.
  - `clu queue list` for pending; `clu list` for fleet status.
  - Run `/clu-monitor` once per machine for background notifications on
    halts and blockers (status: `~/.config/clu/monitor.json`).
  - The `/plan` and `/brainstorm` skills (bundled via `clu install-skill`)
    are the canonical authoring + pre-planning entry points.
  ```

### Out of scope
- Per-project schedule routines (one global schedule monitors all projects).
- Slack / non-iMessage notification backends (#11).
- Auto-injection without prompt (rejected as too invasive for v1).
- Custom cadence on first install (user can `/schedule update` later).

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| skill-marker | `clu-monitor-skill-marker.md` | New `end_of_line/monitor.py` with marker primitives. New `end_of_line/skills/clu-monitor/SKILL.md`. Add `"clu-monitor"` to `BUNDLED_SKILLS`. Update `clu install-skill --list` test to expect 4 skills. Tests for marker read/write/idempotency, schema validation, XDG path resolution. | 2.5h |
| cli-hints | `clu-monitor-cli-hints.md` | Hint emission in `cmd_init` + `cmd_queue_add` (TTY + marker-absent gate). CLAUDE.md injection logic in `cmd_init`: detection of existing `## clu` section, decline-marker check, interactive prompt, append. New `--inject-claude-md` / `--no-claude-md` argparse flags. Tests cover all branches including non-TTY, decline-marker, marker-present suppression. | 3h |
| docs | `clu-monitor-docs.md` | README quickstart adds `/clu-monitor` as the post-install step. `docs/operations.md` walkthrough including marker file lifecycle and clearing it. `docs/contract.md` mentions the marker schema if user-visible (probably yes — operators may inspect it). Skills section of operations.md updated to mention four bundled skills. | 1h |

Total estimate: ~6.5h across 3 sessions.

## Failure modes to anticipate

- **`/schedule` skill invocation from within `/clu-monitor`.** Skills calling other skills via the Skill tool is supported (see the harness's `Skill` tool description), but the exact handoff shape has edge cases. The skill MUST report the `/schedule` invocation's exit status; if `/schedule create` errors (e.g., quota exceeded, auth issue), don't write the marker — leave it absent so the next attempt retries. Test by simulating `/schedule` failure in the skill's example flow.
- **Marker drift.** A schedule could be deleted out-of-band (user runs `/schedule delete`); the marker would lie about being scheduled. v1 trusts the marker; `clu-monitor` re-invocation prints "already scheduled at <ts>" even if the routine no longer exists. Mitigation: include a `clear_marker()` helper and document `clu install-skill --list` and/or `cat ~/.config/clu/monitor.json` so operators can manually reset. A "verify schedule still exists" check is out of scope for v1 — coupling clu to /schedule's introspection API would be premature.
- **CLAUDE.md doesn't exist in the project.** Skip the prompt entirely. Don't auto-create — that's too invasive. Future enhancement could offer to create it, but v1 only appends to existing.
- **CLAUDE.md already has a `## clu` section.** Skip the prompt silently. Detection: grep for the exact line `## clu` (case-insensitive, anchored to line start, anywhere in the file).
- **CLAUDE.md has the section but with stale content.** Out of scope — clu only appends new sections, never migrates existing ones. Document that operators can re-run via `--inject-claude-md` to re-prompt (which they'd then decline if they want manual control).
- **Decline marker location.** `<plan_dir>/.orchestrator/.no-claude-md` is per-project (since the prompt was project-scoped). NOT per-user, so the operator gets re-prompted in each new project — which is correct (CLAUDE.md is per-project).
- **Stdin not a tty but stdout IS a tty (rare).** Use `sys.stdin.isatty()` for the interactive-prompt check, NOT `sys.stdout.isatty()`. The prompt needs to READ input; output going to a tty doesn't matter if we can't read from one. Use `sys.stdout.isatty()` for the *hint emission* check (it's about whether a human will see the tip).
- **Injection race.** Two `clu init` calls in different terminals could both append the section. Mitigation: read CLAUDE.md, check for `## clu`, lock-free append. If race produces duplicates, easy to clean up manually — not worth a lock for a one-time operation.
- **`record_scheduled` failure after `/schedule create` succeeded.** Schedule exists but marker doesn't. Next `/clu-monitor` would try to create another. Mitigation: write marker FIRST with a placeholder, then call `/schedule create`, then update marker with the real schedule_id. If the create fails, delete the marker. Test the exact ordering — this is the load-bearing piece for idempotency.
- **Hints rendered as worker output.** If a worker dispatched by clu runs `clu queue add` for some reason (Day-5+ supports this via `clu spawn`), the hint MUST be suppressed. The TTY check covers this — worker subprocesses pipe stdout to a log file, so `isatty()` returns False.
