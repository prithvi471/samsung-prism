"""Revision-aware tool execution.

The benchmark scores the *telemetry log*, not the executions. Mock APIs are
pure, so running a tool is cheap and reversible; appending a line to
agent_tool_calls.log is the only irreversible act. This module separates the
two, which is what makes self-corrections survivable:

    A stale execution may finish computing, but it must never commit.

Three mechanisms, in the order they fire:

1. Deduplication — an identical (tool, args) call is never executed twice.
2. Slot supersession — calls are keyed by the *target* they act on, not by
   their arguments. "max price 3000 ... no, make it 3500" produces two calls
   on slot ('update_search_filter', 'max_price'); only the last one commits.
   Legitimate repeats survive because they differ in the target: two
   update_identity_doc calls with different doc_type are two different slots.
3. Intent versioning — every execution records the intent version it was
   planned under. A revision bumps the version, and anything planned under an
   older one is stale and never commits, even if it already ran.

Commit is deferred to a barrier (flush), so execution can start the instant a
tool is planned. The logged timestamps are the real execution times, so
deferring the write costs nothing on the latency metrics while letting the
committed set be decided once the turn has settled.

Pure Python: no livekit, no network, no I/O of its own. The LiveKit adapter is
in agent/tools.py; the in-car extension reuses this module unchanged.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

log = logging.getLogger("revision")


class ToolKind(Enum):
    READ_ONLY = "read_only"
    STATE_CHANGING = "state_changing"


# Which argument(s) identify the thing a call acts on. An empty tuple means the
# tool is a singleton: a second call supersedes the first. These are derived
# from the benchmark's own ground truth — every scenario that legitimately
# expects two calls to one function differs in exactly these arguments.
SLOT_KEYS: dict[str, tuple[str, ...]] = {
    "search_flights": (),
    "book_flight": (),
    "update_identity_doc": ("doc_type",),
    "get_card_benefits": (),
    "get_exchange_rate": ("from_currency", "to_currency"),
    "modify_autopay": ("bill_type",),
    "search_apartments": (),
    "calculate_commute": (),
    "update_search_filter": ("filter_name",),
    "track_order": ("order_id",),
    "search_products": (),
    "add_to_cart": ("product_id",),
}

STATE_CHANGING: frozenset[str] = frozenset({
    "book_flight",
    "update_identity_doc",
    "modify_autopay",
    "update_search_filter",
    "add_to_cart",
})

# Cues that retract whatever came before them in the same utterance.
_RETRACTION = re.compile(
    r"\b(no wait|wait,? actually|actually,? (?:make|change|go|use)"
    r"|scratch that|make that|instead of that|i mean|sorry,? i meant"
    r"|change that to|on second thought|forget (?:that|what i said))\b",
    re.IGNORECASE,
)


def detect_retraction(text: str) -> bool:
    """True if the text contains a cue that cancels an earlier value."""
    return bool(_RETRACTION.search(text or ""))


_EXTRA_KINDS: dict[str, ToolKind] = {}


def register_tool(name: str, kind: ToolKind, slot_keys: tuple[str, ...] = ()) -> None:
    """Declare a tool outside the benchmark's 12 (used by extension/)."""
    _EXTRA_KINDS[name] = kind
    SLOT_KEYS[name] = slot_keys


def classify(tool: str) -> ToolKind:
    if tool in _EXTRA_KINDS:
        return _EXTRA_KINDS[tool]
    return ToolKind.STATE_CHANGING if tool in STATE_CHANGING else ToolKind.READ_ONLY


def _normalize(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return " ".join(value.lower().replace("_", " ").split())
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def dedup_key(tool: str, args: dict) -> str:
    norm = {k: _normalize(v) for k, v in sorted(args.items()) if v is not None}
    return tool + ":" + json.dumps(norm, sort_keys=True, default=str)


def slot_of(tool: str, args: dict) -> tuple:
    keys = SLOT_KEYS.get(tool, ())
    return (tool,) + tuple(_normalize(args.get(k)) for k in keys)


@dataclass
class Execution:
    execution_id: str
    tool: str
    args: dict
    kind: ToolKind
    intent_version: int
    slot: tuple
    key: str
    seq: int
    parent: str | None = None
    t_start: float = 0.0
    t_end: float = 0.0
    result: Any = None
    state: str = "planned"  # planned running stale cancelled committed

    def as_log_entry(self) -> dict:
        return {
            "function": self.tool,
            "args": self.args,
            "timestamp_start": self.t_start,
            "timestamp_end": self.t_end,
        }


class RevisionAwareExecutor:
    """Executes tools eagerly, commits them once, and only if still current."""

    def __init__(
        self,
        run_tool: Callable[..., Any],
        commit: Callable[[Execution], None] | None = None,
        events: Callable[[str, dict], None] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self._run_tool = run_tool
        self._commit = commit or (lambda rec: None)
        self._emit = events or (lambda name, data: None)
        self._clock = clock

        self._version = 0
        self._seq = itertools.count()
        self._pending: list[Execution] = []
        self._committed: list[Execution] = []
        self._results: dict[str, Any] = {}
        self._inflight: dict[str, asyncio.Future] = {}

    # ── intent lifecycle ────────────────────────────────────────────
    @property
    def version(self) -> int:
        return self._version

    def new_intent(self, text: str = "") -> int:
        self._version += 1
        self._event("INTENT_CREATED", version=self._version, text=text)
        return self._version

    def revise(self, reason: str = "", text: str = "") -> int:
        """Bump the intent version. Everything planned earlier becomes stale."""
        self._version += 1
        superseded = [e.execution_id for e in self._pending
                      if e.intent_version < self._version
                      and e.state in ("planned", "running")]
        self._event("INTENT_REVISED", version=self._version, reason=reason,
                    text=text, supersedes=superseded)
        for e in self._pending:
            if e.intent_version < self._version and e.state == "planned":
                e.state = "cancelled"
                self._event("TASK_CANCELLED", execution_id=e.execution_id,
                            tool=e.tool, reason="intent revised before start")
        return self._version

    def observe_transcript(self, text: str) -> int:
        """Bump the version if the text retracts something."""
        if detect_retraction(text):
            return self.revise(reason="retraction cue in transcript", text=text)
        return self._version

    # ── execution ───────────────────────────────────────────────────
    async def run(self, tool: str, args: dict, *, parent: str | None = None,
                  version: int | None = None,
                  cancelled: Callable[[], bool] | None = None) -> str:
        args = {k: v for k, v in args.items() if v is not None}
        key = dedup_key(tool, args)
        planned_at = self._version if version is None else version

        if key in self._results:
            self._event("DUPLICATE_SUPPRESSED", tool=tool, args=args, key=key)
            return json.dumps(self._results[key])

        if (fut := self._inflight.get(key)) is not None:
            self._event("DUPLICATE_SUPPRESSED", tool=tool, args=args, key=key,
                        inflight=True)
            return json.dumps(await fut)

        rec = Execution(
            execution_id=uuid.uuid4().hex[:12],
            tool=tool, args=args, kind=classify(tool),
            intent_version=planned_at, slot=slot_of(tool, args), key=key,
            seq=next(self._seq), parent=parent,
        )
        self._pending.append(rec)

        # Already stale before we even start.
        if rec.intent_version < self._version:
            rec.state = "stale"
            self._event("TASK_STALE", execution_id=rec.execution_id, tool=tool,
                        planned_at=rec.intent_version, current=self._version,
                        phase="before_start")
            if rec.kind is ToolKind.STATE_CHANGING:
                return json.dumps({"status": "cancelled",
                                   "reason": "superseded by a correction"})

        if cancelled is not None and cancelled():
            rec.state = "cancelled"
            self._event("TASK_CANCELLED", execution_id=rec.execution_id, tool=tool,
                        reason="speech handle interrupted")
            return json.dumps({"status": "cancelled",
                               "reason": "user changed the request"})

        fut = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        was_stale = rec.state == "stale"
        rec.state = "running"
        rec.t_start = self._clock()
        self._event("TASK_STARTED", execution_id=rec.execution_id, tool=tool,
                    args=args, kind=rec.kind.value,
                    intent_version=rec.intent_version, parent=parent)
        try:
            result = await asyncio.to_thread(self._run_tool, tool, **args)
        except BaseException as exc:
            rec.state = "cancelled"
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(key, None)
        rec.t_end = self._clock()
        rec.result = result
        if not fut.done():
            fut.set_result(result)
        self._results[key] = result

        # Re-validate AFTER execution — the window the point-sample check missed.
        if rec.intent_version < self._version:
            rec.state = "stale"
            if not was_stale:
                self._event("TASK_STALE", execution_id=rec.execution_id, tool=tool,
                            planned_at=rec.intent_version, current=self._version,
                            phase="after_execution")
            if rec.kind is ToolKind.STATE_CHANGING:
                return json.dumps({"status": "cancelled",
                                   "reason": "superseded by a correction"})
        elif not was_stale:
            rec.state = "planned"  # ran clean; awaiting the commit barrier

        return json.dumps(result)

    # ── commit barrier ──────────────────────────────────────────────
    def survivors(self) -> list[Execution]:
        """Last writer wins per slot, among records still current."""
        best: dict[tuple, Execution] = {}
        for rec in self._pending:
            if rec.state in ("cancelled", "stale"):
                continue
            if rec.intent_version < self._version:
                continue
            if rec.t_start == 0.0:
                continue  # never actually ran
            prev = best.get(rec.slot)
            if prev is None or rec.seq > prev.seq:
                best[rec.slot] = rec
        return sorted(best.values(), key=lambda r: r.seq)

    def flush(self) -> list[Execution]:
        """Commit the surviving set, in the order the calls were issued."""
        keep = self.survivors()
        kept = {r.execution_id for r in keep}
        for rec in self._pending:
            if rec.execution_id in kept or rec.state in ("cancelled", "stale"):
                continue
            rec.state = "stale"
            self._event("TASK_STALE", execution_id=rec.execution_id, tool=rec.tool,
                        slot=list(rec.slot), phase="superseded_by_slot")
        for rec in keep:
            rec.state = "committed"
            self._commit(rec)
            self._committed.append(rec)
            self._event("TASK_COMMITTED", execution_id=rec.execution_id,
                        tool=rec.tool, args=rec.args, slot=list(rec.slot),
                        intent_version=rec.intent_version)
        self._pending = []
        return keep

    @property
    def committed(self) -> list[Execution]:
        return list(self._committed)

    def _event(self, name: str, **data: Any) -> None:
        log.info("%s %s", name, json.dumps(data, default=str))
        self._emit(name, data)
