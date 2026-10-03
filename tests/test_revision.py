"""Tests for revision-aware tool execution.

Stdlib only (unittest + asyncio) so they run anywhere Python 3.10+ does —
no livekit, no API keys, no GPU:

    python -m unittest discover -s tests -v
"""

import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.revision import (  # noqa: E402
    RevisionAwareExecutor,
    ToolKind,
    classify,
    detect_retraction,
    slot_of,
)


def fake_registry(delay: float = 0.0):
    """A stand-in for MockAPIRegistry.call with an optional blocking delay."""
    calls = []

    def run_tool(name, **kwargs):
        calls.append((name, kwargs))
        if delay:
            time.sleep(delay)
        return {"status": "success", "tool": name, "args": kwargs}

    run_tool.calls = calls
    return run_tool


class Harness:
    """Collects committed entries and emitted events."""

    def __init__(self, delay: float = 0.0):
        self.commits = []
        self.events = []
        self.run_tool = fake_registry(delay)
        self.ex = RevisionAwareExecutor(
            run_tool=self.run_tool,
            commit=self.commits.append,
            events=lambda name, data: self.events.append((name, data)),
        )

    @property
    def committed(self):
        return [(r.tool, r.args) for r in self.commits]

    def event_names(self):
        return [n for n, _ in self.events]


class TestNormalExecution(unittest.IsolatedAsyncioTestCase):
    async def test_two_independent_calls_both_commit_in_order(self):
        h = Harness()
        h.ex.new_intent("update both documents")
        await h.ex.run("update_identity_doc",
                       {"doc_type": "driver_license", "doc_number": "DL555"})
        await h.ex.run("update_identity_doc",
                       {"doc_type": "passport", "doc_number": "P999"})
        h.ex.flush()

        self.assertEqual(h.committed, [
            ("update_identity_doc", {"doc_type": "driver_license", "doc_number": "DL555"}),
            ("update_identity_doc", {"doc_type": "passport", "doc_number": "P999"}),
        ])
        self.assertIn("TASK_COMMITTED", h.event_names())

    async def test_legitimate_repeats_survive_across_tools(self):
        """finance_13 and ecommerce_22 shapes: same function, different target."""
        h = Harness()
        h.ex.new_intent("two autopay changes and two orders")
        await h.ex.run("modify_autopay", {"bill_type": "credit_card", "source_account": "savings"})
        await h.ex.run("modify_autopay", {"bill_type": "mortgage", "source_account": "checking"})
        await h.ex.run("track_order", {"order_id": "A1"})
        await h.ex.run("track_order", {"order_id": "B2"})
        h.ex.flush()
        self.assertEqual(len(h.committed), 4)


class TestSingleCorrection(unittest.IsolatedAsyncioTestCase):
    async def test_slot_supersession_keeps_only_the_last_value(self):
        """housing_25: max price 3000, then 'wait, actually 3500'."""
        h = Harness()
        h.ex.new_intent("set filters and search")
        await h.ex.run("update_search_filter", {"filter_name": "pets_allowed", "value": True})
        await h.ex.run("update_search_filter", {"filter_name": "max_price", "value": 3000})
        await h.ex.run("update_search_filter", {"filter_name": "max_price", "value": 3500})
        h.ex.flush()

        self.assertEqual(h.committed, [
            ("update_search_filter", {"filter_name": "pets_allowed", "value": True}),
            ("update_search_filter", {"filter_name": "max_price", "value": 3500}),
        ])
        self.assertIn("TASK_STALE", h.event_names())

    async def test_emission_order_matches_mention_order(self):
        """The evaluator pairs same-named calls positionally."""
        h = Harness()
        h.ex.new_intent("filters")
        await h.ex.run("update_search_filter", {"filter_name": "pets_allowed", "value": True})
        await h.ex.run("update_search_filter", {"filter_name": "max_price", "value": 3500})
        h.ex.flush()
        self.assertEqual([a["filter_name"] for _, a in h.committed],
                         ["pets_allowed", "max_price"])

    async def test_correction_does_not_discard_untouched_slots(self):
        """Regression: live housing_25 failed because an intent bump invalidated
        every earlier record, including the pets filter the user never retracted.

        Staleness must be slot-scoped, not turn-scoped.
        """
        h = Harness()
        h.ex.new_intent("set the filters")
        await h.ex.run("update_search_filter", {"filter_name": "pets_allowed", "value": True})
        await h.ex.run("update_search_filter", {"filter_name": "max_price", "value": 3000})
        h.ex.revise(reason="wait, actually change the max price to 3500")
        await h.ex.run("update_search_filter", {"filter_name": "max_price", "value": 3500})
        h.ex.flush()

        self.assertEqual(h.committed, [
            ("update_search_filter", {"filter_name": "pets_allowed", "value": True}),
            ("update_search_filter", {"filter_name": "max_price", "value": 3500}),
        ])

    async def test_completed_work_is_not_reported_as_never_started(self):
        """Regression: `ran` was indistinguishable from `planned`, so revise()
        cancelled finished executions claiming they never started."""
        h = Harness()
        h.ex.new_intent("two autopay changes")
        await h.ex.run("modify_autopay", {"bill_type": "credit_card", "source_account": "savings"})
        h.ex.revise(reason="unrelated correction")
        await h.ex.run("modify_autopay", {"bill_type": "mortgage", "source_account": "checking"})
        h.ex.flush()

        bogus = [d for n, d in h.events
                 if n == "TASK_CANCELLED" and "before start" in d.get("reason", "")]
        self.assertEqual(bogus, [], "completed work was cancelled as 'before start'")
        self.assertEqual(len(h.committed), 2)

    async def test_singleton_tool_supersedes_on_destination_change(self):
        """search_flights to Oslo, corrected to Bergen — one committed call."""
        h = Harness()
        h.ex.new_intent("find a flight")
        await h.ex.run("search_flights", {"destination": "Oslo", "date": "May 3"})
        await h.ex.run("search_flights", {"destination": "Bergen", "date": "May 3"})
        h.ex.flush()
        self.assertEqual(h.committed,
                         [("search_flights", {"destination": "Bergen", "date": "May 3"})])


class TestCorrectionDuringExecution(unittest.IsolatedAsyncioTestCase):
    async def test_stale_state_change_finishes_but_never_commits(self):
        """The invariant: computation may finish, the side effect must not land."""
        h = Harness(delay=0.20)
        h.ex.new_intent("set autopay")

        async def revise_midflight():
            await asyncio.sleep(0.05)
            h.ex.revise(reason="user corrected the account")

        task = asyncio.create_task(
            h.ex.run("modify_autopay", {"bill_type": "mortgage", "source_account": "savings"}))
        await asyncio.gather(task, revise_midflight())
        h.ex.flush()

        # The tool really did run...
        self.assertEqual(len(h.run_tool.calls), 1)
        # ...but nothing was committed.
        self.assertEqual(h.committed, [])
        stale = [d for n, d in h.events if n == "TASK_STALE"]
        self.assertTrue(any(d.get("phase") == "after_execution" for d in stale))

    async def test_correction_before_start_skips_state_change(self):
        h = Harness()
        v = h.ex.new_intent("add to cart")
        h.ex.revise(reason="changed mind")
        await h.ex.run("add_to_cart", {"product_id": "K9", "quantity": 2}, version=v)
        h.ex.flush()
        self.assertEqual(h.run_tool.calls, [])
        self.assertEqual(h.committed, [])

    async def test_readonly_stale_result_returned_but_not_committed(self):
        h = Harness(delay=0.20)
        v = h.ex.new_intent("search")

        async def revise_midflight():
            await asyncio.sleep(0.05)
            h.ex.revise(reason="different city")

        out, _ = await asyncio.gather(
            h.ex.run("search_apartments",
                     {"city": "Portland", "bedrooms": 1, "max_price": 1800}, version=v),
            revise_midflight())
        h.ex.flush()

        self.assertIn("success", out)          # caller still gets the data
        self.assertEqual(h.committed, [])      # but it is not scored


class TestDuplicates(unittest.IsolatedAsyncioTestCase):
    async def test_identical_call_executes_once_and_commits_once(self):
        h = Harness()
        h.ex.new_intent("track it")
        await h.ex.run("track_order", {"order_id": "PO999"})
        await h.ex.run("track_order", {"order_id": "PO999"})
        h.ex.flush()

        self.assertEqual(len(h.run_tool.calls), 1)
        self.assertEqual(len(h.committed), 1)
        self.assertIn("DUPLICATE_SUPPRESSED", h.event_names())

    async def test_concurrent_identical_calls_coalesce(self):
        h = Harness(delay=0.10)
        h.ex.new_intent("track it")
        await asyncio.gather(
            h.ex.run("track_order", {"order_id": "PO999"}),
            h.ex.run("track_order", {"order_id": "PO999"}),
        )
        h.ex.flush()
        self.assertEqual(len(h.run_tool.calls), 1)
        self.assertEqual(len(h.committed), 1)

    async def test_argument_normalisation_catches_near_duplicates(self):
        h = Harness()
        h.ex.new_intent("doc")
        await h.ex.run("update_identity_doc", {"doc_type": "driver_license", "doc_number": "DL1"})
        await h.ex.run("update_identity_doc", {"doc_type": "driver license", "doc_number": "DL1"})
        h.ex.flush()
        self.assertEqual(len(h.run_tool.calls), 1)


class TestRapidCorrections(unittest.IsolatedAsyncioTestCase):
    async def test_two_rapid_corrections_keep_only_the_final_value(self):
        h = Harness()
        h.ex.new_intent("set the budget")
        await h.ex.run("update_search_filter", {"filter_name": "max_price", "value": 3000})
        await h.ex.run("update_search_filter", {"filter_name": "max_price", "value": 3500})
        await h.ex.run("update_search_filter", {"filter_name": "max_price", "value": 4000})
        h.ex.flush()
        self.assertEqual(h.committed,
                         [("update_search_filter", {"filter_name": "max_price", "value": 4000})])

    async def test_versioned_corrections_across_turns(self):
        h = Harness()
        v1 = h.ex.new_intent("book to Oslo")
        await h.ex.run("search_flights", {"destination": "Oslo", "date": "May 3"}, version=v1)
        v2 = h.ex.revise(reason="correction 1")
        await h.ex.run("search_flights", {"destination": "Bergen", "date": "May 3"}, version=v2)
        v3 = h.ex.revise(reason="correction 2")
        await h.ex.run("search_flights", {"destination": "Tromso", "date": "May 3"}, version=v3)
        h.ex.flush()
        self.assertEqual(h.committed,
                         [("search_flights", {"destination": "Tromso", "date": "May 3"})])


class TestPurePieces(unittest.TestCase):
    def test_retraction_cues(self):
        for text in ["under fifty, no wait, under forty",
                     "go to Chennai Central — wait, actually go to the airport",
                     "set it to 3000, scratch that, 3500",
                     "book the 9am, I mean the 10am"]:
            self.assertTrue(detect_retraction(text), text)
        for text in ["search for flights to Tokyo on July 15",
                     "um... so... I need a one bedroom in Portland"]:
            self.assertFalse(detect_retraction(text), text)

    def test_observe_transcript_bumps_only_on_retraction(self):
        ex = RevisionAwareExecutor(run_tool=fake_registry())
        v = ex.new_intent("hello")
        self.assertEqual(ex.observe_transcript("find me a flight to Tokyo"), v)
        self.assertEqual(ex.observe_transcript("no wait, Osaka"), v + 1)

    def test_classification(self):
        self.assertIs(classify("add_to_cart"), ToolKind.STATE_CHANGING)
        self.assertIs(classify("search_products"), ToolKind.READ_ONLY)

    def test_slots_distinguish_targets_not_values(self):
        a = slot_of("update_search_filter", {"filter_name": "max_price", "value": 3000})
        b = slot_of("update_search_filter", {"filter_name": "max_price", "value": 3500})
        c = slot_of("update_search_filter", {"filter_name": "pets_allowed", "value": True})
        self.assertEqual(a, b)      # a correction targets the same slot
        self.assertNotEqual(a, c)   # a different filter is a different slot


if __name__ == "__main__":
    unittest.main(verbosity=2)
