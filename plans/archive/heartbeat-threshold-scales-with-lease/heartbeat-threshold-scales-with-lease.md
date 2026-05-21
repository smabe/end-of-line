# heartbeat-threshold-scales-with-lease

## Goal
Replace the fixed 10-min `DEFAULT_STALLED_HEARTBEAT_MIN` with a per-claim
threshold derived from each phase's lease TTL, so Effort-scaled leases
(#58) and long tool-use chains stop triggering false-alarm `phase_stalled`
notifications. The `stalled_heartbeat_minutes` config knob remains an
operator override.

## Diagnosis
- **Hypothesis:** `supervisor._detect_stalled` (supervisor.py:212) and
  `fleet._project_row` (fleet.py:33) both read a flat
  `stalled_heartbeat_minutes` config key with a 10-min default
  (state.py:88). Workers in deep Bash/Task chains don't call
  `clu heartbeat` until they hit a top-level decision, so legitimate
  long-running phases trip the 10-min ceiling while the 60-min lease
  is still healthy. The user observed simplify-animation-helpers
  recovering with a fresh heartbeat after the stall warning — exactly
  the false-positive shape.
- **Falsifiable test:** with a 60-min lease and the current 10-min
  threshold, fast-forward `last_heartbeat_at` 12 min into the past
  and call `_detect_stalled` — it returns a `TickResult("stalled")`
  even though the lease has 48 min left.
- **Test result:** confirmed by reading the code path. tests/test_heartbeat.py:88-91
  asserts exactly this: `is_claim_stalled(claim, threshold_minutes=10) → True`
  at age 15 min. The behavior is intentional under the current design
  but is the source of the false alarms.

## Non-goals
- Don't change lease TTL behavior or the `_emit_stalled_claim_notify`
  lease-expiry path — that one fires correctly on real failure.
- Don't drop the `stalled_heartbeat_minutes` config knob — operators
  who tuned it stay tuned.
- Don't migrate existing state.json files. Old plans keep the value
  they were initialized with (10); only newly-initialized plans get
  the derived default.
- No new event types, no new notification kinds.
- Don't touch the `clu heartbeat` cadence in
  `end_of_line/skills/clu-phase/SKILL.md` (every 2 min) — only the
  doc lines that quote "10" as the threshold get refreshed.

## Files to touch
- `end_of_line/state.py` — add `stalled_threshold_for_phase(data, phase_id) -> int`
  that returns the explicit config override if set, else
  `max(15, lease_ttl_for_phase(data, phase_id) // 2)`. Stop writing
  `stalled_heartbeat_minutes` into `empty_state()` so absence means
  "derive". Keep `DEFAULT_STALLED_HEARTBEAT_MIN = 10` as a doc-only
  constant (used by tests + as the floor for derived values, see below).
- `end_of_line/supervisor.py` — `_detect_stalled` calls the new helper
  instead of reading config directly.
- `end_of_line/fleet.py` — `_project_row` calls the new helper for the
  per-claim stalled check (needs phase_id from the claim).
- `end_of_line/cli.py` — three more callsites move to the helper:
  `_project_state_status` (STATUS column projection, line ~2419),
  `_format_heartbeat` (label in `clu watch`/`clu status`, line ~3298),
  and `cmd_release_claim` (fresh-claim gate for `--force`, line ~3406).
  All three currently read `config["stalled_heartbeat_minutes"]` with
  the old default; each has a claim in hand so phase_id is available.
- `tests/test_heartbeat.py` — keep existing `is_claim_stalled` tests
  (pure-function, threshold-as-arg); add tests for the new helper:
  derive-from-lease, explicit-override-wins, per-phase-lease-override,
  15-min floor.
- `tests/test_release_claim.py` — line 104 comment ("default
  stalled_heartbeat_minutes is 10") needs updating to "derived from
  lease TTL".
- `tests/test_init_config.py` — confirm the existing explicit-set test
  still passes; add a test that fresh `empty_state()` does NOT write
  the key.
- `docs/architecture.md:104` — refresh the prose around the 10-min default.
- `docs/contract.md:72` — drop `stalled_heartbeat_minutes: 10` from the
  state schema example; note absence = derived.
- `docs/operations.md:799,1149` — refresh the "default 10m" mentions.
- `end_of_line/skills/clu-phase/SKILL.md:113,225` — the SKILL still
  recommends every-2-min heartbeats, but the prose that says "default
  `stalled_heartbeat_minutes: 10`" needs updating.

## Failure modes to anticipate
- **Operator explicitly set `stalled_heartbeat_minutes: 10`** — config
  treats `10` as just another int override, so the operator keeps the
  tight threshold they asked for. Only NEW plans (no explicit value)
  get the derived default.
- **15-min floor too low for very short Effort-scaled leases.** A 20-min
  lease would derive `max(15, 10) = 15` min — only 5 min of headroom
  before lease expiry. Acceptable: short leases mean short phases, and
  the floor matches the original 10-min ballpark. If this bites,
  bump the floor in a follow-up.
- **`fleet._project_row` needs phase_id**, which it doesn't currently
  extract. The claim dict has `phase_id` — easy fetch, but worth
  asserting in the test.
- **Existing state.json files in the wild** still have
  `stalled_heartbeat_minutes: 10` baked in by old `empty_state()`.
  Those plans keep the 10-min behavior forever (until operator clears
  it). Document this in the plan ship message so operator knows to
  edit state.json if they want the new default. Don't auto-migrate.
- **`DEFAULT_STALLED_HEARTBEAT_MIN` as a name** is misleading after
  this change — it's no longer the default, it's the floor. Rename to
  `STALLED_HEARTBEAT_MIN_FLOOR` for clarity. Touches the constant +
  three callsites (state.py imports in supervisor.py, fleet.py,
  cli.py).
- **Race between heartbeat cadence and derived threshold.** Worker
  heartbeats every 2 min; derived threshold for a 60-min lease is 30
  min. Plenty of margin. Worker heartbeats every 2 min vs derived 15
  for a 30-min lease is also fine.
- **`cmd_release_claim --force` gate gets looser by default.** Today
  a claim is "fresh" only if its heartbeat is < 10 min old; with the
  derived default it's < 30 min old (60-min lease). That means
  `clu release-claim` will refuse without `--force` for more claims
  than before. This is consistent — the threshold should mean the
  same thing everywhere — but mention it in the commit body so
  operators know.

## Done criteria
- `state.stalled_threshold_for_phase(data, phase_id)` exists and is the
  single resolution point: explicit config override > derived
  `max(STALLED_HEARTBEAT_MIN_FLOOR, lease_ttl_for_phase // 2)`.
- `supervisor._detect_stalled`, `fleet._project_row`,
  `cli._project_state_status`, `cli._format_heartbeat`, and
  `cli.cmd_release_claim` all call the helper; the old
  `data["config"].get("stalled_heartbeat_minutes", ...)` pattern is
  gone from every callsite.
- `empty_state()` no longer writes `stalled_heartbeat_minutes`.
- Full suite green (1225 → 1225+N where N covers the new helper).
- Architecture / contract / operations / clu-phase SKILL docs updated.
- One commit, structured message with the usual trailer.

## Parking lot
(empty)
