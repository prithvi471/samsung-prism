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


def emit(kind: str, **data) -> None:
    if not PATH:
        return
    try:
        with open(PATH, "a") as f:
            f.write(json.dumps({"t": time.time(), "kind": kind, **data},
                               default=str) + "\n")
    except Exception:  # noqa: BLE001  never let telemetry break a turn
        pass
