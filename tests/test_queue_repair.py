"""Phase `repair` tests: auto-repair worker pipeline + slug-preservation rules.

Most tests exercise `_handle_corrupt_queue` directly with a stubbed
`dispatch_repair_worker` so we can simulate worker outcomes
(success/dropped-slug/empty/history-removal/unparseable/timeout/exit9)
without spawning subprocesses. Two integration-shaped tests run via the
full `tick-all` path to confirm corruption no longer crashes the loop
and to assert multi-project independence.

The load-bearing assertion in this suite: whenever validation says no,
the queue file on disk MUST equal the pre-repair bytes. Drift here is
how silent data loss creeps in.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line import notify, queue, registry, state as st
from end_of_line.cli import _handle_corrupt_queue, main
from end_of_line.config import DispatchSpec, NotifySpec, ProjectConfig
from tests import isolate_registry


def _valid_payload() -> dict:
    return {
        "schema_version": 1,
        "queue": [
            {"slug": "alpha", "added_at": st.utcnow(),
             "added_by": "operator", "position_at_add": "tail"},
            {"slug": "beta", "added_at": st.utcnow(),
             "added_by": "operator", "position_at_add": "tail"},
        ],
        "history": [
            {"slug": "gamma", "added_at": st.utcnow(),
             "ended_at": st.utcnow(), "outcome": "removed"},
        ],
    }


def _valid_bytes() -> bytes:
    return json.dumps(_valid_payload()).encode("utf-8")


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        isolate_registry(self, self.tmp)
        self.project = (self.tmp / "proj").resolve()
        (self.project / "plans" / ".orchestrator").mkdir(parents=True)
        self.queue_path = self.project / "plans" / ".orchestrator" / "queue.json"
        self.backup_bytes = _valid_bytes()
        # Plant corrupt bytes — repair pipeline always reads these as
        # the "original" + the workers we mock are responsible for the
        # post-repair contents.
        self.queue_path.write_bytes(b"{not json")
        sent: list[tuple] = []

        def _record(spec, kind, body, **kw):
            sent.append((kind, body))
            return True
        self.notify_patcher = mock.patch.object(notify, "notify", side_effect=_record)
        self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)
        self.sent = sent

    def _cfg(self, *, repair_command: str | None = None) -> ProjectConfig:
        return ProjectConfig(
            project_root=self.project,
            dispatch=DispatchSpec(
                kind="shell", command="echo phase",
                repair_command=repair_command,
            ),
            notify=NotifySpec.imessage_only("self"),
        )

    def _set_backup_in_corrupt(self, payload_bytes: bytes) -> None:
        """Make the on-disk file the 'original' that backup will preserve."""
        self.queue_path.write_bytes(payload_bytes)

    def _kinds(self) -> list[str]:
        return [k for k, _ in self.sent]

    def _diagnosis_hash(self, exc: Exception) -> str:
        import hashlib
        return hashlib.sha256(
            f"{type(exc).__name__}: {exc}".encode()
        ).hexdigest()[:8]


def _worker_writes(bytes_to_write: bytes):
    """Stub-side-effect: worker overwrites the corrupt_path with bytes."""
    def _side(cfg, corrupt_path, backup_path, diagnosis, log_path, **kw):
        corrupt_path.write_bytes(bytes_to_write)
        return 0
    return _side


def _worker_no_op(rc: int = 9):
    """Worker exits without modifying corrupt_path (REPAIR_DECLINED, timeout, etc.)."""
    def _side(cfg, corrupt_path, backup_path, diagnosis, log_path, **kw):
        return rc
    return _side


class RepairDisabledTests(_Base):
    def test_repair_disabled_when_repair_command_unset(self) -> None:
        self._set_backup_in_corrupt(self.backup_bytes)
        cfg = self._cfg(repair_command=None)
        exc = json.JSONDecodeError("expecting value", "{", 0)
        with mock.patch(
            "end_of_line.dispatch.dispatch_repair_worker"
        ) as mock_disp:
            _handle_corrupt_queue(cfg, exc, self.queue_path)
        mock_disp.assert_not_called()
        # Backup file written + throttle bumped + KIND_QUEUE_CORRUPT fired.
        backups = list(self.queue_path.parent.glob(f"{self.queue_path.name}.corrupt-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.backup_bytes)
        self.assertIn(notify.KIND_QUEUE_CORRUPT, self._kinds())
        throttle = self.queue_path.with_name(self.queue_path.name + ".repair-attempts")
        self.assertEqual(json.loads(throttle.read_text())["attempts"], 1)


class RepairValidationTests(_Base):
    def test_repair_success_validates_and_clears_throttle(self) -> None:
        self._set_backup_in_corrupt(self.backup_bytes)
        cfg = self._cfg(repair_command="run repair {corrupt_path}")
        throttle = self.queue_path.with_name(self.queue_path.name + ".repair-attempts")
        # Seed prior throttle hits — successful repair must clear them.
        throttle.write_text(json.dumps({
            "attempts": 2, "last_at": st.utcnow(), "diagnosis_hash": "x",
        }))
        exc = json.JSONDecodeError("expecting value", "{", 0)
        with mock.patch(
            "end_of_line.dispatch.dispatch_repair_worker",
            side_effect=_worker_writes(_valid_bytes()),
        ):
            _handle_corrupt_queue(cfg, exc, self.queue_path)
        self.assertIn(notify.KIND_QUEUE_REPAIRED, self._kinds())
        self.assertFalse(throttle.exists(), "throttle should be cleared on success")

    def test_repair_reverts_on_dropped_slug(self) -> None:
        self._set_backup_in_corrupt(self.backup_bytes)
        cfg = self._cfg(repair_command="run repair")
        # Worker drops alpha.
        dropped = {
            "schema_version": 1,
            "queue": [
                {"slug": "beta", "added_at": st.utcnow(),
                 "added_by": "operator", "position_at_add": "tail"},
            ],
            "history": [
                {"slug": "gamma", "added_at": st.utcnow(),
                 "ended_at": st.utcnow(), "outcome": "removed"},
            ],
        }
        exc = json.JSONDecodeError("nope", "{", 0)
        with mock.patch(
            "end_of_line.dispatch.dispatch_repair_worker",
            side_effect=_worker_writes(json.dumps(dropped).encode()),
        ):
            _handle_corrupt_queue(cfg, exc, self.queue_path)
        self.assertEqual(self.queue_path.read_bytes(), self.backup_bytes)
        self.assertIn(notify.KIND_QUEUE_REPAIR_FAILED, self._kinds())
        body = next(b for k, b in self.sent if k == notify.KIND_QUEUE_REPAIR_FAILED)
        self.assertIn("alpha", body)

    def test_repair_reverts_on_empty_queue_when_original_nonempty(self) -> None:
        self._set_backup_in_corrupt(self.backup_bytes)
        cfg = self._cfg(repair_command="run repair")
        empty = {
            "schema_version": 1, "queue": [],
            "history": [
                {"slug": "gamma", "added_at": st.utcnow(),
                 "ended_at": st.utcnow(), "outcome": "removed"},
            ],
        }
        exc = json.JSONDecodeError("nope", "{", 0)
        with mock.patch(
            "end_of_line.dispatch.dispatch_repair_worker",
            side_effect=_worker_writes(json.dumps(empty).encode()),
        ):
            _handle_corrupt_queue(cfg, exc, self.queue_path)
        self.assertEqual(self.queue_path.read_bytes(), self.backup_bytes)
        self.assertIn(notify.KIND_QUEUE_REPAIR_FAILED, self._kinds())

    def test_repair_reverts_on_history_removal(self) -> None:
        self._set_backup_in_corrupt(self.backup_bytes)
        cfg = self._cfg(repair_command="run repair")
        removed_history = {
            "schema_version": 1,
            "queue": [
                {"slug": "alpha", "added_at": st.utcnow(),
                 "added_by": "operator", "position_at_add": "tail"},
                {"slug": "beta", "added_at": st.utcnow(),
                 "added_by": "operator", "position_at_add": "tail"},
            ],
            "history": [],
        }
        exc = json.JSONDecodeError("nope", "{", 0)
        with mock.patch(
            "end_of_line.dispatch.dispatch_repair_worker",
            side_effect=_worker_writes(json.dumps(removed_history).encode()),
        ):
            _handle_corrupt_queue(cfg, exc, self.queue_path)
        self.assertEqual(self.queue_path.read_bytes(), self.backup_bytes)
        kind, body = next(
            (k, b) for k, b in self.sent if k == notify.KIND_QUEUE_REPAIR_FAILED
        )
        self.assertIn("gamma", body)

    def test_repair_reverts_on_still_unparseable(self) -> None:
        self._set_backup_in_corrupt(self.backup_bytes)
        cfg = self._cfg(repair_command="run repair")
        exc = json.JSONDecodeError("nope", "{", 0)
        with mock.patch(
            "end_of_line.dispatch.dispatch_repair_worker",
            side_effect=_worker_writes(b"still {not json"),
        ):
            _handle_corrupt_queue(cfg, exc, self.queue_path)
        self.assertEqual(self.queue_path.read_bytes(), self.backup_bytes)
        body = next(b for k, b in self.sent if k == notify.KIND_QUEUE_REPAIR_FAILED)
        self.assertIn("still unparseable", body)

    def test_repair_handles_worker_exit_9(self) -> None:
        # Worker refused to touch the file → file is still corrupt →
        # validation fails on "still unparseable" → REPAIR_FAILED path,
        # revert is a no-op (file already matches backup bytes since
        # worker didn't write).
        self._set_backup_in_corrupt(self.backup_bytes)
        cfg = self._cfg(repair_command="run repair")
        # Plant garbage as the on-disk state to simulate the "still
        # unparseable" outcome even though backup is valid bytes.
        self.queue_path.write_bytes(b"{not json")
        exc = json.JSONDecodeError("nope", "{", 0)
        with mock.patch(
            "end_of_line.dispatch.dispatch_repair_worker",
            side_effect=_worker_no_op(rc=9),
        ):
            _handle_corrupt_queue(cfg, exc, self.queue_path)
        self.assertIn(notify.KIND_QUEUE_REPAIR_FAILED, self._kinds())
        throttle = self.queue_path.with_name(self.queue_path.name + ".repair-attempts")
        self.assertEqual(json.loads(throttle.read_text())["attempts"], 1)

    def test_repair_handles_worker_timeout(self) -> None:
        # Worker hung → dispatch returned REPAIR_RC_TIMEOUT → file
        # unchanged → validation fails → revert (no-op) → REPAIR_FAILED.
        self._set_backup_in_corrupt(self.backup_bytes)
        cfg = self._cfg(repair_command="sleep forever")
        self.queue_path.write_bytes(b"{not json")
        exc = json.JSONDecodeError("nope", "{", 0)
        from end_of_line import dispatch as dispatch_mod
        with mock.patch(
            "end_of_line.dispatch.dispatch_repair_worker",
            side_effect=_worker_no_op(rc=dispatch_mod.REPAIR_RC_TIMEOUT),
        ):
            _handle_corrupt_queue(cfg, exc, self.queue_path)
        self.assertIn(notify.KIND_QUEUE_REPAIR_FAILED, self._kinds())


class RepairThrottleTests(_Base):
    def test_repair_throttle_blocks_fourth_attempt(self) -> None:
        self._set_backup_in_corrupt(self.backup_bytes)
        cfg = self._cfg(repair_command="run repair")
        exc = json.JSONDecodeError("nope", "{", 0)
        diagnosis_hash = self._diagnosis_hash(exc)
        throttle = self.queue_path.with_name(self.queue_path.name + ".repair-attempts")
        throttle.write_text(json.dumps({
            "attempts": 3, "last_at": st.utcnow(),
            "diagnosis_hash": diagnosis_hash,
        }))
        with mock.patch(
            "end_of_line.dispatch.dispatch_repair_worker"
        ) as mock_disp:
            _handle_corrupt_queue(cfg, exc, self.queue_path)
        mock_disp.assert_not_called()
        self.assertIn(notify.KIND_QUEUE_CORRUPT, self._kinds())
        body = next(b for k, b in self.sent if k == notify.KIND_QUEUE_CORRUPT)
        self.assertIn("gave up after 3 attempts", body)

    def test_repair_throttle_resets_on_success(self) -> None:
        self._set_backup_in_corrupt(self.backup_bytes)
        cfg = self._cfg(repair_command="run repair")
        exc = json.JSONDecodeError("nope", "{", 0)
        diagnosis_hash = self._diagnosis_hash(exc)
        throttle = self.queue_path.with_name(self.queue_path.name + ".repair-attempts")
        throttle.write_text(json.dumps({
            "attempts": 2, "last_at": st.utcnow(),
            "diagnosis_hash": diagnosis_hash,
        }))
        with mock.patch(
            "end_of_line.dispatch.dispatch_repair_worker",
            side_effect=_worker_writes(_valid_bytes()),
        ):
            _handle_corrupt_queue(cfg, exc, self.queue_path)
        self.assertFalse(throttle.exists())

    def test_repair_throttle_different_diagnosis_resets(self) -> None:
        self._set_backup_in_corrupt(self.backup_bytes)
        cfg = self._cfg(repair_command="run repair")
        throttle = self.queue_path.with_name(self.queue_path.name + ".repair-attempts")
        # Old hash with 3 attempts already; new corruption diagnosis hash
        # differs → not blocked.
        throttle.write_text(json.dumps({
            "attempts": 3, "last_at": st.utcnow(),
            "diagnosis_hash": "deadbeef",
        }))
        exc = json.JSONDecodeError("nope", "{", 0)
        with mock.patch(
            "end_of_line.dispatch.dispatch_repair_worker",
            side_effect=_worker_writes(_valid_bytes()),
        ) as mock_disp:
            _handle_corrupt_queue(cfg, exc, self.queue_path)
        mock_disp.assert_called_once()


class RepairFilesystemTests(_Base):
    def test_repair_backup_always_written_before_dispatch(self) -> None:
        self._set_backup_in_corrupt(self.backup_bytes)
        cfg = self._cfg(repair_command="run repair")
        exc = json.JSONDecodeError("nope", "{", 0)
        observed_backup_bytes: list[bytes] = []

        def _capture(cfg_, corrupt_path, backup_path, diagnosis, log_path, **kw):
            # When dispatch fires, the backup must already exist.
            observed_backup_bytes.append(backup_path.read_bytes())
            corrupt_path.write_bytes(_valid_bytes())
            return 0

        with mock.patch(
            "end_of_line.dispatch.dispatch_repair_worker",
            side_effect=_capture,
        ):
            _handle_corrupt_queue(cfg, exc, self.queue_path)
        self.assertEqual(observed_backup_bytes, [self.backup_bytes])


class RepairExtractSlugsTests(unittest.TestCase):
    def test_best_effort_extract_slugs_finds_all(self) -> None:
        data = (
            b'{"queue": [{"slug": "foo"}, {"slug": "bar"}],'
            b' "history": [{"slug": "baz"}]}'
        )
        self.assertEqual(
            queue.best_effort_extract_slugs(data),
            {"foo", "bar", "baz"},
        )

    def test_best_effort_extract_slugs_robust_to_corruption(self) -> None:
        # First half is a valid JSON prefix with two slugs; second half is
        # truncated mid-string. We still recover both slugs.
        data = b'{"queue": [{"slug": "alpha"}, {"slug": "beta"}], "histor'
        self.assertEqual(
            queue.best_effort_extract_slugs(data),
            {"alpha", "beta"},
        )

    def test_best_effort_extract_history_slugs_isolates_history(self) -> None:
        data = (
            b'{"queue": [{"slug": "alpha"}],'
            b' "history": [{"slug": "gamma"}, {"slug": "delta"}]}'
        )
        self.assertEqual(
            queue.best_effort_extract_history_slugs(data),
            {"gamma", "delta"},
        )


class RepairMultiProjectTests(unittest.TestCase):
    """Full-stack test: A corrupts, B has clean queue + idle plan → both progress."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        isolate_registry(self, self.tmp)
        patcher = mock.patch("end_of_line.dispatch.dispatch_for_tick")
        self.mock_dispatch = patcher.start()
        self.addCleanup(patcher.stop)
        sent: list[tuple] = []

        def _record(spec, kind, body, **kw):
            sent.append((kind, body))
            return True
        np = mock.patch.object(notify, "notify", side_effect=_record)
        np.start()
        self.addCleanup(np.stop)
        self.sent = sent

    def _bootstrap(self, name: str) -> Path:
        project = (self.tmp / name).resolve()
        plans = project / "plans"
        plans.mkdir(parents=True)
        (plans / "seed.md").write_text(
            "# seed\n\n## Sessions index\n\n"
            "| Session | Plan file | Scope | Effort |\n"
            "|---|---|---|---|\n"
            "| only | `seed-only.md` | x | 1h |\n"
        )
        rc = main(["init", "--project", str(project), "--plan", "seed"])
        self.assertEqual(rc, 0)
        cfg = ProjectConfig(project_root=project)
        with st.mutate(cfg.state_path("seed")) as data:
            data["status"] = st.STATUS_DONE
        return project

    def test_repair_does_not_block_other_projects(self) -> None:
        a = self._bootstrap("alpha")
        b = self._bootstrap("beta")
        # A's queue is corrupt.
        a_q = ProjectConfig(project_root=a).queue_path()
        a_q.parent.mkdir(parents=True, exist_ok=True)
        a_q.write_bytes(b"{not json")
        # B has a clean queue with one entry whose plan file exists.
        b_cfg = ProjectConfig(project_root=b)
        (b / "plans" / "foo.md").write_text(
            "# foo\n\n## Sessions index\n\n"
            "| Session | Plan file | Scope | Effort |\n"
            "|---|---|---|---|\n"
            "| only | `foo-only.md` | x | 1h |\n"
        )
        with queue.mutate(b_cfg.queue_path()) as data:
            data["queue"].append({
                "slug": "foo", "added_at": st.utcnow(),
                "added_by": "operator", "position_at_add": "tail",
            })

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["tick-all"])
        self.assertEqual(rc, 0)
        # B's queue drained; A is still corrupt (no repair_command set, so
        # the pipeline did backup + notify, not write).
        self.assertEqual(queue.load(b_cfg.queue_path())["queue"], [])
        b_slugs = {
            e.plan_slug for e in registry.entries()
            if Path(e.project_root).resolve() == b
        }
        self.assertIn("foo", b_slugs)
        self.assertIn(notify.KIND_QUEUE_CORRUPT, [k for k, _ in self.sent])


if __name__ == "__main__":
    unittest.main()
