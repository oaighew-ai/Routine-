from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from cfb_edge.week5_capture_health import build
from cfb_edge.week5_freeze import FreezeViolation, assert_frozen

ROOT = Path(__file__).resolve().parents[1]


class Week5FreezeTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "config/week5_freeze.json").read_text())
        self.s04 = json.loads((ROOT / "config/s04_es2.json").read_text())
        self.mq = json.loads((ROOT / "config/s03_m1.json").read_text())

    def test_checked_in_configs_match_the_frozen_manifest(self):
        assert_frozen(
            s04_config=self.s04,
            market_quality_config=self.mq,
            manifest=self.manifest,
        )

    def test_threshold_drift_fails_closed(self):
        changed = dict(self.s04)
        changed["minimumConservativeExecutableEv"] = 0.004
        with self.assertRaises(FreezeViolation):
            assert_frozen(
                s04_config=changed,
                market_quality_config=self.mq,
                manifest=self.manifest,
            )

    def test_open_tolerance_drift_fails_closed(self):
        changed = dict(self.s04)
        changed["maximumOpenCaptureLagSeconds"] = 1200
        with self.assertRaises(FreezeViolation):
            assert_frozen(
                s04_config=changed,
                market_quality_config=self.mq,
                manifest=self.manifest,
            )


class Week5CaptureHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.slate = root / "slate.csv"
        self.opens = root / "opens.csv"
        with self.slate.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["game", "kickoff"])
            w.writerow(["A @ B", "2026-10-03T16:00:00Z"])
            w.writerow(["C @ D", "2026-10-03T20:00:00Z"])

    def write_opens(self, rows):
        with self.opens.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow([
                "game", "opening_line", "source", "first_seen",
                "venue_open_time", "open_lag_seconds",
            ])
            w.writerows(rows)

    def test_capture_health_counts_all_slate_games_not_only_signals(self):
        self.write_opens([
            ["A @ B", -3.5, "true_open", "2026-09-27T22:05:00Z", "2026-09-27T22:00:00Z", "300"],
            ["C @ D", 4.5, "first_seen", "2026-09-27T23:00:00Z", "2026-09-27T22:00:00Z", "3600"],
        ])
        r = build(self.slate, self.opens)
        self.assertEqual(r["summary"]["slateRows"], 2)
        self.assertEqual(r["summary"]["trueOpenRows"], 1)
        self.assertEqual(r["summary"]["trueOpenCoveragePct"], 0.5)
        self.assertFalse(r["policy"]["modelSignalsConsulted"])

    def test_mislabeled_true_open_is_an_integrity_failure(self):
        self.write_opens([
            ["A @ B", -3.5, "true_open", "2026-09-27T22:20:00Z", "2026-09-27T22:00:00Z", "1200"],
        ])
        r = build(self.slate, self.opens)
        self.assertEqual(r["status"], "INTEGRITY_FAILURE")
        self.assertEqual(r["summary"]["invalidTrueOpenRows"], 1)


if __name__ == "__main__":
    unittest.main()
