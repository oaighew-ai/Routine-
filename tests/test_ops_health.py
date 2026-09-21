from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cfb_edge.ops_health import build_health, cadence_stage

ROOT = Path(__file__).resolve().parents[1]


def _capture(now: datetime) -> dict:
    return {
        "captureOutcome": "success",
        "freshAtGeneration": True,
        "pollAt": (now - timedelta(minutes=2)).isoformat(),
        "allowDelivery": False,
        "games": [
            {
                "game": "Alpha @ Beta",
                "observations": [{"book": "kalshi"}],
                "exclusions": ["DECISION_EVIDENCE_UNVERIFIED"],
            },
            {
                "game": "Gamma @ Delta",
                "observations": [],
                "exclusions": ["ABSENT_FROM_LATEST_POLL"],
            },
        ],
    }


class CadenceTests(unittest.TestCase):
    def test_edt_tuesday_nine_is_opening_board(self):
        self.assertEqual(
            cadence_stage(datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc)),
            "opening_board",
        )

    def test_est_tuesday_nine_is_still_opening_board(self):
        self.assertEqual(
            cadence_stage(datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc)),
            "opening_board",
        )

    def test_unregistered_hour_does_not_invent_a_stage(self):
        self.assertIsNone(
            cadence_stage(datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc))
        )


class OpsHealthTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config/edge_os.json").read_text())
        self.authority = json.loads(
            (ROOT / "config/delivery_authority.json").read_text()
        )
        self.registry = json.loads(
            (ROOT / "config/model_registry.json").read_text()
        )
        self.implementations = json.loads(
            (ROOT / "config/model_implementation_registry.json").read_text()
        )
        self.now = datetime(2026, 9, 18, 18, 4, tzinfo=timezone.utc)

    def test_current_registered_state_fails_closed(self):
        health = build_health(
            config=self.config,
            authority=self.authority,
            registry=self.registry,
            implementations=self.implementations,
            capture_report=_capture(self.now),
            generated_at=self.now,
            stage="opening_board",
        )
        self.assertEqual(health["state"], "SHADOW_BLOCKED")
        reasons = health["authority"]["recomputedReasons"]
        self.assertIn("MINIMUM_FORECASTS", reasons)
        self.assertIn("MINIMUM_WEEKS", reasons)
        self.assertIn("LOG_LOSS_ADVANTAGE", reasons)
        self.assertIn("BRIER_NO_WORSE", reasons)
        self.assertIn("ANYTIME_E_VALUE", reasons)

    def test_s02_external_implementation_is_explicit(self):
        health = build_health(
            config=self.config,
            authority=self.authority,
            registry=self.registry,
            implementations=self.implementations,
            capture_report=_capture(self.now),
            generated_at=self.now,
        )
        s02 = next(m for m in health["models"] if m["id"] == "S02")
        self.assertFalse(s02["reproducibleInRepo"])
        self.assertEqual(s02["implementationStatus"], "EXTERNAL_PRODUCTION_CANDIDATE")
        self.assertIn("PRODUCTION_MODEL_EXTERNAL_TO_REPO", health["warnings"])

    def test_capture_summary_never_turns_missing_games_into_observed_games(self):
        health = build_health(
            config=self.config,
            authority=self.authority,
            registry=self.registry,
            implementations=self.implementations,
            capture_report=_capture(self.now),
            generated_at=self.now,
        )
        self.assertEqual(health["capture"]["gamesTotal"], 2)
        self.assertEqual(health["capture"]["gamesObserved"], 1)
        self.assertEqual(health["capture"]["gamesMissing"], 1)

    def test_candidate_and_grade_counts_are_recomputed_from_rows(self):
        candidate = {
            "id": "c1",
            "decision": "BET",
            "modelId": "S02",
        }
        grade = {"candidateId": "c1"}
        health = build_health(
            config=self.config,
            authority=self.authority,
            registry=self.registry,
            implementations=self.implementations,
            capture_report=_capture(self.now),
            candidates=[candidate],
            grades=[grade],
            generated_at=self.now,
        )
        self.assertEqual(health["ledger"]["candidateRows"], 1)
        self.assertEqual(health["ledger"]["betRows"], 1)
        self.assertEqual(health["ledger"]["ungradedBetRows"], 0)

    def test_current_quote_is_required_for_final_board_but_not_model_review(self):
        stale = _capture(self.now)
        stale["pollAt"] = (self.now - timedelta(hours=2)).isoformat()

        final = build_health(
            config=self.config,
            authority={**self.authority, "status": "PASSED",
                       "allowPaperDelivery": True, "failedGates": [],
                       "observed": {
                           **self.authority["observed"],
                           "nonPushForecasts": 250,
                           "weekClusters": 10,
                           "modelLogLoss": 0.68,
                           "marketLogLoss": 0.69,
                           "modelBrier": 0.24,
                           "marketBrier": 0.25,
                           "maximumAnytimeEValue": 25.0,
                       }},
            registry=self.registry,
            implementations=self.implementations,
            capture_report=stale,
            generated_at=self.now,
            stage="final_board",
        )
        self.assertEqual(final["state"], "DATA_BLOCKED")

        review = build_health(
            config=self.config,
            authority={**self.authority, "status": "PASSED",
                       "allowPaperDelivery": True, "failedGates": [],
                       "observed": {
                           **self.authority["observed"],
                           "nonPushForecasts": 250,
                           "weekClusters": 10,
                           "modelLogLoss": 0.68,
                           "marketLogLoss": 0.69,
                           "modelBrier": 0.24,
                           "marketBrier": 0.25,
                           "maximumAnytimeEValue": 25.0,
                       }},
            registry=self.registry,
            implementations=self.implementations,
            capture_report=stale,
            generated_at=self.now,
            stage="model_review",
        )
        self.assertEqual(review["state"], "PAPER_READY")


if __name__ == "__main__":
    unittest.main()
