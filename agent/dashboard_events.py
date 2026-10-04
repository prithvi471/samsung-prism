"""Optional JSONL event sink, for the dashboard to read.

Inert unless DASHBOARD_EVENTS names a file, so benchmark runs are byte-for-byte
unaffected: scripts/run_fdb_v3.sh never sets it.

Every line the dashboard displays comes through here from the real executor and
the real AgentSession. Nothing is synthesised for display.
"""

from __future__ import annotations

import json
import os
import time

PATH = os.getenv("DASHBOARD_EVENTS", "")


def enabled() -> bool:
    return bool(PATH)


def emit(event, /, **data) -> None:
    """Append one event line.

    `event` is positional-only: the executor's own payload carries a `kind`
    field (a tool's read-only vs state-changing classification), and a keyword
    parameter named `kind` collided with it -- `emit() got multiple values for
    argument 'kind'`. That TypeError propagated out of the event callback and
    through the executor, failing every tool call: 22 TASK_STARTED, nothing
    committed, the agent never spoke. The payload's own `kind` is kept under
    `tool_kind` so neither value is lost.

    Wrapped in try/except because telemetry must never break a turn -- which is
    exactly what it did before this fix.
    """
    if not PATH:
        return
    try:
        if "kind" in data:
            data["tool_kind"] = data.pop("kind")
        with open(PATH, "a") as f:
            f.write(json.dumps({"t": time.time(), "kind": event, **data},
                               default=str) + "\n")
    except Exception:  # noqa: BLE001  never let telemetry break a turn
        pass
