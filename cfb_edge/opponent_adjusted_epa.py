"""Replayable opponent-adjusted EPA fallback from cfbfastR play-by-play.

CFBD WEPA remains the preferred registered provider. This module is used only
when that source is unavailable. It consumes cfbfastR play-level EPA for
completed weeks strictly before the target cohort and fits a fixed two-way
regularized adjustment:

    play EPA = global mean + offense effect + defense-allowed effect

Effects are estimated by alternating weighted ridge means with a preregistered
50-play shrinkage constant. Positive defense effect means more EPA allowed
(worse defense). Team net EPA = offense effect - defense effect.

The exact used-row subset is archived so the feature is replayable.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .point_in_time import instant

CONTRACT = "CFB_EDGE_OA_EPA_V1"
RIDGE_EQUIVALENT_PLAYS = 50.0
ITERATIONS = 50
USED_FIELDS = (
    "year","season","week","game_id","id_play","pos_team","def_pos_team",
    "EPA","offense_play","completed"
)


def _num(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _truthy(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"1","true","t","yes","y"}:
        return True
    if text in {"0","false","f","no","n"}:
        return False
    return None


def fit_csv(
    path: str | Path,
    *,
    season: int,
    through_week: int,
    archive_used_dir: str | Path,
    source_url: str,
    source_sha256: str,
    retrieved_at: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    retrieved = instant(retrieved_at)
    if not retrieved:
        raise ValueError("retrieved_at must be timezone-aware")
    pair_sum: dict[tuple[str,str], float] = defaultdict(float)
    pair_n: dict[tuple[str,str], int] = defaultdict(int)
    total = 0.0
    n = 0

    archive_dir = Path(archive_used_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", newline="", encoding="utf-8", delete=False) as tmp:
        temp_path = Path(tmp.name)
        writer = csv.DictWriter(tmp, fieldnames=USED_FIELDS, extrasaction="ignore")
        writer.writeheader()
        with Path(path).open(newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh)
            required = {"week","pos_team","def_pos_team","EPA"}
            if not required.issubset(set(reader.fieldnames or [])):
                raise ValueError(f"cfbfastR schema missing {sorted(required-set(reader.fieldnames or []))}")
            for row in reader:
                row_season = row.get("year") or row.get("season")
                try:
                    if row_season not in (None, "") and int(float(row_season)) != season:
                        continue
                    week = int(float(row.get("week") or 0))
                except (TypeError, ValueError):
                    continue
                if week < 1 or week > through_week:
                    continue
                completed = _truthy(row.get("completed"))
                if completed is False:
                    continue
                offense_play = _truthy(row.get("offense_play"))
                if offense_play is False:
                    continue
                off = str(row.get("pos_team") or "").strip()
                deff = str(row.get("def_pos_team") or "").strip()
                value = _num(row.get("EPA"))
                if not off or not deff or value is None:
                    continue
                pair = (off, deff)
                pair_sum[pair] += value
                pair_n[pair] += 1
                total += value
                n += 1
                writer.writerow({k: row.get(k, "") for k in USED_FIELDS})

    if n == 0:
        temp_path.unlink(missing_ok=True)
        raise ValueError("no eligible cfbfastR EPA rows")

    raw = temp_path.read_bytes()
    subset_sha = hashlib.sha256(raw).hexdigest()
    archive_path = archive_dir / f"{subset_sha}.csv.gz"
    if not archive_path.exists():
        with archive_path.open("wb") as fh:
            with gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0) as z:
                z.write(raw)
    temp_path.unlink(missing_ok=True)

    teams = sorted({x for pair in pair_n for x in pair})
    global_mean = total / n
    offense = {t: 0.0 for t in teams}
    defense = {t: 0.0 for t in teams}

    for _ in range(ITERATIONS):
        next_off = {}
        for team in teams:
            num = 0.0
            count = 0
            for (off, opp), cnt in pair_n.items():
                if off != team:
                    continue
                num += pair_sum[(off, opp)] - cnt * (global_mean + defense.get(opp, 0.0))
                count += cnt
            next_off[team] = num / (count + RIDGE_EQUIVALENT_PLAYS) if count else 0.0
        offense = next_off

        next_def = {}
        for team in teams:
            num = 0.0
            count = 0
            for (opp, deff), cnt in pair_n.items():
                if deff != team:
                    continue
                num += pair_sum[(opp, deff)] - cnt * (global_mean + offense.get(opp, 0.0))
                count += cnt
            next_def[team] = num / (count + RIDGE_EQUIVALENT_PLAYS) if count else 0.0
        defense = next_def

    rows = []
    for team in teams:
        off_plays = sum(cnt for (off,_),cnt in pair_n.items() if off == team)
        def_plays = sum(cnt for (_,deff),cnt in pair_n.items() if deff == team)
        rows.append({
            "team": team,
            "epa": {"total": offense[team]},
            "epaAllowed": {"total": defense[team]},
            "netOpponentAdjustedEpa": offense[team] - defense[team],
            "offensePlays": off_plays,
            "defensePlays": def_plays,
            "_meta": {
                "contract": CONTRACT,
                "provider": "cfbfastR",
                "throughWeek": through_week,
                "ridgeEquivalentPlays": RIDGE_EQUIVALENT_PLAYS,
                "iterations": ITERATIONS,
            },
        })

    meta = {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "status": "DATA_COLLECTION_ONLY",
        "generatedAt": retrieved.isoformat(),
        "season": season,
        "throughWeek": through_week,
        "eligiblePlays": n,
        "teams": len(rows),
        "globalMeanEpa": global_mean,
        "algorithm": "TWO_WAY_ALTERNATING_RIDGE",
        "ridgeEquivalentPlays": RIDGE_EQUIVALENT_PLAYS,
        "iterations": ITERATIONS,
        "source": {
            "provider": "sportsdataverse/cfbfastR",
            "url": source_url,
            "sourceSha256": source_sha256,
            "retrievedAt": retrieved.isoformat(),
        },
        "archive": {
            "sha256": subset_sha,
            "bytes": len(raw),
            "path": str(archive_path),
            "format": "canonical-selected-rows-csv.gz",
        },
    }
    return rows, meta


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", required=True)
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--through-week", type=int, required=True)
    p.add_argument("--archive-used-dir", required=True)
    p.add_argument("--source-url", required=True)
    p.add_argument("--source-sha256", required=True)
    p.add_argument("--retrieved-at", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--meta-out", required=True)
    a = p.parse_args(argv)
    rows, meta = fit_csv(
        a.csv, season=a.season, through_week=a.through_week,
        archive_used_dir=a.archive_used_dir, source_url=a.source_url,
        source_sha256=a.source_sha256, retrieved_at=a.retrieved_at,
    )
    Path(a.out).write_text(json.dumps(rows, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    Path(a.meta_out).write_text(json.dumps(meta, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    print(json.dumps({
        "teams": meta["teams"], "eligiblePlays": meta["eligiblePlays"],
        "throughWeek": meta["throughWeek"], "archiveSha256": meta["archive"]["sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
