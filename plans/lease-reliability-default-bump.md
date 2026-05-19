# lease-reliability-default-bump — default TTL 30→60 + clu doctor warning (#58 part 3/3, closes #58)

You are phase `default-bump` of the `lease-reliability` plan. Final
phase. Bump the default lease TTL constant and add a `clu doctor`
warning that surfaces malformed Effort cells across registered plans.
Closes #58.

## Locked decisions (do NOT re-litigate)

See `plans/lease-reliability.md`. Summary:

- `DEFAULT_LEASE_TTL_MIN = 30 → 60` in `end_of_line/state.py:78`.
- `clu doctor` scans each registered plan's phases; emits a warning line listing `<plan>:<phase>` pairs where Effort is set but `parse_effort_minutes` returns `None`.
- `docs/conventions.md` gets a brief paragraph on `lease_ttl_scale` rationale.

## Read first

- `end_of_line/state.py:78` — `DEFAULT_LEASE_TTL_MIN`.
- `end_of_line/state.py:225-235` — the init-time config block; the literal value is referenced here too.
- `end_of_line/cli.py::cmd_doctor` — search `def cmd_doctor`. Read the existing report shape: section headers, warning vs. error lines. Mirror.
- `end_of_line/plan_parser.py` — `parse_effort_minutes` from `effort-parser` phase.
- `end_of_line/registry.py` — how plans are enumerated across the system. `cmd_doctor` iterates this; reuse.
- `docs/conventions.md` — find the existing structure; add the `lease_ttl_scale` paragraph in a logically-adjacent section (probably under "Locked config decisions" or similar).
- `tests/test_*doctor*` if any exist; otherwise `tests/test_init_config.py` for the constant-bump regression test.

## Produce

1. **Failing tests first.** Create `tests/test_lease_default.py`:
   - `test_default_lease_ttl_is_60`: import `DEFAULT_LEASE_TTL_MIN`; assert `== 60`.
   - `test_init_writes_60_when_no_override`: run `cmd_init` with default `.orchestrator.json`; assert `data["config"]["lease_ttl_minutes"] == 60`.
   - `test_existing_test_30_explicit_overrides_default`: tests that previously asserted `30` may still pass IF they set the global explicitly via `.orchestrator.json`. Audit existing tests during this phase — any test that hard-codes 30 as "the default" needs updating.

   Add to `tests/test_doctor.py` (create if missing) or extend the doctor test file:
   - `test_doctor_warns_on_malformed_effort`: register a plan with a Sessions row whose Effort is `"abc"`; run `cmd_doctor`; assert stdout contains a warning line referencing `<plan>:<phase>`.
   - `test_doctor_silent_when_effort_clean`: register a plan with all-clean Effort cells; run `cmd_doctor`; assert no Effort-warning lines.
   - `test_doctor_silent_when_effort_empty`: register a plan with empty Effort cells; assert no warning (empty ≠ malformed).

2. **Implementation in `end_of_line/state.py`:**
   - Change `DEFAULT_LEASE_TTL_MIN = 30` to `DEFAULT_LEASE_TTL_MIN = 60` (line 78).

3. **Implementation in `end_of_line/cli.py::cmd_doctor`:**
   - Find the doctor's iteration over registered plans. For each plan, read its master file, call `parse_sessions_index`, and for each phase call `parse_effort_minutes(phase.effort)`. If the result is `None` AND `phase.effort` is non-empty, add to a `malformed_efforts: list[tuple[str, str]]` list (`(plan_slug, phase_id)`).
   - After iteration, if the list is non-empty, emit:
     ```
     [warn] Malformed Effort cells (lease will fall back to default):
       <plan-1>:<phase-1>  Effort=<raw>
       <plan-2>:<phase-2>  Effort=<raw>
     ```
     Match the existing doctor output style (look for any other `[warn]` lines).
   - Plan-read failures (missing master file, parse errors) should be silently skipped — this is an advisory warning, not a hard check.

4. **Implementation in `docs/conventions.md`:**
   - Add a section (or extend an existing one) with ~5 lines:
     ```markdown
     ### Lease scale (`lease_ttl_scale`)

     `cmd_init` computes per-phase lease TTLs from the Effort column in
     each plan's Sessions index, scaled by `lease_ttl_scale` (default
     `0.5`). Default `0.5` means "trust Effort half-way" — a phase
     declared as 3h gets a 90min lease, with the global default as the
     floor. Raise to `1.0` if you trust your Effort estimates; never
     go below the global default. Malformed or missing Effort cells
     fall back to the global default and surface in `clu doctor`.
     ```

5. **Acceptance.**
   - All new tests pass.
   - Full suite green: `python3 -m unittest discover -s tests`.
   - **Audit existing tests:** `grep -rn "lease_ttl_minutes.*30\|DEFAULT_LEASE_TTL_MIN.*30\|== 30" tests/`. Any test that asserts `30` as the default needs to either update to `60` OR explicitly set the config to `30`. Update minimally — prefer setting the explicit override in test setup over changing assertions.
   - `clu doctor` on the current project shows no Effort warnings (all current plans have clean Effort cells).
   - `python3 -c "from end_of_line.state import DEFAULT_LEASE_TTL_MIN; print(DEFAULT_LEASE_TTL_MIN)"` → `60`.

6. **Commit + complete.**
   - Structured commit: `lease-reliability: phase default-bump — default TTL 30→60 + doctor warning (closes #58)`.
   - Stage explicit paths: `end_of_line/state.py`, `end_of_line/cli.py`, `docs/conventions.md`, `tests/test_lease_default.py`, plus any existing tests that need 30→60 updates.
   - `clu verify` + `clu attest --simplify` per the gate.
   - `clu complete --plan lease-reliability --phase default-bump --token <T>`.

## Failure modes to watch

- **Existing-test fallout from the default change.** This is the biggest risk in this phase. Some tests likely hard-code 30 as "the lease TTL after init". Audit BEFORE implementing the constant change so you can scope the touch surface in the commit. If the fallout is >5 files, consider whether the default should stay 30 and the bump happen via `.orchestrator.json` template — flag for operator review via `clu block` rather than churning many tests.
- **`clu doctor` performance on large registries.** Reading every plan's master file on every doctor invocation is O(N). N is small today; if it ever bites, cache the parse result by mtime. Not in scope now.
- **Empty Effort vs. malformed Effort.** `parse_effort_minutes("")` returns `None`; `parse_effort_minutes("abc")` also returns `None`. The doctor must distinguish — check `phase.effort.strip() != ""` before counting as malformed.
- **`docs/conventions.md` section placement.** Don't create a new top-level section if a logical home exists (e.g. under existing "Locked config decisions" or "Lease + claim lifecycle"). Match the doc's voice.
