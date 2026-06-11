# serve-activity-feed-feed — cursor endpoint + sticky-scroll pane

You are phase `feed` of the `serve-activity-feed` plan (single phase). You
deliver, as one commit: the `/api/feed` cursor endpoint, the detail-pane
scrollback UI, tests, and docs. Read the master's Locked decisions — the
design is settled; this file adds execution detail.

NOTE: you run under hardened dispatch (Fable 5). `clu block` on any denial
you legitimately need lifted. Also: you are the first worker dispatched
through the new PTY shim — nothing to do about that, but if your own Bash
behaves oddly around ptys (`os.openpty` is EPERM in YOUR sandbox — the
shim runs dispatcher-side, unaffected), check the master plan family
`line-buffer-worker-output` findings before blocking.

## Locked decisions (do NOT re-litigate)

See `plans/serve-activity-feed.md` — endpoint shape, cursor/tid semantics,
event mapping, privacy 404, sticky-scroll + 1000-entry cap, esc()/no-GPU-CSS
constraints, short-poll only.

## Read first

- `plans/serve-activity-feed.md` (the whole master — it's the contract).
- `end_of_line/webserver.py:385-466` (gates + routing), `:53-55, 353-361`
  (`include_transcript`), `:508-521` (server class).
- `end_of_line/top.py:86-125` (`locate_transcript`), `:127-161`
  (`tail_records`), `:174-225` (`extract_activity` — the decode shapes),
  `:33, 39` (`_WRITE_TOOLS`, scan bound).
- `end_of_line/web/index.html:214-294` (script head, `esc()`, `toView`),
  `:409-448` (detail pane), `:542-583` (poll loop, byte-identical skip,
  visibility gating), `:18-28` (theme vars).
- `tests/test_webserver.py:24-54` (`_ServerCase`), `:136` (frontend
  substring-guard precedent), `:170` (GPU-compositing guard), `:189-205`
  (endpoint test shapes).
- `tests/test_top.py:1-48` (`_write_jsonl`, `_asst`, `_tool_result`
  factories), `:76-170` (locate tests).
- `docs/reference.md:1127-1195` (D10 + security contract you must not
  violate).

## Produce

1. **Failing tests first.**
   - Endpoint (via `_ServerCase`): backfill on `cursor=-1` (events from a
     fixture transcript, cursor advances to file size); incremental append
     (write more records, poll with cursor → only new events); partial final
     line carried (cursor stops at last `\n`); `tid` mismatch → `reset:true`
     + fresh backfill; `st_size < cursor` → reset; unknown plan / bad slug →
     400/404 (validate_slug path); `--no-transcript` config → 404;
     unauthenticated request on a token-configured server → 401 (gate
     inheritance pin); event text truncated at the server cap.
   - Frontend substring guards (blocked-row precedent): feed container id,
     sticky-scroll function name, cap constant present in index.html; GPU
     guard stays green.

2. **Implementation.**
   - `webserver.py`: route + handler + cursor reader (seek/read-cap/last-\n)
     + record→event mapper (decide: shared helper extracted from
     `extract_activity` if clean, else local — log the decision in
     findings).
   - `web/index.html`: feed pane in detail view; own 1.5s interval gated on
     selection + visibility (or fold into existing `poll()` — pick whichever
     keeps `shapeOf`/`patch` clean); sticky scroll with ~10px bottom
     tolerance; 1000-entry DOM prune; esc() everywhere; theme vars + .panel
     chrome; mind the byte-identical-skip pattern so an idle feed doesn't
     re-render.
   - `docs/reference.md`: `/api/feed` contract (params, response, resets,
     privacy) in the webserver section.

3. **Acceptance.**
   - All new tests green; full suite green; `basedpyright` exit 0 (gate).
   - Manual smoke against your own session: run `clu serve` from the
     worktree (`python3 -m end_of_line.cli serve --port <free>`), open
     /api/feed with your own plan's params via curl, confirm events stream
     as your session works (you ARE a live worker — your own transcript is
     a fixture). Record a sample response in the completion summary.
     (Browser check is operator-side post-ship; curl-level proof suffices.)

4. **Commit + attest + complete.**
   - Findings: decoder-extraction decision; anything the operator should
     check in the browser post-ship.
   - Structured commit: `serve-activity-feed: phase feed — cursor endpoint +
     sticky-scroll activity pane`.
   - Stage explicit paths: `end_of_line/webserver.py`,
     `end_of_line/web/index.html`, `tests/test_webserver.py` (+
     `end_of_line/top.py` + `tests/test_top.py` if the decoder extracted),
     `docs/reference.md` (+ master if findings logged).
   - After the commit:
     - `clu verify --plan serve-activity-feed --phase feed --token <T>`
     - `clu attest --simplify --plan serve-activity-feed --phase feed --token <T>`
   - `clu complete --plan serve-activity-feed --phase feed --token <T>`.

## Failure modes to watch

- **Don't leak transcript content past the gates**: the endpoint must sit
  AFTER the auth gate in `_dispatch` and respect `include_transcript` — a
  feed reachable without the token on a `--lan` bind is a security
  regression (/security-review territory).
- **Path traversal via params**: plan/phase go through `validate_slug`;
  proj is matched against registry entries, never joined into a path.
- **Transcript lines can be megabytes** (embedded file contents) — the
  256KB read cap + 400-char event truncation are load-bearing; don't lift
  them for "completeness."
- **Sticky scroll jank**: append-then-scroll causes flicker if the check
  happens after DOM mutation — measure scroll position BEFORE appending.
- **Sandbox suite caveat**: judge green by `clu verify` (~30 known
  in-sandbox environment failures; `socket.bind` blocks the webserver tests
  in YOUR shell — clu verify exercises them for real).
