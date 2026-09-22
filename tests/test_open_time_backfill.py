import unittest

from cfb_edge.open_time_backfill import historical_open_proof


class HistoricalOpenProofTests(unittest.TestCase):
    def test_latest_event_rung_is_the_conservative_bound(self):
        proof = historical_open_proof(
            "2026-09-21T01:04:50Z",
            [
                "2026-09-20T07:00:00Z",
                "2026-09-20T10:06:00Z",
                "2026-09-20T09:30:00Z",
            ],
        )
        self.assertTrue(proof["definitelyNotTrueOpen"])
        self.assertEqual(
            proof["latestEventRungOpenTime"],
            "2026-09-20T10:06:00+00:00",
        )
        self.assertGreater(proof["minimumPossibleLagSeconds"], 900)
        self.assertEqual(proof["recoveredClassification"], "first_seen")

    def test_inside_tolerance_stays_unverified_and_is_never_promoted(self):
        proof = historical_open_proof(
            "2026-09-20T10:10:00Z",
            ["2026-09-20T10:00:00Z", "2026-09-20T10:05:00Z"],
        )
        self.assertFalse(proof["definitelyNotTrueOpen"])
        self.assertEqual(proof["recoveredClassification"], "unverified")

    def test_missing_metadata_stays_unverified(self):
        for first_seen, opens in [
            ("", ["2026-09-20T10:00:00Z"]),
            ("2026-09-20T10:10:00Z", []),
        ]:
            with self.subTest(first_seen=first_seen, opens=opens):
                proof = historical_open_proof(first_seen, opens)
                self.assertFalse(proof["definitelyNotTrueOpen"])
                self.assertEqual(proof["recoveredClassification"], "unverified")


if __name__ == "__main__":
    unittest.main()
