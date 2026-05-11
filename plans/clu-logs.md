# clu-logs — Day 3 sub-plan #2

`clu logs <plan>` — tail the most recent worker log without the user
needing to know the token. Looks up `current_claim.log_path` first, then
falls back to the newest file in `.orchestrator/logs/`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| Implement | `clu-logs-impl.md` | Add `cmd_logs` to `cli.py` | 30m |
| Tests | `clu-logs-tests.md` | Unit + integration tests | 20m |
