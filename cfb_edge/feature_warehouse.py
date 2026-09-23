"""Point-in-time feature warehouse for the S04_BR2 challenger.

The warehouse records what was actually knowable at a snapshot time. Missing
features stay missing. No proxy is silently relabeled as a requested feature.
S04_BR2 has no delivery authority and this module cannot alter Week 5 S04_ES2.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

CONTRACT = "CFB_EDGE_BR2_FEATURE_SNAPSHOT_V1"
FEATURES = (
    "epaDiff",
    "ppaDiff",
    "successRateDiff",
    "explosivenessDiff",
    "qbContinuityDiff",
    "linePlayDiff",
    "paceDiff",
    "restDaysDiff",
    "travelMilesDiff",
    "windMph",
)


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _success(play: Mapping[str, Any]) -> bool | None:
    down = _number(play.get("down"))
    distance = _number(play.get("distance"))
    gained = _number(play.get("yardsGained"))
    if down is None or distance is None or gained is None or distance <= 0:
        return None
    needed = 0.5 if down <= 1 else 0.7 if down == 2 else 1.0
    return gained >= needed * distance


def team_metrics(plays: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"ppa": [], "success": [], "explosive": [], "games": set(), "plays": 0}
    )
    for play in plays:
        team = str(play.get("offense") or "").strip()
        if not team:
            continue
        a = acc[team]
        a["plays"] += 1
        if play.get("gameId") is not None:
            a["games"].add(str(play["gameId"]))
        ppa = _number(play.get("ppa"))
        if ppa is not None:
            a["ppa"].append(ppa)
        success = _success(play)
        if success is not None:
            a["success"].append(1.0 if success else 0.0)
            if success:
                yards = _number(play.get("yardsGained"))
                if yards is not None:
                    a["explosive"].append(yards)

    out = {}
    for team, a in acc.items():
        games = max(1, len(a["games"]))
        out[team] = {
            "ppa": sum(a["ppa"]) / len(a["ppa"]) if a["ppa"] else None,
            "successRate": sum(a["success"]) / len(a["success"]) if a["success"] else None,
            "explosiveness": (
                sum(a["explosive"]) / len(a["explosive"]) if a["explosive"] else None
            ),
            "pace": a["plays"] / games,
        }
    return out


def _last_game_dates(games: Sequence[Mapping[str, Any]], as_of: datetime) -> dict[str, datetime]:
    last: dict[str, datetime] = {}
    for game in games:
        started = _time(game.get("startDate") or game.get("start_date"))
        if started is None or started >= as_of:
            continue
        for key in ("homeTeam", "awayTeam", "home_team", "away_team"):
            team = str(game.get(key) or "").strip()
            if team and (team not in last or started > last[team]):
                last[team] = started
    return last


def _read_slate(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("game") or "").strip()]


def build_snapshot(
    *,
    slate: Sequence[Mapping[str, Any]],
    plays: Sequence[Mapping[str, Any]],
    prior_games: Sequence[Mapping[str, Any]],
    as_of: datetime,
    source_revision: str | None = None,
    source_manifest: Sequence[Mapping[str, Any]] = (),
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metrics = team_metrics(plays)
    last_games = _last_game_dates(prior_games, as_of)
    context_rows = {
        " ".join(str(r.get("game") or "").strip().lower().replace("&", "and").split()): r
        for r in ((context or {}).get("rows") or [])
        if str(r.get("game") or "").strip()
    }
    rows = []

    for raw in slate:
        game = str(raw.get("game") or "").strip()
        if "@" not in game:
            continue
        away, home = (x.strip() for x in game.split("@", 1))
        kickoff = _time(raw.get("kickoff"))
        feature_values: dict[str, Any] = {name: None for name in FEATURES}
        feature_sources: dict[str, str | None] = {name: None for name in FEATURES}

        hm, am = metrics.get(home, {}), metrics.get(away, {})
        for feature, metric in (
            ("ppaDiff", "ppa"),
            ("successRateDiff", "successRate"),
            ("explosivenessDiff", "explosiveness"),
            ("paceDiff", "pace"),
        ):
            hv, av = _number(hm.get(metric)), _number(am.get(metric))
            if hv is not None and av is not None:
                feature_values[feature] = hv - av
                feature_sources[feature] = "cfbd_plays_completed_before_snapshot"

        if home in last_games and away in last_games:
            home_rest = (as_of - last_games[home]).total_seconds() / 86400.0
            away_rest = (as_of - last_games[away]).total_seconds() / 86400.0
            feature_values["restDaysDiff"] = home_rest - away_rest
            feature_sources["restDaysDiff"] = "cfbd_games_completed_before_snapshot"

        # Context features come only from the separately audited BR2 context contract.
        ctx = context_rows.get(
            " ".join(game.lower().replace("&", "and").split())
        )
        if ctx:
            ctx_features = ctx.get("features") or {}
            ctx_audit = ctx.get("audit") or {}
            for feature in (
                "epaDiff", "qbContinuityDiff", "linePlayDiff",
                "travelMilesDiff", "windMph",
            ):
                value = _number(ctx_features.get(feature))
                if ctx_audit.get(feature) is True and value is not None:
                    feature_values[feature] = value
                    feature_sources[feature] = (
                        f"{context.get('contract', 'BR2_CONTEXT')}:{ctx.get('rowSha256', 'unhashed')}"
                    )

        complete = sum(v is not None for v in feature_values.values())
        row_payload = {
            "game": game,
            "away": away,
            "home": home,
            "kickoff": None if kickoff is None else kickoff.isoformat(),
            "snapshotAt": as_of.isoformat(),
            "features": feature_values,
            "featureSources": feature_sources,
            "availableFeatureCount": complete,
            "requiredFeatureCount": len(FEATURES),
        }
        row_payload["rowSha256"] = hashlib.sha256(
            json.dumps(row_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        rows.append(row_payload)

    feature_coverage = {
        feature: sum(1 for r in rows if r["features"][feature] is not None)
        for feature in FEATURES
    }
    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "modelId": "S04_BR2",
        "status": "DATA_COLLECTION_ONLY",
        "generatedAt": as_of.isoformat(),
        "sourceRevision": source_revision,
        "sourceManifest": [dict(x) for x in source_manifest],
        "pointInTimePolicy": {
            "futureDataAllowed": False,
            "missingValuesImputed": False,
            "proxyRelabelingAllowed": False,
            "decisionEffect": "NONE",
            "promotionEffect": "NONE",
        },
        "featureDefinitions": {
            "epaDiff": "Home minus away opponent-adjusted net EPA from the prospectively frozen CFBD WEPA snapshot; PPA remains separate.",
            "ppaDiff": "Home minus away mean CFBD play PPA through completed games before snapshot.",
            "successRateDiff": "Home minus away conventional down-and-distance success rate.",
            "explosivenessDiff": "Home minus away mean yards on successful offensive plays.",
            "qbContinuityDiff": "Home minus away audited QB continuity from official pregame depth-chart evidence; remains null without deterministic validation.",
            "linePlayDiff": "Home minus away net line yards, where team net = offensive lineYards minus defensive lineYardsAllowed, from week-bounded CFBD advanced stats.",
            "paceDiff": "Home minus away offensive plays per completed game.",
            "restDaysDiff": "Home minus away days since most recent completed game.",
            "travelMilesDiff": "Home minus away great-circle miles from each program's registered CFBD home location to the game venue.",
            "windMph": "Open-Meteo 10m sustained wind nearest kickoff from the forecast captured at snapshot time; confirmed domes are explicitly set to 0.",
        },
        "summary": {
            "games": len(rows),
            "featureCoverageRows": feature_coverage,
            "fullyPopulatedRows": sum(
                1 for r in rows if r["availableFeatureCount"] == len(FEATURES)
            ),
            "modelingEligible": False,
            "reason": "BR2 remains collection-only until the frozen feature contract has point-in-time coverage and an untouched future holdout.",
        },
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slate", required=True)
    p.add_argument("--plays-json", action="append", default=[])
    p.add_argument("--games-json", action="append", default=[])
    p.add_argument("--as-of")
    p.add_argument("--revision")
    p.add_argument("--source-manifest")
    p.add_argument("--context-json")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    as_of = _time(args.as_of) if args.as_of else datetime.now(timezone.utc)
    if as_of is None:
        raise SystemExit("--as-of is not a valid ISO timestamp")
    plays, games = [], []
    for path in args.plays_json:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise SystemExit(f"{path}: expected JSON array")
        plays.extend(payload)
    for path in args.games_json:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise SystemExit(f"{path}: expected JSON array")
        games.extend(payload)

    source_manifest = []
    if args.source_manifest:
        source_manifest = json.loads(
            Path(args.source_manifest).read_text(encoding="utf-8")
        )
        if not isinstance(source_manifest, list):
            raise SystemExit("--source-manifest must be a JSON array")

    context = None
    if args.context_json:
        context = json.loads(Path(args.context_json).read_text(encoding="utf-8"))
        if not isinstance(context, dict):
            raise SystemExit("--context-json must be a JSON object")

    report = build_snapshot(
        slate=_read_slate(args.slate),
        plays=plays,
        prior_games=games,
        as_of=as_of,
        source_revision=args.revision,
        source_manifest=source_manifest,
        context=context,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    s = report["summary"]
    print(f"BR2 warehouse: {s['games']} games; fully populated={s['fullyPopulatedRows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
