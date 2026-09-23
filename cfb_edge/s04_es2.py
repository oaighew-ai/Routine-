"""S04_ES2: Week-5 audit-grade gap + market-quality challenger.

The rule was preregistered before Week 4 outcomes. Week 5 is the first cohort
eligible to validate it. This module cannot place a bet: every output carries
zero stake and deliveryEffect NONE.

A row can enter the prospective cohort only when:
- the persisted opening row is source=true_open;
- its recorded open lag is finite and no more than the frozen tolerance;
- the frozen projection/open disagreement is 4 to <6 points;
- the live venue is compared to Pinnacle at the exact same spread;
- S03_M1 market quality passes;
- conservative executable EV clears the frozen floor;
- the historical movement budget has not already been spent.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import teams
from .engine import config as engine_config
from .market_quality import load_snapshot_history, quality_for_row
from .providers.oddsapi import fetch_pull
from .s04_es1 import cohort_stats
from .shop import ShopRow, shop
from .week5_freeze import assert_frozen_files

CONTRACT = "CFB_EDGE_S04_ES2_LIVE_V1"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def load_candidates(
    *,
    slate_path: Path | str,
    opens_path: Path | str,
    cfg: Mapping[str, Any],
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    """Build the Week-5 signal cohort and retain every exclusion for audit."""
    with Path(slate_path).open(newline="", encoding="utf-8") as fh:
        slate = {
            r["game"].strip(): r for r in csv.DictReader(fh)
            if (r.get("game") or "").strip()
        }
    with Path(opens_path).open(newline="", encoding="utf-8") as fh:
        opens = {
            r["game"].strip(): r for r in csv.DictReader(fh)
            if (r.get("game") or "").strip()
        }

    required_source = str(cfg.get("requiredOpenSource") or "true_open")
    max_lag = float(cfg.get("maximumOpenCaptureLagSeconds", 900))
    gap_min = float(cfg["gapMin"])
    gap_max = float(cfg["gapMax"])

    out: dict[tuple[str, str], dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []

    for game, s in sorted(slate.items()):
        op = opens.get(game)
        projected = _number(s.get("projected_margin"))
        opening = _number(op.get("opening_line")) if op else None
        source = str(op.get("source") or "") if op else ""
        lag = _number(op.get("open_lag_seconds")) if op else None
        exclusions: list[str] = []

        if op is None:
            exclusions.append("NO_CAPTURED_OPEN")
        elif source != required_source:
            exclusions.append("OPEN_NOT_AUDIT_GRADE_TRUE_OPEN")
        if projected is None:
            exclusions.append("PROJECTION_MISSING")
        if opening is None:
            exclusions.append("OPEN_LINE_MISSING")
        if source == required_source:
            if lag is None:
                exclusions.append("OPEN_LAG_MISSING")
            elif lag < -60 or lag > max_lag:
                exclusions.append("OPEN_LAG_OUTSIDE_FROZEN_TOLERANCE")

        gap = None
        side = None
        if projected is not None and opening is not None:
            market_margin = -opening
            gap = projected - market_margin
            if not gap_min <= abs(gap) < gap_max:
                exclusions.append("OUTSIDE_FROZEN_4_TO_6_GAP")
            if "@" not in game:
                exclusions.append("INVALID_GAME")
            else:
                away, home = (x.strip() for x in game.split("@", 1))
                side = home if gap > 0 else away

        row = {
            "game": game,
            "kickoff": s.get("kickoff") or "",
            "source": source or None,
            "firstSeen": op.get("first_seen") if op else None,
            "venueOpenTime": op.get("venue_open_time") if op else None,
            "openLagSeconds": lag,
            "projectedHomeMargin": projected,
            "openingHomeLine": opening,
            "projectionGapVsOpen": gap,
            "side": side,
            "exclusions": sorted(set(exclusions)),
            "auditGrade": not exclusions,
        }
        audit.append(row)
        if exclusions or side is None or gap is None or opening is None:
            continue
        out[(game, side)] = {
            "game": game,
            "side": side,
            "gap": float(gap),
            "openingHomeLine": float(opening),
            "firstSeen": row["firstSeen"],
            "venueOpenTime": row["venueOpenTime"],
            "openLagSeconds": float(lag),
            "kickoff": row["kickoff"],
        }
    return out, audit


def _resolve_provider_team(name: str, known: set[str]) -> str | None:
    direct = teams.resolve(name, known)
    if direct:
        return direct
    words = str(name or "").strip().split()
    for count in (1, 2, 3):
        if len(words) <= count:
            break
        resolved = teams.resolve(" ".join(words[:-count]), known)
        if resolved:
            return resolved
    return None


def _candidate_teams(candidates: Mapping[tuple[str, str], Mapping[str, Any]]) -> set[str]:
    out: set[str] = set()
    for game, _side in candidates:
        if "@" in game:
            away, home = (x.strip() for x in game.split("@", 1))
            out.update((away, home))
    return out


def _current_home_line(row: ShopRow, candidate: Mapping[str, Any]) -> float | None:
    line = _number(row.consensus_line)
    if line is None or "@" not in str(candidate.get("game") or ""):
        return None
    _away, home = (x.strip() for x in str(candidate["game"]).split("@", 1))
    return line if candidate.get("side") == home else -line


def evaluate(
    *,
    candidates: Mapping[tuple[str, str], Mapping[str, Any]],
    audit_rows: Sequence[Mapping[str, Any]],
    shop_rows: Sequence[ShopRow],
    learning: Mapping[str, Any],
    cfg: Mapping[str, Any],
    quality_cfg: Mapping[str, Any],
    edge_cfg: engine_config.Config,
    pull: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    stats = cohort_stats(
        learning,
        gap_min=float(cfg["gapMin"]),
        gap_max=float(cfg["gapMax"]),
    )
    known = _candidate_teams(candidates)
    venues = set(str(x) for x in cfg.get("venues") or ())
    reference = str(cfg.get("referenceBook") or "pinnacle")
    ev_floor = float(cfg.get("minimumConservativeExecutableEv", 0.0))
    inspected: list[dict[str, Any]] = []
    qualified: list[dict[str, Any]] = []
    mapping_failures: set[str] = set()

    for row in shop_rows:
        if row.market != "spreads" or row.venue not in venues:
            continue
        away = _resolve_provider_team(row.away, known)
        home = _resolve_provider_team(row.home, known)
        side = _resolve_provider_team(row.side, known)
        if not away or not home or not side:
            mapping_failures.add(f"{row.away} @ {row.home} | {row.side}")
            continue
        game = f"{away} @ {home}"
        candidate = candidates.get((game, side))
        if candidate is None:
            continue

        exclusions: list[str] = []
        if row.reference != reference:
            exclusions.append("PINNACLE_REFERENCE_REQUIRED")

        quality = quality_for_row(
            row=row,
            candidate=candidate,
            pull=pull,
            history=history,
            cfg=quality_cfg,
            edge_cfg=edge_cfg,
        )
        if cfg.get("requireMarketQualityPass") is True and quality.get("status") != "PASS":
            exclusions.append("MARKET_QUALITY_BLOCKED")

        conservative_ev = _number(quality.get("conservativeExecutableEv"))
        if conservative_ev is None:
            exclusions.append("CONSERVATIVE_EXECUTABLE_EV_MISSING")
        elif conservative_ev < ev_floor:
            exclusions.append("CONSERVATIVE_EXECUTABLE_EV_BELOW_FLOOR")

        current_home = _current_home_line(row, candidate)
        remaining = None
        if current_home is None:
            exclusions.append("CURRENT_REFERENCE_LINE_MISSING")
        else:
            gap = float(candidate["gap"])
            sign = 1.0 if gap > 0 else -1.0
            spent = sign * (float(candidate["openingHomeLine"]) - current_home)
            remaining = stats.mean_directional_clv - spent
            if cfg.get("requireMovementRemaining") is True and remaining <= 0:
                exclusions.append("HISTORICAL_MOVEMENT_BUDGET_SPENT")

        item = {
            "game": game,
            "side": candidate["side"],
            "venue": row.venue,
            "source": "true_open",
            "kickoff": candidate.get("kickoff"),
            "openingHomeLine": candidate["openingHomeLine"],
            "projectionGapVsOpen": candidate["gap"],
            "firstSeen": candidate.get("firstSeen"),
            "venueOpenTime": candidate.get("venueOpenTime"),
            "openLagSeconds": candidate.get("openLagSeconds"),
            "currentLine": row.venue_line,
            "currentPrice": row.venue_price,
            "pinnacleReferenceLine": row.consensus_line,
            "conservativeExecutableEv": conservative_ev,
            "historicalMeanDirectionalClv": stats.mean_directional_clv,
            "estimatedMovementRemaining": remaining,
            "marketQuality": quality,
            "exclusions": sorted(set(exclusions)),
            "status": "EXECUTABLE_SHADOW" if not exclusions else "BLOCKED",
            "actualStakeUnits": 0,
        }
        inspected.append(item)
        if not exclusions:
            qualified.append(item)

    best: dict[tuple[str, str], dict[str, Any]] = {}
    for item in qualified:
        key = (item["game"], item["side"])
        if key not in best or float(item["conservativeExecutableEv"]) > float(best[key]["conservativeExecutableEv"]):
            best[key] = item
    final = sorted(
        best.values(),
        key=lambda x: (-float(x["conservativeExecutableEv"]), str(x["game"])),
    )

    audit_grade = sum(1 for r in audit_rows if r.get("auditGrade"))
    source_counts: dict[str, int] = {}
    for r in audit_rows:
        src = str(r.get("source") or "missing")
        source_counts[src] = source_counts.get(src, 0) + 1

    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "modelId": cfg.get("modelId"),
        "modelVersion": cfg.get("version"),
        "status": "SHADOW_ONLY",
        "season": cfg.get("season"),
        "week": cfg.get("firstProspectiveWeek"),
        "generatedAt": pull.get("fetched_at") or pull.get("fetchedAt"),
        "openCohort": {
            "requiredSource": cfg.get("requiredOpenSource"),
            "maximumLagSeconds": cfg.get("maximumOpenCaptureLagSeconds"),
            "slateRows": len(audit_rows),
            "auditGradeSignalCandidates": audit_grade,
            "sourceCounts": source_counts,
        },
        "historicalMovementBudget": {
            "source": "Weeks 1-3 2026 4-6 gap cohort",
            "n": stats.n,
            "meanDirectionalClv": stats.mean_directional_clv,
            "beatCloseRate": stats.beat_close_rate,
        },
        "candidateAuditRows": list(audit_rows),
        "inspectedLiveRows": inspected,
        "mappingFailures": sorted(mapping_failures),
        "qualifiedCount": len(final),
        "topFive": final[:5],
        "deliveryEffect": "NONE",
        "promotionEffect": "NONE",
        "actualStakeUnits": 0,
        "limitations": [
            "Week 5 is the first prospective audit-grade cohort for S04_ES2.",
            "Week 4 cannot validate this model.",
            "Pinnacle is a market reference, not ground truth.",
            "Market-quality and executable-EV gates cannot create a football opinion.",
            "No row from this contract may enter the authoritative picks feed.",
        ],
    }


def empty_report(
    *,
    candidates: Mapping[tuple[str, str], Mapping[str, Any]],
    audit_rows: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any],
    learning: Mapping[str, Any],
) -> dict[str, Any]:
    stats = cohort_stats(
        learning,
        gap_min=float(cfg["gapMin"]),
        gap_max=float(cfg["gapMax"]),
    )
    counts: dict[str, int] = {}
    for r in audit_rows:
        src = str(r.get("source") or "missing")
        counts[src] = counts.get(src, 0) + 1
    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "modelId": cfg.get("modelId"),
        "modelVersion": cfg.get("version"),
        "status": "SHADOW_ONLY",
        "season": cfg.get("season"),
        "week": cfg.get("firstProspectiveWeek"),
        "generatedAt": None,
        "openCohort": {
            "requiredSource": cfg.get("requiredOpenSource"),
            "maximumLagSeconds": cfg.get("maximumOpenCaptureLagSeconds"),
            "slateRows": len(audit_rows),
            "auditGradeSignalCandidates": len(candidates),
            "sourceCounts": counts,
        },
        "historicalMovementBudget": {
            "source": "Weeks 1-3 2026 4-6 gap cohort",
            "n": stats.n,
            "meanDirectionalClv": stats.mean_directional_clv,
            "beatCloseRate": stats.beat_close_rate,
        },
        "candidateAuditRows": list(audit_rows),
        "inspectedLiveRows": [],
        "mappingFailures": [],
        "qualifiedCount": 0,
        "topFive": [],
        "deliveryEffect": "NONE",
        "promotionEffect": "NONE",
        "actualStakeUnits": 0,
        "limitations": [
            "No audit-grade signal candidate exists yet, so no live odds request was made.",
            "Week 5 is the first prospective audit-grade cohort for S04_ES2.",
        ],
    }


def run_live(
    *,
    learning: Mapping[str, Any],
    slate_path: Path | str,
    opens_path: Path | str,
    cfg: Mapping[str, Any],
    quality_cfg: Mapping[str, Any],
    edge_cfg: engine_config.Config,
    history: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], Any | None]:
    candidates, audit = load_candidates(
        slate_path=slate_path,
        opens_path=opens_path,
        cfg=cfg,
    )
    if not candidates:
        return empty_report(
            candidates=candidates,
            audit_rows=audit,
            cfg=cfg,
            learning=learning,
        ), None

    books = [str(cfg.get("referenceBook") or "pinnacle")] + [
        str(x) for x in cfg.get("venues") or ()
    ]
    pull = fetch_pull(
        sport="americanfootball_ncaaf",
        bookmakers=books,
        markets=["spreads"],
        credit_cap_monthly=float(edge_cfg.raw.get("credit_cap_monthly", 0) or 0) or None,
    )
    now = datetime.fromisoformat(pull.fetched_at.replace("Z", "+00:00"))
    rows = shop(
        pull.__dict__ | {"events": list(pull.events)},
        cfg=edge_cfg,
        venues=tuple(cfg.get("venues") or ()),
        markets=("spreads",),
        reference=str(cfg.get("referenceBook") or "pinnacle"),
        now=now,
        age_seconds=0.0,
    )
    report = evaluate(
        candidates=candidates,
        audit_rows=audit,
        shop_rows=rows,
        learning=learning,
        cfg=cfg,
        quality_cfg=quality_cfg,
        edge_cfg=edge_cfg,
        pull=pull.__dict__ | {"events": list(pull.events)},
        history=history,
    )
    return report, pull


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cfb_edge.s04_es2", description=__doc__)
    p.add_argument("--learning", required=True)
    p.add_argument("--slate", required=True)
    p.add_argument("--opens", required=True)
    p.add_argument("--challenger", default="config/s04_es2.json")
    p.add_argument("--edge-config", default="config/edge_os.json")
    p.add_argument("--market-quality-config", default="config/s03_m1.json")
    p.add_argument("--freeze-manifest", default="config/week5_freeze.json")
    p.add_argument("--history-dir")
    p.add_argument("--out", required=True)
    p.add_argument("--snapshot-out")
    args = p.parse_args(argv)

    learning = json.loads(Path(args.learning).read_text(encoding="utf-8"))
    cfg = json.loads(Path(args.challenger).read_text(encoding="utf-8"))
    quality_cfg = json.loads(Path(args.market_quality_config).read_text(encoding="utf-8"))
    assert_frozen_files(args.challenger, args.market_quality_config, args.freeze_manifest)
    edge_cfg = engine_config.load(args.edge_config)
    history = load_snapshot_history(args.history_dir) if args.history_dir else []

    report, pull = run_live(
        learning=learning,
        slate_path=args.slate,
        opens_path=args.opens,
        cfg=cfg,
        quality_cfg=quality_cfg,
        edge_cfg=edge_cfg,
        history=history,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    if args.snapshot_out and pull is not None:
        snapshot = asdict(pull)
        snapshot["events"] = list(pull.events)
        Path(args.snapshot_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.snapshot_out).write_text(
            json.dumps(snapshot, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    print(
        f"S04_ES2 Week 5 audit shadow: auditCandidates="
        f"{report['openCohort']['auditGradeSignalCandidates']} "
        f"qualified={report['qualifiedCount']} stakes=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
