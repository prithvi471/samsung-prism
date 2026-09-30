#!/usr/bin/env python3
"""In-car destination change under interruption — a deterministic demo.

    python -m extension.demo                 # with revision-aware execution
    python -m extension.demo --unprotected   # the same script without it
    python -m extension.demo --both          # run both and diff the outcome

The scenario, fixed:

    driver : "Navigate to Chennai Central."
             -> agent plans set_destination + start_navigation
             -> route solving begins (300 ms)
    driver : "Wait - actually go to the airport."   [arrives mid-computation]
             -> retraction cue bumps the intent version
    agent  : plans set_destination(airport) + start_navigation

What the protected run must show: the Chennai Central route finishes computing
and is then thrown away, the head unit is never set to Chennai Central, and
navigation starts exactly once, to the airport.

No network, no API keys, no GPU. Exits non-zero if the invariant is violated.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.revision import RevisionAwareExecutor  # noqa: E402
from extension.nav import HeadUnit, NavComputer, register_nav_tools  # noqa: E402

TURN_1 = "Navigate to Chennai Central."
TURN_2 = "Wait - actually go to the airport."
CORRECTION_DELAY = 0.10  # cue arrives 100 ms into a 300 ms route computation


def banner(text: str) -> None:
    print(f"\n\033[1;36m{'=' * 66}\n{text}\n{'=' * 66}\033[0m")


async def protected_run(verbose: bool = True) -> HeadUnit:
    register_nav_tools()
    head_unit = HeadUnit()
    computer = NavComputer(head_unit)
    events: list[tuple[str, dict]] = []

    def on_event(name, data):
        events.append((name, data))
        if verbose:
            detail = data.get("tool") or data.get("text") or data.get("reason") or ""
            print(f"  \033[0;33m{name:<21}\033[0m {detail}")

    ex = RevisionAwareExecutor(
        run_tool=computer,
        commit=lambda rec: head_unit.apply(rec.tool, rec.result),
        events=on_event,
    )

    print(f"\ndriver : {TURN_1}")
    v1 = ex.new_intent(TURN_1)

    async def interrupt():
        await asyncio.sleep(CORRECTION_DELAY)
        print(f"driver : {TURN_2}   \033[0;35m<- mid-computation\033[0m")
        ex.observe_transcript(TURN_2)

    # Turn 1's plan is dispatched, then the correction lands while it runs.
    await asyncio.gather(
        ex.run("set_destination", {"address": "Chennai Central"}, version=v1),
        interrupt(),
    )
    await ex.run("start_navigation", {}, version=v1)

    # Turn 2's plan, under the new intent version.
    v2 = ex.version
    await ex.run("set_destination", {"address": "the airport"}, version=v2)
    await ex.run("start_navigation", {}, version=v2)
    ex.flush()

    print(f"\nagent  : Switched to Chennai International Airport, {head_unit.eta_min} minutes.")
    print(f"\n  routes computed : {len(computer.computed)}")
    print(f"  state changes   : {head_unit.applied or ['(none)']}")
    print(f"  head unit       : {head_unit.summary()}")
    return head_unit


async def unprotected_run() -> HeadUnit:
    """The conventional design: every completed call mutates state."""
    head_unit = HeadUnit()
    computer = NavComputer(head_unit)

    print(f"\ndriver : {TURN_1}")

    async def call(tool, **kwargs):
        result = await asyncio.to_thread(computer, tool, **kwargs)
        head_unit.apply(tool, result)      # commit == execute
        return result

    async def interrupt():
        await asyncio.sleep(CORRECTION_DELAY)
        print(f"driver : {TURN_2}   \033[0;35m<- mid-computation\033[0m")

    await asyncio.gather(call("set_destination", address="Chennai Central"), interrupt())
    await call("start_navigation")
    await call("set_destination", address="the airport")
    await call("start_navigation")

    print(f"\n  routes computed : {len(computer.computed)}")
    print(f"  state changes   : {head_unit.applied}")
    print(f"  head unit       : {head_unit.summary()}")
    return head_unit


def check(head_unit: HeadUnit) -> list[str]:
    problems = []
    if head_unit.destination != "Chennai International Airport":
        problems.append(f"final destination is {head_unit.destination!r}, expected the airport")
    if any("Chennai Central" in line for line in head_unit.applied):
        problems.append("the retracted destination reached the head unit")
    if sum("start_navigation" in line for line in head_unit.applied) != 1:
        problems.append("navigation did not start exactly once")
    return problems


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unprotected", action="store_true")
    parser.add_argument("--both", action="store_true")
    args = parser.parse_args()

    if args.both or args.unprotected:
        banner("WITHOUT revision-aware execution")
        bad = await unprotected_run()
        problems = check(bad)
        print(f"\n  \033[0;31mviolations: {problems or 'none'}\033[0m")
        if not args.both:
            return 0

    banner("WITH revision-aware execution")
    good = await protected_run()
    problems = check(good)
    if problems:
        print(f"\n  \033[0;31mFAIL: {problems}\033[0m")
        return 1
    print("\n  \033[0;32mPASS: stale route computed but never applied; "
          "one destination, one navigation start.\033[0m")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
