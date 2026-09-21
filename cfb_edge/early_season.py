"""Early-season learning without contaminating the forward card.

Weeks 1-2 are training evidence. Week 3 is a single pseudo-holdout. Week 4 is
never used to fit anything in this module.

The market is the baseline. The only learned feature is the pregame gap between
our projection and the opening market-implied home margin. We ask two narrow
questions:

1. Did that gap improve the eventual realized-margin estimate?
2. Did the market subsequently move in the direction of that gap?

CFBD open/close lines are reference evidence, not executable entry prices. This
module can qualify a challenger for Week-4 SHADOW ranking. It cannot grant paper
or live delivery authority.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .slate import SlateRow, build_from_rows
from .teams import match_games

CONTRACT = "CFB_EDGE_EARLY_SEASON_LEARNING_V1"
SHADOW_CONTRACT = "CFB_EDGE_WEEK4_SHADOW_V1"

TRAIN_WEEKS = (1, 2)
PSEUDO_HOLDOUT_WEEK = 3
PROSPECTIVE_WEEK = 4
MIN_TRAIN_GAMES = 30
MIN_HOLDOUT_GAMES = 20
MAX_EARLY_WEIGHT = 0.25
MIN_SHADOW_GAP = 4.0


@dataclass(frozen=True)
class LearningRow:
    season: int
    week: int
    cohort: str
    game: str
    kickoff: str
    projected_home_margin: float
    open_home_line: float
    close_home_line: float
    open_market_margin: float
    close_market_margin: float
    realized_home_margin: float
    projection_gap_vs_open: float
    open_to_close_home_clv: float
    directional_clv: float
    providers: int


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _median(values: Iterable[float]) -> float | None:
    rows = list(values)
    return float(statistics.median(rows)) if rows else None


def _sha(path: Path | str) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def load_schedule(path: Path | str) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_lines(path: Path | str) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: CFBD lines payload must be a list")
    return raw


def _result_map(schedule_rows: Sequence[Mapping[str, str]], week: int) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in schedule_rows:
        try:
            if int(float(row.get("week") or "")) != week:
                continue
        except (TypeError, ValueError):
            continue
        if (row.get("home_division") or "").lower() != "fbs":
            continue
        if (row.get("away_division") or "").lower() != "fbs":
            continue
        home = (row.get("home_team") or "").strip()
        away = (row.get("away_team") or "").strip()
        hp = _number(row.get("home_points"))
        ap = _number(row.get("away_points"))
        if not home or not away or hp is None or ap is None:
            continue
        game = f"{away} @ {home}"
        out[game] = {
            "realized": hp - ap,
            "kickoff": row.get("start_date") or "",
        }
    return out


def _cfbd_consensus(games: Sequence[Mapping[str, Any]]) -> dict[str, dict]:
    """Median open and stored final spread across providers.

    CFBD spread/spreadOpen are reference lines. They have no timestamp proving
    an executable fill, so this function never labels them capture/fill.
    """
    by_game: dict[str, dict[str, list[float] | set[str]]] = {}
    for game in games:
        away = str(game.get("awayTeam") or "").strip()
        home = str(game.get("homeTeam") or "").strip()
        if not away or not home:
            continue
        key = f"{away} @ {home}"
        slot = by_game.setdefault(key, {"open": [], "close": [], "providers": set()})
        for line in game.get("lines") or []:
            op = _number(line.get("spreadOpen"))
            cl = _number(line.get("spread"))
            if op is None or cl is None:
                continue
            slot["open"].append(op)  # type: ignore[union-attr]
            slot["close"].append(cl)  # type: ignore[union-attr]
            provider = str(line.get("provider") or "").strip()
            if provider:
                slot["providers"].add(provider)  # type: ignore[union-attr]

    out: dict[str, dict] = {}
    for game, slot in by_game.items():
        op = _median(slot["open"])  # type: ignore[arg-type]
        cl = _median(slot["close"])  # type: ignore[arg-type]
        if op is None or cl is None:
            continue
        out[game] = {
            "open": op,
            "close": cl,
            "providers": len(slot["providers"]),
        }
    return out


def build_learning_rows(
    *,
    season: int,
    prior_schedule_rows: list[dict],
    schedule_rows: list[dict],
    lines_by_week: Mapping[int, Sequence[Mapping[str, Any]]],
) -> tuple[list[LearningRow], dict[str, Any]]:
    rows: list[LearningRow] = []
    reconciliation: dict[str, Any] = {}

    for week in (*TRAIN_WEEKS, PSEUDO_HOLDOUT_WEEK):
        projections = build_from_rows(
            season,
            week,
            prior_rows=prior_schedule_rows,
            rows=schedule_rows,
        )
        by_projection = {r.game: r for r in projections}
        known_games = list(by_projection)
        result_map = _result_map(schedule_rows, week)
        foreign = _cfbd_consensus(lines_by_week.get(week, ()))
        report = match_games(list(foreign), known_games)
        reconciliation[str(week)] = {
            "matched": len(report.matched),
            "unmatched": list(report.unmatched),
            "rate": report.rate,
        }

        for foreign_game, canonical in report.matched.items():
            proj = by_projection.get(canonical)
            result = result_map.get(canonical)
            market = foreign.get(foreign_game)
            if proj is None or result is None or market is None:
                continue
            open_line = float(market["open"])
            close_line = float(market["close"])
            open_margin = -open_line
            close_margin = -close_line
            gap = proj.projected_margin - open_margin
            # Positive means a home-at-open ticket beat the close.
            home_clv = open_line - close_line
            sign = 1.0 if gap > 0 else -1.0 if gap < 0 else 0.0
            rows.append(LearningRow(
                season=season,
                week=week,
                cohort="TRAIN" if week in TRAIN_WEEKS else "PSEUDO_HOLDOUT",
                game=canonical,
                kickoff=str(result["kickoff"]),
                projected_home_margin=float(proj.projected_margin),
                open_home_line=open_line,
                close_home_line=close_line,
                open_market_margin=open_margin,
                close_market_margin=close_margin,
                realized_home_margin=float(result["realized"]),
                projection_gap_vs_open=gap,
                open_to_close_home_clv=home_clv,
                directional_clv=sign * home_clv,
                providers=int(market["providers"]),
            ))

    rows.sort(key=lambda r: (r.week, r.kickoff, r.game))
    return rows, reconciliation


def _slope(
    rows: Sequence[LearningRow],
    *,
    target,
) -> float | None:
    denom = sum(r.projection_gap_vs_open ** 2 for r in rows)
    if denom <= 1e-12:
        return None
    return sum(r.projection_gap_vs_open * target(r) for r in rows) / denom


def _clip_weight(value: float | None) -> float:
    if value is None or not math.isfinite(value):
        return 0.0
    return max(0.0, min(MAX_EARLY_WEIGHT, value))


def _mae(values: Iterable[float]) -> float | None:
    rows = [abs(x) for x in values]
    return sum(rows) / len(rows) if rows else None


def _bucket(row: LearningRow) -> str:
    gap = abs(row.projection_gap_vs_open)
    if gap < 4:
        return "0-4"
    if gap < 8:
        return "4-8"
    if gap < 12:
        return "8-12"
    return "12+"


def _bucket_stats(rows: Sequence[LearningRow]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in ("0-4", "4-8", "8-12", "12+"):
        group = [r for r in rows if _bucket(r) == name]
        if not group:
            continue
        out.append({
            "gapBucket": name,
            "n": len(group),
            "meanDirectionalClv": sum(r.directional_clv for r in group) / len(group),
            "beatCloseRate": sum(r.directional_clv > 0 for r in group) / len(group),
            "meanAbsoluteGap": sum(abs(r.projection_gap_vs_open) for r in group) / len(group),
        })
    return out


def summarize(rows: Sequence[LearningRow]) -> dict[str, Any]:
    train = [r for r in rows if r.cohort == "TRAIN"]
    holdout = [r for r in rows if r.cohort == "PSEUDO_HOLDOUT"]

    outcome_slope = _slope(
        train,
        target=lambda r: r.realized_home_margin - r.open_market_margin,
    )
    movement_slope = _slope(
        train,
        target=lambda r: r.close_market_margin - r.open_market_margin,
    )
    outcome_weight = _clip_weight(outcome_slope)
    movement_weight = _clip_weight(movement_slope)

    open_mae = _mae(
        r.open_market_margin - r.realized_home_margin for r in holdout
    )
    adjusted_mae = _mae(
        (
            r.open_market_margin
            + outcome_weight * r.projection_gap_vs_open
            - r.realized_home_margin
        )
        for r in holdout
    )
    close_mae = _mae(
        r.close_market_margin - r.realized_home_margin for r in holdout
    )
    mean_clv = (
        sum(r.directional_clv for r in holdout) / len(holdout)
        if holdout else None
    )
    beat_rate = (
        sum(r.directional_clv > 0 for r in holdout) / len(holdout)
        if holdout else None
    )
    mae_improvement = (
        open_mae - adjusted_mae
        if open_mae is not None and adjusted_mae is not None
        else None
    )

    reasons: list[str] = []
    if len(train) < MIN_TRAIN_GAMES:
        reasons.append("INSUFFICIENT_TRAIN_GAMES")
    if len(holdout) < MIN_HOLDOUT_GAMES:
        reasons.append("INSUFFICIENT_HOLDOUT_GAMES")
    if mae_improvement is None or mae_improvement <= 0:
        reasons.append("NO_MARGIN_MAE_IMPROVEMENT")
    if mean_clv is None or mean_clv <= 0:
        reasons.append("NO_POSITIVE_DIRECTIONAL_CLV")
    if beat_rate is None or beat_rate <= 0.5:
        reasons.append("NO_BEAT_CLOSE_MAJORITY")
    if movement_slope is None or movement_slope <= 0:
        reasons.append("MOVEMENT_SLOPE_NOT_POSITIVE")

    return {
        "trainGames": len(train),
        "holdoutGames": len(holdout),
        "rawOutcomeResidualSlope": outcome_slope,
        "rawMovementSlope": movement_slope,
        "week4OutcomeWeight": outcome_weight,
        "week4MovementWeight": movement_weight,
        "holdoutOpenMarketMae": open_mae,
        "holdoutAdjustedMae": adjusted_mae,
        "holdoutCloseMarketMae": close_mae,
        "holdoutMaeImprovementVsOpen": mae_improvement,
        "holdoutMeanDirectionalClv": mean_clv,
        "holdoutBeatCloseRate": beat_rate,
        "shadowQualified": not reasons,
        "failedShadowChecks": reasons,
        "allWeeksBuckets": _bucket_stats(rows),
        "holdoutBuckets": _bucket_stats(holdout),
    }


def build_report(
    *,
    season: int,
    rows: Sequence[LearningRow],
    reconciliation: Mapping[str, Any],
    source_hashes: Mapping[str, str],
    revision: str,
) -> dict[str, Any]:
    summary = summarize(rows)
    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "season": season,
        "revision": revision,
        "cohorts": {
            "trainWeeks": list(TRAIN_WEEKS),
            "pseudoHoldoutWeek": PSEUDO_HOLDOUT_WEEK,
            "prospectiveWeek": PROSPECTIVE_WEEK,
        },
        "sourcePolicy": {
            "cfbdLines": "reference_only_not_executable",
            "results": "cfbfastR_schedule",
            "projection": "frozen_weekly_projection_using_prior_results_only",
        },
        "sourceHashes": dict(source_hashes),
        "reconciliation": dict(reconciliation),
        "summary": summary,
        "rows": [asdict(r) for r in rows],
        "promotionEffect": "NONE",
        "interpretation": (
            "Weeks 1-2 may fit the early-season challenger. Week 3 is a one-time "
            "pseudo-holdout. Week 4 is untouched and prospective. Even a passing "
            "pseudo-holdout qualifies only a Week-4 SHADOW ranking, never delivery."
        ),
    }


def build_shadow_board(
    *,
    report: Mapping[str, Any],
    slate_path: Path | str,
    opens_path: Path | str,
) -> dict[str, Any]:
    """Rank Week-4 shadow candidates from captured opens.

    This never converts a reference line into an executable price and never
    changes delivery authority.
    """
    summary = report.get("summary") or {}
    outcome_weight = float(summary.get("week4OutcomeWeight") or 0.0)
    movement_weight = float(summary.get("week4MovementWeight") or 0.0)
    qualified = bool(summary.get("shadowQualified"))

    with Path(slate_path).open(newline="", encoding="utf-8") as fh:
        slate = {
            r["game"].strip(): r
            for r in csv.DictReader(fh)
            if (r.get("game") or "").strip()
        }
    with Path(opens_path).open(newline="", encoding="utf-8") as fh:
        opens = {
            r["game"].strip(): r
            for r in csv.DictReader(fh)
            if (r.get("game") or "").strip()
        }

    rows: list[dict[str, Any]] = []
    for game, s in slate.items():
        op = opens.get(game)
        projected = _number(s.get("projected_margin"))
        open_line = _number(op.get("opening_line")) if op else None
        source = (op.get("source") or "").strip() if op else ""
        exclusions: list[str] = []
        if op is None:
            exclusions.append("NO_CAPTURED_OPEN")
        elif source != "capture":
            exclusions.append("OPEN_NOT_TIMELY_CAPTURE")
        if projected is None:
            exclusions.append("PROJECTION_MISSING")
        if open_line is None:
            exclusions.append("OPEN_LINE_MISSING")

        row: dict[str, Any] = {
            "game": game,
            "kickoff": s.get("kickoff") or "",
            "source": source or None,
            "firstSeen": op.get("first_seen") if op else None,
            "projectedHomeMargin": projected,
            "openingHomeLine": open_line,
            "status": "PASS",
            "exclusions": exclusions,
        }
        if exclusions:
            rows.append(row)
            continue

        assert projected is not None and open_line is not None
        market_margin = -open_line
        gap = projected - market_margin
        side = game.split("@", 1)[1].strip() if gap > 0 else game.split("@", 1)[0].strip()
        adjusted_margin = market_margin + outcome_weight * gap
        adjusted_line = -adjusted_margin
        expected_clv = abs(movement_weight * gap)
        row.update({
            "side": side,
            "projectionGapVsOpen": gap,
            "adjustedFairHomeLine": adjusted_line,
            "learnedExpectedDirectionalClv": expected_clv,
            "shadowScore": expected_clv,
        })

        if not qualified:
            row["exclusions"].append("EARLY_SEASON_HOLDOUT_NOT_QUALIFIED")
        elif abs(gap) < MIN_SHADOW_GAP:
            row["exclusions"].append("SUB_MIN_DISAGREEMENT")
        elif expected_clv <= 0:
            row["exclusions"].append("NO_EXPECTED_DIRECTIONAL_CLV")
        else:
            row["status"] = "SHADOW_WATCH"

        rows.append(row)

    rows.sort(
        key=lambda r: (
            0 if r.get("status") == "SHADOW_WATCH" else 1,
            -float(r.get("shadowScore") or 0.0),
            r["game"],
        )
    )
    watches = [r for r in rows if r.get("status") == "SHADOW_WATCH"][:5]
    return {
        "schemaVersion": 1,
        "contract": SHADOW_CONTRACT,
        "season": report.get("season"),
        "week": PROSPECTIVE_WEEK,
        "learningContract": report.get("contract"),
        "learningRevision": report.get("revision"),
        "shadowQualified": qualified,
        "failedShadowChecks": list(summary.get("failedShadowChecks") or []),
        "topFive": watches,
        "rows": rows,
        "deliveryEffect": "NONE",
        "actualStakeUnits": 0,
        "status": "SHADOW_ONLY",
    }


def _learn(args: argparse.Namespace) -> int:
    prior = load_schedule(args.prior_schedule)
    current = load_schedule(args.schedule)
    line_paths = {
        1: Path(args.week1_lines),
        2: Path(args.week2_lines),
        3: Path(args.week3_lines),
    }
    lines = {week: load_lines(path) for week, path in line_paths.items()}
    rows, reconciliation = build_learning_rows(
        season=args.season,
        prior_schedule_rows=prior,
        schedule_rows=current,
        lines_by_week=lines,
    )
    hashes = {
        "priorSchedule": _sha(args.prior_schedule),
        "schedule": _sha(args.schedule),
        **{f"week{week}Lines": _sha(path) for week, path in line_paths.items()},
    }
    report = build_report(
        season=args.season,
        rows=rows,
        reconciliation=reconciliation,
        source_hashes=hashes,
        revision=args.revision,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    s = report["summary"]
    print(
        f"early-season: train={s['trainGames']} holdout={s['holdoutGames']} "
        f"qualified={s['shadowQualified']} "
        f"MAE_lift={s['holdoutMaeImprovementVsOpen']} "
        f"CLV={s['holdoutMeanDirectionalClv']}"
    )
    return 0


def _board(args: argparse.Namespace) -> int:
    report = json.loads(Path(args.learning).read_text(encoding="utf-8"))
    if report.get("contract") != CONTRACT:
        raise SystemExit("learning report contract mismatch")
    board = build_shadow_board(
        report=report,
        slate_path=args.slate,
        opens_path=args.opens,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(board, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"week4 shadow: qualified={board['shadowQualified']} "
        f"watch={len(board['topFive'])} stakes=0"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cfb_edge.early_season", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    learn = sub.add_parser("learn")
    learn.add_argument("--season", type=int, default=2026)
    learn.add_argument("--prior-schedule", required=True)
    learn.add_argument("--schedule", required=True)
    learn.add_argument("--week1-lines", required=True)
    learn.add_argument("--week2-lines", required=True)
    learn.add_argument("--week3-lines", required=True)
    learn.add_argument("--revision", default="unknown")
    learn.add_argument("--out", required=True)
    learn.set_defaults(func=_learn)

    board = sub.add_parser("board")
    board.add_argument("--learning", required=True)
    board.add_argument("--slate", required=True)
    board.add_argument("--opens", required=True)
    board.add_argument("--out", required=True)
    board.set_defaults(func=_board)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
