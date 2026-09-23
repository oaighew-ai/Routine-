from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cfb_edge.cohort import (
    build_slate,
    load,
    provider_week,
    status,
    validate_slate_rows,
)
from cfb_edge.slate import SlateRow


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "week5_cohort.json"


class CohortIdentityTests(unittest.TestCase):
    def test_product_week5_maps_to_provider_week4(self):
        c = load(CONFIG)
        self.assertEqual(c["productWeek"], 5)
        self.assertEqual(provider_week(c, "cfbfastR"), 4)
        self.assertEqual(provider_week(c, "collegefootballdata"), 4)

    def test_sep25_26_is_valid_and_october_provider_week5_is_rejected(self):
        c = load(CONFIG)
        good = validate_slate_rows(
            [
                {"game": "A @ H", "kickoff": "2026-09-25T20:00:00Z"},
                {"game": "B @ J", "kickoff": "2026-09-26T23:30:00Z"},
                {"game": "C @ K", "kickoff": "2026-09-27T03:30:00Z"},
            ],
            c,
        )
        self.assertTrue(good["valid"], good["errors"])

        bad = validate_slate_rows(
            [{"game": "A @ H", "kickoff": "2026-10-03T19:30:00Z"}],
            c,
        )
        self.assertFalse(bad["valid"])
        self.assertTrue(any("OUTSIDE_GAME_WINDOW" in x for x in bad["errors"]))

    def test_capture_and_prospective_windows_are_explicit(self):
        c = load(CONFIG)
        t = datetime(2026, 9, 23, 1, tzinfo=timezone.utc)
        self.assertTrue(status(c, now=t, mode="capture")["run"])
        self.assertTrue(status(c, now=t, mode="prospective")["run"])
        after_first_kick = datetime(2026, 9, 25, 21, tzinfo=timezone.utc)
        self.assertFalse(status(c, now=after_first_kick, mode="prospective")["run"])
        self.assertTrue(status(c, now=after_first_kick, mode="game")["run"])

    def test_build_uses_provider_week4_and_filters_to_canonical_window(self):
        c = load(CONFIG)
        seen = []
        def builder(season, week):
            seen.append((season, week))
            return [
                SlateRow("A @ H", 1.0, False, "2026-09-26", "2026-09-26T16:00:00Z"),
                SlateRow("Wrong @ Week", 1.0, False, "2026-10-03", "2026-10-03T16:00:00Z"),
            ]
        rows = build_slate(c, builder=builder)
        self.assertEqual(seen, [(2026, 4)])
        self.assertEqual([r.game for r in rows], ["A @ H"])


if __name__ == "__main__":
    unittest.main()
