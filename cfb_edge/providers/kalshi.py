"""Kalshi's public market data.

No API key is needed for market data, which is why this is the leg of the
pipeline worth automating first.

The one thing that trips people up is the order book. Kalshi publishes two
arrays of *bids*, one for YES and one for NO, and neither is an ask. A NO bid
at q cents is someone offering to buy NO at q, and buying NO at q is the same
trade as selling YES at 100 - q. So the price you can BUY YES at comes from the
NO side, inverted:

    yes_ask = 100 - best_no_bid

Reading the YES array as if it were an ask book is the classic error here, and
it quietly reports a price better than anything you could actually fill at,
which then shows up downstream as a large phantom edge.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Sequence

API_ROOT = "https://api.elections.kalshi.com/trade-api/v2"

# The college football series. Spreads and totals carry their own tickers.
SERIES = {
    "moneyline": "KXNCAAFGAME",
    "spread": "KXNCAAFSPREAD",
    "total": "KXNCAAFTOTAL",
}

Opener = Callable[[str], bytes]


def _default_opener(url: str, *, timeout: float = 20.0) -> bytes:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "cfb-edge/1.0"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class KalshiUnreachable(RuntimeError):
    """Raised when market data cannot be fetched.

    Carries the host so a caller can tell an egress policy denial apart from
    Kalshi actually being down, which are very different problems.
    """


def _get(path: str, opener: Opener | None = None) -> dict:
    opener = opener or _default_opener
    url = f"{API_ROOT}{path}"
    try:
        return json.loads(opener(url))
    except urllib.error.URLError as exc:
        raise KalshiUnreachable(
            f"could not reach {url}: {exc}. If this is an egress policy denial, "
            f"the host has to be allowed for the environment; it is not "
            f"something the client can work around."
        ) from exc


@dataclass(frozen=True)
class Level:
    """One price level in cents, with the contracts resting there."""

    price: float
    size: int


@dataclass(frozen=True)
class Book:
    """One market's resting liquidity, already converted to YES asks."""

    ticker: str
    yes_asks: list[Level]
    yes_bids: list[Level]

    @property
    def depth(self) -> int:
        return sum(level.size for level in self.yes_asks)

    @property
    def best_ask(self) -> float | None:
        return self.yes_asks[0].price if self.yes_asks else None

    @property
    def best_bid(self) -> float | None:
        return self.yes_bids[0].price if self.yes_bids else None

    @property
    def spread(self) -> float | None:
        if self.best_ask is None or self.best_bid is None:
            return None
        return self.best_ask - self.best_bid

    def vwap(self, contracts: int) -> float | None:
        """Average price to buy `contracts` YES, walking the book.

        Returns None when the book cannot fill the size. Sizing off a midpoint
        instead of a real fill is how a thin market looks tradeable when it is
        not, so this refuses rather than extrapolating.
        """
        remaining, cost = contracts, 0.0
        for level in self.yes_asks:
            take = min(remaining, level.size)
            cost += take * level.price
            remaining -= take
            if remaining == 0:
                return cost / contracts
        return None


def parse_book(ticker: str, payload: dict) -> Book:
    """Turn Kalshi's two bid arrays into a one-sided YES view.

    Levels arrive worst-first, so both sides are re-sorted into the order a
    taker would actually consume them.
    """
    book = (payload or {}).get("orderbook") or {}
    yes_raw = book.get("yes") or []
    no_raw = book.get("no") or []

    # A NO bid at q is a YES offer at 100 - q.
    asks = [Level(100.0 - float(p), int(s)) for p, s in no_raw if s]
    bids = [Level(float(p), int(s)) for p, s in yes_raw if s]

    asks.sort(key=lambda level: level.price)          # cheapest YES first
    bids.sort(key=lambda level: level.price, reverse=True)  # highest bid first
    return Book(ticker=ticker, yes_asks=asks, yes_bids=bids)


def fetch_markets(series_ticker: str, *, opener: Opener | None = None,
                  limit: int = 200) -> list[dict]:
    """Open markets in one series."""
    payload = _get(
        f"/markets?limit={limit}&status=open&series_ticker={series_ticker}", opener
    )
    return payload.get("markets", [])


def fetch_book(ticker: str, *, opener: Opener | None = None, depth: int = 50) -> Book:
    payload = _get(f"/markets/{ticker}/orderbook?depth={depth}", opener)
    return parse_book(ticker, payload)


def fetch_series(names: Sequence[str] = ("moneyline", "spread", "total"),
                 *, opener: Opener | None = None) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for name in names:
        out[name] = fetch_markets(SERIES[name], opener=opener)
    return out


def find_markets(
    team: str, *, series: str = SERIES["spread"], opener: Opener | None = None,
    limit: int = 200,
) -> list[dict]:
    """Find the live markets for a game, by team name.

    Exists because a ticker cannot be safely guessed. The event ticker follows
    an obvious shape, `26SEP11` plus two team abbreviations, but the
    abbreviations are Kalshi's own and a spread market appends a strike suffix
    on top. Constructing one from the pattern lands you in a different market
    with no error, which is worse than not finding it.

    So: ask the exchange. Returns every open market whose title or subtitle
    mentions the team, with its ticker, strike and current prices, and lets a
    person read off the one they meant.
    """
    needle = team.strip().lower()
    out: list[dict] = []
    for m in fetch_markets(series, opener=opener, limit=limit):
        haystack = " ".join(str(m.get(k) or "") for k in
                            ("title", "subtitle", "yes_sub_title", "event_ticker"))
        if needle in haystack.lower():
            out.append({
                "ticker": m.get("ticker"),
                "event": m.get("event_ticker"),
                "title": m.get("title"),
                "yes": m.get("yes_sub_title"),
                "yes_bid": m.get("yes_bid"),
                "yes_ask": m.get("yes_ask"),
                "close_time": m.get("close_time"),
            })
    return out


def main(argv: list[str] | None = None) -> int:
    """python3 -m cfb_edge.providers.kalshi --team Kansas --market spread"""
    import argparse

    p = argparse.ArgumentParser(prog="cfb_edge.providers.kalshi",
                                description=main.__doc__)
    p.add_argument("--team", required=True, help="any team in the game")
    p.add_argument("--market", default="spread", choices=sorted(SERIES))
    args = p.parse_args(argv)

    try:
        rows = find_markets(args.team, series=SERIES[args.market])
    except KalshiUnreachable as exc:
        print(f"cannot reach Kalshi: {exc}")
        return 2
    if not rows:
        print(f"no open {args.market} markets mentioning {args.team!r}. "
              f"Lines may not be posted yet.")
        return 1
    print(f"{len(rows)} open {args.market} markets mentioning {args.team!r}:\n")
    for r in rows:
        bid = r["yes_bid"]; ask = r["yes_ask"]
        quote = f"{bid}/{ask}" if bid is not None and ask is not None else "no quote"
        print(f"  {r['ticker']}")
        print(f"      {r['yes'] or r['title']}   [{quote}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
