# lease-claim-operator-control-extend-lease — `clu extend-lease` (#29)

You are phase `extend-lease` of the `lease-claim-operator-control` plan. Add an
operator command to bump a live claim's `lease_expires` without state-file
hand-editing. Phase 1 (`init-knobs`) lets the operator pick bigger defaults at
init time; this phase lets them adjust mid-flight when they realize scope is
larger than the dispatch-time config.

## Locked decisions (do NOT re-litigate)

See `plans/lease-claim-operator-control.md`. Summary:

- **Operator-only command, NO `--token`.** Matches `cmd_release_claim` and
  `cmd_answer`.
- **Argparse:** `add_common(p)` for `--project`/`--plan`, plus positional
  `minutes` (int, reject ≤0).
- **Semantics:** `new_expires = max(now, current_lease_expires) +
  timedelta(minutes=args.minutes)`. The `max(now, ...)` handles past-lease
  (stalled) claims so we never extend backwards.
- **Refusal:** no `current_claim` → `_die` with clear stderr.
- **New event:** `EVENT_LEASE_EXTENDED = "lease_extended"` in state.py
  EVENT_* block. Fields: `phase`, `extended_by_minutes`, `new_expires`,
  `operator: True`.
- **Lease arithmetic:** `datetime.fromisoformat` → `+ timedelta` →
  `.isoformat()`. Match the format from claim creation (state.py:321).

## Read first

- `end_of_line/state.py:76-128` — EVENT_* constants block. You'll add
  `EVENT_LEASE_EXTENDED` here, matching the existing pattern.
- `end_of_line/state.py:318-327` — claim creation, especially line 321 where
  `lease_expires` is written. Note the exact ISO format
  (`datetime.utcnow().isoformat() + "Z"`? Or `datetime.now(timezone.utc).
  isoformat()`? — match exactly).
- `end_of_line/cli.py:2720-2748` — `cmd_release_claim` body. Use this as
  your shape template for an operator-only command that mutates
  `current_claim` under `st.mutate`.
- `end_of_line/cli.py` (around line 530-545 area for `add_common` users +
  argparse setup for other operator commands like `release-claim`,
  `answer`) — find the canonical pattern for registering a new operator
  subcommand.
- `end_of_line/cli.py` top-level dispatcher (lines 651-683) — where new
  subcommands get wired into the main `if/elif` chain.
- The `_die(ExitCode.X, ...)` pattern — there's likely an `ExitCode.NO_CLAIM`
  or similar; grep `ExitCode` for the right value for "tried to operate on
  a plan with no current_claim". If none exists, `STATUS_TRANSITION` is
  the closest existing match.

## Produce

1. **Failing tests first.** New `tests/test_extend_lease.py`:
   - `test_extend_lease_happy_path` — create a state with a running claim,
     run `clu extend-lease --project ... --plan ... 60`, reload state,
     assert `current_claim["lease_expires"]` is bumped by 60 minutes from
     the old value, assert `EVENT_LEASE_EXTENDED` appended with correct
     fields.
   - `test_extend_lease_refuses_when_no_claim` — state with `current_claim
     = None`, run extend-lease, assert exit code matches the refusal
     enum, assert stderr mentions "no claim".
   - `test_extend_lease_refuses_on_zero_minutes` — argparse validation
     path; exit `INVALID_VALUE`.
   - `test_extend_lease_refuses_on_negative_minutes` — same.
   - `test_extend_lease_from_past_lease` — create state where
     `lease_expires` is in the PAST (stalled claim, before lease-expiry
     detector ran), run extend-lease 30, assert new expires is roughly
     `now + 30 min` (NOT `past_lease + 30 min`).
   Use `CluTestCase` if test-isolation-base shipped; otherwise manual
   isolation.

2. **Implementation: event constant.**
   `end_of_line/state.py` (in the EVENT_* block ~76-128): add
   `EVENT_LEASE_EXTENDED = "lease_extended"`. Sort it into the block in
   the same style as the existing constants.

3. **Implementation: argparse.**
   `end_of_line/cli.py` (near other operator command argparse — release-claim,
   answer, pause):
   ```python
   p_extend_lease = sub.add_parser(
       "extend-lease", help="Extend a running claim's lease by N minutes",
   )
   add_common(p_extend_lease)
   p_extend_lease.add_argument(
       "minutes", type=int, help="Minutes to add to the current lease expiry",
   )
   ```

4. **Implementation: `cmd_extend_lease`.**
   ```python
   def cmd_extend_lease(args, cfg: ProjectConfig, state_path: Path) -> int:
       if args.minutes <= 0:
           return _die(
               ExitCode.INVALID_VALUE,
               f"minutes must be positive, got {args.minutes}",
           )
       with st.mutate(state_path) as data:
           claim = data.get("current_claim")
           if claim is None:
               return _die(
                   ExitCode.NO_CLAIM,  # or STATUS_TRANSITION if NO_CLAIM doesn't exist
                   f"no current claim on {args.plan} to extend",
               )
           current = datetime.fromisoformat(claim["lease_expires"].rstrip("Z"))
           if current.tzinfo is None:
               current = current.replace(tzinfo=timezone.utc)
           now = datetime.now(timezone.utc)
           baseline = max(current, now)
           new_expires = (baseline + timedelta(minutes=args.minutes)).isoformat()
           claim["lease_expires"] = new_expires
           st.append_event(
               data, st.EVENT_LEASE_EXTENDED,
               phase=claim["phase_id"],
               extended_by_minutes=args.minutes,
               new_expires=new_expires,
               operator=True,
           )
       print(
           f"Extended {args.plan}/{claim['phase_id']} lease by "
           f"{args.minutes} min → {new_expires}"
       )
       return ExitCode.OK
   ```
   Adjust ISO parsing to match the exact format used at state.py:321 — if it
   appends `"Z"` literally, strip before `fromisoformat`; if it uses
   `datetime.now(timezone.utc).isoformat()`, `fromisoformat` handles the
   offset directly.

5. **Implementation: dispatcher wire-up.**
   In the top-level dispatcher (cli.py:651-683), add a route for `args.cmd
   == "extend-lease"` → `cmd_extend_lease(args, cfg, state_path)`. Use
   the same `cfg`/`state_path` resolution as other plan-specific commands.

6. **Acceptance.**
   - All 5 new tests green.
   - Full suite green.
   - Manual smoke: `clu extend-lease --project . --plan some-plan 30` on a
     plan with a running claim writes the expected event and updates
     `lease_expires`.
   - `EVENT_LEASE_EXTENDED` not routed to iMessage (check `notify.py` —
     most events skip notification; verify this one does too).

7. **Commit + complete.**
   - Structured commit: `lease-claim-operator-control: phase extend-lease —
     clu extend-lease (#29)`.
   - Stage: `end_of_line/state.py`, `end_of_line/cli.py`,
     `tests/test_extend_lease.py`.
   - `clu complete --plan lease-claim-operator-control --phase extend-lease
     --token <T>`.

## Failure modes to watch

- **ISO timezone handling** — Python's `datetime.fromisoformat` is strict.
  Test round-tripping a real `lease_expires` from a fresh claim to be sure
  your parse + format matches.
- **`max(current, now)` with naive vs aware datetimes** — both must be
  timezone-aware OR both naive; mixing raises. The code above forces UTC
  awareness on both.
- **`add_common(p)` mutates the parser** — verify positional `minutes`
  doesn't collide with any common args (it shouldn't; common adds flags).
- **Event ordering** — `st.append_event` must happen BEFORE the `mutate`
  exits, inside the `with` block. The example above is correct.
- **`ExitCode.NO_CLAIM` may not exist** — fall back to `STATUS_TRANSITION`
  or whatever existing code uses for "operation requires a claim that isn't
  there". Check `cmd_release_claim`'s no-claim branch (cli.py:2724-2726)
  for the canonical exit.
- **Future plumbing of operator-friendly defaults** — if phase 1
  (`init-knobs`) is already shipped, the operator might pass `--lease-ttl-
  minutes 720` at init and then never need extend-lease. That's fine —
  extend-lease is for the case where they didn't anticipate the scope at
  init time.
