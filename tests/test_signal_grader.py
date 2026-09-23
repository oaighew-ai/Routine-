from __future__ import annotations

import unittest
from datetime import datetime, timezone

from cfb_edge.signal_grader import grade


class SignalGraderTests(unittest.TestCase):
    def test_only_true_open_audit_rows_are_graded(self):
        signals = {
            "candidateAuditRows": [
                {
                    "game": "A @ H",
                    "side": "H",
                    "kickoff": "2026-09-26T16:00:00Z",
                    "openingHomeLine": -3.0,
                    "source": "true_open",
                    "firstSeen": "2026-09-20T20:00:00Z",
                    "venueOpenTime": "2026-09-20T19:58:00Z",
                    "openLagSeconds": 120,
                    "auditGrade": True,
                },
                {
                    "game": "B @ J",
                    "side": "B",
                    "kickoff": "2026-09-26T16:00:00Z",
                    "openingHomeLine": 2.0,
                    "source": "first_seen",
                    "auditGrade": False,
                },
            ]
        }
        capture = {
            "games": [{
                "game": "A @ H",
                "observations": [
                    {"observedAt": "2026-09-26T15:50:00Z", "derivedHomeLine": -4.0}
                ],
            }]
        }
        report = grade(
            signals,
            capture,
            now=datetime(2026, 9, 26, 17, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(report["summary"]["signalRows"], 1)
        self.assertEqual(report["summary"]["gradeableRows"], 1)
        self.assertAlmostEqual(report["rows"][0]["lineClvPoints"], 1.0)

    def test_stale_close_is_not_gradeable(self):
        signals = {"candidateAuditRows": [{
            "game": "A @ H", "side": "H", "kickoff": "2026-09-26T16:00:00Z",
            "openingHomeLine": -3.0, "source": "true_open", "auditGrade": True,
        }]}
        capture = {"games": [{"game": "A @ H", "observations": [
            {"observedAt": "2026-09-26T14:00:00Z", "derivedHomeLine": -4.0}
        ]}]}
        report = grade(
            signals, capture,
            now=datetime(2026, 9, 26, 17, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(report["rows"][0]["gradeable"])
        self.assertIn("CLOSE_NOT_FRESH_ENOUGH", report["rows"][0]["exclusions"])


if __name__ == "__main__":
    unittest.main()
