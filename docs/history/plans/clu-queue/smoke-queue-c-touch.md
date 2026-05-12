# smoke-queue-c-touch — write sentinel + commit + complete

You are phase `touch` of `smoke-queue-c`. Last entry in the
clu-queue drain dogfood. Identical to `smoke-queue-a-touch`
except for the slug; see that file for the rationale.

## Produce

1. Args: PLAN=smoke-queue-c, PHASE=touch, TOKEN=$3, STATE=$4,
   PROJECT=$(cd $(dirname $STATE)/../.. && pwd).
2. From project root:
   ```bash
   mkdir -p tmp/smoke
   printf 'smoke-queue-c touched %s\n' "$(date -u +%FT%TZ)" \
       > tmp/smoke/smoke-queue-c.touched
   git add tmp/smoke/smoke-queue-c.touched
   ```
3. Commit (structured, HEREDOC):
   ```
   smoke-queue-c touch: sentinel for clu-queue drain dogfood

   Why: prove the cron-driven queue drained the third (last) plan
   and emptied cleanly.

   What's new: tmp/smoke/smoke-queue-c.touched sentinel file.

   Under the hood: trivial no-op smoke; cleaned up by clu-queue
   smoke phase's archive commit.

   Tests: none (no code change).

   Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
   ```
4. SHA=$(git rev-parse HEAD).
5. `/Users/smabe/.local/bin/clu complete --project "$PROJECT" --plan smoke-queue-c --phase touch --token "$TOKEN" --commit "$SHA"`.

## Done criteria

- `tmp/smoke/smoke-queue-c.touched` exists and is committed.
- `clu complete` returned 0.
