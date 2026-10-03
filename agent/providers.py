"""Builds VAD / STT / LLM / TTS from env. Every model is swappable by config."""

import logging

from livekit.agents import llm
from livekit.plugins import openai, silero

# Imported for its side effect: the package calls Plugin.register_plugin(), which
# is what makes "python -m agent.main download-files" fetch the turn detector's
# model files. Without this the plugin is only imported lazily inside
# build_turn_detector(), so the download step silently skips it and the job
# crashes at session start with: Could not find file "languages.json".
# The model classes themselves stay lazily imported in build_turn_detector().
from livekit.plugins import turn_detector  # noqa: F401

from agent import config as C

log = logging.getLogger("providers")


def build_vad():
    return silero.VAD.load(min_speech_duration=0.05, min_silence_duration=C.VAD_MIN_SILENCE)


def build_stt():
    # Groq (default) or any OpenAI-compatible Whisper server, e.g. a local
    # faster-whisper server, by changing STT_BASE_URL.
    return openai.STT(model=C.STT_MODEL, base_url=C.STT_BASE_URL, api_key=C.STT_API_KEY, language="en")


def _openai_llm(model: str, base_url: str, api_key: str):
    kwargs = dict(model=model, base_url=base_url, api_key=api_key, temperature=C.LLM_TEMPERATURE)
    if C.LLM_REASONING_EFFORT and model.startswith("gpt-oss"):
        kwargs["reasoning_effort"] = C.LLM_REASONING_EFFORT
    try:
        return openai.LLM(**kwargs)
    except TypeError:
        # Older plugin versions lack reasoning_effort; fall back without it.
        kwargs.pop("reasoning_effort", None)
        log.warning("openai.LLM does not accept reasoning_effort; using model default")
        return openai.LLM(**kwargs)


def build_llm():
    primary = _openai_llm(C.LLM_MODEL, C.LLM_BASE_URL, C.LLM_API_KEY)
    if not C.LLM_FALLBACK_MODEL:
        return primary
    fallback = _openai_llm(C.LLM_FALLBACK_MODEL, C.LLM_FALLBACK_BASE_URL, C.LLM_FALLBACK_API_KEY)
    return llm.FallbackAdapter([primary, fallback], attempt_timeout=C.LLM_TIMEOUT_S)


def build_tts():
    return openai.TTS(model=C.TTS_MODEL, voice=C.TTS_VOICE, base_url=C.TTS_BASE_URL,
                      api_key=C.TTS_API_KEY, response_format="wav")


def build_turn_detector():
    if C.TURN_DETECTOR == "english":
        from livekit.plugins.turn_detector.english import EnglishModel
        return EnglishModel()
    if C.TURN_DETECTOR == "multilingual":
        from livekit.plugins.turn_detector.multilingual import MultilingualModel
        return MultilingualModel()
    return None
