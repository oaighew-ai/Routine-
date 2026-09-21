from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from cfb_edge.external_forecast import (
    CONTRACT,
    ForecastImportError,
    import_envelope,
    validate_envelope,
)


def authority():
    return {
        "modelId": "S02",
        "modelVersion": "market-anchor-shrinkage-1",
        "modelSha256": "abc123",
        "protocol": "s02-sequential-1",
    }


def forecast(**changes):
    row = {
        "id": "s02-2026w4-game1",
        "gameId": "game-1",
        "generatedAt": "2026-09-21T12:00:00+00:00",
        "startsAt": "2026-09-26T19:30:00+00:00",
        "modelId": "S02",
        "modelVersion": "market-anchor-shrinkage-1",
        "modelSha256": "abc123",
        "protocol": "s02-sequential-1",
        "market": "spreads",
        "side": "home",
        "fairLine": -3.25,
        "fairProbability": 0.541,
        "uncertainty": 0.08,
        "featureSnapshotHash": "feature-hash-1",
        "sourceSnapshotId": "capture-1",
    }
    row.update(changes)
    return row


def envelope(rows=None, **changes):
    data = {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "generatedAt": "2026-09-21T12:01:00+00:00",
        "model": authority(),
        "forecasts": list(rows if rows is not None else [forecast()]),
    }
    data.update(changes)
    return data


class ExternalForecastTests(unittest.TestCase):
    def test_exact_identity_and_frozen_row_validate(self):
        rows = validate_envelope(envelope(), authority())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["modelId"], "S02")

    def test_model_hash_mismatch_fails_closed(self):
        bad = envelope()
        bad["model"]["modelSha256"] = "wrong"
        with self.assertRaises(ForecastImportError):
            validate_envelope(bad, authority())

    def test_forecast_at_or_after_kickoff_is_rejected(self):
        bad = envelope([
            forecast(
                generatedAt="2026-09-26T19:30:00+00:00",
                startsAt="2026-09-26T19:30:00+00:00",
            )
        ])
        with self.assertRaises(ForecastImportError):
            validate_envelope(bad, authority())

    def test_duplicate_game_market_side_is_rejected(self):
        second = forecast(id="s02-2")
        with self.assertRaises(ForecastImportError):
            validate_envelope(envelope([forecast(), second]), authority())

    def test_probability_outside_unit_interval_is_rejected(self):
        with self.assertRaises(ForecastImportError):
            validate_envelope(
                envelope([forecast(fairProbability=1.01)]), authority()
            )

    def test_import_is_append_only_and_refuses_same_forecast_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "forecasts.jsonl"
            self.assertEqual(
                import_envelope(envelope(), authority(), out=path), 1
            )
            with self.assertRaises(ForecastImportError):
                import_envelope(envelope(), authority(), out=path)

    def test_dry_run_validates_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "forecasts.jsonl"
            self.assertEqual(
                import_envelope(
                    envelope(), authority(), out=path, dry_run=True
                ),
                1,
            )
            self.assertFalse(path.exists())

    def test_export_cannot_switch_to_another_market(self):
        bad = deepcopy(forecast())
        bad["market"] = "h2h"
        with self.assertRaises(ForecastImportError):
            validate_envelope(envelope([bad]), authority())


if __name__ == "__main__":
    unittest.main()
