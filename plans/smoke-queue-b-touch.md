# smoke-queue-b-touch — write sentinel + commit + complete

You are phase `touch` of `smoke-queue-b`. Identical to
`smoke-queue-a-touch` except for the slug. See `smoke-queue-a-touch.md`
for the rationale.

## Produce

1. Args: PLAN=smoke-queue-b, PHASE=touch, TOKEN=$3, STATE=$4,
   PROJECT=$(cd $(dirname $STATE)/../.. && pwd).
2. From project root:
   ```bash
   mkdir -p tmp/smoke
   printf 'smoke-queue-b touched %s\n' "$(date -u +%FT%TZ)" \
       > tmp/smoke/smoke-queue-b.touched
   git add tmp/smoke/smoke-queue-b.touched
   ```
3. Commit (structured, HEREDOC):
   ```
   smoke-queue-b touch: sentinel for clu-queue drain dogfood

   Why: prove the cron-driven queue pop advanced past plan A and
   reached plan B.

   What's new: tmp/smoke/smoke-queue-b.touched sentinel file.

   Under the hood: trivial no-op smoke; cleaned up by clu-queue
   smoke phase's archive commit.

   Tests: none (no code change).

   Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
   ```
4. SHA=$(git rev-parse HEAD).
5. `/Users/smabe/.local/bin/clu complete --project "$PROJECT" --plan smoke-queue-b --phase touch --token "$TOKEN" --commit "$SHA"`.

## Done criteria

- `tmp/smoke/smoke-queue-b.touched` exists and is committed.
- `clu complete` returned 0.
