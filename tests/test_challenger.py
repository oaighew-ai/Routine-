from __future__ import annotations

import unittest

from cfb_edge.challenger import Observation, walk_forward


def rows():
    out = []
    for season in (2023, 2024, 2025):
        for i in range(30):
            x = (i - 14.5) / 10
            market = float((i % 7) - 3)
            actual = market + 2.0 * x
            out.append(Observation(
                season=season,
                week=(i % 12) + 1,
                game_id=f"{season}-{i}",
                market_home_margin=market,
                actual_home_margin=actual,
                features=(x,),
            ))
    return out


class S04ChallengerTests(unittest.TestCase):
    def test_walk_forward_can_learn_only_residual_information(self):
        report = walk_forward(
            rows(), alpha=0.01, minimum_train_rows=20,
            minimum_test_rows_per_season=20,
        )
        self.assertEqual(report["status"], "SHADOW_ONLY")
        self.assertEqual(report["promotionEffect"], "NONE")
        self.assertGreater(report["n"], 0)
        self.assertGreater(report["maeImprovement"], 0)
        self.assertLess(report["challengerMae"], report["marketMae"])

    def test_future_labels_cannot_change_an_earlier_season_prediction(self):
        original = rows()
        first = walk_forward(
            original, alpha=0.01, minimum_train_rows=20,
            minimum_test_rows_per_season=20,
        )
        changed = [
            Observation(
                season=r.season, week=r.week, game_id=r.game_id,
                market_home_margin=r.market_home_margin,
                actual_home_margin=(
                    r.actual_home_margin + 1000 if r.season == 2025
                    else r.actual_home_margin
                ),
                features=r.features,
            )
            for r in original
        ]
        second = walk_forward(
            changed, alpha=0.01, minimum_train_rows=20,
            minimum_test_rows_per_season=20,
        )
        a = {
            p["gameId"]: p["challengerHomeMargin"]
            for p in first["predictions"] if p["season"] == 2024
        }
        b = {
            p["gameId"]: p["challengerHomeMargin"]
            for p in second["predictions"] if p["season"] == 2024
        }
        self.assertEqual(a, b)

    def test_every_prediction_records_training_cutoff_before_test_season(self):
        report = walk_forward(
            rows(), alpha=0.01, minimum_train_rows=20,
            minimum_test_rows_per_season=20,
        )
        for p in report["predictions"]:
            self.assertLess(p["trainedThroughSeason"], p["season"])

    def test_empty_eligible_cohort_uses_null_metrics_not_nan(self):
        report = walk_forward(
            rows()[:10], alpha=0.01, minimum_train_rows=100,
            minimum_test_rows_per_season=20,
        )
        self.assertEqual(report["n"], 0)
        self.assertIsNone(report["marketMae"])
        self.assertIsNone(report["challengerMae"])
        self.assertIsNone(report["maeImprovement"])


if __name__ == "__main__":
    unittest.main()
