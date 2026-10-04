#!/usr/bin/env python3
"""LiveKit worker entrypoint for the FDB-v3 benchmark agent.

    python -m agent.main download-files   # turn-detector + VAD weights, once
    python -m agent.main start            # production worker (used by the harness)
    python -m agent.main console          # talk to it with your mic
"""

import logging
import time

from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, llm

from agent import config as C
from agent import providers
from agent.latency import LatencyTracker, heartbeat
from agent.prompts import PLANNER_INSTRUCTIONS
from agent.tools import AssistantFnc
from mock_apis import MockAPIRegistry  # from FDB_V3_DIR, unchanged

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("agent")

server = AgentServer()


class PlannerAgent(Agent):
    def __init__(self, tools):
        super().__init__(instructions=PLANNER_INSTRUCTIONS, tools=tools)


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext):
    room = ctx.room.name
    heartbeat(f"!!! AGENT JOINING ROOM: {room} !!!")

    # Everything below is per room: no state survives across scenarios.
    registry = MockAPIRegistry(latency_profile=C.MOCK_LATENCY)
    tracker = LatencyTracker()
    fnc = AssistantFnc(tracker, room, registry)
    tools = llm.find_function_tools(fnc)

    session = AgentSession(
        vad=providers.build_vad(),
        stt=providers.build_stt(),
        llm=providers.build_llm(),
        tts=providers.build_tts(),
        turn_detection=providers.build_turn_detector(),
        min_endpointing_delay=C.MIN_ENDPOINTING_DELAY,
        max_endpointing_delay=C.MAX_ENDPOINTING_DELAY,
        max_tool_steps=C.MAX_TOOL_STEPS,
        allow_interruptions=True,
    )

    @session.on("user_input_transcribed")
    def on_user_input(ev: agents.voice.UserInputTranscribedEvent):
        log.info("STT (final=%s): %s", ev.is_final, ev.transcript)
        if ev.is_final:
            # A retraction cue bumps the intent version, which makes anything
            # already planned or running under the old version non-committable.
            fnc.observe_transcript(ev.transcript)
            if not tracker.query_received:
                tracker.user_done_at = time.time()
                tracker.query_received = True

    @session.on("agent_state_changed")
    def on_agent_state(ev: agents.voice.AgentStateChangedEvent):
        if ev.new_state == "speaking":
            # Commit barrier: by the time the agent speaks, this turn's tool
            # calls have settled, so the surviving set is final.
            committed = fnc.flush()
            if committed:
                log.info("commit barrier: %d call(s) written for room %s", committed, room)
            if tracker.query_received and not tracker.agent_start_at:
                tracker.agent_start_at = time.time()
                tracker.log_breakdown(room_name=room)
                tracker.reset()

    async def _final_flush():
        # Safety net: never leave a surviving call uncommitted if the session
        # ends without the agent ever reaching the speaking state.
        if fnc.flush():
            log.info("shutdown flush for room %s", room)

    ctx.add_shutdown_callback(_final_flush)

    await session.start(room=ctx.room, agent=PlannerAgent(tools))
    log.info("agent started in room %s (llm=%s, fallback=%s)", room, C.LLM_MODEL, C.LLM_FALLBACK_MODEL)


if __name__ == "__main__":
    agents.cli.run_app(server)
