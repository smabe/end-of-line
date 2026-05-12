# triage-issues-issue-1 — assess #1 (`clu logs <plan>`)

You are phase `issue-1` of the `triage-issues` plan. Read one issue,
assess it, post a single comment. Don't implement.

## Read first

- `plans/triage-issues.md` — especially the **Triage format** section.
  Your comment must match that template exactly.
- The issue body:
  ```
  gh issue view 1 --repo smabe/end-of-line
  ```
- Code to ground effort/risk:
  - `end_of_line/cli.py` — existing subcommand patterns (esp. `status`, `pause`)
  - `end_of_line/state.py` — `current_claim` field shape; check if `log_path` is in there
  - `end_of_line/dispatch.py` — where `log_path` is computed and written

## Produce

A single comment on issue #1 in the master plan's triage format. Post
with:

```bash
gh issue comment 1 --repo smabe/end-of-line --body-file /tmp/triage-1.md
```

Sign off with `— triaged by clu worker (phase: issue-1, token: <first-8-chars-of-token>)`.

## Constraints

- Read-only. No code/test/doc changes, no repo commits.
- One comment. On resume, run `gh issue view 1 --comments --repo smabe/end-of-line`
  first and skip if you already posted.
- Scope is this issue only. Don't pre-rank against the other 7 — the
  synthesis phase does that.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan triage-issues --phase issue-1 \
    --token <token>
```

No `--commit` flags — this is a comment-only phase. clu accepts
complete-without-commits.

## Escape hatch

`clu block` only if: the issue body is materially unclear, `gh comment`
fails non-transiently, or the issue is already obsolete. Don't block for
"I want a second opinion on priority" — pick one, post, move on.
