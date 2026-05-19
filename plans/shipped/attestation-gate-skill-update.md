# attestation-gate-skill-update — clu-phase SKILL.md + docs update

You are phase `skill-update` of the `attestation-gate` plan. No code
changes — update the worker contract (`end_of_line/skill/SKILL.md`)
and docs (`docs/contract.md`, `docs/reference.md`) so workers and
future-readers know about the new callbacks and the refusal gate.

## Locked decisions (do NOT re-litigate)

See `plans/attestation-gate/attestation-gate.md`. Summary:

- New section in SKILL.md titled "Pre-complete callbacks" between
  "Step-by-step protocol" and "Quality mandates".
- Wording is firm: "If you call complete without these, clu refuses
  with `STATUS_TRANSITION` and your phase ticks attempts++."
- `docs/contract.md` documents the `attestations` schema and the
  `quality` config block.
- `docs/reference.md` adds CLI entries for `clu verify` and `clu attest`.

## Read first

- `end_of_line/skill/SKILL.md` (full file) — match tone, voice,
  command formatting.
- `end_of_line/skill/SKILL.md:118-138` — existing "Quality mandates"
  section; new callbacks are the programmatic enforcement of these.
- `docs/contract.md` — current state schema documentation. Find the
  `current_claim` section and extend with `attestations`.
- `docs/reference.md` — current CLI surface. Find where worker
  callbacks are documented (`clu complete`, `clu block`).

## Produce

1. **`end_of_line/skill/SKILL.md` update.**

   Add a new section, positioned between "Step-by-step protocol"
   (around line 117) and "Quality mandates" (around line 118):

   ```markdown
   ## Pre-complete callbacks (mandatory)

   Before calling `clu complete`, you MUST attest that the project's
   quality mandates passed. clu refuses `complete` with
   `STATUS_TRANSITION` if either stamp is missing or stale (i.e. a
   commit landed after the stamp).

   **Always** — re-run verification, then stamp:
   ```bash
   clu verify --project "$PROJECT_ROOT" --plan "$PLAN" \
       --phase "$PHASE" --token "$TOKEN"
   ```
   This runs `quality.verify_command` (or `test_command`) and stamps
   `attestations.verify` on rc=0. On rc!=0 the command fails — fix
   the breakage, commit, re-run `clu verify`.

   **If your diff exceeds threshold** (>1 file OR ~30 lines by
   default; per-project override in `.orchestrator.json:quality.simplify_threshold`)
   — run `/simplify`, then stamp:
   ```bash
   clu attest --simplify --project "$PROJECT_ROOT" --plan "$PLAN" \
       --phase "$PHASE" --token "$TOKEN"
   ```
   clu cannot run `/simplify` itself — it's a Claude-side review
   skill. The attestation is your word that you ran it.

   **Stamps go stale.** Each stamp records the HEAD SHA at attest-time.
   If you commit AFTER stamping, the stamp is stale and `clu complete`
   refuses. Order: do the work, run /simplify, commit, run tests,
   `clu verify`, `clu attest --simplify`, `clu complete`. If you
   need to commit a fix after stamping, re-stamp.

   **Skip flags exist but are operator-owned.** `clu complete
   --skip-verify` and `--skip-simplify` bypass each gate but emit
   audit events. Workers should not use these — if you think a phase
   legitimately needs a skip, `clu block` with the situation instead.
   ```

   Also update the existing "Quality mandates" section: the bullet
   on "Review after non-trivial diffs" gains a closing sentence:
   "Stamp via `clu attest --simplify` after running /simplify, or
   complete will refuse."

   And the bullet on "Re-run verification right before complete"
   gains: "`clu verify` does this for you AND stamps; running the
   test suite manually and skipping `clu verify` will still leave
   `complete` refused."

2. **`docs/contract.md` update.**

   Find the `current_claim` schema documentation. Add the
   `attestations` field:
   ```markdown
   - `attestations` (object, optional) — lazy-init map of quality
     attestations stamped during the claim. Each entry: `{at:
     ISO8601_Z, commit_sha: str}`. Stamp is "stale" if `commit_sha !=
     current HEAD`. Known kinds:
     - `verify` — set by `clu verify` on rc=0 of the configured
       verify command.
     - `simplify` — set by `clu attest --simplify` (worker
       self-attestation after running `/simplify`).
   ```

   Add the `quality` config block under `.orchestrator.json` schema:
   ```markdown
   ### `quality` (optional)
   - `verify_command` (string, optional) — command to run for
     `clu verify`. Falls back to top-level `test_command` if absent.
     Single-string + `shlex.split`; wrap multi-step verify in a
     script.
   - `simplify_threshold` (object, optional) — overrides the default
     `{files: 1, lines: 30}` threshold for the simplify gate.
     Format: `{files: int, lines: int}` — exceeding EITHER triggers
     the gate. Set both to 0 to gate on every phase.
   ```

   New events:
   ```markdown
   - `verify_stamped` — emitted by `clu verify` on success.
     Payload: `{phase, commit_sha}`.
   - `simplify_stamped` — emitted by `clu attest --simplify`.
     Payload: `{phase, commit_sha}`.
   - `operator_skip_verify` — emitted by `clu complete --skip-verify`.
     Audit event; phase still completes.
   - `operator_skip_simplify` — emitted by `clu complete
     --skip-simplify`. Audit event; phase still completes.
   ```

3. **`docs/reference.md` update.**

   Add to the CLI surface section (find where `clu complete` and
   `clu block` are documented):

   ```markdown
   ### `clu verify --project P --plan S --phase X [--token T]`
   Runs the project's `quality.verify_command` (or `test_command`).
   On rc=0 stamps `current_claim.attestations.verify` with current
   HEAD. On rc!=0 exits non-zero with stderr tail; no stamp.
   `--token` validates against the live claim when present (worker
   mode); operator omits it for manual re-verification.

   ### `clu attest --simplify --project P --plan S --phase X --token T`
   Worker self-attestation that `/simplify` ran on the current
   commit. Stamps `current_claim.attestations.simplify`. Token
   required. Extensible — future flavors (`--lint`, `--type-check`)
   slot in on the same command.

   ### `clu complete --skip-verify [--skip-simplify]`
   Bypass the quality gates on this complete. Each flag emits a
   `operator_skip_*` audit event. Operator-owned — workers should
   `clu block` instead.
   ```

4. **Acceptance.**
   - SKILL.md diff covers the new section + two bullet additions.
   - `docs/contract.md` diff covers attestations schema + quality
     config block + 4 new events.
   - `docs/reference.md` diff covers 3 CLI entries.
   - No tests change (this phase is docs-only). Full suite still
     green (sanity-check).
   - `grep -n 'attestation\|quality.verify_command\|simplify_threshold' docs/contract.md docs/reference.md end_of_line/skill/SKILL.md`
     returns ≥6 hits.

5. **Commit + complete.**
   - Title: `attestation-gate: phase skill-update — worker contract + docs for clu verify/attest (#55, closes #10, closes #55)`
   - Stage: `end_of_line/skill/SKILL.md`, `docs/contract.md`, `docs/reference.md`.
   - Diff is multi-file but only ~80 lines of markdown total — at
     threshold. Run `clu attest --simplify` to be safe (and dogfood
     the gate on the phase that wires it into the SKILL.md).
   - `clu complete --plan attestation-gate --phase skill-update --token <T>`.

## Failure modes to watch

- **Closes-on-PR vs closes-on-commit.** "closes #10" and "closes
  #55" in the commit message close the issues when the commit lands
  on `main` (or when the PR merges if it's a PR-based flow).
  Standard GH behavior — no special handling needed.
- **SKILL.md tone drift.** Match existing voice: terse, imperative,
  no hedging. Read the file end-to-end before drafting to absorb the
  voice.
- **Docs-only diff still gets a verify stamp.** The SKILL.md update
  is your phase deliverable; verify is the project's test suite. Run
  `clu verify` for the stamp.
- **Reference doc cross-links.** If `docs/reference.md` cross-links
  to `docs/contract.md` sections, add the matching anchors. Don't
  add new docs files; the existing files own this content.
