from __future__ import annotations

import unittest
from datetime import datetime, timezone

from cfb_edge.powerup_health import build


def cohort():
    return {
        "contract":"CFB_EDGE_COHORT_IDENTITY_V1",
        "cohortId":"CFB_2026_PRODUCT_WEEK5",
        "season":2026,
        "productWeek":5,
        "gameWindow":{"startsAt":"2026-09-25T00:00:00Z","endsAt":"2026-09-27T12:00:00Z"},
        "captureWindow":{"startsAt":"2026-09-20T00:00:00Z","endsAt":"2026-09-27T12:00:00Z"},
        "prospectiveWindow":{"startsAt":"2026-09-20T00:00:00Z","endsAt":"2026-09-25T20:00:00Z"},
        "providerWeeks":{"cfbfastR":4,"collegefootballdata":4},
    }


class PowerupHealthTests(unittest.TestCase):
    def test_pre_window_is_not_a_failure(self):
        r=build(
            capture_health=None, es2=None, grades=None,
            br2={"generatedAt":"2026-09-23T00:00:00Z","summary":{
                "games":56,
                "featureCoverageRows":{"ppaDiff":56,"epaDiff":0}
            },"sourceManifest":[{"kind":"plays","path":"p","sha256":"abc"}]},
            freeze_manifest={"freezeId":"x"},
            cohort=cohort(),
            now=datetime(2026,9,19,1,tzinfo=timezone.utc),
        )
        self.assertEqual(r["status"],"PRE_WINDOW")
        self.assertEqual(r["stages"]["openProvenance"]["status"],"PRE_WINDOW")
        self.assertFalse(r["week5DecisionRuleChanged"])


    def test_active_window_without_health_artifact_is_not_pre_window(self):
        r=build(
            capture_health=None, es2={"qualifiedCount":0}, grades=None,
            br2={"generatedAt":"2026-09-23T00:00:00Z","summary":{
                "games":57,
                "featureCoverageRows":{"ppaDiff":57,"epaDiff":0}
            },"sourceManifest":[{"kind":"plays","path":"p","sha256":"abc"}]},
            freeze_manifest={"freezeId":"x"},
            cohort=cohort(),
            now=datetime(2026,9,23,1,tzinfo=timezone.utc),
        )
        self.assertEqual(r["status"],"PROSPECTIVE_COLLECTION_ACTIVE")
        self.assertEqual(r["captureWindowState"],"ACTIVE")
        self.assertEqual(r["stages"]["openProvenance"]["status"],"AWAITING_ARTIFACT")
        self.assertFalse(r["stages"]["openProvenance"]["complete"])

    def test_integrity_failure_blocks_evidence_state(self):
        r=build(
            capture_health={"status":"INTEGRITY_FAILURE","summary":{
                "provenanceCompleteTrueOpenRows":4,"slateRows":56,"invalidTrueOpenRows":1
            }},
            es2=None, grades=None, br2=None, freeze_manifest={},
            now=datetime(2026,9,23,1,tzinfo=timezone.utc),
        )
        self.assertEqual(r["status"],"EVIDENCE_BLOCKED")
        self.assertIn("WEEK5_OPEN_PROVENANCE_FAILURE",r["warnings"])


if __name__=="__main__":
    unittest.main()
