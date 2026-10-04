#!/usr/bin/env python3
"""Minimal dashboard for the interruptible voice agent.

    DASHBOARD_EVENTS=/tmp/dashboard_events.jsonl python -m dashboard.server
    -> http://127.0.0.1:8000

Four endpoints, no build step, no framework beyond what the repo already
installs (fastapi + uvicorn, already required by agent/kokoro_server.py):

  GET  /            the page
  GET  /token       a LiveKit join token so the browser's mic can enter a room
  GET  /events      server-sent events, tailing the executor's own JSONL sink
  POST /nav         runs the existing extension demo and returns its real output

Every event shown in the browser is a line the running agent wrote through
agent/dashboard_events.py -- the same dict the executor emitted. Nothing is
generated here for display.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import config as C  # noqa: E402

EVENTS = os.getenv("DASHBOARD_EVENTS", "/tmp/dashboard_events.jsonl")
PAGE = Path(__file__).resolve().parent / "index.html"

app = FastAPI()


@app.get("/")
def index():
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))


@app.get("/config")
def config():
    return {
        "planner": C.LLM_MODEL,
        "fallback": C.LLM_FALLBACK_MODEL,
        "stt": C.STT_MODEL,
        "tts": f"{C.TTS_MODEL}/{C.TTS_VOICE}",
        "max_tool_steps": C.MAX_TOOL_STEPS,
        "endpointing": f"{C.MIN_ENDPOINTING_DELAY}/{C.MAX_ENDPOINTING_DELAY}",
        "livekit_url": C.env("LIVEKIT_URL") or "",
        "events_file": EVENTS,
    }


@app.get("/token")
def token(room: str = "dashboard", identity: str = "browser-mic"):
    """Mint a LiveKit join token so the browser can publish its microphone."""
    from livekit import api

    key, secret = C.env("LIVEKIT_API_KEY"), C.env("LIVEKIT_API_SECRET")
    if not key or not secret:
        return JSONResponse({"error": "LIVEKIT_API_KEY/SECRET not set"}, 500)
    jwt = (
        api.AccessToken(key, secret)
        .with_identity(identity)
        .with_name("Dashboard mic")
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .to_jwt()
    )
    return {"token": jwt, "url": C.env("LIVEKIT_URL"), "room": room}


@app.get("/events")
async def events():
    """Tail the executor's JSONL sink as SSE. Starts from the end of the file so
    a reload shows the next interaction, not the whole history."""

    async def gen():
        path = Path(EVENTS)
        path.touch(exist_ok=True)
        with path.open("r") as f:
            f.seek(0, os.SEEK_END)
            yield f"data: {json.dumps({'kind': 'STREAM_OPEN', 't': time.time()})}\n\n"
            idle = 0.0
            while True:
                line = f.readline()
                if not line:
                    await asyncio.sleep(0.25)
                    idle += 0.25
                    if idle >= 15:            # keep-alive through proxies
                        idle = 0.0
                        yield ": ping\n\n"
                    continue
                idle = 0.0
                line = line.strip()
                if line:
                    yield f"data: {line}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/nav")
def nav():
    """Run the existing in-car extension and return its real output.

    Not reimplemented here: this shells out to `python -m extension.demo --both`
    so the dashboard shows the same executor behaviour the test suite checks.
    """
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, "-m", "extension.demo", "--both"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    import re
    clean = re.sub(r"\x1b\[[0-9;]*m", "", proc.stdout)
    return {
        "exit_code": proc.returncode,
        "passed": proc.returncode == 0,
        "seconds": round(time.time() - t0, 2),
        "output": clean,
        "stderr": proc.stderr[-2000:],
    }


if __name__ == "__main__":
    print(f"dashboard: http://127.0.0.1:8000   events file: {EVENTS}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
