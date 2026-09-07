"""CSV in, CSV out.

Deliberately file-based rather than wired to a paid odds API. Files can be
diffed, checked into git, and replayed months later to see what the model
actually said at the time, which is what you need to audit it honestly. An API
client is a thin wrapper to add on top of this; the reverse is painful.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .projection import Matchup
from .ratings import Game

GAME_FIELDS = ["home", "away", "home_points", "away_points", "neutral"]
SLATE_FIELDS = [
    "home", "away", "neutral", "market_home_line", "home_price", "away_price",
    "market_total", "home_rest_days", "away_rest_days", "away_body_clock",
]
PRIOR_FIELDS = ["team", "rating"]


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y"}


def load_games(path: str | Path) -> list[Game]:
    """Completed results, used to fit ratings."""
    games: list[Game] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            games.append(
                Game(
                    home=row["home"].strip(),
                    away=row["away"].strip(),
                    home_points=int(row["home_points"]),
                    away_points=int(row["away_points"]),
                    neutral=_truthy(row.get("neutral")),
                )
            )
    return games


def load_priors(path: str | Path) -> dict[str, float]:
    """Preseason ratings in points. See `priors_from_market_win_totals`."""
    priors: dict[str, float] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            priors[row["team"].strip()] = float(row["rating"])
    return priors




def load_slate(path: str | Path) -> list[tuple[Matchup, dict[str, float]]]:
    """Upcoming games with their market prices.

    Returns each matchup alongside the market fields, because a matchup is the
    model's view of a game and the prices are the market's, and keeping them
    separate makes it obvious which is which.
    """
    out: list[tuple[Matchup, dict[str, float]]] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            matchup = Matchup(
                home=row["home"].strip(),
                away=row["away"].strip(),
                neutral=_truthy(row.get("neutral")),
                home_rest_days=int(row.get("home_rest_days") or 7),
                away_rest_days=int(row.get("away_rest_days") or 7),
                away_body_clock=_truthy(row.get("away_body_clock")),
            )
            market = {
                "market_home_line": float(row["market_home_line"]),
                "home_price": float(row.get("home_price") or -110),
                "away_price": float(row.get("away_price") or -110),
                "market_total": float(row.get("market_total") or 52.0),
            }
            out.append((matchup, market))
    return out
