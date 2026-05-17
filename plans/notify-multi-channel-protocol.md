# notify-multi-channel-protocol — Notifier + InboundPoller extraction

You are phase `protocol` of the `notify-multi-channel` plan. Extract iMessage-specific outbound + inbound logic from `notify.py` and `notify_inbound.py` behind two Protocols, in a single refactor commit that changes zero behavior.

## Locked decisions (do NOT re-litigate)

See `plans/notify-multi-channel.md` §"Phase 1". Summary:
- Two Protocols (`Notifier`, `InboundPoller`) in new `end_of_line/notify_base.py`.
- iMessage code splits into `notify_imessage.py` (outbound) and `notify_imessage_inbound.py` (inbound).
- `notify.notify()` becomes a thin router using `_NOTIFIER_REGISTRY` dict.
- Render helpers stay in `notify.py`.
- `route_reply()` + `OpenBlocker` + `Reply` extracted to `notify_base.py`.
- **No behavior change.** All existing tests green.

## Read first

- `end_of_line/notify.py` lines 1-90 (constants, osascript invocation at 47-69, dispatcher at 93-142).
- `end_of_line/notify_inbound.py` lines 1-50 (constants, regex at line 37, POLL_BATCH_LIMIT) and 54-145 (`open_blockers_for_host`, `route_reply`, `_cli_dispatch`).
- `end_of_line/config.py` lines 27-30 (NotifySpec — passed into `notify()`; stays typed).
- `tests/test_notify.py` — patterns to preserve.
- `tests/test_notify_inbound.py` — patterns to preserve.
- `tests/test_notify_render.py` — render helpers stay put, tests untouched.

## Produce

1. **Failing tests first** in `tests/test_notify_protocols.py`:
   - `test_notifier_is_runtime_checkable_protocol` — `isinstance(IMessageNotifier(...), Notifier)` passes via `@runtime_checkable`.
   - `test_inbound_poller_is_runtime_checkable_protocol` — same for `IMessageInboundPoller`.
   - `test_imessage_notifier_kind_name_is_imessage` — `IMessageNotifier(to="+1...").kind_name == "imessage"`.
   - `test_route_reply_lives_in_notify_base` — `from end_of_line.notify_base import route_reply` works; smoke-call returns expected `Reply` for `("plan-slug 1", [<open blocker>])`.
   - `test_notify_registry_contains_imessage` — `_NOTIFIER_REGISTRY["imessage"] is IMessageNotifier`.

2. **Implementation.**
   - `end_of_line/notify_base.py`: define `Notifier` and `InboundPoller` Protocols with `@runtime_checkable`. Move `route_reply()` verbatim from `notify_inbound.py`. Move `Reply` and `OpenBlocker` dataclasses.
   - `end_of_line/notify_imessage.py`: new module. `IMessageNotifier` class implementing `Notifier`. Constructor takes `to: str`. `send()` wraps the existing `_osascript_send()` (move it here). `kind_name = "imessage"`.
   - `end_of_line/notify_imessage_inbound.py`: new module. `IMessageInboundPoller` class implementing `InboundPoller`. Constructor takes `db_path: Path | None = None` + `registry_loader: Callable | None = None` for DI. `poll()` wraps the chat.db SQL query.
   - `end_of_line/notify.py`: keep render helpers + KIND_* constants + quiet-hours gate. Replace `notify()` body with a router. For phase 1, keep the existing `spec.imessage_to` field working — route it through `IMessageNotifier` internally. Phase 2 swaps in the channels-list iteration.
   - `end_of_line/notify_inbound.py`: becomes a thin `__main__` shim that constructs `IMessageInboundPoller()` and runs its `.poll()` in the cron loop. Re-export `route_reply` and `OpenBlocker` from `notify_base` at module level so existing imports don't break.
   - `_NOTIFIER_REGISTRY = {"imessage": IMessageNotifier}` lives in `notify.py` (the router module).

3. **Acceptance.**
   - 5 new tests in `tests/test_notify_protocols.py` green.
   - All existing `tests/test_notify*.py` + `tests/test_config.py` green (mechanical import updates only).
   - `grep -n osascript end_of_line/notify.py` returns nothing.
   - `python3 -m end_of_line.cli list` runs without error.
   - `python3 -m end_of_line.notify_inbound --help` (if it has a CLI) still works.

4. **Commit + complete.**
   - Title: `notify-multi-channel: phase protocol — Notifier/InboundPoller protocols + iMessage extraction`
   - Stage: `end_of_line/notify.py`, `end_of_line/notify_base.py`, `end_of_line/notify_imessage.py`, `end_of_line/notify_imessage_inbound.py`, `end_of_line/notify_inbound.py`, `tests/test_notify_protocols.py`.
   - `clu complete --plan notify-multi-channel --phase protocol --token <T>`.

## Failure modes to watch

- **Circular imports.** `notify.py` (router) imports `notify_imessage`; the impl should NOT import `notify.py`. Shared types live in `notify_base.py`. Registry registration stays in `notify.py`.
- **Test import-path drift.** Use re-exports from old module paths to keep diff small (`from .notify_imessage_inbound import *` at top of `notify_inbound.py`).
- **Module-level state.** Verify `notify_inbound.py`'s seen-rowid cursor path stays at `~/.clu/seen_msg_rowid` after extraction.
- **Don't expand surface.** Pure extraction only. No new features. No refactoring of unrelated code.
- **Phase 2 dependency.** The router shape assumes `spec.channels` arrives in phase 2; for phase 1, keep the existing single-iMessage path working and let phase 2 replace it.
