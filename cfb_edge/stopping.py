"""When to stop betting this, decided before there is money on the line.

A stop rule set after a losing month is not a stop rule, it is a
rationalisation with a number attached. This one is fixed in advance and it is
a sequential probability ratio test on closing line value, because CLV is the
only quantity whose sample size this strategy can ever support. Profit at
twenty bets is noise: at a true 55% edge a four-bet week loses money about a
third of the time.

**The parameters are measured, not chosen.** Replaying the strategy over 2,611
historical games carrying a consensus opening and closing spread from two or
more books gives 1,375 bets at +0.376 points of CLV, t = +5.01, with a
per-bet standard deviation of **2.78 points**. (`MODEL.md` records the model's
own run as 1,059 bets at +0.44 and t = +4.7; the replication here uses a
cruder Elo-to-margin conversion, fitted at 24.2 Elo points per point of margin
against the standard figure of about 25.) The test below is against the
documented claim of 0.44 rather than the replication's 0.376, because the
claim is the thing on trial.

    H0: the strategy earns no CLV        H1: it earns the claimed 0.44 points
    alpha = 0.05                         beta = 0.20

Alpha is the tighter of the two deliberately. Continuing a dead strategy bleeds
money every week; abandoning a live one forgoes an edge that is marginal even
when real, worth about one cent on a contract. Being quick to kill is the
cheaper mistake.

**What this rule can and cannot do in one season.** At roughly two plays a week
over a thirteen-week season you place about 26 bets. Simulation over 40,000
runs says that at 26 bets a genuinely dead strategy is stopped only 8.7% of the
time, and no strategy can be *confirmed* at all: clearing the upper boundary by
bet 26 needs +2.09 points a bet, five times the claim. So a season is a
tripwire for breakage and nothing more, and the running total carries across
seasons rather than resetting.

The fix for that is not a looser rule. It is more observations: the signal
fires on roughly half of all games with a captured opening line, while only the
few that clear their own fee reach a card. Recording the CLV of every signal,
bet or not, turns a three-season question into a several-week one. Nothing has
to be at risk to measure a number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

# Measured over 1,375 replayed bets. See the module docstring.
CLV_SD_PER_BET = 2.78
CLAIMED_CLV = 0.44
ALPHA = 0.05
BETA = 0.20


def _boundaries() -> tuple[float, float, float]:
    """Lower intercept, upper intercept, and the slope both boundaries share."""
    a = math.log((1.0 - BETA) / ALPHA)
    b = math.log(BETA / (1.0 - ALPHA))
    variance = CLV_SD_PER_BET ** 2
    return b * variance / CLAIMED_CLV, a * variance / CLAIMED_CLV, CLAIMED_CLV / 2.0


LOWER_INTERCEPT, UPPER_INTERCEPT, SLOPE = _boundaries()


def kill_threshold(bets: int) -> float:
    """Cumulative CLV at or below which the strategy is abandoned."""
    return LOWER_INTERCEPT + SLOPE * bets


def confirm_threshold(bets: int) -> float:
    """Cumulative CLV at or above which the edge is taken as established."""
    return UPPER_INTERCEPT + SLOPE * bets


@dataclass(frozen=True)
class Verdict:
    """Where a run of bets stands against the rule fixed in advance."""

    bets: int
    cumulative_clv: float
    kill_at: float
    confirm_at: float

    # Both boundaries are inclusive, and a sum of floats lands on one only to
    # within rounding. Without a tolerance a run that is 1e-15 short reads
    # CONTINUE, which contradicts the rule as written. Far below a half point,
    # the smallest move any real line makes.
    TOLERANCE = 1e-9

    @property
    def decision(self) -> str:
        if self.bets == 0:
            return "no graded bets yet"
        if self.cumulative_clv <= self.kill_at + self.TOLERANCE:
            return "STOP"
        if self.cumulative_clv >= self.confirm_at - self.TOLERANCE:
            return "CONFIRMED"
        return "CONTINUE"

    @property
    def margin_to_kill(self) -> float:
        """Points of CLV between here and abandoning the strategy."""
        return self.cumulative_clv - self.kill_at

    def summary(self) -> str:
        if self.bets == 0:
            return ("Stop rule: no graded bets yet. It needs a closing line on "
                    "each bet, which `settle` records.")
        head = (f"Stop rule after {self.bets} graded bets: **{self.decision}**\n"
                f"  cumulative CLV {self.cumulative_clv:+.1f} pts "
                f"({self.cumulative_clv / self.bets:+.2f} per bet)\n"
                f"  stop at {self.kill_at:+.1f}, confirm at {self.confirm_at:+.1f}")
        if self.decision == "STOP":
            return head + "\n  The rule was set before the money was. Stop."
        if self.decision == "CONFIRMED":
            return head + "\n  The edge cleared its pre-registered bar."
        return (head + f"\n  {self.margin_to_kill:+.1f} pts of room before the "
                f"stop boundary. Confirming needs a long run: see stopping.py.")


def evaluate(clvs: Sequence[float] | Iterable[float]) -> Verdict:
    """Apply the rule to the CLV of every graded bet, oldest first."""
    values = [float(x) for x in clvs]
    n = len(values)
    return Verdict(bets=n, cumulative_clv=sum(values),
                   kill_at=kill_threshold(n), confirm_at=confirm_threshold(n))
