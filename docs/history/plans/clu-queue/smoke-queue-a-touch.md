# smoke-queue-a-touch — write sentinel + commit + complete

You are phase `touch` of `smoke-queue-a`, a trivial sentinel plan
the `clu-queue` smoke phase enqueued. Cron popped this plan and
dispatched you. Do the minimal work below and exit.

This is a **no-op smoke**, NOT a code change. Do not edit
`end_of_line/`, `tests/`, or anything outside `tmp/smoke/`.

## Read first

- Nothing else. The four positional args (PLAN, PHASE, TOKEN,
  STATE) are in your prompt. The `/clu-phase` skill's "sacred
  contract" applies — never exit without `clu complete` or
  `clu block`.

## Produce

1. Capture args: `PLAN=smoke-queue-a`, `PHASE=touch`,
   `TOKEN=$3`, `STATE=$4`, `PROJECT=$(cd $(dirname $STATE)/../.. && pwd)`.
2. From the project root:
   ```bash
   mkdir -p tmp/smoke
   printf 'smoke-queue-a touched %s\n' "$(date -u +%FT%TZ)" \
       > tmp/smoke/smoke-queue-a.touched
   git add tmp/smoke/smoke-queue-a.touched
   ```
3. Commit (HEREDOC, structured):
   ```
   smoke-queue-a touch: sentinel for clu-queue drain dogfood

   Why: prove the cron-driven queue pop reached this plan and
   dispatched the worker.

   What's new: tmp/smoke/smoke-queue-a.touched sentinel file.

   Under the hood: trivial no-op smoke; clu-queue smoke phase's
   archive commit removes this file.

   Tests: none (no code change).

   Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
   ```
4. Capture SHA: `SHA=$(git rev-parse HEAD)`.
5. Call:
   ```bash
   /Users/smabe/.local/bin/clu complete \
       --project "$PROJECT" --plan smoke-queue-a --phase touch \
       --token "$TOKEN" --commit "$SHA"
   ```

## Done criteria

- `tmp/smoke/smoke-queue-a.touched` exists and is committed.
- `clu complete` returned 0 (the live claim's token matched).
- No other files modified.
