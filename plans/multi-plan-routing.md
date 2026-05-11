# multi-plan-routing — Day 3 sub-plan #1

Last-pinged plan routing for ambiguous bare-digit replies in the iMessage
inbound poller. When multiple plans have open blockers, a bare `1` reply
should resolve to whichever plan most-recently sent a question.

(Fleet-test stand-in: real implementation will land after the fake-worker
smoke test proves the plumbing.)

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| Pick strategy (blocker) | `multi-plan-routing-design-block.md` | Pick a routing strategy | 30m |
| Wire registry (slow) | `multi-plan-routing-impl-slow.md` | Wire the registry + inbound poller | 1h |
| Tests | `multi-plan-routing-tests.md` | Multi-plan ambiguity + slug-prefix override | 30m |
