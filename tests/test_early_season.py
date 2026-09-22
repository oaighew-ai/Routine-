from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from cfb_edge.early_season import (
    CONTRACT,
    LearningRow,
    build_shadow_board,
    summarize,
)


def row(week: int, gap: float, *, actual_residual: float, move: float) -> LearningRow:
    open_margin = 3.0
    projected = open_margin + gap
    open_line = -open_margin
    close_margin = open_margin + move
    close_line = -close_margin
    sign = 1 if gap > 0 else -1 if gap < 0 else 0
    return LearningRow(
        season=2026,
        week=week,
        cohort="TRAIN" if week in (1, 2) else "PSEUDO_HOLDOUT",
        game=f"A{week}-{gap} @ H{week}-{gap}",
        kickoff=f"2026-09-{week+1:02d}T19:00:00Z",
        projected_home_margin=projected,
        open_home_line=open_line,
        close_home_line=close_line,
        open_market_margin=open_margin,
        close_market_margin=close_margin,
        realized_home_margin=open_margin + actual_residual,
        projection_gap_vs_open=gap,
        open_to_close_home_clv=open_line - close_line,
        directional_clv=sign * (open_line - close_line),
        providers=3,
    )


class EarlySeasonLearningTests(unittest.TestCase):
    def test_week3_never_fits_the_coefficients(self):
        train = [
            row(1, 4.0, actual_residual=1.0, move=0.5),
            row(2, -4.0, actual_residual=-1.0, move=-0.5),
        ] * 20
        holdout = [
            row(3, 4.0, actual_residual=100.0, move=100.0),
        ] * 25
        base = summarize(train + holdout)

        changed = train + [
            row(3, 4.0, actual_residual=-100.0, move=-100.0),
        ] * 25
        second = summarize(changed)

        self.assertEqual(
            base["rawOutcomeResidualSlope"], second["rawOutcomeResidualSlope"]
        )
        self.assertEqual(base["rawMovementSlope"], second["rawMovementSlope"])

    def test_a_good_pseudo_holdout_can_only_qualify_shadow(self):
        train = []
        for week in (1, 2):
            for i in range(20):
                gap = 4.0 if i % 2 == 0 else -4.0
                train.append(
                    row(
                        week,
                        gap,
                        actual_residual=0.20 * gap,
                        move=0.10 * gap,
                    )
                )
        holdout = []
        for i in range(24):
            gap = 5.0 if i % 2 == 0 else -5.0
            holdout.append(
                row(
                    3,
                    gap,
                    actual_residual=0.20 * gap,
                    move=0.10 * gap,
                )
            )

        s = summarize(train + holdout)
        self.assertTrue(s["shadowQualified"])
        self.assertGreater(s["holdoutMaeImprovementVsOpen"], 0)
        self.assertGreater(s["holdoutMeanDirectionalClv"], 0)
        self.assertLessEqual(s["week4OutcomeWeight"], 0.25)
        self.assertLessEqual(s["week4MovementWeight"], 0.25)

    def test_bad_week3_blocks_shadow_even_after_good_training(self):
        train = []
        for week in (1, 2):
            for i in range(20):
                gap = 4.0 if i % 2 == 0 else -4.0
                train.append(
                    row(
                        week,
                        gap,
                        actual_residual=0.20 * gap,
                        move=0.10 * gap,
                    )
                )
        holdout = [
            row(
                3,
                5.0 if i % 2 == 0 else -5.0,
                actual_residual=-(5.0 if i % 2 == 0 else -5.0),
                move=-(1.0 if i % 2 == 0 else -1.0),
            )
            for i in range(24)
        ]
        s = summarize(train + holdout)
        self.assertFalse(s["shadowQualified"])
        self.assertTrue(s["failedShadowChecks"])

    def test_week4_board_uses_only_timely_captured_opens(self):
        report = {
            "contract": CONTRACT,
            "season": 2026,
            "revision": "abc",
            "summary": {
                "week4OutcomeWeight": 0.20,
                "week4MovementWeight": 0.10,
                "shadowQualified": True,
                "failedShadowChecks": [],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            slate = Path(tmp) / "slate.csv"
            opens = Path(tmp) / "opens.csv"
            with slate.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow([
                    "game", "projected_margin", "side", "posted_line",
                    "total", "kickoff",
                ])
                w.writerow(["Away @ Home", 10.0, "", "", 52, "2026-09-26T19:30:00Z"])
                w.writerow(["Other @ Team", 10.0, "", "", 52, "2026-09-26T20:00:00Z"])
            with opens.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["game", "opening_line", "source", "first_seen"])
                w.writerow(["Away @ Home", -3.0, "true_open", "2026-09-20T23:00:00Z"])
                w.writerow(["Other @ Team", -3.0, "late", "2026-09-25T23:00:00Z"])

            board = build_shadow_board(
                report=report, slate_path=slate, opens_path=opens
            )
            self.assertEqual(board["deliveryEffect"], "NONE")
            self.assertEqual(board["actualStakeUnits"], 0)
            self.assertEqual(len(board["topFive"]), 1)
            self.assertEqual(board["topFive"][0]["game"], "Away @ Home")
            late = next(r for r in board["rows"] if r["game"] == "Other @ Team")
            self.assertIn("OPEN_NOT_AUDIT_GRADE_TRUE_OPEN", late["exclusions"])

    def test_unqualified_learning_report_produces_no_watchlist(self):
        report = {
            "contract": CONTRACT,
            "season": 2026,
            "revision": "abc",
            "summary": {
                "week4OutcomeWeight": 0.20,
                "week4MovementWeight": 0.10,
                "shadowQualified": False,
                "failedShadowChecks": ["NO_MARGIN_MAE_IMPROVEMENT"],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            slate = Path(tmp) / "slate.csv"
            opens = Path(tmp) / "opens.csv"
            slate.write_text(
                "game,projected_margin,side,posted_line,total,kickoff\n"
                "Away @ Home,10,,,52,2026-09-26T19:30:00Z\n",
                encoding="utf-8",
            )
            opens.write_text(
                "game,opening_line,source,first_seen\n"
                "Away @ Home,-3,true_open,2026-09-20T23:00:00Z\n",
                encoding="utf-8",
            )
            board = build_shadow_board(
                report=report, slate_path=slate, opens_path=opens
            )
            self.assertEqual(board["topFive"], [])
            self.assertIn(
                "EARLY_SEASON_HOLDOUT_NOT_QUALIFIED",
                board["rows"][0]["exclusions"],
            )


if __name__ == "__main__":
    unittest.main()
