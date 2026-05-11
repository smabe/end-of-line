# clu-selftest — phase-runner skill smoke test

Single trivial phase whose worker scope is "do nothing, just call `clu
complete`." Validates that `claude --print '/clu-phase ...'` reaches the
skill, parses arguments, and honors the callback contract without any
real code work. Cheap, deterministic, no risk of scope creep.

If this phase doesn't move from running → done within ~2 minutes after a
`clu tick --dispatch`, something is broken in the phase-runner skill or
the dispatch wiring and the multi-plan setup should NOT be cron-driven
until it's fixed.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| Noop | `clu-selftest-noop.md` | Worker calls clu complete with no commits. | 1m |
