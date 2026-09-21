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
import datetime as _dt
import io
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .projection import RATING_SCALE
from .ratings import Game, solve_ratings

RAW_ROOT = (
    "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main"
    "/schedules/csv/cfb_schedules_{season}.csv"
)

Opener = Callable[[str], bytes]

# The CLI's exit code for "there is no week to build", which is an answer and
# not a failure. Anything else non-zero means the slate could not be built.
SEASON_OVER = 3


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


def current_week(
    season: int, *, today: str | None = None, opener: Opener | None = None,
    rows: list[dict] | None = None,
) -> int | None:
    """The week a capture started today should be recording, or None.

    A weekly scheduled task cannot be told the week when it is registered,
    because the week changes underneath it. Deriving it from a hardcoded
    season-start date is how a capture ends up recording the wrong slate in
    the one week the calendar shifts, so this reads the schedule instead.

    The answer is the earliest regular-season week that has not finished:
    lines for it are posting now or have already posted. A season that is over
    returns None rather than the last week, because there is nothing left to
    capture and reporting week 15 forever would look like it was working.
    """
    day = today or _dt.date.today().isoformat()
    rows = rows if rows is not None else fetch_season(season, opener=opener)
    last_day: dict[int, str] = {}
    for r in rows:
        if (r.get("season_type") or "").strip().lower() != "regular":
            continue
        try:
            week = int(float(r["week"]))
        except (ValueError, TypeError, KeyError):
            continue
        date = (r.get("start_date") or "")[:10]
        if date and date > last_day.get(week, ""):
            last_day[week] = date
    upcoming = [w for w, end in sorted(last_day.items()) if end >= day]
    return upcoming[0] if upcoming else None


def _instant(stamp: str) -> _dt.datetime | None:
    """A timezone-aware datetime from a schedule stamp, or None.

    Schedule rows carry `2026-09-17T23:30:00.000Z`; a caller may pass a bare
    date, which means midnight UTC. Comparing the two as strings looks like it
    works and does not: `.000Z` sorts after `+00:00` at the same instant. A
    stamp without a zone is read as UTC, because every stamp in the source has
    one and a naive one would otherwise raise when compared.
    """
    text = (stamp or "").strip()
    if not text:
        return None
    if len(text) == 10:
        text += "T00:00:00+00:00"
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        when = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=_dt.timezone.utc)


def opening_week(
    season: int, *, now: str | None = None, opener: Opener | None = None,
    rows: list[dict] | None = None,
) -> int | None:
    """The week whose lines can still be captured at their open, or None.

    This is not `current_week`, and the gap between them is what a whole week
    of capture was lost to. `current_week` answers which slate is being
    played, so on the Sunday of week three it answers three, correctly. The
    exchange has already moved on: week three's games are over or ending and
    the board lists week four. `RELEASE_WINDOW_UTC` opens at 22:00 on that
    same Sunday, so a capture that asks for the current week spends the entire
    window polling a board that no longer lists the games it is holding. It
    matches nothing, raises nothing, and records nothing.

    The answer is the earliest regular-season week no game of which has
    kicked off. Once a week's first game starts, its opening number is gone
    and what is left on the board is a current number, which the strategy has
    no measured edge against.

    A bare date for `now` means midnight UTC that day, which keeps the week
    of a Tuesday night game capturable through the Tuesday morning that the
    window closes.
    """
    moment = _instant(now) if now else _dt.datetime.now(_dt.timezone.utc)
    if moment is None:
        raise ValueError(f"cannot read {now!r} as a date or a timestamp")
    rows = rows if rows is not None else fetch_season(season, opener=opener)
    first_kick: dict[int, _dt.datetime] = {}
    for r in rows:
        if (r.get("season_type") or "").strip().lower() != "regular":
            continue
        try:
            week = int(float(r["week"]))
        except (ValueError, TypeError, KeyError):
            continue
        kick = _instant(r.get("start_date") or "")
        if kick is None:
            continue
        if week not in first_kick or kick < first_kick[week]:
            first_kick[week] = kick
    ahead = [w for w, kick in sorted(first_kick.items()) if kick > moment]
    return ahead[0] if ahead else None


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
                (model.rating(home) - model.rating(away)) * RATING_SCALE
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
    p.add_argument("--week", default="current",
                   help="week number, 'current' (default) for the earliest "
                        "unfinished week, or 'opening' for the earliest week "
                        "no game of which has kicked off, which is the week a "
                        "capture can still record at its open")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    mode = str(args.week).strip().lower()
    try:
        if mode in ("current", "opening"):
            if mode == "opening":
                week = opening_week(args.season)
                found = "is the next week of %d no game of which has started"
                empty = "no %d regular-season week is still open for capture"
            else:
                week = current_week(args.season)
                found = "is the next unfinished week of %d"
                empty = "no %d regular-season week is unfinished"
            if week is None:
                # Exit 3, not 2: a scheduled caller has to tell "nothing to do"
                # apart from "the schedule is unreachable", or it goes red every
                # half hour for nine months of the year.
                print(empty % args.season + ". Nothing left to capture; pass "
                      "--week explicitly to rebuild an earlier slate.")
                return SEASON_OVER
            print(f"week {week} " + found % args.season + ".")
        else:
            week = int(args.week)
        rows = build(args.season, week)
    except ScheduleUnreachable as exc:
        print(f"cannot build the slate: {exc}")
        return 2
    n = write_csv(rows, args.out)
    played = "results through week %d" % (week - 1)
    print(f"{n} FBS-vs-FBS games in {args.season} week {week}, "
          f"projected from {played}.")
    print(f"wrote {args.out}")
    print("re-run after the current week finishes; the ratings improve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
