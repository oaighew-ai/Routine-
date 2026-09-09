"""American odds, implied probability, and removing the vig.

Devigging matters more than it looks. A -110/-110 spread market implies 52.38%
on each side, summing to 104.8%. How you strip that 4.8% back out changes the
fair probability by up to a point, and a point of probability is roughly a
third of the edge you are hunting for. Proportional devigging is the common
default and it is biased: it shaves too much off the favorite and too little
off the underdog. Shin's method assumes some of the overround exists to protect
the book against informed money, which is closer to how books actually price,
and it corrects most of that bias.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

# Prices longer than this are treated as unquotable rather than as real numbers.
MAX_AMERICAN = 100000


def american_to_decimal(price: float) -> float:
    """Convert American odds to decimal odds (including the returned stake)."""
    if price == 0 or abs(price) < 100:
        raise ValueError(f"not a valid American price: {price}")
    if price > 0:
        return 1.0 + price / 100.0
    return 1.0 + 100.0 / abs(price)


def decimal_to_american(decimal: float) -> float:
    if decimal <= 1.0:
        raise ValueError(f"decimal odds must exceed 1.0, got {decimal}")
    if decimal >= 2.0:
        return round((decimal - 1.0) * 100.0, 2)
    return round(-100.0 / (decimal - 1.0), 2)


def american_to_probability(price: float) -> float:
    """Implied probability *including* the vig. This is a raw price, not a
    fair probability. Pass it through a devig before using it as a belief."""
    return 1.0 / american_to_decimal(price)


def probability_to_american(prob: float) -> float:
    if not 0.0 < prob < 1.0:
        raise ValueError(f"probability must be strictly between 0 and 1, got {prob}")
    return decimal_to_american(1.0 / prob)


def payout_multiple(price: float) -> float:
    """Profit per unit staked on a win. -110 returns 0.909..."""
    return american_to_decimal(price) - 1.0


def overround(prices: Sequence[float]) -> float:
    """Total implied probability across a market. 1.048 means a 4.8% hold."""
    return sum(american_to_probability(p) for p in prices)


def devig_multiplicative(prices: Sequence[float]) -> list[float]:
    """Scale every implied probability by the same factor.

    The most common choice, and the most biased. It assumes the book's margin
    is spread proportionally across outcomes. Real books take a larger margin
    on longshots than on favourites, so proportional scaling leaves too much
    vig on the favourite and hands it a fair probability that is too low. On a
    -800/+550 market it prices the favourite at 85.2% where the equal-margin
    methods say 86.8%. Kept as the baseline worth measuring the others against.
    """
    raw = [american_to_probability(p) for p in prices]
    total = sum(raw)
    return [r / total for r in raw]


def devig_additive(prices: Sequence[float]) -> list[float]:
    """Subtract the margin equally from each outcome.

    The opposite bias to multiplicative: it takes too much off the underdog and
    can produce negative probabilities on lopsided markets, so it falls back to
    multiplicative when it would.
    """
    raw = [american_to_probability(p) for p in prices]
    excess = (sum(raw) - 1.0) / len(raw)
    adjusted = [r - excess for r in raw]
    if any(a <= 0.0 for a in adjusted):
        return devig_multiplicative(prices)
    return adjusted


def devig_power(prices: Sequence[float], tol: float = 1e-10) -> list[float]:
    """Raise each raw probability to a common power k such that they sum to 1.

    Sits between multiplicative and additive in how it distributes the margin.
    k is found by bisection; it is always positive and the sum is monotonically
    decreasing in k, so the search is well behaved.
    """
    raw = [american_to_probability(p) for p in prices]
    if abs(sum(raw) - 1.0) < tol:
        return list(raw)

    def total_at(k: float) -> float:
        return sum(r ** k for r in raw)

    lo, hi = 1e-6, 1.0
    # Raw probabilities are each < 1, so a larger exponent shrinks the sum.
    while total_at(hi) > 1.0 and hi < 64.0:
        hi *= 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if total_at(mid) > 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    k = 0.5 * (lo + hi)
    out = [r ** k for r in raw]
    total = sum(out)
    return [o / total for o in out]


def devig_shin(prices: Sequence[float], tol: float = 1e-12) -> list[float]:
    """Shin's method: assume a fraction z of money comes from insiders.

    The book widens prices to protect itself against bettors who know more than
    it does, and backing that protection out recovers a fairer probability than
    proportional scaling.

    Worth knowing before you reach for it: on a two-outcome market Shin is not
    a third answer. Solving for z and substituting reproduces `devig_additive`
    exactly, to floating point. That is verified in the test suite rather than
    asserted here. The z it recovers is still informative on its own (0.03 to
    0.05 on typical college football markets, i.e. the book is pricing as if
    3-5% of money is sharp), so the solver is kept and exposed via `shin_z`.
    For spreads and totals, which are always two-way, the real choice is only
    between multiplicative, additive/Shin, and power.
    """
    if len(prices) != 2:
        return devig_power(prices)

    raw = [american_to_probability(p) for p in prices]
    total = sum(raw)
    if abs(total - 1.0) < tol:
        return list(raw)

    def shin_sum(z: float) -> float:
        return sum(
            math.sqrt(z * z + 4.0 * (1.0 - z) * (r * r) / total) for r in raw
        )

    lo, hi = 0.0, 0.99
    # shin_sum is decreasing in z; we want it to equal 2.
    if shin_sum(hi) > 2.0:
        return devig_power(prices)
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if shin_sum(mid) > 2.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    z = 0.5 * (lo + hi)
    if z >= 1.0 - 1e-9:
        return devig_power(prices)

    out = [
        (math.sqrt(z * z + 4.0 * (1.0 - z) * (r * r) / total) - z) / (2.0 * (1.0 - z))
        for r in raw
    ]
    s = sum(out)
    return [o / s for o in out]


def shin_z(prices: Sequence[float], tol: float = 1e-12) -> float:
    """Recover Shin's insider fraction z from a two-way market.

    Unlike the probabilities, z is genuinely extra information: it says how
    much of the book's overround is protection against sharp money rather than
    flat profit margin. A market with an unusually high z is one the book is
    nervous about, which is a weak signal that it has already been hit.
    """
    if len(prices) != 2:
        raise ValueError("Shin's z is only defined here for two-way markets")
    raw = [american_to_probability(p) for p in prices]
    total = sum(raw)
    if abs(total - 1.0) < tol:
        return 0.0

    def shin_sum(z: float) -> float:
        return sum(math.sqrt(z * z + 4.0 * (1.0 - z) * (r * r) / total) for r in raw)

    lo, hi = 0.0, 0.99
    if shin_sum(hi) > 2.0:
        return 0.0
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if shin_sum(mid) > 2.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


DEVIG_METHODS = {
    "multiplicative": devig_multiplicative,
    "additive": devig_additive,
    "power": devig_power,
    "shin": devig_shin,
}


def devig(prices: Sequence[float], method: str = "shin") -> list[float]:
    try:
        fn = DEVIG_METHODS[method]
    except KeyError:
        raise ValueError(
            f"unknown devig method {method!r}; choose from {sorted(DEVIG_METHODS)}"
        ) from None
    return fn(prices)


@dataclass(frozen=True)
class Quote:
    """One book's price on one side of one market."""

    book: str
    line: float
    price: float


def best_quote(quotes: Iterable[Quote], *, prefer_higher: bool = True) -> Quote:
    """Pick the quote a bettor would actually take.

    Line shopping is the least glamorous and most reliable edge in sports
    betting, so the model always bets the best available number rather than a
    consensus one. Line is the primary key and price breaks the tie, because
    half a point around a key number is worth far more than a few cents of
    juice.

    For a side bet, leave `prefer_higher` alone. A spread bet wins when
    `margin + line > 0`, so a larger line always weakly dominates, whether you
    are laying (-2.5 beats -3.0) or taking (+7.0 beats +6.5). Totals are the
    only case that flips: an over wants the lowest number available, so pass
    `prefer_higher=False` there. An under wants the highest, like a side.
    """
    quotes = list(quotes)
    if not quotes:
        raise ValueError("no quotes to choose from")
    sign = -1.0 if prefer_higher else 1.0
    return min(quotes, key=lambda q: (sign * q.line, -q.price))


def consensus_line(quotes: Sequence[Quote]) -> float:
    """Median line across books.

    Median rather than mean: one book leaving a stale number should show up as
    an opportunity in `best_quote`, not drag the reference point the model
    measures its edge against.
    """
    if not quotes:
        raise ValueError("no quotes to average")
    lines = sorted(q.line for q in quotes)
    mid = len(lines) // 2
    if len(lines) % 2 == 1:
        return lines[mid]
    return 0.5 * (lines[mid - 1] + lines[mid])


@dataclass(frozen=True)
class Hold:
    """A book's realised margin, measured from its own quotes."""

    quotes: int
    median_hold: float
    equivalent_price: float

    def clears(self, needed_price: float) -> bool:
        """Whether this book is at least as cheap as `needed_price`.

        American prices closer to zero are cheaper, so -103 clears -105 and
        -110 does not.
        """
        return self.equivalent_price >= needed_price

    def describe(self) -> str:
        return (
            f"{self.quotes} two-sided quotes, median hold "
            f"{self.median_hold:.2%}, equivalent to "
            f"{self.equivalent_price:+.0f} on both sides"
        )


def realized_hold(two_sided_quotes: Iterable[tuple[float, float]]) -> Hold:
    """Measure what a book actually charges, rather than what it advertises.

    Takes pairs of American prices, one market at a time, and reports the
    median. Median rather than mean because a handful of shaded or stale
    markets should not move the estimate, and because what matters is the price
    you meet on a typical bet rather than the average across outliers.

    Worth doing before trusting any claim about reduced juice. A book that
    advertises -105 may post it on marquee games and -110 everywhere else, and
    the difference decides whether a half-point edge is a business or a slow
    loss. Measured over 2006-2019 in this project's data, exactly three books
    held at or under 2.44%: an exchange at -103 and two offshore books at -105.
    Every mainstream book sat at -110 to the cent.
    """
    holds = sorted(
        overround(pair) for pair in two_sided_quotes if len(pair) == 2
    )
    if not holds:
        raise ValueError("no two-sided quotes to measure")
    mid = len(holds) // 2
    median = (
        holds[mid] if len(holds) % 2 else 0.5 * (holds[mid - 1] + holds[mid])
    )
    return Hold(
        quotes=len(holds),
        median_hold=median - 1.0,
        equivalent_price=probability_to_american(median / 2.0),
    )
