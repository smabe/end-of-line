# clu-inbox-docs — operations + contract + README + smoke-test step

You are phase `docs` of the `clu-inbox` plan. Third of three. Closes [#20](https://github.com/smabe/end-of-line/issues/20).

Phase 1 shipped the inbox + hook + skill rewrite. Phase 2 added the two missing notification kinds. This phase rewrites the docs we shipped in #19 (which described the broken /schedule mechanism) and adds a manual smoke step.

## Locked decisions (do NOT re-litigate)

- **Rewrite, don't append.** Most of #19's `docs/operations.md` § Background monitoring describes the broken /schedule path. Delete those paragraphs and replace.
- **Contract.md gets two new schemas**: inbox event JSON and monitor.json v2.
- **README change is small**: the existing line about `/clu-monitor` still applies (mechanism changed but the user-facing setup didn't). Just update any mention of `/schedule` or cron to "UserPromptSubmit hook."
- **Smoke step**: a documented manual procedure for verifying the install end-to-end. Operator can run it once per machine after install to confirm everything's wired.
- **No code changes in this phase.** Pure documentation + the project CLAUDE.md status section.

## Read first

- Phase 1 + 2 outputs: `end_of_line/inbox.py`, `end_of_line/hooks/clu_inbox_surface.py`, the rewritten `end_of_line/skills/clu-monitor/SKILL.md`, and the new notification kinds in `end_of_line/notify.py`.
- `docs/operations.md` § Background monitoring — the section to rewrite. Find via `grep -n "## Background monitoring" docs/operations.md` (shipped in #19's docs phase, commit `59650b6`).
- `docs/contract.md` — find the existing marker-file schema entry (also shipped in #19). Replace with v2 + add inbox event schema.
- `docs/reference.md` — `end_of_line.monitor` entry (shipped in #19); add `end_of_line.inbox` entry.
- `README.md` — quickstart section mentioning `/clu-monitor`. Re-skim and adjust any cron / `/schedule` references.
- `CLAUDE.md` at project root — Status section, last updated for #19. Refresh with the #20 ship.

## Produce

### 1. Rewrite `docs/operations.md` § Background monitoring

Delete the existing section that describes `/schedule`. Replace with:

```markdown
## Background monitoring

clu sends iMessages to the operator on halts, blockers, plan completions, and queue
events (when `notify.imessage_to` is configured). The notifications work without
extra setup — the LaunchAgent that ticks the supervisor every minute also handles
the iMessage send.

The gap is in-session signaling: if you're in an active Claude Code session and
walk back to it AFTER clu has changed state, Claude has no idea what happened
unless you summarize for it. **The `/clu-monitor` skill closes that gap** by
installing a UserPromptSubmit hook that surfaces clu's events into Claude's
context automatically on your next message.

### How it works

1. clu writes each notification event as a JSON file to `~/.config/clu/inbox/`
   in addition to sending the iMessage.
2. The bundled `clu_inbox_surface.py` hook script reads that directory on every
   user message in Claude Code.
3. Events tagged with the current project_root get surfaced as a system reminder
   visible to Claude in the same turn as your message.
4. Surfaced events are moved to `inbox/processed/` so you never see the same
   event twice.

Result: you walk back to Claude after a notification, type literally anything
("ok", "next", "/post-ship"), and Claude reacts with full context.

### Setup

```bash
$ clu install-skill --force      # one-time, installs /clu-phase /plan /brainstorm /clu-monitor
$ # then, in a Claude Code session:
$ /clu-monitor
Installed UserPromptSubmit hook → /Users/you/.../end_of_line/hooks/clu_inbox_surface.py
Settings updated: /Users/you/.claude/settings.json
```

The marker at `~/.config/clu/monitor.json` records the install so re-running
`/clu-monitor` is idempotent.

### Status, reset, uninstall

```bash
# Check installed
$ cat ~/.config/clu/monitor.json
{
  "schema_version": 2,
  "hook_installed_at": "2026-05-12T...",
  "hook_path": "/Users/.../end_of_line/hooks/clu_inbox_surface.py",
  "settings_json_path": "/Users/you/.claude/settings.json"
}

# Inspect pending events (debug)
$ ls ~/.config/clu/inbox/

# Full uninstall
$ clu uninstall-hook            # removes hook entry from settings.json
$ rm ~/.config/clu/monitor.json # forget the install
$ rm -rf ~/.config/clu/inbox    # discard pending events (optional)
```

### What gets surfaced

Every event clu would have sent an iMessage for, plus two escalation kinds added
in this release:

- `halted` — plan transitioned to HALTED or HALTED_REPLAN
- `blocked` — worker created a blocker (first ping)
- `plan_completed` — plan finished cleanly
- `queue_*` — queue lifecycle (skipped, corrupt, repaired, repair_failed)
- `stuck_blocker` — blocker open >30min and not consumed; re-pings every 30min
- `stalled_claim` — claim's lease expired with plan status still RUNNING

iMessages and inbox writes both happen for each event. Quiet hours
(`notify.quiet_hours` in `.orchestrator.json`) suppress iMessages but NOT inbox
writes — Claude needs the context even when you're asleep.

### Smoke test (run once after install)

After `clu install-skill --force` and `/clu-monitor`, verify the chain works:

```bash
# 1. Create a quick test event in the inbox.
$ python3 -c "from end_of_line import inbox; inbox.write_event(
    type='smoke', plan_slug='smoke-test', project_root='$(pwd)',
    summary='smoke test event', details={'test': True})"

# 2. Open Claude Code in this directory, type anything (e.g. 'hi').
# 3. Claude should respond with awareness of the smoke-test event.
# 4. Verify the event moved:
$ ls ~/.config/clu/inbox/processed/  # should contain the smoke event
```

If Claude didn't see the event, check:
- `cat ~/.claude/settings.json | jq '.hooks.UserPromptSubmit'` — entry present?
- `cat ~/.config/clu/inbox_hook.log` — hook errors logged here

### CLI tips

`clu init` and `clu queue add` print a one-line tip recommending `/clu-monitor`
when the marker is absent. The tip is suppressed when:

- Monitoring is already installed (marker file v2 present), OR
- Output is not a TTY (suppresses noise in worker subprocesses)

### Project CLAUDE.md integration

On the first `clu init` in a project, clu offers to append a `## clu` section
to the project's CLAUDE.md (mechanism shipped in #19, unchanged in #20). The
section now mentions the inbox/hook setup rather than the broken /schedule.
```

### 2. Rewrite `docs/contract.md` schemas section

Find the marker.json entry (v1) and replace with v2:

```markdown
### `~/.config/clu/monitor.json`

Marks that `/clu-monitor` has installed the UserPromptSubmit hook for
in-session signaling. Account-wide, not per-project. Absent file = monitoring
not set up; clu CLI commands emit a one-line tip recommending `/clu-monitor`.

\`\`\`json
{
  "schema_version": 2,
  "hook_installed_at": "ISO UTC",
  "hook_path": "/abs/path/to/clu_inbox_surface.py",
  "settings_json_path": "/Users/you/.claude/settings.json"
}
\`\`\`

| Field | Meaning |
|---|---|
| `hook_installed_at` | ISO UTC ts of install |
| `hook_path` | Absolute path to the bundled hook script (resolved at install time) |
| `settings_json_path` | Absolute path to the settings.json modified |

v1 markers (pre-#20, contained `schedule_id`) are treated as "needs reinstall" —
`is_scheduled()` returns False so `/clu-monitor` re-runs cleanly. No data migrated;
the v1 schedule_id was never used after the routine creation.
```

Add a new entry for the inbox:

```markdown
### `~/.config/clu/inbox/<timestamp>-<kind>-<short_id>.json`

One file per clu notification event. Surfaced by the UserPromptSubmit hook into
the active Claude Code session, then moved to `~/.config/clu/inbox/processed/`.

\`\`\`json
{
  "id": "evt-<8hex>",
  "schema_version": 1,
  "type": "halted | blocked | plan_completed | queue_skipped | ...",
  "plan_slug": "...",
  "project_root": "/abs/path",
  "timestamp": "ISO UTC",
  "summary": "one-line human summary (≤200 chars)",
  "details": { "...kind-specific...": "..." }
}
\`\`\`

Mark-and-sweep dedup: the hook moves a surfaced event into `processed/` after
writing it to Claude's context. To reset (e.g. clear noise from a debugging
session), delete `~/.config/clu/inbox/` and `~/.config/clu/inbox/processed/`.

Filename pattern is sort-friendly (events read in timestamp order) and
collision-free under concurrent writes (8-char random suffix).
```

### 3. Update `docs/reference.md`

Find the `end_of_line.monitor` entry. Update its function list — `record_scheduled` is gone, replaced by `record_hook_installed`:

```markdown
| `record_hook_installed(hook_path, settings_json_path)` | Atomic v2 marker write |
```

Add a parallel entry for `end_of_line.inbox`:

```markdown
### `end_of_line.inbox`

Per-event JSON inbox surfaced to active Claude Code sessions via the
UserPromptSubmit hook.

| Function | Purpose |
|---|---|
| `inbox_root() -> Path` | XDG-respecting `~/.config/clu/inbox/` |
| `write_event(*, type, plan_slug, project_root, summary, details=None) -> str` | Atomic write; returns event_id |
| `read_unprocessed() -> list[dict]` | All events in inbox/ (NOT inbox/processed/) |
| `mark_processed(event_id)` | Move to `processed/` subdir; idempotent on missing |
| `list_for_project(project_root) -> list[dict]` | `read_unprocessed` filtered by project_root |

The hook script at `end_of_line/hooks/clu_inbox_surface.py` is the canonical
consumer — installed via `clu install-hook` into the user's `~/.claude/settings.json`
as a `UserPromptSubmit` hook entry.
```

### 4. Update README.md

Find the section mentioning `/clu-monitor`. Replace any "cron-driven monitor" / "/schedule routine" / "background polling" language with:

```markdown
After installing the skills, run `/clu-monitor` once in Claude Code to install
a UserPromptSubmit hook that surfaces clu events into Claude's context on your
next message. Idempotent — re-running prints the current install status. State
file: `~/.config/clu/monitor.json`.
```

If the README has a count of "3 bundled skills" anywhere, the count is now 4
(updated in #19 already, but double-check).

### 5. Refresh project CLAUDE.md status section

Find the "Status (as of YYYY-MM-DD)" section. Update the date and append:

```markdown
**clu-inbox** — `/clu-monitor` now installs a UserPromptSubmit hook (commit
`<phase-1-sha>`) that surfaces clu events into the active Claude Code session
on every user message. Replaces the broken /schedule mechanism from #19. New
notification kinds `stuck_blocker` (30min re-ping until consumed) and
`stalled_claim` (one-shot on lease expiry with status RUNNING) added in the
same chain. Tests <prior>→<new>.
```

Add `clu-inbox` to the shipped list. The two ship commit SHAs come from phases 1 + 2.

### 6. Run the test suite

No code changes, so suite count should match end of phase 2. Run anyway as
sanity check.

### 7. Commit

Title: `clu-inbox: operations + contract + reference + README docs`.
Body references `closes #20 phase 3 of 3` AND `closes #20`.

## Verification before `clu complete`

Per CLAUDE.md mandate #9: re-run suite even though no code changed. Confirm
count matches phase 2's final.

## Acceptance

- [ ] `docs/operations.md` § Background monitoring rewritten — no `/schedule` references
- [ ] `docs/contract.md` has inbox event schema + monitor.json v2 schema
- [ ] `docs/reference.md` lists `end_of_line.inbox` module
- [ ] README mentions UserPromptSubmit hook, not cron
- [ ] Project CLAUDE.md status section updated
- [ ] Smoke-test procedure documented in operations.md
- [ ] No test regressions
- [ ] One commit referencing `closes #20 phase 3 of 3` and `closes #20`
