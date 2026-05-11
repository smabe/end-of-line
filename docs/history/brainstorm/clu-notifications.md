# clu Notifications — Channel Bake-off

## Verdict

**Primary: iMessage via the MCP you already have. Fallback: Pushover ($4.99 one-time) for guaranteed AFK delivery.** iMessage wins on every axis that matters here — zero new infra, two-way works today via `chat.db` polling + Messages.app outbound, the user already talks to Claude on it, and there's no second app to install on the phone. The only weakness is "Mac must be on" (chat.db is local). That's a real but small risk for a Mac-primary solo dev. Pushover patches the AFK-Mac-asleep hole with a $5 lifetime fee, and its priority 2 / emergency retry is the closest thing to a pager you can buy. Discord/Telegram bots are tempting but they're more code, more tokens to rotate, and more failure surface — and they don't earn their keep against an iMessage MCP that's already running. Run iMessage as the workhorse, mirror only `halted` and `stale` events to Pushover as belt-and-suspenders.

## Bake-off table

| Channel | Outbound | Inbound 1-tap | Setup friction | Cost | Mac-locked? | Maint (yrs) |
|---|---|---|---|---|---|---|
| **iMessage (existing MCP)** | 5 | 3 (reply text, polled) | 1 (done) | $0 | Yes | 5 — Apple-stable, no token rotation |
| **Pushover** | 5 | 1 (no native reply; must SSH) | 2 | $4.99 once | No (HTTPS REST) | 5 |
| **Telegram Bot** | 5 | 5 (inline keyboard buttons) | 3 (BotFather + webhook or long-poll) | $0 | No | 4 — token + webhook TLS |
| **Discord Bot** | 4 (push reliability on iOS is meh) | 4 (button components) | 4 (gateway WS or webhook + app) | $0 | No | 3 — discord.py churn, intents migrations |
| **ntfy.sh (hosted)** | 4 | 3 (http action buttons call back to your URL) | 2 | $0 | No | 4 — depends on host uptime; self-host = +ops |
| **Slack personal** | 3 (mobile push flaky on free tier) | 4 (block kit buttons) | 4 (app + bot scopes) | $0 | No | 3 — scope/app reauths |
| **Twilio SMS** | 5 (carrier-grade) | 5 (reply text, webhook) | 4 (account + 10DLC + number) | ~$1.15/mo + $0.008/msg in & out | No | 3 — 10DLC compliance churn |
| **macOS osascript** | 2 (only at Mac) | 1 | 1 | $0 | Yes | 5 |
| **Pushbullet** | 3 | 2 | 3 | $0/freemium | No | 2 — moribund |

Scores 1-5, higher is better except setup-friction where higher = more painful.

### Pricing footnotes (verified)
- **Pushover**: $4.99 one-time per platform (iOS/Android/Desktop); 10,000 msgs/month free pool per account as of May 2026.
- **Telegram**: free, 30 msgs/sec global, 1 msg/sec per chat. Inline buttons capped at 1-64 chars `callback_data`.
- **Twilio US long code**: $0.0079 outbound / $0.0075 inbound per segment + $1.15/mo number. Real cost ~$2-3/mo at this volume, but you'll hit 10DLC registration friction.
- **ntfy.sh**: free hosted, action buttons can issue arbitrary HTTP POST — bidirectional if you stand up an endpoint.

## Two-way deep-dive

The scenario: AFK, blocker fires with `["A","B","C"]`. How does the answer get back to `clu answer q-1 0`?

**iMessage (recommended).** clu sends a Messages.app message via `osascript` or the existing MCP's send path: `"q-1: Pick framework. Reply 0/1/2 — [0] A, [1] B, [2] C"`. A background daemon (`launchd` LaunchAgent, runs while user is logged in) polls `~/Library/Messages/chat.db` every 3-5s for new rows in `message` where `is_from_me=0` and `handle_id` matches the user's own number. Regex `^([0-2])$` (or option text), look up the most recent open `q-*`, exec `clu answer q-1 N` locally. Same daemon = same machine, no network. Latency: 3-5s polling + iMessage E2E (sub-second). Works from anywhere with cell/WiFi as long as Mac is awake.

**Telegram (close second).** Inline keyboard with three buttons, each `callback_data="q-1:0"`. Bot runs as a `getUpdates` long-poll loop (no webhook TLS needed) on the Mac. Button tap → `callback_query` → daemon execs `clu answer q-1 0`. Tap-to-answer is genuinely one-tap, which iMessage can't match. But: BotFather, token in config, app install, and an extra messenger the user isn't already living in.

**Discord.** Same shape but heavier: `discord.py` + gateway WebSocket, `app_commands` for buttons, intents config. iOS Discord push has well-known reliability gaps (often delayed or coalesced). Not worth it over Telegram.

**Pushover.** Sends great, receives nothing. Path: notification arrives → user opens Termius/Blink → `ssh mac && clu answer q-1 0`. Friction is real but tolerable as a *fallback* — by the time you're SSH'ing on your phone, you've accepted the situation. Pushover's killer feature is **priority 2 / emergency retry** (re-alerts every N seconds until ack), which is the right tool for `halted` and `stale` events.

**Twilio SMS.** Bulletproof but overkill. The Mac being asleep won't stop the SMS, but it WILL stop `clu answer` from executing — so SMS doesn't actually solve "Mac off." It just degrades to the same Pushover-style "user has to remote into the Mac to answer" path. Not worth the 10DLC paperwork.

**ntfy.sh action buttons.** Interesting third path: action button POSTs to `https://<your-mac>.tailnet.ts.net/answer/q-1/0` and a tiny FastAPI shim execs `clu answer`. Requires Tailscale (the user almost certainly has it) and one HTTP endpoint. Cleaner than Pushover for two-way, free, but adds an app and an endpoint vs. zero net-new with iMessage.

## Implementation sketch

Drop into `end_of_line/notify.py`. Implements iMessage primary + Pushover fallback. Inbound is handled by a sibling `notify_inbound.py` polling chat.db.

```python
# end_of_line/notify.py
from __future__ import annotations
import json, subprocess, time, urllib.request, urllib.parse
from dataclasses import dataclass
from pathlib import Path

CFG = json.loads(Path(".orchestrator.json").read_text())["notify"]
# .orchestrator.json:
# "notify": {
#   "primary": "imessage",
#   "fallback": "pushover",
#   "imessage": {"to": "+15551234567"},
#   "pushover": {"token": "axxx", "user": "uxxx"},
#   "quiet_hours": {"start": 23, "end": 7}  # local time, suppress non-critical
# }

@dataclass
class Blocker:
    qid: str          # "q-1"
    prompt: str       # "Pick web framework"
    options: list[str]  # ["FastAPI", "Flask", "Starlette"]

def _imessage_send(to: str, body: str) -> None:
    # Outbound via Messages.app AppleScript. No daemons required.
    script = f'tell application "Messages" to send "{body}" to buddy "{to}" of service 1st service whose service type = iMessage'
    subprocess.run(["osascript", "-e", script], check=True, timeout=10)

def _pushover_send(token: str, user: str, body: str, *, priority: int = 0, title: str | None = None) -> None:
    data = urllib.parse.urlencode({
        "token": token, "user": user, "message": body,
        "priority": priority, **({"title": title} if title else {}),
        # priority=2 → emergency retry; requires "retry" and "expire"
        **({"retry": 60, "expire": 3600} if priority == 2 else {}),
    }).encode()
    req = urllib.request.Request("https://api.pushover.net/1/messages.json", data=data)
    urllib.request.urlopen(req, timeout=10).read()

def notify_blocker(b: Blocker) -> None:
    opts = "\n".join(f"[{i}] {o}" for i, o in enumerate(b.options))
    body = f"{b.qid}: {b.prompt}\n{opts}\n\nReply with the number (e.g. 0)."
    _imessage_send(CFG["imessage"]["to"], body)

def notify_halted(plan: str, phase: str, log_tail: str) -> None:
    body = f"⛔ {plan} halted at phase {phase}\n\n{log_tail[-400:]}"
    _imessage_send(CFG["imessage"]["to"], body)
    # Belt-and-suspenders: also Pushover at priority 1 (bypass quiet hours)
    p = CFG["pushover"]
    _pushover_send(p["token"], p["user"], body, priority=1, title=f"clu halted: {plan}")

def notify_stale(qid: str, age_hours: int) -> None:
    p = CFG["pushover"]
    _pushover_send(p["token"], p["user"],
                   f"{qid} unanswered for {age_hours}h",
                   priority=2,  # emergency: keep re-alerting
                   title="clu stale blocker")

def notify_complete(plan: str, commits: int, simplify_fixes: int) -> None:
    _imessage_send(CFG["imessage"]["to"],
                   f"✅ {plan} done — {commits} commits, {simplify_fixes} simplify cleanups.")
```

**Inbound (sibling file, runs as `launchd` LaunchAgent):**

```python
# end_of_line/notify_inbound.py — polls chat.db, dispatches clu answer
import re, sqlite3, subprocess, time
from pathlib import Path

DB = Path.home() / "Library/Messages/chat.db"
SEEN = Path.home() / ".clu/seen_msg_rowid"
ANSWER_RE = re.compile(r"^\s*(?:(q-\d+)\s+)?([0-9])\s*$")

def latest_open_qid() -> str | None:
    # ask clu daemon for the most recent unanswered blocker
    out = subprocess.run(["clu", "questions", "--open", "--json"],
                         capture_output=True, text=True, check=True).stdout
    qs = __import__("json").loads(out)
    return qs[0]["id"] if qs else None

def poll_once(conn: sqlite3.Connection, last_rowid: int) -> int:
    rows = conn.execute("""
        SELECT ROWID, text FROM message
        WHERE ROWID > ? AND is_from_me = 0 AND text IS NOT NULL
        ORDER BY ROWID ASC
    """, (last_rowid,)).fetchall()
    for rowid, text in rows:
        m = ANSWER_RE.match(text or "")
        if not m: continue
        qid = m.group(1) or latest_open_qid()
        if not qid: continue
        subprocess.run(["clu", "answer", qid, m.group(2)], check=False)
        last_rowid = rowid
    return rows[-1][0] if rows else last_rowid

def main():
    last = int(SEEN.read_text()) if SEEN.exists() else 0
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    while True:
        try:
            last = poll_once(conn, last)
            SEEN.write_text(str(last))
        except Exception as e:
            print("poll error:", e)
        time.sleep(4)

if __name__ == "__main__": main()
```

LaunchAgent plist at `~/Library/LaunchAgents/com.clu.inbound.plist` with `KeepAlive=true` and `RunAtLoad=true`. Done.

## Anti-patterns

1. **"Just SSH it" as the only inbound path.** SSH-from-phone is fine as a fallback, not a primary. The whole point of two-way is letting muscle memory take over at 11pm — typing a one-digit reply beats unlocking Termius.
2. **Discord because it's "developer-default."** iOS Discord push is unreliable enough that you'll miss blockers. Don't put a flaky transport on the critical path.
3. **Self-hosted ntfy on the same Mac that just hung the orchestrator.** If the Mac is the problem, hosting the notification escape hatch on it defeats the purpose.
4. **Twilio for the "AFK insurance" slot.** SMS arrives, but `clu answer` still needs the Mac up. You're paying $2-3/mo for nothing iMessage doesn't already deliver.
5. **A web dashboard.** Tempting, never built. Adds auth, TLS, hosting. clu is a CLI; keep the loop CLI-shaped.
6. **Inline reply parsing that's too clever.** Accept only `^[0-9]$` or `qid N`. Free-text NL parsing of "the second one I think" will bite within a week.
7. **No quiet hours.** Add `quiet_hours` to config and gate everything except `halted` + priority-2 stale. The user already flagged the "don't ping at 11pm" sensibility.
8. **Token in `.orchestrator.json` committed to git.** Put secrets in `~/.clu/secrets.json` and `chmod 600`. Keep `.orchestrator.json` for non-sensitive prefs only.

## Open questions for the user

1. **Quiet hours policy:** confirm the cutoff (current sketch: 23:00-07:00 local, all events suppressed except `halted` and priority-2 `stale`).
2. **Phone number for iMessage:** which handle? Self-chat works if the MCP already routes it, otherwise the user's own +1 number is the cleanest.
3. **Free-text answers allowed?** e.g. blocker is "name the branch" with no enumerated options. iMessage handles this trivially; Telegram buttons can't. Confirms iMessage as primary.
4. **Heartbeat / dispatch events:** strong recommend OFF. Phase-start pings will train the user to ignore notifications. Log to `clu status` instead.
5. **Pushover worth it?** $4.99 one-time. If the user already owns it for other things, free win. If not, ask: how often is the Mac actually asleep when a `halted` event fires? If "basically never," skip Pushover and rely on iMessage alone — strip the file down to one channel.
6. **Tailscale present?** If yes, ntfy + Tailscale + HTTP action button is a viable swap-in for Pushover (free, one-tap two-way). Worth a phase-2 upgrade if the user gets annoyed with the Pushover→SSH path.

## Sources

- [Pushover pricing](https://pushover.net/pricing)
- [Pushover API limit changes May 2026](https://blog.pushover.net/posts/2026/4/app-limits)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Telegram webhooks guide](https://core.telegram.org/bots/webhooks)
- [ntfy.sh publish docs (action buttons)](https://docs.ntfy.sh/publish/)
- [Twilio US SMS pricing](https://www.twilio.com/en-us/sms/pricing/us)
- [Twilio SMS API cost breakdown 2026](https://apidog.com/blog/twilio-sms-api-cost/)
