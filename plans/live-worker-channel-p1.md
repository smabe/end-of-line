# p1 — Addressing + inbound plumbing

Make every dispatched worker (a) addressable by a deterministic name and
(b) able to receive and reply to cross-session messages. No message logic yet —
this phase only makes the channel *reachable*.

## Locked decisions

- **Address by name, not socket — opt-in via `{worker_name}`.** A worker whose
  dispatch command carries the `{worker_name}` placeholder renders
  `--name clu-<plan_slug>-<phase_id>`; a worker without it gets no name (same
  opt-in model as `{session_id}`). The console resolves the name via
  `ListAgents`. No pid, no socket path, no registry — `SendMessage` routes by
  name. (Adversarial re-scope agent, vs. the official cross-session-messaging
  doc.) **Same-plan+phase re-dispatch (lease expiry, blocker resume) renders the
  SAME name — this is guaranteed, not an edge case (plan-time probe item vii);
  p4 owns picking the LIVE worker among any stale registrations.**
- **Workers must be able to reply.** `SendMessage` and `ListAgents` join the
  worker `--allowedTools`. Receiving is automatic; replying requires the tool be
  allowlisted.
- **`crossSessionInbound:"accept"` in the worker settings.** A headless `--print`
  worker can't show a hold dialog, so a held message hangs forever; `accept`
  makes delivery unconditional. Cost-free (dontAsk already counts as the
  prompting class). No sandbox unix-socket allowance needed — the worker's Claude
  process binds its own socket; Bash never touches it.

## Work

- Add a `{worker_name}` placeholder to `render_command` in `dispatch.py`, derived
  INSIDE `render_command` as `shlex.quote(f"clu-{plan_slug}-{phase_id}")` (parity
  with the other placeholders' quoting), added as one `.format` kwarg.
  **Design decisions the plan-time probe validated (build them this way):**
  - **No opt-in detection function.** Unlike `{session_id}` (whose opt-in gate
    `_template_uses_session_id` at `dispatch.py:177` exists because generating a
    uuid is a side effect), `{worker_name}` has NO side effect — `str.format`
    silently ignores an unused kwarg — so no `_template_uses_worker_name` is
    needed. A template without the placeholder simply renders without a name.
  - **No claim-stamp of the name.** Nothing in this plan reads a stamped name
    (p4 resolves via `ListAgents`, not the claim), so do not stamp it.
  - Deriving inside `render_command` keeps BOTH call sites unchanged — dispatch
    (`dispatch.py:248`) and the doctor render (`cli.py:2758`), which shares
    `render_command`. Confirmed: `cli.py` needs no edit.
- `end_of_line/worker-settings.template.json`: add top-level
  `"crossSessionInbound": "accept"` (JSON key order not significant).
- `examples/hardened.orchestrator.json`: show `--name {worker_name}` in the
  dispatch command (the placeholder — NOT `--name clu-{plan}-{phase}`; `{plan}`
  / `{phase}` are not valid placeholders, and the example must use the same
  `{worker_name}` route (i) chose) and `SendMessage,ListAgents` added to the
  comma-joined `--allowedTools`.
- Tests (in `tests/test_dispatch.py`, beside the existing session-id render
  tests — real names are `test_session_id_placeholder_substituted_and_stamped`
  and siblings, NOT `test_template_uses_session_id`): `{worker_name}` substitutes
  to `clu-<plan_slug>-<phase_id>` and a template without the placeholder renders
  unchanged; worker-settings template carries `crossSessionInbound`.
- Consumes: `none`.
- Produces: a dispatched worker registered in `ListAgents` as
  `clu-<plan_slug>-<phase_id>` (when its template carries `{worker_name}`) with
  `SendMessage`/`ListAgents` allowlisted and inbound accepting. Consumed by p3
  (worker replies) and p4 (console resolves the name).

*(The allowlist + settings additions are DOCUMENTED in p5, not here — p1 ships
the mechanism; p5 owns the operator-facing docs. `docs/operations.md` and any
clu-phase recipe note live in p5/p3 respectively, not p1.)*

## Done criteria

- **Observable (probe):** dispatch a real worker via a `{worker_name}` template;
  from a second session, `ListAgents` shows it as `clu-<plan>-<phase>`; a
  `SendMessage` to that name delivers AND the worker replies (a one-line ack
  written to a file or sent back). This is the same probe shape already run in
  stage-zero — re-run it against the real dispatch path.
- Full `unittest` suite green (`python3 -m unittest discover -s tests`).
- `clu verify` green (includes basedpyright).

## Decisions & findings
<!-- sealed at phase commit -->
_pending._
