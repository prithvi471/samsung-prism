"""Tests for identifier normalisation in the tool layer.

agent.tools imports livekit, which is not installed everywhere the pure-python
revision tests run, so these skip rather than fail when it is absent.

    python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from agent.tools import ID_ARGS, _normalize_identifier
    HAVE_TOOLS = True
except Exception:  # noqa: BLE001  (livekit absent)
    HAVE_TOOLS = False


@unittest.skipUnless(HAVE_TOOLS, "agent.tools requires livekit")
class TestIdentifierNormalization(unittest.TestCase):
    def test_strips_dictation_separators(self):
        """Observed live: speakers spell IDs out, so STT emits separators that
        are artifacts of dictation rather than part of the value."""
        for raw, want in [
            ("P-O-9-9-9", "PO999"),
            ("D-L-5-5-5", "DL555"),
            ("B.O.B. 1-2", "BOB12"),
            ("V-4-4", "V44"),
            ("E77-2211", "E772211"),
            ("P 5 2", "P52"),
        ]:
            self.assertEqual(_normalize_identifier(raw), want, raw)

    def test_already_clean_values_unchanged(self):
        for v in ["PO999", "DL555", "BOB12"]:
            self.assertEqual(_normalize_identifier(v), v)

    def test_prose_keeps_its_separators(self):
        """A value that is really prose must survive untouched -- stripping
        would corrupt it. 'out for delivery' collapsing to 'OUTFORDELIVERY'
        is the bug this test caught in the first implementation.
        """
        for v in ["out for delivery", "my office building", "5th Street west",
                  "the grocery store", "coffee shop on 5th", "Riley Kim",
                  "Apartment APT1, Dallas", "123abc"]:
            self.assertEqual(_normalize_identifier(v), v, v)

    def test_non_strings_pass_through(self):
        for v in [None, 3500, True, 12.5]:
            self.assertEqual(_normalize_identifier(v), v)

    def test_only_identifier_fields_are_targeted(self):
        """passenger_name must NOT be in scope: 'Riley Kim' needs its space."""
        self.assertIn("order_id", ID_ARGS)
        self.assertIn("doc_number", ID_ARGS)
        self.assertIn("product_id", ID_ARGS)
        self.assertNotIn("passenger_name", ID_ARGS)
        self.assertNotIn("city", ID_ARGS)
        self.assertNotIn("origin_address", ID_ARGS)
        self.assertNotIn("query", ID_ARGS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
