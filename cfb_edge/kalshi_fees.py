"""Kalshi's fee, and why a flat cent bar is the wrong shape for it.

Kalshi charges `round_up(0.07 * C * P * (1 - P))` on a fill, where P is the
price in dollars and C the contract count. The `P * (1 - P)` term peaks at
P = 0.50 and falls away toward both tails, so the fee is largest exactly on
coin-flip markets and smallest on longshots and heavy favourites.

That shape matters because a cross-market engine comparing a sportsbook's
de-vigged fair value against a Kalshi fill will find its apparent edges
clustered near 50c, where books post -110 both ways and the de-vig lands on
exactly 50. Those are the same markets where Kalshi's fee is at its maximum of
1.75c per contract. A gross edge of 3c looks identical at 21c and at 49c, but
after fees one is worth 2.7c and the other is worth negative three quarters of
a cent.

So a flat entry bar in cents systematically lets the worst trades through and
holds the best ones back. The bar has to bend with the fee curve.

Fee schedules vary by product and maker orders are priced differently, so treat
the 0.07 coefficient as the documented general case rather than a universal
constant, and read the real rate off the account before staking anything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# The documented general-case coefficient. Some Kalshi products differ.
FEE_COEFFICIENT = 0.07


def fee_dollars(contracts: int, price: float, *, coefficient: float = FEE_COEFFICIENT) -> float:
    """Exact fee for a fill, including Kalshi's round-up to the next cent."""
    if not 0.0 < price < 1.0:
        raise ValueError(f"price must be a probability in dollars, got {price}")
    if contracts <= 0:
        raise ValueError(f"contracts must be positive, got {contracts}")
    raw = coefficient * contracts * price * (1.0 - price)
    return math.ceil(raw * 100.0) / 100.0


def fee_cents_per_contract(price: float, *, coefficient: float = FEE_COEFFICIENT) -> float:
    """Fee per contract in cents, ignoring the round-up.

    The round-up is applied once per order, so on any realistic size it washes
    out and this is the number to reason with. Peaks at 1.75c when the
    coefficient is 0.07 and the price is 50c.
    """
    return coefficient * price * (1.0 - price) * 100.0


def required_gross_edge_cents(
    price: float, *, target_net_cents: float = 1.0, coefficient: float = FEE_COEFFICIENT
) -> float:
    """Gross edge needed at this price to clear `target_net_cents` after fees.

    This is the bar the entry check should be using. It is a curve, not a
    constant: about 2.75c at a coin flip and about 2.16c at 21c, for a one cent
    net target.
    """
    return target_net_cents + fee_cents_per_contract(price, coefficient=coefficient)


@dataclass(frozen=True)
class NetEdge:
    """A gross cross-market edge, restated after Kalshi's fee."""

    price: float
    gross_cents: float
    fee_cents: float
    net_cents: float
    gross_ev: float
    net_ev: float
    fee_share_of_gross: float

    @property
    def survives_fees(self) -> bool:
        return self.net_cents > 0.0

    def describe(self) -> str:
        verdict = "clears" if self.survives_fees else "NEGATIVE after fees"
        return (
            f"at {self.price * 100:.1f}c: gross {self.gross_cents:+.2f}c "
            f"(EV {self.gross_ev:+.2%}) - fee {self.fee_cents:.2f}c "
            f"= net {self.net_cents:+.2f}c (EV {self.net_ev:+.2%}) -> {verdict}"
        )


def net_edge(
    price: float, gross_cents: float, *, coefficient: float = FEE_COEFFICIENT
) -> NetEdge:
    """Restate a gross cent edge as what actually reaches the account.

    `gross_cents` is the gap between the sportsbook's de-vigged fair value and
    the Kalshi fill, in cents, which is what a cross-market gate reports before
    costs. EV is expressed against the entry price, matching the convention a
    board like this usually prints.
    """
    fee = fee_cents_per_contract(price, coefficient=coefficient)
    net = gross_cents - fee
    entry_cents = price * 100.0
    return NetEdge(
        price=price,
        gross_cents=gross_cents,
        fee_cents=fee,
        net_cents=net,
        gross_ev=gross_cents / entry_cents,
        net_ev=net / entry_cents,
        fee_share_of_gross=fee / gross_cents if gross_cents > 0 else float("inf"),
    )
