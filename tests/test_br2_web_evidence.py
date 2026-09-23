from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from cfb_edge.br2_web_evidence import build, validate_packet


class Br2WebEvidenceTests(unittest.TestCase):
    REGISTRY={"teams":{
        "h":{"officialDomains":["h.example.edu"]},
        "a":{"officialDomains":["a.example.edu"]},
    }}

    def setup_capture(self,root:Path,team:str,current_qb:str,prior_qb:str):
        d=root/"raw"; d.mkdir(exist_ok=True)
        cur=d/f"{team}-current.txt"; prior=d/f"{team}-prior.txt"
        cur.write_text(f"QB 1 {current_qb}\nQB 2 Backup",encoding="utf-8")
        prior.write_text(f"QB 1 {prior_qb}\nQB 2 Backup",encoding="utf-8")
        return {
            "contentPath":str(cur.relative_to(root)),
            "contentSha256":hashlib.sha256(cur.read_bytes()).hexdigest(),
            "priorContentPath":str(prior.relative_to(root)),
            "priorContentSha256":hashlib.sha256(prior.read_bytes()).hexdigest(),
        }

    def base(self,root:Path,side="home",team="H",first="QB One",prior="QB One"):
        cap=self.setup_capture(root,team,first,prior)
        return {
            "game":"A @ H","team":team,"side":side,
            "kickoff":"2026-09-26T16:00:00Z",
            "sourceKind":"official_depth_chart",
            "sourceUrl":f"https://{team.lower()}.example.edu/depth-current",
            "priorSourceUrl":f"https://{team.lower()}.example.edu/depth-prior",
            "publishedAt":"2026-09-22T18:00:00Z",
            "priorPublishedAt":"2026-09-15T18:00:00Z",
            "retrievedAt":"2026-09-23T01:00:00Z",
            "priorRetrievedAt":"2026-09-16T01:00:00Z",
            "captureMethod":"firecrawl_pdf_extract",
            "parserVersion":"depth-chart-v1",
            "firstListedQb":first,
            "priorFirstListedQb":prior,
            **cap,
        }

    def test_llm_extraction_does_not_auto_promote(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            p=self.base(root); p["deterministicallyValidated"]=False
            r=validate_packet(
                p,official_registry=self.REGISTRY,evidence_root=root
            )
            self.assertFalse(r["auditGrade"])
            self.assertIn("EXTRACTION_NOT_DETERMINISTICALLY_VALIDATED",r["exclusions"])
            self.assertIsNone(r["teamContinuity"])

    def test_two_validated_sides_create_game_differential(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            h=self.base(root,"home","H","QB One","QB One")
            a=self.base(root,"away","A","QB New","QB Old")
            h["deterministicallyValidated"]=True
            a["deterministicallyValidated"]=True
            r=build(
                [h,a],official_registry=self.REGISTRY,evidence_root=root
            )
            self.assertEqual(r["summary"]["auditGradeGameRows"],1)
            self.assertTrue(r["rows"][0]["auditGrade"])
            self.assertEqual(r["rows"][0]["featureValue"],1.0)

    def test_one_sided_evidence_stays_null(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            h=self.base(root); h["deterministicallyValidated"]=True
            r=build(
                [h],official_registry=self.REGISTRY,evidence_root=root
            )
            self.assertFalse(r["rows"][0]["auditGrade"])
            self.assertIsNone(r["rows"][0]["featureValue"])

    def test_unregistered_domain_stays_null(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            p=self.base(root); p["deterministicallyValidated"]=True
            p["sourceUrl"]="https://fan-site.example/depth"
            r=validate_packet(
                p,official_registry=self.REGISTRY,evidence_root=root
            )
            self.assertFalse(r["auditGrade"])
            self.assertIn("SOURCE_DOMAIN_NOT_REGISTERED_FOR_TEAM",r["exclusions"])

    def test_hash_mismatch_stays_null(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            p=self.base(root); p["deterministicallyValidated"]=True
            p["contentSha256"]="0"*64
            r=validate_packet(
                p,official_registry=self.REGISTRY,evidence_root=root
            )
            self.assertFalse(r["auditGrade"])
            self.assertIn("CURRENT_CONTENT_HASH_MISMATCH",r["exclusions"])

    def test_exa_discovery_never_becomes_feature(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            p=self.base(root); p["captureMethod"]="exa_search"
            p["deterministicallyValidated"]=True
            r=validate_packet(
                p,official_registry=self.REGISTRY,evidence_root=root
            )
            self.assertFalse(r["auditGrade"])
            self.assertIn("DISCOVERY_ONLY",r["exclusions"])


if __name__=="__main__":
    unittest.main()
