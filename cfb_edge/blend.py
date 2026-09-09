"""How much of the model's opinion to actually use.

This is the most important number in the whole system and the one most likely
to be set wrong out of optimism.

The closing line is the best public forecast of a college football game. A
power rating built from a handful of results is not competitive with it. So the
projection the model bets is never the raw model number: it is a weighted
average of the model and the market, and the weight on the model is small.

This module was written expecting to argue for a small weight against the
temptation of a large one. Measurement settled it more harshly than that: on
6,398 real games the model's incremental coefficient is -0.02 with a t of
-0.31, so the honest weight is zero and the honest card is empty. The schedule
below is kept because its shape is still right for any model that does earn a
vote, but the default no longer grants one.
"""

from __future__ import annotations

# Measured, not chosen. Regressing actual margin on both the closing line's
# projection and this model's, over 6,398 real games from 2006 to 2025 with the
# ratings fit walk-forward so nothing looks ahead:
#
#     closing line   +1.0353   (t = +24.6)
#     this model     -0.0192   (t = -0.31)
#
# A market coefficient of one and a model coefficient of zero is what an
# efficient market looks like from the inside: the closing line already
# contains everything the ratings know, and then some. The implied optimal
# weight on the model is -0.019, which is zero.
#
# So the default is zero, and the model bets nothing. That is not a placeholder
# and it is not pessimism, it is the measurement. An earlier version of this
# file allowed 0.45 and described that as conservative; against real closing
# lines it was 0.45 too high, and the walk-forward backtest it produced lost
# 6 to 9 percent per bet across every threshold tried.
#
# Raise it only against your own evidence, and pass it explicitly when you do.
MAX_MODEL_WEIGHT = 0.0

# What the weight would be if a model did earn a vote. Kept because the shape
# of the schedule is still right: whatever weight a model deserves, it deserves
# less of it in September than in November.
DEMONSTRATED_EDGE_WEIGHT = 0.45

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
