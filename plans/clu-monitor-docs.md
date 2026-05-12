# clu-monitor-docs — README + operations.md walkthrough

You are phase `docs` of the `clu-monitor` plan. Third of three. Closes [#19](https://github.com/smabe/end-of-line/issues/19).

Phases 1-2 shipped the skill, marker, hints, and injection. This phase writes the docs that make `/clu-monitor` discoverable to operators who arrive via the README rather than the in-session tips.

Read the master plan first. Do not redesign.

## Locked decisions (do NOT re-litigate)

- README: one-line setup step in the quickstart, in the same section that mentions `clu install-skill`. Don't dedicate a top-level section to monitoring — it's one command and a status file.
- `docs/operations.md`: full walkthrough including marker lifecycle (where it lives, how to inspect, how to clear) and the CLAUDE.md injection prompt's behavior.
- `docs/contract.md`: marker schema documented if user-visible (yes — operators may inspect it for debugging). Add to the existing "Schemas" section.
- `docs/reference.md`: `end_of_line.monitor` module public surface listed.
- No code changes in this phase. Pure documentation.

## Read first

- `README.md` — find the quickstart / install section. Look for where `clu install-skill` is mentioned.
- `docs/operations.md` — find the structure (sections, ordering). Add a new "Background monitoring" section after notifications/quiet hours, before troubleshooting.
- `docs/contract.md` — find the "Schemas" or equivalent section listing state.json, registry.json, queue.json. Add a monitor.json entry.
- `docs/reference.md` — find the per-module public-surface list. Add the `monitor` module.
- `docs/_outline.md` — confirm the structural contract for the docs library; the new content must fit existing conventions (heading levels, code-fence style).
- The shipped `end_of_line/monitor.py` from phase 1 — source of truth for the marker schema and helpers.
- The shipped `end_of_line/skills/clu-monitor/SKILL.md` from phase 1 — for cross-references.
- The shipped CLAUDE.md injection template from phase 2 in cli.py — quote it in the operations walkthrough.

## Produce

### 1. README quickstart edit

Find the install section. After the existing `clu install-skill` line, add:

```markdown
After installing the skills, run `/clu-monitor` once in Claude Code to
schedule background notifications on halts and blockers. Idempotent —
re-running prints the current schedule status. State file:
`~/.config/clu/monitor.json`.
```

If the section already enumerates the bundled skills, update the count from 3 to 4 and add `clu-monitor` to the list.

### 2. `docs/operations.md` — new "Background monitoring" section

Place after the "Notifications" or "Quiet hours" section (whichever is later), before "Troubleshooting." Section content:

```markdown
## Background monitoring

The clu LaunchAgent ticks every minute and dispatches workers, but
between those ticks no AI sits in the loop watching for halts. If a
plan halts at 02:15, the existing iMessage flow fires — but if a
worker is stuck on a blocker for hours because the operator missed
the iMessage, nothing escalates.

`/clu-monitor` (bundled as of <DATE>) closes that gap by scheduling
a Claude Code routine via `/schedule`. The routine runs `clu list`
and `clu queue list` every 15 minutes (default cadence:
`*/15 8-21 * * *`, respecting the same quiet hours convention) and
iMessages the operator if:

- Any plan has status `HALTED` or `HALTED_REPLAN`
- Any plan has an open blocker un-consumed for more than 30 minutes
- Any plan has a stalled claim (lease expired with status `RUNNING`)

Otherwise the routine stays silent. No "all clear" pings.

### Setup

```bash
$ clu install-skill   # one-time, installs /clu-phase /plan /brainstorm /clu-monitor
$ # then, in a Claude Code session:
$ /clu-monitor
Background monitoring scheduled.
Status file: ~/.config/clu/monitor.json
```

The marker file at `~/.config/clu/monitor.json` records the
`schedule_id` so re-running `/clu-monitor` is idempotent.

### Status, pause, reset

```bash
$ cat ~/.config/clu/monitor.json
{
  "schema_version": 1,
  "scheduled_at": "2026-05-12T19:00:00Z",
  "schedule_id": "sch-...",
  "cadence": "*/15 8-21 * * *"
}

# To pause without removing the schedule:
$ /schedule pause sch-...

# To remove entirely (and free Claude Code to schedule a new one later):
$ /schedule delete sch-...
$ rm ~/.config/clu/monitor.json
```

### CLI tips

`clu init` and `clu queue add` both print a one-line tip
recommending `/clu-monitor` when the marker file is absent. The tip
is suppressed when:

- Monitoring is already scheduled (marker file present), OR
- Output is not a TTY (workers running clu commands in dispatch
  subprocesses see no tip — keeps log files clean)

### Project CLAUDE.md integration

On the first `clu init` in a project, clu offers to append a `## clu`
section to the project's `CLAUDE.md` (if one exists). The section
helps future Claude Code sessions orient on the project's clu
workflow — relevant across `/clear` boundaries where in-session
context is gone.

The prompt fires once per project. Decline once, and a marker at
`<plan_dir>/.orchestrator/.no-claude-md` suppresses future prompts.
Flag overrides:

- `clu init --inject-claude-md ...` — force inject, no prompt.
- `clu init --no-claude-md ...` — write the decline marker, no prompt.

The injected section is appended verbatim (never overwrites existing
content) and matches:

\`\`\`markdown
## clu

This project uses clu for autonomous plan execution.

- `clu queue add <slug>` to enqueue a plan; cron dispatches on each tick.
- `clu queue list` for pending; `clu list` for fleet status.
- Run `/clu-monitor` once per machine for background notifications on
  halts and blockers (status: `~/.config/clu/monitor.json`).
- The `/plan` and `/brainstorm` skills (bundled via `clu install-skill`)
  are the canonical authoring + pre-planning entry points.
\`\`\`
```

(Substitute the actual ship date for `<DATE>`.)

### 3. `docs/contract.md` — marker schema entry

Find the section that documents `state.json`, `registry.json`, `queue.json` schemas. Add a parallel entry:

```markdown
### `~/.config/clu/monitor.json`

Marks that `/clu-monitor` has scheduled the background-monitoring
routine. Account-wide (not per-project). Absent file = monitoring
not set up; clu CLI commands emit a one-line tip recommending
`/clu-monitor` when this file is absent and stdout is a TTY.

```json
{
  "schema_version": 1,
  "scheduled_at": "2026-05-12T19:00:00Z",
  "schedule_id": "sch-...",
  "cadence": "*/15 8-21 * * *"
}
```

| Field | Meaning |
|---|---|
| `scheduled_at` | ISO UTC timestamp of marker creation |
| `schedule_id` | The `/schedule` routine ID for management via `/schedule pause / delete` |
| `cadence` | The cron expression passed to `/schedule create` |

Idempotency: `/clu-monitor` checks for this file before invoking
`/schedule create`. A successful create writes the marker; a failed
create does not. To reset (e.g. after manually deleting the
schedule), delete this file and re-run `/clu-monitor`.

Helpers in `end_of_line/monitor.py`:
`marker_path`, `is_scheduled`, `load_marker`, `record_scheduled`,
`clear_marker`.
```

### 4. `docs/reference.md` — module entry

Find the per-module list. Add an entry for `end_of_line.monitor`:

```markdown
### `end_of_line.monitor`

Background-monitoring marker file (account-wide, not per-project).

| Function | Purpose |
|---|---|
| `marker_path() -> Path` | XDG-respecting location (`$XDG_CONFIG_HOME/clu/monitor.json` or `~/.config/...`) |
| `is_scheduled() -> bool` | True iff marker exists, valid JSON, schema_version matches |
| `load_marker() -> dict \| None` | Marker contents, None on any failure |
| `record_scheduled(schedule_id, cadence)` | Atomic write via `state.locked_json` |
| `clear_marker()` | Idempotent delete; no error on absent file |

The CLI consumes `is_scheduled` to suppress monitoring tips after
`clu init` and `clu queue add` when the routine is already in place
(see `docs/operations.md` § Background monitoring).
```

### 5. `docs/_outline.md` — confirm structural contract

If the outline enumerates required sections per doc file (e.g. "operations.md must have Setup, Troubleshooting, ..."), and "Background monitoring" doesn't fit cleanly, propose a one-line update. Otherwise leave it.

### 6. Update CLAUDE.md `## Status` section

At the project root, `CLAUDE.md` has a "Status (as of YYYY-MM-DD)" section. Update the date and append a line for clu-monitor:

```markdown
**clu-monitor** — `/clu-monitor` ships as a bundled skill (#19); operator
runs it once per machine to schedule background notifications. CLI tips
in `clu init` / `clu queue add` and optional project CLAUDE.md
injection make Claude propose it proactively in new sessions.
```

Add to the shipped list. Update "as of" date.

### 7. Run the test suite

No code changes, so the suite should be unchanged from phase 2's count. Run it anyway as a sanity check.

### 8. Commit

Title: `clu-monitor: README + operations.md + contract + reference docs`.
Body references `closes #19 phase 3 of 3` and `closes #19` (the final phase closes the issue).

## Verification before `clu complete`

Per CLAUDE.md mandate #9: re-run the suite even though no code changed. Report the count (should match phase 2's final count).

## Acceptance

- [ ] README quickstart mentions `/clu-monitor` as a one-line setup step
- [ ] `docs/operations.md` has a "Background monitoring" section covering setup, status/pause/reset, CLI tips, CLAUDE.md integration
- [ ] `docs/contract.md` documents the marker schema
- [ ] `docs/reference.md` lists `end_of_line.monitor` module functions
- [ ] Project root `CLAUDE.md` status section updated
- [ ] No test regressions
- [ ] One commit referencing `closes #19 phase 3 of 3` and `closes #19`
