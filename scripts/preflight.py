#!/usr/bin/env python3
"""Fail fast before a 100-scenario run: keys present, providers reachable,
planner models actually emit tool calls. Exit code != 0 on any failure."""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import config as C  # noqa: E402

from openai import OpenAI  # noqa: E402

TEST_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the weather for a city.",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    },
}

failures = []
warnings = []


def check(name, fn):
    try:
        detail = fn()
        print(f"  OK   {name}: {detail}")
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL {name}: {e}")
        failures.append(name)


def warn(name, fn):
    """Non-fatal check: the agent runs without it, but the benchmark score is
    degraded. Reported loudly so it cannot pass unnoticed."""
    try:
        print(f"  OK   {name}: {fn()}")
    except Exception as e:  # noqa: BLE001
        print(f"  WARN {name}: {e}")
        warnings.append(name)


def need_env():
    missing = [k for k, v in {
        "LIVEKIT_URL": C.env("LIVEKIT_URL"), "LIVEKIT_API_KEY": C.env("LIVEKIT_API_KEY"),
        "LIVEKIT_API_SECRET": C.env("LIVEKIT_API_SECRET"), "LLM_API_KEY/OLLAMA_API_KEY": C.LLM_API_KEY,
        "STT_API_KEY/GROQ_API_KEY": C.STT_API_KEY,
    }.items() if not v]
    if missing:
        raise RuntimeError("missing " + ", ".join(missing))
    return "all set"


def judge_reachable():
    """Actually call gpt-4o. Presence of OPENAI_API_KEY proves nothing: an
    invalid or unfunded key still lets --use-llm run, because the evaluators
    swallow the exception and silently fall back to exact string matching.
    A whole 100-scenario benchmark was scored that way before this check
    existed -- argument accuracy and response accuracy were meaningless, and
    the report looked normal."""
    if not C.env("OPENAI_API_KEY"):
        raise RuntimeError("no OPENAI_API_KEY; --use-llm will fall back to exact match")
    client = OpenAI(api_key=C.env("OPENAI_API_KEY"), timeout=30)
    r = client.chat.completions.create(
        model="gpt-4o", temperature=0, max_tokens=5,
        messages=[{"role": "user", "content": "reply with the single word: ready"}],
    )
    return f"gpt-4o responded {r.choices[0].message.content.strip()!r}"


def tool_call(model, base_url, api_key):
    def run():
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=C.LLM_TIMEOUT_S * 2)
        r = client.chat.completions.create(
            model=model, temperature=0, tools=[TEST_TOOL],
            messages=[{"role": "user", "content": "Um, what's the weather in, uh, Oslo? No wait, Bergen."}],
        )
        calls = r.choices[0].message.tool_calls or []
        if not calls:
            raise RuntimeError(f"no tool call returned: {r.choices[0].message.content!r}")
        return f"{calls[0].function.name}({calls[0].function.arguments})"
    return run


def stt_models():
    client = OpenAI(base_url=C.STT_BASE_URL, api_key=C.STT_API_KEY, timeout=20)
    ids = [m.id for m in client.models.list().data]
    if C.STT_MODEL not in ids:
        raise RuntimeError(f"{C.STT_MODEL} not listed")
    return C.STT_MODEL


def tts_speech():
    req = urllib.request.Request(
        f"{C.TTS_BASE_URL}/audio/speech", method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {C.TTS_API_KEY}"},
        data=json.dumps({"model": C.TTS_MODEL, "voice": C.TTS_VOICE, "input": "Ready.", "response_format": "wav"}).encode(),
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return f"{len(resp.read())} bytes audio"


print("Preflight:")
check("env", need_env)
check(f"planner {C.LLM_MODEL}", tool_call(C.LLM_MODEL, C.LLM_BASE_URL, C.LLM_API_KEY))
if C.LLM_FALLBACK_MODEL:
    check(f"fallback {C.LLM_FALLBACK_MODEL}", tool_call(C.LLM_FALLBACK_MODEL, C.LLM_FALLBACK_BASE_URL, C.LLM_FALLBACK_API_KEY))
check(f"stt {C.STT_BASE_URL}", stt_models)
check(f"tts {C.TTS_BASE_URL}", tts_speech)
warn("judge gpt-4o", judge_reachable)

if warnings:
    print(
        f"\nWARNING: {', '.join(warnings)} unavailable. The agent will run, but the\n"
        "FDB-v3 evaluators silently fall back to EXACT STRING MATCHING, which scores\n"
        "correct answers wrong (e.g. date '2026-07-15' vs 'July 15'). Benchmark\n"
        "argument accuracy and response accuracy would NOT be meaningful."
    )

if failures:
    print(f"\nPreflight FAILED: {', '.join(failures)}. Fix .env / services before running the benchmark.")
    sys.exit(1)
print("\nPreflight passed.")
