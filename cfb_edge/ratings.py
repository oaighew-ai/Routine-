"""Opponent-adjusted power ratings with shrinkage toward a prior.

A rating is expressed in points: if A rates +14 and B rates +3, A is eleven
points better than B on a neutral field. The rating is a simple adjusted margin
model, solved by iterating each team's rating toward the average of its results
plus the strength of who it played.

Two details do most of the work.

Shrinkage. In week two a team has played once. Its adjusted margin is almost
entirely noise, and a model that takes it at face value will rate a team that
beat an FCS opponent by 50 as a national title contender. Every rating is
therefore pulled toward a prior with a weight worth several games, so early
season ratings barely move off their preseason values. That is a feature. The
ratings are not supposed to know much in September, and the blend weight in
`blend.py` is set so the model does not pretend otherwise.

Margin capping. Winning by 60 tells you almost nothing that winning by 30 did
not already tell you, but an uncapped average lets one blowout drag a rating
for months. Margins are capped before they enter the average.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

# Beyond roughly four scores, extra margin is garbage time rather than signal.
DEFAULT_MARGIN_CAP = 28.0

# Prior weight in units of games. At 4.0, a team's first game carries a fifth
# of the weight of its prior, and it takes a month for results to dominate.
DEFAULT_PRIOR_WEIGHT = 4.0

# Points a generic home field is worth, fitted from 14,687 FBS-vs-FBS games by
# regressing final margin on the Elo gap with a home-field intercept.
#
#   2004-2008  4.49        2019-2021  2.76
#   2009-2013  3.90        2022-2024  3.39
#   2014-2018  3.14
#
# The long decline is real, and it is tempting to read the 2019-2021 trough as
# the end state and set this near two. That is wrong: those seasons contain the
# empty and half-empty stadiums of 2020, and the effect rebounded to 3.39 once
# crowds came back. The recent four-year figure is 3.21, which is what this
# uses. An earlier version of this file asserted 2.2 on the reasoning that
# modern football had professionalised away the home edge; the data says that
# reasoning was about a point and a half too aggressive.
DEFAULT_HFA = 3.2


@dataclass(frozen=True)
class Game:
    """A completed game. Scores are final, including overtime."""

    home: str
    away: str
    home_points: int
    away_points: int
    neutral: bool = False

    @property
    def home_margin(self) -> int:
        return self.home_points - self.away_points


@dataclass
class RatingModel:
    """Solved ratings plus the settings that produced them."""

    ratings: dict[str, float] = field(default_factory=dict)
    games_played: dict[str, int] = field(default_factory=dict)
    hfa: float = DEFAULT_HFA
    iterations: int = 0
    converged: bool = False

    def rating(self, team: str) -> float:
        """Rating for a team, defaulting to average for anyone unseen.

        Unseen teams are usually FCS opponents. Returning average for them is
        wrong, but it is wrong in a visible way, and `edge.py` refuses to bet
        games involving teams the ratings have never seen.
        """
        return self.ratings.get(team, 0.0)

    def is_known(self, team: str) -> bool:
        return team in self.ratings


def solve_ratings(
    games: Sequence[Game],
    *,
    priors: Mapping[str, float] | None = None,
    prior_weight: float = DEFAULT_PRIOR_WEIGHT,
    hfa: float = DEFAULT_HFA,
    margin_cap: float = DEFAULT_MARGIN_CAP,
    max_iterations: int = 500,
    tolerance: float = 1e-9,
) -> RatingModel:
    """Iterate ratings to a fixed point.

    Each pass sets a team's rating to the shrunk average of (its capped margin
    in each game, adjusted for home field, plus that opponent's current rating).
    Ratings are recentred on zero every pass so the whole scale cannot drift.
    """
    priors = dict(priors or {})
    teams = sorted({g.home for g in games} | {g.away for g in games} | set(priors))
    if not teams:
        return RatingModel(hfa=hfa, converged=True)

    ratings = {t: float(priors.get(t, 0.0)) for t in teams}
    played: dict[str, int] = {t: 0 for t in teams}

    # Precompute each team's results so the inner loop stays cheap.
    results: dict[str, list[tuple[str, float]]] = {t: [] for t in teams}
    for g in games:
        margin = max(-margin_cap, min(margin_cap, float(g.home_margin)))
        venue = 0.0 if g.neutral else hfa
        # Neutralised margin from each side's perspective.
        results[g.home].append((g.away, margin - venue))
        results[g.away].append((g.home, -margin + venue))
        played[g.home] += 1
        played[g.away] += 1

    converged = False
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        updated: dict[str, float] = {}
        for team in teams:
            prior = float(priors.get(team, 0.0))
            numerator = prior_weight * prior
            denominator = prior_weight
            for opponent, neutral_margin in results[team]:
                numerator += neutral_margin + ratings[opponent]
                denominator += 1.0
            updated[team] = numerator / denominator

        mean = sum(updated.values()) / len(updated)
        updated = {t: v - mean for t, v in updated.items()}

        shift = max(abs(updated[t] - ratings[t]) for t in teams)
        ratings = updated
        if shift < tolerance:
            converged = True
            break

    return RatingModel(
        ratings=ratings,
        games_played=played,
        hfa=hfa,
        iterations=iterations,
        converged=converged,
    )


def priors_from_market_win_totals(
    win_totals: Mapping[str, float],
    *,
    games: float = 12.0,
    points_per_win: float = 4.6,
) -> dict[str, float]:
    """Turn posted season win totals into preseason point ratings.

    Win totals are the market's opinion, and the market's preseason opinion is
    a much better prior than a poll or a hand-built ranking. The conversion is
    crude: a win above or below a .500 schedule is worth a few points of rating.
    `points_per_win` is the one knob and it should be refit, but the ordering it
    produces is what matters and the ordering is the market's, not ours.
    """
    if not win_totals:
        return {}
    raw = {t: (w - games / 2.0) * points_per_win for t, w in win_totals.items()}
    mean = sum(raw.values()) / len(raw)
    return {t: v - mean for t, v in raw.items()}
