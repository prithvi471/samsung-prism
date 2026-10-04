"""All runtime knobs, read from environment (.env). One codebase, many profiles."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def env(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name)
    return val if val not in (None, "") else default


# FDB-v3 checkout: we import its mock_apis / latency_injector unchanged.
FDB_V3_DIR = Path(env("FDB_V3_DIR", str(ROOT / "third_party" / "Full-Duplex-Bench" / "v3")))
if str(FDB_V3_DIR) not in sys.path:
    sys.path.insert(0, str(FDB_V3_DIR))

# Telemetry paths the harness reads (hardcoded in run_tool_benchmark.py).
TOOL_LOG_PATH = "/tmp/agent_tool_calls.log"
HEARTBEAT_LOG_PATH = "/tmp/agent_heartbeat.log"

# Mock API latency profile (template default is "instant").
MOCK_LATENCY = env("MOCK_LATENCY", "instant")

# ── STT ───────────────────────────────────────────────────────────
STT_BASE_URL = env("STT_BASE_URL", "https://api.groq.com/openai/v1")
STT_API_KEY = env("STT_API_KEY", env("GROQ_API_KEY"))
STT_MODEL = env("STT_MODEL", "whisper-large-v3-turbo")

# ── Planner LLM (primary + fallback, both OpenAI-compatible) ─────
LLM_BASE_URL = env("LLM_BASE_URL", "https://ollama.com/v1")
LLM_API_KEY = env("LLM_API_KEY", env("OLLAMA_API_KEY"))
LLM_MODEL = env("LLM_MODEL", "gpt-oss:120b")
LLM_FALLBACK_BASE_URL = env("LLM_FALLBACK_BASE_URL", LLM_BASE_URL)
LLM_FALLBACK_API_KEY = env("LLM_FALLBACK_API_KEY", LLM_API_KEY)
LLM_FALLBACK_MODEL = env("LLM_FALLBACK_MODEL", "gemma4:31b")  # verify exact tag on Ollama Cloud
LLM_REASONING_EFFORT = env("LLM_REASONING_EFFORT", "low")
LLM_TEMPERATURE = float(env("LLM_TEMPERATURE", "0"))
LLM_TIMEOUT_S = float(env("LLM_TIMEOUT_S", "15"))

# ── TTS (Kokoro via Kokoro-FastAPI, OpenAI-compatible) ───────────
TTS_BASE_URL = env("TTS_BASE_URL", "http://localhost:8880/v1")
TTS_API_KEY = env("TTS_API_KEY", "not-needed")
TTS_MODEL = env("TTS_MODEL", "kokoro")
TTS_VOICE = env("TTS_VOICE", "af_heart")

# ── Turn taking ───────────────────────────────────────────────────
# LiveKit caps consecutive tool-calling LLM turns at 3 by default. A 3-step
# dependency chain (search -> commute -> filter) needs one step per dependent
# call plus a final step to speak, which sits exactly on that limit; the worker
# log shows "maximum number of function calls steps reached" firing 12 times
# across 4 rooms in the 100-scenario run, and housing_24 lost calculate_commute
# to it. Raised, and kept env-swappable so the change is reversible.
MAX_TOOL_STEPS = int(env("MAX_TOOL_STEPS", "8"))

TURN_DETECTOR = env("TURN_DETECTOR", "english")  # english | multilingual | none
MIN_ENDPOINTING_DELAY = float(env("MIN_ENDPOINTING_DELAY", "0.5"))
MAX_ENDPOINTING_DELAY = float(env("MAX_ENDPOINTING_DELAY", "3.0"))
VAD_MIN_SILENCE = float(env("VAD_MIN_SILENCE", "0.55"))
