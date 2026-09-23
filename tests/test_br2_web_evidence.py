from __future__ import annotations

import unittest

from cfb_edge.br2_web_evidence import validate_packet


class Br2WebEvidenceTests(unittest.TestCase):
    def base(self):
        return {
            "game":"A @ H",
            "team":"H",
            "kickoff":"2026-09-26T16:00:00Z",
            "sourceKind":"official_depth_chart",
            "sourceUrl":"https://athletics.example.edu/depth.pdf",
            "publishedAt":"2026-09-22T18:00:00Z",
            "retrievedAt":"2026-09-23T01:00:00Z",
            "captureMethod":"firecrawl_pdf_extract",
            "parserVersion":"depth-chart-v1",
            "contentSha256":"abc",
            "firstListedQb":"QB One",
            "priorFirstListedQb":"QB One",
        }

    def test_llm_extraction_does_not_auto_promote(self):
        p=self.base()
        p["deterministicallyValidated"]=False
        r=validate_packet(p)
        self.assertFalse(r["auditGrade"])
        self.assertIn("EXTRACTION_NOT_DETERMINISTICALLY_VALIDATED",r["exclusions"])
        self.assertIsNone(r["featureValue"])

    def test_validated_official_pre_kickoff_packet_can_grade(self):
        p=self.base()
        p["deterministicallyValidated"]=True
        r=validate_packet(p)
        self.assertTrue(r["auditGrade"])
        self.assertEqual(r["featureValue"],1.0)

    def test_exa_discovery_never_becomes_feature(self):
        p=self.base()
        p["captureMethod"]="exa_search"
        p["deterministicallyValidated"]=True
        r=validate_packet(p)
        self.assertFalse(r["auditGrade"])
        self.assertIn("DISCOVERY_ONLY",r["exclusions"])


if __name__=="__main__":
    unittest.main()
