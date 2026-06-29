"""`clu serve` — self-hosted web dashboard for `clu top`.

Serves the Tron "End of Line" dashboard at `GET /` and the live worker rows at
`GET /api/workers` (`top.gather_rows()` as JSON, polled by the page every
~1.5s). Localhost-only and unauthenticated by default; a single `--lan` switch
(via `build_config`) flips on the whole security layer at once: bind one
auto-detected LAN IP, require a token, enforce a Host-header allowlist, and
serve auto self-signed HTTPS.

Security constraints baked in here, do not regress:
- **Exact-match routing** to one bundled file — never `SimpleHTTPRequestHandler`
  over a directory (that follows symlinks and serves the cwd).
- **Host-header allowlist is the primary DNS-rebinding defense** — enforced on
  every request before auth, before routing → 421 on mismatch.
- **Token auth** (when configured): `hmac.compare_digest` against a
  `Bearer` header or the `clu_session` cookie; `/login?token=` mints the cookie
  (`HttpOnly; SameSite=Strict`, `Secure` under TLS). Never expose a non-loopback
  bind without a token (`build_config` guardrail).
- **`gather_rows` may raise** (corrupt registry): the request is wrapped so a
  failure yields 500 without killing the handler thread or the server.
- **Silenced request logging** — request lines carry paths and could carry a
  `?token=`; they are never written to stderr.
- **openssl is invoked via an arg list** (never `shell=True`); the bind value is
  validated before it reaches the certificate SAN.
- **`shutdown()` must fire off the `serve_forever` thread** or it deadlocks; the
  signal handler in `serve` spawns a one-shot thread for it.
"""

from __future__ import annotations

import dataclasses
import hmac
import http.cookies
import importlib.resources
import ipaddress
import json
import os
import re
import secrets
import signal
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import registry, top
from . import state as st
from ._xdg_guard import clu_config_dir

# Worker-derived transcript content dropped by `--no-transcript`. These are the
# semi-untrusted LLM/tool strings; omitting them yields a metrics-only feed.
_TRANSCRIPT_FIELDS = ("last_command", "last_text", "last_write")

# Host header values that are always loopback-safe.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
COOKIE_NAME = "clu_session"


class ConfigError(Exception):
    """A serve configuration the operator must fix (bad cert, no openssl, an
    unsafe bind). Surfaces as a clean `_die`, never a traceback."""


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class ServeConfig:
    """Everything the server + handler need. Built from CLI flags by
    `build_config`; constructed directly in tests."""

    host: str = "127.0.0.1"
    port: int = 8787
    project_filter: Path | None = None
    include_transcript: bool = True
    token: str | None = None  # None → no auth gate (loopback default)
    host_allowlist: frozenset[str] = dataclasses.field(default_factory=frozenset)
    tls: ssl.SSLContext | None = None  # None → plaintext
    # Machine-wide cwds whose non-clu sessions are surfaced even without a
    # registered plan (config `session_dirs`); fed to gather_rows + the feed.
    session_dirs: Sequence[str] = ()

    def __post_init__(self) -> None:
        if not self.host_allowlist:
            self.host_allowlist = host_allowlist_for(self.host)


def host_allowlist_for(host: str) -> frozenset[str]:
    """The Host-header values a request may carry: the bind host + loopback."""
    return frozenset({host.lower(), *LOOPBACK_HOSTS})


def _is_loopback(bind: str) -> bool:
    """True for `localhost` and every loopback IP (the whole 127.0.0.0/8 block
    and `::1`), so a `127.0.0.2` alias isn't misread as an exposed bind."""
    if bind.lower() in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return False


def detect_lan_ip() -> str:
    """The machine's primary outbound IPv4, via the UDP-connect trick (no
    packets are actually sent). Raises `ConfigError` with no usable route."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError as exc:
        raise ConfigError(
            "could not detect a LAN IP (no network?); pass --host explicitly"
        ) from exc
    finally:
        s.close()


def token_path() -> Path:
    return clu_config_dir() / "serve_token"


def load_or_create_token() -> str:
    """The shared bearer token, generated once and cached `0600`. Reused across
    runs so the operator's bookmarked `/login?token=` URL keeps working."""
    path = token_path()
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_secret(path, token)
    return token


def _write_secret(path: Path, text: str) -> None:
    """Write a secret atomically and at mode 0600 from birth — `mkstemp` creates
    the temp at 0600, and `os.replace` swaps it in without ever exposing the
    final path at a wider mode (the create-then-chmod pattern leaves a
    world-readable window). Mirrors `state.save_atomic`."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cert_paths() -> tuple[Path, Path]:
    base = clu_config_dir()
    return base / "serve_cert.pem", base / "serve_key.pem"


def _san_for(bind: str) -> str:
    """Build (and validate) the certificate SAN for `bind`. An IP becomes
    `IP:…`, a hostname `DNS:…`; both pin `localhost` too. Rejects anything that
    isn't a real IP or a conservative hostname before it reaches openssl."""
    try:
        ip = ipaddress.ip_address(bind)
        return f"IP:{ip},DNS:localhost"
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", bind):
            raise ConfigError(f"invalid bind host for certificate: {bind!r}")
        return f"DNS:{bind},DNS:localhost"


class _AddextUnsupported(Exception):
    """The installed openssl lacks `-addext` (older OpenSSL / LibreSSL)."""


def ensure_self_signed(bind: str, cert: Path, key: Path) -> None:
    """Mint a self-signed cert (SAN = bind + localhost) at `cert`/`key` if not
    already cached. Cached pair is reused untouched (stable cert across runs)."""
    if cert.exists() and key.exists():
        return
    san = _san_for(bind)
    cert.parent.mkdir(parents=True, exist_ok=True)
    # Tighten umask so openssl creates the private key 0600 from birth rather
    # than world-readable-then-chmod (a TOCTOU window). Runs single-threaded at
    # startup; _lock_down stays as a backstop. Restored in finally.
    old_umask = os.umask(0o077)
    try:
        try:
            _openssl_addext(san, cert, key)
        except _AddextUnsupported:
            _openssl_config(san, cert, key)
    finally:
        os.umask(old_umask)


def _lock_down(cert: Path, key: Path) -> None:
    os.chmod(cert, 0o600)
    os.chmod(key, 0o600)


_OPENSSL_BASE = [
    "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
    "-days", "825",
]


def _openssl_addext(san: str, cert: Path, key: Path) -> None:
    cmd = [
        *_OPENSSL_BASE,
        "-keyout", str(key), "-out", str(cert),
        "-subj", "/CN=clu serve",
        "-addext", f"subjectAltName={san}",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise ConfigError(
            "openssl not found on PATH; pass --cert/--key or use --http"
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").lower()
        if b"addext" in stderr or b"unknown option" in stderr:
            raise _AddextUnsupported() from exc
        raise ConfigError(
            f"openssl failed: {(exc.stderr or b'').decode(errors='replace')[:200]}"
        ) from exc
    _lock_down(cert, key)


def _openssl_config(san: str, cert: Path, key: Path) -> None:
    """Fallback for openssl builds without `-addext`: pass the SAN through a
    temp config file's `x509_extensions` instead."""
    config = (
        "[req]\ndistinguished_name=dn\nx509_extensions=v3\nprompt=no\n"
        "[dn]\nCN=clu serve\n"
        f"[v3]\nsubjectAltName={san}\n"
    )
    fd, cfg_path = tempfile.mkstemp(suffix=".cnf")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(config)
        cmd = [
            *_OPENSSL_BASE,
            "-keyout", str(key), "-out", str(cert),
            "-config", cfg_path,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except FileNotFoundError as exc:
            raise ConfigError(
                "openssl not found on PATH; pass --cert/--key or use --http"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise ConfigError(
                f"openssl failed: {(exc.stderr or b'').decode(errors='replace')[:200]}"
            ) from exc
        _lock_down(cert, key)
    finally:
        os.unlink(cfg_path)


def build_tls_context(certfile: Path, keyfile: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        ctx.load_cert_chain(str(certfile), str(keyfile))
    except (OSError, ssl.SSLError) as exc:
        raise ConfigError(f"could not load cert/key: {exc}") from exc
    return ctx


def build_config(
    *,
    lan: bool = False,
    host: str | None = None,
    port: int = 8787,
    project_filter: Path | None = None,
    include_transcript: bool = True,
    cert: str | None = None,
    key: str | None = None,
    http: bool = False,
    session_dirs: Sequence[str] = (),
) -> ServeConfig:
    """Resolve CLI flags into a `ServeConfig`, applying the security policy:
    LAN-IP detection, token provisioning, the non-loopback-needs-a-token
    guardrail, and TLS selection. This is the one place that decides whether the
    server is exposed, authenticated, and encrypted."""
    if host:
        bind = host
    elif lan:
        bind = detect_lan_ip()
    else:
        bind = "127.0.0.1"

    # `exposed` = reachable off this machine → needs a token. `--lan` always
    # counts as exposed even if the detected IP looked loopback for some reason;
    # a bind that isn't loopback is exposed regardless of the flag.
    exposed = lan or not _is_loopback(bind)
    token = load_or_create_token() if exposed else None
    if exposed and not token:
        # Reachable guard: an exposed bind must never be tokenless. Fires if
        # token provisioning is ever changed to return falsy.
        raise ConfigError(
            f"refusing to expose {bind} without a token; use --lan"
        )

    # TLS: explicit --cert/--key wins; otherwise an exposed bind defaults to
    # auto self-signed HTTPS unless --http opts into cleartext.
    if (cert or key) and http:
        raise ConfigError("--http conflicts with --cert/--key (cleartext vs TLS)")
    tls: ssl.SSLContext | None = None
    if cert or key:
        if not (cert and key):
            raise ConfigError("--cert and --key must be given together")
        tls = build_tls_context(Path(cert), Path(key))
    elif exposed and not http:
        cert_file, key_file = cert_paths()
        ensure_self_signed(bind, cert_file, key_file)
        tls = build_tls_context(cert_file, key_file)

    return ServeConfig(
        host=bind,
        port=port,
        project_filter=project_filter,
        include_transcript=include_transcript,
        token=token,
        tls=tls,
        session_dirs=session_dirs,
    )


# --------------------------------------------------------------------------- #
# Page + data
# --------------------------------------------------------------------------- #
def load_index_html() -> str:
    """Read the bundled dashboard page via `importlib.resources` (works in an
    editable checkout and inside a wheel, given the `web/*.html` package-data)."""
    return (
        importlib.resources.files("end_of_line")
        .joinpath("web", "index.html")
        .read_text(encoding="utf-8")
    )


def load_apple_icon() -> bytes:
    """Read the bundled apple-touch-icon PNG (the phone home-screen icon),
    served at `/apple-touch-icon.png`. Package-data via the `web/*.png` glob."""
    return (
        importlib.resources.files("end_of_line")
        .joinpath("web", "apple-touch-icon.png")
        .read_bytes()
    )


def workers_json(
    *, project_filter: Path | None = None, include_transcript: bool = True,
    session_dirs: Sequence[str] = (),
) -> bytes:
    """`gather_rows()` shaped for the wire. With `include_transcript=False`,
    drop the transcript-content fields so the feed carries metrics only."""
    rows = top.gather_rows(project_filter=project_filter, session_dirs=session_dirs)
    if not include_transcript:
        for row in rows:
            for field in _TRANSCRIPT_FIELDS:
                row.pop(field, None)
    return json.dumps(rows).encode("utf-8")


# --------------------------------------------------------------------------- #
# /api/feed — incremental transcript tail for the detail-pane activity feed
# --------------------------------------------------------------------------- #
# Backfill window for a first poll (matches `top.tail_records`' bound); per-poll
# read cap (transcript lines can embed whole files — one poll must stay
# bounded); per-event text cap (same reason, applied after decode).
FEED_BACKFILL_BYTES = 64 * 1024
FEED_READ_CAP = 256 * 1024
FEED_TEXT_CAP = 2000


def read_feed_window(path: Path, cursor: int) -> tuple[list[dict], int, bool]:
    """Parse complete JSONL records from `path` starting at byte `cursor`.

    Returns `(records, new_cursor, reset)`. `cursor=-1` (a client's first poll)
    backfills from the last `FEED_BACKFILL_BYTES`, starting at a record
    boundary; a cursor past EOF means the file shrank under the client
    (rotation / a new attempt reusing the name) — same backfill, `reset=True`.
    At most `FEED_READ_CAP` bytes are read per call and consumed only to the
    last `\\n`: a partial final line (writer mid-append) stays unconsumed and
    is re-read whole next poll. Unparseable lines are skipped but consumed.
    Raises `OSError` when the file is unreadable (caller maps to 404).
    """
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        reset = cursor > size
        backfill = cursor < 0 or reset
        if backfill:
            cursor = max(0, size - FEED_BACKFILL_BYTES)
        f.seek(cursor)
        buf = f.read(FEED_READ_CAP)
    skip = 0
    if backfill and cursor > 0:
        # A mid-file start lands mid-line; drop up to the first newline.
        nl = buf.find(b"\n")
        if nl < 0:
            return [], cursor, reset
        skip = nl + 1
    end = buf.rfind(b"\n")
    if end < skip:
        # No complete line beyond the (possibly skipped) partial one. Consume
        # just the skip so the next poll starts at the record boundary.
        return [], cursor + skip, reset
    records: list[dict] = []
    for raw in buf[skip : end + 1].split(b"\n"):
        s = raw.strip()
        if not s:
            continue
        try:
            records.append(json.loads(s))
        except json.JSONDecodeError:
            continue
    return records, cursor + end + 1, reset


def _truncate(text: str) -> str:
    return text if len(text) <= FEED_TEXT_CAP else text[: FEED_TEXT_CAP - 1] + "…"


def _result_text(block: dict) -> str:
    """Flatten a tool_result's `content` (string OR text-block list) to one
    string — the same string-or-array tolerance as `top._content_blocks`."""
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b["text"]
            for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        )
    return ""


def record_events(rec) -> list[dict]:
    """One transcript record → feed events `{ts, kind, text}`, in block order.

    Decodes the same record shapes `top.extract_activity` understands —
    assistant text → `say`, Bash tool_use → `tool`, write-tool tool_use →
    `write`, tool_result → `result` — but where that reduces to the LAST of
    each kind for a dashboard row, the feed keeps every occurrence. The
    cross-record state extract_activity also tracks (usage totals, the
    running-command id pairing) has no feed analogue, so this decoder stays
    local rather than contorting a shared helper. Empty-text blocks drop.
    """
    if not isinstance(rec, dict):
        return []
    ts = rec.get("timestamp")
    raw_message = rec.get("message")
    message = raw_message if isinstance(raw_message, dict) else {}
    rtype = rec.get("type")
    events: list[dict] = []

    def _emit(kind: str, text) -> None:
        if isinstance(text, str) and text.strip():
            events.append({"ts": ts, "kind": kind, "text": _truncate(text)})

    if rtype == "assistant":
        for block in top._content_blocks(message):
            btype = block.get("type")
            if btype == "text":
                _emit("say", block.get("text"))
            elif btype == "tool_use":
                raw_input = block.get("input")
                inp = raw_input if isinstance(raw_input, dict) else {}
                name = block.get("name")
                if name == "Bash":
                    _emit("tool", inp.get("command"))
                elif name in top._WRITE_TOOLS:
                    _emit("write", inp.get("file_path"))
                elif name in top._AGENT_TOOLS:
                    # A spawned subagent (a /code-review fan-out, an Explore, …):
                    # its type or, lacking that, its one-line description.
                    _emit("agent", inp.get("subagent_type") or inp.get("description"))
    elif rtype == "user":
        for block in top._content_blocks(message):
            if block.get("type") == "tool_result":
                _emit("result", _result_text(block))
    return events


def _project_entries(proj: str, *, project_filter: Path | None = None):
    """Yield registered entries whose project_root basename is `proj` — matched
    against basenames, NEVER path-joined; that basename match is the feed's
    path-safety boundary for the REGISTRY candidate roots, single-sourced here.
    The `--project` resolve-compare is shared with the curses path via
    `top.matches_project_filter`."""
    for e in registry.entries():
        if Path(e.project_root).name == proj and top.matches_project_filter(
            e.project_root, project_filter
        ):
            yield e


def resolve_feed_transcript(
    plan: str,
    proj: str,
    phase: str,
    *,
    project_filter: Path | None = None,
    projects_root: Path = top.PROJECTS_ROOT,
) -> tuple[Path, str] | None:
    """Transcript path + identity (file stem = session id, the feed `tid`) for
    the live claim of (`proj`, `plan`), or None when there's no such plan, no
    live claim, the claim's phase isn't `phase` (the client's selection is
    stale), or no transcript exists yet.

    Mirrors `gather_rows`' registry → claim → worktree-cwd → `locate_transcript`
    path, over `_project_entries` (the shared basename match). Per-entry resilient
    like `gather_rows`: entries the `project_filter` excludes, and entries that
    cannot serve the request (unreadable state, claim on another phase, no
    transcript yet), are skipped — the first entry that can serve decides."""
    for e in _project_entries(proj, project_filter=project_filter):
        if e.plan_slug != plan:
            continue
        data = registry.load_entry_state(e)
        if not data:
            continue
        claim = data.get("current_claim")
        if not claim or claim.get("phase_id") != phase:
            continue
        wt = st.get_worktree(data)
        cwd = Path(wt["path"]) if wt and wt.get("path") else Path(e.project_root)
        tpath = top.locate_transcript(
            cwd, projects_root=projects_root, session_id=claim.get("session_id")
        )
        if tpath:
            return (tpath, tpath.stem)
    return None


def resolve_session_transcript(
    proj: str,
    sid: str,
    *,
    project_filter: Path | None = None,
    projects_root: Path = top.PROJECTS_ROOT,
    session_dirs: Sequence[str] = (),
) -> tuple[Path, str] | None:
    """Transcript path + identity (`tid`) for a non-clu session `sid` in project
    `proj`, or None. The session counterpart of `resolve_feed_transcript`: there
    is no claim to go through, so it checks for the EXACT `<sid>.jsonl` in the
    matching project's transcript dir — and only serves it if it meets the SAME
    `session` definition the dashboard lists by (`gather_session_rows`): a
    main-session transcript (cwd-confirmed, sidechain-rejected via `top._confirms`)
    modified within `SESSION_FRESH_SECONDS`. The freshness gate keeps the set of
    streamable sids equal to the set of listed sessions — a stale id resolves to
    nothing. Unlike `locate_transcript`, there is no newest-file fallback: an
    unknown `sid` never falls through to another session's tail.

    Candidate roots are the registry roots matching `proj` (via `_project_entries`)
    UNION the configured `session_dirs` matching `proj` (basename + project_filter),
    so a session in a watched-but-unregistered dir resolves too — mirroring how
    `gather_rows` unions session_dirs into the scan roots. The first that resolves
    decides."""
    roots = [e.project_root for e in _project_entries(proj, project_filter=project_filter)]
    for d in session_dirs:
        if Path(d).name == proj and top.matches_project_filter(d, project_filter):
            roots.append(d)
    for root in roots:
        cand = projects_root / top.encode_project_dir(root) / f"{sid}.jsonl"
        try:
            fresh = time.time() - cand.stat().st_mtime < top.SESSION_FRESH_SECONDS
        except OSError:
            continue  # no such file in this root's dir
        if fresh and top._confirms(cand, root):
            return (cand, cand.stem)
    return None


def feed_json(
    query: dict[str, list[str]], *, project_filter: Path | None = None,
    session_dirs: Sequence[str] = (),
) -> tuple[int, bytes]:
    """Resolve one `/api/feed` poll to `(status, body)`. A 200 body is JSON
    `{events, cursor, tid, reset}`; error bodies are plain text. `reset:true`
    tells the client its scrollback is stale (new session id, or the file
    shrank under its cursor) and these events are a fresh backfill.

    Two row kinds: a clu worker is keyed by `(plan, proj, phase)` via its live
    claim; a non-clu session carries `sid` (and `proj`) and resolves by session
    id. `sid` present routes the session path; otherwise the claim path."""
    proj = (query.get("proj") or [""])[0]
    sid = (query.get("sid") or [""])[0]
    tid = (query.get("tid") or [""])[0]
    try:
        cursor = int((query.get("cursor") or ["-1"])[0])
        if sid:
            st.validate_slug(sid, kind="session id")
            resolved = resolve_session_transcript(
                proj, sid, project_filter=project_filter, session_dirs=session_dirs)
        else:
            plan = (query.get("plan") or [""])[0]
            phase = (query.get("phase") or [""])[0]
            st.validate_slug(plan, kind="plan slug")
            st.validate_slug(phase, kind="phase id")
            resolved = resolve_feed_transcript(plan, proj, phase, project_filter=project_filter)
    except (st.InvalidSlug, ValueError):
        return 400, b"bad request\n"
    if resolved is None:
        return 404, b"not found\n"
    tpath, current_tid = resolved
    reset = bool(tid) and tid != current_tid
    if reset:
        cursor = -1  # new attempt / new session: ignore the stale cursor
    try:
        records, cursor, shrank = read_feed_window(tpath, cursor)
    except OSError:
        return 404, b"not found\n"
    events = [ev for rec in records for ev in record_events(rec)]
    body = json.dumps(
        {"events": events, "cursor": cursor, "tid": current_tid, "reset": reset or shrank}
    ).encode("utf-8")
    return 200, body


# --------------------------------------------------------------------------- #
# Handler
# --------------------------------------------------------------------------- #
def make_handler(*, index_html: str, cfg: ServeConfig):
    """Build a `BaseHTTPRequestHandler` closed over the page + config."""
    page = index_html.encode("utf-8")
    apple_icon = load_apple_icon()

    class _Handler(BaseHTTPRequestHandler):
        # Silence default access logging: request lines could carry paths or a
        # `?token=` and must never reach stderr.
        def log_message(self, *args):  # noqa: D401 - silencing override
            return

        def do_HEAD(self):
            self._dispatch(head=True)

        def do_GET(self):
            self._dispatch(head=False)

        # -- gates ---------------------------------------------------------- #
        def _host_allowed(self) -> bool:
            raw = self.headers.get("Host")
            if not raw:
                # Absent Host is tolerated only on the unauthenticated loopback
                # default (simple HTTP/1.0 tooling). On any exposed/tokened bind
                # a missing Host is rejected — legit browsers always send one,
                # and the allowlist is the primary DNS-rebinding defense.
                return cfg.token is None
            if raw.startswith("["):  # [::1]:port
                hostname = raw[1: raw.find("]")] if "]" in raw else raw
            else:
                hostname = raw.rsplit(":", 1)[0] if ":" in raw else raw
            return hostname.lower() in cfg.host_allowlist

        def _cookie(self, name: str) -> str | None:
            raw = self.headers.get("Cookie")
            if not raw:
                return None
            jar = http.cookies.SimpleCookie()
            try:
                jar.load(raw)
            except http.cookies.CookieError:
                return None
            morsel = jar.get(name)
            return morsel.value if morsel else None

        def _authed(self) -> bool:
            if cfg.token is None:
                return True  # no auth configured (loopback default)
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                if hmac.compare_digest(auth[len("Bearer "):], cfg.token):
                    return True
            cookie = self._cookie(COOKIE_NAME)
            return cookie is not None and hmac.compare_digest(cookie, cfg.token)

        # -- dispatch ------------------------------------------------------- #
        def _dispatch(self, *, head: bool) -> None:
            try:
                # 1. DNS-rebinding defense — before auth, before routing.
                if not self._host_allowed():
                    self._respond(
                        421, b"misdirected request\n", "text/plain; charset=utf-8", head=head
                    )
                    return

                url = urlsplit(self.path)
                path = url.path

                # 2. Static, non-sensitive icon — served before the auth gate so
                #    a browser favicon / iOS home-screen fetch (which need not
                #    carry the token) resolves. Discloses no worker data.
                if path == "/apple-touch-icon.png":
                    self._respond(
                        200, apple_icon, "image/png",
                        head=head, extra={"Cache-Control": "max-age=86400"},
                    )
                    return

                # 3. /login mints the session cookie (only when auth is on).
                if path == "/login" and cfg.token is not None:
                    self._handle_login(head=head)
                    return

                # 4. Auth gate.
                if cfg.token is not None and not self._authed():
                    self._respond(401, b"unauthorized\n", "text/plain; charset=utf-8", head=head)
                    return

                # 5. Routes (exact match only).
                if path == "/":
                    self._respond(200, page, "text/html; charset=utf-8", head=head)
                elif path == "/api/workers":
                    body = workers_json(
                        project_filter=cfg.project_filter,
                        include_transcript=cfg.include_transcript,
                        session_dirs=cfg.session_dirs,
                    )
                    self._respond(
                        200, body, "application/json; charset=utf-8",
                        head=head, extra={"Cache-Control": "no-store"},
                    )
                elif path == "/api/feed" and cfg.include_transcript:
                    # The feed is 100% transcript-content data, so --no-transcript
                    # leaves the route unregistered (falls through to 404).
                    status, body = feed_json(
                        parse_qs(url.query),
                        project_filter=cfg.project_filter,
                        session_dirs=cfg.session_dirs,
                    )
                    ctype = (
                        "application/json; charset=utf-8"
                        if status == 200
                        else "text/plain; charset=utf-8"
                    )
                    self._respond(
                        status, body, ctype, head=head, extra={"Cache-Control": "no-store"}
                    )
                else:
                    self._respond(404, b"not found\n", "text/plain; charset=utf-8", head=head)
            except Exception:
                # Corrupt registry / transcript: 500, but keep the thread and
                # the server alive. Never leak the traceback to the client.
                try:
                    self._respond(
                        500, b"internal server error\n", "text/plain; charset=utf-8", head=head
                    )
                except Exception:
                    pass

        def _handle_login(self, *, head: bool) -> None:
            token = (parse_qs(urlsplit(self.path).query).get("token") or [""])[0]
            # A tokenless config (loopback, auth off) must never mint a session
            # cookie: deny, don't assert. The route gate already skips /login
            # when cfg.token is None — this is defense-in-depth if that drifts.
            expected = cfg.token
            if expected is not None and hmac.compare_digest(token, expected):
                attrs = f"{COOKIE_NAME}={expected}; HttpOnly; SameSite=Strict; Path=/"
                if cfg.tls is not None:
                    attrs += "; Secure"
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", attrs)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._respond(401, b"unauthorized\n", "text/plain; charset=utf-8", head=head)

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

    def handle_error(self, request, client_address):
        # A LAN-exposed server gets scanned constantly: a failed TLS handshake
        # (plain HTTP hitting the HTTPS port) or a dropped connection must not
        # spew tracebacks to stderr. Per-request handler errors are already
        # turned into 500s inside the handler; this only swallows transport-
        # level noise on accept/handshake. serve_forever keeps running.
        pass


def build_server(cfg: ServeConfig) -> _Server:
    """Construct (and bind) the server from a config. Raises `OSError` on a bind
    failure (e.g. EADDRINUSE), `ConfigError` if the TLS wrap fails. Kept separate
    from `serve` so tests can drive it without installing signal handlers."""
    handler = make_handler(index_html=load_index_html(), cfg=cfg)
    server = _Server((cfg.host, cfg.port), handler)
    if cfg.tls is not None:
        try:
            # Wrap the listening socket: accept() then returns TLS connections.
            server.socket = cfg.tls.wrap_socket(server.socket, server_side=True)
        except (OSError, ssl.SSLError) as exc:
            server.server_close()
            raise ConfigError(f"could not start TLS: {exc}") from exc
    return server


def _print_banner(cfg: ServeConfig, scheme: str, host: str, port: int) -> None:
    base = f"{scheme}://{host}:{port}"
    lines: list[str] = []
    if cfg.token:
        # The operator's terminal — printing the token here is intended (it is
        # the shareable entry point); the no-log rule is about request logs.
        lines.append(f"clu serve → {base}/login?token={cfg.token}")
        lines.append("  open that URL once; it sets a read-only session cookie.")
    else:
        lines.append(f"clu serve → {base}/  (localhost, read-only; Ctrl-C to stop)")
    if cfg.host.lower() not in LOOPBACK_HOSTS:
        lines.append(
            f"  ⚠ reachable on your LAN at {cfg.host} — anyone on this network "
            "with the token can view worker activity."
        )
        if cfg.tls is None:
            lines.append(
                "  ⚠ CLEARTEXT (--http): token + transcript are sent "
                "unencrypted and are sniffable on shared Wi-Fi."
            )
    # Flush: when stdout is redirected (not a TTY) it block-buffers, which would
    # hide the login URL the operator needs until the process exits.
    print("\n".join(lines), flush=True)


def serve(cfg: ServeConfig) -> int:
    """Run the dashboard server until SIGINT/SIGTERM. Blocks in
    `serve_forever`. Binds before installing signal handlers so a bind error
    surfaces to the caller untouched."""
    httpd = build_server(cfg)

    def _stop(_signum, _frame) -> None:
        # shutdown() blocks until serve_forever returns; calling it from this
        # (the serve_forever) thread would deadlock, so hand it to a one-shot.
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    scheme = "https" if cfg.tls is not None else "http"
    bound_host, bound_port = httpd.server_address[:2]
    assert isinstance(bound_host, str)  # AF_INET/AF_INET6 report the host as str
    _print_banner(cfg, scheme, bound_host, bound_port)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
    return 0
