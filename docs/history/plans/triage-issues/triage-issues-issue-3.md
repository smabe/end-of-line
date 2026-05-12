# triage-issues-issue-3 — assess #3 (multi-plan inbound routing)

You are phase `issue-3` of the `triage-issues` plan. Read one issue,
assess it, post a single comment. Don't implement.

## Read first

- `plans/triage-issues.md` — **Triage format** section.
- The issue body:
  ```
  gh issue view 3 --repo smabe/end-of-line
  ```
- Code to ground effort/risk:
  - `end_of_line/notify_inbound.py` — `route_reply` and how it currently
    handles ambiguous bare-digit replies
  - `end_of_line/state.py` — `EVENT_PHASE_BLOCKED` and what's stamped
    on blocker events; consider where `last_notified_at` could live
  - `end_of_line/registry.py` — per-plan timestamp could live in
    registry vs state; assess

## Produce

A single comment on issue #3, master plan's triage format:

```bash
gh issue comment 3 --repo smabe/end-of-line --body-file /tmp/triage-3.md
```

Sign off `— triaged by clu worker (phase: issue-3, token: <first-8>)`.

## Constraints

- Read-only.
- One comment. Resume-safe (`gh issue view 3 --comments`).
- Don't pre-rank against the other 7.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan triage-issues --phase issue-3 \
    --token <token>
```

No `--commit` flags.

## Escape hatch

`clu block` only for unparseable issue, gh failure, or obsolete issue.
