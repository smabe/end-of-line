# notify-multi-channel-schema — multi-channel config + auto-migration + enabled

You are phase `schema` of the `notify-multi-channel` plan. Extend `NotifySpec` with a `channels` list, add per-channel `enabled: bool` for soft-disable, add a load-time migration translating flat `notify.imessage.to` into a synthetic channels entry, and update the router to iterate channels with per-channel `kinds` filtering + `enabled` checks.

## Locked decisions (do NOT re-litigate)

See `plans/notify-multi-channel.md` §"Phase 2". Summary:
- `notify.channels: [{kind, kinds?, enabled?, ...backend-specific}, ...]`.
- Per-channel `kinds` filter optional (null/omitted = all kinds).
- Per-channel `enabled: bool` defaults to `true`; `false` keeps the channel in config (credentials preserved) but silences delivery.
- Auto-migration: old `notify.imessage.to` + no `channels` → synthesize single-entry channels list with `enabled: true`.
- Validation: `kind` required, per-kind required fields checked. Unknown kind = config-load error. Backend-not-yet-registered (Discord pre-phase-5) = router warn+skip, not crash.
- `quiet_hours` and `inbound_auto_tick` stay top-level (not per-channel).
- Dispatcher check order per channel: registry lookup → `enabled` → `kinds` filter → instantiate → send.

## Read first

- `end_of_line/config.py` lines 27-30 (`NotifySpec`), 68-96 (`load_project_config`).
- `end_of_line/notify.py` post-phase-1: router shape, `_NOTIFIER_REGISTRY`.
- `tests/test_config.py` — existing notify-block load tests (will need mechanical updates).
- `tests/test_notify.py` — existing dispatch tests (likewise mechanical).

## Produce

1. **Failing tests first** in `tests/test_config_channels.py`:
   - `test_load_migrates_flat_imessage_to_channels` — write `.orchestrator.json` with `notify.imessage.to`, load, assert `spec.channels` is 1-element list with `kind="imessage"`, matching `to`, and `enabled=True`.
   - `test_load_native_channels_list_unchanged` — new shape parses untouched.
   - `test_load_rejects_unknown_kind` — `notify.channels: [{kind: "telegram"}]` → ConfigError.
   - `test_load_rejects_missing_required_imessage_field` — `[{kind: "imessage"}]` (no `to`) → ConfigError.
   - `test_load_accepts_discord_kind_schema_only` — `[{kind: "discord", bot_token: "x", user_id: "y"}]` parses OK even though DiscordNotifier doesn't exist yet (schema-level validation only).
   - `test_load_channel_kinds_filter_defaults_to_none` — entry without `kinds` → `channel.kinds is None`.
   - `test_load_channel_enabled_defaults_true` — entry without `enabled` → `channel.enabled is True`.
   - `test_load_channel_enabled_false_persists` — entry with `enabled: false` parses, `channel.enabled is False`.
   - `test_load_migration_preserves_quiet_hours` — old config with both `imessage.to` and `quiet_hours` migrates cleanly, both fields survive.
   
   And in `tests/test_notify.py`:
   - `test_dispatcher_fires_only_matching_channels` — two channels, one with `kinds=frozenset({"halted"})`, one without filter; dispatching `KIND_BLOCKER` hits only the unfiltered one.
   - `test_dispatcher_fires_all_when_kinds_none` — single channel with `kinds=None`, dispatching any kind fires it.
   - `test_dispatcher_skips_disabled_channel` — channel with `enabled=False` is skipped silently (no warning, no send) regardless of kind match.
   - `test_dispatcher_skips_unregistered_kind_with_warning` — channel with `kind="discord"` pre-registration, dispatch logs warning, doesn't crash.
   - `test_quiet_hours_gate_applied_before_channel_loop` — quiet-hours active, `KIND_BLOCKER` dispatch → no channels fire.
   - `test_halt_bypass_works_across_channels` — quiet-hours active, `KIND_HALTED` dispatch → channels fire.

2. **Implementation.**
   - `end_of_line/config.py`:
     - New dataclass `ChannelSpec`:
       ```python
       @dataclass(frozen=True)
       class ChannelSpec:
           kind: str
           kinds: frozenset[str] | None = None  # None = all kinds
           enabled: bool = True
           params: dict[str, str] = field(default_factory=dict)  # backend-specific
       ```
     - Extend `NotifySpec` with `channels: tuple[ChannelSpec, ...]`. Remove `imessage_to` field (migration absorbs it). Keep `quiet_hours`, `inbound_auto_tick`.
     - `_validate_channel(raw: dict) -> ChannelSpec`:
       - `kind` in `{"imessage", "discord"}` else ConfigError.
       - imessage: `to` required (string).
       - discord: `bot_token` + `user_id` required (strings).
       - `kinds` optional list → `frozenset` or None.
       - `enabled` optional bool → defaults True.
     - Migration in `load_project_config()`:
       ```python
       notify_raw = raw.get("notify", {})
       channels_raw = notify_raw.get("channels")
       if channels_raw is None:
           legacy_to = notify_raw.get("imessage", {}).get("to")
           if legacy_to:
               channels_raw = [{"kind": "imessage", "to": legacy_to, "enabled": True}]
           else:
               channels_raw = []
       channels = tuple(_validate_channel(c) for c in channels_raw)
       ```
   - `end_of_line/notify.py`:
     - Router iterates `spec.channels`. Per-channel order: `notifier_cls = _NOTIFIER_REGISTRY.get(ch.kind)` (None → log+skip); `if not ch.enabled: continue`; `if ch.kinds is not None and kind not in ch.kinds: continue`; instantiate `notifier_cls.from_spec(ch)`; call `.send()`.
     - Add `from_spec(channel: ChannelSpec) -> Notifier` classmethod to each Notifier (`IMessageNotifier.from_spec` reads `channel.params["to"]`).
   - `end_of_line/notify_imessage.py`: add `from_spec()` classmethod.

3. **Acceptance.**
   - All new tests green.
   - Existing `tests/test_config.py` updated for the new NotifySpec shape (mechanical: `NotifySpec(imessage_to="+1...")` → `NotifySpec(channels=(ChannelSpec(kind="imessage", params={"to": "+1..."}),))`). Consider helper: `NotifySpec.imessage_only(to: str)` for test ergonomics.
   - Existing `tests/test_notify.py` updated likewise.
   - Manual smoke: an old-shape `.orchestrator.json` loads, `clu list` works, `clu block ...` fires iMessage as before.
   - Manual smoke: edit a channel to `enabled: false`, repeat — no iMessage fires, no errors.

4. **Commit + complete.**
   - Title: `notify-multi-channel: phase schema — channels list + enabled + flat-shape auto-migration`
   - Stage: `end_of_line/config.py`, `end_of_line/notify.py`, `end_of_line/notify_imessage.py` (add `from_spec`), `tests/test_config_channels.py`, `tests/test_notify.py`, `tests/test_config.py`.
   - `clu complete --plan notify-multi-channel --phase schema --token <T>`.

## Failure modes to watch

- **Migration silently dropping fields.** Test asserts `quiet_hours` survives.
- **NotifySpec test-construction churn.** Add the `NotifySpec.imessage_only()` factory to keep test diff bounded.
- **Backend-not-yet-registered.** Phase 2 ships before phase 5 — `kind: "discord"` validates at schema layer but router warn+skips. Locked test confirms this.
- **`enabled: false` silently skips.** Don't log a warning every dispatch — operator chose to disable, it's not a misconfiguration. Test asserts no warning logged.
- **Don't add Discord-specific code.** Schema knows about the `kind` and required fields; the Notifier itself ships phase 5.
- **`from_spec` API stability.** Adding it now means phase 5's `DiscordNotifier.from_spec` is plug-and-play.
