from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cfb_edge.engine import config as engine_config
from cfb_edge.s04_es2 import evaluate, load_candidates, run_live

ROOT = Path(__file__).resolve().parents[1]


def cfg():
    return json.loads((ROOT / "config/s04_es2.json").read_text())


def quality_cfg():
    return json.loads((ROOT / "config/s03_m1.json").read_text())


def learning():
    rows = []
    for week in (1, 2, 3):
        for i in range(5):
            rows.append({
                "week": week,
                "projection_gap_vs_open": 5.0 if i % 2 == 0 else -5.0,
                "directional_clv": 1.0,
            })
    return {"rows": rows}


class S04ES2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.slate = root / "slate.csv"
        self.opens = root / "opens.csv"
        self.edge = engine_config.load(ROOT / "config/edge_os.json")

    def write(self, *, source="true_open", lag="600", projected="8", opening="-3"):
        with self.slate.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["game", "kickoff", "projected_margin"])
            w.writerow(["Away @ Home", "2026-10-03T19:30:00Z", projected])
        with self.opens.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow([
                "game", "opening_line", "source", "first_seen",
                "venue_open_time", "open_lag_seconds",
            ])
            w.writerow([
                "Away @ Home", opening, source,
                "2026-09-27T22:10:00Z", "2026-09-27T22:00:00Z", lag,
            ])

    def test_only_true_open_inside_frozen_lag_enters_week5_cohort(self):
        self.write()
        candidates, audit = load_candidates(
            slate_path=self.slate, opens_path=self.opens, cfg=cfg()
        )
        self.assertIn(("Away @ Home", "Home"), candidates)
        self.assertTrue(audit[0]["auditGrade"])
        self.assertEqual(candidates[("Away @ Home", "Home")]["openLagSeconds"], 600)

    def test_first_seen_is_not_a_week5_candidate(self):
        self.write(source="first_seen")
        candidates, audit = load_candidates(
            slate_path=self.slate, opens_path=self.opens, cfg=cfg()
        )
        self.assertEqual(candidates, {})
        self.assertIn("OPEN_NOT_AUDIT_GRADE_TRUE_OPEN", audit[0]["exclusions"])

    def test_source_label_cannot_bypass_lag_tolerance(self):
        self.write(source="true_open", lag="901")
        candidates, audit = load_candidates(
            slate_path=self.slate, opens_path=self.opens, cfg=cfg()
        )
        self.assertEqual(candidates, {})
        self.assertIn("OPEN_LAG_OUTSIDE_FROZEN_TOLERANCE", audit[0]["exclusions"])

    def row(self):
        return SimpleNamespace(
            market="spreads",
            venue="draftkings",
            away="Away",
            home="Home",
            side="Home",
            reference="pinnacle",
            consensus_line=-3.5,
            venue_line=-3.5,
            venue_price=-105.0,
        )

    def candidate_inputs(self):
        self.write()
        return load_candidates(
            slate_path=self.slate, opens_path=self.opens, cfg=cfg()
        )

    @patch("cfb_edge.s04_es2.quality_for_row")
    def test_market_quality_and_conservative_ev_are_binding(self, quality):
        candidates, audit = self.candidate_inputs()
        quality.return_value = {
            "status": "PASS",
            "conservativeExecutableEv": 0.006,
        }
        report = evaluate(
            candidates=candidates,
            audit_rows=audit,
            shop_rows=[self.row()],
            learning=learning(),
            cfg=cfg(),
            quality_cfg=quality_cfg(),
            edge_cfg=self.edge,
            pull={"fetched_at": "2026-09-28T12:00:00Z", "events": []},
        )
        self.assertEqual(report["qualifiedCount"], 1)
        self.assertEqual(report["topFive"][0]["actualStakeUnits"], 0)
        self.assertEqual(report["deliveryEffect"], "NONE")

        quality.return_value = {
            "status": "PASS",
            "conservativeExecutableEv": 0.004,
        }
        report = evaluate(
            candidates=candidates,
            audit_rows=audit,
            shop_rows=[self.row()],
            learning=learning(),
            cfg=cfg(),
            quality_cfg=quality_cfg(),
            edge_cfg=self.edge,
            pull={"fetched_at": "2026-09-28T12:00:00Z", "events": []},
        )
        self.assertEqual(report["qualifiedCount"], 0)
        self.assertIn(
            "CONSERVATIVE_EXECUTABLE_EV_BELOW_FLOOR",
            report["inspectedLiveRows"][0]["exclusions"],
        )

        quality.return_value = {
            "status": "BLOCKED",
            "conservativeExecutableEv": 0.02,
        }
        report = evaluate(
            candidates=candidates,
            audit_rows=audit,
            shop_rows=[self.row()],
            learning=learning(),
            cfg=cfg(),
            quality_cfg=quality_cfg(),
            edge_cfg=self.edge,
            pull={"fetched_at": "2026-09-28T12:00:00Z", "events": []},
        )
        self.assertEqual(report["qualifiedCount"], 0)
        self.assertIn(
            "MARKET_QUALITY_BLOCKED",
            report["inspectedLiveRows"][0]["exclusions"],
        )

    @patch("cfb_edge.s04_es2.fetch_pull")
    def test_no_audit_grade_signal_spends_no_odds_api_credit(self, fetch):
        self.write(source="first_seen")
        report, pull = run_live(
            learning=learning(),
            slate_path=self.slate,
            opens_path=self.opens,
            cfg=cfg(),
            quality_cfg=quality_cfg(),
            edge_cfg=self.edge,
        )
        self.assertIsNone(pull)
        self.assertEqual(report["qualifiedCount"], 0)
        self.assertEqual(report["actualStakeUnits"], 0)
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
