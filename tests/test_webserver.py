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
import unittest
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from unittest import mock

from end_of_line import top, webserver
from end_of_line.webserver import ServeConfig
from tests import CluTestCase, must


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


if __name__ == "__main__":
    unittest.main()
