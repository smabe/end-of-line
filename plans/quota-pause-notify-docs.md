# quota-pause-notify-docs — quota notification kinds + docs (closes #94)

You are phase `notify-docs` of the `quota-pause` plan. You give the quota machinery its operator-facing surface: three notification kinds with renders, quiet-hours placement, and the four docs updates. Final phase — your commit closes #94.

## Locked decisions (do NOT re-litigate)

See `plans/quota-pause.md`. Summary:

- `KIND_QUOTA_PAUSED` and `KIND_QUOTA_RESUMED` defer during quiet hours (auto-resume means no overnight action is needed; inbox/watch surface the events regardless). `KIND_QUOTA_STUCK` joins `QUIET_HOURS_BYPASS_KINDS` (`notify.py:73-79`) — frozen fleet with no horizon is halt-equivalent.
- Renders carry the human-relevant facts: plan that triggered, signature line, and for PAUSED the local reset time + "auto-resumes ~2min after"; for STUCK the escape hatch (`rm plans/.orchestrator/quota.json`).
- Wiring: pause-with-reset → KIND_QUOTA_PAUSED; pause-without-reset → KIND_QUOTA_STUCK; gate resume → KIND_QUOTA_RESUMED. Notification call sites live where the events are appended (supervisor death blocks, dispatch fast-fail, gate resume) — read how existing TickResult.notify_body / direct `notify.notify` calls split responsibility before choosing per-site.
- Docs are part of the phase, not optional: contract.md (3 events + quota.json schema), architecture.md (tick chain incl. the gate), reference.md (quota module public surface), operations.md (quota-pause recovery runbook).

## Read first

- `plans/quota-pause.md` `## Findings log` — all three prior phases.
- `end_of_line/notify.py:40-100` — KIND constants, bypass set, render function patterns (mirror naming `render_*`).
- `end_of_line/supervisor.py` + `end_of_line/dispatch.py` quota call sites as shipped by phases `classify`/`gate` — where notify hooks in (TickResult.notify_body vs direct notify; follow whichever pattern each site already uses for its non-quota siblings).
- `docs/_outline.md` — the structural contract for which doc owns what; then the four target docs' relevant sections.
- `tests/test_notify*.py` — render/kind test patterns.

## Produce

1. **Failing tests first.**
   - Render tests: each render includes plan slug + reset time (PAUSED), escape hatch path (STUCK).
   - Quiet-hours placement: KIND_QUOTA_STUCK in the bypass set; PAUSED/RESUMED not.
   - Wiring tests: quota death with parseable reset emits a PAUSED notification; stuck path emits STUCK; gate resume emits RESUMED (extend the phase-`classify`/`gate` test scenarios rather than rebuilding fixtures).

2. **Implementation.**
   - `end_of_line/notify.py`: three KINDs, bypass-set addition, three renders.
   - Call-site wiring in `supervisor.py` / `dispatch.py` (replacing the phase-`classify` notify suppression with the real kinds).

3. **Docs.**
   - `docs/contract.md`: `EVENT_QUOTA_DEATH` / `EVENT_QUOTA_PAUSED` / `EVENT_QUOTA_RESUMED` with kwargs; quota.json schema + "file absent == not paused" invariant; attempts_for_phase forgiveness note.
   - `docs/architecture.md`: tick priority chain updated with the gate; canary-resume flow paragraph.
   - `docs/reference.md`: `quota` module public surface + invariants.
   - `docs/operations.md`: runbook section — what a quota pause looks like, the auto-resume timeline, the stuck-pause escape hatch, and the pre-#94 manual recovery it replaces.

4. **Acceptance.**
   - Full suite green.
   - Grep sweep: no leftover "suppressed notify" placeholder from phase `classify`; no stale vocabulary (e.g. "signature A/B" — the shipped split is parseability-based).
   - Docs mention `quota.json` in exactly the four owned places per `_outline.md` boundaries.

5. **Commit + attest + complete.**
   - Log findings if any.
   - Structured commit: `quota-pause: phase notify-docs — KIND_QUOTA_* + docs (closes #94)`.
   - Stage explicit paths: `end_of_line/notify.py`, `end_of_line/supervisor.py`, `end_of_line/dispatch.py`, the four docs, test files (+ master if findings logged).
   - After the commit: `clu verify --plan quota-pause --phase notify-docs --token <T>`, `clu attest --simplify --plan quota-pause --phase notify-docs --token <T>`.
   - `clu complete --plan quota-pause --phase notify-docs --token <T>`.

## Failure modes to watch

- **Notify spam on re-pause loops** — a canary that re-pauses every window would re-notify each cycle. Acceptable for PAUSED (it defers in quiet hours and each carries a new reset time), but verify the render includes the reset time so repeated pings are distinguishable; do NOT build a dedup layer (no second caller justifies it).
- **Quiet-hours interaction with overnight resume** — RESUMED firing at 04:00 defers to 08:00; the inbox event still lands immediately. That's the locked design; don't "fix" it by adding RESUMED to the bypass set.
- **Doc drift** — operations.md still describes the manual `clu retry` sweep as the only recovery; rewrite that paragraph to point at the auto-resume, keeping the manual sweep as the stuck-pause fallback.
