"""S04_ES1: Week-4 prospective early-season challenger.

This challenger was frozen after inspecting Weeks 1-3. It is therefore
exploratory and can never use those weeks to promote itself. Its first
prospective test is 2026 Week 4.

Signal:
- projection disagrees with the timely captured opening line by 4 to <6 points;
- the 4-6 bucket must have positive directional CLV in each of Weeks 1, 2, 3;
- a live sportsbook must offer the same spread as Pinnacle;
- the sportsbook price must be positive-EV relative to Pinnacle's no-vig
  probability by the configured minimum;
- if the captured-open-to-current move has already consumed the historical
  4-6 bucket's mean directional CLV, the candidate expires.

The output is SHADOW only. Stake is always zero and this module has no route to
the authoritative picks contract.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .engine import config as engine_config
from .engine import reasons
from .engine.pricing import max_playable_price
from .providers.oddsapi import fetch_pull
from .shop import ShopRow, shop

CONTRACT = "CFB_EDGE_S04_ES1_LIVE_V1"


@dataclass(frozen=True)
class CohortStats:
    n: int
    mean_directional_clv: float
    beat_close_rate: float
    by_week: dict[int, dict[str, float]]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def cohort_stats(
    learning: Mapping[str, Any],
    *,
    gap_min: float,
    gap_max: float,
) -> CohortStats:
    rows = []
    for row in learning.get("rows") or []:
        gap = _number(row.get("projection_gap_vs_open"))
        clv = _number(row.get("directional_clv"))
        week = row.get("week")
        if gap is None or clv is None or week not in (1, 2, 3):
            continue
        if gap_min <= abs(gap) < gap_max:
            rows.append((int(week), clv))

    by_week: dict[int, dict[str, float]] = {}
    for week in (1, 2, 3):
        values = [clv for w, clv in rows if w == week]
        if values:
            by_week[week] = {
                "n": float(len(values)),
                "meanDirectionalClv": sum(values) / len(values),
                "beatCloseRate": sum(v > 0 for v in values) / len(values),
            }

    values = [clv for _, clv in rows]
    return CohortStats(
        n=len(values),
        mean_directional_clv=(sum(values) / len(values) if values else 0.0),
        beat_close_rate=(sum(v > 0 for v in values) / len(values) if values else 0.0),
        by_week=by_week,
    )


def historical_reasons(
    stats: CohortStats,
    cfg: Mapping[str, Any],
) -> list[str]:
    out: list[str] = []
    if stats.n < int(cfg.get("minimumHistoricalGames", 0)):
        out.append("INSUFFICIENT_HISTORICAL_GAMES")

    per_week = int(cfg.get("minimumGamesPerWeek", 0))
    for week in (1, 2, 3):
        w = stats.by_week.get(week)
        if w is None or int(w["n"]) < per_week:
            out.append(f"WEEK_{week}_INSUFFICIENT_GAMES")
            continue
        if cfg.get("requirePositiveMeanClvEveryWeek") is True:
            if w["meanDirectionalClv"] <= 0:
                out.append(f"WEEK_{week}_NONPOSITIVE_CLV")

    if cfg.get("requireOverallBeatCloseMajority") is True:
        if stats.beat_close_rate <= 0.5:
            out.append("HISTORICAL_BEAT_CLOSE_NOT_MAJORITY")
    if stats.mean_directional_clv <= 0:
        out.append("HISTORICAL_MEAN_CLV_NONPOSITIVE")
    return sorted(set(out))


def candidate_map(
    week4: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    gap_min = float(cfg["gapMin"])
    gap_max = float(cfg["gapMax"])
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in week4.get("rows") or []:
        gap = _number(row.get("projectionGapVsOpen"))
        opening = _number(row.get("openingHomeLine"))
        game = str(row.get("game") or "").strip()
        side = str(row.get("side") or "").strip()
        if (
            row.get("source") != "capture"
            or gap is None
            or opening is None
            or not game
            or not side
            or not gap_min <= abs(gap) < gap_max
        ):
            continue
        out[(game, side)] = {
            "game": game,
            "side": side,
            "gap": gap,
            "openingHomeLine": opening,
            "firstSeen": row.get("firstSeen"),
            "kickoff": row.get("kickoff"),
        }
    return out


def _home_line(row: ShopRow, side: str) -> float | None:
    line = _number(row.consensus_line)
    if line is None:
        return None
    return line if side == row.home else -line


def _row_reasons(
    *,
    row: ShopRow,
    candidate: Mapping[str, Any],
    stats: CohortStats,
    cfg: Mapping[str, Any],
) -> tuple[list[str], float | None, float | None]:
    out: list[str] = []
    d = row.decision

    if row.reference != str(cfg.get("referenceBook")):
        out.append("PINNACLE_REFERENCE_REQUIRED")
    blocked = {
        reasons.LINE_MISMATCH,
        reasons.IN_PLAY,
        reasons.DEVIG_SENSITIVE,
        reasons.DEVIG_IMPLAUSIBLE,
        reasons.STALE,
        reasons.UNMAPPED,
    }
    out.extend(sorted(blocked.intersection(d.reason_codes)))
    if d.p_baseline is None:
        out.append("PINNACLE_BASELINE_MISSING")

    ev_floor = float(cfg.get("minimumLivePriceEv", 0.0))
    if d.ev < ev_floor:
        out.append("LIVE_PRICE_EV_BELOW_FLOOR")

    current_home = _home_line(row, candidate["side"])
    remaining = None
    if current_home is None:
        out.append("CURRENT_REFERENCE_LINE_MISSING")
    else:
        gap = float(candidate["gap"])
        sign = 1.0 if gap > 0 else -1.0
        spent = sign * (float(candidate["openingHomeLine"]) - current_home)
        remaining = stats.mean_directional_clv - spent
        if cfg.get("requireMovementRemaining") is True and remaining <= 0:
            out.append("HISTORICAL_MOVEMENT_BUDGET_SPENT")

    return sorted(set(out)), current_home, remaining


def evaluate(
    *,
    learning: Mapping[str, Any],
    week4: Mapping[str, Any],
    shop_rows: Sequence[ShopRow],
    challenger_cfg: Mapping[str, Any],
    edge_cfg: engine_config.Config,
    fetched_at: str,
) -> dict[str, Any]:
    stats = cohort_stats(
        learning,
        gap_min=float(challenger_cfg["gapMin"]),
        gap_max=float(challenger_cfg["gapMax"]),
    )
    history_reasons = historical_reasons(stats, challenger_cfg)
    candidates = candidate_map(week4, challenger_cfg)

    inspected: list[dict[str, Any]] = []
    qualified: list[dict[str, Any]] = []
    venues = set(challenger_cfg.get("venues") or [])

    for row in shop_rows:
        if row.market != "spreads" or row.venue not in venues:
            continue
        game = f"{row.away} @ {row.home}"
        candidate = candidates.get((game, row.side))
        if candidate is None:
            continue

        exclusions, current_home, remaining = _row_reasons(
            row=row, candidate=candidate, stats=stats, cfg=challenger_cfg
        )
        exclusions.extend(history_reasons)
        exclusions = sorted(set(exclusions))
        max_price = None
        if row.decision.p_baseline is not None:
            max_price = max_playable_price(
                row.decision.p_baseline,
                venue=row.venue,
                venue_config=edge_cfg.venue(row.venue),
                grid=edge_cfg.price_grid,
            )

        item = {
            "game": game,
            "side": row.side,
            "venue": row.venue,
            "currentLine": row.venue_line,
            "currentPrice": row.venue_price,
            "pinnacleReferenceLine": row.consensus_line,
            "pinnacleFairProbability": row.decision.p_baseline,
            "livePriceEv": row.decision.ev,
            "maxPlayablePriceVsPinnacle": max_price,
            "openingHomeLine": candidate["openingHomeLine"],
            "projectionGapVsOpen": candidate["gap"],
            "currentReferenceHomeLine": current_home,
            "historicalMeanDirectionalClv": stats.mean_directional_clv,
            "estimatedMovementRemaining": remaining,
            "firstSeen": candidate.get("firstSeen"),
            "startsAt": row.commence_time,
            "status": "PASS" if exclusions else "EXECUTABLE_SHADOW",
            "exclusions": exclusions,
            "actualStakeUnits": 0,
        }
        inspected.append(item)
        if not exclusions:
            qualified.append(item)

    # One venue per game/side: best live EV wins. Nothing is silently deleted;
    # the full inspected list remains in the output.
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for item in qualified:
        key = (item["game"], item["side"])
        if key not in best or item["livePriceEv"] > best[key]["livePriceEv"]:
            best[key] = item
    final = sorted(
        best.values(),
        key=lambda x: (-float(x["livePriceEv"]), str(x["game"])),
    )

    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "modelId": challenger_cfg.get("modelId"),
        "modelVersion": challenger_cfg.get("version"),
        "status": "SHADOW_ONLY",
        "season": challenger_cfg.get("season"),
        "week": challenger_cfg.get("prospectiveWeek"),
        "generatedAt": fetched_at,
        "historicalCohort": {
            "gapMin": challenger_cfg.get("gapMin"),
            "gapMax": challenger_cfg.get("gapMax"),
            "n": stats.n,
            "meanDirectionalClv": stats.mean_directional_clv,
            "beatCloseRate": stats.beat_close_rate,
            "byWeek": stats.by_week,
            "qualifiedForProspectiveShadow": not history_reasons,
            "failedChecks": history_reasons,
        },
        "week4CapturedCandidates": len(candidates),
        "inspectedLiveRows": inspected,
        "topFive": final[:5],
        "qualifiedCount": len(final),
        "deliveryEffect": "NONE",
        "actualStakeUnits": 0,
        "promotionEffect": "NONE",
        "limitations": [
            "Weeks 1-3 were inspected before S04_ES1 was frozen; they do not validate it.",
            "Week 4 is the first prospective test of this exact challenger.",
            "Pinnacle is a market reference, not ground truth.",
            "Historical mean directional CLV is a timing budget, not guaranteed future CLV.",
            "No row from this contract may enter the authoritative picks feed.",
        ],
    }


def run_live(
    *,
    learning: Mapping[str, Any],
    week4: Mapping[str, Any],
    challenger_cfg: Mapping[str, Any],
    edge_cfg: engine_config.Config,
) -> tuple[dict[str, Any], Any]:
    books = [str(challenger_cfg["referenceBook"])] + [
        str(x) for x in challenger_cfg.get("venues") or []
    ]
    pull = fetch_pull(
        sport="americanfootball_ncaaf",
        bookmakers=books,
        markets=["spreads"],
        credit_cap_monthly=float(edge_cfg.raw.get("credit_cap_monthly", 0) or 0)
        or None,
    )
    now = datetime.fromisoformat(pull.fetched_at.replace("Z", "+00:00"))
    rows = shop(
        pull.__dict__ | {"events": list(pull.events)},
        cfg=edge_cfg,
        venues=tuple(challenger_cfg.get("venues") or ()),
        markets=("spreads",),
        reference=str(challenger_cfg["referenceBook"]),
        now=now,
        age_seconds=0.0,
    )
    report = evaluate(
        learning=learning,
        week4=week4,
        shop_rows=rows,
        challenger_cfg=challenger_cfg,
        edge_cfg=edge_cfg,
        fetched_at=pull.fetched_at,
    )
    return report, pull


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cfb_edge.s04_es1", description=__doc__)
    p.add_argument("--learning", required=True)
    p.add_argument("--week4", required=True)
    p.add_argument("--challenger", default="config/s04_es1.json")
    p.add_argument("--edge-config", default="config/edge_os.json")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    learning = json.loads(Path(args.learning).read_text(encoding="utf-8"))
    week4 = json.loads(Path(args.week4).read_text(encoding="utf-8"))
    challenger_cfg = json.loads(Path(args.challenger).read_text(encoding="utf-8"))
    edge_cfg = engine_config.load(args.edge_config)

    report, pull = run_live(
        learning=learning,
        week4=week4,
        challenger_cfg=challenger_cfg,
        edge_cfg=edge_cfg,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"S04_ES1 shadow: historical={report['historicalCohort']['n']} "
        f"captured={report['week4CapturedCandidates']} "
        f"qualified={report['qualifiedCount']} "
        f"creditsRemaining={pull.credits_remaining}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
