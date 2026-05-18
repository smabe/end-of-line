# Worker token is the entire security boundary

Every worker callback (`complete / block / spawn / task-done /
heartbeat / queue add`) takes `--token`, validated against
`current_claim.claimed_by` and `--phase` via `state.assert_claim_match`.
Workers are spawned as `claude --print` subprocesses with shell access
to the project. The token check is the ONLY thing between a
well-behaved worker and a misbehaving one (or a malicious shell on the
same machine). Decorator `@_translate_claim_mismatch` exists so command
bodies can't accidentally swallow the exception. Skipping the check on
even one callback removes the boundary; do not propose "internal"
worker callbacks that bypass it.
