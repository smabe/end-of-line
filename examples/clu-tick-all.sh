#!/usr/bin/env bash
# Back-compat shim. The original parser-of-`clu list` was promoted into
# the first-class `clu tick-all` subcommand; this file is now a one-line
# bridge so any already-installed LaunchAgent that still points at this
# path keeps working until the operator re-installs the new plist.
#
# Once the LaunchAgent at ~/Library/LaunchAgents/com.clu.tick.plist
# has been re-bootstrapped from examples/clu.tick.plist, this shim can
# be deleted.
exec /Users/smabe/.local/bin/clu tick-all "$@"
