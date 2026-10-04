# Dashboard — backend chain VERIFIED; the microphone click is yours

Every event the page displays is a line the running agent wrote through
`agent/dashboard_events.py` — the same dict the executor emitted. Nothing is
synthesised for display.

**Verified by driving real human audio into the same LiveKit room the dashboard
watches** (`livekit_inference.py --room dashboard`), which exercises every leg
except a person speaking:

```
TASK_STARTED     update_search_filter {pets_allowed: true}
INTENT_REVISED   version 2, "retraction cue in transcript"
TASK_STARTED     update_search_filter {max_price: 3500}
TASK_COMMITTED   x2        COMMIT_BARRIER {calls: 2}
LATENCY          {first_response_s: 16.536}
AGENT_STATE      speaking
SPEECH           assistant: "Search updated with a $3,500 max price for
                 pet-friendly places in San Francisco."
TTS audio        9.11s non-silent, peak 16307
committed set    pets_allowed=true, max_price=3500   (3000 absent)
```

Endpoints verified: `/config` returns the live config, `/token` mints a 357-char
LiveKit JWT, `/` serves, `/events` streams, `/nav` returns the real extension
output (`passed=true`). The page renders with all panels and the SSE stream
connects. **The only unexercised leg is the browser microphone**, because the
build machine has none — that is the click below.

## Start the backend/agent (terminal 1)

```bash
wsl.exe -d Ubuntu-22.04 -u root -- bash -lc "cd /root/Samsung && . .venv/bin/activate && export FDB_V3_DIR=/root/Samsung/third_party/Full-Duplex-Bench/v3 && export DASHBOARD_EVENTS=/tmp/dashboard_events.jsonl && : > \$DASHBOARD_EVENTS && python -m agent.main start"
```

Wait for `registered worker`. Kokoro must already be serving on `:8880`:

```bash
wsl.exe -d Ubuntu-22.04 -u root -- bash -lc "curl -sf http://127.0.0.1:8880/v1/models || (cd /root/Samsung && . .venv/bin/activate && nohup python -m agent.kokoro_server >/tmp/kokoro.log 2>&1 & sleep 25; curl -sf http://127.0.0.1:8880/v1/models)"
```

## Start the dashboard (terminal 2)

```bash
wsl.exe -d Ubuntu-22.04 -u root -- bash -lc "cd /root/Samsung && . .venv/bin/activate && export DASHBOARD_EVENTS=/tmp/dashboard_events.jsonl && python -m dashboard.server"
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
wsl.exe -d Ubuntu-22.04 -u root -- bash -lc "pkill -f 'dashboard.server'; pkill -f 'agent.main'; echo stopped"
```

Leave Kokoro running if you want it warm; otherwise add `pkill -f kokoro_server`.

## What is verified, and what is not

| | |
|---|---|
| Executor events reach a JSONL sink | ✅ wired, env-gated, 24/24 tests still pass |
| Sink is inert for benchmarks | ✅ `run_fdb_v3.sh` never sets `DASHBOARD_EVENTS` |
| Extension endpoint runs the real demo | ✅ shells out, no reimplementation |
| Dashboard serves / SSE streams / token mints | ✅ verified |
| LiveKit → STT → planner → tools → executor → TTS | ✅ verified with real audio |
| Page renders, SSE connects | ✅ verified |
| Browser microphone → LiveKit | ❌ **not run** (no microphone on the build machine) |

The only gap is the mic click. If `/token` or the CDN `livekit-client` load
fails, the page reports it in the status line rather than failing silently.

**Note on the WSL user:** the distro's default user is now `prithvi13`, but the
repo, venv and artifacts live under `/root/Samsung`, so every command above uses
`-u root`. Without it you get `cd: /root/Samsung: Permission denied`.

## Known risks

- `livekit-client` loads from jsDelivr; offline or CSP-restricted environments
  need it vendored locally.
- `/events` starts at end-of-file, so a reload shows the *next* interaction, not
  history. Restart the agent with `: > $DASHBOARD_EVENTS` for a clean slate.
- The agent-response panel depends on `conversation_item_added` carrying
  `text_content`; if a LiveKit version changes that field the panel stays empty
  while every executor event still works.
