"""Timestamp-strict walk-forward evaluation for frozen S06_MR1.

The challenger predicts only a residual to the recorded spread-market margin.
It emits evaluation evidence, never picks, probabilities, stakes, or delivery
authority.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from .challenger import Observation, fit

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class TimestampedObservation:
    season: int
    week: int
    game_id: str
    market_home_margin: float
    actual_home_margin: float
    features: tuple[float, ...]
    market_as_of: datetime
    feature_as_of: tuple[datetime, ...]
    forecast_at: datetime
    kickoff_at: datetime
    settled_at: datetime
    market_source_path: str
    market_manifest_sha256: str
    market_source_sha256: str
    feature_source_path: tuple[str, ...]
    feature_manifest_sha256: tuple[str, ...]
    feature_source_sha256: tuple[str, ...]
    result_source_path: str
    result_manifest_sha256: str
    result_source_sha256: str


def _time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _hash(value: str, field: str) -> str:
    normalized = value.strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise ValueError(f"{field} must be a 64-character SHA-256 digest")
    return normalized


def _finite(value: str, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be numeric") from None
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _archive_file(root: Path, relative: str, field: str) -> Path:
    requested = PurePosixPath(relative)
    if (
        not relative
        or requested.is_absolute()
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in requested.parts)
    ):
        raise ValueError(f"{field} must be a safe archive-relative path")
    archive_root = root.resolve()
    resolved = archive_root.joinpath(*requested.parts).resolve()
    try:
        resolved.relative_to(archive_root)
    except ValueError:
        raise ValueError(f"{field} escapes the evidence archive") from None
    if not resolved.is_file():
        raise ValueError(f"{field} does not identify an archived file")
    return resolved


def _archive_key(root: Path, relative: str, field: str) -> str:
    resolved = _archive_file(root, relative, field)
    return resolved.relative_to(root.resolve()).as_posix()


def _verify_manifest(
    root: Path,
    relative: str,
    expected_sha256: str,
    expected_observed_at: datetime,
    field: str,
    cache: dict[tuple[str, str, str], set[tuple[str, str]]],
) -> set[tuple[str, str]]:
    manifest_path = _archive_file(root, relative, f"{field}Path")
    manifest_key = (
        str(manifest_path),
        expected_sha256,
        expected_observed_at.isoformat(),
    )
    if manifest_key in cache:
        return cache[manifest_key]
    actual_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if actual_hash != expected_sha256:
        raise ValueError(f"{field} SHA-256 does not match its archived manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} is not a readable JSON manifest: {exc}") from None
    entries = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        raise ValueError(f"{field} has an unsupported manifest schema")
    observed_at = _time(manifest.get("observedAt"), f"{field}.observedAt")
    if observed_at != expected_observed_at:
        raise ValueError(f"{field}.observedAt does not match the row source timestamp")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{field} must list at least one archived source file")

    seen: set[str] = set()
    verified: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{field}.files[{index}] must be an object")
        source_path = _archive_file(
            root, str(entry.get("path") or ""), f"{field}.files[{index}].path"
        )
        source_hash = _hash(
            str(entry.get("sha256") or ""), f"{field}.files[{index}].sha256"
        )
        normalized_path = str(source_path)
        if normalized_path in seen:
            raise ValueError(f"{field} lists a duplicate source file")
        seen.add(normalized_path)
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != source_hash:
            raise ValueError(
                f"{field}.files[{index}] SHA-256 does not match archived content"
            )
        verified.add(("/".join(PurePosixPath(entry["path"]).parts), source_hash))
    cache[manifest_key] = verified
    return verified


def load_csv(
    path: Path | str,
    features: Sequence[str],
    *,
    archive_root: Path | str,
) -> list[TimestampedObservation]:
    required = {
        "gameId",
        "season",
        "week",
        "marketHomeMargin",
        "actualHomeMargin",
        "marketAsOf",
        "marketSourcePath",
        "marketManifestPath",
        "marketManifestSha256",
        "marketSourceSha256",
        "forecastAt",
        "kickoffAt",
        "settledAt",
        "resultSourcePath",
        "resultManifestPath",
        "resultManifestSha256",
        "resultSourceSha256",
    }
    for feature in features:
        required.update({
            feature,
            f"{feature}AsOf",
            f"{feature}SourcePath",
            f"{feature}ManifestPath",
            f"{feature}ManifestSha256",
            f"{feature}SourceSha256",
        })

    rows: list[TimestampedObservation] = []
    game_ids: set[str] = set()
    manifest_cache: dict[tuple[str, str, str], set[tuple[str, str]]] = {}
    root = Path(archive_root)
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"missing S06 column(s): {sorted(missing)}")
        for line, raw in enumerate(reader, 2):
            try:
                game_id = str(raw["gameId"]).strip()
                if not game_id:
                    raise ValueError("gameId must not be empty")
                if game_id in game_ids:
                    raise ValueError(f"duplicate gameId {game_id!r}")
                game_ids.add(game_id)

                forecast_at = _time(raw["forecastAt"], "forecastAt")
                market_as_of = _time(raw["marketAsOf"], "marketAsOf")
                kickoff_at = _time(raw["kickoffAt"], "kickoffAt")
                settled_at = _time(raw["settledAt"], "settledAt")
                feature_as_of = tuple(
                    _time(raw[f"{feature}AsOf"], f"{feature}AsOf")
                    for feature in features
                )
                market_hash = _hash(raw["marketSourceSha256"], "marketSourceSha256")
                feature_hashes = tuple(
                    _hash(
                        raw[f"{feature}ManifestSha256"],
                        f"{feature}ManifestSha256",
                    )
                    for feature in features
                )
                feature_source_hashes = tuple(
                    _hash(raw[f"{feature}SourceSha256"], f"{feature}SourceSha256")
                    for feature in features
                )
                result_hash = _hash(
                    raw["resultSourceSha256"], "resultSourceSha256"
                )
                market_manifest_hash = _hash(
                    raw["marketManifestSha256"], "marketManifestSha256"
                )
                result_manifest_hash = _hash(
                    raw["resultManifestSha256"], "resultManifestSha256"
                )
                market_source_path = raw["marketSourcePath"]
                feature_source_paths = tuple(
                    raw[f"{feature}SourcePath"] for feature in features
                )
                result_source_path = raw["resultSourcePath"]
                observation = TimestampedObservation(
                    season=int(raw["season"]),
                    week=int(raw["week"]),
                    game_id=game_id,
                    market_home_margin=_finite(
                        raw["marketHomeMargin"], "marketHomeMargin"
                    ),
                    actual_home_margin=_finite(
                        raw["actualHomeMargin"], "actualHomeMargin"
                    ),
                    features=tuple(
                        _finite(raw[feature], feature) for feature in features
                    ),
                    market_as_of=market_as_of,
                    feature_as_of=feature_as_of,
                    forecast_at=forecast_at,
                    kickoff_at=kickoff_at,
                    settled_at=settled_at,
                    market_source_path=market_source_path,
                    market_manifest_sha256=market_manifest_hash,
                    market_source_sha256=market_hash,
                    feature_source_path=feature_source_paths,
                    feature_manifest_sha256=feature_hashes,
                    feature_source_sha256=feature_source_hashes,
                    result_source_path=result_source_path,
                    result_manifest_sha256=result_manifest_hash,
                    result_source_sha256=result_hash,
                )
                if market_as_of > forecast_at:
                    raise ValueError("marketAsOf is after forecastAt")
                future_features = [
                    feature for feature, source_time in zip(features, feature_as_of)
                    if source_time > forecast_at
                ]
                if future_features:
                    raise ValueError(
                        "feature as-of timestamp is after forecastAt: "
                        + ", ".join(f"{name}AsOf" for name in future_features)
                    )
                if forecast_at >= kickoff_at:
                    raise ValueError("forecastAt must be before kickoffAt")
                if settled_at < kickoff_at:
                    raise ValueError("settledAt must not precede kickoffAt")
                market_manifest_files = _verify_manifest(
                    root,
                    raw["marketManifestPath"],
                    market_manifest_hash,
                    market_as_of,
                    "marketManifest",
                    manifest_cache,
                )
                if (
                    _archive_key(root, market_source_path, "marketSourcePath"),
                    market_hash,
                ) not in market_manifest_files:
                    raise ValueError("market source file/hash is absent from its manifest")
                for feature, source_path, source_hash, source_as_of in zip(
                    features, feature_source_paths, feature_source_hashes,
                    feature_as_of,
                ):
                    feature_manifest_files = _verify_manifest(
                        root,
                        raw[f"{feature}ManifestPath"],
                        _hash(
                            raw[f"{feature}ManifestSha256"],
                            f"{feature}ManifestSha256",
                        ),
                        source_as_of,
                        f"{feature}Manifest",
                        manifest_cache,
                    )
                    if (
                        _archive_key(root, source_path, f"{feature}SourcePath"),
                        source_hash,
                    ) not in feature_manifest_files:
                        raise ValueError(
                            f"{feature} source file/hash is absent from its manifest"
                        )
                result_manifest_files = _verify_manifest(
                    root,
                    raw["resultManifestPath"],
                    result_manifest_hash,
                    settled_at,
                    "resultManifest",
                    manifest_cache,
                )
                if (
                    _archive_key(root, result_source_path, "resultSourcePath"),
                    result_hash,
                ) not in result_manifest_files:
                    raise ValueError("result source file/hash is absent from its manifest")
                rows.append(observation)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{line}: {exc}") from None
    return sorted(rows, key=lambda row: (row.forecast_at, row.game_id))


def _mae(errors: Sequence[float]) -> float | None:
    return sum(abs(error) for error in errors) / len(errors) if errors else None


def _rmse(errors: Sequence[float]) -> float | None:
    return (
        math.sqrt(sum(error * error for error in errors) / len(errors))
        if errors else None
    )


def walk_forward(
    rows: Sequence[TimestampedObservation],
    *,
    alpha: float,
    minimum_training_rows: int,
    prospective_start_at: datetime,
    reporting_minimum_predictions: int,
    reporting_minimum_kickoff_weeks: int,
    freeze_sha256: str,
    implementation_sha256: str,
    dataset_sha256: str,
) -> dict[str, Any]:
    """Predict prospective cohorts using only labels settled before each cutoff."""
    if alpha < 0:
        raise ValueError("ridge alpha must be non-negative")
    if minimum_training_rows < 1:
        raise ValueError("minimum_training_rows must be positive")
    if prospective_start_at.tzinfo is None:
        raise ValueError("prospective_start_at must include a timezone")
    if reporting_minimum_predictions < 1 or reporting_minimum_kickoff_weeks < 1:
        raise ValueError("reporting minima must be positive")

    start = prospective_start_at.astimezone(timezone.utc)
    predictions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    cohorts: dict[datetime, list[TimestampedObservation]] = {}
    for row in rows:
        if row.forecast_at >= start:
            cohorts.setdefault(row.forecast_at, []).append(row)

    for forecast_at in sorted(cohorts):
        test = cohorts[forecast_at]
        train = [row for row in rows if row.settled_at < forecast_at]
        if len(train) < minimum_training_rows:
            skipped.append({
                "forecastAt": forecast_at.isoformat(),
                "reason": "INSUFFICIENT_PRIOR_SETTLED_ROWS",
                "trainingRows": len(train),
                "testRows": len(test),
            })
            continue

        model = fit(
            [
                Observation(
                    season=row.season,
                    week=row.week,
                    game_id=row.game_id,
                    market_home_margin=row.market_home_margin,
                    actual_home_margin=row.actual_home_margin,
                    features=row.features,
                )
                for row in train
            ],
            alpha=alpha,
        )
        trained_through = max(row.settled_at for row in train)
        if trained_through >= forecast_at:
            raise AssertionError("walk-forward training crossed forecast cutoff")
        for row in test:
            challenger_margin = model.predict_margin(
                row.market_home_margin, row.features
            )
            predictions.append({
                "gameId": row.game_id,
                "season": row.season,
                "week": row.week,
                "forecastAt": row.forecast_at.isoformat(),
                "marketAsOf": row.market_as_of.isoformat(),
                "featureAsOf": [
                    value.isoformat() for value in row.feature_as_of
                ],
                "kickoffAt": row.kickoff_at.isoformat(),
                "settledAt": row.settled_at.isoformat(),
                "trainedRows": len(train),
                "trainedThroughAt": trained_through.isoformat(),
                "marketHomeMargin": row.market_home_margin,
                "challengerHomeMargin": challenger_margin,
                "actualHomeMargin": row.actual_home_margin,
                "marketSourcePath": row.market_source_path,
                "marketSourceSha256": row.market_source_sha256,
                "marketManifestSha256": row.market_manifest_sha256,
                "featureSourcePath": list(row.feature_source_path),
                "featureManifestSha256": list(row.feature_manifest_sha256),
                "featureSourceSha256": list(row.feature_source_sha256),
                "resultSourcePath": row.result_source_path,
                "resultManifestSha256": row.result_manifest_sha256,
                "resultSourceSha256": row.result_source_sha256,
            })

    market_errors = [
        item["marketHomeMargin"] - item["actualHomeMargin"]
        for item in predictions
    ]
    challenger_errors = [
        item["challengerHomeMargin"] - item["actualHomeMargin"]
        for item in predictions
    ]
    weeks = sorted({(item["season"], item["week"]) for item in predictions})
    week_metrics = []
    for season, week in weeks:
        cohort = [
            item for item in predictions
            if (item["season"], item["week"]) == (season, week)
        ]
        market_cohort_errors = [
            item["marketHomeMargin"] - item["actualHomeMargin"]
            for item in cohort
        ]
        challenger_cohort_errors = [
            item["challengerHomeMargin"] - item["actualHomeMargin"]
            for item in cohort
        ]
        market_week_mae = _mae(market_cohort_errors)
        challenger_week_mae = _mae(challenger_cohort_errors)
        market_week_rmse = _rmse(market_cohort_errors)
        challenger_week_rmse = _rmse(challenger_cohort_errors)
        week_metrics.append({
            "season": season,
            "week": week,
            "predictionCount": len(cohort),
            "marketMae": market_week_mae,
            "challengerMae": challenger_week_mae,
            "maeImprovement": (
                market_week_mae - challenger_week_mae
                if market_week_mae is not None and challenger_week_mae is not None
                else None
            ),
            "marketRmse": market_week_rmse,
            "challengerRmse": challenger_week_rmse,
            "rmseImprovement": (
                market_week_rmse - challenger_week_rmse
                if market_week_rmse is not None and challenger_week_rmse is not None
                else None
            ),
        })
    enough_evidence = (
        len(predictions) >= reporting_minimum_predictions
        and len(weeks) >= reporting_minimum_kickoff_weeks
    )
    status = (
        "WALK_FORWARD_EVIDENCE_COMPLETE_REVIEW_REQUIRED"
        if enough_evidence else
        "INSUFFICIENT_WALK_FORWARD_EVIDENCE"
        if predictions else
        "INSUFFICIENT_PRIOR_SETTLED_TIMESTAMPED_DATA"
        if skipped else
        "NO_PROSPECTIVE_FORECASTS"
    )
    market_mae = _mae(market_errors)
    challenger_mae = _mae(challenger_errors)
    market_rmse = _rmse(market_errors)
    challenger_rmse = _rmse(challenger_errors)

    return {
        "schemaVersion": 1,
        "contract": "CFB_EDGE_S06_MR1_WALK_FORWARD_V1",
        "modelId": "S06_MR1",
        "version": "pit-market-residual-ridge-1",
        "status": status,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "prospectiveStartAt": start.isoformat(),
        "freezeSha256": freeze_sha256,
        "implementationSha256": implementation_sha256,
        "datasetSha256": dataset_sha256,
        "predictionCount": len(predictions),
        "kickoffWeeks": len(weeks),
        "reportingMinimumPredictions": reporting_minimum_predictions,
        "reportingMinimumKickoffWeeks": reporting_minimum_kickoff_weeks,
        "marketMae": market_mae,
        "challengerMae": challenger_mae,
        "maeImprovement": (
            market_mae - challenger_mae
            if market_mae is not None and challenger_mae is not None else None
        ),
        "marketRmse": market_rmse,
        "challengerRmse": challenger_rmse,
        "rmseImprovement": (
            market_rmse - challenger_rmse
            if market_rmse is not None and challenger_rmse is not None else None
        ),
        "weekMetrics": week_metrics,
        "predictions": predictions,
        "skippedCohorts": skipped,
        "deliveryEligible": False,
        "promotionEffect": "NONE",
        "stakeUnits": 0,
        "probabilityCalibration": "NOT_ESTABLISHED",
        "realizedRoi": None,
        "clv": None,
        "interpretation": (
            "Margin accuracy versus the market is diagnostic evidence only. "
            "It does not establish cover probabilities, executable edge, "
            "profitability, or delivery eligibility."
        ),
    }


def run(
    data_path: Path,
    config_path: Path,
    output_path: Path,
    archive_root: Path,
) -> dict[str, Any]:
    config_bytes = config_path.read_bytes()
    config = json.loads(config_bytes)
    if (
        config.get("modelId") != "S06_MR1"
        or config.get("freezeId") != "S06_MR1_PIT_FREEZE_V1"
        or config.get("status") != "FROZEN_UNTRAINED"
        or config.get("deliveryEligible") is not False
        or config.get("promotionEffect") != "NONE"
        or config.get("stakeUnits") != 0
    ):
        raise ValueError("S06 freeze identity or research-only controls changed")

    features = list(config["features"])
    rows = load_csv(data_path, features, archive_root=archive_root)
    report = walk_forward(
        rows,
        alpha=float(config["ridgeAlpha"]),
        minimum_training_rows=int(config["minimumTrainingRows"]),
        prospective_start_at=_time(
            config["prospectiveStartAt"], "prospectiveStartAt"
        ),
        reporting_minimum_predictions=int(
            config["reportingMinimumPredictions"]
        ),
        reporting_minimum_kickoff_weeks=int(
            config["reportingMinimumKickoffWeeks"]
        ),
        freeze_sha256=hashlib.sha256(config_bytes).hexdigest(),
        implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        dataset_sha256=hashlib.sha256(data_path.read_bytes()).hexdigest(),
    )
    report["features"] = features
    report["ridgeAlphaStatus"] = config["ridgeAlphaStatus"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"{report['status']} | S06_MR1 predictions "
        f"{report['predictionCount']} / {report['reportingMinimumPredictions']} "
        f"| kickoff weeks {report['kickoffWeeks']} / "
        f"{report['reportingMinimumKickoffWeeks']} | delivery disabled"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument(
        "--config", type=Path, default=Path("config/s06_market_relative.json")
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--archive-root", required=True, type=Path)
    args = parser.parse_args(argv)
    run(args.data, args.config, args.out, args.archive_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
