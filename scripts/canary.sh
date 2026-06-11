#!/usr/bin/env bash
# Clean-clone canary — proves the committed repo is self-contained.
#
# Fresh `file://` clone (no hardlinks, working tree built purely from git
# objects, so any dependency on an untracked file or local machine state
# surfaces here), fresh venv at the LOWEST available supported Python
# (requires-python is ">=3.11" — the floor is only a claim unless something
# runs it), then the same gate as development: install, ruff, basedpyright,
# full unittest suite.
#
# Skip-guard: a run is skipped when main's HEAD hash matches the last PASSING
# run. A failing run re-fires on every schedule until fixed or HEAD moves.
#
# Scheduled weekly by ~/Library/LaunchAgents/com.abe.clu-canary.plist.
# Run manually: scripts/canary.sh [--force]
set -uo pipefail

REPO="$HOME/projects/end-of-line"
STATE_DIR="$HOME/.local/state/clu-canary"
LOG="$HOME/Library/Logs/clu-canary.log"
FORCE="${1:-}"

mkdir -p "$STATE_DIR" "$(dirname "$LOG")"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }

notify() { # title, body
    /usr/bin/osascript -e "display notification \"$2\" with title \"$1\"" 2>/dev/null || true
}

HEAD_HASH="$(git -C "$REPO" rev-parse main 2>/dev/null)" || {
    log "ERROR: cannot resolve main in $REPO"
    notify "clu canary: broken" "cannot resolve main in $REPO"
    exit 2
}

LAST_PASS="$(cat "$STATE_DIR/last_pass" 2>/dev/null || true)"
if [ "$HEAD_HASH" = "$LAST_PASS" ] && [ "$FORCE" != "--force" ]; then
    log "skip: main @ ${HEAD_HASH:0:12} already passed"
    exit 0
fi

# Lowest supported Python first — the whole point is testing the floor.
PYBIN=""
for cand in python3.11 python3.12 python3.13 python3; do
    if command -v "$cand" >/dev/null 2>&1; then PYBIN="$cand"; break; fi
done
# Homebrew keg-only fallback (python@3.11 doesn't symlink into PATH).
if [ "$PYBIN" = "python3" ] && [ -x /opt/homebrew/opt/python@3.11/bin/python3.11 ]; then
    PYBIN=/opt/homebrew/opt/python@3.11/bin/python3.11
fi

WORK="$(mktemp -d /tmp/clu-canary.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
RUN_LOG="$WORK/run.log"

fail() { # stage
    log "FAIL at $1: main @ ${HEAD_HASH:0:12} (python: $("$PYBIN" --version 2>&1)) — log tail follows"
    tail -30 "$RUN_LOG" >>"$LOG"
    printf '%s\tfail\t%s\n' "$HEAD_HASH" "$1" >"$STATE_DIR/last_result"
    notify "clu canary FAILED" "stage: $1 @ ${HEAD_HASH:0:12} — see ~/Library/Logs/clu-canary.log"
    exit 1
}

log "run: main @ ${HEAD_HASH:0:12}, python: $("$PYBIN" --version 2>&1) ($PYBIN)"
if ! "$PYBIN" -c 'import sys; sys.exit(0 if sys.version_info < (3,12) else 1)'; then
    log "note: Python 3.11 unavailable — requires-python floor NOT verified this run"
fi

git clone --quiet --depth 1 "file://$REPO" "$WORK/clone" >>"$RUN_LOG" 2>&1 || fail clone
cd "$WORK/clone"

"$PYBIN" -m venv "$WORK/venv"                       >>"$RUN_LOG" 2>&1 || fail venv
"$WORK/venv/bin/pip" install --quiet -e ".[dev]"    >>"$RUN_LOG" 2>&1 || fail install
"$WORK/venv/bin/ruff" check .                       >>"$RUN_LOG" 2>&1 || fail ruff
"$WORK/venv/bin/basedpyright"                       >>"$RUN_LOG" 2>&1 || fail basedpyright
"$WORK/venv/bin/python" -m unittest discover -s tests >>"$RUN_LOG" 2>&1 || fail tests

# Same unittest summary line scripts/partest.py parses (_RAN_RE) — keep in sync.
TESTS_RUN="$(grep -Eo 'Ran [0-9]+ tests?' "$RUN_LOG" | tail -1 || echo 'tests')"
log "PASS: main @ ${HEAD_HASH:0:12} — $TESTS_RUN"
printf '%s' "$HEAD_HASH" >"$STATE_DIR/last_pass"
printf '%s\tpass\n' "$HEAD_HASH" >"$STATE_DIR/last_result"
exit 0
