#!/usr/bin/env bash
# Tick every registered plan once. Suitable for invocation from a
# 5-minute LaunchAgent (one Pop of the cron = one tick per plan).
#
# `clu list` outputs `  <plan>  <project>` per row (two-space indent).
# Parse it, ignore the header/empty case, fire one tick per row.
#
# Output goes to stdout + stderr; the LaunchAgent redirects to logs.
# Exits 0 unconditionally so a failing tick on one plan doesn't poison
# the launchd ThrottleInterval.
set -uo pipefail

CLU=/Users/smabe/.local/bin/clu

# `clu list` first row is non-indented "No plans registered" when empty;
# real rows have leading whitespace.
"$CLU" list | while IFS= read -r line; do
    case "$line" in
        " "*)
            plan=$(awk '{print $1}' <<<"$line")
            project=$(awk '{print $2}' <<<"$line")
            [ -n "$plan" ] && [ -n "$project" ] || continue
            echo "tick: $plan @ $project"
            "$CLU" tick --project "$project" --plan "$plan" --dispatch
            ;;
    esac
done

exit 0
