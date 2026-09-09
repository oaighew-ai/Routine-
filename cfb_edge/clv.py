"""Closing line value: the only honest way to judge this thing in-season.

A week is fifty-something games and a card might be four bets. At a true 55%
edge, a four-bet week loses money about a third of the time. Judging the model
on a week's record, or a month's, is judging it on noise, and the temptation
when a good model runs cold is to change it, which is how a good model becomes
a bad one.

Closing line value measures something the sample size can actually support: did
you consistently get a better number than the market settled on? Beating the
close is not a guarantee of profit, and it is possible to beat the close on
stale numbers that never had value. But over a few dozen bets it separates a
model finding real disagreements from one finding its own errors far faster
than profit does.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .distribution import cover_probability, margin_pmf, sigma_for_total
from .market import american_to_probability, devig


@dataclass
class LoggedBet:
    """A bet as placed, and the market's final word on it."""

    date: str
    away: str
    home: str
    side: str
    line_taken: float
    price_taken: float
    stake: float
    closing_line: float | None = None
    closing_price: float | None = None
    closing_opposite_price: float | None = None
    result: str | None = None  # "win", "loss", "push", or None if unsettled

    @property
    def line_clv(self) -> float | None:
        """Points of line beaten. Positive means a better number than close."""
        if self.closing_line is None:
            return None
        return self.line_taken - self.closing_line

    def probability_clv(self, *, market_total: float = 52.0) -> float | None:
        """CLV expressed as win probability, which is the comparable unit.

        Half a point is worth much more at 3 than at 12, so points of CLV are
        not additive across bets. Converting through the margin distribution
        makes them so.
        """
        if self.closing_line is None:
            return None
        sigma = sigma_for_total(market_total)
        # Centre on the closing line, which is the best estimate of the truth.
        pmf = margin_pmf(-self.closing_line, sigma)
        taken = cover_probability(pmf, self.line_taken).win_excluding_push
        closed = cover_probability(pmf, self.closing_line).win_excluding_push
        return taken - closed

    @property
    def price_clv(self) -> float | None:
        """Value from the price, in probability.

        Your taken price implies a break-even win rate: -110 needs 52.38%. The
        closing two-way market, devigged, says how often your side actually
        wins. The gap between them is the value the price alone gave you.
        Positive means the closing market thinks your side wins more often than
        your price required you to.

        This isolates price only when the line did not move. When it did, read
        it alongside `probability_clv`, which measures the line component.
        """
        if self.closing_price is None or self.closing_opposite_price is None:
            return None
        fair_close = devig([self.closing_price, self.closing_opposite_price])[0]
        breakeven_taken = american_to_probability(self.price_taken)
        return fair_close - breakeven_taken

    def profit(self) -> float | None:
        """Units won or lost. Push returns the stake, so zero."""
        if self.result is None:
            return None
        if self.result == "push":
            return 0.0
        if self.result == "win":
            from .market import payout_multiple

            return self.stake * payout_multiple(self.price_taken)
        return -self.stake


@dataclass
class CLVReport:
    """Aggregate scorecard across a set of settled or graded bets."""

    bets: int
    graded: int
    beat_close: int
    tied_close: int
    lost_to_close: int
    mean_line_clv: float
    mean_probability_clv: float
    realised_profit: float
    staked: float

    @property
    def beat_close_rate(self) -> float:
        return self.beat_close / self.graded if self.graded else 0.0

    @property
    def roi(self) -> float:
        return self.realised_profit / self.staked if self.staked else 0.0

    def summary(self) -> str:
        return (
            f"{self.bets} bets, {self.graded} with a closing line.\n"
            f"Beat close {self.beat_close} / tied {self.tied_close} / "
            f"lost to close {self.lost_to_close} "
            f"({self.beat_close_rate:.1%} beat rate)\n"
            f"Mean CLV {self.mean_line_clv:+.2f} pts, "
            f"{self.mean_probability_clv:+.2%} win probability\n"
            f"Realised {self.realised_profit:+.3f} units on "
            f"{self.staked:.3f} staked (ROI {self.roi:+.2%})"
        )


def build_report(bets: list[LoggedBet]) -> CLVReport:
    graded = [b for b in bets if b.line_clv is not None]
    line_clvs = [b.line_clv for b in graded]
    prob_clvs = [
        p for p in (b.probability_clv() for b in graded) if p is not None
    ]
    profits = [p for p in (b.profit() for b in bets) if p is not None]
    staked = sum(b.stake for b in bets if b.profit() is not None)

    return CLVReport(
        bets=len(bets),
        graded=len(graded),
        beat_close=sum(1 for v in line_clvs if v > 0),
        tied_close=sum(1 for v in line_clvs if v == 0),
        lost_to_close=sum(1 for v in line_clvs if v < 0),
        mean_line_clv=sum(line_clvs) / len(line_clvs) if line_clvs else 0.0,
        mean_probability_clv=sum(prob_clvs) / len(prob_clvs) if prob_clvs else 0.0,
        realised_profit=sum(profits),
        staked=staked,
    )


BET_FIELDS = [
    "date", "away", "home", "side", "line_taken", "price_taken", "stake",
    "closing_line", "closing_price", "closing_opposite_price", "result",
]


class BetNotFound(LookupError):
    """Raised when a bet to settle cannot be identified from the log."""


def append_bet(path: str | Path, bet: LoggedBet) -> int:
    """Add one bet to the log, creating it if absent. Returns the new count.

    Read-modify-write rather than a bare append, so a log written under an
    older field order is rewritten into the current one instead of gaining a
    row whose columns silently do not line up.
    """
    path = Path(path)
    existing = load_bets(path) if path.exists() else []
    existing.append(bet)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_bets(existing, path)
    return len(existing)


def settle_bet(
    path: str | Path,
    *,
    game: str,
    closing_line: float | None = None,
    closing_price: float | None = None,
    closing_opposite_price: float | None = None,
    result: str | None = None,
    date: str | None = None,
) -> LoggedBet:
    """Fill in the market's final word on one logged bet.

    A bet is identified by its game, and by its date when one game appears
    more than once. An ambiguous match raises rather than settling the first
    row found: settling the wrong bet corrupts the only scorecard this project
    has, and does it silently.
    """
    path = Path(path)
    bets = load_bets(path)

    def matches(b: LoggedBet) -> bool:
        if f"{b.away} @ {b.home}".lower() != game.strip().lower():
            return False
        return date is None or b.date == date

    hits = [i for i, b in enumerate(bets) if matches(b)]
    if not hits:
        raise BetNotFound(
            f"no logged bet for {game!r}"
            + (f" on {date}" if date else "")
            + f". The log has {len(bets)} bets."
        )
    open_hits = [i for i in hits if bets[i].result is None]
    candidates = open_hits or hits
    if len(candidates) > 1:
        dates = ", ".join(sorted({bets[i].date for i in candidates}))
        raise BetNotFound(
            f"{len(candidates)} bets match {game!r}. Pass a date to choose "
            f"between them: {dates}"
        )

    bet = bets[candidates[0]]
    if closing_line is not None:
        bet.closing_line = closing_line
    if closing_price is not None:
        bet.closing_price = closing_price
    if closing_opposite_price is not None:
        bet.closing_opposite_price = closing_opposite_price
    if result is not None:
        if result not in ("win", "loss", "push"):
            raise ValueError(f"result must be win, loss or push, got {result!r}")
        bet.result = result
    save_bets(bets, path)
    return bet


def load_bets(path: str | Path) -> list[LoggedBet]:
    rows: list[LoggedBet] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            def num(key: str) -> float | None:
                value = (row.get(key) or "").strip()
                return float(value) if value else None

            rows.append(
                LoggedBet(
                    date=row["date"],
                    away=row["away"],
                    home=row["home"],
                    side=row["side"],
                    line_taken=float(row["line_taken"]),
                    price_taken=float(row["price_taken"]),
                    stake=float(row["stake"]),
                    closing_line=num("closing_line"),
                    closing_price=num("closing_price"),
                    closing_opposite_price=num("closing_opposite_price"),
                    result=(row.get("result") or "").strip() or None,
                )
            )
    return rows


def save_bets(bets: list[LoggedBet], path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=BET_FIELDS)
        writer.writeheader()
        for bet in bets:
            row = asdict(bet)
            writer.writerow({k: ("" if row[k] is None else row[k]) for k in BET_FIELDS})
