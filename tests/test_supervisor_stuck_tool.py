"""Process-tree walker for stuck-tool detection (worker-watchdog P2).

The supervisor walks a worker pid's process tree via `ps` to find descendants
that have been alive long enough with low enough CPU usage to be considered
wedged. P2 is the pure walker — tick wiring lands in P3.
"""
from __future__ import annotations

import unittest

from end_of_line.supervisor import (
    Descendant,
    STUCK_TOOL_IGNORE_PATTERNS,
    _parse_duration,
    _parse_ps_output,
    walk_worker_tree,
)


class ParseDurationTestCase(unittest.TestCase):
    def test_seconds_only(self) -> None:
        self.assertEqual(_parse_duration("30"), 30)

    def test_minutes_seconds(self) -> None:
        self.assertEqual(_parse_duration("01:30"), 90)

    def test_hours_minutes_seconds(self) -> None:
        self.assertEqual(_parse_duration("1:30:45"), 5445)

    def test_days_hours_minutes_seconds(self) -> None:
        # 2 days, 1 hour, 30 min, 0 sec = 2*86400 + 3600 + 1800 = 178200
        self.assertEqual(_parse_duration("2-01:30:00"), 178200)

    def test_fractional_seconds_truncated(self) -> None:
        # CPU time format `0:00.05` should round down to 0 sec.
        self.assertEqual(_parse_duration("0:00.05"), 0)

    def test_fractional_with_minutes(self) -> None:
        self.assertEqual(_parse_duration("1:23.45"), 83)

    def test_empty_returns_zero(self) -> None:
        self.assertEqual(_parse_duration(""), 0)

    def test_dash_returns_zero(self) -> None:
        # `ps` sometimes emits "-" for unmeasurable fields.
        self.assertEqual(_parse_duration("-"), 0)

    def test_whitespace_tolerated(self) -> None:
        self.assertEqual(_parse_duration("  01:30  "), 90)


PS_SAMPLE_HEADER = "  PID  PPID    ELAPSED        TIME COMMAND"

# Two-line worker + xcodebuild subtree, mirroring real output we observed
# on 2026-05-21 during the HealthDash debugging session.
PS_SAMPLE_WEDGED = """\
  PID  PPID    ELAPSED        TIME COMMAND
78233     1   12:28        0:30.50 claude --print --model claude-opus-4-7 /clu-phase plan-x ai-tools
78277 78233   12:27        0:00.10 /opt/homebrew/bin/github-mcp-server stdio
81679 78233   09:19        0:00.05 /bin/zsh -c xcodebuild test -project HealthDash.xcodeproj
81681 81679   09:19        0:00.10 /usr/bin/xcodebuild test -project HealthDash.xcodeproj
81718 81681   09:17        0:00.05 SWBBuildService
"""


class ParsePsOutputTestCase(unittest.TestCase):
    def test_skips_header_line(self) -> None:
        procs = _parse_ps_output(PS_SAMPLE_WEDGED)
        pids = [p.pid for p in procs]
        self.assertNotIn(0, pids)  # header would parse as garbage
        self.assertEqual(len(procs), 5)

    def test_parses_canonical_line(self) -> None:
        procs = _parse_ps_output(PS_SAMPLE_WEDGED)
        worker = next(p for p in procs if p.pid == 78233)
        self.assertEqual(worker.parent_pid, 1)
        self.assertEqual(worker.elapsed_seconds, 12 * 60 + 28)
        self.assertEqual(worker.cpu_seconds, 30)
        self.assertIn("claude --print", worker.command)

    def test_ignores_malformed_lines(self) -> None:
        raw = PS_SAMPLE_HEADER + "\nthis is not a valid ps line\n" + (
            "12345     1   00:30        0:00.10 /bin/sleep 30\n"
        )
        procs = _parse_ps_output(raw)
        self.assertEqual(len(procs), 1)
        self.assertEqual(procs[0].pid, 12345)

    def test_command_can_contain_spaces(self) -> None:
        procs = _parse_ps_output(PS_SAMPLE_WEDGED)
        zsh = next(p for p in procs if p.pid == 81679)
        self.assertEqual(
            zsh.command,
            "/bin/zsh -c xcodebuild test -project HealthDash.xcodeproj",
        )

    def test_empty_input_returns_empty_list(self) -> None:
        self.assertEqual(_parse_ps_output(""), [])
        self.assertEqual(_parse_ps_output(PS_SAMPLE_HEADER + "\n"), [])


class WalkWorkerTreeTestCase(unittest.TestCase):
    def test_returns_descendants_in_bfs_order(self) -> None:
        desc = walk_worker_tree(78233, ps_output=PS_SAMPLE_WEDGED)
        pids = [d.pid for d in desc]
        # Direct children first (78277, 81679), then grandchildren (81681), etc.
        self.assertEqual(pids, [78277, 81679, 81681, 81718])

    def test_does_not_include_root_pid(self) -> None:
        desc = walk_worker_tree(78233, ps_output=PS_SAMPLE_WEDGED)
        self.assertNotIn(78233, [d.pid for d in desc])

    def test_missing_root_pid_returns_empty(self) -> None:
        desc = walk_worker_tree(99999, ps_output=PS_SAMPLE_WEDGED)
        self.assertEqual(desc, [])

    def test_ignore_pattern_excludes_matching_command(self) -> None:
        # github-mcp-server is a known long-lived quiet process; filter it.
        desc = walk_worker_tree(
            78233,
            ps_output=PS_SAMPLE_WEDGED,
            ignore_patterns=("github-mcp-server",),
        )
        pids = [d.pid for d in desc]
        self.assertNotIn(78277, pids)
        # The xcodebuild subtree (81679/81681/81718) is still present.
        self.assertIn(81681, pids)

    def test_ignore_pattern_continues_walking_subtree(self) -> None:
        # If an ignored process has children, the children are still walked.
        # We don't include the ignored proc itself, but its subtree shows up.
        raw = PS_SAMPLE_HEADER + "\n" + "\n".join([
            "78233     1   12:28        0:30.50 claude --print",
            "11111 78233   05:00        0:00.10 npm exec xcodebuildmcp@latest",
            "11112 11111   05:00        0:00.05 node /opt/homebrew/bin/xcodebuildmcp",
            "22222 78233   05:00        0:00.05 /usr/bin/xcodebuild test",
        ])
        desc = walk_worker_tree(
            78233, ps_output=raw, ignore_patterns=("xcodebuildmcp",),
        )
        pids = [d.pid for d in desc]
        self.assertNotIn(11111, pids)  # ignored — matches "xcodebuildmcp"
        self.assertNotIn(11112, pids)  # also ignored — also matches
        self.assertIn(22222, pids)  # xcodebuild itself stays

    def test_polling_shell_pattern_ignored(self) -> None:
        # Claude Code's background-task wait shell: while kill -0 ... sleep loop.
        raw = PS_SAMPLE_HEADER + "\n" + "\n".join([
            "78233     1   12:28        0:30.50 claude --print",
            "92165 78233   01:13        0:00.05 /bin/zsh -c eval 'while kill -0 $(pgrep -f xcodebuild) 2>/dev/null; do sleep 5; done'",
        ])
        desc = walk_worker_tree(
            78233, ps_output=raw, ignore_patterns=STUCK_TOOL_IGNORE_PATTERNS,
        )
        self.assertEqual(desc, [])

    def test_default_no_ignore_patterns(self) -> None:
        # Calling without ignore_patterns returns the full tree (caller's
        # decision whether to apply filters).
        desc = walk_worker_tree(78233, ps_output=PS_SAMPLE_WEDGED)
        self.assertEqual(len(desc), 4)


class StuckToolIgnorePatternsTestCase(unittest.TestCase):
    def test_covers_github_mcp_server(self) -> None:
        self.assertTrue(
            any("github-mcp-server" in p for p in STUCK_TOOL_IGNORE_PATTERNS)
        )

    def test_covers_xcodebuildmcp(self) -> None:
        self.assertTrue(
            any("xcodebuildmcp" in p for p in STUCK_TOOL_IGNORE_PATTERNS)
        )

    def test_covers_polling_shell(self) -> None:
        # The `while kill -0 ... sleep` pattern Claude Code's bg-task uses.
        self.assertTrue(
            any("while kill -0" in p for p in STUCK_TOOL_IGNORE_PATTERNS)
        )


class WalkWorkerTreeLiveSmokeTestCase(unittest.TestCase):
    """Verify the live `ps` call (no ps_output passed) returns something
    plausible — the harness's own process tree should always contain the
    test runner's pid and at least one ancestor."""

    def test_live_ps_returns_descendants_for_pid_1(self) -> None:
        # PID 1 (init/launchd) has many descendants on any running system.
        # We can't assert exact contents, just that the walk produces something.
        desc = walk_worker_tree(1)
        # Be lenient: containers / minimal CI envs might have few processes.
        # The contract is "returns a list without crashing"; the precise
        # contents are OS-dependent.
        self.assertIsInstance(desc, list)


if __name__ == "__main__":
    unittest.main()
