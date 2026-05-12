# triage-issues-issue-6 — assess #6 (`clu prior-blocker` helper)

You are phase `issue-6` of the `triage-issues` plan. Read one issue,
assess it, post a single comment. Don't implement.

## Read first

- `plans/triage-issues.md` — **Triage format** section.
- The issue body:
  ```
  gh issue view 6 --repo smabe/end-of-line
  ```
- Code to ground effort/risk:
  - `examples/clu-phase-skill.md` — current inline Python in the worker
    skill for prior-blocker detection
  - `examples/fake-worker.sh` — also uses inline Python for the same
  - `end_of_line/state.py` — `blockers` schema (so the helper knows
    what it's reading)
  - `end_of_line/cli.py` — existing subcommand patterns for shape

## Produce

A single comment on issue #6, master plan's triage format:

```bash
gh issue comment 6 --repo smabe/end-of-line --body-file /tmp/triage-6.md
```

Sign off `— triaged by clu worker (phase: issue-6, token: <first-8>)`.

## Constraints

- Read-only.
- One comment. Resume-safe.
- Don't pre-rank against the other 7.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan triage-issues --phase issue-6 \
    --token <token>
```

No `--commit` flags.

## Escape hatch

`clu block` only for unparseable issue, gh failure, or obsolete issue.
