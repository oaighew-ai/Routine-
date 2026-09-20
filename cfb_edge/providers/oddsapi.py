"""The Odds API adapter: a full college football board in one call.

Chosen over scraping because it returns every book at once and includes the
sportsbooks that matter for this strategy.

**It is not free at this polling rate, and an earlier version of this file
said it was.** Billing is one credit per region per market, so the defaults
here cost 3 credits a poll, and the schedule in `watch.py` makes 652 polls in
a regular-season week: about 1,956 credits a week and 8,400 a month. That was
never checked against the arithmetic until it was measured. Read your own
plan's allowance before leaving the capture running, and note that
`--regions us` costs a third as much at the price of the low-hold European
books the strategy was measured on.

Set the key in the environment rather than passing it around:

    export ODDS_API_KEY=...          # or setx on Windows

The key is stripped before use. A secret pasted into a GitHub Actions box with
a trailing newline arrives carrying it, and the two APIs this project talks to
fail differently on that: CFBD takes its key in a header, where urllib refuses
outright with "Invalid header value", and this one takes it in a query string,
where the newline is percent-encoded into the URL and comes back as a plain
401. The loud failure costs a minute. The quiet one looks exactly like a wrong
key, and this project has already spent time reading a 401 that way.

The adapter deliberately does no filtering, deduplication or averaging. It
turns one HTTP response into quotes and stops. Everything else is a pure
function of the raw log, which is the only part that cannot be recomputed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..watch import Quote

API_ROOT = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_ncaaf"

# BUILD_PROMPT §7. Requesting by `bookmakers` rather than by region is not a
# preference: region `us` alone omits Kalshi, which the API lists under `us_ex`,
# and Pinnacle, which it lists under `eu`. A bookmakers list of ten keys or
# fewer is billed as one region, so it is the only way to get the venue and the
# sharp reference in the same pull.
#
# **It is not automatically cheaper, and an earlier version of this comment said
# it was.** Billing is regions x markets. Naming books collapses the region
# factor to 1; the market factor is untouched. Measured on a real pull:
# 7 bookmakers x `h2h,spreads,totals` cost 3 credits, exactly what
# `regions=us,us2,eu` costs for one market. The saving is real only at a
# constant market count, and the EDGE OS scan asks for three markets where the
# old capture asked for one, so it nets out. Read `x-requests-last`; do not
# reason about the price from the parameter you used.
MAX_BOOKMAKERS_PER_REGION = 10

SPORT_KEYS = {
    "ncaaf": "americanfootball_ncaaf",
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
}

DEFAULT_MARKETS = ("h2h", "spreads", "totals")

Opener = Callable[[str], bytes]


class OddsApiUnreachable(RuntimeError):
    """Raised when the board cannot be fetched, naming the host.

    An empty board and an unreachable one mean opposite things, and a capture
    that silently records nothing is worse than one that stops loudly.
    """


def _default_opener(url: str, *, timeout: float = 30.0) -> bytes:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "cfb-edge/1.0"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_board(
    *,
    api_key: str | None = None,
    markets: str = "spreads",
    regions: str = "us,us2,eu",
    bookmakers: Sequence[str] | None = None,
    sport: str = SPORT,
    opener: Opener | None = None,
    seen_at: str | None = None,
) -> list[Quote]:
    """One poll of the whole board for `sport`.

    `regions` includes eu because the low-hold books this strategy needs are
    not all in the us region, and a board without them is a board where the
    edge does not clear.

    Passing `bookmakers` replaces `regions` entirely, which is what §7 requires:
    up to ten named keys bill as one region and can span `us`, `us_ex` and `eu`
    in a single request, so the exchange and Pinnacle arrive together.
    """
    key = (api_key or os.environ.get("ODDS_API_KEY") or "").strip()
    if not key:
        raise OddsApiUnreachable(
            "no API key. Set ODDS_API_KEY in the environment.\n"
            "Check your plan's allowance first: the-odds-api.com bills one "
            "credit per region per market, so these defaults cost 3 a poll, "
            "and the schedule in watch.py makes 652 polls a week. That is "
            "about 1,956 credits a week and 8,400 a month. Dropping to "
            "--regions us cuts it to a third."
        )
    url = _odds_url(key, sport=sport, markets=markets, regions=regions,
                    bookmakers=bookmakers)
    try:
        payload = json.loads((opener or _default_opener)(url))
    except urllib.error.URLError as exc:
        raise OddsApiUnreachable(
            f"could not reach api.the-odds-api.com: {exc}. If this is a network "
            f"policy denial the host has to be allowed; the client cannot work "
            f"around it."
        ) from exc
    return parse_board(payload, seen_at=seen_at)


def _checked_seen_at(seen_at: str | None) -> str:
    """The timestamp to stamp on this batch, or a loud failure.

    Only `None` means "this is a live poll, use the clock". Every other value
    is a caller saying it knows when the payload was fetched, and a caller that
    is wrong about that has to find out here rather than downstream.

    The two ways it used to go wrong were both silent. An empty string took the
    `or` branch and got today's clock, which is the exact substitution this
    argument exists to prevent. An unparseable string survived all the way to
    `Quote.before_kickoff`, which catches `ValueError` and returns `None`, and
    `OpeningBook._observe` counts `None` as usable: every quote in the batch
    would then be eligible to become the close, including one taken mid-game.
    The close would be wrong in the direction of whoever was winning, and
    nothing would have complained.

    Validation runs the same transformation `before_kickoff` does, so a value
    that passes here cannot fail there. The string is returned unchanged rather
    than normalised, so replaying an archived log rewrites nothing.
    """
    if seen_at is None:
        return datetime.now(timezone.utc).isoformat()
    if not isinstance(seen_at, str):
        raise ValueError(
            f"seen_at must be an ISO 8601 string, got {type(seen_at).__name__}. "
            f"A datetime is the natural thing to reach for and is not accepted, "
            f"because Quote.seen_at is serialised to the log as text."
        )
    if not seen_at.strip():
        raise ValueError(
            "seen_at is empty. Pass None for a live poll, or the time the "
            "payload was actually fetched. An empty string used to fall back "
            "to the current clock, which silently destroys the first-seen "
            "ordering the whole strategy is measured against."
        )
    try:
        datetime.fromisoformat(seen_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"seen_at is not an ISO 8601 timestamp: {seen_at!r}. It has to "
            f"parse, because the kickoff gate compares it against the game's "
            f"start time and an unparseable value silently disables that gate "
            f"for every quote in this batch."
        ) from exc
    return seen_at


def parse_board(payload: list[dict], *, seen_at: str | None = None) -> list[Quote]:
    """Flatten the API's nested response into quotes.

    Lines are recorded from the home team's perspective, matching every other
    convention in this package: negative means the home team lays points.

    `seen_at` defaults to now, which is right for a live poll and wrong for
    everything else. Re-parsing an archived payload without it stamps every
    quote with today's clock, which silently destroys the first-seen ordering
    the whole strategy is built on: the open stops being the open. Pass the
    time the payload was actually fetched. Anything that is neither `None` nor
    a parseable ISO 8601 string raises rather than falling back.
    """
    seen_at = _checked_seen_at(seen_at)
    out: list[Quote] = []
    for event in payload or []:
        home = (event.get("home_team") or "").strip()
        away = (event.get("away_team") or "").strip()
        if not home or not away:
            continue
        game = f"{away} @ {home}"
        # Carried on every quote so the close can be gated on kickoff. The
        # capture runs from Sunday into Tuesday and games start inside that
        # window, so without this a quote taken mid-game becomes the "close".
        commence = (event.get("commence_time") or "").strip() or None
        for bookmaker in event.get("bookmakers") or []:
            book = (bookmaker.get("title") or bookmaker.get("key") or "").strip()
            for market in bookmaker.get("markets") or []:
                kind = market.get("key")
                for outcome in market.get("outcomes") or []:
                    # Only the home side is stored; the away line is its
                    # negative, and storing both would double-count the book.
                    if (outcome.get("name") or "").strip() != home:
                        continue
                    point = outcome.get("point")
                    if point is None:
                        continue
                    out.append(Quote(
                        game=game, book=book,
                        market="spread" if kind == "spreads" else str(kind),
                        line=float(point), price=outcome.get("price"),
                        seen_at=seen_at, commence_time=commence,
                    ))
    return out


# ---------------------------------------------------------------------------
# BUILD_PROMPT §5 and §7: snapshots, book sets, and the credit meter.
#
# `fetch_board` above returns home-side spread quotes and nothing else, which is
# what the opening-line capture needs. A scan needs all three markets, both
# sides, the book set that produced the consensus, and the credit headers, so
# that lives here rather than being bolted onto a function whose shape 172 tests
# already depend on.
# ---------------------------------------------------------------------------


HTTP_MEANING = {
    401: "the API key is missing, wrong, or no longer active",
    404: "no such sport key, or the endpoint moved",
    422: "the parameters are malformed; check the bookmaker and market keys",
    429: "the plan's request quota is exhausted for this period",
}
"""What each refusal means, so the message names a cause rather than a number.

Deliberately short and deliberately not exhaustive: a code that is not here
says so, which is better than a confident wrong gloss.
"""


class CreditCapReached(RuntimeError):
    """Raised when a pull would run past `CREDIT_CAP_MONTHLY`.

    Raised rather than logged. A capture that quietly keeps spending past its
    cap is how a plan runs out mid-week, and the week it runs out is the week
    the opens are posted.
    """


def _odds_url(
    key: str,
    *,
    sport: str,
    markets: str,
    regions: str | None = None,
    bookmakers: Sequence[str] | None = None,
    extra: Mapping[str, str] | None = None,
    path: str = "odds",
) -> str:
    params = [f"apiKey={key}", f"markets={markets}", "oddsFormat=american"]
    if bookmakers:
        keys = list(dict.fromkeys(bookmakers))
        if len(keys) > MAX_BOOKMAKERS_PER_REGION:
            raise ValueError(
                f"{len(keys)} bookmaker keys requested; more than "
                f"{MAX_BOOKMAKERS_PER_REGION} is billed as more than one region, "
                f"which is the cost this parameter exists to avoid. Trim the "
                f"list in config rather than raising this limit."
            )
        params.append("bookmakers=" + ",".join(keys))
    else:
        params.append(f"regions={regions or 'us'}")
    for k, v in (extra or {}).items():
        params.append(f"{k}={v}")
    return f"{API_ROOT}/sports/{sport}/{path}/?" + "&".join(params)


def _opener_with_headers(url: str, *, timeout: float = 30.0):
    """Like `_default_opener`, but keeps the response headers.

    The headers are the whole point: `x-requests-remaining` is the only honest
    view of the credit budget, and the existing opener throws it away.
    """
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "cfb-edge/1.0"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), {k.lower(): v for k, v in response.headers.items()}


@dataclass(frozen=True)
class Pull:
    """One odds pull, extracted. This is a `snapshots/` row.

    Raw payloads are re-fetchable from the historical endpoint and extracted
    rows are not, so the ledger keeps these and gzips or drops the raw JSON.
    """

    sport: str
    fetched_at: str
    markets: tuple[str, ...]
    book_set: tuple[str, ...]
    events: tuple[dict, ...] = ()
    credits_remaining: float | None = None
    credits_used: float | None = None
    last_cost: float | None = None

    def summary(self) -> str:
        """Compact by design: §4 forbids reading raw pulls into a conversation."""
        prices = sum(
            len(m["outcomes"])
            for e in self.events
            for b in e["bookmakers"]
            for m in b["markets"]
        )
        return (
            f"{self.sport}: {len(self.events)} events, {len(self.book_set)} books, "
            f"{prices} prices, markets {','.join(self.markets)}, "
            f"credits remaining {self.credits_remaining}"
        )


def extract(payload: Any, *, sport: str, fetched_at: str,
            markets: Sequence[str]) -> tuple[tuple[dict, ...], tuple[str, ...]]:
    """Flatten the API response into storable rows, keeping both sides.

    Unlike `parse_board`, nothing is dropped here. The away side of a spread,
    the total's under, and the moneyline are all needed: §6.10 de-vigs two-way
    markets and cannot do it from one side.
    """
    events: list[dict] = []
    books: set[str] = set()
    for event in payload or []:
        home = (event.get("home_team") or "").strip()
        away = (event.get("away_team") or "").strip()
        if not home or not away:
            continue
        rows = []
        for bookmaker in event.get("bookmakers") or []:
            key = (bookmaker.get("key") or bookmaker.get("title") or "").strip()
            if not key:
                continue
            books.add(key)
            market_rows = []
            for market in bookmaker.get("markets") or []:
                kind = market.get("key")
                if kind not in markets:
                    continue
                outcomes = [
                    {
                        "name": (o.get("name") or "").strip(),
                        "price": o.get("price"),
                        "point": o.get("point"),
                    }
                    for o in market.get("outcomes") or []
                    if o.get("price") is not None
                ]
                if outcomes:
                    market_rows.append({
                        "key": kind,
                        "lastUpdate": market.get("last_update"),
                        "outcomes": outcomes,
                    })
            if market_rows:
                rows.append({"key": key, "markets": market_rows})
        if rows:
            events.append({
                "id": event.get("id") or f"{away} @ {home}",
                "commenceTime": (event.get("commence_time") or "").strip() or None,
                "home": home,
                "away": away,
                "bookmakers": rows,
            })
    return tuple(events), tuple(sorted(books))


def fetch_pull(
    *,
    sport: str,
    bookmakers: Sequence[str],
    markets: Sequence[str] = DEFAULT_MARKETS,
    api_key: str | None = None,
    opener=None,
    fetched_at: str | None = None,
    credit_cap_monthly: float | None = None,
) -> Pull:
    """One pull of every market for one sport, with the credit meter read.

    `credit_cap_monthly` is checked against `x-requests-used` *after* the call,
    because the cap is about not starting the next pull rather than about
    refusing this one: the credits for this request are already spent by the
    time the header arrives.
    """
    key = (api_key or os.environ.get("ODDS_API_KEY") or "").strip()
    if not key:
        raise OddsApiUnreachable(
            "no API key. Set ODDS_API_KEY in the environment. Requesting by "
            "bookmakers keeps a three-market pull at one region's billing."
        )
    sport_key = SPORT_KEYS.get(sport, sport)
    url = _odds_url(key, sport=sport_key, markets=",".join(markets),
                    bookmakers=bookmakers)
    try:
        body, headers = (opener or _opener_with_headers)(url)
    except urllib.error.HTTPError as exc:
        # Checked before URLError, which it subclasses. The first live run of
        # phase0-coverage reported a 401 as "if this is a network policy denial
        # the host has to be allowed", which sends the reader after a firewall
        # when the answer is the key. A server that answers is reachable; what
        # it answered is the finding.
        raise OddsApiUnreachable(
            f"api.the-odds-api.com answered HTTP {exc.code} "
            f"({HTTP_MEANING.get(exc.code, 'see the API docs')}). The host is "
            f"reachable; this is the API refusing the request, not the network."
        ) from exc
    except urllib.error.URLError as exc:
        raise OddsApiUnreachable(
            f"could not reach api.the-odds-api.com: {exc}. Nothing answered, so "
            f"if this is a network policy denial the host has to be allowed; "
            f"the client cannot work around it."
        ) from exc

    stamp = _checked_seen_at(fetched_at)
    events, books = extract(json.loads(body), sport=sport_key, fetched_at=stamp,
                            markets=tuple(markets))

    def number(name: str) -> float | None:
        raw = headers.get(name)
        try:
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    pull = Pull(
        sport=sport_key,
        fetched_at=stamp,
        markets=tuple(markets),
        book_set=books,
        events=events,
        credits_remaining=number("x-requests-remaining"),
        credits_used=number("x-requests-used"),
        last_cost=number("x-requests-last"),
    )

    if credit_cap_monthly is not None and pull.credits_used is not None:
        if pull.credits_used >= credit_cap_monthly:
            raise CreditCapReached(
                f"x-requests-used is {pull.credits_used:.0f} against a cap of "
                f"{credit_cap_monthly:.0f}. This pull completed; the next one "
                f"must not run. Raise CREDIT_CAP_MONTHLY deliberately or wait "
                f"for the billing period to roll."
            )
    return pull
