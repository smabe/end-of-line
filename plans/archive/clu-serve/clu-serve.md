# clu serve — self-hosted web dashboard

## Goal
A `clu serve` command that self-hosts the `clu top` worker dashboard as a web
page (the Tron prototype), fed by a JSON endpoint over the existing
`gather_rows()` data. Localhost by default; a single `--lan` switch makes it
reachable from the operator's phone on the LAN, with token auth, DNS-rebinding
protection, and an explicit cleartext warning. Pure stdlib, read-only.

## Non-goals
- **No write / mutate / control endpoints — read-only, always.** No kill /
  release / answer from the web. *Why safe:* matches the existing
  operator-approval discipline; there is no reason to expose mutations over HTTP.
- **No SSE / websockets in v1 — the page polls `/api/workers` every ~1.5s.**
  *Why safe:* polling is sufficient at 1.5s and avoids ThreadingHTTPServer's
  one-thread-per-stream + awkward-shutdown failure mode (stdlib research).
- **No *stdlib* cert generation** (`ssl` can't — verified: `load_cert_chain`
  takes file paths only). Resolution: clu shells out to the system `openssl` to
  mint a self-signed cert on first `--lan`, so **HTTPS is the `--lan` default**
  (browser warns: untrusted CA — accepted). `--cert/--key` overrides with
  operator PEMs; `--http` explicitly opts into plaintext (loud warning).
  `openssl` is a system binary, not a Python dep.
- **No `0.0.0.0` / all-interfaces bind.** `--lan` binds one auto-detected LAN
  IP. *Why safe:* `0.0.0.0` silently exposes interfaces the operator didn't
  reason about; one explicit IP is auditable.
- **Does not replace curses `clu top`** — additive; both render the same data.
- **No multi-user / accounts / RBAC** — one shared token (single operator).
- **Does not depend on the in-flight TUI redesign** (`clu-top-tui-master.md`);
  reuses today's `gather_rows`. It *composes* with the future metric registry
  (serve registered metrics as JSON) but doesn't wait for it.

## Files to touch
- **`end_of_line/webserver.py`** (new) — the server. `ThreadingHTTPServer`
  (`allow_reuse_address`) + a `BaseHTTPRequestHandler`: route on
  `urlsplit(self.path).path` → `GET /` (bundled `index.html`), `GET
  /api/workers` (`json.dumps(top.gather_rows(...))`, `Cache-Control: no-store`),
  `GET /login?token=…` (set cookie). Auth gate, Host-header allowlist, bind-IP
  detection. **TLS:** load `--cert/--key` if given, else auto-mint a self-signed
  cert via `openssl` (SAN = bind IP + `localhost`, cached `0600` under
  `clu_config_dir()`), then `ssl.SSLContext.load_cert_chain`; `--http` skips it.
  Plus token load/gen, silenced
  `log_message`, per-request `try/except` → 500 (a bad `gather_rows` must not
  kill the server thread), `shutdown()` from a SIGINT/SIGTERM handler **on a
  separate thread** (calling it from `serve_forever`'s thread deadlocks).
  Reuses `_xdg_guard.clu_config_dir()` for the token file.
- **`end_of_line/web/index.html`** (new) — the dashboard, adapted from
  `plans/brainstorm-clu-top-tui/prototype.html`: replace the hardcoded
  `WORKERS` array with a `fetch('/api/workers')` poll, mapping the row fields
  (`ran_seconds`→age, `last_text`→SAYING, `command_running`→`*`, etc.). The
  prototype is a throwaway design artifact, not a maintained sibling, so this
  is adapt-from-reference, not a code-dedup mirror.
- **`end_of_line/cli.py`** — `p_serve` subparser after `p_top` (~cli.py:1196)
  with `--port`, `--lan`, `--host`, `--cert`, `--key`, `--http`,
  `--no-transcript`, `--project`; dispatch `if args.cmd == "serve"` (~cli.py:1327); `cmd_serve(args)
  -> int` near `cmd_top` (cli.py:3891), using `_die(ExitCode.X, …)`.
- **`pyproject.toml`** — add `"web/*.html"` to `[tool.setuptools.package-data]`
  `end_of_line` (currently `skills/*/SKILL.md`, `hooks/*.py` at pyproject.toml:20-21).
- **`tests/test_webserver.py`** (new) — handler logic + an ephemeral-port
  (`127.0.0.1:0`) integration test in a daemon thread.
- **`docs/operations.md`** — `clu serve` usage + the security model + phone
  access. **`docs/reference.md`** — `webserver.py` module section.
  **`README.md`** — operator-commands row.

## Failure modes to anticipate
- **DNS rebinding (highest threat).** A malicious page in the phone's browser
  rebinds its hostname to the LAN server IP; the browser then sends same-origin
  requests carrying the auth cookie, exfiltrating transcript/command data — the
  token does NOT stop this. **Mitigation (primary defense): validate the `Host`
  header against an allowlist** (loopback names + the configured bind IP) →
  reject with 421. Lands as phase 2's first test.
- **XSS via worker-derived content (new attack surface — HIGH).** `/api/workers`
  carries `last_command` / `last_text` (SAYING) / `last_write` — semi-untrusted
  LLM + tool/repo text. The design-reference prototype renders these with
  `innerHTML` string interpolation; carried over verbatim, a worker SAYING/command
  containing `<img src=x onerror=…>` or `<script>` executes in the browser — and
  over `--lan` the auth cookie is present, so it's session-grade. The curses TUI
  is immune (literal text); the web frontend is NOT. **Mitigation: the shipped
  `index.html` inserts every worker-derived string via `textContent` / an
  HTML-escape helper — never `innerHTML` with interpolated data.**
- **openssl shell-out injection** — the auto-cert path interpolates the bind
  IP/host into an `openssl` invocation. **Mitigation: call `openssl` via a
  `subprocess` arg list (never `shell=True`), and validate the bind value is a
  real IP/hostname before it reaches the SAN.**
- **Static-serving path traversal** — serving the bundled page must be
  **exact-match routing to one fixed file**, never `SimpleHTTPRequestHandler`
  over a directory (it follows symlinks and serves the cwd).
- **Accidental wide exposure** — binding a LAN/non-loopback address with no
  token. Guardrail: refuse to bind a non-loopback address unless a token exists.
- **`shutdown()` deadlock** — calling it from the `serve_forever` thread hangs;
  must fire from the signal handler on a separate thread.
- **`gather_rows` raises mid-request** (corrupt registry) — must return 500, not
  crash the handler thread or the server.
- **Token leaking to logs / history** — `?token=` persists in logs, browser
  history, `Referer`. Mitigation: `/login`→cookie flow, silence `log_message`,
  redact token/cookie from any output.
- **Port in use (EADDRINUSE)** — clean `_die`, not a traceback; `allow_reuse_address`.
- **LAN-IP detection** — multiple interfaces / no network. Detect the primary
  outbound IP (UDP-connect trick); `--host` overrides; fail clearly if none.
- **Frontend not bundled in the wheel** — package-data omission ⇒
  `importlib.resources` lookup fails. A test must load the resource.
- **`/api/workers` browser caching** — set `Cache-Control: no-store`.
- **`openssl` missing or no `-addext`** (bare macOS LibreSSL) — auto-cert
  generation fails. Mitigation: probe `openssl version`; on `-addext` failure
  fall back to a temp openssl-config invocation (`[v3_req] subjectAltName`); if
  `openssl` is absent entirely, `_die` with a clear message pointing at
  `--cert/--key` or `--http`. (Verified working here on OpenSSL 3.6.2.)
- **Cleartext only when explicitly chosen** — `--http` (or supplying neither
  cert nor a working openssl) sends token + transcript in the clear, sniffable
  on shared Wi-Fi. Mitigation: `--lan` defaults to auto-self-signed HTTPS; print
  a loud cleartext warning whenever plaintext is actually used.
- **XDG / `CLU_TEST_MODE` guard** refusing the token-file write in tests — use
  the existing isolate pattern / tmp XDG.

## Done criteria
- `clu serve` runs a localhost server; `GET /` serves the dashboard, `GET
  /api/workers` returns `gather_rows()` as JSON; the page polls + renders live
  workers (ephemeral-port integration test).
- Clean shutdown on SIGINT/SIGTERM (from a separate thread; no deadlock); a
  `gather_rows` exception yields 500, server stays up — tested.
- **`--lan`**: binds one auto-detected LAN IP, auto-generates + 0600-stores a
  `secrets.token_urlsafe(32)` token, enforces the Host-allowlist (cross-origin
  Host → 421), **serves HTTPS via an auto-generated self-signed cert** (SAN =
  bind IP + `localhost`, cached + reused), prints a LAN-exposure warning, and
  **refuses a non-loopback bind without a token** — tested.
- **Auth**: protected endpoint unauthenticated → 401; `GET /login?token=<valid>`
  sets `Set-Cookie: clu_session=…; HttpOnly; SameSite=Strict; Path=/`; subsequent
  requests authorized; `Authorization: Bearer <token>` also works; comparison via
  `hmac.compare_digest` — tested. Bad token → 401.
- **`--no-transcript`** omits `last_command`/`last_text`/`last_write` from the
  JSON — tested.
- **No XSS:** every worker-derived string in the dashboard is inserted via
  `textContent` / an HTML-escape helper, never `innerHTML` with interpolated
  data — a worker field containing `<script>`/`<img onerror>` renders inert.
  Verified in `/code-review` + the post-build `/security-review`.
- **TLS**: with no cert supplied, `--lan` auto-mints a self-signed cert via
  `openssl` (probed; temp-config fallback for `-addext`-less openssl; `_die` if
  openssl absent), caches it `0600`, and wraps the socket via
  `ssl.SSLContext.load_cert_chain`; `--cert/--key` overrides with operator PEMs;
  `--http` serves plaintext with the loud warning — tested (assert the wrap
  fires; cert reused on a second run, not regenerated).
- Frontend loads via `importlib.resources` (editable + wheel); token / cookie /
  transcript never logged.
- `docs/operations.md`, `docs/reference.md`, `README.md` updated. Full suite
  green (report count).

## Parking lot
(empty at start)
