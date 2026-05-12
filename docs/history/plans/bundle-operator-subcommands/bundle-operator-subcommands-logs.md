# bundle-operator-subcommands-logs — ship `clu logs <plan>`

You are phase `logs` of the `bundle-operator-subcommands` plan.
Implement GH issue #1: `clu logs --project P --plan S` prints the
active worker's log to stdout, falls back to the newest file in the
logs dir when no claim is active, supports `--follow` for streaming.

This is the largest of the three bundle phases (~45m). Take the
acceptance criteria one at a time; don't bundle multiple test cases
into a single test method.

## Read first

- GH issue #1 for full acceptance criteria:
  ```
  gh issue view 1 --repo smabe/end-of-line
  ```
- The triage comment on #1 (already posted):
  ```
  gh issue view 1 --repo smabe/end-of-line --comments \
      --jq '.comments[].body' --json comments
  ```
- `end_of_line/state.py` — `current_claim` shape; verify `log_path` is
  present on the claim (the triage said dispatch.py:127 stamps it).
- `end_of_line/dispatch.py` — where `log_path` is computed (around
  line 63). Match the path convention exactly so `clu logs` always
  finds the file.
- `end_of_line/cli.py` — `status` (cli.py:105) is a clean reference
  for read-only `--project/--plan` subcommands.

## Produce

1. **Failing tests first.** New file `tests/test_logs.py`. Cover at
   minimum:
   - Active claim with `log_path` → file contents on stdout.
   - No active claim → newest file in logs dir on stdout (use
     mtime-based comparison; create two files and stamp them).
   - Logs dir empty or missing → exit non-zero with a clean stderr.
   - `--follow` flag — at least a smoke test that the command exits
     cleanly when the file isn't growing (subprocess with a short
     timeout, or refactor `_follow_loop` into a testable function
     that takes a `stop_after_seconds` knob for the test).
   Use `isolate_registry(self, tmp_path)` in `setUp`.
2. **Implementation.** Add `logs` subparser to `end_of_line/cli.py`.
   Handler:
   - Load state; if `current_claim` exists and has a `log_path`, dump
     that file. If `--follow`, switch to streaming mode.
   - Else, glob the logs dir and pick the newest by `st_mtime`.
   - Empty/missing dir → `_die(ExitCode.UNKNOWN_TASK, "no logs found
     for plan <slug>")` or whichever ExitCode is the right semantic.
3. **`--follow` happy path only.** The triage said punt rotation
   semantics — if the file is rotated/truncated mid-follow, the
   command may stop streaming. Document this in a one-line code
   comment if you must; otherwise just leave it. **Don't** invent a
   watchdog reopen loop; that's a follow-up issue.
4. **Run `/simplify`** (diff likely spans cli.py + new test file; if
   the handler grows past ~40 lines, factor out a `_resolve_log_path`
   helper).
5. **Full suite green:** `python3 -m unittest discover -s tests`.
6. **Commit** (structured format). Stage explicit paths:
   `git add end_of_line/cli.py tests/test_logs.py`.
7. **Close GH #1:**
   ```bash
   gh issue close 1 --repo smabe/end-of-line --reason completed \
       --comment "Shipped in $(git rev-parse --short HEAD)."
   ```

## Constraints

- `--follow` happy path only. No file-rotation handling, no
  watchdog reopen — punt to a follow-up.
- Don't write to the logs dir from `clu logs`. Read-only.
- The newest-file fallback must NOT be the default when a claim is
  active — claim's `log_path` always wins. Test this branch
  explicitly.
- If `current_claim` doesn't carry `log_path` (e.g. the field was
  added later and old state files predate it), fall through to the
  newest-file path rather than crashing. A `.get("log_path")` defense
  is enough; don't migrate old state.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan bundle-operator-subcommands --phase logs \
    --token <token> --commit <sha>
```

## Escape hatch

`clu block` if:
- `--follow` testing is genuinely intractable in the unittest harness
  (no clean way to assert streaming without flake) — block with
  options "ship without --follow test", "ship without --follow at
  all", "design a test harness". Don't silently skip the test.
- `current_claim.log_path` isn't actually being stamped — claim with
  no log path is the common case, not the fallback. Surface and ask.
