"""A game's whole strike ladder, read as one object.

Kalshi posts many spread strikes per game, each of the form "wins by over N
points". That is a survival function evaluated at N, so a game's ladder is a
picture of the market's entire margin distribution rather than a single number.
Two things follow, and neither needs a sportsbook, an assumed fair value, or
any opinion about who wins.

**A ladder has to be coherent.** `{margin > 7.5}` is a subset of
`{margin > 2.5}`, so the higher strike can never be worth more than the lower
one. When the tradeable prices invert, buying the low strike and selling the
high one pays at least 100 in every outcome. That is a lock, and it is
detectable inside a single game.

**Half-point strikes isolate single margins.** The gap between "over 2.5" and
"over 3.5" is exactly the probability the game ends on a three-point margin. So
a ladder states, out loud, what the market thinks the chance of each key number
is, and that can be compared against what actually happens. Retail flow prices
a ladder smoothly. Real football margins spike on 3 and 7. Where the market's
implied mass on a key number is well below the truth, the strikes bracketing it
are mispriced in a direction that does not depend on the teams.

Everything here works on bid and ask, never midpoints. A midpoint is not a
price you can trade, and an arbitrage measured on midpoints usually disappears
the moment you try to fill it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .distribution import KEY_BUMPS, margin_pmf, sigma_for_total
from .kalshi_fees import fee_cents_per_contract


@dataclass(frozen=True)
class Strike:
    """One rung: "wins by over `threshold` points", with its two-sided market."""

    threshold: float
    yes_bid: float | None = None
    yes_ask: float | None = None

    @property
    def mid(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return 0.5 * (self.yes_bid + self.yes_ask)

    @property
    def tradeable(self) -> bool:
        return self.yes_bid is not None and self.yes_ask is not None


@dataclass
class Ladder:
    """Every strike posted on one side of one game."""

    game: str
    team: str
    strikes: list[Strike]

    def __post_init__(self) -> None:
        self.strikes = sorted(self.strikes, key=lambda s: s.threshold)

    @property
    def thresholds(self) -> list[float]:
        return [s.threshold for s in self.strikes]


@dataclass(frozen=True)
class Arbitrage:
    """A coherence break that still pays after fees.

    Long YES on the low strike and long NO on the high strike returns at least
    100 in every outcome, so any total cost below 100 is locked profit.
    """

    low: float
    high: float
    cost_cents: float
    fee_cents: float
    profit_cents: float

    def describe(self) -> str:
        return (
            f"over {self.low} vs over {self.high}: buy the low strike and sell "
            f"the high one for {self.cost_cents:.2f}c, pay {self.fee_cents:.2f}c "
            f"in fees, locked profit {self.profit_cents:+.2f}c per contract pair"
        )


def find_arbitrage(ladder: Ladder, *, min_profit_cents: float = 0.0) -> list[Arbitrage]:
    """Coherence breaks worth taking after fees.

    A one cent inversion is not an opportunity: both legs pay a fee, and near a
    coin flip that is 3.5c of cost against 1c of edge. Only breaks that clear
    their own fees are returned.
    """
    out: list[Arbitrage] = []
    rungs = [s for s in ladder.strikes if s.tradeable]
    for i, low in enumerate(rungs):
        for high in rungs[i + 1:]:
            # Buy YES(low) at its ask; sell YES(high), i.e. buy NO(high) at
            # 100 - the YES bid.
            cost = low.yes_ask + (100.0 - high.yes_bid)
            if cost >= 100.0:
                continue
            fee = (
                fee_cents_per_contract(low.yes_ask / 100.0)
                + fee_cents_per_contract((100.0 - high.yes_bid) / 100.0)
            )
            profit = 100.0 - cost - fee
            if profit > min_profit_cents:
                out.append(
                    Arbitrage(
                        low=low.threshold,
                        high=high.threshold,
                        cost_cents=cost,
                        fee_cents=fee,
                        profit_cents=profit,
                    )
                )
    return sorted(out, key=lambda a: a.profit_cents, reverse=True)


def implied_mass(ladder: Ladder) -> dict[tuple[float, float], float]:
    """Probability the market assigns to each gap between consecutive strikes.

    Uses midpoints, because this is a description of what the market believes
    rather than a trade. Negative values mean the ladder is incoherent and
    should be run through `find_arbitrage` instead of read as a distribution.
    """
    out: dict[tuple[float, float], float] = {}
    rungs = [s for s in ladder.strikes if s.mid is not None]
    for low, high in zip(rungs, rungs[1:]):
        out[(low.threshold, high.threshold)] = (low.mid - high.mid) / 100.0
    return out


@dataclass(frozen=True)
class KeyNumberGap:
    """What the market implies for one key margin, against what football does."""

    margin: int
    implied: float
    model: float
    ratio: float

    @property
    def underpriced(self) -> bool:
        """The market is assigning less chance to this margin than it deserves,
        so the pair of strikes bracketing it is cheap."""
        return self.ratio < 1.0

    def describe(self) -> str:
        direction = "under" if self.underpriced else "over"
        return (
            f"margin of {self.margin}: market implies {self.implied:.2%}, "
            f"model says {self.model:.2%} ({direction}priced, "
            f"ratio {self.ratio:.2f})"
        )


def key_number_gaps(
    ladder: Ladder,
    *,
    model_margin: float,
    total: float = 52.0,
    key_numbers: Iterable[int] = tuple(KEY_BUMPS),
) -> list[KeyNumberGap]:
    """Compare the ladder's implied mass on each key margin against the model.

    Only margins isolated by a pair of half-point strikes one point apart are
    reported, because only those pin a single integer. A gap spanning several
    points mixes key and ordinary margins and says nothing clean.
    """
    pmf = margin_pmf(model_margin, sigma_for_total(total))
    mass = implied_mass(ladder)
    out: list[KeyNumberGap] = []
    for (low, high), implied in mass.items():
        if abs((high - low) - 1.0) > 1e-9:
            continue
        # (2.5, 3.5] isolates a margin of exactly 3.
        candidate = high - 0.5
        if abs(candidate - round(candidate)) > 1e-9:
            continue
        margin = int(round(candidate))
        if margin not in set(key_numbers):
            continue
        model = pmf.get(margin, 0.0)
        if implied <= 0 or model <= 0:
            continue
        out.append(
            KeyNumberGap(
                margin=margin, implied=implied, model=model, ratio=implied / model
            )
        )
    return sorted(out, key=lambda g: g.ratio)


@dataclass(frozen=True)
class StrikeEdge:
    """One rung priced against the model distribution, net of fees."""

    threshold: float
    side: str
    entry_cents: float
    model_cents: float
    gross_cents: float
    fee_cents: float
    net_cents: float
    net_ev: float

    @property
    def clears(self) -> bool:
        return self.net_cents > 0.0


def strike_edges(
    ladder: Ladder,
    *,
    model_margin: float,
    total: float = 52.0,
    key_bumps: Mapping[int, float] | None = None,
) -> list[StrikeEdge]:
    """Price every rung off the key-number-aware margin distribution.

    Buying YES is compared against the ask and selling YES against the bid, so
    each edge is measured at a price that could actually be filled.
    """
    pmf = margin_pmf(model_margin, sigma_for_total(total), key_bumps=key_bumps)
    out: list[StrikeEdge] = []
    for s in ladder.strikes:
        fair = sum(v for k, v in pmf.items() if k > s.threshold) * 100.0
        if s.yes_ask is not None:
            gross = fair - s.yes_ask
            fee = fee_cents_per_contract(s.yes_ask / 100.0)
            out.append(StrikeEdge(s.threshold, "buy yes", s.yes_ask, fair, gross,
                                  fee, gross - fee,
                                  (gross - fee) / s.yes_ask if s.yes_ask else 0.0))
        if s.yes_bid is not None:
            entry = 100.0 - s.yes_bid          # cost of the NO side
            gross = (100.0 - fair) - entry
            fee = fee_cents_per_contract(entry / 100.0)
            out.append(StrikeEdge(s.threshold, "sell yes", entry, 100.0 - fair,
                                  gross, fee, gross - fee,
                                  (gross - fee) / entry if entry else 0.0))
    return sorted(out, key=lambda e: e.net_ev, reverse=True)


def coherence_report(ladder: Ladder) -> str:
    """One-line health check before a ladder is used for anything."""
    rungs = [s for s in ladder.strikes if s.mid is not None]
    inversions = sum(
        1 for a, b in zip(rungs, rungs[1:]) if b.mid > a.mid + 1e-9
    )
    return (
        f"{ladder.team} ({ladder.game}): {len(ladder.strikes)} strikes, "
        f"{len(rungs)} two-sided, {inversions} inverted"
    )


@dataclass(frozen=True)
class BucketTrade:
    """The trade that actually harvests a key number.

    A single strike is a survival probability and moves with everything below
    it. To take a view on one margin you buy the lower strike and sell the
    upper one, which pays 100 only when the margin lands in the gap. On a pair
    of half-point strikes one point apart, that gap is a single integer.

    The instrument is what makes the test honest. Reading a key-number
    mispricing off one rung understates the cost, because a vertical spread
    crosses two bid-ask spreads and pays two fees, not one.
    """

    margin: int
    cost_cents: float
    fair_cents: float
    fee_cents: float
    net_cents: float
    breakeven_cost: float

    @property
    def clears(self) -> bool:
        return self.net_cents > 0.0

    def describe(self) -> str:
        return (
            f"margin {self.margin}: pay {self.cost_cents:.2f}c for something "
            f"worth {self.fair_cents:.2f}c, fees {self.fee_cents:.2f}c, "
            f"net {self.net_cents:+.2f}c "
            f"(needed the pair under {self.breakeven_cost:.2f}c)"
        )


def bucket_trades(
    ladder: Ladder,
    *,
    model_margin: float,
    total: float = 52.0,
    key_numbers: Iterable[int] | None = None,
) -> list[BucketTrade]:
    """Price the vertical spread that isolates each single margin.

    Buying the bucket costs `ask(low) - bid(high)`: you lift the lower strike
    and hit the bid on the upper one, so both sides of both spreads are paid.
    """
    pmf = margin_pmf(model_margin, sigma_for_total(total))
    keys = set(KEY_BUMPS if key_numbers is None else key_numbers)
    rungs = [s for s in ladder.strikes if s.tradeable]
    out: list[BucketTrade] = []
    for low, high in zip(rungs, rungs[1:]):
        if abs((high.threshold - low.threshold) - 1.0) > 1e-9:
            continue
        candidate = high.threshold - 0.5
        if abs(candidate - round(candidate)) > 1e-9:
            continue
        margin = int(round(candidate))
        if margin not in keys:
            continue
        cost = low.yes_ask - high.yes_bid
        if cost <= 0:
            continue
        fair = pmf.get(margin, 0.0) * 100.0
        fee = (
            fee_cents_per_contract(low.yes_ask / 100.0)
            + fee_cents_per_contract(high.yes_bid / 100.0)
        )
        out.append(
            BucketTrade(
                margin=margin,
                cost_cents=cost,
                fair_cents=fair,
                fee_cents=fee,
                net_cents=fair - cost - fee,
                breakeven_cost=fair - fee,
            )
        )
    return sorted(out, key=lambda b: b.net_cents, reverse=True)
