"""Chronology-safe Bayesian/regime market-residual challenger.

The market is always the baseline. This module asks whether small, explicitly
registered corrections improve an untouched future cohort. It is research only:
no output from this module has delivery or staking authority.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

MODEL_ID = "S04_BR1"
CONTRACT = "CFB_EDGE_BAYESIAN_REGIME_RESIDUAL_V1"
REGIME_EDGES = (4.0, 8.0, 12.0)
DEFAULT_PRIOR_PRECISION_GRID = (1.0, 5.0, 10.0, 25.0, 50.0, 100.0, 250.0)
DEFAULT_TEAM_GAIN_GRID = (0.05, 0.10, 0.20, 0.30, 0.40, 0.50)
DEFAULT_ENSEMBLE_WEIGHT_GRID = (0.0, 0.25, 0.50, 0.75, 1.0)
EPS = 1e-12


@dataclass(frozen=True)
class Observation:
    season: int
    week: int
    game_id: str
    game: str
    market_home_margin: float
    actual_home_margin: float
    projection_gap: float
    close_market_margin: float | None = None

    @property
    def residual(self) -> float:
        return self.actual_home_margin - self.market_home_margin

    @property
    def movement(self) -> float | None:
        if self.close_market_margin is None:
            return None
        return self.close_market_margin - self.market_home_margin


@dataclass(frozen=True)
class BayesianSlope:
    mean: float
    variance: float
    prior_precision: float
    n: int


def _noise_variance(rows: Sequence[Observation], target: str) -> float:
    values = [float(getattr(r, target)) for r in rows if getattr(r, target) is not None]
    if not values:
        return 1.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)
    return max(variance, 1.0)


def fit_slope(
    rows: Sequence[Observation],
    *,
    prior_precision: float,
    target: str = "residual",
) -> BayesianSlope:
    """Normal-prior posterior for y = beta*x, centered on market beta=0."""
    if prior_precision <= 0:
        raise ValueError("prior_precision must be positive")
    pairs = [
        (r.projection_gap, getattr(r, target))
        for r in rows
        if getattr(r, target) is not None
    ]
    if not pairs:
        return BayesianSlope(0.0, 1.0 / prior_precision, prior_precision, 0)
    noise = _noise_variance(rows, target)
    sum_xx = sum(x * x for x, _ in pairs)
    sum_xy = sum(x * float(y) for x, y in pairs)
    precision = prior_precision + sum_xx / noise
    variance = 1.0 / precision
    mean = variance * (sum_xy / noise)
    return BayesianSlope(mean, variance, prior_precision, len(pairs))


def regime_name(gap: float, edges: Sequence[float] = REGIME_EDGES) -> str:
    a = abs(float(gap))
    if a < edges[0]:
        return "0-4"
    if a < edges[1]:
        return "4-8"
    if a < edges[2]:
        return "8-12"
    return "12+"


def fit_regimes(
    rows: Sequence[Observation],
    *,
    prior_precision: float,
    target: str = "residual",
) -> dict[str, BayesianSlope]:
    names = ("0-4", "4-8", "8-12", "12+")
    return {
        name: fit_slope(
            [r for r in rows if regime_name(r.projection_gap) == name],
            prior_precision=prior_precision,
            target=target,
        )
        for name in names
    }


def slope_corrections(
    model: BayesianSlope, rows: Sequence[Observation]
) -> list[float]:
    return [model.mean * r.projection_gap for r in rows]


def regime_corrections(
    models: Mapping[str, BayesianSlope], rows: Sequence[Observation]
) -> list[float]:
    return [
        models[regime_name(r.projection_gap)].mean * r.projection_gap
        for r in rows
    ]


def _teams(game: str) -> tuple[str, str] | None:
    if " @ " not in game:
        return None
    away, home = (x.strip() for x in game.split(" @ ", 1))
    return (away, home) if away and home else None


def team_state_corrections(
    history: Sequence[Observation],
    test: Sequence[Observation],
    *,
    gain: float,
) -> list[float]:
    """Kalman-like team residual state, updated only after completed results."""
    if not 0.0 <= gain <= 1.0:
        raise ValueError("gain must be in [0, 1]")
    states: dict[str, float] = {}
    for row in sorted(history, key=lambda r: (r.season, r.week, r.game_id)):
        parsed = _teams(row.game)
        if parsed is None:
            continue
        away, home = parsed
        current = states.get(home, 0.0) - states.get(away, 0.0)
        innovation = row.residual - current
        states[home] = states.get(home, 0.0) + gain * innovation / 2.0
        states[away] = states.get(away, 0.0) - gain * innovation / 2.0

    corrections = []
    for row in test:
        parsed = _teams(row.game)
        if parsed is None:
            corrections.append(0.0)
            continue
        away, home = parsed
        corrections.append(states.get(home, 0.0) - states.get(away, 0.0))
    return corrections


def _metrics(
    rows: Sequence[Observation],
    corrections: Sequence[float],
    *,
    target: str = "actual",
) -> dict[str, Any]:
    if len(rows) != len(corrections):
        raise ValueError("prediction length mismatch")
    errors: list[float] = []
    hits = 0
    directional_n = 0
    for row, correction in zip(rows, corrections):
        if target == "actual":
            truth = row.actual_home_margin
            baseline = row.market_home_margin
            residual = row.residual
        elif target == "movement":
            if row.movement is None:
                continue
            truth = row.movement
            baseline = 0.0
            residual = row.movement
        else:
            raise ValueError("unknown target")
        prediction = baseline + float(correction)
        errors.append(prediction - truth)
        if abs(correction) > EPS and abs(float(residual)) > EPS:
            directional_n += 1
            if (correction > 0) == (float(residual) > 0):
                hits += 1
    if not errors:
        return {
            "n": 0,
            "mae": None,
            "rmse": None,
            "directionalAccuracy": None,
            "directionalN": 0,
        }
    return {
        "n": len(errors),
        "mae": sum(abs(e) for e in errors) / len(errors),
        "rmse": math.sqrt(sum(e * e for e in errors) / len(errors)),
        "directionalAccuracy": hits / directional_n if directional_n else None,
        "directionalN": directional_n,
    }


def _select(
    items: Sequence[float], score
) -> tuple[float, list[dict[str, float]]]:
    scored = [{"value": float(v), "mae": float(score(float(v)))} for v in items]
    scored.sort(key=lambda x: (x["mae"], x["value"]))
    return scored[0]["value"], scored


def _verdict(
    candidate: Mapping[str, Any], baseline: Mapping[str, Any]
) -> str:
    if candidate.get("mae") is None or candidate.get("rmse") is None:
        return "INSUFFICIENT_EVIDENCE"
    if (
        candidate["mae"] < baseline["mae"]
        and candidate["rmse"] < baseline["rmse"]
    ):
        return "IMPROVED_OOS"
    return "REJECTED_OOS"


def staged_holdout(
    rows: Sequence[Observation],
    *,
    prior_precision_grid: Sequence[float] = DEFAULT_PRIOR_PRECISION_GRID,
    team_gain_grid: Sequence[float] = DEFAULT_TEAM_GAIN_GRID,
    ensemble_weight_grid: Sequence[float] = DEFAULT_ENSEMBLE_WEIGHT_GRID,
) -> dict[str, Any]:
    """Week 1 fit, Week 2 selection, Week 3 untouched final holdout."""
    w1 = [r for r in rows if r.week == 1]
    w2 = [r for r in rows if r.week == 2]
    w3 = [r for r in rows if r.week == 3]
    if not w1 or not w2 or not w3:
        raise ValueError("staged holdout requires non-empty Weeks 1, 2 and 3")

    baseline = _metrics(w3, [0.0] * len(w3))

    def global_score(alpha: float) -> float:
        model = fit_slope(w1, prior_precision=alpha)
        return _metrics(w2, slope_corrections(model, w2))["mae"]

    global_alpha, global_selection = _select(
        prior_precision_grid, global_score
    )
    global_model = fit_slope(w1 + w2, prior_precision=global_alpha)
    global_corr = slope_corrections(global_model, w3)
    global_metrics = _metrics(w3, global_corr)

    def regime_score(alpha: float) -> float:
        model = fit_regimes(w1, prior_precision=alpha)
        return _metrics(w2, regime_corrections(model, w2))["mae"]

    regime_alpha, regime_selection = _select(
        prior_precision_grid, regime_score
    )
    regime_model = fit_regimes(w1 + w2, prior_precision=regime_alpha)
    regime_corr = regime_corrections(regime_model, w3)
    regime_metrics = _metrics(w3, regime_corr)

    def team_score(gain: float) -> float:
        return _metrics(
            w2, team_state_corrections(w1, w2, gain=gain)
        )["mae"]

    team_gain, team_selection = _select(team_gain_grid, team_score)
    team_corr = team_state_corrections(w1 + w2, w3, gain=team_gain)
    team_metrics = _metrics(w3, team_corr)

    global_w1 = fit_slope(w1, prior_precision=global_alpha)
    global_w2_corr = slope_corrections(global_w1, w2)
    team_w2_corr = team_state_corrections(w1, w2, gain=team_gain)

    def ensemble_score(weight: float) -> float:
        correction = [
            weight * g + (1.0 - weight) * t
            for g, t in zip(global_w2_corr, team_w2_corr)
        ]
        return _metrics(w2, correction)["mae"]

    ensemble_weight, ensemble_selection = _select(
        ensemble_weight_grid, ensemble_score
    )
    ensemble_corr = [
        ensemble_weight * g + (1.0 - ensemble_weight) * t
        for g, t in zip(global_corr, team_corr)
    ]
    ensemble_metrics = _metrics(w3, ensemble_corr)

    movement_baseline = _metrics(
        w3, [0.0] * len(w3), target="movement"
    )
    move_w1 = [r for r in w1 if r.movement is not None]
    move_w2 = [r for r in w2 if r.movement is not None]

    def move_global_score(alpha: float) -> float:
        model = fit_slope(
            move_w1, prior_precision=alpha, target="movement"
        )
        return _metrics(
            move_w2,
            slope_corrections(model, move_w2),
            target="movement",
        )["mae"]

    move_alpha, move_selection = _select(
        prior_precision_grid, move_global_score
    )
    move_model = fit_slope(
        move_w1 + move_w2,
        prior_precision=move_alpha,
        target="movement",
    )
    move_metrics = _metrics(
        w3, slope_corrections(move_model, w3), target="movement"
    )

    def move_regime_score(alpha: float) -> float:
        model = fit_regimes(
            move_w1, prior_precision=alpha, target="movement"
        )
        return _metrics(
            move_w2,
            regime_corrections(model, move_w2),
            target="movement",
        )["mae"]

    move_regime_alpha, move_regime_selection = _select(
        prior_precision_grid, move_regime_score
    )
    move_regime_model = fit_regimes(
        move_w1 + move_w2,
        prior_precision=move_regime_alpha,
        target="movement",
    )
    move_regime_metrics = _metrics(
        w3,
        regime_corrections(move_regime_model, w3),
        target="movement",
    )

    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "modelId": MODEL_ID,
        "status": "SHADOW_ONLY",
        "promotionEffect": "NONE",
        "chronology": {
            "fitWeek": 1,
            "selectionWeek": 2,
            "untouchedHoldoutWeek": 3,
        },
        "counts": {
            "week1": len(w1),
            "week2": len(w2),
            "week3": len(w3),
        },
        "outcomeResidual": {
            "marketBaseline": baseline,
            "globalBayesian": {
                **global_metrics,
                "priorPrecision": global_alpha,
                "posteriorMeanSlope": global_model.mean,
                "posteriorVariance": global_model.variance,
                "selection": global_selection,
                "verdict": _verdict(global_metrics, baseline),
            },
            "regimeBayesian": {
                **regime_metrics,
                "priorPrecision": regime_alpha,
                "posteriorMeanSlopes": {
                    k: v.mean for k, v in regime_model.items()
                },
                "selection": regime_selection,
                "verdict": _verdict(regime_metrics, baseline),
            },
            "dynamicTeamState": {
                **team_metrics,
                "gain": team_gain,
                "selection": team_selection,
                "verdict": _verdict(team_metrics, baseline),
            },
            "ensemble": {
                **ensemble_metrics,
                "globalWeight": ensemble_weight,
                "selection": ensemble_selection,
                "verdict": _verdict(ensemble_metrics, baseline),
            },
        },
        "marketMovement": {
            "noMoveBaseline": movement_baseline,
            "globalBayesian": {
                **move_metrics,
                "priorPrecision": move_alpha,
                "posteriorMeanSlope": move_model.mean,
                "selection": move_selection,
                "verdict": _verdict(move_metrics, movement_baseline),
            },
            "regimeBayesian": {
                **move_regime_metrics,
                "priorPrecision": move_regime_alpha,
                "posteriorMeanSlopes": {
                    k: v.mean for k, v in move_regime_model.items()
                },
                "selection": move_regime_selection,
                "verdict": _verdict(
                    move_regime_metrics, movement_baseline
                ),
            },
        },
        "interpretation": (
            "Historical staged holdout can reject components but cannot "
            "promote them. Week 3 remains a one-time pseudo-holdout and "
            "Week 5 S04_ES2 remains untouched."
        ),
    }


def load_learning_report(path: Path | str) -> list[Observation]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    observations = []
    for n, row in enumerate(raw.get("rows") or [], 1):
        try:
            observations.append(
                Observation(
                    season=int(row["season"]),
                    week=int(row["week"]),
                    game_id=str(row.get("gameId") or row.get("game") or n),
                    game=str(row.get("game") or ""),
                    market_home_margin=float(row["open_market_margin"]),
                    actual_home_margin=float(row["realized_home_margin"]),
                    projection_gap=float(row["projection_gap_vs_open"]),
                    close_market_margin=(
                        None
                        if row.get("close_market_margin") is None
                        else float(row["close_market_margin"])
                    ),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid learning row {n}: {exc}"
            ) from None
    return observations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cfb_edge.bayesian_regime", description=__doc__
    )
    parser.add_argument("--learning-report", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    report = staged_holdout(load_learning_report(args.learning_report))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    baseline = report["outcomeResidual"]["marketBaseline"]
    print(
        f"{MODEL_ID} shadow: n={baseline['n']} "
        f"market MAE={baseline['mae']:.4f}"
    )
    for name in (
        "globalBayesian",
        "regimeBayesian",
        "dynamicTeamState",
        "ensemble",
    ):
        row = report["outcomeResidual"][name]
        print(
            f"  {name}: MAE={row['mae']:.4f} "
            f"RMSE={row['rmse']:.4f} {row['verdict']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
