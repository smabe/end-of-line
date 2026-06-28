"""`clu serve` — localhost dashboard server + the `--lan` security layer.

Phase 1 covers JSON shaping (`--no-transcript`), the bundled-page resource
load, the XSS-safe frontend invariant, and routing / headers / 500-on-exception
via an ephemeral-port integration test. Phase 2 covers the Host-header
allowlist (DNS-rebinding defense), token auth (cookie + Bearer), token storage,
LAN-IP detection, the `build_config` security policy + guardrail, and TLS.
"""

import json
import re
import ssl
import threading
import time
import unittest
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from end_of_line import registry, top, webserver
from end_of_line.webserver import ServeConfig
from tests import CluTestCase, GitProjectTestCase, must
from tests.test_top import _asst, _tool_result, _write_jsonl


class _ServerCase(CluTestCase):
    """Boots a webserver from a ServeConfig on an ephemeral port (127.0.0.1:0)
    in a daemon thread, with `gather_rows` mocked to an empty fleet."""

    def _boot(self, cfg, *, rows=None):
        self._gpatch = mock.patch.object(top, "gather_rows", return_value=rows or [])
        self.gather = self._gpatch.start()
        self.addCleanup(self._gpatch.stop)
        httpd = webserver.build_server(cfg)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        # LIFO cleanup: join (first) runs last ← server_close ← shutdown.
        self.addCleanup(thread.join, 2)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        thread.start()
        self._tls = cfg.tls is not None
        return httpd.server_address[1]

    def _get(self, port, path, *, headers=None):
        if self._tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            conn = HTTPSConnection("127.0.0.1", port, timeout=5, context=ctx)
        else:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp, body

    def _raw(self, port, request_bytes, *, tls=False):
        """Send raw bytes (e.g. an HTTP/1.0 request with no Host header, or a
        plaintext probe at a TLS port) and return the first response line."""
        import socket as _socket

        sock = _socket.create_connection(("127.0.0.1", port), timeout=5)
        if tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(sock)
        data = b""
        try:
            sock.sendall(request_bytes)
            while b"\r\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        except OSError:
            # A rejected handshake / reset connection is an expected outcome for
            # a malformed probe — the caller asserts on server survival instead.
            pass
        finally:
            sock.close()
        return data.split(b"\r\n", 1)[0]


# --------------------------------------------------------------------------- #
# Phase 1 — data shaping + bundled page
# --------------------------------------------------------------------------- #
class WorkersJsonTest(CluTestCase):
    def test_includes_transcript_fields_by_default(self):
        fake = [{"plan": "p", "last_command": "ls", "last_text": "hi", "last_write": "a.py"}]
        with mock.patch.object(top, "gather_rows", return_value=fake):
            payload = json.loads(webserver.workers_json())
        self.assertEqual(payload[0]["last_command"], "ls")
        self.assertEqual(payload[0]["last_text"], "hi")
        self.assertEqual(payload[0]["last_write"], "a.py")

    def test_no_transcript_omits_only_transcript_fields(self):
        fake = [
            {"plan": "p", "ran_seconds": 5, "last_command": "ls", "last_text": "hi", "last_write": "a.py"}
        ]
        with mock.patch.object(top, "gather_rows", return_value=fake):
            payload = json.loads(webserver.workers_json(include_transcript=False))
        row = payload[0]
        self.assertNotIn("last_command", row)
        self.assertNotIn("last_text", row)
        self.assertNotIn("last_write", row)
        self.assertEqual(row["ran_seconds"], 5)
        self.assertEqual(row["plan"], "p")

    def test_project_filter_is_forwarded(self):
        with mock.patch.object(top, "gather_rows", return_value=[]) as g:
            webserver.workers_json(project_filter=Path("/some/proj"))
        _, kwargs = g.call_args
        self.assertEqual(kwargs["project_filter"], Path("/some/proj"))


class IndexResourceTest(CluTestCase):
    def test_load_index_html_returns_dashboard(self):
        html = webserver.load_index_html()
        self.assertIn("/api/workers", html)
        self.assertIn("END OF LINE", html)

    def test_load_apple_icon_returns_png_bytes(self):
        data = webserver.load_apple_icon()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_frontend_renders_phase_progress(self):
        # #86 — the detail pane + list rows display phase position / attempts /
        # lease from the toView keys, via a segment-strip helper.
        html = webserver.load_index_html()
        self.assertIn("function phaseSteps(", html)
        self.assertIn("function rowPhase(", html)
        self.assertIn("w.phaseIndex", html)
        self.assertIn("w.leaseRemaining", html)
        self.assertIn(".steps", html)  # the segment-strip CSS

    def test_frontend_renders_blocked_state(self):
        # clu-dashboard-blocked / phase serve — a plan waiting on the operator
        # (claimless, `clu block` released the claim) renders an amber blocked
        # row: a BLK badge + the blocker question + a blocked count in the
        # header, off the gather_rows wire keys (blocked / blocker_question /
        # blocked_seconds, append-only D10).
        html = webserver.load_index_html()
        self.assertIn("r.blocked", html)           # toView reads the discriminator
        self.assertIn("blockerQuestion", html)     # view-model carries the question
        self.assertIn("blockedSeconds", html)      # ... and the blocked-since age
        self.assertIn(".dot.blocked", html)        # amber dot, distinct from .dot.dead
        self.assertIn("— blocked ${age(w.blockedSeconds)} —", html)  # blocked-since in metrics
        self.assertIn("w.blocked", html)           # render + header count gate on the flag
        self.assertIn("blocked</span>", html)      # header builds an N blocked count

    def test_frontend_has_activity_feed(self):
        # serve-activity-feed — the detail pane carries a capped, sticky-scroll
        # scrollback of the selected worker's transcript events, polled from the
        # /api/feed cursor endpoint. Substring guards in the blocked-row style.
        html = webserver.load_index_html()
        self.assertIn("/api/feed", html)             # polls the cursor endpoint
        self.assertIn('id="feedlog"', html)          # feed container in the shell
        self.assertIn("function feedStuck(", html)   # sticky-scroll bottom check
        self.assertIn("FEED_CAP = 1000", html)       # capped scrollback + DOM prune
        self.assertIn("${esc(e.text)}", html)        # feed strings escaped
        # pollFeed re-checks the log element after its awaits: buildShell can
        # null feedLogRef mid-fetch (view switch), and appending to a dead ref
        # would throw — the stale response must be dropped instead.
        self.assertIn("if (!feedLogRef) return", html)

    def test_frontend_scales_ui_above_browser_default(self):
        # The dashboard renders larger than browser 100% by default (readability)
        # via a tweakable --ui-scale zoom, not per-element font bumps.
        html = webserver.load_index_html()
        self.assertIn("--ui-scale:1.25", html)
        self.assertIn("zoom:var(--ui-scale)", html)

    def test_frontend_escapes_worker_derived_strings(self):
        html = webserver.load_index_html()
        self.assertIn("function esc(", html)
        for raw in ("${w.say}", "${w.cmd}", "${w.wrote}", "${ww.cmd}", "${ww.say}"):
            self.assertNotIn(raw, html)

    def test_frontend_has_token_reducer_and_identity_key(self):
        html = webserver.load_index_html()
        self.assertIn("function tokenTotal(", html)
        self.assertIn("function wkey(", html)
        self.assertIn("findIndex", html)

    def test_frontend_avoids_continuous_gpu_compositing(self):
        html = webserver.load_index_html()
        # Strip /* */ block comments first so the guard asserts on real CSS
        # declarations, not on explanatory prose that mentions these properties.
        code = re.sub(r"/\*.*?\*/", "", html, flags=re.DOTALL)
        # Both force a full-viewport GPU recomposite every frame, forever — the
        # dashboard's idle-cost hog.
        self.assertNotIn("backdrop-filter:blur(", code)
        self.assertNotIn("mix-blend-mode:", code)
        # Work is gated on tab visibility, and motion is opt-out-able.
        self.assertIn("visibilitychange", html)
        self.assertIn("prefers-reduced-motion", html)


class ServerIntegrationTest(_ServerCase):
    def setUp(self):
        super().setUp()
        self.port = self._boot(ServeConfig(host="127.0.0.1", port=0))

    def test_root_serves_dashboard(self):
        resp, body = self._get(self.port, "/")
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", must(resp.getheader("Content-Type")))
        self.assertIn(b"END OF LINE", body)

    def test_api_workers_json_with_no_store(self):
        self.gather.return_value = [{"plan": "p", "phase_id": "impl"}]
        resp, body = self._get(self.port, "/api/workers")
        self.assertEqual(resp.status, 200)
        self.assertIn("application/json", must(resp.getheader("Content-Type")))
        self.assertEqual(resp.getheader("Cache-Control"), "no-store")
        self.assertEqual(json.loads(body), [{"plan": "p", "phase_id": "impl"}])

    def test_unknown_path_is_404(self):
        resp, _ = self._get(self.port, "/nope")
        self.assertEqual(resp.status, 404)

    def test_path_traversal_is_not_served(self):
        resp, _ = self._get(self.port, "/../../../etc/passwd")
        self.assertEqual(resp.status, 404)

    def test_gather_rows_exception_yields_500_and_server_survives(self):
        self.gather.side_effect = RuntimeError("corrupt registry")
        resp, _ = self._get(self.port, "/api/workers")
        self.assertEqual(resp.status, 500)
        self.gather.side_effect = None
        resp2, body2 = self._get(self.port, "/")
        self.assertEqual(resp2.status, 200)
        self.assertIn(b"END OF LINE", body2)

    def test_apple_touch_icon_served(self):
        resp, body = self._get(self.port, "/apple-touch-icon.png")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "image/png")
        self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))


# --------------------------------------------------------------------------- #
# Phase 2 — Host-header allowlist (DNS-rebinding defense), lands FIRST
# --------------------------------------------------------------------------- #
class HostAllowlistTest(_ServerCase):
    def setUp(self):
        super().setUp()
        # No token here so the Host gate is tested in isolation from auth.
        self.port = self._boot(ServeConfig(host="127.0.0.1", port=0))

    def test_foreign_host_rejected_421(self):
        resp, _ = self._get(self.port, "/", headers={"Host": "evil.example.com"})
        self.assertEqual(resp.status, 421)

    def test_loopback_host_allowed(self):
        for host in ("localhost", "127.0.0.1", f"localhost:{self.port}", "[::1]"):
            resp, _ = self._get(self.port, "/", headers={"Host": host})
            self.assertEqual(resp.status, 200, f"Host {host!r} should be allowed")

    def test_missing_host_allowed(self):
        # http.client always sends Host unless skipped; simulate absent Host by
        # checking the gate predicate directly.
        cfg = ServeConfig(host="192.168.1.5", port=0)
        handler = webserver.make_handler(index_html="x", cfg=cfg)
        # A bind IP is in its own allowlist; a foreign host is not.
        self.assertIn("192.168.1.5", cfg.host_allowlist)
        self.assertNotIn("evil.example.com", cfg.host_allowlist)

    def test_host_gate_precedes_auth(self):
        # With a token set, a foreign Host still gets 421 (not 401) — the
        # rebinding check runs first.
        port = self._boot(ServeConfig(host="127.0.0.1", port=0, token="sekret"))
        resp, _ = self._get(port, "/", headers={"Host": "evil.example.com"})
        self.assertEqual(resp.status, 421)

    def test_absent_host_allowed_on_unauthenticated_loopback(self):
        # The loopback default (no token) tolerates a Host-less HTTP/1.0 request.
        line = self._raw(self.port, b"GET / HTTP/1.0\r\n\r\n")
        self.assertIn(b"200", line)

    def test_absent_host_rejected_on_exposed_bind(self):
        # A tokened (exposed) server rejects a missing Host with 421 — browsers
        # always send one, so this only blocks rebinding/probe traffic.
        port = self._boot(ServeConfig(host="127.0.0.1", port=0, token="sekret"))
        line = self._raw(port, b"GET / HTTP/1.0\r\n\r\n")
        self.assertIn(b"421", line)


# --------------------------------------------------------------------------- #
# Phase 2 — token auth (cookie + Bearer)
# --------------------------------------------------------------------------- #
class AuthTest(_ServerCase):
    TOKEN = "s3kret-token"

    def setUp(self):
        super().setUp()
        self.port = self._boot(ServeConfig(host="127.0.0.1", port=0, token=self.TOKEN))

    def test_unauthenticated_request_401(self):
        resp, _ = self._get(self.port, "/")
        self.assertEqual(resp.status, 401)

    def test_apple_touch_icon_served_before_auth_gate(self):
        # The icon is a static, non-sensitive asset served before the auth gate,
        # so a browser favicon / iOS home-screen fetch carrying no token still
        # gets it (200) — unlike the 401 protecting "/" and "/api/workers".
        resp, body = self._get(self.port, "/apple-touch-icon.png")
        self.assertEqual(resp.status, 200)
        self.assertTrue(body.startswith(b"\x89PNG"))

    def test_login_valid_token_sets_cookie_and_redirects(self):
        resp, _ = self._get(self.port, f"/login?token={self.TOKEN}")
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.getheader("Location"), "/")
        cookie = must(resp.getheader("Set-Cookie"))
        self.assertIn(f"{webserver.COOKIE_NAME}={self.TOKEN}", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/", cookie)
        # Plaintext server → no Secure attribute.
        self.assertNotIn("Secure", cookie)

    def test_login_bad_token_401_no_cookie(self):
        resp, _ = self._get(self.port, "/login?token=wrong")
        self.assertEqual(resp.status, 401)
        self.assertIsNone(resp.getheader("Set-Cookie"))

    def test_cookie_authorizes_request(self):
        resp, body = self._get(
            self.port, "/", headers={"Cookie": f"{webserver.COOKIE_NAME}={self.TOKEN}"}
        )
        self.assertEqual(resp.status, 200)
        self.assertIn(b"END OF LINE", body)

    def test_bearer_authorizes_request(self):
        resp, _ = self._get(
            self.port, "/api/workers", headers={"Authorization": f"Bearer {self.TOKEN}"}
        )
        self.assertEqual(resp.status, 200)

    def test_bad_bearer_401(self):
        resp, _ = self._get(
            self.port, "/api/workers", headers={"Authorization": "Bearer nope"}
        )
        self.assertEqual(resp.status, 401)


# --------------------------------------------------------------------------- #
# Phase 2 — token storage
# --------------------------------------------------------------------------- #
class TokenStorageTest(CluTestCase):
    def test_create_persists_and_reuses(self):
        first = webserver.load_or_create_token()
        self.assertTrue(first)
        self.assertTrue(webserver.token_path().exists())
        second = webserver.load_or_create_token()
        self.assertEqual(first, second)  # reused, not regenerated

    def test_token_file_is_0600(self):
        webserver.load_or_create_token()
        mode = webserver.token_path().stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


# --------------------------------------------------------------------------- #
# Phase 2 — LAN-IP detection
# --------------------------------------------------------------------------- #
class LanIpTest(CluTestCase):
    def test_detect_returns_socket_name(self):
        fake = mock.MagicMock()
        fake.getsockname.return_value = ("192.168.7.7", 0)
        with mock.patch("socket.socket", return_value=fake):
            self.assertEqual(webserver.detect_lan_ip(), "192.168.7.7")
        fake.close.assert_called_once()

    def test_no_network_raises_configerror(self):
        fake = mock.MagicMock()
        fake.connect.side_effect = OSError("no route")
        with mock.patch("socket.socket", return_value=fake):
            with self.assertRaises(webserver.ConfigError):
                webserver.detect_lan_ip()


# --------------------------------------------------------------------------- #
# Phase 2 — build_config security policy
# --------------------------------------------------------------------------- #
class BuildConfigTest(CluTestCase):
    def test_localhost_default_no_token_no_tls(self):
        cfg = webserver.build_config(port=9000)
        self.assertEqual(cfg.host, "127.0.0.1")
        self.assertIsNone(cfg.token)
        self.assertIsNone(cfg.tls)

    def test_lan_provisions_token_and_tls(self):
        with mock.patch.object(webserver, "detect_lan_ip", return_value="192.168.4.4"):
            cfg = webserver.build_config(lan=True, port=9000)
        self.assertEqual(cfg.host, "192.168.4.4")
        self.assertTrue(cfg.token)
        self.assertIsNotNone(cfg.tls)  # auto self-signed
        self.assertIn("192.168.4.4", cfg.host_allowlist)

    def test_explicit_non_loopback_host_gets_token(self):
        # --host on a LAN IP (without --lan) is still exposed → must get a token.
        cfg = webserver.build_config(host="192.168.9.9", port=9000)
        self.assertTrue(cfg.token)
        self.assertIsNotNone(cfg.tls)

    def test_lan_http_is_cleartext_but_still_tokened(self):
        with mock.patch.object(webserver, "detect_lan_ip", return_value="192.168.4.4"):
            cfg = webserver.build_config(lan=True, http=True, port=9000)
        self.assertTrue(cfg.token)
        self.assertIsNone(cfg.tls)  # --http opted out of HTTPS

    def test_guardrail_refuses_non_loopback_without_token(self):
        # If token provisioning yields nothing, a non-loopback bind must refuse.
        with mock.patch.object(webserver, "load_or_create_token", return_value=None):
            with self.assertRaises(webserver.ConfigError):
                webserver.build_config(host="192.168.9.9", port=9000)

    def test_cert_without_key_refused(self):
        with self.assertRaises(webserver.ConfigError):
            webserver.build_config(host="127.0.0.1", cert="/tmp/c.pem")

    def test_loopback_alias_127_0_0_2_not_exposed(self):
        # A 127.x alias is loopback → no token / no TLS forced on it.
        cfg = webserver.build_config(host="127.0.0.2", port=9000)
        self.assertIsNone(cfg.token)
        self.assertIsNone(cfg.tls)

    def test_http_with_cert_is_refused(self):
        with self.assertRaises(webserver.ConfigError):
            webserver.build_config(
                host="127.0.0.1", http=True, cert="/tmp/c.pem", key="/tmp/k.pem"
            )


# --------------------------------------------------------------------------- #
# Phase 2 — TLS (real openssl mint + ssl wrap)
# --------------------------------------------------------------------------- #
class TlsTest(_ServerCase):
    def test_https_server_serves_dashboard(self):
        cert, key = webserver.cert_paths()
        webserver.ensure_self_signed("127.0.0.1", cert, key)
        ctx = webserver.build_tls_context(cert, key)
        port = self._boot(ServeConfig(host="127.0.0.1", port=0, tls=ctx))
        resp, body = self._get(port, "/")
        self.assertEqual(resp.status, 200)
        self.assertIn(b"END OF LINE", body)

    def test_plain_http_to_tls_port_does_not_kill_server(self):
        cert, key = webserver.cert_paths()
        webserver.ensure_self_signed("127.0.0.1", cert, key)
        ctx = webserver.build_tls_context(cert, key)
        port = self._boot(ServeConfig(host="127.0.0.1", port=0, tls=ctx))
        # A plaintext probe fails the handshake server-side (silently).
        self._raw(port, b"GET / HTTP/1.0\r\n\r\n", tls=False)
        # The server must still serve a proper HTTPS request afterward.
        resp, body = self._get(port, "/")
        self.assertEqual(resp.status, 200)
        self.assertIn(b"END OF LINE", body)

    def test_minted_key_is_0600(self):
        cert, key = webserver.cert_paths()
        webserver.ensure_self_signed("127.0.0.1", cert, key)
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)

    def test_cert_reused_not_regenerated(self):
        cert, key = webserver.cert_paths()
        webserver.ensure_self_signed("127.0.0.1", cert, key)
        before = cert.read_bytes()
        # Second call must NOT shell out to openssl again.
        with mock.patch.object(webserver, "_openssl_addext") as mocked:
            webserver.ensure_self_signed("127.0.0.1", cert, key)
            mocked.assert_not_called()
        self.assertEqual(cert.read_bytes(), before)

    def test_san_contains_bind_ip(self):
        cert, key = webserver.cert_paths()
        webserver.ensure_self_signed("127.0.0.1", cert, key)
        import subprocess

        out = subprocess.run(
            ["openssl", "x509", "-in", str(cert), "-noout", "-ext", "subjectAltName"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("127.0.0.1", out)
        self.assertIn("localhost", out)

    def test_addext_fallback_path_produces_valid_san(self):
        # Exercise the -addext-less fallback directly (valid on OpenSSL 3.x too).
        cert, key = webserver.cert_paths()
        cert.parent.mkdir(parents=True, exist_ok=True)
        webserver._openssl_config("IP:10.0.0.5,DNS:localhost", cert, key)
        import subprocess

        out = subprocess.run(
            ["openssl", "x509", "-in", str(cert), "-noout", "-ext", "subjectAltName"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("10.0.0.5", out)

    def test_ensure_self_signed_falls_back_when_addext_unsupported(self):
        cert, key = webserver.cert_paths()
        with mock.patch.object(
            webserver, "_openssl_addext", side_effect=webserver._AddextUnsupported()
        ), mock.patch.object(webserver, "_openssl_config") as fallback:
            webserver.ensure_self_signed("127.0.0.1", cert, key)
            fallback.assert_called_once()


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #
class CmdServeWiringTest(CluTestCase):
    def test_dispatch_builds_config_and_serves(self):
        from end_of_line import cli
        from end_of_line import webserver as ws

        with mock.patch.object(ws, "serve", return_value=0) as m:
            rc = cli.main(["serve", "--port", "9999", "--no-transcript"])
        self.assertEqual(rc, 0)
        cfg = m.call_args[0][0]
        self.assertEqual(cfg.port, 9999)
        self.assertFalse(cfg.include_transcript)
        self.assertIsNone(cfg.token)  # localhost default

    def test_config_error_dies_cleanly(self):
        from end_of_line import cli
        from end_of_line import webserver as ws

        with mock.patch.object(ws, "build_config", side_effect=ws.ConfigError("nope")):
            rc = cli.main(["serve"])
        self.assertNotEqual(rc, 0)

    def test_bind_error_dies_cleanly(self):
        from end_of_line import cli
        from end_of_line import webserver as ws

        with mock.patch.object(ws, "serve", side_effect=OSError("address in use")):
            rc = cli.main(["serve"])
        self.assertNotEqual(rc, 0)


# --------------------------------------------------------------------------- #
# serve-activity-feed — /api/feed cursor window reader
# --------------------------------------------------------------------------- #
class FeedWindowTest(CluTestCase):
    def _file(self, content: bytes) -> Path:
        path = self.tmp_path / "t.jsonl"
        path.write_bytes(content)
        return path

    def _line(self, i: int) -> bytes:
        return json.dumps({"type": "assistant", "n": i}).encode() + b"\n"

    def test_first_poll_backfills_and_advances_to_eof(self):
        f = self._file(self._line(1) + self._line(2))
        records, cursor, reset = webserver.read_feed_window(f, -1)
        self.assertEqual([r["n"] for r in records], [1, 2])
        self.assertEqual(cursor, f.stat().st_size)
        self.assertFalse(reset)

    def test_incremental_poll_returns_only_new_records(self):
        f = self._file(self._line(1))
        _, cursor, _ = webserver.read_feed_window(f, -1)
        with open(f, "ab") as fh:
            fh.write(self._line(2) + self._line(3))
        records, cursor2, reset = webserver.read_feed_window(f, cursor)
        self.assertEqual([r["n"] for r in records], [2, 3])
        self.assertEqual(cursor2, f.stat().st_size)
        self.assertFalse(reset)

    def test_partial_final_line_is_carried_not_consumed(self):
        # Writer mid-append: consume only to the last \n; the partial tail is
        # re-read (whole) on the next poll once the writer finishes the line.
        partial = b'{"type": "assistant", "n": 2'
        f = self._file(self._line(1) + partial)
        records, cursor, _ = webserver.read_feed_window(f, -1)
        self.assertEqual([r["n"] for r in records], [1])
        self.assertEqual(cursor, len(self._line(1)))
        with open(f, "ab") as fh:
            fh.write(b', "x": 1}\n')
        records2, cursor2, _ = webserver.read_feed_window(f, cursor)
        self.assertEqual([r["n"] for r in records2], [2])
        self.assertEqual(cursor2, f.stat().st_size)

    def test_idle_poll_at_eof_returns_nothing(self):
        f = self._file(self._line(1))
        size = f.stat().st_size
        records, cursor, reset = webserver.read_feed_window(f, size)
        self.assertEqual(records, [])
        self.assertEqual(cursor, size)
        self.assertFalse(reset)

    def test_shrunken_file_resets_and_backfills(self):
        # st_size < cursor → rotation/truncation (new attempt reusing the
        # filename): reset + fresh backfill.
        f = self._file(self._line(1))
        records, cursor, reset = webserver.read_feed_window(f, f.stat().st_size + 999)
        self.assertEqual([r["n"] for r in records], [1])
        self.assertTrue(reset)
        self.assertEqual(cursor, f.stat().st_size)

    def test_backfill_into_large_file_starts_at_a_record_boundary(self):
        # First poll against a transcript larger than the backfill window must
        # drop the partial first line in the window — every record parses whole.
        lines = [json.dumps({"type": "assistant", "pad": "x" * 1024, "n": i}) for i in range(100)]
        f = self._file(("\n".join(lines) + "\n").encode())
        self.assertGreater(f.stat().st_size, webserver.FEED_BACKFILL_BYTES)
        records, cursor, reset = webserver.read_feed_window(f, -1)
        self.assertFalse(reset)
        self.assertTrue(records)
        self.assertTrue(all("n" in r for r in records))
        self.assertLess(len(records), 100)  # window-bounded, not the whole file
        self.assertEqual(cursor, f.stat().st_size)

    def test_unparseable_line_skipped_but_consumed(self):
        f = self._file(self._line(1) + b"not json\n" + self._line(2))
        records, cursor, _ = webserver.read_feed_window(f, -1)
        self.assertEqual([r["n"] for r in records], [1, 2])
        self.assertEqual(cursor, f.stat().st_size)


# --------------------------------------------------------------------------- #
# serve-activity-feed — transcript record → feed events
# --------------------------------------------------------------------------- #
class RecordEventsTest(CluTestCase):
    def test_assistant_text_becomes_say(self):
        events = webserver.record_events(_asst(text="checking tests"))
        self.assertEqual(
            events,
            [{"ts": "2026-06-03T00:00:00Z", "kind": "say", "text": "checking tests"}],
        )

    def test_bash_tool_use_becomes_tool(self):
        events = webserver.record_events(_asst(tool="Bash", tool_input={"command": "pytest -q"}))
        self.assertEqual(events, [{"ts": "2026-06-03T00:00:00Z", "kind": "tool", "text": "pytest -q"}])

    def test_write_tool_use_becomes_write(self):
        events = webserver.record_events(_asst(tool="Write", tool_input={"file_path": "/r/x.py"}))
        self.assertEqual(events[0]["kind"], "write")
        self.assertEqual(events[0]["text"], "/r/x.py")

    def test_tool_result_becomes_result(self):
        events = webserver.record_events(_tool_result("tu1"))  # string content "ok"
        self.assertEqual(events, [{"ts": "2026-06-03T00:00:01Z", "kind": "result", "text": "ok"}])

    def test_agent_tool_use_becomes_agent(self):
        ev = webserver.record_events(_asst(tool="Task", tool_input={
            "subagent_type": "Explore", "description": "map code"}))
        self.assertEqual(ev[0]["kind"], "agent")
        self.assertEqual(ev[0]["text"], "Explore")

    def test_agent_tool_use_description_fallback(self):
        ev = webserver.record_events(_asst(tool="Agent", tool_input={"description": "review the diff"}))
        self.assertEqual([(e["kind"], e["text"]) for e in ev], [("agent", "review the diff")])

    def test_tool_result_block_list_content_flattened(self):
        rec = _tool_result("tu1")
        rec["message"]["content"][0]["content"] = [
            {"type": "text", "text": "12 passed"},
            {"type": "text", "text": "0 failed"},
        ]
        events = webserver.record_events(rec)
        self.assertEqual(events[0]["text"], "12 passed 0 failed")

    def test_multiple_blocks_keep_order(self):
        rec = _asst(text="say first", tool="Bash", tool_input={"command": "ls"})
        self.assertEqual([e["kind"] for e in webserver.record_events(rec)], ["say", "tool"])

    def test_event_text_truncated_at_cap(self):
        text = webserver.record_events(_asst(text="y" * 5000))[0]["text"]
        self.assertEqual(len(text), webserver.FEED_TEXT_CAP)
        self.assertTrue(text.endswith("…"))

    def test_empty_or_garbage_records_yield_nothing(self):
        self.assertEqual(webserver.record_events({"type": "other"}), [])
        self.assertEqual(webserver.record_events("not a dict"), [])
        self.assertEqual(webserver.record_events(_asst(text="   ")), [])


# --------------------------------------------------------------------------- #
# serve-activity-feed — worker → transcript resolution (registry path)
# --------------------------------------------------------------------------- #
class ResolveFeedTranscriptTest(GitProjectTestCase):
    """registry entry → live claim → worktree cwd → locate_transcript — the
    same resolution path gather_rows uses, keyed by (proj name, plan, phase)."""

    def setUp(self) -> None:
        super().setUp()
        self._pr = TemporaryDirectory()
        self.addCleanup(self._pr.cleanup)
        self.projects_root = Path(self._pr.name)
        self.reg_root = registry.entries()[0].project_root
        self.proj = Path(self.reg_root).name

    def _transcript(self, name: str = "sess") -> Path:
        d = self.projects_root / top.encode_project_dir(self.reg_root)
        return _write_jsonl(d / f"{name}.jsonl", [_asst(cwd=self.reg_root)], mtime=1000)

    def test_resolves_live_claim_to_transcript_and_tid(self):
        self._claim("a")
        path = self._transcript()
        resolved = webserver.resolve_feed_transcript(
            "test-plan", self.proj, "a", projects_root=self.projects_root
        )
        self.assertEqual(resolved, (path, "sess"))

    def test_unknown_plan_or_project_is_none(self):
        self._claim("a")
        self._transcript()
        self.assertIsNone(
            webserver.resolve_feed_transcript("nope", self.proj, "a", projects_root=self.projects_root)
        )
        self.assertIsNone(
            webserver.resolve_feed_transcript("test-plan", "other-proj", "a", projects_root=self.projects_root)
        )

    def test_phase_mismatch_is_none(self):
        # The claim moved on (new phase = new session): the client's selection
        # is stale, so the feed 404s rather than serving another phase's tail.
        self._claim("a")
        self._transcript()
        self.assertIsNone(
            webserver.resolve_feed_transcript("test-plan", self.proj, "b", projects_root=self.projects_root)
        )

    def test_no_claim_is_none(self):
        self._transcript()
        self.assertIsNone(
            webserver.resolve_feed_transcript("test-plan", self.proj, "a", projects_root=self.projects_root)
        )

    def test_no_transcript_is_none(self):
        self._claim("a")
        self.assertIsNone(
            webserver.resolve_feed_transcript("test-plan", self.proj, "a", projects_root=self.projects_root)
        )

    def test_project_filter_mismatch_is_none(self):
        self._claim("a")
        self._transcript()
        self.assertIsNone(
            webserver.resolve_feed_transcript(
                "test-plan", self.proj, "a",
                project_filter=self.tmp_path / "elsewhere",
                projects_root=self.projects_root,
            )
        )

    def _servable_sibling(self) -> tuple[str, Path]:
        """Register a second project with the same basename + plan slug under a
        different parent, claim phase 'a' on it, and give it a transcript.
        Returns (registry-recorded root, transcript path)."""
        from end_of_line import state as st
        from end_of_line.cli import main as cli_main
        from tests import DEFAULT_PLAN_BODY

        sibling = self.tmp_path / "elsewhere" / self.proj
        (sibling / "plans").mkdir(parents=True)
        (sibling / "plans" / "test-plan.md").write_text(DEFAULT_PLAN_BODY)
        self.assertEqual(cli_main(["init", "--project", str(sibling), "--plan", "test-plan"]), 0)
        root = must(
            next((e.project_root for e in registry.entries() if e.project_root != self.reg_root), None)
        )
        with st.mutate(sibling / "plans" / ".orchestrator" / "test-plan.state.json") as data:
            st.claim_phase(data, "a", lease_minutes=30)
        d = self.projects_root / top.encode_project_dir(root)
        return root, _write_jsonl(d / "sibling.jsonl", [_asst(cwd=root)], mtime=1000)

    def test_project_filter_scans_past_foreign_entries(self):
        # A --project-scoped server treats other registrations as nonexistent:
        # a same-named, fully servable project registered earlier (basename
        # collision) must not shadow the in-filter one — the scan continues
        # past filtered-out entries instead of 404ing on the first name match.
        self._claim("a")
        self._transcript()
        root, path = self._servable_sibling()
        resolved = webserver.resolve_feed_transcript(
            "test-plan", self.proj, "a",
            project_filter=Path(root),
            projects_root=self.projects_root,
        )
        self.assertEqual(resolved, (path, "sibling"))

    def test_scan_continues_past_entries_that_cannot_serve(self):
        # Per-entry resilience, like gather_rows: an earlier same-named entry
        # whose claim is on another phase is a skip, not a dead end — the row
        # the client clicked is whichever entry CAN serve that (proj, plan,
        # phase) identity, since rows with identical identity are
        # indistinguishable client-side anyway.
        self._claim("b")
        root, path = self._servable_sibling()
        resolved = webserver.resolve_feed_transcript(
            "test-plan", self.proj, "a", projects_root=self.projects_root
        )
        self.assertEqual(resolved, (path, "sibling"))


class ResolveSessionTranscriptTest(GitProjectTestCase):
    """resolve_session_transcript: a non-clu session resolves by (proj, sid),
    with the same cwd-confirm + sidechain rejection + project scoping as the
    worker feed — but EXACT (no newest-file fallback) and with no claim."""

    def setUp(self) -> None:
        super().setUp()
        self._pr = TemporaryDirectory()
        self.addCleanup(self._pr.cleanup)
        self.projects_root = Path(self._pr.name)
        self.reg_root = registry.entries()[0].project_root
        self.proj = Path(self.reg_root).name

    def _transcript(self, name: str, *, cwd: str | None = None, sidechain: bool = False,
                    mtime: float | None = None) -> Path:
        rec = _asst(cwd=cwd or self.reg_root)
        if sidechain:
            rec["isSidechain"] = True
        d = self.projects_root / top.encode_project_dir(self.reg_root)
        return _write_jsonl(d / f"{name}.jsonl", [rec],
                            mtime=mtime if mtime is not None else time.time())

    def test_resolves_session_to_transcript_and_tid(self) -> None:
        path = self._transcript("sess-xyz")
        self.assertEqual(
            webserver.resolve_session_transcript(
                self.proj, "sess-xyz", projects_root=self.projects_root
            ),
            (path, "sess-xyz"),
        )

    def test_unknown_project_is_none(self) -> None:
        self._transcript("sess-xyz")
        self.assertIsNone(webserver.resolve_session_transcript(
            "other-proj", "sess-xyz", projects_root=self.projects_root))

    def test_unknown_sid_does_not_fall_back_to_another_session(self) -> None:
        # A real, fresh session exists in the dir; asking for a DIFFERENT sid must
        # return None — never that session's tail (the locate_transcript fallback
        # would have leaked it).
        self._transcript("sess-xyz")
        self.assertIsNone(webserver.resolve_session_transcript(
            self.proj, "nope", projects_root=self.projects_root))

    def test_sidechain_sid_is_none(self) -> None:
        self._transcript("sub", sidechain=True)
        self.assertIsNone(webserver.resolve_session_transcript(
            self.proj, "sub", projects_root=self.projects_root))

    def test_stale_session_is_none(self) -> None:
        # The feed serves only sessions the dashboard would list — a transcript
        # idle past SESSION_FRESH_SECONDS resolves to nothing, same definition.
        self._transcript("old", mtime=time.time() - (top.SESSION_FRESH_SECONDS + 100))
        self.assertIsNone(webserver.resolve_session_transcript(
            self.proj, "old", projects_root=self.projects_root))

    def test_project_filter_mismatch_is_none(self) -> None:
        self._transcript("sess-xyz")
        self.assertIsNone(webserver.resolve_session_transcript(
            self.proj, "sess-xyz",
            project_filter=self.tmp_path / "elsewhere",
            projects_root=self.projects_root))


# --------------------------------------------------------------------------- #
# serve-activity-feed — /api/feed endpoint over a live server
# --------------------------------------------------------------------------- #
class FeedEndpointTest(_ServerCase):
    """Gate inheritance, param validation, and the cursor round-trip. The
    registry resolution is mocked (covered above); the transcript is real."""

    def setUp(self):
        super().setUp()
        self.port = self._boot(ServeConfig(host="127.0.0.1", port=0))
        self.transcript = self.tmp_path / "sess-1.jsonl"
        _write_jsonl(self.transcript, [
            _asst(text="starting work"),
            _asst(tool="Bash", tool_input={"command": "pytest -q"}),
        ])
        patcher = mock.patch.object(
            webserver, "resolve_feed_transcript", return_value=(self.transcript, "sess-1")
        )
        self.resolve = patcher.start()
        self.addCleanup(patcher.stop)
        spatcher = mock.patch.object(
            webserver, "resolve_session_transcript", return_value=(self.transcript, "sess-1")
        )
        self.resolve_session = spatcher.start()
        self.addCleanup(spatcher.stop)

    def _feed(self, query: str):
        """GET /api/feed?<query> → (resp, parsed body). Only 200 bodies are
        JSON; error-status tests assert on the code alone, so those get {}."""
        resp, body = self._get(self.port, f"/api/feed?{query}")
        data: dict = json.loads(body) if resp.status == 200 else {}
        return resp, data

    def test_backfill_on_first_poll(self):
        resp, data = self._feed("plan=p&proj=x&phase=a&cursor=-1")
        self.assertEqual(resp.status, 200)
        self.assertIn("application/json", must(resp.getheader("Content-Type")))
        self.assertEqual(resp.getheader("Cache-Control"), "no-store")
        self.assertEqual(
            [(e["kind"], e["text"]) for e in data["events"]],
            [("say", "starting work"), ("tool", "pytest -q")],
        )
        self.assertEqual(data["cursor"], self.transcript.stat().st_size)
        self.assertEqual(data["tid"], "sess-1")
        self.assertFalse(data["reset"])

    def test_incremental_poll_returns_only_new_events(self):
        _, first = self._feed("plan=p&proj=x&phase=a&cursor=-1")
        with open(self.transcript, "a") as fh:
            fh.write(json.dumps(_asst(text="now testing")) + "\n")
        _, data = self._feed(f"plan=p&proj=x&phase=a&cursor={first['cursor']}&tid=sess-1")
        self.assertEqual([e["text"] for e in data["events"]], ["now testing"])
        self.assertFalse(data["reset"])

    def test_tid_mismatch_resets_and_backfills(self):
        # New attempt = new session id: the client's scrollback is stale, so the
        # server ignores its cursor and backfills fresh with reset:true.
        _, first = self._feed("plan=p&proj=x&phase=a&cursor=-1")
        _, data = self._feed(f"plan=p&proj=x&phase=a&cursor={first['cursor']}&tid=old-attempt")
        self.assertTrue(data["reset"])
        self.assertEqual(len(data["events"]), 2)
        self.assertEqual(data["tid"], "sess-1")

    def test_shrunken_transcript_resets(self):
        _, data = self._feed("plan=p&proj=x&phase=a&cursor=999999&tid=sess-1")
        self.assertTrue(data["reset"])
        self.assertEqual(len(data["events"]), 2)

    def test_bad_plan_slug_400(self):
        resp, _ = self._feed("plan=..%2Fetc&proj=x&phase=a&cursor=-1")
        self.assertEqual(resp.status, 400)

    def test_bad_phase_slug_400(self):
        resp, _ = self._feed("plan=p&proj=x&phase=..%2Fetc&cursor=-1")
        self.assertEqual(resp.status, 400)

    def test_bad_cursor_400(self):
        resp, _ = self._feed("plan=p&proj=x&phase=a&cursor=abc")
        self.assertEqual(resp.status, 400)

    def test_unknown_plan_404(self):
        self.resolve.return_value = None
        resp, _ = self._feed("plan=p&proj=x&phase=a&cursor=-1")
        self.assertEqual(resp.status, 404)

    def test_vanished_transcript_404(self):
        self.transcript.unlink()
        resp, _ = self._feed("plan=p&proj=x&phase=a&cursor=-1")
        self.assertEqual(resp.status, 404)

    def test_no_transcript_config_404(self):
        # Privacy: --no-transcript means the feed route is simply not there —
        # the endpoint is 100% transcript-content data.
        port = self._boot(ServeConfig(host="127.0.0.1", port=0, include_transcript=False))
        resp, _ = self._get(port, "/api/feed?plan=p&proj=x&phase=a&cursor=-1")
        self.assertEqual(resp.status, 404)

    def test_unauthenticated_feed_401(self):
        # Gate-inheritance pin: /api/feed sits AFTER the auth gate, so a
        # tokened server never serves transcript content unauthenticated.
        port = self._boot(ServeConfig(host="127.0.0.1", port=0, token="sekret"))
        resp, _ = self._get(port, "/api/feed?plan=p&proj=x&phase=a&cursor=-1")
        self.assertEqual(resp.status, 401)

    def test_session_feed_routes_by_sid(self):
        # `sid` present routes the session resolver (no plan/phase needed) and
        # streams that session's transcript tail.
        resp, data = self._feed("proj=x&sid=sess-1&cursor=-1")
        self.assertEqual(resp.status, 200)
        self.assertTrue(self.resolve_session.called)
        self.assertFalse(self.resolve.called)  # NOT the claim path
        self.assertEqual(
            [(e["kind"], e["text"]) for e in data["events"]],
            [("say", "starting work"), ("tool", "pytest -q")],
        )
        self.assertEqual(data["tid"], "sess-1")

    def test_bad_sid_400(self):
        resp, _ = self._feed("proj=x&sid=..%2Fetc&cursor=-1")
        self.assertEqual(resp.status, 400)

    def test_unknown_sid_404(self):
        self.resolve_session.return_value = None
        resp, _ = self._feed("proj=x&sid=ghost&cursor=-1")
        self.assertEqual(resp.status, 404)

    def test_event_text_truncated_at_cap(self):
        _write_jsonl(self.transcript, [_asst(text="y" * 5000)])
        _, data = self._feed("plan=p&proj=x&phase=a&cursor=-1")
        self.assertEqual(len(data["events"][0]["text"]), webserver.FEED_TEXT_CAP)


if __name__ == "__main__":
    unittest.main()
