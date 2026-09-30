"""The fill test: price a shopped row at the size you actually want.

`shop.py` finds candidates by comparing what venues quote. A quote is not a
fill, and its own docstring says so: "A median is what was quoted, not what you
would be filled at, and the gap between the two is where this kind of edge
usually dies." Until now nothing measured that gap, because the only price
`shop` had was the one The Odds API publishes, and The Odds API publishes no
size at all.

**What this changes today is the recorded EV, not the decision.** Be precise
about it, because the obvious claim is wrong: no shopped row can BET as things
stand. `decide` returns PASS with zero stake for any row without a registered
Stage-A-eligible signal, `shop` passes none, and `systems.jsonl` is empty, so
every row here is already monitor-only. What the fill test corrects is the
number on the row. On one live-shaped fixture a Kalshi moneyline quoted at +400
carried +15.7% EV against the sharp reference; walked at 200 contracts against a
ladder holding 10 at that price, the executable average was 39c and the same row
is **-39.9%**. That figure is what the board ranks by and what the SHADOW ledger
grades, so an EV inflated by unavailable size is a candidate that will look like
a miss later for a reason no one can reconstruct. The fill test becomes a
bet-blocker the moment a system is registered; until then it is a truth-in-
labelling fix on the record, which is the thing the ledger exists to protect.

Kalshi publishes the whole ladder, so for Kalshi rows the gap is measurable.
This module walks that ladder for the size the operator intends to take and
hands `shop` the average price that size would actually pay. `Book.vwap`
refuses rather than extrapolating when the ladder cannot fill, so a book that
cannot support the size produces `NO_FILL` instead of a flattering midpoint.

**The asymmetry is real and it is deliberate.** Only Kalshi publishes depth, so
only Kalshi rows face this test. That is worth stating plainly because the naive
reading of the resulting board is backwards. It does **not** mean the soft-book
rows are safer: a DraftKings row still has no fill test and its price is still a
quote, exactly as unverified as every Kalshi row was before this. What changes is
that the one venue whose liquidity can be checked is now held to a stricter
standard than the venues whose liquidity cannot. So the test is conservative
where it applies and silent everywhere else, and it will under-select Kalshi
relative to books that get no such scrutiny. Reading "fewer Kalshi rows survive"
as "Kalshi is worse" would invert the actual finding, which is only that Kalshi
is the venue where being wrong about size is detectable.

**Moneylines only, and that is not a shortcut.** A Kalshi contract and a book's
moneyline on the same team are the same bet, which is why `shop` already notes
that "moneylines have no line and are always comparable". Spreads are not: the
exchange writes rungs at a strike and `strike_of` floors half-points, because
16.5 and 16 settle identically there, while a book quoting -16.5 against -16 is
offering a different wager. Bridging that needs the margin PMF, and `shop`
refuses those comparisons for exactly that reason (`LINE_MISMATCH`). Importing
the model here to line up a strike with a book line would put a model error back
inside a market-relative measurement. Spread and total rows are left untested
rather than tested badly, and they say so.

**The size is a policy input, not a derived one.** Sizing off the fill while
deriving the fill size from the stake is circular: the stake depends on the
edge, the edge depends on the executable price, and the executable price depends
on the size. `board.py` already breaks that loop by taking `--size` as an
exogenous number, and this module takes the same one from `gate.DEFAULT_SIZE`,
so "the size the gate insists it can fill" has one definition in the codebase.

Nothing here reaches the network. The ladders arrive as already-fetched `Book`
objects, the way `board.run` injects an `opener`, so the suite can exercise
every path with sockets denied.

See DECISIONS.md D37, which records the venue asymmetry as a settled decision
rather than an accident, and tags the 200-contract size as a PRIOR (Law 6): it is
the number `gate.py` has always used and no derivation for it exists here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .engine import reasons
from .gate import DEFAULT_SIZE
from .market import probability_to_american
from .providers import kalshi
from .teams import resolve

# The venues whose rows face the fill test. A venue belongs here only if it
# publishes resting size; adding one that does not would mean inventing depth.
FILL_TESTED_VENUES = ("kalshi",)

# The only market where an exchange contract and a book price are the same bet.
FILL_TESTED_MARKETS = ("h2h",)


@dataclass(frozen=True)
class Fill:
    """What the ladder says the intended size would actually cost.

    `price` is American so that it drops straight into the place `shop` used the
    quoted price, keeping one decision per row (Law 5) rather than deciding on
    the quote and then re-deciding on the fill.
    """

    price: float | None = None
    ticker: str | None = None
    vwap_cents: float | None = None
    depth: float | None = None
    size: float | None = None
    flag: str | None = None

    @property
    def tested(self) -> bool:
        """Whether a ladder was actually walked for this row."""
        return self.vwap_cents is not None


def moneyline_index(
    markets: Sequence[Mapping[str, object]], *, known_teams: set[str]
) -> dict[str, str]:
    """Resolved team name to the ticker of its moneyline contract.

    A Kalshi moneyline market names its team in `yes_sub_title` and nothing
    else: the YES contract pays if that team wins. Provider spellings go through
    the same explicit alias table used everywhere else, and an unresolved name
    is dropped rather than guessed at.

    A team that resolves to more than one ticker is dropped entirely, not
    resolved to whichever came last. `board_quotes` learned that lesson the
    expensive way: an orientation that fell out of set iteration order passed
    locally and failed on CI, and a contract bought on the wrong team is not a
    mispriced bet, it is a different one.
    """
    hits: dict[str, set[str]] = {}
    for market in markets:
        ticker = str(market.get("ticker") or "").strip()
        name = str(market.get("yes_sub_title") or "").strip()
        if not ticker or not name:
            continue
        team = resolve(name, known_teams)
        if team is None:
            continue
        hits.setdefault(team, set()).add(ticker)
    return {team: next(iter(t)) for team, t in hits.items() if len(t) == 1}


class LadderProbe:
    """Callable that prices one shopped side at the intended size.

    Built once per scan from a resolved ticker index and the ladders already
    fetched for those tickers, then handed to `shop.shop` as `fill_probe`.
    """

    def __init__(
        self,
        index: Mapping[str, str],
        books: Mapping[str, kalshi.Book],
        *,
        size: float = DEFAULT_SIZE,
    ) -> None:
        self.index = dict(index)
        self.books = dict(books)
        self.size = float(size)

    def __call__(self, *, side: str, home: str, away: str) -> Fill:
        """The executable price for `side`, or the reason there is none.

        `side` is the outcome name as the odds provider spells it, which for a
        moneyline is the team. It is resolved against the two teams actually in
        this fixture rather than against every team on the board, so a name that
        is ambiguous league-wide is still unambiguous here.
        """
        team = resolve(side, {home, away})
        ticker = self.index.get(team) if team else None
        if ticker is None:
            # No contract on this side that could be resolved with confidence.
            # UNMAPPED already means exactly this and `decide` already treats it
            # as a PASS, so there is no new code to invent.
            return Fill(size=self.size, flag=reasons.UNMAPPED)

        book = self.books.get(ticker)
        vwap = book.vwap(self.size) if book is not None else None
        if vwap is None:
            # Either no ladder was fetched for this ticker, or the resting size
            # cannot cover the order. Both mean the same thing to a decision:
            # there is no price at which this size trades.
            return Fill(
                ticker=ticker,
                depth=book.depth if book is not None else None,
                size=self.size,
                flag=reasons.NO_FILL,
            )

        return Fill(
            price=_american_from_cents(vwap),
            ticker=ticker,
            vwap_cents=vwap,
            depth=book.depth,
            size=self.size,
        )


def _american_from_cents(cents: float) -> float | None:
    """A Kalshi price in cents as American odds.

    Kalshi quotes probability directly, so the conversion is exact and needs no
    de-vig: a contract at 42c is a 42% chance by construction. Prices at or
    outside the 0-100 bounds have no American equivalent and return None rather
    than a number the caller would treat as a price.
    """
    if not 0.0 < cents < 100.0:
        return None
    return probability_to_american(cents / 100.0)


def probe_from_payloads(
    markets: Sequence[Mapping[str, object]],
    payloads: Mapping[str, Mapping[str, object]],
    *,
    known_teams: set[str],
    size: float = DEFAULT_SIZE,
) -> LadderProbe:
    """A probe built from raw Kalshi payloads, parsing each book on the way.

    The convenience path for a caller that has already pulled the listing and
    the order books, offline or live. Parsing here rather than at the call site
    means every ladder goes through `parse_book`, which is what knows about the
    fixed-point wire shape (D36).
    """
    index = moneyline_index(markets, known_teams=known_teams)
    books = {
        ticker: kalshi.parse_book(ticker, dict(payload))
        for ticker, payload in payloads.items()
    }
    return LadderProbe(index, books, size=size)
