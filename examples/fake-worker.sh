#!/usr/bin/env bash
# Fake phase-runner for smoke-testing clu's fleet without a real Claude worker.
#
# Dispatch invokes this with {plan_slug} {phase_id} {token} {state_file}. Behavior
# is selected by the phase-id suffix so we can mix happy-path, blocker, slow,
# and fail phases inside the same plan to exercise different code paths.
#
#   *-block         clu block (opens a question; user must answer)
#   *-slow          sleep 3s then clu complete (exercises mid-tick observation)
#   *-fail          exit 1 without completing (lease will expire; attempts counter ticks)
#   anything else   clu complete immediately
#
# All clu calls go through `python3 -m end_of_line.cli` so this works without a
# pipx install. Once you've done `pipx install -e .` swap to bare `clu`.
set -euo pipefail

PLAN="$1"
PHASE="$2"
TOKEN="$3"
STATE_FILE="$4"

# State file lives at <project>/plans/.orchestrator/<slug>.state.json — strip 3
# components to recover the project root.
PROJECT="$(cd "$(dirname "$STATE_FILE")/../.." && pwd)"

CLU=(python3 -m end_of_line.cli)

case "$PHASE" in
    *-block)
        # Resume-aware: clu re-dispatches a phase after its blocker is
        # answered (expecting the worker to continue from the answer). On
        # first invocation we open the blocker; on the re-dispatched run
        # `clu prior-blocker` reports the answered blocker, so we complete.
        if "${CLU[@]}" prior-blocker --project "$PROJECT" --plan "$PLAN" \
                --phase "$PHASE" >/dev/null 2>&1; then
            "${CLU[@]}" complete --project "$PROJECT" --plan "$PLAN" \
                --phase "$PHASE" --token "$TOKEN"
        else
            "${CLU[@]}" block --project "$PROJECT" --plan "$PLAN" \
                --phase "$PHASE" --token "$TOKEN" \
                --question "Fake blocker for $PLAN/$PHASE — pick one" \
                --option "yes" --option "no"
        fi
        ;;
    *-fail)
        echo "fake-worker: simulating failure on $PLAN/$PHASE" >&2
        exit 1
        ;;
    *-slow)
        sleep 3
        "${CLU[@]}" complete --project "$PROJECT" --plan "$PLAN" \
            --phase "$PHASE" --token "$TOKEN"
        ;;
    *)
        "${CLU[@]}" complete --project "$PROJECT" --plan "$PLAN" \
            --phase "$PHASE" --token "$TOKEN"
        ;;
esac
