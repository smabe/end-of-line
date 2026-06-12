# quota-pause-gate — dispatch gate + canary-first auto-resume

You are phase `gate` of the `quota-pause` plan. You make the pause file actually gate dispatch, and add the canary-first auto-resume state machine. After this phase the full #94 loop works: quota death → project pause → first tick past reset dispatches one canary → canary survives 180s → fleet resumes (or canary re-pauses, attempt-free).

## Locked decisions (do NOT re-litigate)

See `plans/quota-pause.md`. Summary:

- Gate check sits in `supervisor.tick` immediately before `st.claim_phase` (`supervisor.py:807-808`) — i.e. only a plan that has a dispatchable phase consults the gate, so canary stamping never happens for a plan with nothing to dispatch. Watchdog priorities 1–5 are untouched and keep running while paused.
- Gated ticks return `TickResult("idle", "quota_paused until=<ts>")` — reuse the existing `"idle"` action literal, no new TickResult action.
- State machine in `end_of_line/quota.py`, one decision per `locked_json` window — `gate_decision(project_root, plan_slug, now) -> Decision` with four outcomes:
  1. `paused_until` set, now < it → **idle**.
  2. now ≥ `paused_until`, no canary stamped → stamp `canary_plan = plan_slug`, `canary_deadline = now + CANARY_WINDOW_SEC (180s)` → **dispatch** (this plan is the canary).
  3. Canary stamped ≠ this plan, now < `canary_deadline` → **idle**. (Canary == this plan, now < deadline → **dispatch** — re-tick of the canary plan must not idle its own redispatchable phases? No: the canary already holds a claim after dispatch; priority 7 idles it. A canary plan re-reaching the gate before deadline means its dispatch fast-failed non-quota — let it dispatch again; do NOT special-case.)
  4. now ≥ `canary_deadline` → delete/clear quota.json, append `EVENT_QUOTA_RESUMED` to this plan's event log → **dispatch**.
  - `paused_until: null` (stuck pause) → always **idle**; only operator file removal clears it.
- No quota.json present (or unreadable/corrupt → treat as absent and log to stderr) → **dispatch** (the overwhelmingly common path must stay cheap: one `Path.exists()` check before any locking).
- Canary survival is implicit: re-pause via phase-`classify` machinery overwrites the file and clears canary; deadline passing without a re-pause ⇒ resume. No heartbeat reads.

## Read first

- `plans/quota-pause.md` `## Findings log` — phases `matcher` + `classify` findings.
- `end_of_line/quota.py` — as shipped by the prior two phases (`record_quota_pause`, constants, file schema).
- `end_of_line/supervisor.py:1-15` — the priority-chain docstring you must renumber; and `:774-816` — the dispatch loop you're gating.
- `end_of_line/cross_plan_rules.py:76-91` — `_is_plan_active` / freeze semantics, to confirm the gate does NOT need queue-side changes (popped plans tick through the same gate).
- `tests/test_supervisor.py` — tick-action assertion patterns (`result.action == ...`).

## Produce

1. **Failing tests first** (`tests/test_quota.py` `GateDecisionTests` + `tests/test_supervisor.py` integration):
   - Active pause → tick returns idle with `quota_paused` detail; no claim created.
   - Past `paused_until`, no canary → first plan to tick dispatches AND quota.json now names it canary with deadline = now + 180s.
   - Second plan ticking during canary window → idle; canary plan itself → dispatch.
   - Past `canary_deadline` with unchanged pause file → file cleared, `EVENT_QUOTA_RESUMED` appended, dispatch proceeds; subsequent plans dispatch normally (file gone).
   - Canary re-pause: simulate phase-`classify` rewriting the file during the window → other plans stay gated against the NEW `paused_until`, no resume event.
   - Stuck pause (`paused_until: null`) → idle even days later; file removal → normal dispatch.
   - No quota.json → zero behavior change (regression guard on existing dispatch tests).
   - Corrupt quota.json → treated as absent, dispatch proceeds, stderr note.

2. **Implementation.**
   - `end_of_line/quota.py`: `gate_decision(...)` implementing the four-outcome machine under one `locked_json` window; clearing = `Path.unlink(missing_ok=True)` of quota.json after the window (or a cleared sentinel inside it — pick one, document in module docstring; unlink keeps "file absent == not paused" as the single invariant).
   - `end_of_line/supervisor.py`: gate call before `st.claim_phase`; on idle outcomes return the locked TickResult; on resume outcome append `EVENT_QUOTA_RESUMED` inside the already-open `st.mutate` window. Renumber the module docstring chain (insert "Project quota pause gate" between current 7 and 8).
   - Keep one-tick-one-action: the resume-and-dispatch tick is ONE action (dispatch) whose gate side-effect is the clear — matches how claim creation itself is a dispatch side-effect.

3. **Acceptance.**
   - Full suite green.
   - End-to-end test: quota death (classify machinery) → gate idles all plans → clock past reset → exactly one canary dispatch → clock past deadline → all plans dispatch. Assert at most ONE plan holds a fresh claim during the canary window.
   - Docstring chain in `supervisor.py` matches the implemented order.

4. **Commit + attest + complete.**
   - Log findings (e.g. unlink-vs-sentinel choice consequences) in the master's `## Findings log`.
   - Structured commit: `quota-pause: phase gate — quota dispatch gate + canary auto-resume (#94)`.
   - Stage explicit paths: `end_of_line/quota.py`, `end_of_line/supervisor.py`, `tests/test_quota.py`, `tests/test_supervisor.py` (+ master if findings logged).
   - After the commit: `clu verify --plan quota-pause --phase gate --token <T>`, `clu attest --simplify --plan quota-pause --phase gate --token <T>`.
   - `clu complete --plan quota-pause --phase gate --token <T>`.

## Failure modes to watch

- **Two plans racing for the canary slot** — both tick past `paused_until` in the same cron pass. The `locked_json` window makes stamping atomic: second reader sees the first's stamp and idles. Test with two sequential gate calls against one file.
- **Gate placement starving the DONE transition** — placing the gate before the phase loop would also block "all phases complete → done" (`supervisor.py:818-824`). The locked placement (immediately before `claim_phase`) avoids this; don't move it.
- **Clock source drift** — `paused_until` comparisons must use aware-UTC now (`st._now_utc` family), never naive `datetime.now()`; the quiet-hours code's naive-local pattern (`supervisor.py:32-34`) is the WRONG model for this.
- **Hot-path cost** — every healthy tick adds one `exists()` check; do not take the flock when the file is absent.
