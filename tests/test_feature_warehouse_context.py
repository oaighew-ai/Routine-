from __future__ import annotations

import unittest
from datetime import datetime, timezone

from cfb_edge.feature_warehouse import build_snapshot


class FeatureWarehouseContextTests(unittest.TestCase):
    def test_only_audited_context_features_enter_warehouse(self):
        context={
            "contract":"CFB_EDGE_BR2_CONTEXT_V1",
            "rows":[{
                "game":"A @ H",
                "rowSha256":"rowhash",
                "features":{
                    "epaDiff":0.4,"linePlayDiff":0.2,"travelMilesDiff":-100,
                    "windMph":12,"qbContinuityDiff":1.0,
                },
                "audit":{
                    "epaDiff":True,"linePlayDiff":True,"travelMilesDiff":True,
                    "windMph":True,"qbContinuityDiff":False,
                },
            }]
        }
        r=build_snapshot(
            slate=[{"game":"A @ H","kickoff":"2026-09-26T16:00:00Z"}],
            plays=[],prior_games=[],
            as_of=datetime(2026,9,23,tzinfo=timezone.utc),
            context=context,
        )
        f=r["rows"][0]["features"]
        self.assertEqual(f["epaDiff"],0.4)
        self.assertEqual(f["linePlayDiff"],0.2)
        self.assertEqual(f["travelMilesDiff"],-100)
        self.assertEqual(f["windMph"],12)
        self.assertIsNone(f["qbContinuityDiff"])
        self.assertTrue(r["rows"][0]["featureSources"]["epaDiff"].startswith("CFB_EDGE_BR2_CONTEXT_V1:"))


if __name__=="__main__":
    unittest.main()
