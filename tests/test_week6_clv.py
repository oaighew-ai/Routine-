from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cfb_edge.authority_gate_audit import audit
from cfb_edge.week6_clv import (
    CLOSE_CONTRACT,
    DECISION_CONTRACT,
    build_close_report,
    freeze_decisions,
)


class Week6ClvTests(unittest.TestCase):
    def freeze(self):
        return {
            "contract":"CFB_EDGE_WEEK6_CLV_FREEZE_V1",
            "cohortId":"X",
            "registeredAt":"2026-09-29T06:42:27Z",
            "entryRowsFrozen":1,
            "rows":[{
                "game":"A @ H",
                "kickoff":"2026-10-03T16:00:00Z",
                "openingHomeLine":-3.0,
                "openObservedAt":"2026-09-28T12:05:00Z",
                "recoveryRetrievedAt":"2026-09-29T02:40:00Z",
                "venueOpenTime":"2026-09-28T12:00:00Z",
                "openLagSeconds":300,
                "evidenceSha256":"a"*64,
                "evidencePath":"data/x.json.gz",
            }],
        }

    def test_decision_freeze_rejects_out_of_cohort_candidate(self):
        report={
            "contract":"CFB_EDGE_S04_ES2_LIVE_V1",
            "modelId":"S04_ES2","modelVersion":"v",
            "candidateAuditRows":[{"game":"B @ C","kickoff":"2026-10-03T17:00:00Z","auditGrade":True}],
            "inspectedLiveRows":[],"qualifiedCount":0,"topFive":[],
        }
        with self.assertRaises(ValueError):
            freeze_decisions(
                source_report=report,cohort_freeze=self.freeze(),
                cohort_freeze_sha256="b"*64,
                decision_time=datetime(2026,9,29,7,tzinfo=timezone.utc),
            )

    def test_decision_freeze_is_pre_outcome(self):
        report={
            "contract":"CFB_EDGE_S04_ES2_LIVE_V1",
            "modelId":"S04_ES2","modelVersion":"v",
            "candidateAuditRows":[{"game":"A @ H","kickoff":"2026-10-03T16:00:00Z","auditGrade":True}],
            "inspectedLiveRows":[],"qualifiedCount":0,"topFive":[],
        }
        r=freeze_decisions(
            source_report=report,cohort_freeze=self.freeze(),
            cohort_freeze_sha256="b"*64,
            decision_time=datetime(2026,9,29,7,tzinfo=timezone.utc),
        )
        self.assertEqual(r["contract"],DECISION_CONTRACT)
        self.assertEqual(r["status"],"FROZEN_PRE_OUTCOME")
        self.assertEqual(r["deliveryEffect"],"NONE")

    def test_close_report_uses_last_pre_kickoff_quote(self):
        recs=[
            {"polled_at":"2026-10-03T15:40:00Z","quotes":[{
                "game":"A @ H","market":"spread","book":"kalshi","line":-3.5,
                "seen_at":"2026-10-03T15:40:00Z"
            }]},
            {"polled_at":"2026-10-03T15:55:00Z","quotes":[{
                "game":"A @ H","market":"spread","book":"kalshi","line":-4.0,
                "seen_at":"2026-10-03T15:55:00Z"
            }]},
            {"polled_at":"2026-10-03T16:05:00Z","quotes":[{
                "game":"A @ H","market":"spread","book":"kalshi","line":-4.5,
                "seen_at":"2026-10-03T16:05:00Z"
            }]},
        ]
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"opens.jsonl.gz"
            with gzip.open(path,"wt",encoding="utf-8") as fh:
                for rec in recs:
                    fh.write(json.dumps(rec)+"\n")
            r=build_close_report(
                freeze=self.freeze(),log_path=path,
                now=datetime(2026,10,3,16,10,tzinfo=timezone.utc),
                maximum_close_age_seconds=900,
            )
        self.assertEqual(r["contract"],CLOSE_CONTRACT)
        self.assertEqual(r["summary"]["gradeableCloseRows"],1)
        row=r["games"][0]
        self.assertEqual(row["observations"][-1]["derivedHomeLine"],-4.0)
        self.assertEqual(row["closeAgeSeconds"],300)

    def test_authority_audit_does_not_promote_from_clv(self):
        authority={
            "authorityId":"CFB_EDGE_PICKS_V1","modelId":"S02","modelVersion":"v",
            "status":"MODEL_REVIEW_REQUIRED","allowPaperDelivery":False,
            "asOf":"2026-09-18T18:02:10.143Z","deterministicReplay":"VERIFIED",
            "requirements":{
                "minimumNonPushForecasts":200,"minimumWeekClusters":8,
                "minimumOutcomeCoverage":.95,"minimumLogLossAdvantage":.003,
                "brierNoWorseThanMarket":True,"maximumEceDisadvantage":.01,
                "minimumAnytimeEValue":20,"maximumValidationAgeSeconds":604800,
            },
            "observed":{
                "nonPushForecasts":48,"weekClusters":2,"outcomeCoverage":1,
                "modelLogLoss":.6987,"marketLogLoss":.6932,
                "modelBrier":.2528,"marketBrier":.25,
                "modelEce":.0964,"marketEce":.0995,
                "maximumAnytimeEValue":1.024,
            },
            "failedGates":["MINIMUM_FORECASTS","MINIMUM_WEEKS"],
        }
        clv={"contract":"X","summary":{"gradeableRows":27}}
        r=audit(authority,now=datetime(2026,9,29,7,tzinfo=timezone.utc),clv_grades=clv)
        self.assertFalse(r["summary"]["allRegisteredGatesPass"])
        self.assertFalse(r["summary"]["authorityCanChangeFromThisReport"])
        self.assertEqual(r["researchContext"]["week6GradeableRows"],27)


if __name__=="__main__":
    unittest.main()
