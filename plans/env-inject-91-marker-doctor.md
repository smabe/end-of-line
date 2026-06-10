# env-inject-91-marker-doctor — doctor warning for unbounded plan-slug in dispatch.command (#83)

You are phase `marker-doctor` of the `env-inject-91` plan. You deliver, as one
commit: a `clu doctor` printer that warns when `dispatch.command` cannot
surface the plan slug as a bounded token in the rendered worker command line —
the condition that makes `claim_worker_alive` / `reap_orphan_pgroup` PID-reuse
guards unreliable (#83).

NOTE: you are running under the hardened #90 dispatch. If a command you
legitimately need is denied, `clu block` with the specifics.

## Locked decisions (do NOT re-litigate)

See `plans/env-inject-91.md`. Summary:

- New `_print_dispatch_marker_health(cfg)` in `end_of_line/cli.py`, inserted
  in cmd_doctor's chain adjacent to `_print_dispatch_permission_health`
  (cli.py:2672). Mirror that printer's exact shape (cli.py:2708-2731): quiet
  when clean; on findings print a one-line diagnosis per finding + a
  remediation pointer (docs/operations.md "Hardened worker dispatch").
- **Check = sentinel render + production matcher.** Render
  `cfg.dispatch.command` with a sentinel slug (pick something boundary-prone,
  e.g. `probeslug0`) through the same placeholder set as `dispatch.py:194-201`
  ({plan_slug},{phase_id},{token},{project},{state_file},{session_id}), then
  warn iff `state._cmdline_marker_present(rendered, sentinel)` is False. This
  catches missing `{plan_slug}` AND unbounded embeddings (`x{plan_slug}y`).
  shlex.quote in the render is fine — quotes are non-slug boundary chars.
- **Unparseable templates are a quiet skip, not a warning** — `.format` on a
  template with unknown/missing placeholders must not crash doctor: catch
  KeyError/IndexError/ValueError and return silently (same tolerance class as
  `resolved_model`, dispatch.py:95-111). Empty command: quiet skip.
- **`repair_command` excluded**: marker checks only run against phase-worker
  claims (`cmdline_match = data["plan_slug"]` at supervisor.py:620, 642-646,
  665); repair workers carry no claim. State this rationale in the printer
  docstring.

## Read first

- `plans/env-inject-91.md` `## Findings log` — REQUIRED: phase inject may
  have logged hardened-dispatch friction or seam changes.
- `end_of_line/cli.py:2708-2731` — `_print_dispatch_permission_health`, your
  template.
- `end_of_line/state.py:288-300` — `_SLUG_CHAR` + `_cmdline_marker_present`
  (import it; do not duplicate the regex).
- `end_of_line/dispatch.py:194-201` — the placeholder set to mirror in the
  sentinel render.
- `tests/test_doctor.py:365-405` — permission-health tests; mirror their
  stdout-capture + cfg-construction pattern.

## Produce

1. **Failing tests first** (`tests/test_doctor.py`):
   - command WITH `'/clu-phase {plan_slug} ...'` (the shipped recipe shape) →
     no output.
   - command missing `{plan_slug}` entirely → warning naming dispatch.command.
   - command embedding it unbounded (`--tag=x{plan_slug}y`) → warning.
   - `{plan_slug}` present but template also has an unknown placeholder
     (`{bogus}`) → quiet skip, no crash.
   - empty command → quiet.
   - repair_command missing the slug while command has it → still quiet
     (exclusion pinned by test).

2. **Implementation.**
   - `end_of_line/cli.py`: `_print_dispatch_marker_health` + chain insertion
     right after `_print_dispatch_permission_health`.
   - `docs/reference.md`: doctor section line for the new printer.
   - `docs/operations.md`: one guard-rails bullet in "Hardened worker
     dispatch": `{plan_slug}` in dispatch.command is load-bearing for
     PID-reuse liveness checks; doctor warns when it's absent/unbounded.

3. **Acceptance.**
   - All new tests green; full suite green.
   - Live check: `clu doctor --project <canonical root>` against this repo's
     real config → no marker warning (recipe carries `{plan_slug}`); a
     synthetic cfg without it → warning (cover via the unit tests' captured
     stdout; no need to mutate the real config).

4. **Commit + attest + complete.**
   - Findings: log anything the operator needs for the #90 dogfood
     confirmation (this plan completing clean IS that evidence).
   - Structured commit: `env-inject-91: phase marker-doctor — doctor warning
     for unbounded plan-slug in dispatch.command (closes #83)`.
   - Stage explicit paths: `end_of_line/cli.py`, `docs/reference.md`,
     `docs/operations.md`, `tests/test_doctor.py` (+ master if findings
     logged).
   - After the commit:
     - `clu verify --plan env-inject-91 --phase marker-doctor --token <T>`
     - `clu attest --simplify --plan env-inject-91 --phase marker-doctor --token <T>`
   - `clu complete --plan env-inject-91 --phase marker-doctor --token <T>`.
   - Completion summary MUST note: both #91 and #83 are closed by this plan's
     commits; remind the operator that confirming this plan ran clean under
     hardened dispatch is the trigger to close #90 and to re-run
     `clu install-skill --only clu-phase --force` for the phase-1 SKILL.md
     edit.

## Failure modes to watch

- **Don't hand-roll a boundary regex** — import `_cmdline_marker_present`.
  Two regexes drift; the doctor check must agree with the production matcher
  byte-for-byte.
- **Doctor noise discipline** — quiet when clean; an always-printing health
  line breaks the doctor contract.
- **Sentinel choice** — must match `^[a-z0-9][a-z0-9_-]{0,63}$` and not
  collide with literal text plausibly present in commands (avoid `clu`,
  `claude`, `plan`).
- **You are the #90 dogfood, phase 2 of 2** — clean completion closes the
  loop; if anything in the hardened stack misbehaves, log it in the findings
  before completing.
