"""Latency tracker, output format kept compatible with FDB-v3's heartbeat parser."""

import json
import logging
import time

from agent.config import HEARTBEAT_LOG_PATH


class LatencyTracker:
    def __init__(self):
        self.user_done_at = 0
        self.tool_start_at = 0
        self.tool_end_at = 0
        self.agent_start_at = 0
        self.query_received = False

    def reset(self):
        self.__init__()

    def log_breakdown(self, room_name: str, tool_name: str = "Search Tool"):
        if not self.user_done_at or not self.agent_start_at:
            return
        # A turn that correctly calls no tool still has a reasoning + synthesis
        # breakdown worth recording; only the execution phase is absent.
        reasoning_end = self.tool_start_at or self.agent_start_at
        metrics = {
            "room": room_name,
            "tool": tool_name if self.tool_start_at else "none",
            "reasoning": round(reasoning_end - self.user_done_at, 3),
            "execution": round((self.tool_end_at - self.tool_start_at) if self.tool_end_at else 0, 3),
            "synthesis": round(self.agent_start_at - (self.tool_end_at or self.user_done_at), 3),
            "total": round(self.agent_start_at - self.user_done_at, 3),
            "agent_start_at": self.agent_start_at,
        }
        line = f"LATENCY_TRACK_JSON: {json.dumps(metrics)}"
        logging.info(line)
        with open(HEARTBEAT_LOG_PATH, "a") as f:
            f.write(line + "\n")


def heartbeat(msg: str):
    with open(HEARTBEAT_LOG_PATH, "a") as f:
        f.write(f"{msg} at {time.ctime()}\n")
