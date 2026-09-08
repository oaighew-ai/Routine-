"""Build a week's slate with fresh projections, straight from public data.

The projections in a slate are only as good as the results behind them, and
those change every Saturday. A slate written on Tuesday for week three is built
on one week of football; the same slate rebuilt on Sunday is built on two. So
this fetches rather than ships a file, and it should be re-run after each week
completes.

Schedules and results come from cfbfastR on GitHub, which needs no key and is
reachable from anywhere GitHub is. Ratings are fitted here rather than read from
the file's Elo column, because that column is sparse: in the 2026 file only 16
of 49 week-two matchups carried Elo on both sides, missing Alabama, Georgia,
Notre Dame and Penn State among others.

The projections locate the margin distribution so the key numbers land in the
right place. They are not a reason to bet anything; that measured -0.02 with a
t of -0.31 against real closing lines. If you have the market's current number
for a game, it is a better locator than these are.
"""

from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .ratings import Game, solve_ratings

RAW_ROOT = (
    "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main"
    "/schedules/csv/cfb_schedules_{season}.csv"
)

Opener = Callable[[str], bytes]


class ScheduleUnreachable(RuntimeError):
    """Raised when the schedule cannot be fetched, naming the host."""


def _default_opener(url: str, *, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "cfb-edge/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_season(season: int, *, opener: Opener | None = None) -> list[dict]:
    url = RAW_ROOT.format(season=season)
    try:
        blob = (opener or _default_opener)(url)
    except urllib.error.URLError as exc:
        raise ScheduleUnreachable(
            f"could not reach raw.githubusercontent.com: {exc}. The schedule is "
            f"public and needs no key, so this is a network policy denial."
        ) from exc
    return list(csv.DictReader(io.StringIO(blob.decode("utf-8"))))


def _fbs_results(rows: list[dict], *, upto_week: int | None = None) -> list[Game]:
    out: list[Game] = []
    for r in rows:
        if (r.get("home_division", "").lower() != "fbs"
                or r.get("away_division", "").lower() != "fbs"):
            continue
        try:
            week = int(float(r["week"]))
            if upto_week is not None and week >= upto_week:
                continue
            home_pts = int(float(r["home_points"]))
            away_pts = int(float(r["away_points"]))
        except (ValueError, TypeError, KeyError):
            continue
        out.append(Game(
            r["home_team"], r["away_team"], home_pts, away_pts,
            str(r.get("neutral_site", "")).strip().upper() in ("TRUE", "1"),
        ))
    return out


@dataclass(frozen=True)
class SlateRow:
    game: str
    projected_margin: float
    neutral: bool
    date: str


def build(
    season: int, week: int, *, opener: Opener | None = None,
    prior_regression: float = 0.5,
) -> list[SlateRow]:
    """Project every FBS-vs-FBS game in a week, using only prior results.

    Nothing from the target week or later enters the ratings, so a slate built
    for week three cannot see week three.
    """
    prior_rows = fetch_season(season - 1, opener=opener)
    prior_model = solve_ratings(_fbs_results(prior_rows), prior_weight=4.0)
    priors = {t: v * prior_regression for t, v in prior_model.ratings.items()}

    rows = fetch_season(season, opener=opener)
    model = solve_ratings(
        _fbs_results(rows, upto_week=week), priors=priors, prior_weight=4.0
    )

    out: list[SlateRow] = []
    for r in rows:
        try:
            if int(float(r["week"])) != week:
                continue
        except (ValueError, TypeError, KeyError):
            continue
        if (r.get("home_division", "").lower() != "fbs"
                or r.get("away_division", "").lower() != "fbs"):
            continue
        home, away = r["home_team"], r["away_team"]
        if not (model.is_known(home) and model.is_known(away)):
            continue
        neutral = str(r.get("neutral_site", "")).strip().upper() in ("TRUE", "1")
        out.append(SlateRow(
            game=f"{away} @ {home}",
            projected_margin=round(
                model.rating(home) - model.rating(away)
                + (0.0 if neutral else model.hfa), 2),
            neutral=neutral,
            date=(r.get("start_date") or "")[:10],
        ))
    return sorted(out, key=lambda s: (s.date, s.game))


def write_csv(rows: list[SlateRow], path: str) -> int:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["game", "projected_margin", "side", "posted_line", "total"])
        for r in rows:
            w.writerow([r.game, r.projected_margin, "", "", 52.0])
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    """python3 -m cfb_edge.slate --season 2026 --week 3 --out week3.csv"""
    import argparse

    p = argparse.ArgumentParser(prog="cfb_edge.slate", description=main.__doc__)
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--week", type=int, required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    try:
        rows = build(args.season, args.week)
    except ScheduleUnreachable as exc:
        print(f"cannot build the slate: {exc}")
        return 2
    n = write_csv(rows, args.out)
    played = "results through week %d" % (args.week - 1)
    print(f"{n} FBS-vs-FBS games in {args.season} week {args.week}, "
          f"projected from {played}.")
    print(f"wrote {args.out}")
    print("re-run after the current week finishes; the ratings improve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
