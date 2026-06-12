# Resume — verify-gate-93 (briefed 2026-06-12, after quota-pause)

```
quota-pause shipped 2026-06-12 — merge d160f96 on main, #94 closed. Quota
worker deaths are now classified from the log tail, burn no attempt, pause
the project until the parsed reset, and canary-auto-resume (new
end_of_line/quota.py + dispatch gate at tick priority 8). Cleanup done:
plans auto-archived to plans/archive/quota-pause/ (5880206), memory record
written (project_quota_pause.md), follow-up filed as #95.

Next steps available (pick one or propose your own):
- #93: verify gate hardening — RECOMMENDED. Two scoped pieces: clu doctor
  dry-validates quality.verify_command (a broken one currently refuses every
  completion on the host, discovered only at first worker verify), and
  process-group kill on shell=True timeout in cmd_verify + the merge gate
  (both orphan the shell's children today). Mirrors the doctor printer
  family from the 06-10 session.
- #95: sandbox-aware test skips — the 42 known env failures inside hardened
  workers (test_webserver socket binds + killpg reap tests) should report
  as skips. Pairs well with #93 as a same-batch second plan: disjoint files
  (tests/ only vs cli/doctor), safe to run concurrently.
- #74: attestation-refused inbox gets diff context + verify history.
- #73: dry-merge-gate auto-enqueues the merge-resolve plan on dirty result.
- #71: clu-ship followups epic (browse before picking — may be stale).
- #92 stays parked (containers; unpark trigger documented in the issue).

Recommended next pickup: /clu-plan #93, optionally batched with #95.

Read first if continuing from this work:
- gh issue view 93 (and 95 if batching)
- ~/.claude/projects/-Users-smabe-projects-end-of-line/memory/project_quota_pause.md
  (quota machinery gotchas if touching supervisor/dispatch: quota.json
  absent == not paused; gate_decision must not reuse locked_json)
- ~/.claude/projects/-Users-smabe-projects-end-of-line/memory/project_basedpyright_drain.md
  (workers can't pip-install; verify worker filed-issue claims)
- docs/operations.md "Recovering from a quota pause" + "Hardened worker
  dispatch"

Notes: worker model pinned to claude-opus-4-8 in .orchestrator.json
(operator choice 2026-06-12, "stay on opus for now" — ask before reverting
to fable-5). Open questions or blockers: none.
```
