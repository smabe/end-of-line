# end-of-line / clu

Personal plan orchestrator for the `/plan` skill. Cron-driven supervisor, file-state, cold-context phase workers. Tron-themed (binary is `clu`; the program IS End of Line).

## Stack
- Python 3.11+ (uses `fromisoformat` Z-suffix support, `IntEnum`, dataclasses with `kw_only`)
- Zero runtime deps — stdlib only
- `unittest` for tests (NOT pytest)
- Installed via `pip install -e .` → `clu` on PATH

## Run + test
```bash
python3 -m unittest discover -s tests       # full suite (~1s, 48 tests)
python3 -m end_of_line.cli --help           # CLI
```

## Read these before changing anything
- `brainstorm/clu-master.md` — load-bearing. Synthesizes 4 expert reviews into the critical-fix list. **Day 1 shipped (security + correctness); Day 2 is the UX surface.**
- `docs/contract.md` — state schema + worker contract + cron snippet
- `brainstorm/clu-{dist-sys,red-team,notifications,end-user}.md` — the four full reviews if you need to re-justify a decision

## Architecture in one screen
- **Supervisor** (`supervisor.py`): one-tick decision logic. 8-priority chain. No long-running process — cron fires `clu tick` every 5 min.
- **State** (`state.py`): atomic JSON file under `<project>/plans/.orchestrator/<slug>.state.json`. Mutations via `with st.mutate(path) as data:` (locks + load + save_atomic). Event log is append-only.
- **Dispatch** (`dispatch.py`): fire-and-forget shell Popen with fast-fail (`proc.wait(timeout=0.5)`). Worker stderr → per-token log under `.orchestrator/logs/`. PID stamped on the live claim.
- **CLI** (`cli.py`): `init / tick / status / answer / spawn / complete / block / task-done`. Worker-side commands (`complete / block / spawn / task-done`) require `--token` matching the live claim — this is load-bearing for security.
- **Plan parser** (`plan_parser.py`): reads master plan's `## Sessions index` table.
- **Config** (`config.py`): `.orchestrator.json` per project.

## Conventions (mandatory)

### TDD for any logic change
Write failing tests first. AAA pattern. Factory helpers (see `tests/test_worker_callbacks.py` for the `setUp` template — git init + claim_phase).

### `/simplify` after non-trivial work
Hook the simplify skill before committing anything bigger than a typo. The Day-1 simplify pass collapsed 9 error sites into `_die()` + `ExitCode` and cut test runtime in half. Pays its own rent.

### `/commit` style
Structured message:
```
Title under 70 chars

Why
<1-3 sentences on motivation, not changelog>

What's new
- <user-visible behavior>

Under the hood
- <implementation notes>

Tests
<count / what's covered>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Exit codes
Use `ExitCode` IntEnum, never bare ints:
```python
return _die(ExitCode.CLAIM_MISMATCH, str(exc))
```

### Worker callback contract (load-bearing)
Every worker-side CLI command (`complete`, `block`, `spawn`, `task-done`, `heartbeat`) requires `--token` matching the live claim. Don't add a worker command that skips this — it breaks the whole security model. New worker commands should use the `@_translate_claim_mismatch` decorator and let `ClaimMismatch` propagate. See `state.assert_claim_match` and `state.release_claim`.

### Slugs
`args.plan` and any parsed `phase_id` MUST go through `st.validate_slug()` before they touch a filesystem path. Regex: `^[a-z0-9][a-z0-9_-]{0,63}$`. Don't add a code path that bypasses.

### Event types are constants
Never write raw event-type strings. Use `state.EVENT_*`. A typo silently breaks `completed_phase_ids()` projection.

### Test isolation for the host registry
Any test that calls `main(["init", ...])` (or otherwise touches `registry.register`) MUST call `tests.isolate_registry(self, tmp_path)` in `setUp`. Without it, tests pollute the user's real `~/.config/clu/registry.json`. The helper points `XDG_CONFIG_HOME` at a per-test tmp dir and auto-restores via `addCleanup`. See `tests/__init__.py`.

## What NOT to do
- No SwiftUI / iOS code — this repo is pure Python. No `/review` needed (that's HealthData's mandatory SwiftUI gate, doesn't apply here).
- Don't `git add -A` — stage explicit paths.
- Don't introduce third-party deps without a real justification. Stdlib has everything we need.
- Don't break the "one tick = one action" contract in `supervisor.tick`. If a tick needs to do two things, that's two ticks.

## Status (as of 2026-05-11)

**Day 1 shipped** (commits `1f2da6c` → `fad80e9`):
- Token validation + SHA quality gate on all worker callbacks
- Path-traversal guards, lockfile O_NOFOLLOW, schema-version check
- Spawned-task completion path + per-phase spawn cap (default 10)
- Dispatch fast-fail with per-token logs + pid stamping
- ExitCode enum + `_die` helper

**Day 2 in progress** (108 tests, all green, ~1s suite):
- 2.1 (shipped `ef01756`): Worker heartbeat → `stalled` status (Cliff 1).
- 2.2 (shipped `738fcb8`): iMessage outbound via osascript + quiet hours 22-08 default. Wired to blocker/stalled/plan_completed events.
- 2.3 (shipped `95f9f7c`): Host-level registry at `~/.config/clu/registry.json`; `clu init` auto-registers; `clu register/unregister/list` commands; notifications now slug-prefixed (`❓ <plan>/q-1`) for multi-plan disambiguation.
- 2.4 (in progress): iMessage inbound poller in `end_of_line/notify_inbound.py` + LaunchAgent template at `examples/clu.inbound.plist`. Polls `chat.db` read-only, routes via `^\s*(<plan-slug>\s+)?[0-9]\s*$`; bare digit only honored when exactly one plan has an open blocker (multi-plan: require slug prefix — last-pinged routing deferred). Seen-rowid persisted at `~/.clu/seen_msg_rowid`; rowid advances even on no-match to prevent stuck-cursor resurrection.

**Pick up here:** `clu` no-args fleet view (Cliff 3). Walk `registry.entries()`, render one line per plan: slug, status, current phase, open-blocker count, age of last event. Bare `clu` invokes this; existing `clu list` is the dumb name-only listing. Code: `cli.py` `cmd_list` enrichment + maybe a small `summarize_plan` helper. Tests: empty registry, single plan running, plan with open blocker, plan halted.

**Day 2 backlog after that:**
1. SLA-pauses-during-quiet (stale-blocker escalations should respect quiet hours)
2. `clu retry` / `clu pause` / `clu resume`
3. Halt-reason as first-class field in `clu status`
4. Halt notification (user explicitly de-selected this from Day 2.2 — re-confirm before adding)

**Locked config decisions** (from the brainstorm — don't re-litigate):
- Notifications: iMessage to **self-chat** handle, NO Pushover (user picked iMessage-only)
- Quiet hours: 22:00–08:00 local
- Worker sandbox: document-only for v0.1; user is responsible for what the worker LLM does

## Sister project
- `/Users/smabe/projects/HealthData` — the iOS app this orchestrator was built to drive. Run `clu init --project ~/projects/HealthData --plan watch-start-workout` once cron is wired (watch-start-workout has 3 phases per `## Sessions index`).
