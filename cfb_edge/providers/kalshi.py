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
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Sequence

API_ROOT = "https://api.elections.kalshi.com/trade-api/v2"

# Contract counts have a granularity of 0.01, so a residual smaller than this
# is float noise from walking the ladder, not size still left to fill. Four
# orders of magnitude below the smallest real quantity; derived, not a PRIOR.
# See DECISIONS.md D31.
_SIZE_TOLERANCE = 1e-6

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
    """One price level in cents, with the contracts resting there.

    Both fields are floats because the exchange quotes both fractionally:
    prices to four decimal dollars on the sub-cent tick grids, and contract
    counts to 0.01. Rounding either to an integer here throws away real
    liquidity, and rounding a size down is how a level holding 0.5 contracts
    reads as empty.
    """

    price: float
    size: float


@dataclass(frozen=True)
class Book:
    """One market's resting liquidity, already converted to YES asks."""

    ticker: str
    yes_asks: list[Level]
    yes_bids: list[Level]

    @property
    def depth(self) -> float:
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

    def vwap(self, contracts: float) -> float | None:
        """Average price to buy `contracts` YES, walking the book.

        Returns None when the book cannot fill the size. Sizing off a midpoint
        instead of a real fill is how a thin market looks tradeable when it is
        not, so this refuses rather than extrapolating.

        Sizes are fractional, so the fill test carries a tolerance. Exact
        equality against zero would leave a float crumb on the last level and
        report a book that does fill as one that does not, which fails the
        safe way but still hides tradeable size.
        """
        if contracts <= 0:
            return None
        remaining, cost = float(contracts), 0.0
        for level in self.yes_asks:
            take = min(remaining, level.size)
            cost += take * level.price
            remaining -= take
            if remaining <= _SIZE_TOLERANCE:
                return cost / float(contracts)
        return None


def _level(raw: object, *, scale: float, invert: bool) -> Level | None:
    """One `[price, size]` pair as a Level in cents, or None if unusable.

    A malformed rung is dropped rather than raised on: one bad level in a
    response should cost that level, not the whole book.
    """
    try:
        price, size = raw[0], raw[1]          # type: ignore[index]
    except (TypeError, KeyError, IndexError):
        return None
    try:
        cents = float(price) * scale
        count = float(size)
    except (TypeError, ValueError):
        return None
    if count <= 0:
        return None
    if invert:
        cents = 100.0 - cents
    return Level(price=cents, size=count)


def parse_book(ticker: str, payload: dict) -> Book:
    """Turn Kalshi's two bid arrays into a one-sided YES view.

    Levels arrive worst-first, so both sides are re-sorted into the order a
    taker would actually consume them.

    Two wire shapes are accepted. The live one wraps the book in
    `orderbook_fp` and quotes `[price_dollars, count_fp]` as strings
    ("0.4200", "13.00"); the older one wrapped it in `orderbook` and quoted
    integer cents. Reading only the second returned an empty book for every
    live market while the request itself succeeded, so `vwap` reported that
    nothing could be filled at any size, and a gate that refuses on thin depth
    refuses everything. Prices are normalised to cents either way, because
    that is the unit the rest of this package, `kalshi_fees` included, already
    works in. Cents are kept as floats: the sub-cent tick grids quote to four
    decimal dollars, so a rung can legitimately sit at 1.2c.

    Source: docs.kalshi.com/getting_started/orderbook_responses and
    /getting_started/fixed_point_migration, both read 2026-09-25.
    """
    payload = payload or {}
    fixed_point = payload.get("orderbook_fp")
    if isinstance(fixed_point, dict):
        yes_raw = fixed_point.get("yes_dollars") or []
        no_raw = fixed_point.get("no_dollars") or []
        scale = 100.0                      # dollars -> cents
    else:
        book = payload.get("orderbook") or {}
        yes_raw = book.get("yes") or []
        no_raw = book.get("no") or []
        scale = 1.0                        # already cents

    # A NO bid at q is a YES offer at 100 - q.
    asks = [lv for lv in (_level(r, scale=scale, invert=True) for r in no_raw)
            if lv is not None]
    bids = [lv for lv in (_level(r, scale=scale, invert=False) for r in yes_raw)
            if lv is not None]

    asks.sort(key=lambda level: level.price)          # cheapest YES first
    bids.sort(key=lambda level: level.price, reverse=True)  # highest bid first
    return Book(ticker=ticker, yes_asks=asks, yes_bids=bids)


def fetch_markets(series_ticker: str, *, opener: Opener | None = None,
                  limit: int = 200, max_pages: int = 25) -> list[dict]:
    """Every open market in one series, following the cursor.

    The endpoint caps a page at 200 regardless of what `limit` asks for, and a
    college football spread ladder runs twenty-odd rungs per game, so one page
    covers about ten games out of a sixty-game board. Reading only the first
    page silently drops most of the slate, and the games it keeps are whichever
    the exchange happened to return first rather than the ones on the card.

    `max_pages` bounds the walk so a server that keeps handing back the same
    cursor cannot spin here forever.
    """
    out: list[dict] = []
    cursor = ""
    seen: set[str] = set()
    for _ in range(max_pages):
        path = f"/markets?limit={limit}&status=open&series_ticker={series_ticker}"
        if cursor:
            path += f"&cursor={cursor}"
        payload = _get(path, opener)
        page = payload.get("markets") or []
        out.extend(page)
        cursor = str(payload.get("cursor") or "")
        # An empty cursor ends the walk; a repeated one means it is not advancing.
        if not cursor or cursor in seen or not page:
            break
        seen.add(cursor)
    return out


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
                "yes_bid": _cents(m, "yes_bid"),
                "yes_ask": _cents(m, "yes_ask"),
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


# Strikes are written into the market's subtitle rather than carried as a
# field, so they have to be read out of text. Kalshi phrases a spread market
# as "wins by over 16.5 points"; the number is what a Play calls its strike.
_STRIKE = re.compile(r"by (?:over|more than)\s+(\d+(?:\.\d+)?)", re.I)

# Widest bid-ask, in probability, that still counts as a quoted price. The live
# college football ladder is mostly wider than this: rungs at 0.09/0.83 and
# 0.14/0.38 are common. A mid taken from those is a number nobody would trade
# at, and it would set an opening line the market never offered.
MAX_SPREAD = 0.15


def strike_of(market: dict) -> float | None:
    """The strike a spread market is written at, or None if it cannot be read.

    Returning None rather than guessing is the point. A strike inferred from a
    title that did not contain one would be priced as if it were listed, which
    is the exact failure this module exists to prevent: the card quoted a
    three-point contract on a game whose only listed strike was the line, and
    the operator filled the listed one at a price where the edge is negative.
    """
    # `floor_strike` is the exchange's own number for this rung. Preferring it
    # over a regex on prose is not a style choice: the title is marketing copy
    # and can be reworded, while this field is the contract.
    floor = market.get("floor_strike")
    if floor not in (None, ""):
        try:
            return float(floor)
        except (TypeError, ValueError):
            pass
    for key in ("yes_sub_title", "subtitle", "title"):
        text = market.get(key)
        if not text:
            continue
        found = _STRIKE.search(str(text))
        if found:
            return float(found.group(1))
    return None


def listed_strikes(markets: Sequence[dict]) -> list[int]:
    """Every strike the venue will actually sell, as whole margins.

    A contract written at 16.5 pays when the margin exceeds 16, so 16 is the
    number the pricing model reasons about. Half-points are floored rather than
    rounded for that reason: 16.5 and 16 are the same bet.
    """
    out = set()
    for m in markets:
        s = strike_of(m)
        if s is not None:
            out.add(int(s))
    return sorted(out)


def _team_and_strike(market: dict) -> tuple[str, float] | None:
    """Which team the contract is written on, and at what margin.

    A rung reads "Boston College wins by over 20.5 points". The team is
    everything before "wins", the strike is the number after "over".
    """
    for key in ("yes_sub_title", "subtitle", "title"):
        text = str(market.get(key) or "")
        if not text:
            continue
        found = _STRIKE.search(text)
        if not found:
            continue
        team = re.split(r"\bwins?\b", text, maxsplit=1)[0].strip()
        if team:
            return team, float(found.group(1))
    return None


def _cents(market: dict, base: str) -> float | None:
    """One side's price in cents for display, whichever way Kalshi spelled it.

    Wraps `_side_price`, which already prefers the `_dollars` spelling, and
    converts back so the human-facing listing keeps the cents it always
    printed. Reading `m["yes_ask"]` directly printed None for every row.
    """
    price = _side_price(market, base)
    return None if price is None else round(price * 100.0, 2)


def _side_price(market: dict, base: str) -> float | None:
    """One side's price as a probability, whichever way Kalshi spelled it.

    The live payload carries `yes_bid_dollars` as a STRING ("0.0900"), while
    this module was written against `yes_bid` as an integer in cents. Reading
    only the second returned None for every rung on the board, so the capture
    parsed zero markets while the request itself succeeded: a silent nothing
    rather than an error. Both spellings are accepted, dollars preferred,
    because the dollar field is the one the exchange actually sends.
    """
    dollars = market.get(f"{base}_dollars")
    if dollars not in (None, ""):
        try:
            return float(dollars)
        except (TypeError, ValueError):
            return None
    cents = market.get(base)
    if cents in (None, ""):
        return None
    try:
        return float(cents) / 100.0
    except (TypeError, ValueError):
        return None


def _mid(market: dict) -> float | None:
    """Mid price in probability, or None when the rung is not two-sided.

    A rung quoted on one side only is not a price, it is an aspiration, and
    reading the lone side as a probability is how a market with no liquidity
    ends up setting an opening line.
    """
    bid, ask = _side_price(market, "yes_bid"), _side_price(market, "yes_ask")
    if bid is None or ask is None or bid <= 0.0 or ask <= 0.0:
        return None
    if ask - bid > MAX_SPREAD:
        # Not a price. On the real NCAAF board a rung is routinely quoted 0.09
        # bid against 0.83 ask, and a midpoint of that is an invention: the
        # edge this project chases is about one cent, so a 74-cent spread is
        # not a cost to subtract, it is the absence of a market.
        return None
    return (bid + ask) / 2.0


def _curve_evidence(markets: Sequence[dict], *, home: str, away: str) -> dict[float, dict]:
    """Survival-curve points plus exact listed-contract provenance."""
    from ..teams import resolve

    out: dict[float, dict] = {}
    known = {home, away}
    for m in markets:
        parsed = _team_and_strike(m); price = _mid(m)
        if parsed is None or price is None:
            continue
        team, strike = parsed
        resolved = resolve(team, known)
        if resolved == home:
            x, p = strike, price
        elif resolved == away:
            x, p = -strike, 1.0 - price
        else:
            continue
        out[x] = {"probability": p, "ticker": str(m.get("ticker") or ""),
                  "event_ticker": str(m.get("event_ticker") or ""),
                  "open_time": m.get("open_time") or m.get("openTime"),
                  "yes_bid": _side_price(m, "yes_bid"), "yes_ask": _side_price(m, "yes_ask"),
                  "strike": strike, "team": team}
    return dict(sorted(out.items()))

def survival_curve(markets: Sequence[dict], *, home: str, away: str) -> dict[float, float]:
    return {x: float(e["probability"]) for x, e in _curve_evidence(markets, home=home, away=away).items()}

def implied_line_evidence(markets: Sequence[dict], *, home: str, away: str) -> tuple[float | None, list[dict]]:
    pts = _curve_evidence(markets, home=home, away=away)
    ordered = sorted(pts.items())
    for (x1, e1), (x2, e2) in zip(ordered, ordered[1:]):
        p1, p2 = float(e1["probability"]), float(e2["probability"])
        if (p1 - 0.5) * (p2 - 0.5) <= 0 and p1 != p2:
            margin = x1 + (x2 - x1) * (p1 - 0.5) / (p1 - p2)
            return -margin, [dict(e1), dict(e2)]
    return None, []


def implied_line(curve: dict[float, float]) -> float | None:
    """The market's line, as the home team's number, or None.

    The line is the margin the market makes a coin flip, so it is where the
    survival curve crosses one half. Two rungs either side of the crossing are
    enough to interpolate; a curve entirely above or below it only proves the
    ladder does not reach the middle of this game, and extrapolating off the
    end of a ladder to invent a line is precisely the kind of number this
    project has spent a week learning not to manufacture.

    Returned negated, because every line in this package is written from the
    home team's perspective and a home favourite lays points.
    """
    pts = sorted(curve.items())
    if len(pts) < 2:
        return None
    for (x1, p1), (x2, p2) in zip(pts, pts[1:]):
        if (p1 - 0.5) * (p2 - 0.5) <= 0 and p1 != p2:
            margin = x1 + (x2 - x1) * (p1 - 0.5) / (p1 - p2)
            return -margin
    return None


def board_quotes(
    *, games: Sequence[str], opener: Opener | None = None, limit: int = 1000,
    seen_at: str | None = None, kickoffs: dict[str, str] | None = None,
) -> list["object"]:
    """One poll of the whole spread board, as Quotes the capture already eats.

    This is the Odds API's replacement and it is deliberately shaped to need no
    other change. `watch`, `grade` and `clv` take Quotes; they do not care that
    the line came from a ladder rather than from a book, and measuring value on
    the venue that actually fills you is the correct version of the measurement
    rather than a compromise on it.

    The old chain recorded a sportsbook's opening line, bet a Kalshi contract,
    and scored against a sportsbook's close: two of the three steps on a venue
    the operator never trades. That gap is what let a card quote a strike the
    exchange does not list.

    A game whose ladder does not straddle a coin flip is skipped rather than
    guessed at. It has no line yet, which is a different thing from having one
    this function could not read.

    `games` is the slate, as "Away @ Home" strings, and it is required because
    **nothing in a Kalshi market says which team is at home**. The first version
    of this function inferred it by looking for each team's first three letters
    in the event ticker. Kalshi abbreviates, so on 26SEP19KYTAM neither "tex"
    nor "ken" is present, both lookups returned -1, the sort key did nothing,
    and the orientation fell out of set iteration order. It passed locally and
    failed on CI, which is the only reason it was caught: a home line written
    upside down is a sign error on every number downstream of it.

    So the schedule decides. A game on the exchange that is not on the slate is
    skipped, because there is nothing to orient it against.

    A ladder quoted from one side only still yields a line. The away team's
    name is used to recognise away-side rungs and complement them, so a ladder
    with no away rungs simply never needs it, and the curve it does give reads
    the same as any other. Skipping those cost the capture every thinly-quoted
    game on the board, which on this book is a large share of it and is
    precisely where the observations are scarcest.
    """
    from datetime import datetime, timezone

    from ..watch import Quote

    stamp = seen_at or datetime.now(timezone.utc).isoformat()
    kickoffs = kickoffs or {}
    by_event: dict[str, list[dict]] = {}
    for m in fetch_markets(SERIES["spread"], opener=opener, limit=limit):
        ev = m.get("event_ticker")
        if ev:
            by_event.setdefault(str(ev), []).append(m)

    # The slate is the only authority on who is at home. Provider spellings are
    # reconciled through the same explicit alias table used everywhere else;
    # unresolved or ambiguous names still fail closed.
    from ..teams import resolve

    schedule: set[tuple[str, str]] = set()
    known_teams: set[str] = set()
    for g in games:
        if "@" not in g:
            continue
        a, h = (part.strip() for part in g.split("@", 1))
        if a and h and a != h:
            schedule.add((a, h))
            known_teams.update((a, h))

    out = []
    for event, markets in by_event.items():
        teams = {t for t, _ in filter(None, map(_team_and_strike, markets))}
        if not teams:
            # No title here parsed into a team and a strike, so there is
            # nothing to look up and nothing to price.
            continue
        resolved_teams = {resolve(t, known_teams) for t in teams}
        if None in resolved_teams:
            continue
        if len(resolved_teams) == 1:
            # One-sided. The pair is not in the markets, so take it from the
            # slate: exactly one fixture may contain this resolved team, or the
            # orientation is a guess again and the game is skipped.
            solo = next(iter(resolved_teams))
            hits = [f for f in schedule if solo in f]
            fixture = hits[0] if len(hits) == 1 else None
        else:
            hits = [f for f in schedule if frozenset(f) == frozenset(resolved_teams)]
            fixture = hits[0] if len(resolved_teams) == 2 and len(hits) == 1 else None
        if fixture is None:
            continue
        away, home = fixture
        line, used = implied_line_evidence(markets, home=home, away=away)
        if line is None:
            continue
        game = f"{away} @ {home}"
        # Contract close_time is an exchange settlement/trading timestamp, not
        # necessarily the football kickoff. Only the schedule may supply kickoff.
        commence = kickoffs.get(game)
        open_times = [str(e.get("open_time") or "") for e in used]
        venue_open = None
        if used and all(open_times):
            try:
                parsed_open = [datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc) for v in open_times]
                venue_open = max(parsed_open).isoformat()
            except (ValueError, TypeError):
                venue_open = None
        game_tickers = tuple(str(e.get("ticker") or "") for e in used if e.get("ticker"))
        quote_inputs = tuple({"ticker": e.get("ticker"), "strike": e.get("strike"),
                              "team": e.get("team"), "probability": e.get("probability"),
                              "yesBid": e.get("yes_bid"), "yesAsk": e.get("yes_ask"),
                              "openTime": e.get("open_time")} for e in used)
        import os
        out.append(Quote(
            game=game, book="kalshi", market="spread",
            line=round(line, 1), price=None, seen_at=stamp,
            commence_time=str(commence) if commence else None,
            venue_open_time=venue_open, event_ticker=event,
            market_tickers=game_tickers, quote_inputs=quote_inputs,
            poll_time=stamp, first_valid_two_sided_quote_time=stamp,
            code_revision=os.getenv("GITHUB_SHA") or None,
        ))
    return out
