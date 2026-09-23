from __future__ import annotations

import unittest
from datetime import datetime, timezone

from cfb_edge.private_site_bridge import build


class PrivateSiteBridgeTests(unittest.TestCase):
    def test_bridge_never_claims_pick_authority(self):
        r=build(
            authority={"modelId":"S02","modelVersion":"v1","status":"FAILED","allowPaperDelivery":False},
            readiness={"status":"PRE_WINDOW","warnings":[],"nextUnlocks":[],"metrics":{
                "br2Games":56,"br2SourceManifestComplete":True
            }},
            capture_health=None, es2=None, grades=None,
            br2={"status":"DATA_COLLECTION_ONLY","generatedAt":"2026-09-23T01:00:00Z",
                 "sourceManifest":[{"kind":"plays","path":"x","sha256":"abc"}],
                 "summary":{"games":56,"modelingEligible":False,
                 "fullyPopulatedRows":0,
                 "featureCoverageRows":{"ppaDiff":56,"epaDiff":0,"qbContinuityDiff":1}},
                 "rows":[{"game":"A @ H","kickoff":"2026-09-26T16:00:00Z",
                          "features":{"qbContinuityDiff":0.0}}]},
            generated_at=datetime(2026,9,23,1,tzinfo=timezone.utc),
        )
        self.assertFalse(r["authority"]["githubCanPublishPicks"])
        self.assertEqual(r["authority"]["picksSource"],"PRIVATE_SITE_LOCAL")
        self.assertEqual(r["research"]["deliveryEffect"],"NONE")
        self.assertEqual(r["br2"]["populatedFamilies"],["ppaDiff","qbContinuityDiff"])
        self.assertEqual(r["br2"]["missingFamilies"],["epaDiff"])
        self.assertEqual(r["br2"]["coverageRows"]["qbContinuityDiff"],1)
        self.assertEqual(r["br2"]["fullyPopulatedRows"],0)
        self.assertEqual(r["br2"]["qbContinuityExample"]["game"],"A @ H")
        self.assertEqual(r["br2"]["qbContinuityExample"]["value"],0.0)


    def test_missing_capture_health_uses_readiness_window_state(self):
        r=build(
            authority={},
            readiness={
                "status":"PROSPECTIVE_COLLECTION_ACTIVE",
                "warnings":[],
                "nextUnlocks":[],
                "metrics":{},
                "stages":{
                    "openProvenance":{
                        "status":"AWAITING_ARTIFACT",
                        "detail":"Capture window active, health artifact pending.",
                        "complete":False,
                    }
                },
            },
            capture_health=None, es2={"status":"SHADOW_ONLY"}, grades=None,
            br2=None,
            generated_at=datetime(2026,9,23,1,tzinfo=timezone.utc),
        )
        self.assertEqual(r["week5"]["captureStatus"],"AWAITING_ARTIFACT")
        self.assertEqual(r["research"]["status"],"PROSPECTIVE_COLLECTION_ACTIVE")

    def test_week5_exec_grade_is_kept_separate(self):
        r=build(
            authority={},readiness={},capture_health={"status":"COLLECTING","summary":{
                "slateRows":56,"provenanceCompleteTrueOpenRows":10
            }},
            es2={"qualifiedCount":2,"status":"SHADOW_ONLY"},
            grades={"summary":{"executableShadow":{
                "gradeableRows":1,"meanLineClvPoints":0.5,"beatCloseRate":1.0
            }}},
            br2=None,
            generated_at=datetime(2026,9,23,1,tzinfo=timezone.utc),
        )
        self.assertEqual(r["week5"]["qualifiedExecutableShadowRows"],2)
        self.assertEqual(r["week5"]["gradedExecutableShadowRows"],1)
        self.assertEqual(r["week5"]["meanExecutableShadowClvPoints"],0.5)


if __name__=="__main__":
    unittest.main()
