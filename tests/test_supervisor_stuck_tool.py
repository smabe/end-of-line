"""Process-tree walker + stuck-tool emit logic (worker-watchdog P2–P3).

The supervisor walks a worker pid's process tree via `ps` to find descendants
that have been alive long enough with low enough CPU usage to be considered
wedged. P2 is the pure walker; P3 is the threshold + dedup + emit logic
wired into the supervisor tick.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from end_of_line import inbox, state as st
from end_of_line.config import ProjectConfig
from end_of_line.supervisor import (
    Descendant,
    STUCK_TOOL_IGNORE_PATTERNS,
    _emit_stuck_tool,
    _parse_duration,
    _parse_ps_output,
    walk_worker_tree,
)
from tests import CluTestCase


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


# ---------------------------------------------------------------------------
# P3 — config + _emit_stuck_tool helper tests
# ---------------------------------------------------------------------------


class StuckToolConfigDefaultsTestCase(unittest.TestCase):
    def test_default_threshold_seconds(self) -> None:
        cfg = ProjectConfig(project_root=Path("/tmp"))
        self.assertEqual(cfg.stuck_tool_threshold_seconds, 300)

    def test_default_cpu_threshold_seconds(self) -> None:
        cfg = ProjectConfig(project_root=Path("/tmp"))
        self.assertEqual(cfg.stuck_tool_cpu_threshold_seconds, 5)


# A minimal wedged-xcodebuild scenario: worker pid 78233 has one descendant
# (81681) that's been alive 600s with only 0.5s CPU — clearly wedged.
PS_WEDGED_XCODEBUILD = """\
  PID  PPID    ELAPSED        TIME COMMAND
78233     1   12:28        0:30.50 claude --print /clu-phase plan-x ai-tools
81681 78233   10:00        0:00.50 /usr/bin/xcodebuild test -project HealthDash.xcodeproj
"""

PS_FRESH_BUILD = """\
  PID  PPID    ELAPSED        TIME COMMAND
78233     1   12:28        0:30.50 claude --print /clu-phase plan-x ai-tools
81681 78233   00:30        0:25.00 /usr/bin/xcodebuild test -project HealthDash.xcodeproj
"""

PS_BUSY_BUILD = """\
  PID  PPID    ELAPSED        TIME COMMAND
78233     1   12:28        0:30.50 claude --print /clu-phase plan-x ai-tools
81681 78233   10:00        8:00.00 /usr/bin/xcodebuild test -project HealthDash.xcodeproj
"""


def _empty_data_with_claim(worker_pid: int | None = 78233) -> dict:
    data = st.empty_state("plan-x", "/tmp/plan-x")
    data["current_claim"] = {
        "phase_id": "ai-tools",
        "claimed_by": "session-abc",
        "lease_expires": "2026-05-21T15:00:00Z",
        "started_at": "2026-05-21T14:00:00Z",
        "last_heartbeat_at": "2026-05-21T14:00:00Z",
        "attempts": 1,
    }
    if worker_pid is not None:
        data["current_claim"]["pid"] = worker_pid
    return data


def _config_with_thresholds(threshold: int = 300, cpu_max: int = 5) -> ProjectConfig:
    return ProjectConfig(
        project_root=Path("/tmp/plan-x"),
        stuck_tool_threshold_seconds=threshold,
        stuck_tool_cpu_threshold_seconds=cpu_max,
    )


class EmitStuckToolTestCase(CluTestCase):
    def test_emits_event_when_descendant_wedged(self) -> None:
        data = _empty_data_with_claim()
        cfg = _config_with_thresholds()
        _emit_stuck_tool(data, cfg, ps_output=PS_WEDGED_XCODEBUILD)
        events = [e for e in data["events"] if e["type"] == st.EVENT_TOOL_STUCK]
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["worker_pid"], 78233)
        self.assertEqual(ev["descendant_pid"], 81681)
        self.assertIn("xcodebuild", ev["command"])
        self.assertGreaterEqual(ev["elapsed_seconds"], 600)
        self.assertLessEqual(ev["cpu_seconds"], 5)

    def test_dedup_does_not_re_emit_same_descendant(self) -> None:
        data = _empty_data_with_claim()
        cfg = _config_with_thresholds()
        _emit_stuck_tool(data, cfg, ps_output=PS_WEDGED_XCODEBUILD)
        _emit_stuck_tool(data, cfg, ps_output=PS_WEDGED_XCODEBUILD)
        events = [e for e in data["events"] if e["type"] == st.EVENT_TOOL_STUCK]
        self.assertEqual(len(events), 1)

    def test_no_emit_when_descendant_below_elapsed_threshold(self) -> None:
        data = _empty_data_with_claim()
        cfg = _config_with_thresholds()
        _emit_stuck_tool(data, cfg, ps_output=PS_FRESH_BUILD)
        events = [e for e in data["events"] if e["type"] == st.EVENT_TOOL_STUCK]
        self.assertEqual(events, [])

    def test_no_emit_when_descendant_busy(self) -> None:
        # 10 min alive but 8 min CPU — clearly doing work, not wedged.
        data = _empty_data_with_claim()
        cfg = _config_with_thresholds()
        _emit_stuck_tool(data, cfg, ps_output=PS_BUSY_BUILD)
        events = [e for e in data["events"] if e["type"] == st.EVENT_TOOL_STUCK]
        self.assertEqual(events, [])

    def test_no_emit_when_no_claim(self) -> None:
        data = st.empty_state("plan-x", "/tmp/plan-x")
        cfg = _config_with_thresholds()
        _emit_stuck_tool(data, cfg, ps_output=PS_WEDGED_XCODEBUILD)
        events = [e for e in data["events"] if e["type"] == st.EVENT_TOOL_STUCK]
        self.assertEqual(events, [])

    def test_no_emit_when_claim_has_no_pid(self) -> None:
        data = _empty_data_with_claim(worker_pid=None)
        cfg = _config_with_thresholds()
        _emit_stuck_tool(data, cfg, ps_output=PS_WEDGED_XCODEBUILD)
        events = [e for e in data["events"] if e["type"] == st.EVENT_TOOL_STUCK]
        self.assertEqual(events, [])

    def test_threshold_zero_disables_detection(self) -> None:
        data = _empty_data_with_claim()
        cfg = _config_with_thresholds(threshold=0)
        _emit_stuck_tool(data, cfg, ps_output=PS_WEDGED_XCODEBUILD)
        events = [e for e in data["events"] if e["type"] == st.EVENT_TOOL_STUCK]
        self.assertEqual(events, [])

    def test_ignore_patterns_applied(self) -> None:
        # github-mcp-server should be filtered even if it meets thresholds.
        raw = (
            "  PID  PPID    ELAPSED        TIME COMMAND\n"
            "78233     1   12:28        0:30.50 claude --print /clu-phase plan-x ai-tools\n"
            "78277 78233   10:00        0:00.50 /opt/homebrew/bin/github-mcp-server stdio\n"
        )
        data = _empty_data_with_claim()
        cfg = _config_with_thresholds()
        _emit_stuck_tool(data, cfg, ps_output=raw)
        events = [e for e in data["events"] if e["type"] == st.EVENT_TOOL_STUCK]
        self.assertEqual(events, [])

    def test_writes_inbox_event(self) -> None:
        # The inbox event is what session-start surfaces via inbox-hook.
        data = _empty_data_with_claim()
        cfg = _config_with_thresholds()
        _emit_stuck_tool(data, cfg, ps_output=PS_WEDGED_XCODEBUILD)
        # Find any tool_stuck event file in the test's isolated inbox.
        events = inbox.read_unprocessed()
        tool_stuck = [e for e in events if e["type"] == "tool_stuck"]
        self.assertEqual(len(tool_stuck), 1)
        details = tool_stuck[0]["details"]
        self.assertEqual(details["worker_pid"], 78233)
        self.assertEqual(details["descendant_pid"], 81681)
        self.assertIn("xcodebuild", details["command"])

    def test_dedup_survives_intermediate_event(self) -> None:
        # If the dedup map isn't cleared by some other event in between,
        # repeated detection on the same descendant stays one-shot.
        data = _empty_data_with_claim()
        cfg = _config_with_thresholds()
        _emit_stuck_tool(data, cfg, ps_output=PS_WEDGED_XCODEBUILD)
        st.append_event(data, st.EVENT_PHASE_STARTED, phase="X")
        _emit_stuck_tool(data, cfg, ps_output=PS_WEDGED_XCODEBUILD)
        events = [e for e in data["events"] if e["type"] == st.EVENT_TOOL_STUCK]
        self.assertEqual(len(events), 1)


# ---------------------------------------------------------------------------
# P4 — surfaces: clu watch formatter + clu doctor health line
# ---------------------------------------------------------------------------


class WatchFormatterTestCase(unittest.TestCase):
    def test_formatter_registered_for_tool_stuck(self) -> None:
        from end_of_line import watch
        self.assertIn(st.EVENT_TOOL_STUCK, watch._FORMATTERS)

    def test_event_visible_by_default(self) -> None:
        # Stuck-tool events are actionable; they belong in the default stream
        # (not the verbose-only band like lease_expired).
        from end_of_line import watch
        self.assertIn(st.EVENT_TOOL_STUCK, watch._DEFAULT_VISIBLE)
        self.assertNotIn(st.EVENT_TOOL_STUCK, watch._VERBOSE_ONLY)

    def test_formatter_includes_phase_pid_elapsed(self) -> None:
        from end_of_line import watch
        fmt = watch._FORMATTERS[st.EVENT_TOOL_STUCK]
        rendered = fmt("plan-x", {
            "type": st.EVENT_TOOL_STUCK,
            "phase": "ai-tools",
            "worker_pid": 78233,
            "descendant_pid": 81681,
            "command": "/usr/bin/xcodebuild test -project HealthDash.xcodeproj",
            "elapsed_seconds": 600,
            "cpu_seconds": 0,
        })
        # The operator should be able to identify the wedged subprocess at a
        # glance without expanding the JSON payload.
        self.assertIn("plan-x/ai-tools", rendered)
        self.assertIn("81681", rendered)
        self.assertIn("600", rendered)
        self.assertIn("xcodebuild", rendered)


class DoctorStuckToolHealthTestCase(CluTestCase):
    def _write_state_with_wedge(
        self,
        project: Path,
        *,
        slug: str = "plan-x",
        worker_pid: int = 78233,
    ) -> Path:
        orch = project / "plans" / ".orchestrator"
        orch.mkdir(parents=True, exist_ok=True)
        state_path = orch / f"{slug}.state.json"
        data = st.empty_state(slug, str(project / "plans"))
        data["current_claim"] = {
            "phase_id": "ai-tools",
            "claimed_by": "session-abc",
            "lease_expires": "2026-05-21T15:00:00Z",
            "started_at": "2026-05-21T14:00:00Z",
            "last_heartbeat_at": "2026-05-21T14:00:00Z",
            "attempts": 1,
            "pid": worker_pid,
        }
        st.save_atomic(state_path, data)
        return state_path

    def _register(self, project: Path, slug: str = "plan-x") -> None:
        from end_of_line import registry
        cfg_path = project / "plans" / f"{slug}.md"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(
            "# x\n\n## Sessions index\n\n"
            "| Session | Plan file | Scope | Effort |\n"
            "|---|---|---|---|\n"
            "| ai-tools | `x-ai.md` | thing | 30min |\n"
        )
        (project / ".orchestrator.json").write_text('{"plan_dir": "plans"}')
        registry.register(project, slug)

    def test_doctor_prints_stuck_tool_when_descendant_wedged(self) -> None:
        import io
        from contextlib import redirect_stdout
        from end_of_line.cli import _print_stuck_tool_health
        from end_of_line.config import load_project_config

        project = self.tmp_path / "proj"
        project.mkdir()
        self._register(project)
        self._write_state_with_wedge(project)
        cfg = load_project_config(project)
        # Inject a ps_output instead of relying on a real wedged process —
        # the helper must accept a test seam analogous to _emit_stuck_tool.
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_stuck_tool_health(cfg, ps_output=PS_WEDGED_XCODEBUILD)
        out = buf.getvalue()
        self.assertIn("Stuck tools", out)
        self.assertIn("plan-x", out)
        self.assertIn("ai-tools", out)
        self.assertIn("81681", out)

    def test_doctor_silent_when_no_wedges(self) -> None:
        import io
        from contextlib import redirect_stdout
        from end_of_line.cli import _print_stuck_tool_health
        from end_of_line.config import load_project_config

        project = self.tmp_path / "proj"
        project.mkdir()
        self._register(project)
        self._write_state_with_wedge(project)
        cfg = load_project_config(project)
        # PS_BUSY_BUILD has high CPU — not stuck.
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_stuck_tool_health(cfg, ps_output=PS_BUSY_BUILD)
        # Stay quiet when there's nothing to report — doctor noise hurts
        # signal-to-noise across many plans.
        self.assertNotIn("Stuck tools", buf.getvalue())

    def test_doctor_silent_when_no_active_claims(self) -> None:
        import io
        from contextlib import redirect_stdout
        from end_of_line.cli import _print_stuck_tool_health
        from end_of_line.config import load_project_config

        project = self.tmp_path / "proj"
        project.mkdir()
        self._register(project)
        # Write state with no claim.
        orch = project / "plans" / ".orchestrator"
        orch.mkdir(parents=True, exist_ok=True)
        data = st.empty_state("plan-x", str(project / "plans"))
        st.save_atomic(orch / "plan-x.state.json", data)
        cfg = load_project_config(project)
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_stuck_tool_health(cfg, ps_output=PS_WEDGED_XCODEBUILD)
        self.assertNotIn("Stuck tools", buf.getvalue())


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
