"""Walk-forward harness for the S04 football-context challenger.

S04 is a residual model: the market supplies the baseline home margin and the
model may only try to explain what is left. Historical performance can reject
the challenger, but it cannot grant delivery authority. That requires a
separate prospective validation cohort.

The implementation is dependency-free so it runs under the repository's
standard-library-only CI policy.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

EPS = 1e-12


@dataclass(frozen=True)
class Observation:
    season: int
    week: int
    game_id: str
    market_home_margin: float
    actual_home_margin: float
    features: tuple[float, ...]


@dataclass(frozen=True)
class RidgeResidualModel:
    means: tuple[float, ...]
    scales: tuple[float, ...]
    beta: tuple[float, ...]

    def predict_residual(self, features: Sequence[float]) -> float:
        if len(features) != len(self.means):
            raise ValueError("feature count does not match fitted model")
        x = [1.0]
        for value, mean, scale in zip(features, self.means, self.scales):
            x.append((float(value) - mean) / scale)
        return sum(b * v for b, v in zip(self.beta, x))

    def predict_margin(self, market_home_margin: float,
                       features: Sequence[float]) -> float:
        return float(market_home_margin) + self.predict_residual(features)


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Solve a square linear system with partial pivoting."""
    n = len(b)
    aug = [list(row) + [float(rhs)] for row, rhs in zip(a, b)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < EPS:
            raise ValueError("singular ridge system")
        if pivot != col:
            aug[col], aug[pivot] = aug[pivot], aug[col]
        divisor = aug[col][col]
        aug[col] = [v / divisor for v in aug[col]]
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            if abs(factor) < EPS:
                continue
            aug[row] = [
                v - factor * p for v, p in zip(aug[row], aug[col])
            ]
    return [aug[i][-1] for i in range(n)]


def fit(rows: Sequence[Observation], *, alpha: float) -> RidgeResidualModel:
    if not rows:
        raise ValueError("cannot fit S04 without training rows")
    if alpha < 0:
        raise ValueError("ridge alpha must be non-negative")
    width = len(rows[0].features)
    if width == 0:
        raise ValueError("S04 requires at least one registered feature")
    if any(len(r.features) != width for r in rows):
        raise ValueError("inconsistent feature width")

    means = []
    scales = []
    for j in range(width):
        values = [r.features[j] for r in rows]
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        scale = math.sqrt(variance)
        means.append(mean)
        scales.append(scale if scale > EPS else 1.0)

    x_rows: list[list[float]] = []
    y: list[float] = []
    for row in rows:
        x = [1.0]
        x.extend(
            (value - mean) / scale
            for value, mean, scale in zip(row.features, means, scales)
        )
        x_rows.append(x)
        y.append(row.actual_home_margin - row.market_home_margin)

    p = width + 1
    xtx = [[0.0 for _ in range(p)] for _ in range(p)]
    xty = [0.0 for _ in range(p)]
    for x, target in zip(x_rows, y):
        for i in range(p):
            xty[i] += x[i] * target
            for j in range(p):
                xtx[i][j] += x[i] * x[j]

    # Never penalize the intercept. The challenger is allowed to learn a
    # persistent market bias, but that bias must be estimated from prior seasons.
    for i in range(1, p):
        xtx[i][i] += alpha

    beta = _solve(xtx, xty)
    return RidgeResidualModel(
        means=tuple(means), scales=tuple(scales), beta=tuple(beta)
    )


def _mae(errors: Sequence[float]) -> float | None:
    return sum(abs(x) for x in errors) / len(errors) if errors else None


def _rmse(errors: Sequence[float]) -> float | None:
    return math.sqrt(sum(x * x for x in errors) / len(errors)) if errors else None


def walk_forward(
    rows: Sequence[Observation],
    *,
    alpha: float,
    minimum_train_rows: int,
    minimum_test_rows_per_season: int,
) -> dict[str, Any]:
    """Evaluate S04 chronologically with no future-season training rows."""
    seasons = sorted({r.season for r in rows})
    predictions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for season in seasons:
        train = [r for r in rows if r.season < season]
        test = [r for r in rows if r.season == season]
        if len(train) < minimum_train_rows:
            skipped.append({
                "season": season, "reason": "INSUFFICIENT_TRAIN",
                "trainRows": len(train), "testRows": len(test),
            })
            continue
        if len(test) < minimum_test_rows_per_season:
            skipped.append({
                "season": season, "reason": "INSUFFICIENT_TEST",
                "trainRows": len(train), "testRows": len(test),
            })
            continue

        model = fit(train, alpha=alpha)
        for row in test:
            forecast = model.predict_margin(row.market_home_margin, row.features)
            predictions.append({
                "season": row.season,
                "week": row.week,
                "gameId": row.game_id,
                "marketHomeMargin": row.market_home_margin,
                "challengerHomeMargin": forecast,
                "actualHomeMargin": row.actual_home_margin,
                "trainedThroughSeason": season - 1,
            })

    market_errors = [
        p["marketHomeMargin"] - p["actualHomeMargin"] for p in predictions
    ]
    challenger_errors = [
        p["challengerHomeMargin"] - p["actualHomeMargin"] for p in predictions
    ]
    market_mae = _mae(market_errors)
    challenger_mae = _mae(challenger_errors)
    market_rmse = _rmse(market_errors)
    challenger_rmse = _rmse(challenger_errors)

    return {
        "modelId": "S04",
        "status": "SHADOW_ONLY",
        "n": len(predictions),
        "testedSeasons": sorted({p["season"] for p in predictions}),
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
        "predictions": predictions,
        "skippedSeasons": skipped,
        "promotionEffect": "NONE",
        "interpretation": (
            "Historical walk-forward evidence can reject this challenger but "
            "cannot authorize delivery. Prospective shadow validation is required."
        ),
    }


def load_csv(path: Path | str, features: Sequence[str]) -> list[Observation]:
    required = {
        "season", "week", "gameId", "marketHomeMargin", "actualHomeMargin",
        *features,
    }
    rows: list[Observation] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing challenger column(s): {sorted(missing)}")
        for n, raw in enumerate(reader, 2):
            try:
                rows.append(Observation(
                    season=int(raw["season"]),
                    week=int(raw["week"]),
                    game_id=str(raw["gameId"]),
                    market_home_margin=float(raw["marketHomeMargin"]),
                    actual_home_margin=float(raw["actualHomeMargin"]),
                    features=tuple(float(raw[name]) for name in features),
                ))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{n}: invalid challenger row: {exc}") from None
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cfb_edge.challenger", description=__doc__)
    p.add_argument("--data", required=True)
    p.add_argument("--config", default="config/s04_challenger.json")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if cfg.get("modelId") != "S04" or cfg.get("status") != "SHADOW_ONLY":
        raise SystemExit("S04 config must remain SHADOW_ONLY")
    features = list(cfg.get("features") or [])
    rows = load_csv(args.data, features)
    report = walk_forward(
        rows,
        alpha=float(cfg.get("ridgeAlpha", 10.0)),
        minimum_train_rows=int(cfg.get("minimumTrainRows", 500)),
        minimum_test_rows_per_season=int(
            cfg.get("minimumTestRowsPerSeason", 100)
        ),
    )
    report["features"] = features
    report["configStatus"] = cfg.get("featureStatus")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    if report["n"]:
        print(
            f"S04 shadow: n={report['n']} "
            f"MAE lift={report['maeImprovement']:.4f}"
        )
    else:
        print("S04 shadow: no eligible walk-forward cohort")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
