from __future__ import annotations

import unittest
from datetime import datetime, timezone

from cfb_edge.powerup_health import build


class PowerupHealthTests(unittest.TestCase):
    def test_pre_window_is_not_a_failure(self):
        r=build(
            capture_health=None, es2=None, grades=None,
            br2={"generatedAt":"2026-09-23T00:00:00Z","summary":{
                "games":56,
                "featureCoverageRows":{"ppaDiff":56,"epaDiff":0}
            },"sourceManifest":[{"kind":"plays","path":"p","sha256":"abc"}]},
            freeze_manifest={"freezeId":"x"},
            now=datetime(2026,9,23,1,tzinfo=timezone.utc),
        )
        self.assertEqual(r["status"],"PRE_WINDOW")
        self.assertEqual(r["stages"]["openProvenance"]["status"],"PRE_WINDOW")
        self.assertFalse(r["week5DecisionRuleChanged"])

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
