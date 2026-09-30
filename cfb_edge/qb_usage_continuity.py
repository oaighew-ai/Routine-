"""Deterministic QB continuity from completed-game passing usage.

The base feature does not require historical depth-chart PDFs. For each team,
the incumbent is the passing-attempt leader in its most recent completed game.
Continuity is that QB's share of team pass attempts across the team's last
three completed games. A validated official pregame starter can override the
incumbent only when it maps unambiguously to the captured participation data.

This module is collection-only and cannot create picks or change S04_ES2.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .point_in_time import instant, known_at, norm, source_time

CONTRACT = "CFB_EDGE_BR2_QB_CONTINUITY_V4"


def _num(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _attempts(stat: Any) -> float | None:
    if stat in (None, ""):
        return None
    text = str(stat).strip()
    for sep in ("/", "-"):
        if sep in text:
            parts = [p.strip() for p in text.split(sep)]
            if len(parts) == 2:
                return _num(parts[1])
    return _num(text)


def _passing_attempts(team_row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return athlete attempts from a CFBD /games/players team object."""
    for category in team_row.get("categories") or []:
        if norm(category.get("name")) != "passing":
            continue
        # Prefer a direct attempts type. Fall back to completions/attempts.
        direct = []
        combo = []
        for typ in category.get("types") or []:
            name = norm(typ.get("name")).replace(" ", "")
            target = direct if name in {"att", "attempts", "passingattempts"} else combo
            if target is combo and not (
                "c/att" in name or "cmp/att" in name or "completions/attempts" in name
            ):
                continue
            for athlete in typ.get("athletes") or []:
                value = _attempts(athlete.get("stat"))
                if value is None:
                    continue
                target.append({
                    "athleteId": str(athlete.get("id") or "").strip() or None,
                    "athleteName": str(athlete.get("name") or "").strip(),
                    "attempts": value,
                })
        return direct or combo
    return []


def _game_index(games: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(g.get("id")): g for g in games if g.get("id") is not None}


def _team_game_rows(
    games: Sequence[Mapping[str, Any]],
    boxes: Sequence[Mapping[str, Any]],
    *,
    as_of: datetime,
) -> dict[str, list[dict[str, Any]]]:
    by_game = _game_index(games)
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for box in boxes:
        gid = str(box.get("id") or "")
        game = by_game.get(gid)
        if not game:
            continue
        start = instant(game.get("startDate"))
        if not start or start >= as_of:
            continue
        if game.get("completed") is False:
            continue
        for team in box.get("teams") or []:
            name = str(team.get("team") or "").strip()
            if not name:
                continue
            passers = _passing_attempts(team)
            total = sum(float(p["attempts"]) for p in passers)
            if total <= 0:
                continue
            out[norm(name)].append({
                "gameId": gid,
                "startDate": start.isoformat(),
                "team": name,
                "passers": passers,
                "teamAttempts": total,
            })
    for rows in out.values():
        rows.sort(key=lambda r: instant(r["startDate"]) or datetime.min.replace(tzinfo=timezone.utc))
    return out


def _official_by_team(payload: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in (payload or {}).get("rows") or []:
        if row.get("auditGrade") is not True:
            continue
        for side in ("home", "away"):
            packet = row.get(side)
            if not isinstance(packet, Mapping):
                continue
            team = str(packet.get("team") or "").strip()
            name = str(packet.get("firstListedQb") or packet.get("expectedQbName") or "").strip()
            if team and name:
                out[norm(team)] = {
                    "team": team,
                    "qbName": name,
                    "retrievedAt": packet.get("retrievedAt"),
                    "sourceUrl": packet.get("sourceUrl"),
                    "packetSha256": packet.get("packetSha256"),
                }
    return out


def _select_qb(
    recent: Sequence[Mapping[str, Any]],
    official: Mapping[str, Any] | None,
) -> tuple[str | None, str | None, str, bool, list[str]]:
    all_passers: dict[str, dict[str, Any]] = {}
    for game in recent:
        for p in game["passers"]:
            key = norm(p.get("athleteName"))
            if not key:
                continue
            slot = all_passers.setdefault(key, {
                "name": p.get("athleteName"),
                "id": p.get("athleteId"),
                "attempts": 0.0,
            })
            slot["attempts"] += float(p["attempts"])

    notes: list[str] = []
    if official:
        key = norm(official.get("qbName"))
        match = all_passers.get(key)
        if match:
            return match["id"], match["name"], "OFFICIAL_PREGAME_OVERRIDE", True, notes
        notes.append("OFFICIAL_QB_NOT_MAPPED_TO_RECENT_PARTICIPATION")

    if not recent:
        return None, None, "NONE", False, notes
    latest = recent[-1]
    if not latest["passers"]:
        return None, None, "NONE", False, notes
    ranked = sorted(
        latest["passers"],
        key=lambda p: (
            float(p["attempts"]),
            all_passers.get(norm(p.get("athleteName")), {}).get("attempts", 0.0),
            str(p.get("athleteId") or ""),
            str(p.get("athleteName") or ""),
        ),
        reverse=True,
    )
    lead = ranked[0]
    return (
        lead.get("athleteId"),
        lead.get("athleteName"),
        "LAST_COMPLETED_GAME_ATTEMPT_LEADER",
        False,
        notes,
    )


def build(
    *,
    slate: Sequence[Mapping[str, Any]],
    prior_games: Sequence[Mapping[str, Any]],
    player_boxes: Sequence[Mapping[str, Any]],
    source_manifest: Sequence[Mapping[str, Any]],
    as_of: datetime,
    official_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    history = _team_game_rows(prior_games, player_boxes, as_of=as_of)
    official = _official_by_team(official_evidence)
    team_rows: dict[str, dict[str, Any]] = {}

    teams = set()
    for row in slate:
        game = str(row.get("game") or "")
        if "@" in game:
            away, home = (x.strip() for x in game.split("@", 1))
            teams.update((away, home))

    # Player-box captures are the minimum evidence required for this feature.
    for team in sorted(teams):
        rows = history.get(norm(team), [])
        recent = rows[-3:]
        exclusions: list[str] = []
        if not recent:
            exclusions.append("NO_COMPLETED_PASSING_HISTORY")
        qid, qname, source, verified, notes = _select_qb(recent, official.get(norm(team)))
        if not qname:
            exclusions.append("INCUMBENT_QB_UNRESOLVED")
        numerator = 0.0
        denominator = sum(float(g["teamAttempts"]) for g in recent)
        if qname:
            qkey = norm(qname)
            for game in recent:
                numerator += sum(
                    float(p["attempts"]) for p in game["passers"]
                    if norm(p.get("athleteName")) == qkey
                )
        if denominator <= 0:
            exclusions.append("TEAM_PASS_ATTEMPTS_ZERO")
        continuity = None if exclusions else numerator / denominator
        team_rows[norm(team)] = {
            "team": team,
            "selectedQbId": qid,
            "selectedQbName": qname,
            "starterSource": source,
            "availabilityVerified": verified,
            "continuity": continuity,
            "qbAttemptsLast3": numerator if denominator > 0 else None,
            "teamAttemptsLast3": denominator if denominator > 0 else None,
            "gamesConsidered": [g["gameId"] for g in recent],
            "gameCount": len(recent),
            "notes": notes,
            "exclusions": exclusions,
        }

    rows = []
    for raw in slate:
        game = str(raw.get("game") or "").strip()
        kickoff = instant(raw.get("kickoff"))
        if "@" not in game or not kickoff:
            continue
        away, home = (x.strip() for x in game.split("@", 1))
        h, a = team_rows.get(norm(home)), team_rows.get(norm(away))
        exclusions = []
        if not h or h.get("continuity") is None:
            exclusions.append("HOME_QB_CONTINUITY_UNAVAILABLE")
        if not a or a.get("continuity") is None:
            exclusions.append("AWAY_QB_CONTINUITY_UNAVAILABLE")
        base_as_of = source_time(
            source_manifest, ("player_box",), as_of.isoformat(), kickoff.isoformat()
        )
        official_times = []
        for team in (home, away):
            o = official.get(norm(team))
            if o and team_rows.get(norm(team), {}).get("starterSource") == "OFFICIAL_PREGAME_OVERRIDE":
                t = o.get("retrievedAt")
                if known_at(t, as_of.isoformat(), kickoff.isoformat()):
                    official_times.append(t)
                else:
                    exclusions.append("OFFICIAL_QB_EVIDENCE_NOT_KNOWN_AT_DECISION")
        times = [t for t in [base_as_of, *official_times] if t]
        feature_as_of = max(times, key=instant) if times else None
        if not base_as_of:
            exclusions.append("PLAYER_BOX_LINEAGE_INVALID")
        value = None
        if not exclusions:
            value = float(h["continuity"]) - float(a["continuity"])
        rows.append({
            "game": game,
            "kickoff": kickoff.isoformat(),
            "decisionTime": as_of.isoformat(),
            "featureAsOf": feature_as_of,
            "auditGrade": not exclusions,
            "featureValue": value,
            "home": h,
            "away": a,
            "exclusions": sorted(set(exclusions)),
        })

    return {
        "schemaVersion": 4,
        "contract": CONTRACT,
        "status": "DATA_COLLECTION_ONLY",
        "generatedAt": as_of.isoformat(),
        "decisionEffect": "NONE",
        "definition": (
            "home minus away incumbent-QB passing-attempt share across each team's "
            "last three completed games; incumbent is last-game attempt leader unless "
            "a validated official pregame starter maps to captured participation"
        ),
        "summary": {
            "games": len(rows),
            "auditGradeGameRows": sum(1 for r in rows if r["auditGrade"]),
            "officialStarterOverrides": sum(
                1 for r in team_rows.values()
                if r.get("starterSource") == "OFFICIAL_PREGAME_OVERRIDE"
            ),
            "teamsWithContinuity": sum(
                1 for r in team_rows.values() if r.get("continuity") is not None
            ),
        },
        "rows": rows,
    }


def _list(path: str | Path) -> list[dict[str, Any]]:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj, list):
        raise SystemExit(f"{path}: expected JSON array")
    return obj


def _slate(path: str | Path) -> list[dict[str, str]]:
    import csv
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _obj(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    obj = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise SystemExit(f"{path}: expected JSON object")
    return obj


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slate", required=True)
    p.add_argument("--games-json", action="append", required=True)
    p.add_argument("--player-box-json", action="append", required=True)
    p.add_argument("--source-manifest", required=True)
    p.add_argument("--official-evidence")
    p.add_argument("--as-of", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    games = [row for path in a.games_json for row in _list(path)]
    boxes = [row for path in a.player_box_json for row in _list(path)]
    as_of = instant(a.as_of)
    if not as_of:
        raise SystemExit("invalid --as-of")
    report = build(
        slate=_slate(a.slate),
        prior_games=games,
        player_boxes=boxes,
        source_manifest=_list(a.source_manifest),
        as_of=as_of,
        official_evidence=_obj(a.official_evidence),
    )
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
