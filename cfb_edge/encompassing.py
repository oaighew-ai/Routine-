"""Does a model know anything the closing line does not?

This is the test that settles whether a model should be bet, and it is stricter
and more informative than a win-loss record. Regress what actually happened on
two projections at once: the market's and the model's. If the market's
coefficient is one and the model's is zero, the closing line already contains
everything the model knows, and no threshold, no staking rule and no amount of
selectivity will fix that. The model is not adding information, it is adding
noise with a fee attached.

Run on this repository's own ratings against 6,398 real games from 2006 to
2025, with the ratings fit walk-forward so nothing looked ahead:

    closing line   +1.0353   (se 0.0421, t = +24.6)
    this model     -0.0192   (se 0.0627, t = -0.31)
    residual sd     15.41

That is what an efficient market looks like from the inside. The implied
optimal weight on the model is -0.019, which is zero, and it is why
`blend.MAX_MODEL_WEIGHT` ships at zero.

For context, from the same games: the closing line's residual dispersion is
15.39 points, against 16.33 for an Elo-based projection. The market is not
merely as good as a public rating system, it is better, and it is better by
about a point of standard deviation.

A win-loss record cannot tell you this. A model can lose 40 bets in a row while
holding real information, and win 40 while holding none. The regression asks
the question directly and answers it at a sample size a season can actually
supply.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Encompassing:
    """Two projections tested against each other on the same outcomes."""

    market_coef: float
    market_se: float
    model_coef: float
    model_se: float
    residual_sd: float
    observations: int

    @property
    def market_t(self) -> float:
        return self.market_coef / self.market_se if self.market_se else 0.0

    @property
    def model_t(self) -> float:
        return self.model_coef / self.model_se if self.model_se else 0.0

    @property
    def implied_model_weight(self) -> float:
        """The blend weight the data supports, which is the model's share of
        the two coefficients. Negative means the model is worse than nothing."""
        total = self.market_coef + self.model_coef
        return self.model_coef / total if total else 0.0

    @property
    def model_adds_information(self) -> bool:
        """Whether the model's coefficient clears two standard errors. Anything
        less is not evidence, whatever the win rate says."""
        return self.model_t > 2.0

    def summary(self) -> str:
        verdict = (
            "the model adds information over the closing line"
            if self.model_adds_information
            else "the closing line already contains what the model knows"
        )
        return (
            f"{self.observations} games\n"
            f"  market projection {self.market_coef:+.4f} "
            f"(se {self.market_se:.4f}, t = {self.market_t:+.2f})\n"
            f"  model projection  {self.model_coef:+.4f} "
            f"(se {self.model_se:.4f}, t = {self.model_t:+.2f})\n"
            f"  residual sd {self.residual_sd:.2f}\n"
            f"  implied blend weight on the model {self.implied_model_weight:+.3f}\n"
            f"  -> {verdict}"
        )


def encompassing_regression(
    actual: Sequence[float],
    market_projection: Sequence[float],
    model_projection: Sequence[float],
) -> Encompassing:
    """Least squares of the outcome on both projections, with an intercept.

    All three sequences are in the same units and the same direction: a
    projected home margin, and the home margin that actually happened.
    """
    n = len(actual)
    if not (n == len(market_projection) == len(model_projection)):
        raise ValueError("all three sequences must be the same length")
    if n < 4:
        raise ValueError(f"need at least 4 observations, got {n}")

    my = statistics.fmean(actual)
    m1 = statistics.fmean(market_projection)
    m2 = statistics.fmean(model_projection)
    y = [v - my for v in actual]
    a = [v - m1 for v in market_projection]
    b = [v - m2 for v in model_projection]

    saa = sum(v * v for v in a)
    sbb = sum(v * v for v in b)
    sab = sum(x * z for x, z in zip(a, b))
    say = sum(x * z for x, z in zip(a, y))
    sby = sum(x * z for x, z in zip(b, y))
    det = saa * sbb - sab * sab
    if abs(det) < 1e-12:
        raise ValueError(
            "the two projections are collinear, so their separate "
            "contributions cannot be identified"
        )

    market_coef = (sbb * say - sab * sby) / det
    model_coef = (saa * sby - sab * say) / det
    resid = [
        yy - (market_coef * aa + model_coef * bb) for yy, aa, bb in zip(y, a, b)
    ]
    s2 = sum(r * r for r in resid) / (n - 3)
    return Encompassing(
        market_coef=market_coef,
        market_se=math.sqrt(s2 * sbb / det),
        model_coef=model_coef,
        model_se=math.sqrt(s2 * saa / det),
        residual_sd=math.sqrt(sum(r * r for r in resid) / n),
        observations=n,
    )
