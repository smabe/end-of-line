# bundle-operator-subcommands-tick-all — ship `clu tick-all`

You are phase `tick-all` of the `bundle-operator-subcommands` plan.
Implement GH issue #5: promote `examples/clu-tick-all.sh` to a
first-class `clu tick-all` subcommand, rewire the LaunchAgent plist,
delete (or stub) the shell script.

## Read first

- GH issue #5 for the full acceptance criteria:
  ```
  gh issue view 5 --repo smabe/end-of-line
  ```
- The triage comment on #5 (already posted):
  ```
  gh issue view 5 --repo smabe/end-of-line --comments \
      --jq '.comments[].body' --json comments
  ```
- `examples/clu-tick-all.sh` — the current shell parser of `clu list`.
- `examples/clu.tick.plist` — LaunchAgent that invokes the shell script;
  rewire it to call `clu tick-all` directly.
- `end_of_line/cli.py` — `tick` subcommand wiring (cli.py:82); the new
  `tick-all` lives next to it.
- `end_of_line/registry.py` — `entries()` is what `tick-all` iterates
  (not `clu list`'s stdout).
- `end_of_line/supervisor.py` — what `tick --dispatch` actually does;
  `tick-all` calls into the same path per plan.

## Produce

1. **Failing test first.** New file `tests/test_tick_all.py` (or
   extend `test_lifecycle.py`). Cover three branches:
   - Multi-plan registry → tick fires for each in turn.
   - One plan errors → others still tick, overall exit 0, error logged
     to stderr.
   - Empty registry → exit 0, no-op (don't error).
   Use `isolate_registry(self, tmp_path)` in `setUp`.
2. **Minimal implementation.** Add a `tick-all` subparser to
   `end_of_line/cli.py`. Handler iterates `registry.entries()` and
   runs the per-plan tick (with `--dispatch` semantics, matching what
   the shell script did). Per-plan exceptions are caught + logged;
   the loop continues.
3. **Rewire the LaunchAgent.** Update `examples/clu.tick.plist` to
   invoke `clu tick-all` directly (no shell wrapper). Replace
   `examples/clu-tick-all.sh` contents with a one-line `exec clu
   tick-all "$@"` (back-compat shim — the live LaunchAgent on this
   Mac may still reference the script path; one-line shim is safer
   than deletion).
4. **Don't touch the loaded LaunchAgent.** The `.plist` in `examples/`
   is the template. The user has a copy at `~/Library/LaunchAgents/`;
   that's the operator's responsibility to re-install. Mention it in
   the commit message under "Under the hood".
5. **Run `/simplify`** (diff likely spans cli.py + tests + 2 examples).
6. **Full suite green:** `python3 -m unittest discover -s tests`.
7. **Commit** (structured format). Stage explicit paths:
   `git add end_of_line/cli.py tests/test_tick_all.py examples/clu-tick-all.sh examples/clu.tick.plist`.
8. **Close GH #5:**
   ```bash
   gh issue close 5 --repo smabe/end-of-line --reason completed \
       --comment "Shipped in $(git rev-parse --short HEAD)."
   ```

## Constraints

- Don't add or remove any actual LaunchAgents (no `launchctl load`,
  no plist install). The operator does the re-install pass after
  reviewing the commit.
- Don't change `clu tick`'s signature. `tick-all` is a separate
  command that calls into the same logic per plan; it doesn't
  replace `tick`.
- Per-plan errors must not abort the loop — a single bad plan must
  not poison the cron cadence. The test for this is mandatory.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan bundle-operator-subcommands --phase tick-all \
    --token <token> --commit <sha>
```

## Escape hatch

`clu block` if:
- The `prior-blocker` phase commit (immediately prior) is broken in a
  way that affects `tick-all`'s test path. Surface the conflict.
- `registry.entries()` doesn't exist or has a different name. Verify
  with grep before blocking — naming may have drifted.
