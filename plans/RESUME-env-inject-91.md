# Resume — env-inject-91 (briefed 2026-06-10, after harden-worker-dispatch)

```
harden-worker-dispatch is shipped — merge on main (ship commits 813db38..7f20768,
auto-archive ca86407), no deploy step (CLI project). Cleanup pass done: plan
files auto-archived to plans/archive/harden-worker-dispatch/, memory rewritten
as a shipped record (+ >14d index prune to memory/archive/), follow-up filed as
#91. Workers now dispatch hardened: dontAsk + comma-joined allowlist + Seatbelt
sandbox (settings at ~/.config/clu/worker-settings.json), Fable 5 pinned.

Next steps available (pick one or propose your own):
- #91: headless workers — dispatcher-side CLU_* env injection — RECOMMENDED.
  Small, well-scoped (build_worker_env + SKILL.md step 2b + doctor marker), and
  queueing it as a clu plan makes it the #90 dogfood run for free.
- #90: stays open by design — close it (with a confirming comment) once the
  first hardened-dispatch plan completes clean. If that plan is #91, do both.
- [pending] Parked containers issue (layer 3): drafted but NOT filed — the gh
  keyring token expired mid-session (gh auth status looks fine but API calls
  401). Run `gh auth refresh -h github.com` first, then file from the draft in
  the harden-worker-dispatch session transcript / re-draft from #90's AC.
- HealthData migration: deliberately NOT tracked here — operator decided it's
  HealthData-side work, after the dogfood confirms.

Recommended next pickup: /clu-plan #91 (env injection), queue it, watch it run
under the hardened dispatch, then close #90 with the result.

Read first if continuing from this work:
- ~/.claude/projects/-Users-smabe-projects-end-of-line/memory/project_harden_worker_dispatch.md
- gh issue view 91 (after gh auth refresh)
- docs/operations.md "Hardened worker dispatch" (the recipe #91's worker will run under)

Open questions or blockers:
- gh CLI 401 (expired keyring token) — blocks issue filing/closing until
  `gh auth refresh`.
- .orchestrator.json.pre-90.bak at repo root is the worker's pre-swap config
  backup — delete once the dogfood run confirms the hardened dispatch.
```
