"""SportsDataverse weekly opponent-adjusted EPA adapter for S04_BR2.

The source is the cfbfastR/SportsDataverse weekly team summary release. The
cohort fixes one source provider and one through_week before outcomes. Raw CSV
bytes are archived and hashed before feature construction.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .teams import resolve

CONTRACT = "CFB_EDGE_BR2_ADJUSTED_EPA_V1"


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _slate_teams(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    out: set[str] = set()
    for row in rows:
        game = str(row.get("game") or "")
        if "@" in game:
            a, h = (x.strip() for x in game.split("@", 1))
            if a: out.add(a)
            if h: out.add(h)
    return out


def _manifest_row(
    manifest: Sequence[Mapping[str, Any]], decision_time: datetime
) -> tuple[Mapping[str, Any] | None, list[str]]:
    rows = [r for r in manifest if r.get("kind") == "sdv_adjusted_epa_weekly"]
    errors = []
    if len(rows) != 1:
        errors.append("ADJUSTED_EPA_SOURCE_MANIFEST_NOT_UNIQUE")
        return None, errors
    row = rows[0]
    ts = _time(row.get("retrievedAt"))
    sha = str(row.get("sha256") or "").lower()
    if ts is None or ts > decision_time:
        errors.append("ADJUSTED_EPA_SOURCE_AFTER_DECISION_OR_UNTIMESTAMPED")
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        errors.append("ADJUSTED_EPA_SOURCE_HASH_INVALID")
    if not row.get("path"):
        errors.append("ADJUSTED_EPA_SOURCE_PATH_MISSING")
    return row, errors


def build(
    *,
    csv_rows: Sequence[Mapping[str, Any]],
    slate: Sequence[Mapping[str, Any]],
    season: int,
    through_week: int,
    decision_time: datetime,
    source_manifest: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    known = _slate_teams(slate)
    source, source_errors = _manifest_row(source_manifest, decision_time)
    selected: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    unresolved: set[str] = set()

    for row in csv_rows:
        try:
            row_season = int(float(str(row.get("season") or "0")))
            row_week = int(float(str(row.get("through_week") or "0")))
        except ValueError:
            continue
        if row_season != season or row_week != through_week:
            continue
        raw_team = str(
            row.get("pos_team")
            or row.get("team")
            or row.get("school")
            or row.get("team_name")
            or ""
        ).strip()
        team = resolve(raw_team, known)
        if not team:
            if raw_team:
                unresolved.add(raw_team)
            continue
        off = _num(row.get("adj_off_epa"))
        deff = _num(row.get("adj_def_epa"))
        net = _num(row.get("net_adj_epa"))
        if net is None and off is not None and deff is not None:
            net = off - deff
        if off is None or deff is None or net is None:
            continue
        payload = {
            "team": team,
            "providerTeam": raw_team,
            "throughWeek": through_week,
            "epa": {"total": off},
            "epaAllowed": {"total": deff},
            "netAdjustedEpa": net,
            "source": "sportsdataverse_cfb_team_summaries_weekly",
            "decisionTime": decision_time.isoformat(),
            "auditGrade": not source_errors,
        }
        if team in selected:
            duplicates.add(team)
        else:
            selected[team] = payload

    for team in duplicates:
        selected.pop(team, None)

    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "status": "DATA_COLLECTION_ONLY",
        "generatedAt": decision_time.isoformat(),
        "decisionEffect": "NONE",
        "season": season,
        "throughWeek": through_week,
        "source": {
            "provider": "SportsDataverse/cfbfastR",
            "dataset": "cfb_team_summaries_weekly",
            "manifest": source,
            "errors": source_errors,
        },
        "summary": {
            "slateTeams": len(known),
            "auditGradeTeams": sum(1 for r in selected.values() if r["auditGrade"]),
            "unresolvedProviderTeams": sorted(unresolved),
            "duplicateResolvedTeams": sorted(duplicates),
        },
        "rows": [selected[k] for k in sorted(selected)],
    }


def _read_slate(path: str) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", required=True)
    p.add_argument("--slate", required=True)
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--through-week", type=int, required=True)
    p.add_argument("--decision-time", required=True)
    p.add_argument("--source-manifest", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    decision = _time(args.decision_time)
    if decision is None:
        raise SystemExit("invalid --decision-time")
    with Path(args.csv).open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    manifest = json.loads(Path(args.source_manifest).read_text(encoding="utf-8"))
    if not isinstance(manifest, list):
        raise SystemExit("--source-manifest must be a JSON array")
    report = build(
        csv_rows=rows,
        slate=_read_slate(args.slate),
        season=args.season,
        through_week=args.through_week,
        decision_time=decision,
        source_manifest=manifest,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
