"""How much to bet.

Kelly maximises long-run growth given a known edge. The edge here is not known,
it is estimated, and estimation error makes full Kelly actively dangerous: bet
full Kelly on an edge you have overestimated by half and you are betting well
past the growth-optimal point, where returns fall and risk of ruin climbs fast.
So everything is a fraction of Kelly, and there are hard caps on top.

The standard Kelly formula also assumes no pushes, which is wrong for spread
betting on whole numbers. A push returns the stake, so it contributes nothing
to log wealth, and the optimum has to be solved numerically rather than read
off the closed form. The difference is small but it is free to get right.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .distribution import CoverOutcome

# A quarter of Kelly. Enough to compound, small enough to survive being wrong
# about the edge by a factor of two.
DEFAULT_KELLY_FRACTION = 0.25

# No single game gets more than this share of bankroll, whatever Kelly says.
DEFAULT_MAX_STAKE = 0.02

# Total exposure across a week's card. College Saturdays are correlated by
# weather, by conference officiating, and by whatever the model has got
# systematically wrong, so the sum is capped well below the sum of the parts.
DEFAULT_MAX_WEEKLY_EXPOSURE = 0.10


@dataclass(frozen=True)
class Stake:
    """A sizing decision and the numbers behind it."""

    full_kelly: float
    fractional_kelly: float
    recommended: float
    expected_value: float
    capped_by: str | None


def expected_value(outcome: CoverOutcome, payout: float) -> float:
    """Expected profit per unit staked. A push returns the stake, so it is
    worth exactly zero and drops out."""
    return outcome.win * payout - outcome.loss


def full_kelly(outcome: CoverOutcome, payout: float, *, tol: float = 1e-12) -> float:
    """Growth-optimal stake fraction, solved numerically to respect pushes.

    Maximises win*log(1 + f*b) + loss*log(1 - f), which has no closed form once
    push mass is present. The derivative is strictly decreasing on (0, 1), so
    bisection on the derivative is safe and fast.
    """
    if payout <= 0.0:
        raise ValueError(f"payout must be positive, got {payout}")
    if expected_value(outcome, payout) <= 0.0:
        return 0.0
    if outcome.loss <= 0.0:
        # Cannot lose, so growth is unbounded in f. Cap at the whole bankroll
        # and let the fraction and caps downstream bring it back to sanity.
        return 1.0

    def derivative(f: float) -> float:
        return outcome.win * payout / (1.0 + f * payout) - outcome.loss / (1.0 - f)

    lo, hi = 0.0, 1.0 - 1e-12
    if derivative(lo) <= 0.0:
        return 0.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if derivative(mid) > 0.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def size_bet(
    outcome: CoverOutcome,
    payout: float,
    *,
    kelly_fraction: float = DEFAULT_KELLY_FRACTION,
    max_stake: float = DEFAULT_MAX_STAKE,
) -> Stake:
    """Size a single bet as a fraction of bankroll."""
    ev = expected_value(outcome, payout)
    fk = full_kelly(outcome, payout)
    frac = fk * kelly_fraction
    recommended = min(frac, max_stake)
    capped = "max_stake" if recommended < frac - 1e-15 else None
    if ev <= 0.0:
        recommended, capped = 0.0, "negative_ev"
    return Stake(
        full_kelly=fk,
        fractional_kelly=frac,
        recommended=recommended,
        expected_value=ev,
        capped_by=capped,
    )


def apply_portfolio_cap(
    stakes: list[float], *, max_total: float = DEFAULT_MAX_WEEKLY_EXPOSURE
) -> list[float]:
    """Scale a week's stakes down proportionally if they sum past the cap.

    Proportional scaling rather than dropping the weakest bets: the ranking is
    the model's opinion and truncating it throws away information, while
    scaling preserves it.
    """
    total = sum(stakes)
    if total <= max_total or total <= 0.0:
        return list(stakes)
    scale = max_total / total
    return [s * scale for s in stakes]


def kelly_growth_rate(outcome: CoverOutcome, payout: float, f: float) -> float:
    """Expected log growth at a given stake. Useful for showing what
    overbetting costs: growth is concave and turns negative past 2x Kelly."""
    if f <= 0.0:
        return 0.0
    if f >= 1.0:
        return float("-inf")
    return outcome.win * math.log1p(f * payout) + outcome.loss * math.log1p(-f)
