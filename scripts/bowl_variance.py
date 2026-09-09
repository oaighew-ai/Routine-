#!/usr/bin/env python3
"""Do bowl games need their own margin distribution? Re-runnable answer.

The conclusion is in MODEL.md under "Do bowl games need their own
distribution?": no, at n = 348 postseason games the residual spread of margins
is 5.3% wider than the regular season at z = +1.34, and the seven is flat. This
script is what produced it. It exists because the answer changes as cfbfastR
extends its play-by-play coverage, which currently stops at 2021, and because
getting it wrong is easy in two specific ways this script guards against.

    python3 scripts/bowl_variance.py --cache /tmp/bowlfit

Stages are cached, so a second run is seconds. Delete the cache to refetch.

Two traps, both of which produced a confident wrong answer on the first pass:

**cfbfastR schedules contain no bowl games.** The per-season files stop in
mid-December; the 2019 file's last game is Dec 14. Nothing marks this, and
`season_type` reads `regular` on every row. Bowls are in the play-by-play
instead, under `week` values that restart at 1, so a postseason game is
identified here by its absence from the schedule.

**The play-by-play's own `homeTeamSpread` is a fill value.** It reads 2.5 on 42%
of games. Measured against it, bowls came out significantly *tighter* at
z = -3.26, the reverse of the truth, because the filler concentrated near
pick'em. Real closing numbers come from the betting file, a median of 15 books
per game. `_assert_real_lines` fails loudly rather than let that recur.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import os
import statistics as st
import subprocess
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main"
LINES_URL = f"{ROOT}/betting/csv/cfb_line_odds.csv.gz"
PBP_COLS = ["game_id", "week", "end.homeScore", "end.awayScore",
            "homeTeamName", "awayTeamName", "period.number"]


# --------------------------------------------------------------- fetching

def _get(url: str, dest: Path) -> Path:
    """Fetch with curl: urllib truncates these transfers on a flaky proxy."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["curl", "-sSf", "--retry", "5", "--retry-all-errors",
                        "--max-time", "900", "-o", str(dest), url],
                       capture_output=True)
    if r.returncode != 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"could not fetch {url}: {r.stderr.decode()[:200]}")
    return dest


def _schedule(season: int, cache: Path) -> dict[int, tuple[str, str]]:
    """game_id -> (home_division, away_division) for one season's regular games."""
    p = _get(f"{ROOT}/schedules/csv/cfb_schedules_{season}.csv",
             cache / f"sched_{season}.csv")
    out = {}
    for r in csv.DictReader(io.StringIO(p.read_text(encoding="utf-8"))):
        try:
            out[int(float(r["game_id"]))] = (r.get("home_division", "").lower(),
                                             r.get("away_division", "").lower())
        except (ValueError, TypeError):
            continue
    return out


def margins(seasons: range, cache: Path) -> list[dict]:
    """Final margin per game from play-by-play, tagged regular or postseason."""
    out_path = cache / "margins.jsonl"
    if out_path.exists():
        return [json.loads(l) for l in out_path.open()]

    import pyarrow.parquet as pq

    rows = []
    for season in seasons:
        tmp = cache / "_pbp.parquet"
        try:
            _get(f"{ROOT}/pbp/parquet/play_by_play_{season}.parquet", tmp)
        except RuntimeError as exc:
            print(f"  {season}: no play-by-play ({exc.args[0][:60]})", file=sys.stderr)
            continue
        have = set(pq.ParquetFile(tmp).schema_arrow.names)
        d = pq.read_table(tmp, columns=[c for c in PBP_COLS if c in have]).to_pydict()
        tmp.unlink()

        n = len(d["game_id"])
        games: dict[int, dict] = {}
        for i in range(n):
            gid = d["game_id"][i]
            if gid is None:
                continue
            g = games.setdefault(int(gid), {"h": 0, "a": 0, "per": 0,
                                            "home": None, "away": None})
            # Scores never decrease, so the maximum over the game is the final.
            for src, key in (("end.homeScore", "h"), ("end.awayScore", "a")):
                v = d.get(src, [None] * n)[i]
                if v is not None and v > g[key]:
                    g[key] = v
            p = d.get("period.number", [None] * n)[i]
            if p is not None and p > g["per"]:
                g["per"] = p
            for src, key in (("homeTeamName", "home"), ("awayTeamName", "away")):
                if g[key] is None:
                    g[key] = d.get(src, [None] * n)[i]

        sched = _schedule(season, cache)
        kept = 0
        for gid, g in games.items():
            post = gid not in sched
            if not post and sched[gid] != ("fbs", "fbs"):
                continue                      # regular season is FBS-vs-FBS only
            rows.append({"season": season, "gid": gid, "post": post,
                         "margin": g["h"] - g["a"], "periods": g["per"],
                         "home": g["home"], "away": g["away"]})
            kept += 1
        print(f"  {season}: {kept:>4} games, "
              f"{sum(1 for r in rows[-kept:] if r['post']):>3} postseason", flush=True)

    with out_path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return rows


# --------------------------------------------------------------- joining

def _resolve_abbrs(rows: list[dict]) -> dict[str, str]:
    """Map each book abbreviation to the team id it names.

    The team an abbreviation names appears in every one of its rows, as home or
    away; any other id appears only as that game's opponent. So the most
    frequent id wins. Derived from the data rather than from a static map,
    which went stale and silently dropped two thirds of the sample.
    """
    seen: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        seen[r["abbr"]][r["home_team_id"]] += 1
        seen[r["abbr"]][r["away_team_id"]] += 1
    out = {}
    for a, c in seen.items():
        (top, n1), = c.most_common(1)
        n2 = c.most_common(2)[1][1] if len(c) > 1 else 0
        if len(c) == 1 or n1 > 2 * n2:
            out[a] = top
    return out


def _assert_real_lines(values: list[float]) -> None:
    """Refuse to analyse a spread column that is mostly one filler value."""
    top, n = Counter(values).most_common(1)[0]
    share = n / len(values)
    if share > 0.15:
        raise SystemExit(
            f"refusing to continue: {share:.0%} of spreads are exactly {top}. "
            f"That is a fill value, not a line. Measured against one, bowls "
            f"come out significantly tighter than the regular season, which is "
            f"the opposite of the truth."
        )


def join_lines(margin_rows: list[dict], cache: Path) -> list[dict]:
    """Attach a real median closing spread, from the home team's perspective."""
    path = _get(LINES_URL, cache / "lines.csv.gz")
    spreads = [r for r in csv.DictReader(gzip.open(path, "rt"))
               if r["market_type"] == "spread"]
    home_of = _resolve_abbrs(spreads)
    by_gid = {r["gid"]: r for r in margin_rows}

    close: dict[int, list[float]] = defaultdict(list)
    stype: dict[int, str] = {}
    for r in spreads:
        try:
            gid = int(float(r["game_id"]))
        except (ValueError, TypeError):
            continue
        if gid not in by_gid or home_of.get(r["abbr"]) != r["home_team_id"]:
            continue
        stype[gid] = r["season_type"]
        v = r["lines"].strip()
        if v:
            try:
                close[gid].append(float(v))
            except ValueError:
                pass

    out = [{**by_gid[g], "close": st.median(cs), "nbooks": len(cs),
            "stype": stype.get(g)} for g, cs in close.items() if cs]
    _assert_real_lines([r["close"] for r in out])
    return out


# --------------------------------------------------------------- analysis

def _sd(xs: list[float]) -> float:
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def report(rows: list[dict]) -> None:
    rows = [r for r in rows if abs(r["margin"]) < 100]
    reg = [r for r in rows if r["stype"] != "postseason"]
    post = [r for r in rows if r["stype"] == "postseason"]
    res = lambda r: r["margin"] + r["close"]   # close is negative when home is favoured

    xs = [-r["close"] for r in rows]
    ys = [float(r["margin"]) for r in rows]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = (sum((x - mx) * (y - my) for x, y in zip(xs, ys))
             / sum((x - mx) ** 2 for x in xs))
    print(f"\n{len(rows)} games, {len(post)} postseason, "
          f"{min(r['season'] for r in rows)}-{max(r['season'] for r in rows)}, "
          f"median {st.median([r['nbooks'] for r in rows]):.0f} books each")
    print(f"margin on negated closing spread: slope {slope:.3f} (a real line is ~1.0)")

    er, ep = [res(r) for r in reg], [res(r) for r in post]
    sr, sp = _sd(er), _sd(ep)
    z = math.log(sp / sr) / math.sqrt(1 / (2 * (len(ep) - 1)) + 1 / (2 * (len(er) - 1)))
    floor = math.exp(1.96 * math.sqrt(1 / (2 * (len(ep) - 1)) + 1 / (2 * (len(er) - 1))))
    print(f"\n{'set':<10}{'n':>7}{'line bias':>11}{'sd':>8}")
    for nm, e in (("regular", er), ("bowl", ep)):
        print(f"{nm:<10}{len(e):>7}{sum(e)/len(e):>11.2f}{_sd(e):>8.2f}")
    print(f"\nsd ratio bowl/regular = {sp/sr:.4f}, z = {z:+.2f} "
          f"({'significant' if abs(z) > 1.96 else 'NOT significant'} at 5%)")
    print(f"detectable only above {floor:.3f} at this sample, so anything "
          f"smaller stays unresolved")

    print(f"\n{'margin':>7}{'regular':>10}{'bowl':>9}{'ratio':>8}{'z':>7}")
    cr, cp = Counter(abs(r["margin"]) for r in reg), Counter(abs(r["margin"]) for r in post)
    nr, npo = sum(cr.values()), sum(cp.values())
    for k in (1, 2, 3, 4, 5, 6, 7, 8, 10, 14, 17, 21):
        pr, pp = cr[k] / nr, cp[k] / npo
        se = math.sqrt(pr * (1 - pr) / npo)
        print(f"{k:>7}{pr:>9.2%}{pp:>9.2%}{(pp/pr if pr else 0):>8.2f}{(pp-pr)/se:>7.2f}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cache", default="/tmp/bowlfit", type=Path,
                   help="where fetched data is kept between runs")
    p.add_argument("--from-season", type=int, default=2004)
    p.add_argument("--to-season", type=int, default=2024,
                   help="inclusive; seasons without play-by-play are skipped")
    a = p.parse_args(argv)

    a.cache.mkdir(parents=True, exist_ok=True)
    print(f"cache: {a.cache}")
    m = margins(range(a.from_season, a.to_season + 1), a.cache)
    report(join_lines(m, a.cache))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
