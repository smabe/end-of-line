# lease-reliability-ttl-storage — per-phase TTL storage + resolver (#58 part 2/3)

You are phase `ttl-storage` of the `lease-reliability` plan. Wire the
`parse_effort_minutes` helper (shipped in `effort-parser`) into
`cmd_init` so each registered phase carries its own `lease_ttl_minutes`,
and add the resolver that all claim sites use.

## Locked decisions (do NOT re-litigate)

See `plans/lease-reliability.md`. Summary:

- New config field: `lease_ttl_scale: float = 0.5` in `ProjectConfig` (`config.py`).
- `cmd_init` computes `per_phase_ttl = max(global_default, round(effort_minutes * scale))` per phase IF Effort parses; otherwise omit.
- Per-phase override stored on the phase record under key `lease_ttl_minutes`.
- Global `data["config"]["lease_ttl_minutes"]` unchanged; per-phase wins when present.
- New resolver `state.lease_ttl_for_phase(data, phase_id) -> int`: per-phase override → `data["config"]["lease_ttl_minutes"]` → `DEFAULT_LEASE_TTL_MIN`.
- All claim sites use the resolver. Today: supervisor dispatch at `supervisor.py` ~line 301.

## Read first

- `end_of_line/state.py:78` — `DEFAULT_LEASE_TTL_MIN`.
- `end_of_line/state.py:225-235` — the config block written at init time (look for `"lease_ttl_minutes": DEFAULT_LEASE_TTL_MIN`). This is the per-state global; you do NOT modify it.
- `end_of_line/state.py:362-393` — `claim_phase(data, phase_id, lease_minutes, ...)`. The caller (supervisor) passes `lease_minutes`; you'll change the caller to use the resolver.
- `end_of_line/cli.py::cmd_init` — search `def cmd_init` (around line 1337). Find where phases are registered into state (look for `parse_sessions_index` usage or the loop that builds the phases list). Per-phase TTL is computed here.
- `end_of_line/config.py:67-208` — `ProjectConfig` dataclass + parsing. Add `lease_ttl_scale: float = 0.5` to the dataclass and to the parsing logic that reads `.orchestrator.json`.
- `end_of_line/plan_parser.py` — `parse_effort_minutes` (shipped in `effort-parser`).
- `end_of_line/supervisor.py:301` — dispatch site. Look for `ttl = data["config"].get("lease_ttl_minutes", st.DEFAULT_LEASE_TTL_MIN)`. Replace with the resolver call.
- `tests/test_init_config.py` — existing init test patterns; mirror.

## Produce

1. **Failing tests first.** Extend `tests/test_init_config.py`:
   - `test_init_writes_per_phase_ttl_from_effort`: author a master plan with a Sessions index row whose Effort is `2h`; run `cmd_init`; assert the resulting phase record in state.json has `lease_ttl_minutes == 60` (default scale 0.5 × 120 = 60; max(global=30, 60) = 60). Wait — with `DEFAULT_LEASE_TTL_MIN=30` at this phase (#58 default-bump not yet shipped), use Effort `4h` → `120 = max(30, 0.5*240=120)`.
   - `test_init_omits_per_phase_ttl_when_effort_missing`: Sessions row with empty Effort cell; assert no `lease_ttl_minutes` key on the phase record.
   - `test_init_omits_per_phase_ttl_when_effort_malformed`: Sessions row with Effort `"abc"`; assert no `lease_ttl_minutes` key; assert init still succeeds (no exception).
   - `test_init_respects_lease_ttl_scale_override`: set `lease_ttl_scale: 1.0` in `.orchestrator.json`; Effort `2h`; assert per-phase TTL is 120.
   - `test_init_per_phase_floor_at_global_default`: Effort `0.5h` (30min × 0.5 scale = 15); assert per-phase TTL is `max(global_default, 15)` = global_default.

   New test file `tests/test_lease_ttl_resolver.py`:
   - `test_resolver_uses_per_phase_override`: state with per-phase `lease_ttl_minutes=90`; resolver returns 90.
   - `test_resolver_falls_back_to_global`: no per-phase; global config = 45; resolver returns 45.
   - `test_resolver_falls_back_to_default`: neither per-phase nor global; resolver returns `DEFAULT_LEASE_TTL_MIN`.

2. **Implementation in `end_of_line/config.py`:**
   - Add `lease_ttl_scale: float = 0.5` to `ProjectConfig`.
   - In the parsing function (search for `lease_ttl_minutes` or `stalled_heartbeat_minutes`), add `lease_ttl_scale=raw.get("lease_ttl_scale", 0.5)` with `float()` coercion + validation (`< 0` → fall back to default + warn).

3. **Implementation in `end_of_line/state.py`:**
   - Add `lease_ttl_for_phase(data: dict, phase_id: str) -> int` near `release_if_expired`:
     ```python
     def lease_ttl_for_phase(data: dict, phase_id: str) -> int:
         for ph in data.get("phases", []):
             if ph.get("id") == phase_id and "lease_ttl_minutes" in ph:
                 return int(ph["lease_ttl_minutes"])
         return int(data.get("config", {}).get("lease_ttl_minutes", DEFAULT_LEASE_TTL_MIN))
     ```
   - (Adjust to match the actual phase-record schema in state.json. Inspect existing init code to find whether phases are stored as a list-of-dicts or dict-of-dicts.)

4. **Implementation in `end_of_line/cli.py::cmd_init`:**
   - In the phase-registration loop, after parsing the Phase from `parse_sessions_index`:
     ```python
     from .plan_parser import parse_effort_minutes
     ...
     effort_minutes = parse_effort_minutes(phase.effort)
     if effort_minutes is not None:
         scale = cfg.lease_ttl_scale
         global_default = data["config"]["lease_ttl_minutes"]
         per_phase_ttl = max(global_default, round(effort_minutes * scale))
         phase_record["lease_ttl_minutes"] = per_phase_ttl
     ```
   - `phase_record` is whatever dict gets appended to `data["phases"]`. Inspect the existing loop to match the variable name.

5. **Implementation in `end_of_line/supervisor.py`:**
   - At ~line 301, replace `ttl = data["config"].get("lease_ttl_minutes", st.DEFAULT_LEASE_TTL_MIN)` with `ttl = st.lease_ttl_for_phase(data, phase_id)`. (`phase_id` is the phase being dispatched; locate it in the surrounding code.)

6. **Acceptance.**
   - All new tests pass.
   - Full suite green.
   - `grep -n lease_ttl_for_phase end_of_line/` shows the resolver + the supervisor callsite.
   - Manual smoke: register a fresh test plan with mixed Effort cells (some valid, some malformed, some empty); inspect state.json and verify per-phase keys appear where expected.

7. **Commit + complete.**
   - Structured commit: `lease-reliability: phase ttl-storage — per-phase TTL at cmd_init + resolver (#58)`.
   - Stage explicit paths: `end_of_line/config.py`, `end_of_line/state.py`, `end_of_line/cli.py`, `end_of_line/supervisor.py`, `tests/test_init_config.py`, `tests/test_lease_ttl_resolver.py`.
   - `clu verify` + `clu attest --simplify` per the gate.
   - `clu complete --plan lease-reliability --phase ttl-storage --token <T>`.

## Failure modes to watch

- **Phase-record schema mismatch.** Don't guess — read `cmd_init` first to see whether phases are list-of-dicts (with `"id"` key) or dict-of-dicts (keyed by id). The resolver code above assumes list-of-dicts; adjust to match.
- **Existing claim sites in `cli.py`.** If `claim_phase` is called from anywhere besides the supervisor (e.g. an operator manual-claim CLI), those sites also need the resolver. `grep -n claim_phase` to enumerate; update all.
- **Floor behavior is "max with global default", NOT `DEFAULT_LEASE_TTL_MIN`.** The operator may have lowered the global in `.orchestrator.json`; respect that. Use `data["config"]["lease_ttl_minutes"]` as the floor.
- **Don't change `claim_phase`'s signature.** It still takes a `lease_minutes: int` argument; the caller does the resolving. Keeping it caller-resolved means tests can pass arbitrary TTLs without state plumbing.
- **State migration not required.** Plans registered BEFORE this phase ships have no per-phase TTL key; the resolver naturally falls back. No backfill needed.
