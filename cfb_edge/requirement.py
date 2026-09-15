"""How good would the model have to be? Computed, so nobody has to guess.

Every instinct this project has had to fight points the same way: make the
model better. Fit more, tune more, add a feature. The arithmetic below is why
that cannot work, and it is short enough that it should have existed from the
start.

A spread bet is not a forecasting contest against nature. It is a contest
against one specific number, and that number is already better than the model:

    closing line residual sd     15.39 points
    Elo projection residual sd   16.33 points, same games

The model is nearly a point of standard deviation *worse* than the line it
would be betting into. Measured directly, its incremental coefficient over the
close is -0.02 at a t of -0.31 across 6,398 games: no information the market
does not already have, and the point estimate is on the wrong side of zero.

So "make it a perfect model" is the wrong target twice over. A perfect model
would of course clear the vig, and so would being a perfect anything; the
question is what an achievable model would need to be worth, and the answer is
much further away than tuning reaches. `beta_required` computes it.

What has ever measured positive here is a different claim: +0.44 points of
closing line value against the **opening** number. That is not an assertion
about the model being accurate. It is an assertion about the market being
slow, and the model's only job in it is picking which side of a stale number
to take. Two things follow, and they are the whole strategy:

  - Model accuracy is not the binding constraint, so improving it buys little.
  - The claim is about a venue and an instant, so it has to be measured on the
    venue actually traded, at the instant the number posts.

That measurement has zero observations. Nothing in this module changes that;
it exists so the next person who wants to tune the ratings can see the size of
the gap first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# From 20 seasons of real closing lines, 2006-2025, median 14 books per game.
# Recorded in MODEL.md. Not reproducible from anything in this repository: the
# sportsbook history is not vendored and no odds host is reachable from CI.
CLOSING_LINE_RESIDUAL_SD = 15.39
MODEL_RESIDUAL_SD = 16.33

# The model's incremental coefficient on the market's residual, and its t.
# A coefficient of zero means the model knows nothing the close does not.
MEASURED_BETA = -0.02
MEASURED_BETA_T = -0.31


def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _probit(p: float) -> float:
    """Inverse standard normal CDF by bisection. Exact enough and dependency-free."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"probit needs a probability strictly inside (0, 1), got {p}")
    low, high = -8.0, 8.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if _phi(mid) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def breakeven_probability(american_price: float) -> float:
    """Win rate a bet must clear to break even, pushes excluded."""
    from .market import american_to_probability

    return american_to_probability(american_price)


@dataclass(frozen=True)
class Requirement:
    """What the model would have to be worth, and how far that is from measured."""

    american_price: float
    mean_disagreement: float
    breakeven: float
    beta_required: float
    beta_measured: float
    beta_stderr: float

    @property
    def standard_errors_away(self) -> float:
        """How many SE the requirement sits above what was measured."""
        if self.beta_stderr <= 0.0:
            return float("inf")
        return (self.beta_required - self.beta_measured) / self.beta_stderr

    @property
    def reachable(self) -> bool:
        """Whether the measurement is even consistent with clearing the vig.

        Two standard errors is the conventional bar, used here in the direction
        that matters: if the requirement sits further than that above the point
        estimate, the data does not support the strategy being profitable on
        the model's accuracy, whatever a backtest of it says.
        """
        return self.standard_errors_away <= 2.0

    def summary(self) -> str:
        return (
            f"At {self.american_price:+.0f} a bet must win "
            f"{self.breakeven:.2%}.\n"
            f"On a mean disagreement of {self.mean_disagreement:.1f} points "
            f"that needs an incremental coefficient of "
            f"{self.beta_required:+.3f}.\n"
            f"Measured: {self.beta_measured:+.3f} "
            f"(SE {self.beta_stderr:.3f}), which is "
            f"{self.standard_errors_away:.1f} SE below the requirement.\n"
            + ("Consistent with clearing the vig."
               if self.reachable else
               "Not consistent with clearing the vig on model accuracy.")
        )


def beta_required(
    *, american_price: float = -110.0, mean_disagreement: float,
    residual_sd: float = CLOSING_LINE_RESIDUAL_SD,
) -> float:
    """Incremental coefficient needed to break even at this price.

    Writing the market's residual as `e = beta * d + u`, where `d` is the
    model's disagreement with the close, betting the sign of `d` wins with
    probability `Phi(beta * |d| / sd(u))`. Setting that equal to the breakeven
    rate and solving for beta gives the requirement.

    `residual_sd` stands in for `sd(u)`, which overstates it slightly when beta
    is non-zero and therefore makes the requirement conservative: the real bar
    is a shade higher than what comes back here.
    """
    if mean_disagreement <= 0.0:
        raise ValueError(
            f"a disagreement of {mean_disagreement} is not a signal, so no "
            f"coefficient can make it profitable"
        )
    z = _probit(breakeven_probability(american_price))
    return z * residual_sd / mean_disagreement


def requirement(
    *, mean_disagreement: float, american_price: float = -110.0,
    beta_measured: float = MEASURED_BETA, beta_t: float = MEASURED_BETA_T,
) -> Requirement:
    """The full comparison, requirement against measurement."""
    stderr = abs(beta_measured / beta_t) if beta_t else 0.0
    return Requirement(
        american_price=american_price,
        mean_disagreement=mean_disagreement,
        breakeven=breakeven_probability(american_price),
        beta_required=beta_required(american_price=american_price,
                                    mean_disagreement=mean_disagreement),
        beta_measured=beta_measured,
        beta_stderr=stderr,
    )


def model_deficit() -> float:
    """Points of residual sd by which the model trails the closing line.

    Positive means worse. This is the number that makes tuning futile: the
    model is not a slightly weaker forecaster than the market, it is a weaker
    forecaster than the single number it would be betting against.
    """
    return MODEL_RESIDUAL_SD - CLOSING_LINE_RESIDUAL_SD
