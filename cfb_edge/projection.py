"""Turning ratings into a projected line for a specific game.

Scope note, because overclaiming here is how models get trusted too far: this
projects spreads only. It does not project totals. Predicting points scored
needs pace and efficiency data that adjusted margin simply does not contain,
and a total projected from margin ratings would be a guess wearing a decimal
point. The market total is used, but only to set how variable the game is
likely to be, which is a much weaker claim than predicting it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ratings import RatingModel

# Points per day of rest advantage, capped. A team off a bye is genuinely
# better prepared, but the effect is small and the market already knows about
# byes, so this is a nudge rather than a thesis.
POINTS_PER_REST_DAY = 0.12
MAX_REST_ADJUSTMENT = 1.5

# Cross-country road trips with an early local kickoff are the one travel spot
# with a real, repeatedly documented effect. Everything else is folklore.
BODY_CLOCK_PENALTY = 1.0


@dataclass(frozen=True)
class Matchup:
    """An upcoming game and its circumstances."""

    home: str
    away: str
    neutral: bool = False
    home_rest_days: int = 7
    away_rest_days: int = 7
    # True when the away team crosses two or more time zones westward-to-eastward
    # into a kickoff before 1pm local time.
    away_body_clock: bool = False


@dataclass(frozen=True)
class Projection:
    """The model's own opinion, before any contact with the market."""

    home_margin: float
    home_line: float
    rating_gap: float
    hfa_applied: float
    rest_adjustment: float
    body_clock_adjustment: float
    min_games_played: int
    both_teams_known: bool


def project(model: RatingModel, matchup: Matchup) -> Projection:
    """Project a home margin and the fair home line that implies.

    Line convention throughout: a home line of -7.0 means the home team lays
    seven. It is the negative of the projected home margin.
    """
    gap = model.rating(matchup.home) - model.rating(matchup.away)
    hfa = 0.0 if matchup.neutral else model.hfa

    rest_delta = matchup.home_rest_days - matchup.away_rest_days
    rest = max(
        -MAX_REST_ADJUSTMENT,
        min(MAX_REST_ADJUSTMENT, rest_delta * POINTS_PER_REST_DAY),
    )

    body_clock = BODY_CLOCK_PENALTY if matchup.away_body_clock else 0.0

    margin = gap + hfa + rest + body_clock
    known = model.is_known(matchup.home) and model.is_known(matchup.away)
    min_games = min(
        model.games_played.get(matchup.home, 0),
        model.games_played.get(matchup.away, 0),
    )

    return Projection(
        home_margin=margin,
        home_line=-margin,
        rating_gap=gap,
        hfa_applied=hfa,
        rest_adjustment=rest,
        body_clock_adjustment=body_clock,
        min_games_played=min_games,
        both_teams_known=known,
    )
