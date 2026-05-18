# notify-multi-channel-docs — init prompts, --no-notify, smoke command, docs

You are phase `docs` of the `notify-multi-channel` plan. Wire new channels into `clu init`'s interactive prompts, add the `clu --no-notify` global runtime suppression flag, add the `clu notify-test` smoke command, and update docs for three setup paths (iMessage / Discord / clu-watch-only) plus a "Suppressing notifications" reference section.

## Locked decisions (do NOT re-litigate)

See `plans/notify-multi-channel.md` §"Phase 7". Summary:
- Default: empty/unset `notify.channels` is valid → clu-watch-only mode. No silent OS-default fallback.
- `clu init` adds prompts (skippable with `--no-notify-prompt`): "Wire iMessage?" (default Y mac, N elsewhere), "Wire Discord?" (default N).
- `clu --no-notify <cmd>` global flag: short-circuits dispatch at the router entry for a single invocation. Useful for debug/dry-runs.
- `clu notify-test [--channel KIND]` fires a test notification through one or all configured channels, skipping disabled ones.
- Four docs sections: iMessage / Discord / clu-watch-only / Suppressing notifications.

## Read first

- `end_of_line/cli.py` `cmd_init` function — existing prompts, skip-flag patterns (e.g. `--no-claude-md`).
- `end_of_line/cli.py` argparse root setup — where global flags live (for `--no-notify`).
- `end_of_line/notify.py` post-phase-2: dispatcher entry point (where `--no-notify` short-circuits).
- `docs/operations.md` — current iMessage setup section + structure conventions.
- `docs/contract.md` — current notify schema section.
- `README.md` — current install + caveats.

## Produce

1. **Failing tests first** in `tests/test_cmd_init_notify_prompts.py`:
   - `test_init_no_notify_prompt_skips_prompts` — `cmd_init(..., notify_prompt=False)` → config has empty `channels: ()`.
   - `test_init_imessage_prompt_default_yes_on_macos` — `platform.system()` patched to `"Darwin"`, simulated "enter" → iMessage channel in config.
   - `test_init_imessage_prompt_default_no_off_mac` — patched to `"Linux"`, enter → no iMessage channel.
   - `test_init_discord_prompt_writes_channel_when_yes` — simulate "y" + bot_token + user_id input → Discord channel in config.
   - `test_init_no_notify_prompt_flag_recognized` — `clu init --no-notify-prompt ...` parses cleanly.
   
   And in `tests/test_global_no_notify_flag.py`:
   - `test_global_no_notify_flag_recognized_by_argparse` — `clu --no-notify list` parses cleanly.
   - `test_global_no_notify_short_circuits_dispatch` — invoke a command path that would normally fire `notify.notify()`; with `--no-notify` set, no channels are queried, no `.send()` calls happen.
   - `test_global_no_notify_does_not_affect_inbox_writes` — inbox events still written (clu-watch UX is orthogonal); only outbound transport is suppressed.
   
   And in `tests/test_cmd_notify_test.py`:
   - `test_notify_test_no_channels_configured` — config with empty channels → non-zero exit, helpful message ("No channels configured. Run `clu init` to add one, or edit `.orchestrator.json` directly.").
   - `test_notify_test_fires_one_channel_by_kind` — `--channel imessage` with iMessage configured, mock notifier, `.send()` called once.
   - `test_notify_test_fires_all_channels_when_no_filter` — multiple configured, all `.send()` called.
   - `test_notify_test_skips_disabled_channels` — one enabled + one disabled; `notify-test` only fires the enabled one, prints `<kind>: SKIPPED (disabled)` for the other.
   - `test_notify_test_reports_per_channel_status` — output line per channel: `imessage: OK` / `discord: FAILED (HTTPError 401)`.

2. **Implementation.**
   - `end_of_line/cli.py`:
     - Extend `cmd_init` with prompt block (use existing input helper if present):
       ```python
       if args.notify_prompt:
           channels = []
           if _prompt_yn("Wire iMessage?", default=platform.system() == "Darwin"):
               to = input("  iMessage handle (phone or email): ").strip()
               if to:
                   channels.append({"kind": "imessage", "to": to})
           if _prompt_yn("Wire Discord?", default=False):
               token = input("  Discord bot token: ").strip()
               user_id = input("  Discord user ID: ").strip()
               if token and user_id:
                   channels.append({"kind": "discord", "bot_token": token, "user_id": user_id})
           config["notify"]["channels"] = channels
       ```
     - Add `--no-notify-prompt` to `cmd_init` argparse (default True → prompts run).
     - Add `--no-notify` to the **root argparse parser** (global flag). Thread `args.no_notify` through to a module-level toggle or context that `notify.notify()` checks at entry. Cleanest: `notify.notify()` accepts an optional `_suppress: bool` parameter; CLI command dispatchers pass it through. Failing that, a `notify._GLOBAL_SUPPRESS` boolean toggled at CLI startup works for v1 — document the choice.
     - New `cmd_notify_test(args)`:
       ```python
       def cmd_notify_test(args):
           spec = config.load_project_config(args.project).notify
           channels = spec.channels
           if args.channel:
               channels = tuple(c for c in channels if c.kind == args.channel)
           if not channels:
               print("No channels configured...", file=sys.stderr)
               return ExitCode.CONFIG_ERROR
           for ch in channels:
               if not ch.enabled:
                   print(f"{ch.kind}: SKIPPED (disabled)")
                   continue
               notifier = notify._NOTIFIER_REGISTRY[ch.kind].from_spec(ch)
               try:
                   msg_id = notifier.send(KIND_COMPLETED, "clu notify smoke test", plan_slug="_test", blocker_id=None)
                   print(f"{ch.kind}: OK" + (f" (msg {msg_id})" if msg_id else ""))
               except Exception as e:
                   print(f"{ch.kind}: FAILED ({e!r})")
           return ExitCode.OK
       ```
     - Register `notify-test` subcommand in the argparse tree.
   - `end_of_line/notify.py`:
     - Add `_GLOBAL_SUPPRESS = False` module-level, plus `set_global_suppress(v: bool)` setter called from CLI startup when `--no-notify` is present.
     - `notify.notify()` short-circuits early if `_GLOBAL_SUPPRESS` is True (return False, log INFO once per process: "notifications suppressed via --no-notify").
   - `docs/operations.md` — add four top-level setup sections (mirror existing iMessage section's structure):
     - "Setup: iMessage (macOS only)" — existing content, minor edits.
     - "Setup: Discord (any OS)" — Developer Portal walkthrough (create app → bot → enable Message Content intent → create personal server → OAuth invite → enable DMs in server settings → copy user ID), config entry shape, LaunchAgent/systemd install pointing at `examples/clu.discord_inbound.plist` and `examples/clu-discord-inbound.service`.
     - "Setup: clu-watch only (zero external transport)" — note inbox-hook surface is always-on; just `/clu-monitor` for the hook install. `channels: []` is valid.
     - "Suppressing notifications" — covers the four levers: per-kind `kinds` filter, per-channel `enabled: false`, runtime `clu --no-notify <cmd>`, and `channels: []` for permanent silence. Brief example for each.
   - `docs/contract.md` — replace flat `notify.imessage.to` shape with new `notify.channels` list + ChannelSpec fields (`kind`, `kinds`, `enabled`, `params`) + migration semantics + halt-bypass policy.
   - `README.md` — drop "macOS only" caveat; "supports iMessage (macOS), Discord (any OS), or in-session-only mode."

3. **Acceptance.**
   - 14 new tests green.
   - Existing `cmd_init` tests green (may need `--no-notify-prompt` added to fixtures).
   - `clu --help` shows `--no-notify`.
   - `clu init --help` shows `--no-notify-prompt`.
   - `clu notify-test --help` works.
   - Manual smoke: `clu init` walks through prompts; answering N to both leaves `channels: []`; `clu notify-test` exits cleanly with "no channels" message.
   - Manual smoke: `clu --no-notify block --plan x ...` (or any command that fires notify) → no DM sent, log message visible.
   - `grep -i "macos only\|mac only" README.md` returns nothing.

4. **Commit + complete.**
   - Title: `notify-multi-channel: phase docs — init prompts, --no-notify flag, notify-test smoke, three setup paths (closes #11)`
   - Stage: `end_of_line/cli.py`, `end_of_line/notify.py`, `docs/operations.md`, `docs/contract.md`, `README.md`, `tests/test_cmd_init_notify_prompts.py`, `tests/test_global_no_notify_flag.py`, `tests/test_cmd_notify_test.py`.
   - `clu complete --plan notify-multi-channel --phase docs --token <T>`.

## Failure modes to watch

- **`cmd_init` is heavily tested.** Existing fixtures may pipe fixed input; new prompts break them. Run `tests/test_cmd_init*.py` early; use `--no-notify-prompt` in old fixtures.
- **`notify-test` should never blast real iMessage/Discord in CI.** Mock the Notifier; operator-side smoke is manual.
- **Global module-level state for `--no-notify`.** Module globals are fine for a CLI single-process invocation; don't propagate the pattern elsewhere. Test that the toggle resets between test invocations (or use a context-manager helper for cleaner test isolation).
- **`--no-notify` must not suppress inbox writes.** Inbox-hook is the clu-watch surface; it should fire regardless because operator's session UX depends on it. Only outbound transport is suppressed. Test asserts this.
- **Docs structure conventions.** Mirror existing section headings; don't introduce a new style.
- **README hyperbole.** "Supports macOS (iMessage), any OS (Discord), or in-session-only mode" — specific > marketing.
- **Closes #11 in commit title.** Final phase carries the closer (per project commit convention).
