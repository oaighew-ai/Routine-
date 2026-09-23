from __future__ import annotations

import unittest

from cfb_edge.br2_web_evidence import build, validate_packet


class Br2WebEvidenceTests(unittest.TestCase):
    def base(self,side="home",team="H",first="QB One",prior="QB One"):
        return {
            "game":"A @ H",
            "team":team,
            "side":side,
            "kickoff":"2026-09-26T16:00:00Z",
            "sourceKind":"official_depth_chart",
            "sourceUrl":f"https://{team.lower()}.example.edu/depth.pdf",
            "publishedAt":"2026-09-22T18:00:00Z",
            "retrievedAt":"2026-09-23T01:00:00Z",
            "captureMethod":"firecrawl_pdf_extract",
            "parserVersion":"depth-chart-v1",
            "contentSha256":"abc",
            "firstListedQb":first,
            "priorFirstListedQb":prior,
        }

    def test_llm_extraction_does_not_auto_promote(self):
        p=self.base(); p["deterministicallyValidated"]=False
        r=validate_packet(p)
        self.assertFalse(r["auditGrade"])
        self.assertIn("EXTRACTION_NOT_DETERMINISTICALLY_VALIDATED",r["exclusions"])
        self.assertIsNone(r["teamContinuity"])

    def test_two_validated_sides_create_game_differential(self):
        h=self.base("home","H","QB One","QB One")
        a=self.base("away","A","QB New","QB Old")
        h["deterministicallyValidated"]=True; a["deterministicallyValidated"]=True
        r=build([h,a])
        self.assertEqual(r["summary"]["auditGradeGameRows"],1)
        self.assertTrue(r["rows"][0]["auditGrade"])
        self.assertEqual(r["rows"][0]["featureValue"],1.0)

    def test_one_sided_evidence_stays_null(self):
        h=self.base(); h["deterministicallyValidated"]=True
        r=build([h])
        self.assertFalse(r["rows"][0]["auditGrade"])
        self.assertIsNone(r["rows"][0]["featureValue"])

    def test_exa_discovery_never_becomes_feature(self):
        p=self.base(); p["captureMethod"]="exa_search"; p["deterministicallyValidated"]=True
        r=validate_packet(p)
        self.assertFalse(r["auditGrade"])
        self.assertIn("DISCOVERY_ONLY",r["exclusions"])


if __name__=="__main__":
    unittest.main()
