# p5 — Docs + issue #100 writeup

Write the trust boundary down, document the optional console, and resolve issue
#100 — including the explicit record that warm blockers were declined and why.
Doc-only phase; reference check, no `/code-review`.

## Locked decisions

- **The issue's acceptance criteria are answered, not silently dropped.** Each
  #100 checkbox is either satisfied by what shipped or explicitly declined with
  reasoning in the writeup. No checkbox is left ambiguous.
- **Trust boundary is stated in `docs/contract.md`** next to the token/callback
  contract: a `SendMessage` is NOT a `--token` callback; it can never mutate
  state or reach `complete/block/spawn/verify/attest`, which validate `--token`
  against the live claim. A worker acts on a message only through its OWN token,
  which it already holds; the same-uid prompt-injection risk is named and scoped
  (advice-into-context, not escalation).

## Work

- `docs/contract.md`: add the SendMessage trust-boundary paragraph (message ≠
  token callback; same-uid model; the worker's `crossSessionInbound:"accept"`).
- `docs/architecture.md`: document the optional live-console layer (console
  session ↔ worker via SendMessage; state stays source of truth). Update the
  blocker round-trip section to note warm-resume was evaluated and **declined**
  (one line + pointer), so a future reader doesn't re-propose it. Do NOT claim a
  warm path ships.
- `docs/operations.md`: how to run the console (start a session in the repo, use
  `ListAgents`/SendMessage), AND document the two hardened-dispatch additions p1
  shipped (the `SendMessage,ListAgents` allowlist entries + the
  `crossSessionInbound:"accept"` setting) in the "Hardened worker dispatch"
  section — p1 ships them in the template/example; p5 is where they're explained,
  including the same-uid prompt-injection posture the operator signed off on.
- Issue #100: post a writeup comment — spike result (listed / delivered / drain
  latency from the stage-zero probe, incl. the doc-vs-probe `--print`
  contradiction and the direct-Python-write result banked-but-unused); what
  shipped (status + stop via resident console); what was declined (warm blockers)
  and why; the trust-boundary resolution. Then close or park per operator.
- Grep for stale vocabulary the plan introduced/invalidated across docs
  (`rg -n "warm|SendMessage|cross-session|operator_stop|clu-<plan>" docs`).
- Consumes: everything p1–p4 shipped (this is the writeup of it).
- Produces: `none` (terminal docs phase).

## Done criteria

- **Observable (artifact):** the #100 writeup comment is posted and every
  acceptance-criterion line is addressed (satisfied or declined-with-reason);
  paste the mapping into the phase's Decisions & findings.
- `docs/contract.md` states the trust boundary; `docs/architecture.md` records
  the declined warm path; `docs/operations.md` documents running the console.
- Reference check clean: every pointer resolves; no doc still implies a warm
  blocker path ships; no stale `clu-monitor`-is-only-a-hook claim remains.

## Decisions & findings
<!-- sealed at phase commit -->
_pending._
