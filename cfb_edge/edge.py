"""Where a projection, a market price and a set of filters become a bet.

The filters matter as much as the arithmetic. A model with no discipline about
which games it is allowed to touch will find its largest "edges" in exactly the
games it understands least: FCS opponents it has never rated, teams one game
into a season, backup quarterbacks it has never heard of. Every one of those is
a place where a big model-market disagreement means the model is wrong, not
that the market is.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import blend as blend_mod
from .distribution import CoverOutcome, cover_probability, margin_pmf, sigma_for_total
from .market import payout_multiple
from .projection import Matchup, Projection, project
from .ratings import RatingModel
from .staking import Stake, size_bet

# Minimum points of edge, after blending, before a bet is considered. Below
# this the edge is smaller than the error in the ratings themselves.
DEFAULT_MIN_EDGE = 1.5

# Early in the season, ratings are noise and the threshold rises to match.
EARLY_SEASON_GAMES = 3
EARLY_SEASON_MIN_EDGE = 2.5


@dataclass(frozen=True)
class Candidate:
    """One side of one game, priced and judged."""

    home: str
    away: str
    side: str
    line: float
    price: float
    model_line: float
    market_line: float
    blended_line: float
    model_weight: float
    edge_points: float
    outcome: CoverOutcome
    stake: Stake
    rejected_for: str | None

    @property
    def is_bet(self) -> bool:
        return self.rejected_for is None and self.stake.recommended > 0.0

    @property
    def team(self) -> str:
        return self.home if self.side == "home" else self.away

    def describe(self) -> str:
        verdict = "PASS" if not self.is_bet else f"BET {self.stake.recommended:.3%}"
        reason = f" ({self.rejected_for})" if self.rejected_for else ""
        return (
            f"{self.away} at {self.home}: {self.team} {self.line:+.1f} "
            f"@ {self.price:+.0f} | model {self.model_line:+.1f} "
            f"market {self.market_line:+.1f} blend {self.blended_line:+.1f} "
            f"| edge {self.edge_points:+.2f} pts, "
            f"win {self.outcome.win_excluding_push:.1%}, "
            f"EV {self.stake.expected_value:+.2%} | {verdict}{reason}"
        )


def evaluate(
    model: RatingModel,
    matchup: Matchup,
    *,
    market_home_line: float,
    home_price: float = -110.0,
    away_price: float = -110.0,
    market_total: float = 52.0,
    min_edge: float | None = None,
    known_teams_only: bool = True,
    max_model_weight: float | None = None,
    rating_scale: float | None = None,
) -> Candidate:
    """Judge a single game and return the better side, bet or not.

    `market_home_line` follows the same convention as everything else: negative
    means the home team is laying points.

    `max_model_weight` raises the ceiling on how much of a vote the model gets;
    it does not bypass the games-played schedule, so an early-season game is
    still shrunk toward the market. The default ceiling is zero, because zero
    is what the model measured against real closing lines. Raise it only with
    your own evidence.
    """
    projection: Projection = project(
        model, matchup,
        **({} if rating_scale is None else {"rating_scale": rating_scale}),
    )
    weight = blend_mod.model_weight(
        projection.min_games_played,
        **({} if max_model_weight is None else {"max_weight": max_model_weight}),
    )
    blended = blend_mod.blend_line(projection.home_line, market_home_line, weight)

    # Positive means the home side is the value; negative means the away side.
    home_edge = market_home_line - blended

    if home_edge >= 0.0:
        side, line, price, edge = "home", market_home_line, home_price, home_edge
    else:
        side, line, price, edge = "away", -market_home_line, away_price, -home_edge

    # The distribution is centred on the blended margin, not the raw model one,
    # because the blended line is what the model actually believes.
    blended_home_margin = -blended
    sigma = sigma_for_total(market_total)
    if side == "home":
        pmf = margin_pmf(blended_home_margin, sigma)
    else:
        pmf = margin_pmf(-blended_home_margin, sigma)

    outcome = cover_probability(pmf, line)
    stake = size_bet(outcome, payout_multiple(price))

    threshold = min_edge
    if threshold is None:
        threshold = (
            EARLY_SEASON_MIN_EDGE
            if projection.min_games_played < EARLY_SEASON_GAMES
            else DEFAULT_MIN_EDGE
        )

    rejected = None
    if known_teams_only and not projection.both_teams_known:
        rejected = "unrated team"
    elif edge < threshold:
        rejected = f"edge {edge:.2f} below {threshold:.2f} pt threshold"
    elif stake.expected_value <= 0.0:
        rejected = "negative EV after price"

    return Candidate(
        home=matchup.home,
        away=matchup.away,
        side=side,
        line=line,
        price=price,
        model_line=projection.home_line,
        market_line=market_home_line,
        blended_line=blended,
        model_weight=weight,
        edge_points=edge,
        outcome=outcome,
        stake=stake,
        rejected_for=rejected,
    )


def rank_card(candidates: list[Candidate]) -> list[Candidate]:
    """Order a week's plays by expected value, best first.

    Ranked on EV rather than on points of edge: a three-point edge into a key
    number at a good price is worth more than a four-point edge into a number
    that never comes up, and only EV sees the difference.
    """
    bets = [c for c in candidates if c.is_bet]
    return sorted(bets, key=lambda c: c.stake.expected_value, reverse=True)
