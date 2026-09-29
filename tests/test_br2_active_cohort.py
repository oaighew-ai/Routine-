from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from cfb_edge.cohort import load, provider_week, status, validate_slate_rows


ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/"config"/"br2_active_cohort.json"


class Br2ActiveCohortTests(unittest.TestCase):
    def test_product_week6_maps_to_provider_week5(self):
        c=load(CONFIG)
        self.assertEqual(c["productWeek"],6)
        self.assertEqual(provider_week(c,"cfbfastR"),5)
        self.assertEqual(provider_week(c,"collegefootballdata"),5)

    def test_sep29_is_prospective_and_oct2_games_are_in_window(self):
        c=load(CONFIG)
        self.assertTrue(status(c,now=datetime(2026,9,29,1,tzinfo=timezone.utc),mode="prospective")["run"])
        audit=validate_slate_rows([
            {"game":"Pittsburgh @ Virginia Tech","kickoff":"2026-10-02T23:00:00Z"},
            {"game":"Penn State @ Northwestern","kickoff":"2026-10-03T00:00:00Z"},
        ],c)
        self.assertTrue(audit["valid"],audit["errors"])


if __name__=="__main__":
    unittest.main()
