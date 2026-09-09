"""How much closing line value you need before it pays for itself.

Beating the closing line is the right scoreboard, but it is not automatically
profit. A half point of edge on a market that needs a full point is still a
losing bet, and a bettor watching a positive and statistically solid CLV number
while their bankroll drains is looking at exactly that.

The arithmetic is short. A price implies a break-even win rate. A point of line
is worth some amount of win probability, which is the local density of the
margin distribution. Divide one by the other and you have the CLV a strategy
must clear at that price, in points.

Measured on real data, an opening-line strategy on this repository's ratings
earns between 0.22 and 0.83 points of CLV depending on selectivity, with
t-statistics from 3.2 to 5.5. It is real. At -110 it needs about a point, so it
does not clear. It would clear at about -103.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .distribution import margin_pmf, sigma_for_total
from .market import american_to_probability


def local_density(
    line: float = 0.0, *, total: float = 52.0, window: int = 3,
) -> float:
    """Win probability gained per point of line, near `line`.

    Averaged over a window of integer margins rather than read off a single
    one, because the margin distribution is lumpy and a point-estimate at a key
    number would overstate the density while one in a trough would understate
    it.

    The distribution is centred on `line` and the window is taken around the
    same place, and there is deliberately no way to separate the two. A line is
    the market's own estimate of the margin, so a density read at a line under
    a distribution centred somewhere else is the density of a game nobody is
    offering. That separation used to be reachable here through a
    `projected_margin` argument, and the same mistake priced a real card
    thirteen cents wrong. See `find_plays`.
    """
    pmf = margin_pmf(-line, sigma_for_total(total))
    centre = -line
    lo, hi = math.floor(centre - window), math.ceil(centre + window)
    mass = sum(v for k, v in pmf.items() if lo <= k <= hi)
    span = hi - lo
    return mass / span if span else 0.0


@dataclass(frozen=True)
class Requirement:
    """What a price demands of a strategy, and whether it is met."""

    price: float
    break_even: float
    density: float
    clv_needed: float
    clv_achieved: float | None = None

    @property
    def clears(self) -> bool | None:
        if self.clv_achieved is None:
            return None
        return self.clv_achieved > self.clv_needed

    def describe(self) -> str:
        base = (
            f"at {self.price:+.0f} you need {self.break_even:.2%} to break even, "
            f"so {self.clv_needed:.2f} points of CLV"
        )
        if self.clv_achieved is None:
            return base
        verdict = "clears" if self.clears else "does NOT clear"
        return f"{base}; {self.clv_achieved:.2f} achieved -> {verdict}"


def clv_required(
    price: float = -110.0, *, total: float = 52.0, achieved: float | None = None
) -> Requirement:
    """Points of closing line value needed to break even at `price`."""
    break_even = american_to_probability(price)
    density = local_density(total=total)
    needed = (break_even - 0.5) / density if density > 0 else float("inf")
    return Requirement(
        price=price,
        break_even=break_even,
        density=density,
        clv_needed=max(0.0, needed),
        clv_achieved=achieved,
    )


def break_even_price(clv: float, *, total: float = 52.0) -> float:
    """The worst price at which `clv` points of edge still breaks even.

    The practical output: a strategy earning half a point of CLV needs a book
    at roughly -105, and one earning a quarter of a point needs -102. Those are
    reduced-juice numbers, not standard ones, which is why an edge this size
    lives or dies on where it is bet rather than on how good it is.
    """
    density = local_density(total=total)
    win = 0.5 + clv * density
    if win >= 1.0:
        return -100.0
    if win <= 0.5:
        return -float("inf")
    # American price whose implied probability equals this win rate.
    return -100.0 * win / (1.0 - win) if win >= 0.5 else 0.0
