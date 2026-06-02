"""Discord inbound poller — DiscordInboundPoller tests."""

from __future__ import annotations

import json
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from end_of_line import registry
from end_of_line import state as st
from end_of_line.notify_base import InboundPoller
from tests import CluTestCase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resp(data: dict | list):
    """Context-manager mock whose .read() yields JSON bytes."""
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    cm = mock.MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


def _make_429(retry_after_header: str | None = None):
    headers = mock.MagicMock()
    _hdr = retry_after_header
    headers.get = lambda k, d=None: _hdr if k == "Retry-After" else d
    return urllib.error.HTTPError("https://example.com", 429, "Too Many Requests", headers, None)


def _msg(
    msg_id: str, content: str, author_id: str = "USER", message_reference: dict | None = None
) -> dict:
    m: dict = {"id": msg_id, "content": content, "author": {"id": author_id}}
    if message_reference is not None:
        m["message_reference"] = message_reference
    return m


def _create_project_with_blocker(
    tmp_path: Path,
    plan_slug: str = "p",
    notify_metadata: dict | None = None,
) -> tuple[Path, str]:
    """Create a minimal project + state file with one open blocker.

    Returns (project_root, blocker_id).
    """
    project_root = tmp_path / f"proj-{plan_slug}"
    orch_dir = project_root / "plans" / ".orchestrator"
    orch_dir.mkdir(parents=True)
    data = st.empty_state(plan_slug, "plans")
    st.add_blocker(data, "ph-1", "Which?", ["A", "B"])
    blocker_id = data["blockers"][0]["id"]
    if notify_metadata:
        data["blockers"][0]["notify_metadata"] = notify_metadata
    st.save_atomic(orch_dir / f"{plan_slug}.state.json", data)
    return project_root, blocker_id


def _make_registry_loader(entries: list[registry.PlanEntry]):
    return lambda: entries


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class DiscordInboundDefaultPathsTestCase(CluTestCase):
    """Default cursor/state paths honor XDG (resolve under clu_config_dir())."""

    def test_default_paths_honor_xdg(self):
        from end_of_line._xdg_guard import clu_config_dir
        from end_of_line.notify_discord_inbound import DiscordInboundPoller

        poller = DiscordInboundPoller(bot_token="T", user_id="U", bot_user_id="BOT")
        self.assertEqual(poller.cursor_path, clu_config_dir() / "discord_cursor.json")
        self.assertEqual(poller._state_path, clu_config_dir() / "discord_state.json")
        self.assertTrue(str(poller.cursor_path).startswith(str(self.tmp_path)))


class DiscordInboundPollerBase(CluTestCase):
    def setUp(self):
        super().setUp()
        self.cursor_path = self.tmp_path / "discord_cursor.json"
        self.state_path = self.tmp_path / "discord_state.json"
        self.dm_channel_id = "dm-ch-42"
        # Pre-seed DM channel cache so _ensure_dm_channel skips API call.
        self.state_path.write_text(json.dumps({"U": self.dm_channel_id}))

    def _poller(self, registry_loader=None, **kw):
        from end_of_line.notify_discord_inbound import DiscordInboundPoller

        return DiscordInboundPoller(
            bot_token="T",
            user_id="U",
            bot_user_id="BOT",
            cursor_path=self.cursor_path,
            state_path=self.state_path,
            registry_loader=registry_loader or (lambda: []),
            **kw,
        )

    def _urlopen_for_messages(self, messages: list[dict]):
        """Side-effect that serves the DM-channel messages GET."""

        def _side_effect(req, timeout=None):
            return _make_resp(messages)

        return _side_effect


# ---------------------------------------------------------------------------
# Poll mechanics
# ---------------------------------------------------------------------------


class PollMechanicsTestCase(DiscordInboundPollerBase):
    def test_poll_fetches_messages_after_cursor(self):
        # Pre-seed cursor so we expect ?after=msg-100 in the URL.
        self.cursor_path.write_text(json.dumps({self.dm_channel_id: "msg-100"}))
        seen_urls: list[str] = []

        def fake_urlopen(req, timeout=None):
            seen_urls.append(req.full_url)
            return _make_resp([])

        p = self._poller()
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            p.poll()

        self.assertTrue(
            any("after=msg-100" in u and "limit=100" in u for u in seen_urls),
            f"Expected ?after=msg-100&limit=100 in URL; saw: {seen_urls}",
        )

    def test_poll_advances_cursor_to_latest_message_id(self):
        messages = [_msg("msg-10", "hi"), _msg("msg-20", "bye")]
        p = self._poller()
        with mock.patch("urllib.request.urlopen", side_effect=self._urlopen_for_messages(messages)):
            p.poll()

        cursor = json.loads(self.cursor_path.read_text())
        self.assertEqual(cursor.get(self.dm_channel_id), "msg-20")

    def test_poll_filters_bot_own_messages(self):
        dispatched: list[tuple] = []

        project_root, blocker_id = _create_project_with_blocker(self.tmp_path, "pp")
        entry = registry.PlanEntry(
            project_root=str(project_root),
            plan_slug="pp",
            registered_at="2026-01-01T00:00:00Z",
        )
        # Two messages: one from the bot (should be filtered), one from USER ("1" → routes).
        messages = [
            _msg("msg-1", "1", author_id="BOT"),  # filtered — bot's own
            _msg("msg-2", "1", author_id="USER"),  # should dispatch
        ]

        p = self._poller(registry_loader=_make_registry_loader([entry]))
        with mock.patch("urllib.request.urlopen", side_effect=self._urlopen_for_messages(messages)):
            with mock.patch("subprocess.run") as mock_run:
                mock_run.return_value = mock.MagicMock(returncode=0)
                p.poll()

        # subprocess.run should have been called exactly once (for the USER message).
        self.assertEqual(mock_run.call_count, 1)

    def test_cursor_keyed_by_channel_id(self):
        # Verify cursor file stores {channel_id: message_id}, not a flat value.
        messages = [_msg("msg-99", "hi")]
        p = self._poller()
        with mock.patch("urllib.request.urlopen", side_effect=self._urlopen_for_messages(messages)):
            p.poll()

        cursor = json.loads(self.cursor_path.read_text())
        # Keyed by channel id, not flat.
        self.assertIn(self.dm_channel_id, cursor)
        self.assertEqual(cursor[self.dm_channel_id], "msg-99")
        # Must not be a flat string.
        self.assertIsInstance(cursor, dict)


# ---------------------------------------------------------------------------
# Reply correlation
# ---------------------------------------------------------------------------


class ReplyCorrelationTestCase(DiscordInboundPollerBase):
    def test_reply_with_message_reference_routes_by_metadata(self):
        # Blocker has notify_metadata.discord.message_id = "discord-msg-99".
        project_root, blocker_id = _create_project_with_blocker(
            self.tmp_path,
            "plan-a",
            notify_metadata={
                "discord": {"channel_id": self.dm_channel_id, "message_id": "discord-msg-99"}
            },
        )
        entry = registry.PlanEntry(
            project_root=str(project_root),
            plan_slug="plan-a",
            registered_at="2026-01-01T00:00:00Z",
        )
        messages = [_msg("msg-1", "A", message_reference={"message_id": "discord-msg-99"})]

        p = self._poller(registry_loader=_make_registry_loader([entry]))
        with mock.patch("urllib.request.urlopen", side_effect=self._urlopen_for_messages(messages)):
            with mock.patch("subprocess.run") as mock_run:
                mock_run.return_value = mock.MagicMock(returncode=0)
                p.poll()

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn("answer", cmd)
        self.assertNotIn(blocker_id, cmd)
        self.assertIn("A", cmd)

    def test_reply_without_message_reference_falls_back_to_text_grammar(self):
        # Bare "1" with one open blocker routes via route_reply().
        project_root, blocker_id = _create_project_with_blocker(self.tmp_path, "plan-b")
        entry = registry.PlanEntry(
            project_root=str(project_root),
            plan_slug="plan-b",
            registered_at="2026-01-01T00:00:00Z",
        )
        messages = [_msg("msg-1", "1")]  # no message_reference

        p = self._poller(registry_loader=_make_registry_loader([entry]))
        with mock.patch("urllib.request.urlopen", side_effect=self._urlopen_for_messages(messages)):
            with mock.patch("subprocess.run") as mock_run:
                mock_run.return_value = mock.MagicMock(returncode=0)
                p.poll()

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertNotIn(blocker_id, cmd)
        self.assertIn("1", cmd)

    def test_reply_unrecognized_text_advances_cursor_but_no_dispatch(self):
        messages = [_msg("msg-1", "lol whatever")]

        p = self._poller()
        with mock.patch("urllib.request.urlopen", side_effect=self._urlopen_for_messages(messages)):
            with mock.patch("subprocess.run") as mock_run:
                p.poll()

        mock_run.assert_not_called()
        cursor = json.loads(self.cursor_path.read_text())
        self.assertEqual(cursor.get(self.dm_channel_id), "msg-1")


# ---------------------------------------------------------------------------
# Rate-limit
# ---------------------------------------------------------------------------


class RateLimitTestCase(DiscordInboundPollerBase):
    def test_poll_handles_rate_limit_429(self):
        messages = [_msg("msg-5", "1")]
        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise _make_429(retry_after_header="0.1")
            return _make_resp(messages)

        p = self._poller()
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with mock.patch("time.sleep") as mock_sleep:
                p.poll()

        mock_sleep.assert_called_once_with(0.1)
        cursor = json.loads(self.cursor_path.read_text())
        self.assertEqual(cursor.get(self.dm_channel_id), "msg-5")


# ---------------------------------------------------------------------------
# DM channel resolution
# ---------------------------------------------------------------------------


class DMChannelResolutionTestCase(DiscordInboundPollerBase):
    def test_dm_channel_resolved_at_startup(self):
        # No pre-seeded state_path — poller must call POST /users/@me/channels.
        empty_state_path = self.tmp_path / "empty_discord_state.json"
        dm_calls: list[str] = []

        def fake_urlopen(req, timeout=None):
            dm_calls.append(req.full_url)
            if "/users/@me/channels" in req.full_url:
                return _make_resp({"id": "dm-ch-fresh"})
            # GET messages
            return _make_resp([])

        from end_of_line.notify_discord_inbound import DiscordInboundPoller

        p = DiscordInboundPoller(
            bot_token="T",
            user_id="U",
            bot_user_id="BOT",
            cursor_path=self.cursor_path,
            state_path=empty_state_path,  # no cache
            registry_loader=lambda: [],
        )
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            p.poll()

        self.assertTrue(
            any("/users/@me/channels" in u for u in dm_calls),
            f"Expected POST to /users/@me/channels; saw: {dm_calls}",
        )

    def test_discord_inbound_poller_is_inbound_poller(self):
        from end_of_line.notify_discord_inbound import DiscordInboundPoller

        p = DiscordInboundPoller(
            "T",
            "U",
            "BOT",
            cursor_path=self.cursor_path,
            state_path=self.state_path,
        )
        self.assertIsInstance(p, InboundPoller)


if __name__ == "__main__":
    unittest.main()
