"""How much of the model's opinion to actually use.

This is the most important number in the whole system and the one most likely
to be set wrong out of optimism.

The closing line is the best public forecast of a college football game. A
power rating built from a handful of results is not competitive with it. So the
projection the model bets is never the raw model number: it is a weighted
average of the model and the market, and the weight on the model is small.

The consequence is deliberate and worth stating plainly. In week two, with a
weight near 0.09, a model that disagrees with the market by ten full points
produces less than one point of edge, which will not clear the betting
threshold. The model will find almost nothing to bet in September. That is the
correct behaviour for a system that knows almost nothing in September, and the
main reason models like this lose money is that their authors could not stand
seeing an empty card and turned the weight up.
"""

from __future__ import annotations

# Even in December, with a full season of results, the model gets less than
# half the vote. Nothing here justifies more.
MAX_MODEL_WEIGHT = 0.45

# Games played at which the model reaches half its maximum weight.
HALF_WEIGHT_GAMES = 4.0


def model_weight(
    games_played: float,
    *,
    max_weight: float = MAX_MODEL_WEIGHT,
    half_weight_games: float = HALF_WEIGHT_GAMES,
) -> float:
    """Weight on the model, rising with sample size and capped well below 1."""
    n = max(0.0, float(games_played))
    return max_weight * n / (n + half_weight_games)


def blend_line(model_line: float, market_line: float, weight: float) -> float:
    """Weighted average of the model's fair line and the market's line."""
    if not 0.0 <= weight <= 1.0:
        raise ValueError(f"weight must be in [0, 1], got {weight}")
    return weight * model_line + (1.0 - weight) * market_line


def blended_edge(model_line: float, market_line: float, weight: float) -> float:
    """Points of edge from backing the side the model prefers.

    Algebraically this collapses to `weight * (market_line - model_line)`, which
    is the cleanest way to see why the weight dominates everything: the edge is
    the disagreement, scaled down by how much the model has earned the right to
    disagree.
    """
    return market_line - blend_line(model_line, market_line, weight)
