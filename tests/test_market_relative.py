from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cfb_edge.market_relative import (
    TimestampedObservation,
    load_csv,
    run,
    walk_forward,
)

UTC = timezone.utc
FEATURES = ("epaDiff", "successRateDiff")
HASH_A = hashlib.sha256(b"a").hexdigest()
HASH_B = hashlib.sha256(b"b").hexdigest()
HASH_C = hashlib.sha256(b"c").hexdigest()
START = datetime(2026, 10, 1, tzinfo=UTC)


def observation(
    game_id: str,
    *,
    season: int = 2025,
    week: int = 1,
    forecast_at: datetime,
    settled_at: datetime,
    actual: float,
) -> TimestampedObservation:
    kickoff = forecast_at + timedelta(days=2)
    return TimestampedObservation(
        season=season,
        week=week,
        game_id=game_id,
        market_home_margin=0.0,
        actual_home_margin=actual,
        features=(actual, actual * 0.5),
        market_as_of=forecast_at - timedelta(minutes=5),
        feature_as_of=(forecast_at - timedelta(days=1), forecast_at),
        forecast_at=forecast_at,
        kickoff_at=kickoff,
        settled_at=settled_at,
        market_source_path="raw/market.json",
        market_manifest_sha256=HASH_A,
        market_source_sha256=HASH_A,
        feature_source_path=("raw/feature-a.json", "raw/feature-b.json"),
        feature_manifest_sha256=(HASH_B, HASH_C),
        feature_source_sha256=(HASH_B, HASH_C),
        result_source_path="raw/result.json",
        result_manifest_sha256=HASH_C,
        result_source_sha256=HASH_C,
    )


def rows():
    historical = []
    for i in range(10):
        forecast = START - timedelta(days=30 - i)
        historical.append(observation(
            f"train-{i}", week=i + 1, forecast_at=forecast,
            settled_at=forecast + timedelta(days=3), actual=float(i % 3),
        ))
    # All test games share one forecast cutoff and are not training data for
    # one another, even though their labels are already present in this file.
    for i in range(3):
        historical.append(observation(
            f"test-{i}", season=2026, week=1, forecast_at=START,
            settled_at=START + timedelta(days=3), actual=float(i),
        ))
    return historical


def write_manifest(root: Path, name: str, content: bytes) -> tuple[str, str, str]:
    raw_path = root / "raw" / f"{name}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(content)
    source_hash = hashlib.sha256(content).hexdigest()
    manifest_path = root / "manifests" / f"{name}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "schemaVersion": 1,
        "files": [{"path": f"raw/{name}.json", "sha256": source_hash}],
    }), encoding="utf-8")
    return (
        f"raw/{name}.json",
        f"manifests/{name}.json",
        hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    )


def csv_row(root: Path, *, game_id: str = "game-1") -> dict[str, str]:
    market = write_manifest(root, "market", b'{"market":true}')
    feature_a = write_manifest(root, "feature-a", b'{"epa":0.2}')
    feature_b = write_manifest(root, "feature-b", b'{"success":0.1}')
    result = write_manifest(root, "result", b'{"final":true}')
    forecast = START.isoformat()
    return {
        "gameId": game_id,
        "season": "2026",
        "week": "1",
        "marketHomeMargin": "3.5",
        "actualHomeMargin": "7",
        "marketAsOf": (START - timedelta(minutes=1)).isoformat(),
        "marketSourcePath": market[0],
        "marketManifestPath": market[1],
        "marketManifestSha256": market[2],
        "marketSourceSha256": hashlib.sha256(b'{"market":true}').hexdigest(),
        "forecastAt": forecast,
        "kickoffAt": (START + timedelta(hours=2)).isoformat(),
        "settledAt": (START + timedelta(days=1)).isoformat(),
        "resultSourcePath": result[0],
        "resultManifestPath": result[1],
        "resultManifestSha256": result[2],
        "resultSourceSha256": hashlib.sha256(b'{"final":true}').hexdigest(),
        "epaDiff": "0.2",
        "epaDiffAsOf": (START - timedelta(days=1)).isoformat(),
        "epaDiffSourcePath": feature_a[0],
        "epaDiffManifestPath": feature_a[1],
        "epaDiffManifestSha256": feature_a[2],
        "epaDiffSourceSha256": hashlib.sha256(b'{"epa":0.2}').hexdigest(),
        "successRateDiff": "0.1",
        "successRateDiffAsOf": START.isoformat(),
        "successRateDiffSourcePath": feature_b[0],
        "successRateDiffManifestPath": feature_b[1],
        "successRateDiffManifestSha256": feature_b[2],
        "successRateDiffSourceSha256": hashlib.sha256(b'{"success":0.1}').hexdigest(),
    }


class MarketRelativeTests(unittest.TestCase):
    def test_only_strictly_previously_settled_rows_train_each_cohort(self):
        report = walk_forward(
            rows(),
            alpha=10.0,
            minimum_training_rows=10,
            prospective_start_at=START,
            reporting_minimum_predictions=3,
            reporting_minimum_kickoff_weeks=1,
            freeze_sha256=HASH_A,
            implementation_sha256=HASH_B,
            dataset_sha256=HASH_C,
        )
        self.assertEqual(report["status"], "WALK_FORWARD_EVIDENCE_COMPLETE_REVIEW_REQUIRED")
        self.assertEqual(report["predictionCount"], 3)
        self.assertEqual(report["weekMetrics"][0]["predictionCount"], 3)
        self.assertEqual({p["trainedRows"] for p in report["predictions"]}, {10})
        self.assertTrue(all(
            datetime.fromisoformat(p["trainedThroughAt"]) <
            datetime.fromisoformat(p["forecastAt"])
            for p in report["predictions"]
        ))
        self.assertFalse(report["deliveryEligible"])
        self.assertEqual(report["stakeUnits"], 0)

    def test_future_outcomes_cannot_change_same_cutoff_predictions(self):
        original = rows()
        changed = [
            replace(row, actual_home_margin=row.actual_home_margin + 10000)
            if row.game_id.startswith("test-") else row
            for row in original
        ]
        kwargs = dict(
            alpha=10.0,
            minimum_training_rows=10,
            prospective_start_at=START,
            reporting_minimum_predictions=1,
            reporting_minimum_kickoff_weeks=1,
            freeze_sha256=HASH_A,
            implementation_sha256=HASH_B,
            dataset_sha256=HASH_C,
        )
        first = walk_forward(original, **kwargs)
        second = walk_forward(changed, **kwargs)
        self.assertEqual(
            [p["challengerHomeMargin"] for p in first["predictions"]],
            [p["challengerHomeMargin"] for p in second["predictions"]],
        )

    def test_omits_test_predictions_without_training_floor(self):
        report = walk_forward(
            rows(),
            alpha=10.0,
            minimum_training_rows=500,
            prospective_start_at=START,
            reporting_minimum_predictions=1,
            reporting_minimum_kickoff_weeks=1,
            freeze_sha256=HASH_A,
            implementation_sha256=HASH_B,
            dataset_sha256=HASH_C,
        )
        self.assertEqual(
            report["status"], "INSUFFICIENT_PRIOR_SETTLED_TIMESTAMPED_DATA"
        )
        self.assertEqual(report["predictionCount"], 0)
        self.assertIsNone(report["marketMae"])
        self.assertEqual(report["deliveryEligible"], False)

    def test_reports_no_prospective_rows_separately_from_missing_training(self):
        report = walk_forward(
            rows()[:10],
            alpha=10.0,
            minimum_training_rows=10,
            prospective_start_at=START + timedelta(days=1),
            reporting_minimum_predictions=1,
            reporting_minimum_kickoff_weeks=1,
            freeze_sha256=HASH_A,
            implementation_sha256=HASH_B,
            dataset_sha256=HASH_C,
        )
        self.assertEqual(report["status"], "NO_PROSPECTIVE_FORECASTS")

    def test_csv_validates_source_manifest_and_archived_file_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = csv_row(root)
            path = root / "rows.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
            loaded = load_csv(path, FEATURES, archive_root=root)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].game_id, "game-1")
            self.assertEqual(loaded[0].market_source_path, "raw/market.json")
            (root / "raw" / "feature-a.json").write_bytes(b'{"epa":999}')
            with self.assertRaisesRegex(ValueError, "SHA-256 does not match"):
                load_csv(path, FEATURES, archive_root=root)

    def test_csv_rejects_future_feature_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "rows.csv"
            row = csv_row(root, game_id="bad-row")
            row["epaDiffAsOf"] = (START + timedelta(seconds=1)).isoformat()
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
            with self.assertRaisesRegex(ValueError, "epaDiffAsOf"):
                load_csv(path, FEATURES, archive_root=root)

    def test_csv_requires_valid_hash_and_source_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "rows.csv"
            row = csv_row(root, game_id="bad-hash")
            row["marketSourceSha256"] = "bad"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
            with self.assertRaisesRegex(ValueError, "marketSourceSha256"):
                load_csv(path, FEATURES, archive_root=root)

    def test_archive_paths_cannot_escape_evidence_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "rows.csv"
            row = csv_row(root, game_id="bad-path")
            row["marketSourcePath"] = "../secret.json"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
            with self.assertRaisesRegex(ValueError, "safe archive-relative path"):
                load_csv(path, FEATURES, archive_root=root)


    def test_runner_hashes_freeze_and_does_not_claim_ready_without_data(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            config = json.loads(
                (root / "config/s06_market_relative.json").read_text()
            )
            config["features"] = list(FEATURES)
            config["minimumTrainingRows"] = 500
            config_path = Path(tmp) / "freeze.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            data_path = Path(tmp) / "rows.csv"
            with data_path.open("w", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=list(csv_row(Path(tmp)))).writeheader()
            output_path = Path(tmp) / "report.json"
            report = run(data_path, config_path, output_path, Path(tmp))
            self.assertEqual(
                report["freezeSha256"],
                hashlib.sha256(config_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                report["datasetSha256"],
                hashlib.sha256(data_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(report["status"], "NO_PROSPECTIVE_FORECASTS")
            self.assertFalse(json.loads(output_path.read_text())["deliveryEligible"])


if __name__ == "__main__":
    unittest.main()
