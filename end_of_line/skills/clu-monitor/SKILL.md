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

The marker file at `~/.config/clu/monitor.json` is the single source
of truth for "is monitoring already scheduled." Read it first; write
it only after `/schedule` confirms the routine was created.

## Workflow

### 1. Check if monitoring is already scheduled

Run:

```bash
test -f ~/.config/clu/monitor.json && cat ~/.config/clu/monitor.json
```

If the file exists and contains valid JSON with `schedule_id` and
`scheduled_at`, monitoring is already set up. Print:

> Monitoring already scheduled at `<scheduled_at>` (id: `<schedule_id>`,
> cadence: `<cadence>`). To reset, delete `~/.config/clu/monitor.json`
> and re-run `/clu-monitor`.

Exit. Do NOT create a duplicate schedule.

### 2. Compose the canonical monitoring prompt

The routine that `/schedule` runs each tick should execute this prompt
**verbatim** — the routine has no shared context with this session, so
the prompt must stand alone:

> Check clu state by running `clu list` and `clu queue list`. Send the
> user an iMessage if: (a) any plan has status HALTED or
> HALTED_REPLAN — include the slug + halt reason from the most recent
> event; (b) any plan has an open blocker (no `consumed: true`) for
> more than 30 minutes — include the question + option list; (c) any
> plan has a stalled claim (`lease_expires` past current time with
> status RUNNING). Otherwise: stay silent. Do NOT send "all clear" or
> heartbeat messages.

### 3. Invoke `/schedule` to create the routine

Default cadence: `*/15 8-21 * * *` (every 15 minutes during
08:00-22:00 local). Matches clu's existing quiet_hours convention so
the monitor stays silent overnight.

Use the Skill tool to invoke `/schedule create` with the canonical
prompt from step 2 and the cadence above. The routine is a remote
agent and inherits no context — the prompt itself is the whole brief.

### 4. Record the marker

On successful schedule creation, capture the `schedule_id` from
`/schedule`'s response and write the marker:

```bash
python3 -c "from end_of_line import monitor; monitor.record_scheduled('<schedule_id>', '*/15 8-21 * * *')"
```

If `/schedule create` fails (auth, quota, missing skill), do NOT write
the marker — leave it absent so the next `/clu-monitor` invocation
retries cleanly. Report the failure to the user with the next steps
to diagnose.

### 5. Confirm to the user

Print a one-screen summary:

> Background monitoring scheduled. clu will iMessage you on halts,
> stuck blockers, and stalled claims (silent otherwise). Status file:
> `~/.config/clu/monitor.json`. To pause: `/schedule pause
> <schedule_id>`. To remove: delete the status file AND the
> `/schedule` routine.

## Failure modes

- **`/schedule` skill not available.** Some Claude Code installs may
  not have the schedule skill present. Detect by trying to invoke and
  catching the missing-skill error. Tell the user: "The `/schedule`
  skill is required but not available in this Claude Code install.
  See https://docs.claude.com/claude-code for setup."
- **User declines to authorize the schedule.** `/schedule` will prompt
  before creating the routine (it costs money on their account). If
  the user declines, do NOT write the marker. Exit cleanly with
  "Monitoring not scheduled (declined). Re-run /clu-monitor whenever
  you're ready."
- **Marker write fails.** Disk full / permissions issue. The schedule
  exists but the marker doesn't — next `/clu-monitor` invocation would
  create a duplicate. Tell the user explicitly: "Schedule created but
  marker file write failed at `<path>`. To prevent duplicates,
  manually create the file with `python3 -c \"from end_of_line import
  monitor; monitor.record_scheduled('<id>', '<cadence>')\"`."
- **Stale marker after manual `/schedule delete`.** The marker would
  lie about being scheduled. v1 trusts the marker; re-invocation
  prints "already scheduled at <ts>" even if the routine no longer
  exists. Operators can manually reset with `rm
  ~/.config/clu/monitor.json` (documented in the step-1 message).
