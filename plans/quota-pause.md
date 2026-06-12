# quota-pause — quota-aware worker-death classification (closes #94)

Workers killed by the operator's subscription session limit are indistinguishable from real failures: the death escapes the 0.5s fast-fail window (`dispatch.py:362`), lands in the supervisor's dead-PID probe (`supervisor.py:662-699`) which reads no logs, and each redispatch burns a `phase_started` until the 3-attempt halt (`supervisor.py:785-806`). On 2026-06-11 this froze 8 HealthData plans for ~5.5h past a 01:50 reset. This plan classifies quota deaths from the worker log, forgives the attempt, pauses the **project** until the parsed reset time, and auto-resumes canary-first.

Ordering: pure matcher first (P1), then classification + attempt forgiveness + pause write (P2), then the dispatch gate + canary resume (P3), then notifications + docs (P4). Each phase ships green on its own; P2 is already a behavior improvement (no attempt burn) before P3 adds the gate.

## Diagnosis

- **Hypothesis:** quota deaths take the *unclassified* path twice over — the session-limit wording doesn't match `_RATE_LIMIT_RE` (`dispatch.py:80-83`, needs literal "rate limit"), and the supervisor's worker-dead block (`supervisor.py:662-699`) never reads the log at all.
- **Falsifiable test:** run the observed log lines against `_RATE_LIMIT_RE`.
- **Test result:** ran at plan time (2026-06-12) — both verbatim lines from the HealthData worker logs (`You've hit your session limit · resets 1:50am (America/New_York)`, `You're out of usage credits · resets 12:30pm (America/New_York)`) return `False`. Confirmed; phases scoped normally.

## Locked design decisions

### Phase 1 — matcher (`end_of_line/quota.py`, pure functions)
- **New module `quota.py`**, stdlib-only. `classify_quota(tail) -> QuotaMatch | None` (signature name + matched line) and `parse_reset(line, now) -> datetime | None` (aware UTC).
- **Signature table mirrors the systemic table's shape** (`dispatch.py:77-88`): hard-coded, grows via PR, first match wins. Covers `You've hit your {session|weekly|Opus|Sonnet} limit` and `You're out of usage credits` / `You've used...extra usage` prefixes; separator class `[·∙•|-]` (both U+00B7 and U+2219 observed in the wild); optional `:MM`, optional am/pm, optional `(IANA-tz)`. Grounded in the two verbatim local log lines + community-corroborated variants (claude-code issues #65080, #60708, #67556; vibe-coding-auto-resume + Opendray regexes).
- **Reset parsing:** `%I:%M%p` via `strptime` (case-insensitivity verified at plan time), `ZoneInfo` from the parens (works on stock macOS Python, `/usr/share/zoneinfo` first in TZPATH — verified at plan time), local tz when absent, next-occurrence rollover (candidate ≤ now → +1 day), default `fold=0` (max 1h error twice a year — acceptable for a ~5h window). First `zoneinfo` import in the codebase (none today).
- **Bucketing by parseability, not wording** (refines #94's A/B split): parseable reset → auto-resume pause; quota match without parseable reset (weekly `resets Mon 12:00am`, date forms, future SDK credit wording) → **stuck pause**, no auto-resume, loud notify. The fallback can never burn attempts or auto-resume wrongly.

### Phase 2 — classify deaths + forgive attempts + write the pause
- **New `EVENT_QUOTA_DEATH`** (token + phase + signature kwargs). `attempts_for_phase` subtraction set (`state.py:1210-1223`) widens to `{EVENT_SYSTEMIC_FAILURE, EVENT_QUOTA_DEATH}` — same forgiveness mechanism systemic failures already use.
- **Three classification sites** (the full peer set of death paths): supervisor dead-PID block (`supervisor.py:662-699`), supervisor lease-expiry block (the straggler path, same function), dispatch fast-fail (`dispatch.py:365-380`, quota check *before* systemic). All tail `claim["log_path"]` (stamped at `dispatch.py:392`) before the claim is released, reusing the 50-line-tail discipline (`_SYSTEMIC_TAIL_LINES`, `dispatch.py:75`).
- **Pause file `plans/.orchestrator/quota.json`** via `st.locked_json` with an `empty` factory (the registry/queue pattern, `state.py:543-565`). Schema: `{schema_version, paused_until, signature, line, canary_plan, canary_deadline, created_at}`. `paused_until = reset + 120s` fixed buffer (deterministic, testable; no random jitter needed on a single host).
- **Plan status never flips.** Unlike systemic's `_pause_and_halt` (`dispatch.py:624-669`), quota pause is a project-level dispatch gate — plans stay `RUNNING`, `EVENT_QUOTA_PAUSED` rides the triggering plan's event log (visible in `clu watch` for free), and `clu resume` needs no changes.
- **Quota-classified deaths suppress the misleading `render_worker_dead` notify body** — the quota events are the record; proper notification kinds land in P4.

### Phase 3 — dispatch gate + canary-first auto-resume
- **Gate inserts between tick priorities 7 and 8** (`supervisor.py:3-15` docstring renumbered): only the *dispatch* action is gated; watchdog priorities 1–5 keep running against in-flight claims while paused. Gate check sits immediately before `st.claim_phase` (`supervisor.py:807-808`) so canary stamping only happens for a plan that actually has a dispatchable phase. Gated ticks return `TickResult("idle", "quota_paused until=<ts>")` — no new action literal.
- **State machine lives in `quota.py`, decided under one `locked_json` window:** now < `paused_until` → idle; past it with no canary → *this* plan stamps itself canary (+180s deadline) and dispatches; canary stamped ≠ me, now < `canary_deadline` → idle; now ≥ `canary_deadline` → clear file, `EVENT_QUOTA_RESUMED`, dispatch normally.
- **Canary survival is implicit:** a canary quota-death goes through P2 machinery unchanged — it overwrites the pause file with a fresh `paused_until` and clears the canary slot. Deadline passing *without* a re-pause ⇒ resume. No new heartbeat plumbing.
- **Stuck pause** (`paused_until: null`) idles indefinitely; only the operator clears it (documented escape hatch: delete `quota.json`).
- **Queue pops during pause are harmless** (exclusion analysis): a popped plan's tick hits the same gate and idles, so running and queued plans are uniformly gated — no serialization hazard.

### Phase 4 — notify + docs
- **`KIND_QUOTA_PAUSED` / `KIND_QUOTA_RESUMED` defer in quiet hours** (auto-resume means no 3am action needed; inbox surfaces regardless). **`KIND_QUOTA_STUCK` joins `QUIET_HOURS_BYPASS_KINDS`** (`notify.py:73-79`) — a fleet frozen with no horizon is halt-equivalent.
- Render functions carry the reset time in the body.
- Docs: contract.md (3 new events + quota.json schema), architecture.md (tick chain), reference.md (quota module), operations.md (recovery runbook incl. the `rm quota.json` escape hatch).

## Non-goals

- **`ANTHROPIC_API_KEY` in dispatch env** — explicitly excluded by #94; orthogonal escape hatch.
- **CLI verbs for pause management** — safe to omit: the pause self-heals via auto-resume, and the stuck case has a documented one-line escape hatch (`rm plans/.orchestrator/quota.json`). A `clu doctor` staleness check can follow if it ever bites.
- **Parsing weekly/date reset forms** — safe asymmetry: unparseable forms fall into the stuck bucket, which never burns attempts, never auto-resumes wrongly, and notifies loudly.
- **Config knobs for the 120s buffer / 180s canary window** — module constants; no second caller exists.
- **stream-json `api_retry` detection** — workers run text mode; the log tail is the contract for all three existing signatures already.

## Files touched

- `end_of_line/quota.py` — P1 NEW, P2, P3 — matcher + reset parser + pause-file state machine. API hotspot: `quota.json` schema.
- `end_of_line/state.py` — P2 — `EVENT_QUOTA_DEATH`/`EVENT_QUOTA_PAUSED`/`EVENT_QUOTA_RESUMED`; `attempts_for_phase` subtraction set. API hotspot: EVENT_* constants (append-only).
- `end_of_line/supervisor.py` — P2, P3 — death-site classification ×2; dispatch gate. API hotspot: tick priority chain docstring.
- `end_of_line/dispatch.py` — P2 — fast-fail quota check before systemic.
- `end_of_line/notify.py` — P4 — 3 new KINDs + renders + bypass set.
- `tests/test_quota.py` — P1-P3 NEW; `tests/test_supervisor.py`, `tests/test_systemic_failure.py` — P2, P3 additions.
- `docs/contract.md`, `docs/architecture.md`, `docs/reference.md`, `docs/operations.md` — P4.

## Per-phase done checklist

- TDD: failing tests first.
- `/code-review` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood / Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- **Stamp attestations AFTER the commit:**
  - `clu verify --plan quota-pause --phase <id> --token <T>`
  - `clu attest --simplify --plan quota-pause --phase <id> --token <T>`
- Call `clu complete --plan quota-pause --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| matcher | `quota-pause-matcher.md` | quota.py signature table + reset-time parser, pure TDD | 1h |
| classify | `quota-pause-classify.md` | 3 death sites classify + attempt forgiveness + pause write | 2h |
| gate | `quota-pause-gate.md` | tick dispatch gate + canary state machine + auto-resume | 2h |
| notify-docs | `quota-pause-notify-docs.md` | KIND_QUOTA_* + renders + 4 docs (closes #94) | 1h |

## Findings log

_Empty at plan time. As phases run, the worker appends one dated bullet per cross-phase finding — a gotcha, a spike result, an API surprise, an assumption that turned out wrong — so a later phase doesn't rediscover it. Cite file:line._

- 2026-06-12 (matcher): `python3 -m unittest discover -s tests` run inside the hardened worker sandbox fails with 42 known env failures — all of `test_webserver` (socket binds blocked) plus the process-group reaping tests in `test_terminalize` / `test_zombie_sweep` / `test_reap_orphan_pgroup` (killpg restricted). None quota-related. Don't burn time investigating; `clu verify` runs sandbox-exempt and is the authoritative green.
- 2026-06-12 (classify): `record_quota_pause` takes the **orchestrator dir** (state-file parent), not `project_root` as the sub-plan sketched — `plan_dir` is configurable (`config.py:117`) and every death site already holds the state path, so `state_path.parent / quota.QUOTA_FILE_NAME` is the canonical derivation. Phase `gate` must derive the same way.
- 2026-06-12 (classify): the shared tail helper landed as `quota.read_log_tail` (deque-bounded, "" on OSError); `_match_systemic_signature` reads through it and `_SYSTEMIC_TAIL_LINES` is gone from dispatch.py. Known minor cost: a non-quota fast-fail reads the tail twice (quota check, then systemic) — judged acceptable (≤50-line read on a rare path) over changing the matcher's path-based test contract.
- 2026-06-12 (classify): `EVENT_QUOTA_PAUSED` events carry **no `phase` key** (kwargs: paused_until, signature). Every current `data["events"]` consumer uses `.get` or filters by type first (verified via cross-file trace), but gate-phase code iterating events must not assume `evt["phase"]` exists.
- 2026-06-12 (gate): chose **unlink** over a cleared sentinel — "quota.json absent == not paused" is the single invariant the hot path (`Path.exists()` before any lock) and the operator escape hatch (`rm quota.json`) both rely on. The resume tick unlinks inside the gate's locked window; the supervisor appends `EVENT_QUOTA_RESUMED` in its own already-open `st.mutate` window (different file/lock, no nesting hazard).
- 2026-06-12 (gate): the gate **cannot reuse `record_quota_pause`'s `locked_json`** — `locked_json` unconditionally re-saves on exit, which would resurrect the file on the unlink-resume path and re-save on read-only idle ticks. `gate_decision` uses raw `st.locked` + `st.load` + conditional `st.save_atomic`/`unlink`. Don't "DRY" these together; their save semantics genuinely differ.
- 2026-06-12 (gate): the gate changes quota-death **redispatch** behavior — a death writes the pause, so the next tick idles until reset instead of immediately redispatching. The classify-phase `test_three_quota_deaths_burn_zero_attempts` was updated to clear `quota.json` between deaths (modeling the reset elapsing). Forgiveness (zero attempts) is unchanged; only the redispatch timing moved behind the gate.
- 2026-06-12 (gate): "malformed file must not freeze the fleet" must cover **field-level** corruption (bad `paused_until` string, unpaired `canary_deadline`), not just bad JSON — the timestamp `parse_iso` calls live inside the read guard, and a `canary_plan` with null `canary_deadline` self-heals via resume. `FileNotFoundError` from a concurrent-resume race is caught separately and stays silent (no misleading "unreadable" note).
