# clu — Distributed Systems / SRE Brutal Review

## Verdict
Solid weekend scaffold for a single-Mac toy; ships with two real footguns (status drift + event-log-inside-state-file) and a near-zero observability story that will turn the first incident into archaeology.

## Top 3 issues that would actually bite

1. **Event log lives INSIDE the state JSON** (`state.py:142`). A torn write doesn't just lose the latest mutation — it loses the entire derivation source. The "events are source of truth" claim is undermined by the storage layout.
2. **`data["status"]` is a parallel source of truth** (`supervisor.py:62, 75, 106, 126`). It's mutated imperatively next to `append_event`, never re-derived. Skip an event, miss a flip, get a paused plan with no SLA event, or a halted plan with no max-attempts event. The two will drift; tests don't cover it.
3. **Dispatch is fire-and-forget into `/dev/null`** (`dispatch.py:56`). When `claude` is not in cron's PATH, you get a perfectly successful `Popen`, a shell exit 127 nobody sees, a lease that ticks down to expiry 30 minutes later, a retry, another silent failure, then `halt` at attempt 3 — and the user has no telemetry distinguishing "worker crashed in phase" from "worker never ran." Worst-case bad signal.

---

## Detailed findings

### 1. `fcntl.flock` correctness — **Medium**
`state.py:90-100`. POSIX advisory lock; kernel auto-releases on process exit OR fd close, including SIGKILL — that's fine and is the right primitive for single-host coordination. Two real concerns:

- **Lock file is the same path the lock protects' sibling, opened `"w"`.** Opening a lockfile in write mode truncates it on every acquire. Harmless (it stores nothing) but ugly; should be `"a"` or `os.open(..., O_CREAT|O_RDWR)`. **Low.**
- **No lock on `load()` in `cmd_status`** (`cli.py:138`). Reader can observe a state mid-mutation… actually no, `os.replace` is atomic so a reader sees pre-or-post, not torn. Fine.
- **NFS / iCloud Drive caveat.** `~/Documents` syncing? `fcntl` is meaningless across hosts. For single-Mac it's fine. Worth a README note.

**Fix:** Switch lockfile open to `os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)` to avoid truncating noise. Document iCloud/NFS as unsupported.

### 2. `save_atomic` crash-safety — **High** (one real gap)
`state.py:120-139`. The file-rename ordering is correct: write tmp → flush → `fsync(fd)` → `os.replace`. APFS `rename(2)` is atomic w.r.t. crash on macOS (Apple FS guarantees rename atomicity per inode; man page confirms). The **real gap**:

- **No `fsync` on the parent directory** after `os.replace`. Crash-after-rename-before-dirent-flush can leave the directory entry pointing at either the old inode or no entry (depending on journal state). On APFS this is largely mitigated by the metadata journal, but POSIX-correct durable rename requires `dir_fd = os.open(parent, O_DIRECTORY); os.fsync(dir_fd)` after replace. **Medium for solo-Mac with battery; High for a Mac mini behind a flaky UPS.**

**Fix:**
```python
os.replace(tmp_name, state_path)
dir_fd = os.open(state_path.parent, os.O_DIRECTORY)
try: os.fsync(dir_fd)
finally: os.close(dir_fd)
```

### 3. Lease lifecycle — **High**
`state.py:146-167`, `state.py:187`. Three real bugs:

- **Wall-clock everywhere.** `lease_expires = now + timedelta(30min)`, then `expires > _now_utc()` compared as wall time. **System sleep does NOT pause the clock** — Mac sleeps 8 hours, lease is expired on wake, supervisor re-dispatches as if the worker died. That's actually the right behavior here (cron didn't fire either), but if the user adjusts clock backwards (NTP step, manual change, DST is not an issue since UTC) the lease appears to extend. Acceptable. **Low.**
- **`parse_iso` uses `fromisoformat`** (`state.py:68`). `_ISO_FMT` writes `Z` suffix. Python 3.11+ handles `Z` — confirmed. Older runtimes would silently drop tz and compare naïve-vs-aware → `TypeError`. Pin Python ≥3.11 in `pyproject.toml`. **Medium.**
- **String timestamps stored, then re-parsed every comparison.** Seconds precision means two events in the same second sort by list order, not time. Bites when you replay events. **Low** for solo, plant a memory.

**Fix:** Pin Python 3.11. Add monotonic equivalent? No — solo-mac, wall clock is fine; just document.

### 4. Crash recovery / idempotency — **High**
`supervisor.py:103-118`. Lease expiry → `claim_phase` increments `attempts` (via projection — good) and dispatches the same phase again. **clu enforces nothing about idempotency.** It is 100% on the worker LLM to:
- Notice that prior commits already exist for this phase,
- Not double-create files,
- Not double-commit.

Even with `--commit SHA` logged in `EVENT_PHASE_COMPLETED`, there's nowhere for the worker to inspect "what did the previous attempt do?" except by reading the git log. Phase plans don't get a list of prior partial commits passed in. **This will produce duplicate commits or worse, conflicting refactors.**

**Fix:** When dispatching attempt N>1, pass `--prior-commits=SHA1,SHA2` via template variable. Add `prior_attempts_for_phase()` projection. Worker prompt must explicitly say "phase N was retried — inspect commits X,Y for partial work."

### 5. State corruption — **Critical (architecturally)**
Events live in `state.json["events"]` (`state.py:85, 142`). The whole "events are durable, derivations are cheap" narrative breaks the moment the single file gets truncated, because **events and derived state share the same blast radius.** If `os.replace` is interrupted before completion (APFS makes this rare but not impossible), you lose both the latest state AND the projection source.

**Fix:** Append events to a sibling `<slug>.events.jsonl` via `open(..., "a")` + `fsync` BEFORE the state mutation closes. Then `state.json` becomes a cache/checkpoint and a corrupt one is recoverable by replaying events. This is a real architectural change but it's the one that justifies the words "source of truth."

### 6. Concurrency edge cases — **Medium**
- **SIGTERM mid-mutate.** cron's parent gets killed while `tick` holds the lock. `flock` auto-releases on fd close (kernel). `os.replace` is atomic. **State is fine.** Worst case: lock released without writing → next tick reloads previous state. ✓
- **Slow tick under lock.** State files will be <100KB for years; not a concern. Until you keep events forever — then it's a concern at month 12. Add an event compaction story now.
- **Multiple plans per project.** Different `.state.json`, different `.lock`, fully independent. Fine. ✓
- **Two `tick` invocations racing.** Second blocks on flock, runs after first commits, sees the new claim, returns idle. ✓

### 7. Observability — **Critical (DX)**
There is essentially nothing:
- No log file. Cron stderr goes wherever the user's crontab redirects it (typically nowhere).
- `dispatch_for_tick` prints to stderr (`dispatch.py:60`) — invisible under cron.
- No structured tick log (timestamp, action, reason).
- `clu status` only shows current snapshot. No "what happened in the last 24h."
- No way to ask "why did this phase halt" without `jq` on `state.json`.

**Fix (this is cheap and high-leverage):**
1. Append every tick result to `<plan_dir>/.orchestrator/<slug>.tick.log` (jsonl: `{ts, action, detail, pid}`).
2. `clu status --since 24h` reads the log + recent events into a single timeline.
3. Add `clu doctor` that checks: `claude` on PATH, cron entry exists, state file parses, lock not held >10s, last tick within 2× cadence.

### 8. "Events are source of truth" — **High**
Spot-check across the code:

| Derived? | Field | Reality |
|---|---|---|
| Yes | `completed_phase_ids` | Projects from `EVENT_PHASE_COMPLETED`. ✓ |
| Yes | `attempts_for_phase` | Projects from `EVENT_PHASE_STARTED`. ✓ |
| **No** | `data["status"]` | Imperatively mutated at `supervisor.py:62,75,106,126`. ✗ |
| **No** | `data["current_claim"]` | Mutated directly in `claim_phase` / `release_claim` / `release_if_expired`. Only paired event is `EVENT_PHASE_STARTED`; no `EVENT_PHASE_RELEASED`. ✗ |
| **No** | `data["blockers"][i].consumed` | Set in `supervisor.py:74` next to `EVENT_BLOCKER_CONSUMED`. Two writes, one truth. ✗ |

**Fix:** Either honestly downgrade the claim ("events are an audit log, state is the truth, they MAY drift") or introduce `EVENT_CLAIM_RELEASED` / derive `status` via a `project_status(events)` function. The honest downgrade is fine for solo; just stop telling yourself the events are authoritative.

### 9. Dispatch failure modes — **Critical**
`dispatch.py:52-59`. `Popen(cmd, shell=True, stderr=DEVNULL, start_new_session=True)`. Failure modes invisible:
- `claude` not on cron's PATH → shell exit 127, no signal.
- `claude` segfaults at startup → exit 139, no signal.
- Cookie/auth expired → claude prints to stderr and exits, no signal.

**Fix:**
1. Redirect stderr/stdout to `<plan_dir>/.orchestrator/<slug>.worker.<token>.log` (NOT `DEVNULL`).
2. Record dispatched `pid` in the claim; `clu status` can `kill -0 pid` to assert worker is still alive.
3. Optional: synchronous pre-flight `shutil.which("claude")` in `dispatch_for_tick` and emit an `EVENT_DISPATCH_FAILED` if missing.

### 10. Test coverage gaps — **High**
Missing:
- `save_atomic` interrupted (mock `os.replace` to raise → tmp cleanup).
- Two `tick()` racing (use `multiprocessing` + `Barrier`, assert one dispatches).
- Lease-clock-skew (monkeypatch `_now_utc` backward between calls).
- Dispatch fail (`cmd_tmpl = "false"`, assert state still claims so retry can happen — or that we **don't** claim if dispatch fails, which is a design choice clu hasn't made yet).
- Worker crash mid-phase (claim exists, no completion event, lease expires, attempts increments to 2).
- Status drift (manually set `data["status"] = "halted"` without event; assert `clu doctor` flags).
- `events.jsonl` corruption / out-of-order (once you add it).

### 11. 5-min cron vs 30-min lease — **Low**
6:1 ratio is fine. 1-min cron works: tick is O(events), still <50ms at 1k events. The only thing that breaks at high cadence is **dispatch double-fire if a worker hasn't called `complete` yet but is mid-run** — but that's covered by `current_claim` blocking re-dispatch (priority 5). ✓. If you go to 1-min cron, drop lease to 5–10min so failures recover faster.

### 12. Daemon mode (`clu watch`) — **Medium**
Real trade-offs:

| | cron | `clu watch` (daemon) |
|---|---|---|
| Survives reboot | yes (launchd loads crontab) | needs launchd plist |
| Sleep behavior | misses ticks while asleep (OK) | wakes immediately on resume (better UX) |
| Sub-minute cadence | no (cron min granularity = 1m) | yes |
| Crash visibility | none | process visible in Activity Monitor |
| Concurrency | many short procs, each grabs lock | one proc, internal scheduler |
| Failure modes | per-tick isolated | daemon crash = total outage |

Recommendation: ship cron (already done), add `clu watch --interval 30s` later as opt-in. The daemon path lets you do **inotify on `state.json`** for instant response to `clu answer` / `clu complete`, which is the real UX win — the user shouldn't wait up to 5 min after answering a blocker.

---

## Recommendations (concrete, ordered by ROI)

1. **Move events to `<slug>.events.jsonl`** — append + fsync before state mutation. (Finding 5.)
2. **Add `clu doctor` + tick log** — single biggest debuggability lift. (Finding 7.)
3. **Pipe worker stdout/stderr to per-token log** + record pid in claim. (Finding 9.)
4. **Pass prior-attempt commits to retried workers** via dispatch template. (Finding 4.)
5. **`fsync` parent directory after `os.replace`.** Two-line fix. (Finding 2.)
6. **Pick a lane on status:** either derive it (`project_status(events)`) or document "state is truth, events are audit." Stop having both. (Finding 8.)
7. **Pin Python 3.11+** in `pyproject.toml`. (Finding 3.)
8. **Add the missing tests** in finding 10, especially the two-tick race and dispatch-fail cases.

---

## Bonus — things you didn't ask

- **`/dev/null` discards stderr from the supervisor's own `print` in `dispatch.py:60`.** Even your own logging is invisible. Use `logging` module + a rotating file handler everywhere.
- **`empty_state` stamps `created_at` but no `clu_version`.** When schema_version moves to 2, you'll wish you had the binary version that wrote each file. Cheap to add now.
- **`resolve_blocker_answer`** (`state.py:257`) treats any all-digit answer as an option index — so a user trying to answer "1234 reps" with free text gets silently coerced to options[1234] → IndexError → returned raw. Inconsistent. Prefer explicit `--index 0` / `--text "..."` flags in `clu answer`.
- **`add_blocker` IDs are `q-{len+1}`** (`state.py:228`). If you ever GC answered blockers, IDs collide. Use a monotonic counter in state or a uuid. **Medium.**
- **No `EVENT_CLAIM_RELEASED`** — you can't reconstruct lease history. If month-6 you want "how often do phases retry," you can count `phase_started` but not "how long did each attempt actually run." Add it.
- **`notify.py` is 13 lines I didn't read** — but if you're routing alerts there and it talks to anything network, that's the first place a future you will break the "local-only" promise. Flag for self-review.
- **launchd > cron on macOS.** Apple has been deprecating cron for years; `launchd` plist gives you `StandardOutPath` / `StandardErrorPath` for free, which solves half of finding 7.
