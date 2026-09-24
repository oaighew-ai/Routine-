"""Deterministic point-in-time context builder for S04_BR2.

This module combines only replayable, timestamped evidence:
- CFBD opponent-adjusted EPA snapshot
- CFBD week-bounded advanced line-play statistics
- CFBD team/venue coordinates
- Open-Meteo point-in-time kickoff forecast
- separately validated QB continuity evidence

It cannot create a pick or change S04_ES2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .point_in_time import instant, known_at, source_time, resolve_game, unique_index

CONTRACT = "CFB_EDGE_BR2_CONTEXT_V1"
EARTH_RADIUS_MILES = 3958.7613


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("&", "and").split())


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def _index_team_locations(teams: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in teams:
        school = str(row.get("school") or "").strip()
        loc = row.get("location") or {}
        lat, lon = _num(loc.get("latitude")), _num(loc.get("longitude"))
        if not school or lat is None or lon is None:
            continue
        payload = {
            "team": school,
            "latitude": lat,
            "longitude": lon,
            "timezone": loc.get("timezone"),
            "venueId": loc.get("id"),
            "venueName": loc.get("name"),
        }
        for name in [school, *(row.get("alternateNames") or []), row.get("abbreviation")]:
            if str(name or "").strip():
                out[_norm(name)] = payload
    return out


def _index_venues(venues: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for row in venues:
        lat, lon = _num(row.get("latitude")), _num(row.get("longitude"))
        if lat is None or lon is None:
            continue
        payload = {
            "venueId": row.get("id"),
            "venueName": row.get("name"),
            "latitude": lat,
            "longitude": lon,
            "timezone": row.get("timezone"),
            "dome": row.get("dome") is True,
            "elevation": row.get("elevation"),
        }
        if row.get("id") is not None:
            by_id[str(row["id"])] = payload
        if str(row.get("name") or "").strip():
            by_name[_norm(row["name"])] = payload
    return {"byId": by_id, "byName": by_name}


def _game_venue(
    game: Mapping[str, Any],
    venue_index: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> Mapping[str, Any] | None:
    for key in ("venueId", "venue_id"):
        if game.get(key) is not None:
            hit = venue_index["byId"].get(str(game[key]))
            if hit:
                return hit
    name = game.get("venue")
    return venue_index["byName"].get(_norm(name)) if name else None


def _index_games(games: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out = {}
    for row in games:
        away = str(row.get("awayTeam") or row.get("away_team") or "").strip()
        home = str(row.get("homeTeam") or row.get("home_team") or "").strip()
        if away and home:
            out[_norm(f"{away} @ {home}")] = row
    return out


def _wepa_index(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {
        _norm(r.get("team")): r
        for r in rows
        if str(r.get("team") or "").strip()
    }


def _advanced_index(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {
        _norm(r.get("team")): r
        for r in rows
        if str(r.get("team") or "").strip()
    }


def _net_epa(row: Mapping[str, Any] | None) -> float | None:
    if not row:
        return None
    offense = row.get("epa") or {}
    allowed = row.get("epaAllowed") or {}
    own, opp = _num(offense.get("total")), _num(allowed.get("total"))
    if own is None or opp is None:
        return None
    return own - opp


def _line_yards(row: Mapping[str, Any] | None, side: str) -> float | None:
    if not row:
        return None
    block = row.get(side) or {}
    return _num(block.get("lineYards"))


def _line_team_net(row: Mapping[str, Any] | None) -> float | None:
    off = _line_yards(row, "offense")
    deff = _line_yards(row, "defense")
    if off is None or deff is None:
        return None
    return off - deff


def _line_aux(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    off, deff = row.get("offense") or {}, row.get("defense") or {}
    return {
        "offLineYards": _num(off.get("lineYards")),
        "defLineYardsAllowed": _num(deff.get("lineYards")),
        "offStuffRate": _num(off.get("stuffRate")),
        "defStuffRateAllowed": _num(deff.get("stuffRate")),
        "offPowerSuccess": _num(off.get("powerSuccess")),
        "defPowerSuccessAllowed": _num(deff.get("powerSuccess")),
        "offFrontSevenHavocAllowed": _num((off.get("havoc") or {}).get("frontSeven")),
        "defFrontSevenHavocCreated": _num((deff.get("havoc") or {}).get("frontSeven")),
    }


def _weather_index(payload):
    return unique_index((payload or {}).get("rows") or [], lambda r: _norm(r.get("game")))


def _qb_index(payload):
    return unique_index((payload or {}).get("rows") or [], lambda r: _norm(r.get("game")))


def build_context(
    *,
    slate: Sequence[Mapping[str, Any]],
    week_games: Sequence[Mapping[str, Any]],
    teams: Sequence[Mapping[str, Any]],
    venues: Sequence[Mapping[str, Any]],
    wepa: Sequence[Mapping[str, Any]],
    advanced: Sequence[Mapping[str, Any]],
    weather: Mapping[str, Any] | None,
    qb_evidence: Mapping[str, Any] | None,
    as_of: datetime,
    source_revision: str | None = None,
    source_manifest: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    games = _index_games(week_games)
    team_locs = _index_team_locations(teams)
    venue_idx = _index_venues(venues)
    wepa_idx = _wepa_index(wepa)
    adv_idx = _advanced_index(advanced)
    weather_idx = _weather_index(weather)
    qb_idx = _qb_index(qb_evidence)
    rows = []

    for raw in slate:
        game = str(raw.get("game") or "").strip()
        if "@" not in game:
            continue
        away, home = (x.strip() for x in game.split("@", 1))
        kickoff = _time(raw.get("kickoff"))
        identity = resolve_game(raw, week_games)
        source_game = next((g for g in week_games if identity and str(g.get("id")) == identity["providerIds"]["cfbd"]), None)
        audit: dict[str, Any] = {}
        pregame_eligible = kickoff is not None and as_of < kickoff
        features = {
            "epaDiff": None,
            "qbContinuityDiff": None,
            "linePlayDiff": None,
            "travelMilesDiff": None,
            "windMph": None,
        }
        evidence: dict[str, Any] = {}

        if not pregame_eligible:
            audit = {
                "epaDiff": False,
                "qbContinuityDiff": False,
                "linePlayDiff": False,
                "travelMilesDiff": False,
                "windMph": False,
            }
        else:
            # Opponent-adjusted EPA. The API snapshot itself is frozen prospectively.
            he, ae = _net_epa(wepa_idx.get(_norm(home))), _net_epa(wepa_idx.get(_norm(away)))
            if he is not None and ae is not None:
                features["epaDiff"] = he - ae
                audit["epaDiff"] = True
                evidence["epa"] = {"homeNet": he, "awayNet": ae}
            else:
                audit["epaDiff"] = False

            # Line play: a single registered unit-consistent statistic, net line yards.
            hl, al = _line_team_net(adv_idx.get(_norm(home))), _line_team_net(adv_idx.get(_norm(away)))
            if hl is not None and al is not None:
                features["linePlayDiff"] = hl - al
                audit["linePlayDiff"] = True
                evidence["linePlay"] = {
                    "definition": "(offensive lineYards - defensive lineYardsAllowed), home minus away",
                    "homeNetLineYards": hl,
                    "awayNetLineYards": al,
                    "homeAux": _line_aux(adv_idx.get(_norm(home))),
                    "awayAux": _line_aux(adv_idx.get(_norm(away))),
                }
            else:
                audit["linePlayDiff"] = False

            # Travel: straight-line distance from registered program home location to game venue.
            venue = _game_venue(source_game or {}, venue_idx) if source_game else None
            hloc, aloc = team_locs.get(_norm(home)), team_locs.get(_norm(away))
            if venue and hloc and aloc:
                home_miles = haversine_miles(
                    hloc["latitude"], hloc["longitude"], venue["latitude"], venue["longitude"]
                )
                away_miles = haversine_miles(
                    aloc["latitude"], aloc["longitude"], venue["latitude"], venue["longitude"]
                )
                features["travelMilesDiff"] = home_miles - away_miles
                audit["travelMilesDiff"] = True
                evidence["travel"] = {
                    "definition": "great-circle program-home-location to game-venue miles; home minus away",
                    "homeMiles": home_miles,
                    "awayMiles": away_miles,
                    "venue": dict(venue),
                    "neutralSite": bool((source_game or {}).get("neutralSite")),
                }
            else:
                audit["travelMilesDiff"] = False

            # Weather: normalized by a separate capture contract.
            wr = weather_idx.get(_norm(game))
            if wr and wr.get("auditGrade") is True:
                wind = _num(wr.get("windMph"))
                if wind is not None:
                    features["windMph"] = wind
                    audit["windMph"] = True
                    evidence["weather"] = dict(wr)
                else:
                    audit["windMph"] = False
            else:
                audit["windMph"] = False

            # QB continuity: only an independently validated official-source contract may populate it.
            qr = qb_idx.get(_norm(game))
            if qr and qr.get("auditGrade") is True and qr.get("featureValue") is not None:
                features["qbContinuityDiff"] = _num(qr.get("featureValue"))
                audit["qbContinuityDiff"] = features["qbContinuityDiff"] is not None
                evidence["qbContinuity"] = dict(qr)
            else:
                audit["qbContinuityDiff"] = False

        feature_as_of = {}
        required = {"epaDiff": ("wepa",), "linePlayDiff": ("advanced_stats",),
                    "travelMilesDiff": ("teams", "venues", "week_games")}
        for feature, kinds in required.items():
            feature_as_of[feature] = source_time(source_manifest, kinds, as_of.isoformat(), raw.get("kickoff"))
        wr = weather_idx.get(_norm(game)) or {}
        feature_as_of["windMph"] = wr.get("retrievedAt") if known_at(wr.get("retrievedAt"), as_of.isoformat(), raw.get("kickoff")) and instant(wr.get("kickoff")) == kickoff else None
        qr = qb_idx.get(_norm(game)) or {}
        qb_times = [qr.get(side, {}).get(k) for side in ("home", "away")
                    for k in ("retrievedAt", "priorRetrievedAt") if isinstance(qr.get(side), dict)]
        feature_as_of["qbContinuityDiff"] = max(qb_times, key=instant) if len(qb_times) == 4 and all(known_at(t, as_of.isoformat(), raw.get("kickoff")) for t in qb_times) and instant(qr.get("kickoff")) == kickoff else None
        for feature in features:
            if not identity or not feature_as_of.get(feature):
                features[feature] = None
                audit[feature] = False
        payload = {
            "game": game,
            "canonicalGameId": identity["canonicalGameId"] if identity else None,
            "providerIds": identity["providerIds"] if identity else {},
            "decisionTime": as_of.isoformat(),
            "featureAsOf": feature_as_of,
            "identityStatus": "VERIFIED" if identity else "UNRESOLVED",
            "away": away,
            "home": home,
            "kickoff": None if kickoff is None else kickoff.isoformat(),
            "snapshotAt": as_of.isoformat(),
            "pregameEligible": pregame_eligible,
            "eligibilityExclusions": [] if pregame_eligible else [
                "KICKOFF_MISSING" if kickoff is None else "SNAPSHOT_NOT_PRE_KICKOFF"
            ],
            "features": features,
            "audit": audit,
            "evidence": evidence,
        }
        payload["rowSha256"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        rows.append(payload)

    coverage = {
        feature: sum(1 for r in rows if r["audit"].get(feature) is True)
        for feature in ("epaDiff","qbContinuityDiff","linePlayDiff","travelMilesDiff","windMph")
    }
    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "modelId": "S04_BR2",
        "status": "DATA_COLLECTION_ONLY",
        "generatedAt": as_of.isoformat(),
        "sourceRevision": source_revision,
        "week5DecisionRuleChanged": False,
        "decisionEffect": "NONE",
        "summary": {
            "games": len(rows),
            "auditGradeCoverageRows": coverage,
            "fullyContextPopulatedRows": sum(1 for r in rows if all(r["audit"].values())),
            "modelingEligible": False,
        },
        "rows": rows,
    }


def _load_list(path: str | Path) -> list[dict[str, Any]]:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj, list):
        raise SystemExit(f"{path}: expected JSON array")
    return obj


def _slate(path: str | Path) -> list[dict[str, str]]:
    import csv
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("game") or "").strip()]


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slate", required=True)
    p.add_argument("--week-games", required=True)
    p.add_argument("--teams", required=True)
    p.add_argument("--venues", required=True)
    p.add_argument("--wepa", required=True)
    p.add_argument("--advanced", required=True)
    p.add_argument("--source-manifest", required=True)
    p.add_argument("--weather")
    p.add_argument("--qb-evidence")
    p.add_argument("--as-of")
    p.add_argument("--revision")
    p.add_argument("--out", required=True)
    args=p.parse_args(argv)
    as_of=_time(args.as_of) if args.as_of else datetime.now(timezone.utc)
    if as_of is None:
        raise SystemExit("invalid --as-of")
    report=build_context(
        slate=_slate(args.slate),
        week_games=_load_list(args.week_games),
        teams=_load_list(args.teams),
        venues=_load_list(args.venues),
        wepa=_load_list(args.wepa),
        advanced=_load_list(args.advanced),
        weather=_json(args.weather),
        qb_evidence=_json(args.qb_evidence),
        as_of=as_of,
        source_revision=args.revision,
        source_manifest=_load_list(args.source_manifest),
    )
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(report["summary"],sort_keys=True))
    return 0


def _json(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    p=Path(path)
    if not p.exists():
        return None
    obj=json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise SystemExit(f"{path}: expected JSON object")
    return obj


if __name__=="__main__":
    raise SystemExit(main())
