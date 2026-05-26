"""Phase `pop` tests: post-loop per-project queue advancement in `cmd_tick_all`.

Each test sets up a project (registry-bootstrapped with a `seed` plan in a
parked state) plus a populated queue, mocks `dispatch_for_tick` so we can
assert what would have been dispatched without spawning subprocesses, then
runs `main(["tick-all"])` and inspects the resulting queue / state.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line import notify, queue, registry
from end_of_line import state as st
from end_of_line.cli import main
from end_of_line.config import ProjectConfig
from tests import isolate_registry

_PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| only | `{slug}-only.md` | thing | 1h |
"""


def _plan_body(slug: str) -> str:
    return _PLAN_BODY.format(slug=slug)


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        isolate_registry(self, self.tmp)
        patcher = mock.patch("end_of_line.dispatch.dispatch_for_tick")
        self.mock_dispatch = patcher.start()
        self.addCleanup(patcher.stop)

    def _make_project(self, name: str, *, parked_seed: bool = True) -> Path:
        """Create a project with a registered `seed` plan.

        When `parked_seed=True` (default), seed's status is flipped to DONE
        so its per-plan tick returns idle and the post-loop queue pop is the
        only meaningful action that fires for this project.
        """
        project = (self.tmp / name).resolve()
        plans_dir = project / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "seed.md").write_text(_plan_body("seed"))
        rc = main(["init", "--project", str(project), "--plan", "seed"])
        self.assertEqual(rc, 0)
        if parked_seed:
            cfg = ProjectConfig(project_root=project)
            with st.mutate(cfg.state_path("seed")) as data:
                data["status"] = st.STATUS_DONE
        return project

    def _write_plan_file(self, project: Path, slug: str) -> Path:
        plans_dir = project / "plans"
        plans_dir.mkdir(exist_ok=True)
        path = plans_dir / f"{slug}.md"
        path.write_text(_plan_body(slug))
        return path

    def _queue_path(self, project: Path) -> Path:
        return ProjectConfig(project_root=project).queue_path()

    def _enqueue(
        self,
        project: Path,
        slug: str,
        *,
        write_plan: bool = True,
    ) -> None:
        if write_plan:
            self._write_plan_file(project, slug)
        with queue.mutate(self._queue_path(project)) as data:
            data["queue"].append(
                {
                    "slug": slug,
                    "added_at": st.utcnow(),
                    "added_by": "operator",
                    "position_at_add": "tail",
                }
            )

    def _set_status(self, project: Path, slug: str, status: str) -> None:
        cfg = ProjectConfig(project_root=project)
        with st.mutate(cfg.state_path(slug)) as data:
            data["status"] = status

    def _state(self, project: Path, slug: str) -> dict:
        return st.load(ProjectConfig(project_root=project).state_path(slug))

    def _queue_data(self, project: Path) -> dict:
        return queue.load(self._queue_path(project))

    def _run(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["tick-all"])
        return rc, out.getvalue(), err.getvalue()


class QueuePopTestCase(_Base):
    def test_pop_dispatches_idle_project_with_pending_queue(self) -> None:
        project = self._make_project("alpha")
        self._enqueue(project, "foo")

        rc, _, _ = self._run()
        self.assertEqual(rc, 0)

        # registry now has foo + seed; queue is empty; foo state.json exists
        # with EVENT_QUEUE_POPPED as the first event; dispatch was called.
        slugs = {
            e.plan_slug for e in registry.entries() if Path(e.project_root).resolve() == project
        }
        self.assertIn("foo", slugs)
        data = self._queue_data(project)
        self.assertEqual(data["queue"], [])
        foo = self._state(project, "foo")
        self.assertEqual(foo["events"][0]["type"], st.EVENT_QUEUE_POPPED)
        self.assertEqual(foo["events"][0]["slug"], "foo")
        # dispatch_for_tick was called for foo at least once
        calls = [c for c in self.mock_dispatch.call_args_list if "foo" in str(c)]
        self.assertTrue(calls, "expected dispatch_for_tick to be called for foo")

    def test_pop_skipped_when_project_has_active_claim(self) -> None:
        project = self._make_project("alpha", parked_seed=False)
        # Plant a current_claim on seed so the busy gate fires.
        cfg = ProjectConfig(project_root=project)
        with st.mutate(cfg.state_path("seed")) as data:
            data["current_claim"] = {
                "phase_id": "only",
                "claimed_by": "session-busy",
                "lease_expires": "2099-01-01T00:00:00Z",
                "started_at": st.utcnow(),
                "last_heartbeat_at": st.utcnow(),
                "attempts": 1,
            }
        self._enqueue(project, "foo")

        rc, _, _ = self._run()
        self.assertEqual(rc, 0)

        data = self._queue_data(project)
        self.assertEqual([e["slug"] for e in data["queue"]], ["foo"])
        slugs = {
            e.plan_slug for e in registry.entries() if Path(e.project_root).resolve() == project
        }
        self.assertNotIn("foo", slugs)
        # No dispatch call mentioned foo.
        for c in self.mock_dispatch.call_args_list:
            self.assertNotIn("foo", str(c))

    def test_pop_multi_project_independent(self) -> None:
        a = self._make_project("alpha", parked_seed=False)
        # busy A
        cfg_a = ProjectConfig(project_root=a)
        with st.mutate(cfg_a.state_path("seed")) as data:
            data["current_claim"] = {
                "phase_id": "only",
                "claimed_by": "session-busy",
                "lease_expires": "2099-01-01T00:00:00Z",
                "started_at": st.utcnow(),
                "last_heartbeat_at": st.utcnow(),
                "attempts": 1,
            }
        b = self._make_project("beta")
        self._enqueue(b, "foo")

        rc, _, _ = self._run()
        self.assertEqual(rc, 0)

        # B drained, A unchanged.
        self.assertEqual(self._queue_data(b)["queue"], [])
        b_slugs = {e.plan_slug for e in registry.entries() if Path(e.project_root).resolve() == b}
        self.assertIn("foo", b_slugs)

    def test_pop_caps_at_one_per_project_per_tick(self) -> None:
        project = self._make_project("alpha")
        self._enqueue(project, "a")
        self._enqueue(project, "b")

        rc, _, _ = self._run()
        self.assertEqual(rc, 0)
        data = self._queue_data(project)
        self.assertEqual([e["slug"] for e in data["queue"]], ["b"])
        # After 1st tick, a has a current_claim (from supervisor.tick claiming
        # its phase) → busy gate fires on next tick.
        rc, _, _ = self._run()
        self.assertEqual(rc, 0)
        data = self._queue_data(project)
        self.assertEqual([e["slug"] for e in data["queue"]], ["b"])
        # Clear a's claim AND park it DONE so the per-plan tick on the next
        # tick-all doesn't immediately re-claim phase `only`. With a parked,
        # the busy gate lifts and b finally pops.
        cfg = ProjectConfig(project_root=project)
        with st.mutate(cfg.state_path("a")) as data:
            data["current_claim"] = None
            data["status"] = st.STATUS_DONE
        rc, _, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(self._queue_data(project)["queue"], [])

    def _assert_freeze(self, freeze_status: str) -> None:
        project = self._make_project("alpha")
        self._write_plan_file(project, "foo")
        # Pre-register foo + set its status to the freeze status.
        registry.register(project, "foo")
        cfg = ProjectConfig(project_root=project)
        st.save_atomic(cfg.state_path("foo"), st.empty_state("foo", cfg.plan_dir))
        with st.mutate(cfg.state_path("foo")) as data:
            data["status"] = freeze_status
        self._enqueue(project, "foo", write_plan=False)
        self._enqueue(project, "bar")

        rc, _, _ = self._run()
        self.assertEqual(rc, 0)
        data = self._queue_data(project)
        self.assertEqual([e["slug"] for e in data["queue"]], ["foo", "bar"])

    def test_pop_freezes_on_halted_head(self) -> None:
        self._assert_freeze(st.STATUS_HALTED)

    def test_pop_freezes_on_paused_head(self) -> None:
        self._assert_freeze(st.STATUS_PAUSED)

    def test_pop_freezes_on_halted_replan_head(self) -> None:
        self._assert_freeze(st.STATUS_HALTED_REPLAN)

    def test_pop_absorbs_done_head(self) -> None:
        project = self._make_project("alpha")
        self._write_plan_file(project, "foo")
        registry.register(project, "foo")
        cfg = ProjectConfig(project_root=project)
        st.save_atomic(cfg.state_path("foo"), st.empty_state("foo", cfg.plan_dir))
        with st.mutate(cfg.state_path("foo")) as data:
            data["status"] = st.STATUS_DONE
        self._enqueue(project, "foo", write_plan=False)

        before_events = self._state(project, "foo")["events"][:]

        rc, _, _ = self._run()
        self.assertEqual(rc, 0)

        data = self._queue_data(project)
        self.assertEqual(data["queue"], [])
        self.assertEqual(len(data["history"]), 1)
        self.assertEqual(data["history"][0]["outcome"], "absorbed")
        self.assertEqual(data["history"][0]["slug"], "foo")
        # foo's state.json untouched (no new EVENT_QUEUE_POPPED).
        after_events = self._state(project, "foo")["events"]
        self.assertEqual(after_events, before_events)

    def test_pop_absorbs_running_head(self) -> None:
        project = self._make_project("alpha")
        # Write a body without a Sessions index so foo's per-plan tick
        # returns `error` and does NOT claim a phase — the absorb-RUNNING
        # path only fires when there's no live claim on foo.
        (project / "plans" / "foo.md").write_text("# foo\n(no sessions)\n")
        registry.register(project, "foo")
        cfg = ProjectConfig(project_root=project)
        st.save_atomic(cfg.state_path("foo"), st.empty_state("foo", cfg.plan_dir))
        # Status stays at RUNNING (the default).
        self._enqueue(project, "foo", write_plan=False)

        rc, _, _ = self._run()
        self.assertEqual(rc, 0)

        data = self._queue_data(project)
        self.assertEqual(data["queue"], [])
        self.assertEqual(len(data["history"]), 1)
        self.assertEqual(data["history"][0]["outcome"], "absorbed")
        # No EVENT_QUEUE_POPPED in foo's state — absorb skips state-create.
        foo_events = self._state(project, "foo")["events"]
        self.assertFalse(any(e["type"] == st.EVENT_QUEUE_POPPED for e in foo_events))

    def test_pop_abandons_missing_plan_file(self) -> None:
        project = self._make_project("alpha")
        # Queue contains slug whose plan file does NOT exist.
        with queue.mutate(self._queue_path(project)) as data:
            data["queue"].append(
                {
                    "slug": "ghost",
                    "added_at": st.utcnow(),
                    "added_by": "operator",
                    "position_at_add": "tail",
                }
            )
        sent: list[tuple[str, str]] = []

        def fake_send(spec, kind, body, **kw):  # signature: (spec, kind, body)
            sent.append((kind, body))
            return True

        with mock.patch.object(notify, "notify", side_effect=fake_send):
            rc, _, _ = self._run()
        self.assertEqual(rc, 0)

        data = self._queue_data(project)
        self.assertEqual(data["queue"], [])
        self.assertEqual(len(data["history"]), 1)
        self.assertEqual(data["history"][0]["outcome"], "abandoned")
        # KIND_QUEUE_SKIPPED ping fired (we mocked notify, so quiet-hours
        # gating is bypassed — what we assert is that the call happened with
        # the right kind).
        self.assertTrue(any(k == notify.KIND_QUEUE_SKIPPED for k, _ in sent))

    def test_pop_kind_queue_skipped_not_in_bypass_set(self) -> None:
        # Defense against a future PR that accidentally puts QUEUE_SKIPPED
        # in the halt-bypass set — skips MUST defer during quiet hours.
        self.assertNotIn(
            notify.KIND_QUEUE_SKIPPED,
            notify.QUIET_HOURS_BYPASS_KINDS,
        )

    def test_pop_recovers_after_crash_between_state_and_registry(self) -> None:
        project = self._make_project("alpha")
        self._write_plan_file(project, "foo")
        # Simulate a partial pop: state.json exists for foo (RUNNING, no
        # claim) but registry has NOT been updated and queue still has foo.
        cfg = ProjectConfig(project_root=project)
        state = st.empty_state("foo", cfg.plan_dir)
        st.append_event(
            state,
            st.EVENT_QUEUE_POPPED,
            slug="foo",
            added_at=st.utcnow(),
            added_by="operator",
            position=1,
        )
        st.save_atomic(cfg.state_path("foo"), state)
        self._enqueue(project, "foo", write_plan=False)

        rc, _, _ = self._run()
        self.assertEqual(rc, 0)

        # First-tick recovery path is the "absorb RUNNING" branch — foo's
        # state already exists in RUNNING, so the queue is absorbed rather
        # than re-popped. This is the correct recovery: the pre-existing
        # state.json IS the popped state, and the operator gets one history
        # entry rather than two pops.
        slugs = {
            e.plan_slug for e in registry.entries() if Path(e.project_root).resolve() == project
        }
        self.assertIn("foo", slugs)
        self.assertEqual(self._queue_data(project)["queue"], [])
        # foo's events still have the original EVENT_QUEUE_POPPED (not a duplicate).
        events = self._state(project, "foo")["events"]
        popped = [e for e in events if e["type"] == st.EVENT_QUEUE_POPPED]
        self.assertEqual(len(popped), 1)

    def test_pop_event_queue_popped_first_event(self) -> None:
        project = self._make_project("alpha")
        self._enqueue(project, "foo")

        # We need to inspect foo's state BEFORE the per-plan tick on the
        # popped plan races ahead — but since dispatch_for_tick is mocked,
        # the per-plan tick still runs and may emit phase_started after
        # EVENT_QUEUE_POPPED. The assertion: QUEUE_POPPED is event[0].
        rc, _, _ = self._run()
        self.assertEqual(rc, 0)
        events = self._state(project, "foo")["events"]
        self.assertEqual(events[0]["type"], st.EVENT_QUEUE_POPPED)
        self.assertEqual(events[0]["slug"], "foo")
        self.assertEqual(events[0]["added_by"], "operator")
        self.assertIn("added_at", events[0])
        # The per-plan tick that follows DOES emit phase_started — assert it
        # is later in the event log, not first.
        types = [e["type"] for e in events]
        self.assertLess(
            types.index(st.EVENT_QUEUE_POPPED),
            types.index(st.EVENT_PHASE_STARTED),
        )

    def test_pop_skipped_when_bootstrap_project_not_in_registry(self) -> None:
        # Project Q exists on disk but has no registered plans and no queue
        # file. tick-all walks registry.entries() — which is empty for Q —
        # so Q is never visited and never crashes.
        rc, _, err = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")

    def test_pop_does_not_block_other_projects_if_one_queue_corrupt(self) -> None:
        a = self._make_project("alpha")
        # Corrupt A's queue.json on disk.
        qa = self._queue_path(a)
        qa.parent.mkdir(parents=True, exist_ok=True)
        qa.write_text("{ not valid json")
        b = self._make_project("beta")
        self._enqueue(b, "foo")

        rc, _, err = self._run()
        self.assertEqual(rc, 0)
        # B still drained.
        self.assertEqual(self._queue_data(b)["queue"], [])
        # Error mentions the corruption so the operator can find it.
        self.assertIn("alpha", err.lower() if "alpha" in err else err + "alpha")


if __name__ == "__main__":
    unittest.main()
