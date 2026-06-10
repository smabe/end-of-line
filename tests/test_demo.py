"""`clu demo` orchestration: scaffold / up / down / sweep.

All registry + filesystem state is XDG-isolated via CluTestCase, and `down`
takes an injected `projects_root` so no test touches the real
`~/.config/clu` or `~/.claude/projects`. The dispatch step is stubbed so `up`
never spawns a real worker subprocess.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from end_of_line import demo, notify, registry, top
from end_of_line import state as st
from end_of_line.config import CONFIG_FILENAME, load_project_config
from end_of_line.plan_parser import parse_sessions_index
from tests import CluTestCase


class ScaffoldTest(CluTestCase):
    def test_writes_config_and_master_per_scenario(self) -> None:
        plans = demo.scaffold(["busy", "idle"])
        self.assertEqual([p.slug for p in plans], ["demo-busy", "demo-idle"])
        for p in plans:
            self.assertTrue((p.project_root / CONFIG_FILENAME).is_file())
            self.assertTrue((p.project_root / "plans" / f"{p.slug}.md").is_file())

    def test_dispatch_command_is_the_scenario_template(self) -> None:
        (plan,) = demo.scaffold(["block"])
        cfg = json.loads((plan.project_root / CONFIG_FILENAME).read_text())
        cmd = cfg["dispatch"]["command"]
        self.assertIn("demo-worker", cmd)
        self.assertIn("--scenario block", cmd)
        self.assertIn("{plan_slug}", cmd)

    def test_config_masks_every_global_notify_channel(self) -> None:
        # Inherited global iMessage/Discord must be disabled so neither the
        # worker, the orchestrator, nor the cron supervisor pings the operator.
        (plan,) = demo.scaffold(["dead"])
        cfg = json.loads((plan.project_root / CONFIG_FILENAME).read_text())
        masks = {c["kind"]: c["enabled"] for c in cfg["notify"]["channels"]}
        self.assertEqual(set(masks), set(notify._NOTIFIER_REGISTRY))
        self.assertTrue(all(enabled is False for enabled in masks.values()))
        # And the merged config genuinely resolves to zero live channels.
        loaded = load_project_config(plan.project_root)
        self.assertEqual([c for c in loaded.notify.channels if c.enabled], [])

    def test_master_parses_scenario_phase_index(self) -> None:
        # The master is multi-phase now: busy scaffolds a 5-row Sessions index
        # (the worker parks at phase 4) so the dashboard strip can render.
        (plan,) = demo.scaffold(["busy"])
        phases = parse_sessions_index(plan.project_root / "plans" / "demo-busy.md")
        self.assertEqual([ph.id for ph in phases], ["a", "b", "c", "d", "e"])

    def test_project_root_is_resolved(self) -> None:
        (plan,) = demo.scaffold(["busy"])
        self.assertEqual(plan.project_root, plan.project_root.resolve())


class UpTest(CluTestCase):
    def test_up_init_suppresses_the_notify_wizard(self) -> None:
        # `clu demo` runs in a TTY, so init must pass --no-notify-prompt or its
        # interactive "Wire iMessage? / Wire Discord?" wizard fires per plan and
        # clobbers the masked config. (The test env's non-TTY stdin would hide a
        # regression, so assert the flag is passed explicitly.)
        # `dead` parks at position 1, so its prefill prefix is empty — the stub
        # `_cli` never writes a state file, and an empty prefix keeps
        # `_prefill_completed` a no-op, so the init-flag assertion stands alone.
        calls: list[list[str]] = []
        with mock.patch("end_of_line.demo._cli", side_effect=lambda a: calls.append(a) or 0), \
             mock.patch("end_of_line.demo._dispatch"):
            demo.up(["dead"])
        init_calls = [c for c in calls if c and c[0] == "init"]
        self.assertTrue(init_calls)
        self.assertIn("--no-notify-prompt", init_calls[0])

    def test_up_inits_and_registers_without_spawning(self) -> None:
        with mock.patch("end_of_line.demo._dispatch") as disp:
            plans = demo.up(["busy", "idle"])
        # Dispatched once per plan (stubbed — no real worker).
        self.assertEqual(disp.call_count, 2)
        slugs = {e.plan_slug for e in registry.entries()}
        self.assertEqual(slugs, {"demo-busy", "demo-idle"})
        for p in plans:
            state = p.project_root / "plans" / ".orchestrator" / f"{p.slug}.state.json"
            self.assertTrue(state.is_file())


class MultiPhaseTest(CluTestCase):
    """Multi-phase demo masters + `_prefill_completed` park each worker at a
    varied phase position so `clu top`'s done/active/pending strip renders."""

    def test_master_plan_emits_one_row_per_phase(self) -> None:
        path = self.tmp_path / "demo-busy.md"
        path.write_text(demo._master_plan("demo-busy", ["a", "b", "c"]))
        phases = parse_sessions_index(path)
        self.assertEqual([p.id for p in phases], ["a", "b", "c"])

    def test_phase_layout_prefix_is_contiguous_and_parks_at_position(self) -> None:
        # The locked design: scenario -> (active 1-based position, phase total).
        # Single-source check: the done-prefix is always `ids[:position-1]`, and
        # the first uncompleted phase sits at `position`.
        for scenario, (position, total) in {
            "busy": (4, 5),
            "idle": (2, 4),
            "block": (3, 3),
            "dead": (1, 3),
        }.items():
            ids, done = demo._phase_layout(scenario)
            self.assertEqual(len(ids), total, scenario)
            self.assertEqual(done, ids[: position - 1], scenario)
            first_uncompleted = next(p for p in ids if p not in done)
            self.assertEqual(ids.index(first_uncompleted) + 1, position, scenario)

    def test_workers_park_at_varied_dashboard_positions(self) -> None:
        # Full `up` flow (init -> prefill -> tick) with the real subprocess spawn
        # stubbed: the tick still claims the first uncompleted phase, so the
        # gather_rows strip shows the target X-of-N per scenario.
        projects_root = self.tmp_path / "projects"
        with mock.patch("end_of_line.dispatch.dispatch_for_tick"):
            demo.up(["busy", "idle", "block", "dead"])
        rows = {r["plan"]: r for r in top.gather_rows(projects_root=projects_root)}
        expected = {
            "demo-busy": (4, 5),
            "demo-idle": (2, 4),
            "demo-block": (3, 3),
            "demo-dead": (1, 3),
        }
        for slug, (idx, total) in expected.items():
            self.assertIn(slug, rows)
            self.assertEqual(
                (rows[slug]["phase_index"], rows[slug]["phase_total"]),
                (idx, total),
                slug,
            )


class SweepTest(CluTestCase):
    def test_lists_only_demo_slugs(self) -> None:
        other = self.tmp_path / "realproj"
        other.mkdir()
        registry.register(other, "real-plan")
        with mock.patch("end_of_line.demo._dispatch"):
            demo.up(["busy"])
        self.assertEqual(demo.sweep(), ["demo-busy"])


class DownTest(CluTestCase):
    def _claim_with_worker(self, plan, *, pgid: int, session_id: str) -> Path:
        """Stamp a live-looking claim (pgid + session_id) onto a demo plan's state."""
        state = plan.project_root / "plans" / ".orchestrator" / f"{plan.slug}.state.json"
        with st.mutate(state) as data:
            data["current_claim"] = {
                "phase_id": "a",
                "token": "tok",
                "pgid": pgid,
                "session_id": session_id,
            }
        return state

    def test_removes_only_demo_entries_and_tree(self) -> None:
        other = self.tmp_path / "realproj"
        other.mkdir()
        registry.register(other, "real-plan")
        with mock.patch("end_of_line.demo._dispatch"):
            demo.up(["busy", "idle"])
        root = demo.demo_root()
        self.assertTrue(root.is_dir())

        removed = demo.down()

        self.assertEqual(sorted(removed), ["demo-busy", "demo-idle"])
        self.assertFalse(root.exists())
        # The non-demo plan survives.
        self.assertEqual({e.plan_slug for e in registry.entries()}, {"real-plan"})

    def test_reaps_worker_pgroup_and_drops_transcripts(self) -> None:
        projects_root = self.tmp_path / "projects"
        with mock.patch("end_of_line.demo._dispatch"):
            (plan,) = demo.up(["busy"])
        self._claim_with_worker(plan, pgid=424242, session_id="sess-x")
        # A synthetic transcript dir the teardown must remove.
        enc = projects_root / top.encode_project_dir(plan.project_root)
        (enc).mkdir(parents=True)
        (enc / "sess-x.jsonl").write_text("{}\n")

        with mock.patch("end_of_line.state.reap_orphan_pgroup") as reap:
            demo.down(projects_root=projects_root)

        reap.assert_called_once_with(424242, cmdline_match="demo-busy")
        self.assertFalse(enc.exists())

    def test_idempotent_second_call_is_noop(self) -> None:
        with mock.patch("end_of_line.demo._dispatch"):
            demo.up(["busy"])
        demo.down()
        self.assertEqual(demo.down(), [])


class DemoCliTest(CluTestCase):
    """`clu demo` glue: the `down` path, notify suppression, and the
    teardown-on-exit guarantee. The blocking/signal loop itself is manual."""

    def test_down_subcommand_tears_down_and_exits(self) -> None:
        from end_of_line.cli import main

        with mock.patch("end_of_line.demo.up") as up, \
             mock.patch("end_of_line.demo.down", return_value=["demo-busy"]) as down:
            rc = main(["demo", "down"])
        self.assertEqual(rc, 0)
        down.assert_called_once()
        up.assert_not_called()  # `down` never launches a fleet

    def test_suppresses_notifications(self) -> None:
        from end_of_line.cli import main

        with mock.patch("end_of_line.notify.set_global_suppress") as suppress, \
             mock.patch("end_of_line.demo.down", return_value=[]):
            main(["demo", "down"])
        suppress.assert_called_once_with(True)

    def test_up_path_always_tears_down_on_interrupt(self) -> None:
        # The core safety contract: however the foreground wait ends (Ctrl-C
        # here), the `finally` runs demo.down() so nothing is left registered.
        from end_of_line.cli import main

        plans = [demo.DemoPlan("busy", "demo-busy", Path("/x/demo-busy"))]
        with mock.patch("end_of_line.demo.up", return_value=plans), \
             mock.patch("end_of_line.demo.down", return_value=["demo-busy"]) as down, \
             mock.patch("end_of_line.cli.signal.pause", side_effect=KeyboardInterrupt):
            rc = main(["demo"])
        self.assertEqual(rc, 0)
        down.assert_called_once()

    def test_partial_launch_failure_still_tears_down(self) -> None:
        # up() is inside the try, so a mid-launch dispatch error must still hit
        # the teardown `finally` rather than leaking a half-registered fleet.
        from end_of_line.cli import main

        with mock.patch("end_of_line.demo.up", side_effect=RuntimeError("dispatch boom")), \
             mock.patch("end_of_line.demo.down", return_value=["demo-busy"]) as down:
            with self.assertRaises(RuntimeError):
                main(["demo"])
        down.assert_called_once()


if __name__ == "__main__":
    import unittest

    unittest.main()
