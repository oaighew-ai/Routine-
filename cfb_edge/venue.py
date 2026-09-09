"""Where to express a line-movement edge, once you have one.

A closing-line-value edge is measured in points of line. Points are not
probability, and the exchange rate between them is the density of the margin
distribution at whatever number you are betting. That makes venue choice a
substantive question rather than an administrative one, and it produces a
result that is not obvious.

The instinct is that a prediction market's fee, which is largest at a coin flip
and smallest in the tails, favours tail strikes. That is half the picture and
the wrong half. The fee does fall in the tails, but so does the density, so the
same half point of line buys less probability out there. The two effects very
nearly cancel and the tails are not the answer.

The answer is key numbers. Three and seven carry roughly two and a half times
the mass of an ordinary margin, so a half point of line is worth two and a half
times as much probability there. That swamps every fee difference on the board.

Which leads to the actual advantage of an exchange, and it is not the fee. A
sportsbook sells you the one line it has posted, which sits near the median of
the game. An exchange sells you the whole ladder, so you can always choose to
express the edge at three or seven. When a book's posted line happens to land
on a key number, the book wins outright. When it does not, which is most of the
time, the exchange wins by being able to move to where the density is.
"""

from __future__ import annotations

from dataclasses import dataclass

from .distribution import KEY_BUMPS, margin_pmf, sigma_for_total
from .market import american_to_probability

# Kalshi's fee coefficient, as a required edge in probability: 0.07 * P * (1-P).
FEE_COEFFICIENT = 0.07


@dataclass(frozen=True)
class Expression:
    """One way of expressing a line edge: a venue and a number."""

    venue: str
    strike: float
    price: float
    density: float
    gain: float
    cost: float

    @property
    def net(self) -> float:
        return self.gain - self.cost

    @property
    def profitable(self) -> bool:
        return self.net > 0.0

    def describe(self) -> str:
        return (
            f"{self.venue} at {self.strike:+g} ({self.price:.1%}): "
            f"gain {self.gain:.3%} - cost {self.cost:.3%} = {self.net:+.3%}"
        )


def exchange_fee(price: float, *, coefficient: float = FEE_COEFFICIENT) -> float:
    """Required edge in probability to clear an exchange fee at `price`."""
    return coefficient * price * (1.0 - price)


def book_vig(american_price: float) -> float:
    """Required edge in probability over a coin flip at a book's price."""
    return american_to_probability(american_price) - 0.5


def rank_expressions(
    clv_points: float,
    *,
    market_margin: float = 0.0,
    total: float = 52.0,
    book_prices: tuple[float, ...] = (-110.0, -105.0, -103.0),
    coefficient: float = FEE_COEFFICIENT,
    price_bounds: tuple[float, float] = (0.05, 0.95),
) -> list[Expression]:
    """Every way of expressing `clv_points` of edge, best first.

    `market_margin` is the market's implied home margin, which is its posted
    line negated. It is deliberately not the model's projection: the density
    that turns a line move into probability is the density where the line
    actually sits, and the price you pay is the market's price. Feeding a
    projection here prices a contract nobody is offering. See `find_plays`.

    The book entries are pinned to the line a book would actually post, which
    is the game's median margin. The exchange entries range over the ladder,
    because that is the freedom being priced.
    """
    pmf = margin_pmf(market_margin, sigma_for_total(total))

    def survival(k: float) -> float:
        return sum(v for kk, v in pmf.items() if kk > k)

    median = min(pmf, key=lambda k: abs(survival(k) - 0.5))
    out: list[Expression] = []

    for price in book_prices:
        out.append(
            Expression(
                venue=f"book {price:+.0f}",
                strike=median,
                price=survival(median),
                density=pmf[median],
                gain=clv_points * pmf[median],
                cost=book_vig(price),
            )
        )

    lo, hi = price_bounds
    for k in sorted(pmf):
        p = survival(k)
        if not (lo < p < hi):
            continue
        out.append(
            Expression(
                venue="exchange",
                strike=k,
                price=p,
                density=pmf[k],
                gain=clv_points * pmf[k],
                cost=exchange_fee(p, coefficient=coefficient),
            )
        )

    return sorted(out, key=lambda e: e.net, reverse=True)


def best_strike(clv_points: float, **kw) -> Expression:
    """The single best way to express the edge. It is almost always a three."""
    ranked = rank_expressions(clv_points, **kw)
    if not ranked:
        raise ValueError("no expressible strikes in the given price bounds")
    return ranked[0]


def is_key_number(strike: float) -> bool:
    """Whether a strike sits on a margin that carries extra mass."""
    k = abs(int(strike)) if float(strike).is_integer() else None
    return k is not None and KEY_BUMPS.get(k, 1.0) > 1.0
