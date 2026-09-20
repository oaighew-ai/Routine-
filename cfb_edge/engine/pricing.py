"""The executable price, net of the venue's fee, and the Kelly fraction on it.

Law 4: the de-vigged consensus is a belief, not a price. Everything that decides
whether there is a bet happens against the number you can actually be filled at,
after the venue takes its cut. BUILD_PROMPT §6.1's worked example is the whole
point of this module: a pick 1.5 points above no-vig at -110 is -1.7% EV.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..distribution import CoverOutcome
from ..kalshi_fees import fee_cents_per_contract
from ..market import american_to_decimal, american_to_probability, decimal_to_american
from ..staking import full_kelly
from . import reasons


class UnsupportedVenue(ValueError):
    """Raised rather than guessing a fee rule. §6.12: never from memory."""


@dataclass(frozen=True)
class Executable:
    """One price, restated as what it actually pays after fees."""

    venue: str
    quoted: float
    kind: str
    payout: float          # profit per unit of total outlay, on a win
    fee_fraction: float    # the venue's cut, as a share of total outlay
    decimal: float         # total return per unit of outlay, on a win

    @property
    def breakeven(self) -> float:
        """Win probability this price requires to be a coin flip in EV terms."""
        return 1.0 / (1.0 + self.payout)


def executable(
    quoted: float,
    *,
    kind: str = "american",
    venue: str = "book_default",
    venue_config: Mapping[str, Any] | None = None,
) -> Executable:
    """Restate a quoted price as payout per unit of total outlay, after fees.

    Every price format collapses to the same shape first: what it costs, in
    cents, to hold something that pays 100. A -110 American price costs 52.38;
    a 49c exchange contract costs 49. The venue's fee is then added to the
    outlay rather than netted off the win, because on an exchange the fee is
    paid at fill whether or not the contract settles your way, and treating it
    as a haircut on the win alone understates the cost of a loss.

    `kind` is the format of *this quote*, not a property of the venue. That
    distinction cost a round of debugging worth writing down: The Odds API
    returns Kalshi's prices in American odds like everyone else's when asked
    for `oddsFormat=american`, so a venue config that declared Kalshi
    "exchange cents" rejected every price the scan actually receives. The fee
    rule belongs to the venue; the format belongs to the quote.
    """
    cfg = dict(venue_config or {})

    # The fee rule is checked before the price is even parsed. An unknown rule
    # is a configuration error and should say so, rather than surfacing as
    # whatever the price parser happens to complain about first.
    rule = cfg.get("fee_rule", "none")
    if rule not in ("none", "kalshi_general"):
        raise UnsupportedVenue(
            f"no encoded fee rule named {rule!r}. §6.12 requires the rule to "
            f"come from the venue's published schedule with a unit test citing "
            f"the URL, never from memory."
        )

    if kind == "american":
        cents = american_to_probability(quoted) * 100.0
    elif kind == "decimal":
        if quoted <= 1.0:
            raise ValueError(f"decimal odds must exceed 1.0, got {quoted}")
        cents = 100.0 / quoted
    elif kind == "exchange_cents":
        if not 0.0 < quoted < 100.0:
            raise ValueError(f"exchange price must be in (0, 100) cents, got {quoted}")
        cents = float(quoted)
    else:
        raise UnsupportedVenue(f"unknown price kind {kind!r}")

    fee = 0.0
    if rule == "kalshi_general":
        fee = fee_cents_per_contract(
            cents / 100.0, coefficient=float(cfg.get("fee_coefficient", 0.07))
        )

    outlay = cents + fee
    if outlay >= 100.0:
        # The fee has eaten the whole contract. Not a price.
        return Executable(venue, quoted, kind, 0.0, 1.0, 1.0)
    payout = (100.0 - outlay) / outlay
    return Executable(venue, quoted, kind, payout, fee / outlay, 1.0 + payout)


def outcome(p_post: float, *, push: float = 0.0) -> CoverOutcome:
    """A posterior probability as a resolvable outcome.

    `p_post` is the win rate among games that resolve, matching
    `CoverOutcome.win_excluding_push`. Push mass is carried separately so the
    push-aware Kelly solve in `staking.full_kelly` has something to work with on
    whole-number spreads.
    """
    if not 0.0 <= push < 1.0:
        raise ValueError(f"push mass must be in [0, 1), got {push}")
    live = 1.0 - push
    return CoverOutcome(win=p_post * live, push=push, loss=(1.0 - p_post) * live)


@dataclass(frozen=True)
class PriceDecision:
    """What one price does to one posterior."""

    price: Executable
    ev: float
    f_full: float

    @property
    def playable(self) -> bool:
        """Law 5: the only gate. There is no second threshold."""
        return self.f_full > 0.0


def decide_at_price(
    p_post: float,
    price: Executable,
    *,
    push: float = 0.0,
) -> PriceDecision:
    """EV per unit staked and the full-Kelly fraction at this price."""
    out = outcome(p_post, push=push)
    ev = out.win * price.payout - out.loss
    f = full_kelly(out, price.payout) if price.payout > 0.0 else 0.0
    return PriceDecision(price=price, ev=ev, f_full=f)


def max_playable_price(
    p_post: float,
    *,
    venue: str = "book_default",
    venue_config: Mapping[str, Any] | None = None,
    push: float = 0.0,
    grid: tuple[int, int, int] = (-400, 400, 1),
) -> float | None:
    """The worst American price at which `f_full > 0` still holds.

    §6.3: swept across a grid, never obtained by inverting `p_post`. Inverting
    would give the price at which EV is exactly zero, which is not the same
    question once a fee schedule bends the payout, and is wrong at any venue
    whose cost depends on the price.

    Worst means most expensive, so the sweep runs from longest odds to shortest
    and keeps the last price that still clears.
    """
    low, high, step = grid
    worst: float | None = None
    # Ordered cheapest (+400) to most expensive (-400): a bettor prefers a
    # larger American number, so walking down finds the last one that works.
    for cents in range(high, low - 1, -step):
        if -100 < cents < 100:
            continue
        try:
            px = executable(float(cents), kind="american",
                            venue=venue, venue_config=venue_config)
        except (ValueError, UnsupportedVenue):
            continue
        if decide_at_price(p_post, px, push=push).playable:
            worst = float(cents)
        elif worst is not None:
            break
    return worst


def american_from_probability(p: float) -> float:
    """Convenience for reporting only. Never used to make a decision."""
    return decimal_to_american(1.0 / p)


__all__ = [
    "Executable", "PriceDecision", "UnsupportedVenue",
    "executable", "outcome", "decide_at_price", "max_playable_price",
    "american_from_probability", "reasons",
]
