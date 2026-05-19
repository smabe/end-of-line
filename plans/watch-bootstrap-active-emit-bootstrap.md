# watch-bootstrap-active-emit-bootstrap — bootstrap reconciliation for active phase (closes #62)

You are phase `emit-bootstrap` of the `watch-bootstrap-active` plan.
Single-phase plan. Closes #62.

Extend `clu watch --task-list` so that when a consumer connects to a
plan with an in-flight phase, the bootstrap output reconciles that
phase to `in_progress` via TASK_UPDATE lines — instead of leaving the
consumer's UI showing all phases as pending until the next state
transition.

## Locked decisions (do NOT re-litigate)

See `plans/watch-bootstrap-active.md`. Summary:

- Site: `end_of_line/watch.py`, the `--task-list` mode's bootstrap function (where TASK_CREATE batch is emitted).
- After TASK_CREATE batch, before `[snapshot]` line, check `data.get("current_claim")`. If present, emit two TASK_UPDATE lines.
- Plan line: `TASK_UPDATE task=<slug> status=in_progress msg="bootstrap: plan running"` (parent line has NO `parent=` field).
- Phase line: `TASK_UPDATE task=<slug>/<phase> parent=<slug> status=in_progress msg="bootstrap: already active"`.
- Line shape exactly mirrors runtime TASK_UPDATE — consumer can't tell bootstrap from runtime.
- Read-only: bootstrap doesn't mutate state.json.

## Read first

- `end_of_line/watch.py` — the entire file. Find the `--task-list` bootstrap function (search `TASK_CREATE` to locate the batch-emit site).
- `end_of_line/cli.py::cmd_watch` — the caller that drives watch in `--task-list` mode. Understand the entry point but don't modify.
- `end_of_line/state.py` — search for `current_claim` schema; understand that `current_claim["phase_id"]` is the field carrying the active phase id.
- Existing watch tests in `tests/` — search `test_clu_watch*` or grep for `TASK_CREATE` in tests. Mirror the AAA shape.

## Produce

1. **Failing tests first.** Extend the watch test file with:
   - `test_bootstrap_emits_task_update_when_phase_active`: build a state.json with `current_claim={"phase_id": "p1", ...}`. Run the bootstrap function. Assert output contains both `TASK_UPDATE task=<slug> ... status=in_progress` AND `TASK_UPDATE task=<slug>/p1 parent=<slug> status=in_progress`.
   - `test_bootstrap_no_task_update_when_no_claim`: state.json with `current_claim=None` (or absent). Assert output contains NO `TASK_UPDATE` lines, only `TASK_CREATE` lines + snapshot.
   - `test_bootstrap_ordering`: state with active claim. Assert sequence is: all TASK_CREATE lines → TASK_UPDATE plan line → TASK_UPDATE phase line → snapshot line. Use index-of comparisons on the output, not exact line-by-line — order is the contract; whitespace and additional lines should be tolerated.
   - `test_bootstrap_skips_task_update_for_blocked_or_paused`: state with `current_claim` present but `status` is `blocked` or `paused` (not `running`). Document and implement: per the issue's "out of scope", only emit when status is `running`. (If you decide to be permissive and emit whenever current_claim exists, document that in the test name + the test asserts the permissive shape — pick one.)

2. **Implementation in `end_of_line/watch.py`:**
   - Locate the bootstrap function (where the TASK_CREATE batch is written to stdout).
   - After the batch loop, before the `[snapshot]` line emission:
     ```python
     claim = data.get("current_claim")
     if claim and data.get("status") == "running":
         slug = data["plan_slug"]
         print(f'TASK_UPDATE task={slug} status=in_progress msg="bootstrap: plan running"')
         print(f'TASK_UPDATE task={slug}/{claim["phase_id"]} parent={slug} status=in_progress msg="bootstrap: already active"')
     ```
   - (Adjust to match the actual emit pattern in the file — may be a helper, may inline `sys.stdout.write`, etc.)

3. **Acceptance.**
   - 3-4 new tests pass.
   - Full suite green: `python3 -m unittest discover -s tests`.
   - Manual smoke: `clu init` + `clu queue add` on a tiny test plan, wait for dispatch, then `clu watch --task-list --plan <slug>` and verify the active phase's TASK_UPDATE appears on the second batch of output (after TASK_CREATE, before `[snapshot]`).
   - `grep -n "bootstrap:" end_of_line/watch.py` shows the two new emissions.

4. **Commit + complete.**
   - Structured commit: `watch-bootstrap-active: phase emit-bootstrap — TASK_UPDATE for active phase at connect (closes #62)`.
   - Stage explicit paths: `end_of_line/watch.py`, the watch test file.
   - `clu verify --plan watch-bootstrap-active --phase emit-bootstrap --token <T>`.
   - `clu attest --simplify --plan watch-bootstrap-active --phase emit-bootstrap --token <T>` (skip if diff is single-file ≤30 lines).
   - `clu complete --plan watch-bootstrap-active --phase emit-bootstrap --token <T>`.

## Failure modes to watch

- **Bootstrap might be a `yield`-based generator, not a print loop.** Read carefully — if it yields lines for a runner to print, emit your TASK_UPDATE lines as yields, not as direct stdout writes.
- **`data["status"]` may not exist on older state files.** Use `.get("status")` — missing → don't emit. This is consistent with the issue's out-of-scope note (only emit when `running`).
- **Don't drop the `[snapshot]` line.** It's operator-context but the existing skill still tells humans to read it; preserve.
- **The `parent=<slug>` field is present on phase lines but absent on the plan line.** Mirror the existing `TASK_CREATE` pattern exactly — the consumer's parser keys off this.
- **Quoting the `msg` value.** Existing TASK_UPDATE lines use `msg="..."` with double quotes. Match exactly; don't substitute single quotes.
