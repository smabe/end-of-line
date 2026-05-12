# triage-issues-issue-5 — assess #5 (`clu tick-all` subcommand)

You are phase `issue-5` of the `triage-issues` plan. Read one issue,
assess it, post a single comment. Don't implement.

## Read first

- `plans/triage-issues.md` — **Triage format** section.
- The issue body:
  ```
  gh issue view 5 --repo smabe/end-of-line
  ```
- Code to ground effort/risk:
  - `examples/clu-tick-all.sh` — the current shell-parses-`clu list` thing
  - `examples/clu.tick.plist` — LaunchAgent that calls the shell script
  - `end_of_line/cli.py` — existing subcommand patterns to follow
  - `end_of_line/registry.py` — `entries()` is what `tick-all` would iterate

## Produce

A single comment on issue #5, master plan's triage format:

```bash
gh issue comment 5 --repo smabe/end-of-line --body-file /tmp/triage-5.md
```

Sign off `— triaged by clu worker (phase: issue-5, token: <first-8>)`.

## Constraints

- Read-only.
- One comment. Resume-safe.
- Don't pre-rank against the other 7.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan triage-issues --phase issue-5 \
    --token <token>
```

No `--commit` flags.

## Escape hatch

`clu block` only for unparseable issue, gh failure, or obsolete issue.
