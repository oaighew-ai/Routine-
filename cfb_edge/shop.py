"""Candidates from the price alone, when no handicapping system exists.

`runner.scan` is driven by signal fires: a system says something, the engine
prices it. With `systems.jsonl` empty every game resolves to `NO_EVIDENCE` and
the board is correctly blank. That is the right answer to "what does my
handicapper like", and it is the wrong answer to "is there anything on this
slate worth betting", because one edge needs no handicapper at all.

That edge is the spread between books. Six books quote the same game and do not
agree, and one of them is better at it than the others. Pinnacle's no-vig price
is the reference; a soft book offering one side at a price whose breakeven sits
below that reference is selling it under what the sharp market says it is worth.
`BUILD_PROMPT` §8 names this as an owned strategy (reduced-juice shopping) and
it is market-relative end to end: no projection, no model, no invented record.
Law 4 still decides, on EV at the executable price.

Three constraints keep it honest.

**The reference is the sharp book, and it is never the thing you bet.** The
first version of this module used the median of the soft books as truth and
shopped every venue against it, Pinnacle included. That is backwards, and it
is backwards in the direction that loses money. Pinnacle prices closer to the
truth than the soft consensus does, so "Pinnacle is cheaper than DraftKings and
FanDuel think it should be" is overwhelmingly Pinnacle being right, and a
system built that way bets the sharp book every time the soft books are slow.
The reference is Pinnacle's no-vig price where Pinnacle quotes the market, the
soft median where it does not, and the reference book is excluded from the
candidates it prices. A book is never measured against a median it belongs to
either, for the same reason at smaller scale.

**A different line is a different bet.** On spreads and totals a venue quoting
-2.5 against a consensus of -3 is not offering a better price, it is offering
another wager. Bridging the half-point needs the margin PMF, and a model error
imported into a market-relative measurement comes back looking like an edge.
Those rows are logged `LINE_MISMATCH` and do not bet. Moneylines have no line
and are always comparable, which is why most of what survives is a moneyline.

**Nothing here is a fill.** A median is what was quoted, not what you would be
filled at, and the gap between the two is where this kind of edge usually dies.
Every row carries `priceSource` and SHADOW grading is what decides whether the
edge is real. The engine's job is to find the candidates; the ledger's job is
to find out whether they were worth finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .engine import config, reasons
from .engine.posterior import Decision, Quote, decide
from .engine.rows import candidate_row
from .runner import US_CONSENSUS_EXCLUDED, market_view, stale_sources, utcnow

# Books shopped by default. Pinnacle is absent on purpose: it is the reference
# (see SHARP_REFERENCE) and a reference you also bet is not a reference.
DEFAULT_VENUES = ("draftkings", "fanduel", "betmgm", "betrivers", "kalshi")

# The book whose no-vig price is treated as the market's best estimate. Not a
# claim that Pinnacle is right, a claim that it is less wrong than the median
# of the books that copy it, which is the assumption the whole method rests on
# and the first thing the SHADOW ledger should be able to falsify.
SHARP_REFERENCE = "pinnacle"

# The smallest soft consensus worth calling one, when the sharp book is absent
# and the median has to stand in. Two books is a disagreement.
MIN_CONSENSUS_BOOKS = 3


@dataclass(frozen=True)
class ShopRow:
    """One venue's price on one side, decided against the others' median."""

    decision: Decision
    event_id: str
    home: str
    away: str
    commence_time: str | None
    market: str
    side: str
    venue: str
    venue_line: float | None
    venue_price: float
    consensus_line: float | None
    consensus_price: float | None
    consensus_books: tuple[str, ...]
    reference: str = "soft_median"

    @property
    def bets(self) -> bool:
        return self.decision.decision == reasons.BET

    @property
    def ev(self) -> float:
        return self.decision.ev


def shop(
    pull: Mapping[str, Any],
    *,
    cfg: config.Config,
    venues: Sequence[str] = DEFAULT_VENUES,
    markets: Sequence[str] | None = None,
    sport: str = "americanfootball_ncaaf",
    reference: str = SHARP_REFERENCE,
    now: datetime | None = None,
    age_seconds: float | None = None,
    min_consensus_books: int = MIN_CONSENSUS_BOOKS,
) -> list[ShopRow]:
    """Every venue, every side, decided against the other books' median.

    Returns one row per (event, market, side, venue) that the venue actually
    quotes, including the ones that do not bet, because a board that shows only
    what cleared cannot be checked against what did not.
    """
    now = now or utcnow()
    markets = list(
        markets if markets is not None
        else cfg.raw.get("markets", ["h2h", "spreads", "totals"])
    )
    stale = stale_sources({"odds": age_seconds}, cfg)

    rows: list[ShopRow] = []
    for event in pull.get("events", []):
        sides_by_market: dict[str, list[str]] = {}
        for book in event.get("bookmakers", []):
            for m in book.get("markets", []):
                key = m.get("key")
                if key not in markets:
                    continue
                seen = sides_by_market.setdefault(key, [])
                for out in m.get("outcomes", []):
                    name = out.get("name")
                    if name and name not in seen:
                        seen.append(name)

        for market, sides in sides_by_market.items():
            for side in sides:
                ref = market_view(
                    event, market=market, side=side, venue=reference,
                    exclude_venue=False,
                )
                for venue in venues:
                    if venue == reference:
                        continue
                    view = market_view(
                        event, market=market, side=side, venue=venue,
                        exclude_venue=venue not in US_CONSENSUS_EXCLUDED,
                    )
                    if view is None or view.venue_price is None:
                        continue

                    # The reference: the sharp book's own two-way price where it
                    # quotes this market, the soft median otherwise. Falling
                    # back is a real weakening, so the row records which one it
                    # got rather than presenting them as the same number.
                    if ref is not None and ref.venue_price is not None:
                        ref_price = float(ref.venue_price)
                        ref_other = ref.venue_other_price
                        ref_line = ref.venue_line
                        ref_kind = reference
                    elif len(view.book_set) >= min_consensus_books:
                        ref_price = view.price
                        ref_other = view.other_price
                        ref_line = view.line
                        ref_kind = "soft_median"
                    else:
                        continue

                    quote = Quote(
                        event_id=view.event_id or f"{view.away} @ {view.home}",
                        sport=sport,
                        market=market,
                        side=side,
                        line=view.venue_line,
                        price=float(view.venue_price),
                        other_price=view.venue_other_price,
                        consensus_price=ref_price,
                        consensus_other_price=ref_other,
                        venue=venue,
                        book_set=view.book_set,
                        starts_at=view.commence_time,
                        price_source="capture",
                    )
                    flags: list[str] = []
                    if market != "h2h" and _line_differs(view.venue_line, ref_line):
                        flags.append(reasons.LINE_MISMATCH)

                    d = decide(
                        quote, (), cfg=cfg,
                        venue_config=cfg.venue(venue),
                        stale_sources=stale,
                        integrity_flags=flags,
                    )
                    if reasons.LINE_MISMATCH in d.reason_codes:
                        # Logged, never bet: it is a different wager.
                        d.decision = reasons.PASS
                        d.stake_units = 0.0
                    rows.append(ShopRow(
                        decision=d,
                        event_id=quote.event_id,
                        home=view.home,
                        away=view.away,
                        commence_time=view.commence_time,
                        market=market,
                        side=side,
                        venue=venue,
                        venue_line=view.venue_line,
                        venue_price=float(view.venue_price),
                        consensus_line=ref_line,
                        consensus_price=ref_price,
                        consensus_books=view.book_set,
                        reference=ref_kind,
                    ))

    return _one_per_game_and_side(rows)


def _line_differs(a: float | None, b: float | None) -> bool:
    """Whether two lines are the same number, with one side missing counting
    as different. A missing line on a spread is not a match, it is an unknown."""
    if a is None or b is None:
        return True
    return abs(a - b) > 1e-9


def _one_per_game_and_side(rows: Sequence[ShopRow]) -> list[ShopRow]:
    """§6.8: one position per game and side, best EV wins.

    Shopping generates the duplicates §6.8 exists for by construction, since
    every book quoting the same side is the same position at a different price.
    The loser is marked `DOMINATED` rather than dropped, so the board can show
    what the best price was measured against.
    """
    best: dict[tuple[str, str, str], ShopRow] = {}
    for row in rows:
        if not row.bets:
            continue
        key = (row.event_id, row.market, row.side)
        incumbent = best.get(key)
        if incumbent is None or row.ev > incumbent.ev:
            if incumbent is not None:
                incumbent.decision.decision = reasons.PASS
                incumbent.decision.add(reasons.DOMINATED)
                incumbent.decision.stake_units = 0.0
            best[key] = row
        else:
            row.decision.decision = reasons.PASS
            row.decision.add(reasons.DOMINATED)
            row.decision.stake_units = 0.0
    return list(rows)


def board_rows(
    rows: Sequence[ShopRow], *, logged_at: str, phase: str
) -> list[dict]:
    """Ledger candidate rows, in the §5 schema, for every shopped row."""
    return [
        candidate_row(
            r.decision,
            candidate_id=f"{logged_at}-shop-{i:04d}",
            logged_at=logged_at,
            phase=phase,
            time_seen=logged_at,
        )
        for i, r in enumerate(rows)
    ]


def summary(rows: Sequence[ShopRow]) -> str:
    """What the scan saw, in the shape the CLI prints."""
    bets = [r for r in rows if r.bets]
    counts: dict[str, int] = {}
    for r in rows:
        for code in r.decision.reason_codes:
            counts[code] = counts.get(code, 0) + 1
    games = {r.event_id for r in rows}
    lines = [
        f"{len(rows)} priced quotes across {len(games)} games, "
        f"{len(bets)} clear the gate",
    ]
    fallback = sum(1 for r in rows if r.reference == "soft_median")
    if fallback:
        lines.append(
            f"{fallback} quotes priced against the soft median because the "
            f"sharp book did not quote them; those are the weakest rows here"
        )
    if bets:
        lines.append(
            "best EV " + ", ".join(
                f"{r.away} @ {r.home} {r.side} {r.venue} {r.ev:+.2%}"
                for r in sorted(bets, key=lambda r: -r.ev)[:5]
            )
        )
    if counts:
        lines.append("reasons: " + ", ".join(
            f"{k} x{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
        ))
    return "\n".join(lines)
