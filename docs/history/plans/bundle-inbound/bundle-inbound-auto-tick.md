# bundle-inbound-auto-tick — fire `clu tick` after answered blocker

You are phase `auto-tick` of the `bundle-inbound` plan. Implement
GH issue #2: after the inbound poller routes a reply through
`_cli_dispatch` (which calls `clu answer`), fire a fire-and-forget
`clu tick --dispatch` for the affected plan so the next phase
dispatches immediately instead of waiting up to 60s for the next
cron firing.

## Locked decisions (do NOT re-litigate)

See `plans/bundle-inbound.md`. Summary:

- Opt-out key: `notify.inbound_auto_tick` (bool, default `true`)
- Spawn: `subprocess.Popen(..., stdout=DEVNULL, stderr=DEVNULL,
  start_new_session=True)` — fire-and-forget, never `wait()`
- Auto-tick only on `_cli_dispatch` rc=0
- Auto-tick failure is swallowed; never stalls the poller

## Read first

- GH issue #2 body + the existing triage comment:
  ```
  gh issue view 2 --repo smabe/end-of-line --comments \
      --jq '.comments[].body' --json comments
  ```
- `end_of_line/notify_inbound.py` — the polling loop:
  - `_cli_dispatch` at line 96 — currently runs `clu answer` via
    `subprocess.run`. **Do not modify its signature** — other callers
    pass it as `dispatcher=` for testability.
  - `poll_once` at line 110 — the dispatcher's rc is implicit (raises
    on non-zero). After it returns, that's where the auto-tick spawns.
  - The `dispatcher: Dispatcher = _cli_dispatch` DI seam at line 115 —
    use the same pattern: add a `tick_spawner: Optional[Callable] = None`
    parameter so tests can inject a fake.
- `end_of_line/dispatch.py` — the existing fire-and-forget worker
  spawn pattern at around line 66 (`subprocess.Popen(..., stdout=,
  stderr=, start_new_session=True)`). **Mirror it exactly** — don't
  reinvent the spawn idiom.
- `end_of_line/config.py` — `notify` config block; you need to thread
  the `inbound_auto_tick` flag through (default True if absent).

## Produce

1. **Failing tests first.** Extend `tests/test_notify_inbound.py` with
   four cases:
   - Successful dispatch → tick spawner called with the right project +
     plan + `--dispatch` flag.
   - `notify.inbound_auto_tick: false` in config → spawner NOT called.
   - Dispatcher raises → spawner NOT called (only fire on rc=0).
   - Spawner itself raises → poller continues, doesn't crash. (The
     spawn is wrapped in try/except.)
   Use the existing dispatcher-DI seam style; inject a fake `tick_spawner`
   that records calls into a list.

2. **Implementation.**
   - `end_of_line/notify_inbound.py`: add a `_spawn_tick(project_root,
     plan_slug)` helper that does the `Popen([clu, "tick", "--project",
     project_root, "--plan", plan_slug, "--dispatch"], ...)` call with
     DEVNULL + `start_new_session=True`. Wrap in try/except so a missing
     `clu` binary or OSError doesn't propagate.
   - Add `tick_spawner` parameter to `poll_once` for DI; default to
     `_spawn_tick`.
   - In `poll_once`, after `dispatcher(target, answer)` returns
     successfully (no exception), check the `notify.inbound_auto_tick`
     config flag (default `True`). If on, call `tick_spawner(target.project_root,
     target.plan_slug)`.
   - The `clu` binary path: prefer `sys.executable -m end_of_line.cli`
     style so it works regardless of PATH (the #9 lesson). If you use
     a string `"clu"`, the worker subprocess PATH issues recur.

3. **Config plumbing.** If `notify.inbound_auto_tick` isn't present in
   the operator's `.orchestrator.json`, default to True. Don't error
   on a missing field; it's opt-out, not required.

4. **`/simplify`** if the diff spans >1 file or ~30 lines.

5. **Full suite green:** `python3 -m unittest discover -s tests`.

6. **Commit** (structured format). Stage explicit paths:
   `git add end_of_line/notify_inbound.py end_of_line/config.py tests/test_notify_inbound.py`.

7. **Close GH #2:** PATH-defensive `gh` close from
   `plans/bundle-recovery.md`'s snippet.

## Constraints

- **Fire-and-forget only.** Never `wait()` on the spawned tick.
- **No new top-level subcommand.** This is internal poller behavior.
- **Don't change `_cli_dispatch`'s signature.** Other tests pass it as
  `dispatcher=`.
- **Don't add retries on auto-tick failure.** If `clu tick` fails, the
  60s cron will catch it. Retrying here is just rebuilding what cron
  already does.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan bundle-inbound --phase auto-tick \
    --token <token> --commit <sha>
```

## Escape hatch

`clu block` if:
- The `notify` config schema can't easily carry a new bool without a
  migration. Surface and ask whether to (a) add it without migration
  (defaults work), (b) bump schema version, (c) put it under a
  different config namespace.
- `_cli_dispatch`'s success/failure signal is harder to capture than
  expected (e.g. it currently swallows errors and doesn't raise on
  rc!=0). Surface the actual contract.
