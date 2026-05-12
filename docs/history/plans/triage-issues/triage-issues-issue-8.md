# triage-issues-issue-8 — assess #8 (`clu release-claim` escape hatch)

You are phase `issue-8` of the `triage-issues` plan. Read one issue,
assess it, post a single comment. Don't implement.

**Note:** This is paired with #7 — the issue body explicitly says so.
#7 (auto-detect systemic failures) and #8 (manual escape hatch) are
defense-in-depth on the same operator-pain class. Reflect that in
**Dependencies**.

## Read first

- `plans/triage-issues.md` — **Triage format** section.
- The issue body:
  ```
  gh issue view 8 --repo smabe/end-of-line
  ```
- Code to ground effort/risk:
  - `end_of_line/state.py` — `current_claim` shape, `lease_expires`,
    `EVENT_*` constants (a new event type is needed)
  - `end_of_line/cli.py` — `pause` / `resume` / `retry` subcommands;
    `release-claim` would sit next to these as an operator command
  - `end_of_line/supervisor.py` — `lease_expired` auto-clearing path
    (so the new command mimics that, but operator-triggered)

## Produce

A single comment on issue #8, master plan's triage format. In your
**Dependencies**, call out the #7/#8 pairing explicitly — even if #7
lands first, this is still the manual fallback for everything #7
doesn't pattern-match.

```bash
gh issue comment 8 --repo smabe/end-of-line --body-file /tmp/triage-8.md
```

Sign off `— triaged by clu worker (phase: issue-8, token: <first-8>)`.

## Constraints

- Read-only.
- One comment. Resume-safe.
- Don't pre-rank against the other 7.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan triage-issues --phase issue-8 \
    --token <token>
```

No `--commit` flags.

## Escape hatch

`clu block` only for unparseable issue, gh failure, or obsolete issue.
