"""Phase 1 of `clu serve`: localhost dashboard server.

Covers the JSON shaping (`--no-transcript`), the bundled-page resource load,
the XSS-safe frontend invariant, and an ephemeral-port integration test of the
routing / Cache-Control / 500-on-exception / exact-match-routing contract.
"""

import json
import threading
import unittest
from http.client import HTTPConnection
from unittest import mock

from end_of_line import top, webserver
from tests import CluTestCase


class WorkersJsonTest(CluTestCase):
    """`workers_json` shapes `gather_rows` output for the wire."""

    def test_includes_transcript_fields_by_default(self):
        fake = [{"plan": "p", "last_command": "ls", "last_text": "hi", "last_write": "a.py"}]
        with mock.patch.object(top, "gather_rows", return_value=fake):
            payload = json.loads(webserver.workers_json())
        self.assertEqual(payload[0]["last_command"], "ls")
        self.assertEqual(payload[0]["last_text"], "hi")
        self.assertEqual(payload[0]["last_write"], "a.py")

    def test_no_transcript_omits_only_transcript_fields(self):
        fake = [
            {
                "plan": "p",
                "ran_seconds": 5,
                "last_command": "ls",
                "last_text": "hi",
                "last_write": "a.py",
            }
        ]
        with mock.patch.object(top, "gather_rows", return_value=fake):
            payload = json.loads(webserver.workers_json(include_transcript=False))
        row = payload[0]
        self.assertNotIn("last_command", row)
        self.assertNotIn("last_text", row)
        self.assertNotIn("last_write", row)
        # Non-transcript fields survive.
        self.assertEqual(row["ran_seconds"], 5)
        self.assertEqual(row["plan"], "p")

    def test_project_filter_is_forwarded(self):
        with mock.patch.object(top, "gather_rows", return_value=[]) as g:
            webserver.workers_json(project_filter="/some/proj")
        _, kwargs = g.call_args
        self.assertEqual(kwargs["project_filter"], "/some/proj")


class IndexResourceTest(CluTestCase):
    """The dashboard page is bundled and XSS-safe."""

    def test_load_index_html_returns_dashboard(self):
        html = webserver.load_index_html()
        self.assertIn("/api/workers", html)
        self.assertIn("END OF LINE", html)

    def test_frontend_escapes_worker_derived_strings(self):
        html = webserver.load_index_html()
        # An HTML-escape helper exists, and no worker-derived field is
        # raw-interpolated into innerHTML (the prototype's XSS vector).
        self.assertIn("function esc(", html)
        for raw in ("${w.say}", "${w.cmd}", "${w.wrote}", "${ww.cmd}", "${ww.say}"):
            self.assertNotIn(raw, html)

    def test_frontend_has_token_reducer_and_identity_key(self):
        html = webserver.load_index_html()
        # `tokens` arrives as the raw usage dict — it must be reduced to a
        # scalar, not rendered straight (which yields "[object Object]").
        self.assertIn("function tokenTotal(", html)
        # Selection is re-resolved by worker identity across polls, so a
        # worker dropping out doesn't jump the highlight.
        self.assertIn("function wkey(", html)
        self.assertIn("findIndex", html)


class ServerIntegrationTest(CluTestCase):
    """Ephemeral-port server in a daemon thread — routing + headers + 500."""

    def setUp(self):
        super().setUp()
        self._gpatch = mock.patch.object(top, "gather_rows", return_value=[])
        self.gather = self._gpatch.start()
        self.addCleanup(self._gpatch.stop)
        self.httpd = webserver.build_server("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        # LIFO cleanup: join (registered first, runs last) ← server_close ← shutdown.
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def _get(self, path):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp, body

    def test_root_serves_dashboard(self):
        resp, body = self._get("/")
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", resp.getheader("Content-Type"))
        self.assertIn(b"END OF LINE", body)

    def test_api_workers_json_with_no_store(self):
        self.gather.return_value = [{"plan": "p", "phase_id": "impl"}]
        resp, body = self._get("/api/workers")
        self.assertEqual(resp.status, 200)
        self.assertIn("application/json", resp.getheader("Content-Type"))
        self.assertEqual(resp.getheader("Cache-Control"), "no-store")
        self.assertEqual(json.loads(body), [{"plan": "p", "phase_id": "impl"}])

    def test_unknown_path_is_404(self):
        resp, _ = self._get("/nope")
        self.assertEqual(resp.status, 404)

    def test_path_traversal_is_not_served(self):
        # Exact-match routing only — never a directory handler.
        resp, _ = self._get("/../../../etc/passwd")
        self.assertEqual(resp.status, 404)

    def test_gather_rows_exception_yields_500_and_server_survives(self):
        self.gather.side_effect = RuntimeError("corrupt registry")
        resp, _ = self._get("/api/workers")
        self.assertEqual(resp.status, 500)
        # Server thread did not die: a later request still succeeds.
        self.gather.side_effect = None
        resp2, body2 = self._get("/")
        self.assertEqual(resp2.status, 200)
        self.assertIn(b"END OF LINE", body2)


class CmdServeWiringTest(CluTestCase):
    """`clu serve` parses and forwards flags to `webserver.serve`."""

    def test_dispatch_passes_flags(self):
        from end_of_line import cli
        from end_of_line import webserver as ws

        with mock.patch.object(ws, "serve", return_value=0) as m:
            rc = cli.main(["serve", "--port", "9999", "--no-transcript"])
        self.assertEqual(rc, 0)
        _, kwargs = m.call_args
        self.assertEqual(kwargs["port"], 9999)
        self.assertFalse(kwargs["include_transcript"])

    def test_bind_error_dies_cleanly(self):
        from end_of_line import cli
        from end_of_line import webserver as ws

        with mock.patch.object(ws, "serve", side_effect=OSError("address in use")):
            rc = cli.main(["serve"])
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
