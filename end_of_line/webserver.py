"""`clu serve` — self-hosted web dashboard for `clu top`.

Phase 1: localhost-only, read-only. Serves the bundled Tron "End of Line"
dashboard at `GET /` and the live worker rows at `GET /api/workers`
(`top.gather_rows()` as JSON, polled by the page every ~1.5s). The `--lan`
security layer (token auth, Host-header allowlist, auto self-signed TLS) lands
in a later phase; this module deliberately binds loopback only for now.

Design constraints baked in here, do not regress:
- **Exact-match routing** to one bundled file — never `SimpleHTTPRequestHandler`
  over a directory (that follows symlinks and serves the cwd).
- **`gather_rows` may raise** (corrupt registry): every request is wrapped so a
  failure yields 500 without killing the handler thread or the server.
- **Silenced request logging** — request lines can carry paths/tokens; they are
  never written to stderr.
- **`shutdown()` must fire off the `serve_forever` thread** or it deadlocks; the
  signal handler in `serve` spawns a one-shot thread for it.
"""

from __future__ import annotations

import importlib.resources
import json
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import top

# Worker-derived transcript fields dropped by `--no-transcript`. These are the
# semi-untrusted LLM/tool strings; omitting them yields a metrics-only feed.
_TRANSCRIPT_FIELDS = ("last_command", "last_text", "last_write")


def load_index_html() -> str:
    """Read the bundled dashboard page via `importlib.resources` (works in an
    editable checkout and inside a wheel, given the `web/*.html` package-data)."""
    return (
        importlib.resources.files("end_of_line")
        .joinpath("web", "index.html")
        .read_text(encoding="utf-8")
    )


def workers_json(*, project_filter: Path | None = None, include_transcript: bool = True) -> bytes:
    """`gather_rows()` shaped for the wire. With `include_transcript=False`,
    drop the transcript-derived fields so the feed carries metrics only."""
    rows = top.gather_rows(project_filter=project_filter)
    if not include_transcript:
        for row in rows:
            for field in _TRANSCRIPT_FIELDS:
                row.pop(field, None)
    return json.dumps(rows).encode("utf-8")


def make_handler(*, index_html: str, project_filter: Path | None, include_transcript: bool):
    """Build a `BaseHTTPRequestHandler` closed over the page + feed config."""
    page = index_html.encode("utf-8")

    class _Handler(BaseHTTPRequestHandler):
        # Silence default access logging: request lines could carry paths or a
        # `?token=` and must never reach stderr.
        def log_message(self, *args):  # noqa: D401 - silencing override
            return

        def do_HEAD(self):
            self._dispatch(head=True)

        def do_GET(self):
            self._dispatch(head=False)

        def _dispatch(self, *, head: bool) -> None:
            try:
                path = urlsplit(self.path).path
                if path == "/":
                    self._respond(200, page, "text/html; charset=utf-8", head=head)
                elif path == "/api/workers":
                    body = workers_json(
                        project_filter=project_filter,
                        include_transcript=include_transcript,
                    )
                    self._respond(
                        200,
                        body,
                        "application/json; charset=utf-8",
                        head=head,
                        extra={"Cache-Control": "no-store"},
                    )
                else:
                    self._respond(404, b"not found\n", "text/plain; charset=utf-8", head=head)
            except Exception:
                # Corrupt registry / transcript: 500, but keep the thread and
                # the server alive. Never leak the traceback to the client.
                try:
                    self._respond(
                        500,
                        b"internal server error\n",
                        "text/plain; charset=utf-8",
                        head=head,
                    )
                except Exception:
                    pass

        def _respond(self, code: int, body: bytes, ctype: str, *, head: bool, extra=None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if not head:
                self.wfile.write(body)

    return _Handler


class _Server(ThreadingHTTPServer):
    # Reuse the address so a quick restart after Ctrl-C doesn't hit a lingering
    # TIME_WAIT bind. Daemon request threads so a slow client can't block
    # shutdown.
    allow_reuse_address = True
    daemon_threads = True


def build_server(
    host: str,
    port: int,
    *,
    project_filter: Path | None = None,
    include_transcript: bool = True,
) -> _Server:
    """Construct (and bind) the server. Raises `OSError` on a bind failure
    (e.g. EADDRINUSE) — the caller turns that into a clean exit. Kept separate
    from `serve` so tests can drive it without installing signal handlers."""
    handler = make_handler(
        index_html=load_index_html(),
        project_filter=project_filter,
        include_transcript=include_transcript,
    )
    return _Server((host, port), handler)


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    project_filter: Path | None = None,
    include_transcript: bool = True,
) -> int:
    """Run the dashboard server until SIGINT/SIGTERM. Blocks in
    `serve_forever`. Binds before installing signal handlers so a bind error
    surfaces to the caller untouched."""
    httpd = build_server(
        host,
        port,
        project_filter=project_filter,
        include_transcript=include_transcript,
    )

    def _stop(_signum, _frame) -> None:
        # shutdown() blocks until serve_forever returns; calling it from this
        # (the serve_forever) thread would deadlock, so hand it to a one-shot.
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    bound_host, bound_port = httpd.server_address[:2]
    print(f"clu serve → http://{bound_host}:{bound_port}/  (read-only; Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
    return 0
