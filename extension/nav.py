"""In-car navigation: a simulated head unit with a real, mutable state.

The benchmark's mock APIs are pure, so a stale tool call there costs you a
wrong line in a log. In a car it costs you a wrong turn. This extension keeps
the same architecture but splits each tool into the two halves the invariant
is about:

    compute(...)  — expensive, pure, safe to run speculatively
    apply(...)    — mutates the head unit, and must never run for a stale call

agent/revision.py already draws that line: it executes `compute` eagerly and
calls `commit` only for the calls that survive to the barrier. Here `commit`
is what actually moves the car.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from agent.revision import ToolKind, register_tool

# Deterministic "map": address -> (canonical name, drive minutes, distance km)
KNOWN_PLACES = {
    "chennai central": ("Chennai Central Railway Station", 18, 7.4),
    "chennai airport": ("Chennai International Airport", 41, 21.3),
    "the airport": ("Chennai International Airport", 41, 21.3),
    "airport": ("Chennai International Airport", 41, 21.3),
    "marina beach": ("Marina Beach", 12, 4.1),
    "t nagar": ("T. Nagar", 22, 9.0),
}

ROUTE_COMPUTE_SECONDS = 0.30  # route solving is the expensive, pure half


def _lookup(address: str):
    key = " ".join((address or "").lower().replace(",", " ").split())
    if key in KNOWN_PLACES:
        return KNOWN_PLACES[key]
    for name, value in KNOWN_PLACES.items():
        if name in key or key in name:
            return value
    return (address, 30, 15.0)


@dataclass
class HeadUnit:
    """The side-effecting half. Only committed calls ever touch this."""

    destination: str | None = None
    navigating: bool = False
    eta_min: int | None = None
    applied: list[str] = field(default_factory=list)

    def apply(self, tool: str, result: dict) -> None:
        if tool == "set_destination":
            self.destination = result["destination"]
            self.eta_min = result["eta_min"]
            self.applied.append(f"set_destination -> {self.destination} (ETA {self.eta_min} min)")
        elif tool == "start_navigation":
            self.navigating = True
            self.applied.append(f"start_navigation -> guiding to {self.destination}")
        elif tool == "cancel_navigation":
            self.navigating = False
            self.applied.append("cancel_navigation")

    def summary(self) -> str:
        state = "guiding" if self.navigating else "idle"
        return f"destination={self.destination!r} eta={self.eta_min} state={state}"


class NavComputer:
    """The pure half. Safe to run for a request that is about to be retracted."""

    def __init__(self, head_unit: HeadUnit, compute_seconds: float = ROUTE_COMPUTE_SECONDS):
        self.head_unit = head_unit
        self.compute_seconds = compute_seconds
        self.computed: list[tuple[str, dict]] = []

    def __call__(self, tool: str, **kwargs) -> dict:
        self.computed.append((tool, dict(kwargs)))
        if tool == "set_destination":
            time.sleep(self.compute_seconds)  # route solving
            name, minutes, km = _lookup(kwargs["address"])
            return {"status": "success", "destination": name,
                    "eta_min": minutes, "distance_km": km}
        if tool == "start_navigation":
            return {"status": "success", "guidance": "started"}
        if tool == "cancel_navigation":
            return {"status": "success", "guidance": "cancelled"}
        if tool == "search_charging_stations":  # read-only, never mutates
            return {"status": "success",
                    "stations": [{"id": "CS1", "name": "Anna Salai Fast Charge", "km": 2.1}]}
        return {"status": "error", "message": f"unknown tool {tool}"}


def register_nav_tools() -> None:
    """Declare the nav tools to the revision layer.

    set_destination and start_navigation are singletons: a later call on the
    same slot supersedes the earlier one, which is exactly what a destination
    change is. cancel_navigation and the station search are their own slots.
    """
    register_tool("set_destination", ToolKind.STATE_CHANGING, slot_keys=())
    register_tool("start_navigation", ToolKind.STATE_CHANGING, slot_keys=())
    register_tool("cancel_navigation", ToolKind.STATE_CHANGING, slot_keys=())
    register_tool("search_charging_stations", ToolKind.READ_ONLY, slot_keys=())
