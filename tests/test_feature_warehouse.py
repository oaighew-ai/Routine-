from __future__ import annotations

import unittest
from datetime import datetime, timezone

from cfb_edge.feature_warehouse import build_snapshot


class FeatureWarehouseTests(unittest.TestCase):
    def test_missing_features_stay_missing_and_ppa_is_not_epa(self):
        slate = [{"game": "A @ H", "kickoff": "2026-09-26T16:00:00Z"}]
        plays = [
            {"gameId": 1, "offense": "H", "ppa": 0.4, "down": 1, "distance": 10, "yardsGained": 6},
            {"gameId": 2, "offense": "A", "ppa": 0.1, "down": 1, "distance": 10, "yardsGained": 4},
        ]
        r = build_snapshot(
            slate=slate, plays=plays, prior_games=[],
            as_of=datetime(2026, 9, 23, tzinfo=timezone.utc),
        )
        f = r["rows"][0]["features"]
        self.assertAlmostEqual(f["ppaDiff"], 0.3)
        self.assertIsNone(f["epaDiff"])
        self.assertIsNone(f["qbContinuityDiff"])
        self.assertFalse(r["summary"]["modelingEligible"])

    def test_future_games_do_not_create_rest_features(self):
        slate = [{"game": "A @ H", "kickoff": "2026-09-26T16:00:00Z"}]
        games = [
            {"homeTeam": "H", "awayTeam": "X", "startDate": "2026-09-24T16:00:00Z"},
            {"homeTeam": "A", "awayTeam": "Y", "startDate": "2026-09-20T16:00:00Z"},
        ]
        r = build_snapshot(
            slate=slate, plays=[], prior_games=games,
            as_of=datetime(2026, 9, 23, tzinfo=timezone.utc),
        )
        self.assertIsNone(r["rows"][0]["features"]["restDaysDiff"])


if __name__ == "__main__":
    unittest.main()
