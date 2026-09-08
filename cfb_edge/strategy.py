"""The one strategy the evidence in this repository actually supports.

Everything else here is apparatus. This module is the conclusion, and it is
narrow because the evidence is narrow. Five findings survived testing, and
together they leave exactly one thing worth doing.

**The model cannot predict games.** Regressed against 6,398 real closing lines
with the ratings fit walk-forward, its incremental coefficient is -0.02 with a
t of -0.31. The closing line already contains everything it knows, which is why
`blend.MAX_MODEL_WEIGHT` is zero and the power-rating card is empty.

**The model can predict line movement.** Backing the side an Elo fair value
prefers against the opening number earns positive closing line value in every
configuration tried, 0.22 to 0.83 points, with t-statistics from 3.2 to 5.5.
Predicting where the market is going is a different and easier problem than
predicting the game.

**That edge is about half the size it needs to be.** A point of line is worth
roughly 0.036 of win probability near a pick'em, so -110 demands 0.67 points
and the strategy earns around 0.44.

**Key numbers close the gap.** Three and seven carry about 2.6 and 2.3 times
the mass of an ordinary margin, fitted on 17,472 games. The same 0.44 points of
line is worth about 2.8% of win probability at a three and about 1.0% anywhere
else. Nothing else on the board moves the arithmetic that far.

**Venue decides the rest.** A book sells the one line it posted; an exchange
sells the whole ladder. When a book's line lands on a key number its 1.22% of
vig at -105 beats an exchange's 1.75% fee on the same proposition. When it does
not, only the exchange can move to where the density is. A -110 book clears
only when its posted line is a key number sitting near the projected margin,
where the density peaks, and even then by about a third of a percent against the
-105 book's one and a half.

So: bet line movement, only at key numbers, only where the venue is cheap
enough, sized at a quarter of Kelly. That is a small strategy that fires on
maybe a sixth of the board, and it is the only one here whose arithmetic closes.

It is still unproven in the one way that matters. The CLV is solid; the profit
is not demonstrated, and the realised win rates do not confirm it at the sample
sizes available. Treat what follows as the best-supported hypothesis in the
repository, not as a result.
"""

from __future__ import annotations

from dataclasses import dataclass

from .distribution import KEY_BUMPS, margin_pmf, sigma_for_total
from .venue import book_vig, exchange_fee

# Measured closing line value, in points, from the opening-line strategy at its
# most reliable setting: 2+ books quoting the open, 4-point minimum signal.
DEFAULT_CLV_POINTS = 0.44

# Only these margins carry enough extra mass to be worth expressing an edge on.
# Ten and fourteen are real but marginal; three and seven do the work.
TRADEABLE_KEY_NUMBERS = (3, 7, 10, 14)

# Fraction of Kelly, and the per-bet ceiling. Both from `staking.py`.
KELLY_FRACTION = 0.25
MAX_STAKE = 0.02


@dataclass(frozen=True)
class Venue:
    """Somewhere a bet can actually be placed."""

    name: str
    # A book quotes an American price on a posted line; an exchange quotes a
    # probability on any strike. Exactly one of these applies.
    american_price: float | None = None
    is_exchange: bool = False

    def cost(self, strike_price: float) -> float:
        """Required edge in probability at this venue."""
        if self.is_exchange:
            return exchange_fee(strike_price)
        if self.american_price is None:
            raise ValueError(f"{self.name} has neither a price nor an exchange flag")
        return book_vig(self.american_price)


@dataclass(frozen=True)
class Play:
    """A bet the evidence supports, with the arithmetic behind it."""

    game: str
    side: str
    number: int
    venue: str
    # Price of the side actually being backed. `survival(number)` is the home
    # side's price, so an away bet pays its complement, and quoting the home
    # number would have you asking for the wrong side of the book.
    strike_price: float
    density: float
    gain: float
    cost: float
    stake: float

    @property
    def net(self) -> float:
        return self.gain - self.cost

    def describe(self) -> str:
        return (
            f"{self.game}: {self.side} at {self.number:+d} on {self.venue} "
            f"({self.strike_price:.0%}) | gain {self.gain:.2%} "
            f"- cost {self.cost:.2%} = {self.net:+.2%} | stake {self.stake:.2%}"
        )


def _quarter_kelly(net_edge: float, strike_price: float) -> float:
    """Stake as a fraction of bankroll for a contract bought at `strike_price`.

    Full Kelly for a binary priced at p that settles at 1 is edge / (1 - p);
    taken at a quarter and capped, because the edge is an estimate and the
    estimate came from a t-statistic, not a guarantee.
    """
    if net_edge <= 0 or not 0.0 < strike_price < 1.0:
        return 0.0
    return min(MAX_STAKE, KELLY_FRACTION * net_edge / (1.0 - strike_price))


def find_plays(
    game: str,
    *,
    projected_margin: float,
    side: str,
    venues: list[Venue],
    posted_line: float | None = None,
    total: float = 52.0,
    clv_points: float = DEFAULT_CLV_POINTS,
    key_numbers: tuple[int, ...] = TRADEABLE_KEY_NUMBERS,
) -> list[Play]:
    """Every way to express one game's edge that clears its own cost.

    `side` is which team the line-movement signal prefers, and it is the
    caller's job to have produced it from an opening line rather than a closing
    one: the signal is about movement, and there is none left at the close. It
    is required and must be non-empty. Without a side there is no bet, only a
    game, and a card built from games with no signal is the most dangerous
    output this module could produce: it looks authoritative and contains
    nothing. So a blank side raises rather than returning plays.

    `posted_line` is what a book is offering. A book only appears in the result
    when its posted line is itself a key number, because that is the only case
    where a book beats an exchange.
    """
    if not (side or "").strip():
        raise ValueError(
            f"{game}: no side. The line-movement signal has to come from an "
            f"opening line; without one there is nothing to bet."
        )

    pmf = margin_pmf(projected_margin, sigma_for_total(total))

    def survival(k: float) -> float:
        return sum(v for kk, v in pmf.items() if kk > k)

    out: list[Play] = []
    for number in key_numbers:
        density = pmf.get(number, 0.0)
        if density <= 0.0:
            continue
        gain = clv_points * density
        price = survival(number)
        if not 0.02 < price < 0.98:
            continue
        backing_home = side.strip() == game.split("@")[-1].strip()
        entry = price if backing_home else 1.0 - price
        for venue in venues:
            if not venue.is_exchange:
                # A book is only usable when the number it posted is this one.
                if posted_line is None or int(abs(posted_line)) != number:
                    continue
            # The fee is symmetric in P, so cost is unchanged; the quoted price
            # is not, and that is what a person reads off the screen.
            cost = venue.cost(price)
            net = gain - cost
            if net <= 0.0:
                continue
            out.append(
                Play(
                    game=game, side=side, number=number, venue=venue.name,
                    strike_price=entry, density=density, gain=gain, cost=cost,
                    stake=_quarter_kelly(net, entry),
                )
            )
    return sorted(out, key=lambda p: p.net, reverse=True)


def build_card(
    plays_by_game: list[list[Play]], *, max_weekly_exposure: float = 0.10
) -> list[Play]:
    """Take the best play per game, then cap the week's total exposure.

    One play per game, because two strikes on the same game are the same
    opinion twice and Kelly on correlated bets overstakes badly.
    """
    best = [max(ps, key=lambda p: p.net) for ps in plays_by_game if ps]
    best.sort(key=lambda p: p.net, reverse=True)
    total = sum(p.stake for p in best)
    if total <= max_weekly_exposure or total <= 0:
        return best
    scale = max_weekly_exposure / total
    return [
        Play(**{**p.__dict__, "stake": p.stake * scale}) for p in best
    ]


def signal_side(
    home: str, away: str, *, projected_margin: float, opening_home_line: float,
    min_disagreement: float = 4.0,
) -> tuple[str | None, float]:
    """Which side the line-movement signal prefers, and by how much.

    This is the whole signal, and it takes one market input: the opening
    number. The model's fair line is compared against it, and the side backed
    is the one the model thinks the market underpriced at the open. What the
    line does afterwards is the thing being predicted, not an input, which is
    why a current line is not needed and a closing line would be useless.

    `min_disagreement` defaults to four points because that is where the
    measured closing line value was strongest and most reliable: 0.44 points at
    a t of 4.7, on games where at least two books had posted an open. Below
    about two points the signal is there but thin.

    Returns `(None, gap)` when the disagreement is too small to act on, which
    on a normal board is most games.
    """
    fair = -projected_margin
    gap = fair - opening_home_line
    if abs(gap) < min_disagreement:
        return None, gap
    # gap < 0 means the model wants the home team laying more than the open
    # asks, so the home side is the underpriced one.
    return (home if gap < 0 else away), gap


def is_tradeable(number: int) -> bool:
    """Whether a margin carries enough extra mass to be worth betting."""
    return number in TRADEABLE_KEY_NUMBERS and KEY_BUMPS.get(number, 1.0) > 1.0
