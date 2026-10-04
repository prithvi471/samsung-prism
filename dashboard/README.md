# Dashboard — status: BUILT, NOT YET VERIFIED END-TO-END

Every event the page displays is a line the running agent wrote through
`agent/dashboard_events.py` — the same dict the executor emitted. Nothing is
synthesised for display. But **the full browser → mic → LiveKit → STT → planner
→ tools → executor → TTS loop has not been run**, so treat this as untested
until you do the procedure below.

## Start the backend/agent (terminal 1)

```bash
wsl.exe -d Ubuntu-22.04 -- bash -lc "cd /root/Samsung && . .venv/bin/activate && export FDB_V3_DIR=/root/Samsung/third_party/Full-Duplex-Bench/v3 && export DASHBOARD_EVENTS=/tmp/dashboard_events.jsonl && : > \$DASHBOARD_EVENTS && python -m agent.main start"
```

Wait for `registered worker`. Kokoro must already be serving on `:8880`:

```bash
wsl.exe -d Ubuntu-22.04 -- bash -lc "curl -sf http://127.0.0.1:8880/v1/models || (cd /root/Samsung && . .venv/bin/activate && nohup python -m agent.kokoro_server >/tmp/kokoro.log 2>&1 & sleep 25; curl -sf http://127.0.0.1:8880/v1/models)"
```

## Start the dashboard (terminal 2)

```bash
wsl.exe -d Ubuntu-22.04 -- bash -lc "cd /root/Samsung && . .venv/bin/activate && export DASHBOARD_EVENTS=/tmp/dashboard_events.jsonl && python -m dashboard.server"
```

## URL

```
http://127.0.0.1:8000
```

WSL forwards localhost to Windows, so open that in a normal browser. **Use
`127.0.0.1`, not the WSL IP** — browsers only grant microphone access on
`localhost`/`127.0.0.1` or HTTPS, so a LAN address will silently fail.

## Demo procedure

1. Open the URL. The header should show the live config (planner, STT, TTS,
   steps, endpointing) — that proves `/config` reached the real `agent.config`.
2. Click **Start voice**, allow the microphone. The dot turns green and the
   status reads `live in room "dashboard" — speak now`.
3. Say, in **one breath, without pausing**:
   > "Find apartments with a maximum price of 3000 — wait, actually 3500."

   Expected on screen: `INTENT_CREATED` → `TASK_STARTED` → `INTENT_REVISED`
   (version increments, badge turns red) → `TASK_STALE` → `TASK_COMMITTED`, with
   the committed set showing `max_price 3500` and **not** 3000.

   **The one-breath part matters.** The commit barrier fires when the agent
   starts speaking; pause long enough for the turn to end and `3000` commits
   irreversibly, giving two committed calls. That is the documented limitation,
   not a bug.
4. Click **Run in-car extension**. This shells out to
   `python -m extension.demo --both` and shows its real output: unprotected
   applies the retracted destination and starts navigation twice; protected
   computes 3 routes, applies 2, never touches Chennai Central.
5. For the navigation flow by voice, the agent has no navigation tools — those
   live in `extension/nav.py`, exercised by the button. The voice path covers
   the 12 FDB-v3 tools.

## Safe shutdown

```bash
wsl.exe -d Ubuntu-22.04 -- bash -lc "pkill -f 'dashboard.server'; pkill -f 'agent.main'; echo stopped"
```

Leave Kokoro running if you want it warm; otherwise add `pkill -f kokoro_server`.

## What is verified, and what is not

| | |
|---|---|
| Executor events reach a JSONL sink | ✅ wired, env-gated, 24/24 tests still pass |
| Sink is inert for benchmarks | ✅ `run_fdb_v3.sh` never sets `DASHBOARD_EVENTS` |
| Extension endpoint runs the real demo | ✅ shells out, no reimplementation |
| Dashboard serves / SSE streams / token mints | ❌ **not run** |
| Browser microphone → LiveKit → agent | ❌ **not run** (no microphone on the build machine) |

The honest gap: I could not speak into a microphone, so the mic leg is
unexercised. If `/token` or the CDN `livekit-client` load fails, the page will
report it in the status line rather than failing silently.

## Known risks

- `livekit-client` loads from jsDelivr; offline or CSP-restricted environments
  need it vendored locally.
- `/events` starts at end-of-file, so a reload shows the *next* interaction, not
  history. Restart the agent with `: > $DASHBOARD_EVENTS` for a clean slate.
- The agent-response panel depends on `conversation_item_added` carrying
  `text_content`; if a LiveKit version changes that field the panel stays empty
  while every executor event still works.
