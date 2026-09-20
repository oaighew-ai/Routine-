from __future__ import annotations

import json
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cfb_edge.source_of_truth import build

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc)


def authority(**changes):
    data = {
        "authorityId": "CFB_EDGE_PICKS_V1",
        "asOf": "2026-09-20T15:58:00+00:00",
        "modelId": "S02",
        "modelVersion": "market-anchor-shrinkage-1",
        "modelSha256": "eb79385a8b492855b219a2417858a57fb61482014c1e375546dd3a166c4a10c8",
        "protocol": "s02-sequential-1",
        "status": "PASSED",
        "allowPaperDelivery": True,
        "validationCompleted": True,
        "deterministicReplay": "VERIFIED",
        "failedGates": [],
        "requirements": {
            "minimumNonPushForecasts": 200,
            "minimumWeekClusters": 8,
            "minimumOutcomeCoverage": 0.95,
            "minimumLogLossAdvantage": 0.003,
            "brierNoWorseThanMarket": True,
            "maximumEceDisadvantage": 0.01,
            "minimumAnytimeEValue": 20.0,
            "maximumValidationAgeSeconds": 604800,
            "maximumQuoteAgeSeconds": 900,
            "minimumSamePointBooks": 3,
        },
        "observed": {
            "nonPushForecasts": 250,
            "weekClusters": 10,
            "outcomeCoverage": 0.99,
            "modelLogLoss": 0.68,
            "marketLogLoss": 0.69,
            "modelBrier": 0.24,
            "marketBrier": 0.25,
            "modelEce": 0.04,
            "marketEce": 0.04,
            "maximumAnytimeEValue": 25.0,
        },
        "actualBetsRecorded": False,
    }
    data.update(changes)
    return data


def capture(at=NOW):
    return {
        "captureOutcome": "success",
        "freshAtGeneration": True,
        "pollAt": (at - timedelta(minutes=2)).isoformat(),
    }


def registry():
    return {"models": [{"id": "S02", "deliveryEligible": True}]}


def candidate(i="one", *, side="Alpha", market="spreads", ev=0.04,
              reason_codes=None, decision="BET", logged=NOW):
    return {
        "id": i,
        "loggedAt": (logged - timedelta(minutes=3)).isoformat(),
        "startsAt": (NOW + timedelta(hours=3)).isoformat(),
        "eventId": "event-1",
        "market": market,
        "side": side,
        "line": 3.5,
        "price": -105,
        "venue": "draftkings",
        "bookSet": ["draftkings", "fanduel", "caesars"],
        "priceSource": "capture",
        "decision": decision,
        "reasonCodes": list(reason_codes or []),
        "outputs": {"ev": ev, "stake": 0.75},
        "modelId": "S02",
        "modelVersion": "market-anchor-shrinkage-1",
        "modelSha256": "eb79385a8b492855b219a2417858a57fb61482014c1e375546dd3a166c4a10c8",
        "protocol": "s02-sequential-1",
    }


class SourceOfTruthTests(unittest.TestCase):
    def test_repository_registry_has_one_delivery_candidate(self):
        registry = json.loads((ROOT / "config/model_registry.json").read_text())
        eligible = [m["id"] for m in registry["models"] if m["deliveryEligible"]]
        self.assertEqual(eligible, ["S02"])

    def test_blocked_authority_suppresses_even_a_bet_row(self):
        result = build(
            authority=authority(status="MODEL_REVIEW_REQUIRED",
                                allowPaperDelivery=False,
                                failedGates=["MINIMUM_FORECASTS"]),
            registry=registry(), candidates=[candidate()], capture_report=capture(),
            generated_at=NOW,
        )
        self.assertEqual(result["status"], "NO_BET")
        self.assertEqual(result["picks"], [])
        self.assertEqual(result["paperExposureUnits"], 0.0)
        self.assertIn("PAPER_DELIVERY_BLOCKED", result["authorityReasons"])

    def test_exact_current_validated_row_can_make_a_paper_pick(self):
        result = build(
            authority=authority(), registry=registry(), candidates=[candidate()],
            capture_report=capture(), generated_at=NOW,
        )
        self.assertTrue(result["allowPaperDelivery"])
        self.assertEqual(result["status"], "PROVISIONAL_PAPER_CARD")
        self.assertEqual(len(result["picks"]), 1)
        self.assertEqual(result["picks"][0]["status"], "PROVISIONAL_LEAN")
        self.assertEqual(result["paperExposureUnits"], 0.75)
        self.assertEqual(result["actualExposureUnits"], 0.0)

    def test_passed_label_cannot_override_failed_metrics(self):
        bad = authority()
        bad["observed"]["nonPushForecasts"] = 48
        result = build(
            authority=bad, registry=registry(), candidates=[candidate()],
            capture_report=capture(), generated_at=NOW,
        )
        self.assertEqual(result["picks"], [])
        self.assertIn("MINIMUM_FORECASTS", result["authorityReasons"])

    def test_stale_validation_fails_closed(self):
        old = authority(asOf=(NOW - timedelta(days=8)).isoformat())
        result = build(
            authority=old, registry=registry(), candidates=[candidate()],
            capture_report=capture(), generated_at=NOW,
        )
        self.assertIn("VALIDATION_STALE", result["authorityReasons"])

    def test_registry_must_name_exactly_one_matching_delivery_model(self):
        result = build(
            authority=authority(), registry={"models": []},
            candidates=[candidate()], capture_report=capture(), generated_at=NOW,
        )
        self.assertIn("REGISTRY_AUTHORITY_MISMATCH", result["authorityReasons"])

    def test_same_point_book_minimum_is_rechecked(self):
        row = candidate()
        row["bookSet"] = ["draftkings", "fanduel"]
        result = build(
            authority=authority(), registry=registry(), candidates=[row],
            capture_report=capture(), generated_at=NOW,
        )
        self.assertIn("INSUFFICIENT_SAME_POINT_BOOKS",
                      result["candidateAudit"][0]["exclusions"])

    def test_no_evidence_can_never_enter_the_card(self):
        row = candidate(reason_codes=["NO_EVIDENCE"])
        result = build(authority=authority(), registry=registry(), candidates=[row],
                       capture_report=capture(), generated_at=NOW)
        self.assertEqual(result["picks"], [])
        self.assertIn("NO_EVIDENCE", result["candidateAudit"][0]["exclusions"])

    def test_model_hash_mismatch_fails_closed(self):
        row = candidate()
        row["modelSha256"] = "wrong"
        result = build(authority=authority(), registry=registry(), candidates=[row],
                       capture_report=capture(), generated_at=NOW)
        self.assertEqual(result["picks"], [])
        self.assertIn("MODELSHA256_MISMATCH",
                      result["candidateAudit"][0]["exclusions"])

    def test_stale_quote_fails_closed(self):
        old = NOW - timedelta(hours=1)
        result = build(authority=authority(), registry=registry(),
                       candidates=[candidate(logged=old)],
                       capture_report=capture(), generated_at=NOW)
        self.assertEqual(result["picks"], [])
        self.assertIn("QUOTE_STALE", result["candidateAudit"][0]["exclusions"])

    def test_opposing_sides_void_the_market(self):
        a = candidate("a", side="Alpha", ev=0.05)
        b = candidate("b", side="Beta", ev=0.06)
        result = build(authority=authority(), registry=registry(), candidates=[a, b],
                       capture_report=capture(), generated_at=NOW)
        self.assertEqual(result["picks"], [])

    def test_card_is_capped_at_five_and_ranked(self):
        rows = []
        for i in range(7):
            row = candidate(str(i), ev=0.01 + i / 100)
            row["eventId"] = f"event-{i}"
            rows.append(row)
        result = build(authority=authority(), registry=registry(), candidates=rows,
                       capture_report=capture(), generated_at=NOW)
        self.assertEqual(len(result["picks"]), 5)
        self.assertEqual([p["rank"] for p in result["picks"]], [1, 2, 3, 4, 5])
        self.assertGreaterEqual(result["picks"][0]["netEdge"],
                                result["picks"][-1]["netEdge"])

    def test_change_flag_compares_immutable_card_hashes(self):
        first = build(authority=authority(), registry=registry(), candidates=[candidate()],
                      capture_report=capture(), generated_at=NOW)
        same = build(authority=authority(), registry=registry(), candidates=[candidate()],
                     capture_report=capture(), generated_at=NOW,
                     previous=first)
        self.assertFalse(same["changedSincePreviousCard"])
        altered = candidate(ev=0.05)
        changed = build(authority=authority(), registry=registry(), candidates=[altered],
                        capture_report=capture(), generated_at=NOW,
                        previous=first)
        self.assertTrue(changed["changedSincePreviousCard"])


if __name__ == "__main__":
    unittest.main()
