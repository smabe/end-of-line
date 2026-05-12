# triage-issues-issue-2 — assess #2 (inbound poller auto-tick)

You are phase `issue-2` of the `triage-issues` plan. Read one issue,
assess it, post a single comment. Don't implement.

## Read first

- `plans/triage-issues.md` — **Triage format** section. Your comment
  must match that template exactly.
- The issue body:
  ```
  gh issue view 2 --repo smabe/end-of-line
  ```
- Code to ground effort/risk:
  - `end_of_line/notify_inbound.py` — `route_reply`, `_cli_dispatch`, `poll_once`
  - `end_of_line/dispatch.py` — the existing fire-and-forget worker subprocess pattern (to mirror)
  - `.orchestrator.json` — `notify` section; assess where the opt-out config sits

## Produce

A single comment on issue #2, master plan's triage format:

```bash
gh issue comment 2 --repo smabe/end-of-line --body-file /tmp/triage-2.md
```

Sign off `— triaged by clu worker (phase: issue-2, token: <first-8>)`.

## Constraints

- Read-only. No code/test/doc changes, no commits.
- One comment. Resume-safe: check existing comments with
  `gh issue view 2 --comments` and skip if posted.
- Don't pre-rank against the other 7.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan triage-issues --phase issue-2 \
    --token <token>
```

No `--commit` flags.

## Escape hatch

`clu block` only for: unparseable issue body, gh failure, or obsolete
issue. Don't block for second opinions.
