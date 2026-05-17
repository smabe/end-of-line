# lease-claim-operator-control-init-knobs — `clu init` config knobs (#26)

You are phase `init-knobs` of the `lease-claim-operator-control` plan. Add three
init-time flags so the operator can pick lease/heartbeat/attempts values per-plan
at dispatch time, instead of editing `state.json` after the fact. The default
values (30-min lease, 10-min stall threshold, 3 attempts) are sized for short
ratchet phases; long-form plans (12h commit phases) need bigger defaults to
avoid lease-expiry spam.

## Locked decisions (do NOT re-litigate)

See `plans/lease-claim-operator-control.md`. Summary:

- **Three flags on `clu init`:** `--lease-ttl-minutes`, `--stalled-heartbeat-minutes`,
  `--max-attempts-per-phase`. All `type=int`, all reject `≤0` post-parse with
  `_die(ExitCode.INVALID_VALUE, ...)`.
- **Write site:** after `st.empty_state(...)` at cli.py:1046, override
  `data["config"]["<key>"]` for any non-None CLI value, before
  `st.save_atomic()` at cli.py:1049. Same `st.mutate` window.
- **Defaults unchanged.** Operator opts in via flags.
- **No schema bump.** Keys already exist in `empty_state()`
  (state.py:156-174) with `DEFAULT_*` constants (state.py:50-54).

## Read first

- `end_of_line/state.py:50-54` — `DEFAULT_LEASE_TTL_MIN = 30`,
  `DEFAULT_STALLED_HEARTBEAT_MIN = 10`, `DEFAULT_MAX_ATTEMPTS = 3`.
- `end_of_line/state.py:156-174` — `empty_state()` body; confirm the three
  keys are already populated there.
- `end_of_line/cli.py:219-255` — current `cmd_init` argparse setup. Look at
  the `--worktree` / `--branch` / `--base-ref` pattern (they're the recently
  shipped flags) for how new init flags get added.
- `end_of_line/cli.py:1031-1049` — current `cmd_init` body. The `--worktree`
  post-parse path at line 1031-1035 is your template for post-parse
  validation + write. The `st.empty_state(...)` call at ~1046 is where the
  defaults land; your overrides happen between that and `save_atomic` at
  ~1049.
- `tests/test_init.py` (or `tests/test_cli_validation.py` — check both) —
  existing flag tests for shape and assertion style.

## Produce

1. **Failing tests first.** Add to whichever test file owns `cmd_init` flag
   tests (likely `tests/test_init.py`):
   - `test_init_lease_ttl_flag_writes_override` — `main(["init", "--project",
     ..., "--plan", "foo", "--lease-ttl-minutes", "720"])`; load
     `state.json`, assert `data["config"]["lease_ttl_minutes"] == 720`.
   - `test_init_stalled_heartbeat_flag_writes_override` — same shape for
     `--stalled-heartbeat-minutes 60`.
   - `test_init_max_attempts_flag_writes_override` — same shape for
     `--max-attempts-per-phase 5`.
   - `test_init_lease_ttl_rejects_zero` — `main(["init", ..., "--lease-ttl-
     minutes", "0"])` exits with `INVALID_VALUE`.
   - `test_init_lease_ttl_rejects_negative` — same with `-30`.
   - `test_init_default_lease_ttl_when_flag_omitted` — no flag, default 30
     in state.
   Use `CluTestCase` (from the phase-1 `test-isolation-base` plan if it's
   shipped before this; if not, fall back to manual `isolate_registry` + env
   patches — check the test-isolation-base plan status before deciding).

2. **Implementation: argparse.**
   In `cmd_init` argparse setup (cli.py:219-255), after `--base-ref`:
   ```python
   p_init.add_argument(
       "--lease-ttl-minutes", type=int, default=None,
       help="Override default lease TTL (minutes). Default: 30.",
   )
   p_init.add_argument(
       "--stalled-heartbeat-minutes", type=int, default=None,
       help="Override stall threshold (minutes). Default: 10.",
   )
   p_init.add_argument(
       "--max-attempts-per-phase", type=int, default=None,
       help="Override max phase attempts. Default: 3.",
   )
   ```

3. **Implementation: post-parse validation + write.**
   In `cmd_init` body, before the `st.empty_state(...)` call, validate:
   ```python
   for attr, label in [
       ("lease_ttl_minutes", "--lease-ttl-minutes"),
       ("stalled_heartbeat_minutes", "--stalled-heartbeat-minutes"),
       ("max_attempts_per_phase", "--max-attempts-per-phase"),
   ]:
       val = getattr(args, attr, None)
       if val is not None and val <= 0:
           return _die(
               ExitCode.INVALID_VALUE,
               f"{label} must be a positive integer, got {val}",
           )
   ```
   After `st.empty_state(...)` populates defaults and BEFORE `save_atomic`:
   ```python
   for key in ("lease_ttl_minutes", "stalled_heartbeat_minutes",
               "max_attempts_per_phase"):
       val = getattr(args, key, None)
       if val is not None:
           data["config"][key] = val
   ```

4. **Acceptance.**
   - All 6 new tests green.
   - Full suite green at 538+6 = 544 tests (assuming phase 1 of test-isolation-
     base shipped first; if not, the baseline is 536+6 = 542).
   - Manual smoke: `clu init --project /tmp/foo --plan bar --lease-ttl-minutes
     720` followed by inspecting the written state.json shows `"config":
     {"lease_ttl_minutes": 720, ...}`.

5. **Commit + complete.**
   - Structured commit: `lease-claim-operator-control: phase init-knobs —
     three init config knobs (#26)`.
   - Stage explicit paths: `end_of_line/cli.py`, the test file.
   - `clu complete --plan lease-claim-operator-control --phase init-knobs
     --token <T>`.

## Failure modes to watch

- **`getattr(args, "lease_ttl_minutes", None)` typo** — argparse converts
  `--lease-ttl-minutes` to `args.lease_ttl_minutes` (dashes → underscores).
  Verify the attribute names match.
- **Validation in wrong order** — must validate BEFORE `st.empty_state(...)`
  to avoid creating a bogus state.json that then has to be cleaned up.
- **`ExitCode.INVALID_VALUE`** — confirm this enum value exists in
  `state.py` or wherever ExitCode lives. If not, use the nearest match
  (`INVALID_SLUG` is wrong; try `STATUS_TRANSITION` or whatever covers bad
  CLI input — grep `ExitCode` for the right one).
- **The `--worktree` flag wraps init in a try/rollback** — if you're
  inserting between the worktree setup and `save_atomic`, ensure failures
  here still trigger the rollback. Test by passing `--worktree` + bad init
  flag values; the worktree should not be left behind.
