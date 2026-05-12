# clu-inbox-gap-notifications — stuck-blocker re-ping + stalled-claim transition

You are phase `gap-notifications` of the `clu-inbox` plan. Second of three. Closes part of [#20](https://github.com/smabe/end-of-line/issues/20).

Phase 1 (`inbox-hook`) shipped the inbox primitive and hook. This phase adds the two missing notification kinds that the monitor was supposed to provide — stuck-blocker re-ping (escalation) and stalled-claim transition. Both fire to BOTH iMessage AND the inbox so they're visible to active Claude sessions on next turn.

Read the master plan first. Do not redesign.

## Locked decisions (do NOT re-litigate)

- **Stuck blocker**: blockers with `consumed: false` AND `(now - created_at) > 30min` AND no `last_repinged_at` OR `(now - last_repinged_at) > 30min`. Fire notify + inbox event. Stamp `last_repinged_at`. Repeat every 30min until consumed.
- **Stalled claim**: `current_claim` with `lease_expires < now` AND plan `status == STATUS_RUNNING`. Fire notify + inbox event ONCE. Stamp `current_claim.stalled_notified: true` to prevent re-firing on subsequent ticks.
- **Notification kinds**: new `KIND_STUCK_BLOCKER`, `KIND_STALLED_CLAIM`. Both respect quiet hours (NOT in `QUIET_HOURS_BYPASS_KINDS` — they're not emergencies, they're escalations).
- **Inbox writes**: unconditional (not gated by quiet hours). The inbox is for next-turn Claude, not for waking the operator.
- **Tick chain slotting**: the existing 8-priority chain is first-match-wins per CLAUDE.md. Stuck-blocker and stalled-claim detection slot AFTER higher-priority blocker-resumed / halt / dispatch but BEFORE idle. New priorities are non-preempting — they fire alongside the existing tick action, not instead of it.
- **Where to fire**: notify + inbox writes happen in `cmd_tick` (or `supervisor.tick`) after the TickResult is produced but before returning. They're side-effects on the same tick, not their own first-class actions.

## Read first

- Phase 1 output: `end_of_line/inbox.py` (especially `write_event`).
- `end_of_line/state.py` — blockers structure (`data["blockers"]`), `current_claim`, `STATUS_*` constants, `STATUS_RUNNING`, `STATUS_STALLED`. Look for fields you'd add (`last_repinged_at`, `stalled_notified`).
- `end_of_line/notify.py:25-50` — `KIND_*` constants and `QUIET_HOURS_BYPASS_KINDS` set.
- `end_of_line/notify.py:87-112` — `notify()` function. Read to understand the iMessage path; do NOT change its shape.
- `end_of_line/notify.py:126-134` — `render_blocker` pattern. Mirror for the new render functions.
- `end_of_line/supervisor.py:100-209` — `tick()` body. The new detection logic goes here, NOT in cmd_tick. Returns alongside the existing TickResult.
- `end_of_line/supervisor.py:130-138` — the consumed-blocker resumption branch. The stuck-blocker check sits adjacent (same data structure, different predicate).
- `end_of_line/cli.py` — `cmd_tick` and `cmd_tick_all`. These call `supervisor.tick` and then dispatch notifications via `result.notify_body`. Stuck-blocker / stalled-claim emissions need a similar return-channel — augment `TickResult` with an optional `side_notifies: list[(kind, body)]` for parallel emissions on the same tick.
- `tests/test_supervisor.py` — existing tick tests. Patterns for state setup, time-monkeypatching, assert on TickResult shape.

## Produce

### 1. TDD: failing tests first

New file `tests/test_stuck_blocker.py`:

- `test_blocker_under_30min_does_not_reping` — create blocker at t=0, tick at t=29min, no notify, no inbox event, no `last_repinged_at`.
- `test_blocker_over_30min_first_reping_fires` — blocker at t=0, tick at t=31min, notify fires (kind=stuck_blocker), inbox event written, blocker has `last_repinged_at` stamped.
- `test_blocker_reping_repeats_every_30min` — tick at t=31, t=61, t=91. Three repings, each updates `last_repinged_at`.
- `test_blocker_reping_does_not_fire_within_30min_of_last_reping` — tick at t=31 fires, tick at t=45 does NOT (last_repinged_at=31, only 14min gap).
- `test_consumed_blocker_does_not_reping` — blocker created, answered + consumed, tick at t=31min — no notify, no inbox event.
- `test_reping_respects_quiet_hours` — tick at quiet-hour time, blocker eligible. iMessage suppressed (quiet hours respected) but inbox event still written (inbox is for Claude, not the operator).
- `test_reping_renders_question_and_options` — notify body contains the original question + option list (mirrors `render_blocker` shape but with re-ping prefix).
- `test_reping_inbox_event_shape` — inbox event type=`stuck_blocker`, summary mentions "blocker open Nmin", details contains question + options + blocker_id.

New file `tests/test_stalled_claim.py`:

- `test_active_claim_within_lease_no_notify` — claim with lease 10min in future; tick fires no stalled-claim notify.
- `test_expired_lease_with_status_running_fires_once` — claim with lease 10min past; status=RUNNING; tick fires notify (KIND_STALLED_CLAIM) + inbox event; claim has `stalled_notified: true`.
- `test_expired_lease_does_not_refire_after_notified` — same setup, two ticks; only first one fires notify.
- `test_expired_lease_with_status_halted_does_not_fire` — claim expired BUT plan halted; no stalled notify (halt already covers it).
- `test_stalled_claim_respects_quiet_hours` — quiet-hour tick, claim eligible; iMessage suppressed, inbox event still written.
- `test_stalled_inbox_event_shape` — event type=`stalled_claim`, summary mentions phase + how long stalled, details has claim metadata.
- `test_stalled_then_released_then_re_stalled_fires_again` — claim stalled (notified), released via `release-claim`, new claim re-stalls; new claim has no `stalled_notified` → fires again.

Run suite — all new tests FAIL.

### 2. State + event additions

In `end_of_line/state.py`:

- Document (in module docstring or near blockers section) the new blocker field: `last_repinged_at` (optional ISO ts; absent on never-repinged).
- Document the new claim field: `stalled_notified` (optional bool; absent on never-notified).
- No schema bump needed — new optional fields are additive on state.json reads.
- Add new event kinds:
  ```python
  EVENT_STUCK_BLOCKER_REPINGED = "stuck_blocker_repinged"
  EVENT_STALLED_CLAIM_NOTIFIED = "stalled_claim_notified"
  ```

### 3. Notify additions

In `end_of_line/notify.py`:

```python
KIND_STUCK_BLOCKER = "stuck_blocker"
KIND_STALLED_CLAIM = "stalled_claim"

# Add to ALL_KINDS but NOT to QUIET_HOURS_BYPASS_KINDS — these are escalations,
# not emergencies. Halt notifications still bypass; these don't.


def render_stuck_blocker(plan_slug, blocker_id, phase, question, options, age_min):
    opts = "\n".join(f"[{i}] {o}" for i, o in enumerate(options))
    return (
        f"⏰ {plan_slug}/{blocker_id} still open ({age_min}min) [{phase}]\n"
        f"{question}\n{opts}\n\n"
        f"Reply: `{plan_slug} <number>`."
    )


def render_stalled_claim(plan_slug, phase, age_min):
    return (
        f"🐌 {plan_slug}/{phase} claim stalled ({age_min}min past lease).\n"
        f"Worker is unresponsive. Run `clu release-claim --plan {plan_slug} "
        f"--phase {phase}` to free it, or `clu retry` if you've fixed the underlying "
        f"cause."
    )
```

### 4. Detection in supervisor.tick

Augment the existing detection chain. Each detection writes its inbox event + appends a side-notify entry to a new `TickResult.side_notifies: list[tuple[str, str]] | None` field.

```python
@dataclass
class TickResult:
    action: str
    detail: str = ""
    notify_body: str | None = None
    phase_id: str | None = None
    token: str | None = None
    side_notifies: list[tuple[str, str]] = field(default_factory=list)  # NEW
```

In `tick()` body, before returning each TickResult, detect:

```python
# Stuck-blocker re-pings: applies even when tick returns "idle" or "blocker_resumed".
now = st._now_utc()
for b in data["blockers"]:
    if b.get("consumed"):
        continue
    if b.get("answer") is not None:
        continue  # answered, not yet consumed — handled by resumption branch above
    created = st.parse_iso(b["created_at"])
    last_pinged = st.parse_iso(b["last_repinged_at"]) if b.get("last_repinged_at") else None
    age_min = int((now - created).total_seconds() / 60)
    pingable = age_min >= 30 and (last_pinged is None or (now - last_pinged).total_seconds() >= 30 * 60)
    if pingable:
        b["last_repinged_at"] = st.utcnow()
        st.append_event(data, st.EVENT_STUCK_BLOCKER_REPINGED, blocker_id=b["id"], age_min=age_min)
        body = notify.render_stuck_blocker(
            data["plan_slug"], b["id"], b["phase_id"],
            b["question"], b["options"], age_min,
        )
        result.side_notifies.append((notify.KIND_STUCK_BLOCKER, body))
        inbox.write_event(
            type="stuck_blocker", plan_slug=data["plan_slug"],
            project_root=str(config.project_root.resolve()),
            summary=f"Blocker {b['id']} on phase {b['phase_id']} open {age_min}min",
            details={"blocker_id": b["id"], "phase_id": b["phase_id"],
                     "question": b["question"], "options": b["options"]},
        )

# Stalled-claim transition: one-shot.
claim = data.get("current_claim")
if claim and data["status"] == st.STATUS_RUNNING:
    lease = st.parse_iso(claim["lease_expires"])
    if now > lease and not claim.get("stalled_notified"):
        claim["stalled_notified"] = True
        age_min = int((now - lease).total_seconds() / 60)
        st.append_event(data, st.EVENT_STALLED_CLAIM_NOTIFIED, phase=claim["phase_id"], stalled_min=age_min)
        body = notify.render_stalled_claim(
            data["plan_slug"], claim["phase_id"], age_min,
        )
        result.side_notifies.append((notify.KIND_STALLED_CLAIM, body))
        inbox.write_event(
            type="stalled_claim", plan_slug=data["plan_slug"],
            project_root=str(config.project_root.resolve()),
            summary=f"Claim on phase {claim['phase_id']} stalled {age_min}min past lease",
            details={"phase_id": claim["phase_id"], "stalled_min": age_min,
                     "claimed_by": claim["claimed_by"]},
        )
```

**Important**: insert these BEFORE the early-return `if data["status"] in st.TERMINAL_STATUSES` at supervisor.py:140. Stalled claims must be detectable even when the plan is mid-running; stuck blockers must be detectable even when tick would otherwise return "idle." Position carefully — don't preempt the consumed-blocker resumption branch (it's higher priority).

### 5. Wire side_notifies into cmd_tick and cmd_tick_all

In `end_of_line/cli.py`'s `cmd_tick` (or wherever notifications get dispatched after a TickResult is returned), add:

```python
if result.notify_body:
    notify.notify(cfg.notify, _kind_for_result(result), result.notify_body)
for kind, body in result.side_notifies:
    notify.notify(cfg.notify, kind, body)
```

Same in `cmd_tick_all`.

### 6. Also wire phase 1's inbox writes for the existing notification kinds

Phase 1 shipped `inbox.write_event` but did NOT actually wire `notify.py` to call it. Do that here so the full inbox surface is live. In `notify.py`'s `notify()` function:

```python
def notify(spec, kind, body, *, now=None, sender=None, inbox_writer=None,
           plan_slug=None, project_root=None) -> bool:
    # ... existing iMessage + quiet-hour logic ...

    # Inbox write happens regardless of quiet hours — Claude needs the signal
    # even when the operator is asleep. Plan_slug + project_root needed for filtering.
    if inbox_writer is not None and plan_slug is not None and project_root is not None:
        inbox_writer(
            type=kind, plan_slug=plan_slug, project_root=project_root,
            summary=body.splitlines()[0][:200],  # one-line teaser
            details={"full_body": body},
        )

    # ... rest of existing logic ...
```

`inbox_writer` defaults to `inbox.write_event`. Callers in `cmd_tick` / `cmd_tick_all` pass plan_slug + project_root.

Existing tests for `notify()` continue to pass (new params optional). New test:
`test_notify_writes_to_inbox_when_plan_slug_provided` — call `notify` with plan_slug/project_root; inbox event appears.

### 7. Run the suite — all green

Phase 1 tests + phase 2 tests. ~15 new tests in phase 2 across stuck_blocker, stalled_claim, and the notify wiring.

### 8. `/simplify` then commit

Title: `clu-inbox: stuck-blocker re-ping + stalled-claim transition + notify→inbox wiring`.
Body references `closes #20 phase 2 of 3`.

## Verification before `clu complete`

Re-run full suite. Confirm: existing notify / supervisor / state tests green; new tests pass; total count delta matches.

## Acceptance

- [ ] Stuck blocker re-pings every 30min until consumed
- [ ] Stalled claim transition fires once per (claim, transition) pair
- [ ] Both kinds respect quiet hours for iMessage but always write to inbox
- [ ] `notify.notify` writes inbox events for ALL existing kinds when plan_slug/project_root provided
- [ ] `TickResult.side_notifies` field exists for parallel emissions
- [ ] No regression in existing supervisor tick behavior
- [ ] One commit referencing `closes #20 phase 2 of 3`
