# lease-reliability-supervisor-wire — wire reap into lease expiry (#57 part 2/2, closes #57)

You are phase `supervisor-wire` of the `lease-reliability` plan. Wire
the `reap_orphan_pid` helper (shipped in `reap-core`) into the
supervisor's lease-expiry path. Closes #57.

## Locked decisions (do NOT re-litigate)

See `plans/lease-reliability.md`. Summary:

- Wire site: `supervisor.py` line ~229, the `if st.release_if_expired(data):` block.
- Read `claim["pid"]` and `claim["claimed_by"]` BEFORE calling `release_if_expired` (which nulls `current_claim`).
- Call `reap_orphan_pid` AFTER `release_if_expired` returns True.
- `cmdline_match=f"/clu-phase {data['plan_slug']} {claim['phase_id']}"` — both slug + phase id, defeats PID reuse.
- Both events (`EVENT_LEASE_EXPIRED` then `EVENT_PHASE_ORPHAN_REAPED`) land in the same tick.
- `clu watch` text mode adds an `orphan_reaped` line matching the existing `lease_expired` shape.

## Read first

- `end_of_line/supervisor.py:217-236` — the `with st.mutate(state_path) as data:` block where `release_if_expired` is called. The tick chain is first-match-wins; do not change ordering, just add the reap call inside the existing `if st.release_if_expired(data):` branch.
- `end_of_line/state.py` — locate `reap_orphan_pid` (added in `reap-core`) and `EVENT_PHASE_ORPHAN_REAPED`.
- `end_of_line/watch.py` — find the event-to-line projection. Add an `orphan_reaped` case mirroring `lease_expired`.
- `tests/test_supervisor.py` — existing pattern for lease-expiry tests. Mirror the AAA shape.

## Produce

1. **Failing tests first.** Extend `tests/test_supervisor.py`:
   - `test_lease_expired_reaps_orphan_pid`: monkeypatch `state.reap_orphan_pid` to a `MagicMock` recording the call. Build state with a `current_claim` whose `lease_expires` is in the past + a `pid` set. Run `supervisor.tick`. Assert the mock was called with `pid=<expected>` and `cmdline_match=f"/clu-phase {slug} {phase}"`. Assert event order in `data["events"]`: last two events are `lease_expired` then `phase_orphan_reaped`.
   - `test_lease_expired_no_pid_skips_reap`: same setup but omit `pid` from `current_claim`. Assert reap mock NOT called; assert only `lease_expired` event appended; tick still returns `lease_expired` action.
   - `test_orphan_reaped_event_carries_signal`: when reap mock returns `ReapResult(signaled="SIGTERM", escalated_kill=False, cmdline_mismatch=False)`, assert the appended event includes `signaled="SIGTERM"`.

   Extend `tests/test_clu_watch.py` (or wherever watch projection lives):
   - `test_watch_emits_orphan_reaped_line`: feed an event of type `phase_orphan_reaped` through the projection; assert output line matches the expected shape (e.g. `orphan_reaped plan=<slug> phase=<id> pid=<pid> signaled=SIGTERM`).

2. **Implementation in `end_of_line/supervisor.py`:**
   - Around line 228-230, change:
     ```python
     if claim := data.get("current_claim"):
         if st.release_if_expired(data):
             return _attach(TickResult("lease_expired", f"phase={claim['phase_id']}"))
     ```
     to:
     ```python
     if claim := data.get("current_claim"):
         pid = claim.get("pid")
         phase_id = claim["phase_id"]
         if st.release_if_expired(data):
             if pid:
                 result = st.reap_orphan_pid(
                     pid,
                     cmdline_match=f"/clu-phase {data['plan_slug']} {phase_id}",
                 )
                 st.append_event(
                     data, st.EVENT_PHASE_ORPHAN_REAPED,
                     phase=phase_id, pid=pid,
                     signaled=result.signaled,
                     cmdline_mismatch=result.cmdline_mismatch,
                 )
             return _attach(TickResult("lease_expired", f"phase={phase_id}"))
     ```
   - Note: `claim` is captured BEFORE `release_if_expired` mutates `current_claim` to `None`, so `claim["phase_id"]` is still valid.

3. **Implementation in `end_of_line/watch.py`:**
   - Locate the event-projection function (search for `EVENT_LEASE_EXPIRED` or `lease_expired` string). Add an `elif event["type"] == st.EVENT_PHASE_ORPHAN_REAPED:` branch emitting a text line in the same shape as `lease_expired`.

4. **Acceptance.**
   - New supervisor tests pass; new watch test passes.
   - Full suite green: `python3 -m unittest discover -s tests`.
   - Manual smoke: write a minimal repro script that creates a `current_claim` with an expired lease + a real PID running `time.sleep(30)`, runs one `supervisor.tick`, and verifies the PID is dead. Optional — only if tests don't already cover live-PID end-to-end.
   - `grep -n EVENT_PHASE_ORPHAN_REAPED end_of_line/supervisor.py end_of_line/watch.py` shows both call sites.

5. **Commit + complete.**
   - Structured commit: `lease-reliability: phase supervisor-wire — orphan reap on lease expiry (closes #57)`.
   - Stage explicit paths: `end_of_line/supervisor.py`, `end_of_line/watch.py`, `tests/test_supervisor.py`, `tests/test_clu_watch.py` (or whichever watch test file).
   - `clu verify` + `clu attest --simplify` per the gate.
   - `clu complete --plan lease-reliability --phase supervisor-wire --token <T>`.

## Failure modes to watch

- **`claim` reference after `release_if_expired`.** The walrus assignment captures the dict reference, but `release_if_expired` sets `data["current_claim"] = None` — the local `claim` is still valid because Python doesn't follow the dict-key reassignment. If you're unsure, copy `phase_id = claim["phase_id"]` BEFORE the release call (recommended; safer to read).
- **`current_claim["pid"]` may not exist on legacy state.** Today's dispatch code sets `pid`, but old state files registered before `pid` was added won't have it. Guard with `claim.get("pid")` — if missing, skip reap and only fire `lease_expired` (existing behavior). The test `test_lease_expired_no_pid_skips_reap` covers this.
- **Watch line shape drift.** Mirror `lease_expired` exactly (same fields, same key=value spacing). Downstream consumers (the `/clu-monitor` inbox hook, AI-agent task-list arming) may be regex-scanning these lines.
- **Don't change the TickResult action string.** Stays `"lease_expired"` even when reap fired. The reap is a side-effect within the same primary action.
