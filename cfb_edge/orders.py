"""One step from a captured board to orders you can place, or to nothing.

The card this replaces was correct arithmetic on contracts that did not exist.
It optimised over a fixed ladder of key numbers, 3 and 7 and 10 and 14, chosen
because those margins carry the most density, and it never asked the exchange
what it was willing to sell. On Kentucky at Texas A&M, a game the market had at
-16.5, it printed "Texas A&M at +3, 77c". The exchange listed one strike: the
line, at 50c, where the fee peaks and the edge is -1.18%. A person reading that
card filled the contract that existed. The card had quoted one that did not.

So this module refuses to name a bet it has not seen listed. It takes the
venue's ladder, prices only those strikes, and when none of them clear it says
so. "No placeable bet" is the honest output of a week where the edge does not
survive the fee, and the system had no way to say it before.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

from .strategy import DEFAULT_CLV_POINTS, Venue, find_plays, signal_side

# What a listing lookup has to provide: the strikes a venue will sell for one
# game. Injected so this is testable without the exchange, which is the same
# reason `opener` exists on every provider here.
Lister = Callable[[str, str], Sequence[int]]


@dataclass(frozen=True)
class Order:
    """A fillable instruction, or the reason there isn't one."""

    game: str
    side: str | None
    strike: int | None
    limit_price: float | None
    stake_fraction: float
    contracts: int
    dollars: float
    net_edge: float
    reason: str = ""

    @property
    def placeable(self) -> bool:
        return self.strike is not None

    def line(self) -> str:
        if not self.placeable:
            return f"  {self.game}: no bet. {self.reason}"
        return (
            f"  {self.game}: BUY {self.side} over {self.strike}, "
            f"{self.contracts} contracts at {self.limit_price:.0%} or better "
            f"(${self.dollars:,.2f}, edge {self.net_edge:+.2%})"
        )


def orders_for(
    slate: dict[str, float],
    opens: dict[str, float],
    *,
    lister: Lister,
    week: int,
    bankroll: float = 10_000.0,
    min_disagreement: float = 4.0,
    total: float = 52.0,
    clv_points: float = DEFAULT_CLV_POINTS,
) -> list[Order]:
    """Every game with a signal, priced against what the venue lists.

    A game appears in the result whenever the signal fires, whether or not it
    produces a bet. A signal with no fillable contract is information: it says
    the model disagrees with the market and the venue will not sell the
    disagreement at a price worth paying. Dropping those rows silently is how
    the previous card gave the impression that every signal was actionable.
    """
    venues = [Venue("exchange", is_exchange=True)]
    out: list[Order] = []

    for game, line in sorted(opens.items()):
        if game not in slate or "@" not in game:
            continue
        away, home = (s.strip() for s in game.split("@", 1))
        side, gap = signal_side(
            home, away, projected_margin=slate[game], opening_home_line=line,
            min_disagreement=min_disagreement, week=week,
        )
        if side is None:
            continue

        strikes = list(lister(game, side))
        if not strikes:
            out.append(Order(game, side, None, None, 0.0, 0, 0.0, 0.0,
                             "the venue lists no strike for this game"))
            continue

        plays = find_plays(
            game, market_line=line, side=side, venues=venues, total=total,
            clv_points=clv_points, listed_strikes=strikes,
        )
        if not plays:
            out.append(Order(
                game, side, None, None, 0.0, 0, 0.0, 0.0,
                f"none of the {len(strikes)} listed strikes clear the fee "
                f"({', '.join(str(s) for s in strikes[:6])}"
                f"{'...' if len(strikes) > 6 else ''})",
            ))
            continue

        best = plays[0]
        dollars = bankroll * best.stake
        out.append(Order(
            game=game, side=side, strike=best.number,
            limit_price=best.strike_price, stake_fraction=best.stake,
            # Floor, never round: one contract over the limit is a stake the
            # portfolio cap did not authorise.
            contracts=int(math.floor(dollars / best.strike_price)),
            dollars=dollars, net_edge=best.net,
        ))
    return out


def summarise(orders: Sequence[Order], *, bankroll: float = 10_000.0) -> str:
    """What to do, and what the week refused to offer."""
    fillable = [o for o in orders if o.placeable]
    blocked = [o for o in orders if not o.placeable]
    lines: list[str] = []

    if fillable:
        risk = sum(o.dollars for o in fillable)
        ev = sum(o.dollars * o.net_edge for o in fillable)
        lines.append(f"{len(fillable)} order{'s' if len(fillable) != 1 else ''} "
                     f"to place. ${risk:,.2f} at risk "
                     f"({risk / bankroll:.2%} of bankroll), "
                     f"expected ${ev:,.2f}.")
        lines += [o.line() for o in sorted(fillable, key=lambda o: -o.net_edge)]
    else:
        lines.append("No placeable bet this week.")

    if blocked:
        lines.append("")
        lines.append(f"{len(blocked)} signal{'s' if len(blocked) != 1 else ''} "
                     f"fired with nothing fillable behind them:")
        lines += [o.line() for o in blocked]
        lines.append("")
        lines.append("A signal the venue will not sell at a clearing price is "
                     "not a missed bet. It is the fee winning, which is the "
                     "thing this edge has always had to survive.")
    return "\n".join(lines)
