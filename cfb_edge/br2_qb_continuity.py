"""Deterministic point-in-time QB continuity for S04_BR2.

The feature measures *participation stability*, not injury status:
1. identify each team's primary passer in its most recent completed game
   before decisionTime;
2. measure that passer's share of team pass attempts over the last three
   completed games;
3. qbContinuityDiff = home share - away share.

Official depth-chart/injury evidence remains a separate availability overlay.
No web article or LLM extraction is required to populate this feature.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .teams import resolve

CONTRACT = "CFB_EDGE_BR2_QB_CONTINUITY_V4"
ATTEMPT_TYPES = {"completion", "incompletion", "interception_thrown"}


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _athlete_key(row: Mapping[str, Any]) -> str | None:
    athlete_id = str(row.get("athleteId") or row.get("athlete_id") or "").strip()
    name = str(row.get("athleteName") or row.get("athlete_name") or "").strip()
    if athlete_id:
        return "id:" + athlete_id
    if name:
        return "name:" + _key(name)
    return None


def _athlete_name(row: Mapping[str, Any]) -> str | None:
    value = str(row.get("athleteName") or row.get("athlete_name") or "").strip()
    return value or None


def _manifest_ok(
    manifest: Sequence[Mapping[str, Any]],
    *,
    decision_time: datetime,
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    rows = [r for r in manifest if r.get("kind") in {"play_stats", "games"}]
    if not any(r.get("kind") == "play_stats" for r in rows):
        errors.append("PLAY_STATS_MANIFEST_MISSING")
    if not any(r.get("kind") == "games" for r in rows):
        errors.append("GAMES_MANIFEST_MISSING")
    for row in rows:
        ts = _time(row.get("retrievedAt"))
        sha = str(row.get("sha256") or "")
        if ts is None or ts > decision_time:
            errors.append("SOURCE_AFTER_DECISION_OR_UNTIMESTAMPED")
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha.lower()):
            errors.append("SOURCE_HASH_INVALID")
        if not row.get("path"):
            errors.append("SOURCE_PATH_MISSING")
    return not errors, sorted(set(errors))


def _game_index(games: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out = {}
    for row in games:
        gid = str(row.get("id") or row.get("gameId") or row.get("game_id") or "").strip()
        if gid:
            out[gid] = row
    return out


def _slate_teams(slate: Sequence[Mapping[str, Any]]) -> set[str]:
    out: set[str] = set()
    for row in slate:
        game = str(row.get("game") or "")
        if "@" in game:
            a, h = (x.strip() for x in game.split("@", 1))
            if a: out.add(a)
            if h: out.add(h)
    return out


def build(
    *,
    slate: Sequence[Mapping[str, Any]],
    play_stats: Sequence[Mapping[str, Any]],
    prior_games: Sequence[Mapping[str, Any]],
    decision_time: datetime,
    source_manifest: Sequence[Mapping[str, Any]],
    lookback_games: int = 3,
) -> dict[str, Any]:
    if decision_time.tzinfo is None:
        raise ValueError("decision_time must be timezone-aware")
    known = _slate_teams(slate)
    games = _game_index(prior_games)
    manifest_ok, manifest_errors = _manifest_ok(source_manifest, decision_time=decision_time)

    # team -> game -> athlete -> attempts
    attempts: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    names: dict[str, str] = {}
    seen: set[tuple[str, str, str, str]] = set()

    for row in play_stats:
        stat_type = _key(row.get("statType") or row.get("stat_type"))
        if stat_type not in ATTEMPT_TYPES:
            continue
        gid = str(row.get("gameId") or row.get("game_id") or "").strip()
        game = games.get(gid)
        if not gid or game is None:
            continue
        start = _time(game.get("startDate") or game.get("start_date"))
        if start is None or start >= decision_time:
            continue
        if game.get("completed") is False:
            continue
        team_raw = str(row.get("team") or "").strip()
        team = resolve(team_raw, known)
        if not team:
            continue
        athlete = _athlete_key(row)
        if not athlete:
            continue
        play_id = str(row.get("playId") or row.get("play_id") or "").strip()
        dedup = (gid, play_id, team, athlete)
        if dedup in seen:
            continue
        seen.add(dedup)
        attempts[team][gid][athlete] += 1
        name = _athlete_name(row)
        if name:
            names[athlete] = name

    team_rows: dict[str, dict[str, Any]] = {}
    for team in sorted(known):
        team_games = []
        for gid, by_player in attempts.get(team, {}).items():
            game = games.get(gid)
            start = _time((game or {}).get("startDate") or (game or {}).get("start_date"))
            total = sum(by_player.values())
            if start is not None and total > 0:
                team_games.append((start, gid, dict(by_player), total))
        team_games.sort(key=lambda x: (x[0], x[1]))
        if not team_games:
            continue
        latest = team_games[-1]
        max_attempts = max(latest[2].values())
        leaders = sorted(k for k, v in latest[2].items() if v == max_attempts)
        exclusions = list(manifest_errors)
        if len(leaders) != 1:
            exclusions.append("LATEST_GAME_PRIMARY_PASSER_TIED")
            incumbent = None
        else:
            incumbent = leaders[0]

        last_n = team_games[-lookback_games:]
        total_attempts = sum(g[3] for g in last_n)
        incumbent_attempts = (
            sum(g[2].get(incumbent, 0) for g in last_n) if incumbent else 0
        )
        if total_attempts <= 0:
            exclusions.append("NO_PASS_ATTEMPTS_IN_LOOKBACK")
        share = (
            incumbent_attempts / total_attempts
            if incumbent and total_attempts > 0 and not exclusions
            else None
        )

        previous_primary = None
        if len(team_games) >= 2:
            prev = team_games[-2]
            prev_max = max(prev[2].values())
            prev_leaders = [k for k, v in prev[2].items() if v == prev_max]
            if len(prev_leaders) == 1:
                previous_primary = prev_leaders[0]

        season_total = sum(g[3] for g in team_games)
        season_incumbent = (
            sum(g[2].get(incumbent, 0) for g in team_games) if incumbent else 0
        )
        team_rows[team] = {
            "team": team,
            "auditGrade": share is not None and manifest_ok,
            "currentQbMethod": "LATEST_COMPLETED_GAME_PRIMARY_PASSER",
            "currentQbKey": incumbent,
            "currentQbName": names.get(incumbent) if incumbent else None,
            "lastGameAttemptShare": (
                max_attempts / latest[3] if incumbent and latest[3] else None
            ),
            "last3AttemptShare": share,
            "seasonAttemptShare": (
                season_incumbent / season_total if incumbent and season_total else None
            ),
            "qbChangedFromPreviousGame": (
                previous_primary is not None and incumbent is not None and previous_primary != incumbent
            ),
            "gamesUsed": [
                {
                    "gameId": gid,
                    "startDate": start.isoformat(),
                    "teamAttempts": total,
                    "incumbentAttempts": by_player.get(incumbent, 0) if incumbent else 0,
                }
                for start, gid, by_player, total in last_n
            ],
            "exclusions": sorted(set(exclusions)),
        }

    rows = []
    for raw in slate:
        game = str(raw.get("game") or "").strip()
        if "@" not in game:
            continue
        away, home = (x.strip() for x in game.split("@", 1))
        h = team_rows.get(home)
        a = team_rows.get(away)
        exclusions = []
        if not h or not h.get("auditGrade"):
            exclusions.append("HOME_QB_CONTINUITY_UNAVAILABLE")
        if not a or not a.get("auditGrade"):
            exclusions.append("AWAY_QB_CONTINUITY_UNAVAILABLE")
        feature = None
        if not exclusions:
            feature = float(h["last3AttemptShare"]) - float(a["last3AttemptShare"])
        row = {
            "game": game,
            "kickoff": raw.get("kickoff"),
            "decisionTime": decision_time.isoformat(),
            "auditGrade": not exclusions,
            "featureValue": feature,
            "home": h,
            "away": a,
            "exclusions": exclusions,
        }
        row["rowSha256"] = hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        rows.append(row)

    return {
        "schemaVersion": 4,
        "contract": CONTRACT,
        "status": "DATA_COLLECTION_ONLY",
        "generatedAt": decision_time.isoformat(),
        "decisionEffect": "NONE",
        "definition": (
            "home minus away share of prior-three-game team pass attempts taken by "
            "the primary passer from each team's most recent completed game"
        ),
        "officialAvailabilityOverlay": "SEPARATE_NOT_IN_FEATURE",
        "summary": {
            "games": len(rows),
            "auditGradeGameRows": sum(1 for r in rows if r["auditGrade"]),
            "teams": len(known),
            "auditGradeTeams": sum(1 for r in team_rows.values() if r["auditGrade"]),
        },
        "teams": [team_rows[k] for k in sorted(team_rows)],
        "rows": rows,
    }


def _load_many(paths: Sequence[str]) -> list[dict[str, Any]]:
    out = []
    for path in paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise SystemExit(f"{path}: expected JSON array")
        out.extend(payload)
    return out


def _read_slate(path: str) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slate", required=True)
    p.add_argument("--play-stats-json", action="append", default=[])
    p.add_argument("--games-json", action="append", default=[])
    p.add_argument("--source-manifest", required=True)
    p.add_argument("--decision-time", required=True)
    p.add_argument("--lookback-games", type=int, default=3)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    decision = _time(args.decision_time)
    if decision is None:
        raise SystemExit("invalid --decision-time")
    manifest = json.loads(Path(args.source_manifest).read_text(encoding="utf-8"))
    if not isinstance(manifest, list):
        raise SystemExit("--source-manifest must be a JSON array")
    report = build(
        slate=_read_slate(args.slate),
        play_stats=_load_many(args.play_stats_json),
        prior_games=_load_many(args.games_json),
        decision_time=decision,
        source_manifest=manifest,
        lookback_games=args.lookback_games,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
