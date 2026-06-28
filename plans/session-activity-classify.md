# session-activity-classify — render session rows distinctly + resolve their feed

You are phase `classify` of the `session-activity` plan. It delivers one commit: session rows render as a distinct class in both surfaces (curses `clu top` + web `clu serve`), and the web detail pane can stream a session's transcript via `/api/feed` keyed by session-id (sessions have no plan/phase to resolve through).

## Locked decisions (do NOT re-litigate)
See master `plans/session-activity.md`. Binding here:
- Classification ordering mirrors the existing `blocked`-before-`alive` pattern: check `session` BEFORE the dead/alive branch in every render surface (a session row has `alive` absent ⇒ falsy ⇒ would mis-read as `dead`).
- Session marker label: `sess` (parallels `blk`/`ok`/`dead`). PHASE cell for a session: `—` (no `x/N`).
- Feed route for sessions adds query param `sid=<session-id>` + `proj=<project basename>`; the existing `plan`/`phase` route is untouched for workers.

## Work
- `end_of_line/top.py` (curses cells):
  - **`_liveness_cell`** (top.py:460 @7dbe001): add `if r.get("session"): return "sess"` BEFORE the `blocked`/`alive` checks.
  - **`_phase_cell`** (top.py:453): return `—` when `r.get("session")` (no sessions index).
  - **`_row_cells` / name** (top.py:470): for a session row the NAME column should show `session_name` (and project), not `project/plan·phase_id` (plan/phase are `None`). Build name as `f"{project} · {session_name}"` when `session`, else the existing form.
  - **`format_detail`** (top.py:557): a session block has no PHASE/ATT/LEASE lines (all `None` → already omitted); ensure the meta line's liveness uses `_liveness_cell` (shows `sess`) and the header uses the session name. Confirm no `KeyError` on the absent claim keys (they're `None` from `assemble_session_row`).
- `end_of_line/top_registry.py`:
  - **`_m_health`** (~:305 @7dbe001): add `if row.get("session"): return "session"` before the `worker_health(...)` call (mirror the `blocked` branch). Add a `"session"` style mapping wherever `"blocked"` is styled (color/marker registry) so the row reads distinctly in the table panes.
  - Verify the PID/progress metric cells route through `top._liveness_cell`/`_phase_cell` (so the `sess`/`—` from top.py flow through) — if `top_registry` has its own pair renderer (`_render_pair`), branch it on `session` the same way it branches on `blocked`.
- `end_of_line/webserver.py` (feed resolution by session-id):
  - **`resolve_feed_transcript`** (webserver.py:482 @7dbe001) currently keys on `(plan, proj, phase)` via the live claim. Add a session branch: a new `resolve_session_transcript(proj, sid, *, project_filter, projects_root) -> tuple[Path, str] | None` that finds the registered entry whose `Path(e.project_root).name == proj`, then `top.locate_transcript(project_root, session_id=sid)` and returns `(path, path.stem)`. Reuse, don't duplicate, the registry-scan + `locate_transcript` shape.
  - **`feed_json`** (webserver.py:524): when the query carries `sid` (and not `plan`/`phase`), route to the session resolver; else the existing claim resolver. Validate `sid` as a slug-safe token (it's a UUID — `validate_slug` accepts `[a-z0-9][a-z0-9_-]{0,63}`; a UUID with hyphens passes). Keep the same `read_feed_window` + `record_events` tail + `{events,cursor,tid,reset}` body.
- `end_of_line/web/index.html`:
  - **`toView`** (index.html:277–306 @7dbe001): set `health = r.session ? "session" : r.blocked ? "blocked" : …` (session checked first, before `alive`). Carry `session`, `session_name`, `session_id`, `project` into the view model.
  - **badge** (patchRow ~:408): emit a `<span class="badge sess">SESS</span>` for session rows; add a `.badge.sess` CSS rule (style distinct from `.blk`/`.dead`). Row label uses `session_name`.
  - **feed request** (~:595–597): when the selected row is a session, build the query as `proj=…&sid=…&cursor=…(&tid=…)` instead of `plan/proj/phase`. Branch on `w.session`.
- `docs/reference.md`: add `session`/`session_name`/`session_id` to the D10 row-contract note (reference.md:1213) and document the `?sid=` feed route in the webserver section (~:1255–1278).
- `tests/`: curses — session row renders `sess` liveness + `—` phase + name from `session_name` (extend `test_top_sessions.py` or the render test module). webserver — `feed_json` with `sid` resolves a session transcript and returns events; with a bad/unknown `sid` → 404; claim route still works. web `toView` is JS (no python test) — assert via a comment/manual note, or if the project has an index.html render test harness, extend it (grep `tests/` for index.html coverage).

## Decisions & findings
### Carried from P1 `discover` /code-review (xhigh) — these are REQUIRED, not optional
P1 ships session rows in `gather_rows` that render with the GENERIC cells until
this phase routes on `session`. Two confirmed findings name the exact gap; both
are already in this shard's Work, flagged here so they aren't dropped:
- **alive=None reads as `dead`.** A session row sets `alive=None`. `_liveness_cell`
  (top.py) and `top_registry._m_health` only special-case `blocked`, so a LIVE
  session shows red `dead`/health glyph in curses; web `toView` (index.html:278
  `r.alive !== false`) reads `null` as alive → the two surfaces disagree. Fix:
  branch on `session` BEFORE the dead path in ALL of `_liveness_cell`,
  `_m_health`, and web `toView` (mirror the `blocked` ordering).
- **`session_name` is never read.** The NAME cell is built from
  `project/plan·phase_id` (`_row_cells`, `top_registry._m_name`); a session has
  `plan=None`,`phase_id=None`, so it renders `<project>/None·None`. Fix: NAME
  reads `session_name` for session rows.

### Decision: feed keys sessions by `(proj, sid)`, reusing `locate_transcript`  *(status: active)*
- **Rationale:** a session has no claim/worktree, so the worker resolver's `(plan, proj, phase) → claim → worktree-cwd` path doesn't apply. But the session's cwd IS the registered `project_root` (discovery only emits sessions found in that encoded dir), so `locate_transcript(project_root, session_id=sid)` re-resolves the exact file statelessly — same primitive the worker path uses, no new transcript-finding logic.
- **Alternatives considered:** pass the absolute transcript path from client → server — rejected (lets a client request any file on disk; the registry-scoped resolver is the security boundary, mirroring `resolve_feed_transcript`'s "proj matched against basenames, never joined into a path").
- **Evidence:** `resolve_feed_transcript` registry-scan + `locate_transcript` webserver.py:502–520; path-safety note webserver.py:498–500 (@7dbe001).

## Failure modes to anticipate
- **`alive`-absent mis-render as `dead`:** the whole reason session is checked first. A missed branch in ANY of the four surfaces (curses `_liveness_cell`, `top_registry._m_health`, web `toView`, web badge) shows a live session as dead. Test each surface.
- **`sid` validation:** a UUID has hyphens; confirm `validate_slug` passes a real session id (it does per the regex) — but a `tid`-only or empty `sid` must 400, not crash.
- **Feed route ambiguity:** a request carrying BOTH `plan` and `sid` — define precedence (prefer `sid` if present, else claim route) and test it, so a stale client query can't dead-end.
- **Name with control chars / very long title:** `customTitle` is user input; `_clean`/`_fit` already collapse + truncate in curses, and the web escapes — confirm the session name flows through the same sanitizers, not raw.
- **top_registry style registry miss:** if `"session"` health has no color mapping, the pane may throw or render blank — add the mapping alongside `"blocked"` and exercise it in the registry render test.

## Done criteria
- `clu top` shows a session row: NAME = session name, PID cell = `sess`, PHASE = `—`, distinct health styling; worker/blocked rows unchanged.
- `clu serve` web badges session rows `SESS`, distinct from plans; clicking one streams its transcript (feed via `?proj=&sid=`).
- `feed_json` session route tested (resolves, 404 on unknown, claim route intact, precedence defined).
- `docs/reference.md` updated (row keys + `?sid=` route).
- Full suite green; `/code-review` run on the diff.
