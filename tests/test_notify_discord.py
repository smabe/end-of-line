"""Discord outbound backend — DiscordNotifier tests."""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from end_of_line import state as st
from end_of_line.config import ChannelSpec
from end_of_line.notify_base import Notifier
from end_of_line.notify_discord import DiscordNotifier
from tests import CluTestCase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_429(retry_after_header: str | None = None, retry_after_body: float | None = None):
    headers = mock.MagicMock()
    _hdr = retry_after_header
    headers.get = lambda k, d=None: _hdr if k == "Retry-After" else d
    fp: io.BytesIO | None = None
    if retry_after_body is not None:
        fp = io.BytesIO(json.dumps({"retry_after": retry_after_body}).encode())
    return urllib.error.HTTPError("https://example.com", 429, "Too Many Requests", headers, fp)


def _mock_resp(data: dict):
    """Return a context-manager mock whose .read() yields JSON-encoded data."""
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    cm = mock.MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


# ---------------------------------------------------------------------------
# Basic
# ---------------------------------------------------------------------------


class DiscordNotifierBasicTestCase(CluTestCase):
    def test_discord_notifier_kind_name(self):
        n = DiscordNotifier("T", "U", state_path=self.tmp_path / "d.json")
        self.assertEqual(n.kind_name, "discord")

    def test_discord_notifier_is_a_notifier(self):
        n = DiscordNotifier("T", "U", state_path=self.tmp_path / "d.json")
        self.assertIsInstance(n, Notifier)

    def test_from_spec_reads_channel_params(self):
        spec = ChannelSpec(kind="discord", params={"bot_token": "T", "user_id": "U"})
        n = DiscordNotifier.from_spec(spec)
        self.assertEqual(n.bot_token, "T")
        self.assertEqual(n.user_id, "U")


# ---------------------------------------------------------------------------
# Send behaviour
# ---------------------------------------------------------------------------


class DiscordNotifierSendTestCase(CluTestCase):
    def setUp(self):
        super().setUp()
        self.discord_state = self.tmp_path / "discord_state.json"

    def _notifier(self, **kw):
        return DiscordNotifier("T", "U", state_path=self.discord_state, **kw)

    def _fake_urlopen(self, responses: list):
        """Return a side_effect function that pops from `responses` (dicts → success, exceptions → raise)."""
        it = iter(responses)

        def _side_effect(req, timeout=None):
            item = next(it)
            if isinstance(item, Exception):
                raise item
            return _mock_resp(item)

        return _side_effect

    # --- DM channel creation ------------------------------------------------

    def test_send_creates_dm_channel_on_first_call(self):
        urls: list[str] = []
        bodies: list[dict | None] = []

        def fake_urlopen(req, timeout=None):
            urls.append(req.full_url)
            bodies.append(json.loads(req.data.decode()) if req.data else None)
            if "/users/@me/channels" in req.full_url:
                return _mock_resp({"id": "dm-ch"})
            return _mock_resp({"id": "msg-1"})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self._notifier().send("blocker", "hello", plan_slug="p", blocker_id=None)

        self.assertEqual(len(urls), 2)
        self.assertIn("/users/@me/channels", urls[0])
        self.assertEqual(bodies[0], {"recipient_id": "U"})
        self.assertIn("/channels/dm-ch/messages", urls[1])

    def test_send_caches_dm_channel_id(self):
        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if "/users/@me/channels" in req.full_url:
                return _mock_resp({"id": "dm-ch"})
            return _mock_resp({"id": "msg-1"})

        notifier = self._notifier()
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            notifier.send("blocker", "hi", plan_slug="p", blocker_id=None)
            notifier.send("blocker", "hi2", plan_slug="p", blocker_id=None)

        # 2 calls first send (DM create + msg), 1 call second send (msg only)
        self.assertEqual(call_count[0], 3)

    # --- Return value / auth ------------------------------------------------

    def test_send_returns_message_id(self):
        def fake_urlopen(req, timeout=None):
            if "/users/@me/channels" in req.full_url:
                return _mock_resp({"id": "dm-ch"})
            return _mock_resp({"id": "12345"})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = self._notifier().send("blocker", "hello", plan_slug="p", blocker_id=None)

        self.assertEqual(result, "12345")

    def test_send_uses_bot_auth_header(self):
        seen_auth: list[str] = []

        def fake_urlopen(req, timeout=None):
            seen_auth.append(req.get_header("Authorization"))
            if "/users/@me/channels" in req.full_url:
                return _mock_resp({"id": "dm-ch"})
            return _mock_resp({"id": "msg-1"})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self._notifier().send("blocker", "hello", plan_slug="p", blocker_id=None)

        self.assertTrue(all(a == "Bot T" for a in seen_auth), seen_auth)

    # --- Blocker metadata persistence ---------------------------------------

    def test_send_persists_notify_metadata(self):
        state_dir = self.tmp_path / "orch"
        state_dir.mkdir()
        state_path = state_dir / "p.state.json"
        data = st.empty_state("p", "plans")
        st.add_blocker(data, "ph-1", "Which?", ["A", "B"])
        blocker_id = data["blockers"][0]["id"]
        st.save_atomic(state_path, data)

        def fake_urlopen(req, timeout=None):
            if "/users/@me/channels" in req.full_url:
                return _mock_resp({"id": "dm-ch-99"})
            return _mock_resp({"id": "msg-42"})

        notifier = DiscordNotifier(
            "T",
            "U",
            state_path=self.discord_state,
            state_root=state_dir,
        )
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            notifier.send("blocker", "Which?", plan_slug="p", blocker_id=blocker_id)

        updated = st.load(state_path)
        b = next(x for x in updated["blockers"] if x["id"] == blocker_id)
        meta = b.get("notify_metadata", {})
        self.assertEqual(meta["discord"]["channel_id"], "dm-ch-99")
        self.assertEqual(meta["discord"]["message_id"], "msg-42")

    # --- Rate-limit handling ------------------------------------------------

    def test_send_retries_once_on_429(self):
        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if "/users/@me/channels" in req.full_url:
                return _mock_resp({"id": "dm-ch"})
            # Second overall call is first message attempt → 429
            if call_count[0] == 2:
                raise _make_429(retry_after_header="1")
            return _mock_resp({"id": "msg-ok"})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with mock.patch("time.sleep") as mock_sleep:
                result = self._notifier().send("blocker", "hello", plan_slug="p", blocker_id=None)

        mock_sleep.assert_called_once_with(1.0)
        self.assertEqual(result, "msg-ok")

    def test_send_gives_up_after_second_429(self):
        def fake_urlopen(req, timeout=None):
            if "/users/@me/channels" in req.full_url:
                return _mock_resp({"id": "dm-ch"})
            raise _make_429(retry_after_header="1")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with mock.patch("time.sleep"):
                result = self._notifier().send("blocker", "hello", plan_slug="p", blocker_id=None)

        self.assertIsNone(result)

    def test_send_handles_retry_after_in_body(self):
        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if "/users/@me/channels" in req.full_url:
                return _mock_resp({"id": "dm-ch"})
            if call_count[0] == 2:
                raise _make_429(retry_after_body=1.5)
            return _mock_resp({"id": "msg-ok"})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with mock.patch("time.sleep") as mock_sleep:
                result = self._notifier().send("blocker", "hello", plan_slug="p", blocker_id=None)

        mock_sleep.assert_called_once_with(1.5)
        self.assertEqual(result, "msg-ok")

    # --- Cross-process cache ------------------------------------------------

    def test_dm_state_cache_persisted_across_processes(self):
        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if "/users/@me/channels" in req.full_url:
                return _mock_resp({"id": "dm-ch-cached"})
            return _mock_resp({"id": "msg-1"})

        # First instance writes cache
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self._notifier().send("blocker", "hello", plan_slug="p", blocker_id=None)
        self.assertEqual(call_count[0], 2)  # DM create + message

        # Second fresh instance reads cache — skips DM creation
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self._notifier().send("blocker", "hello2", plan_slug="p", blocker_id=None)
        self.assertEqual(call_count[0], 3)  # message only


if __name__ == "__main__":
    unittest.main()
